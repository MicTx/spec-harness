#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""
spec server-mode adapter
========================

Exposes the spec task-package lifecycle as the three HTTP interfaces
required by an async-task workflow platform (e.g. UUMit):

    POST {ROOT}/start   body {root, goal}      -> {"task_id": "<slug>", ...}
    GET  {ROOT}/result?task_id=<slug>&root=<p> -> {"workflow_status": "running|completed|failed", ...}
    GET  {ROOT}/health                          -> {"code": 0, "status": "healthy"}

The adapter initializes a task package and projects its current route/status.
The target platform remains responsible for later run, check, and done actions.
The adapter does not import spec scripts; it shells out to them under
SPEC_SKILL_DIR so it remains a single standard-library file usable from an
exported runtime package.

Security model:
- SPEC_SKILL_DIR defaults to this file's grandparent (the runtime package root,
  which holds scripts/ and references/).
- ALLOWED_ROOTS (comma-separated) whitelists project paths callers may pass as
  root; empty means unrestricted and must not be used for an internet-facing
  deployment. Derived package paths under a root are resolved and containment-
  checked so symlinks cannot escape the root.
- SPEC_SERVER_TOKEN (Bearer) authenticates /start and /result; /health stays
  anonymous for probes. The server refuses to start on a non-loopback HOST
  without a token.
- MAX_CONCURRENT_WORK bounds subprocess-backed requests; excess requests get
  429 instead of unbounded process fan-out.
- The startup guard refuses to start (clean message, no traceback) on a
  non-integer or out-of-range PORT and on an invalid MAX_CONCURRENT_WORK.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Package root = parent of this script's dir (server/ -> root with scripts/ references/).
SPEC_SKILL_DIR = os.environ.get("SPEC_SKILL_DIR") or str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOST = os.environ.get("HOST", "127.0.0.1")
ROOT_PREFIX = os.environ.get("ROOT_PREFIX", "/v1")
if not ROOT_PREFIX.startswith("/"):
    ROOT_PREFIX = "/" + ROOT_PREFIX
ROOT_PREFIX = ROOT_PREFIX.rstrip("/") or "/"
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "")
PY = sys.executable or "python3"
ALLOWED_ROOTS = [p.strip() for p in os.environ.get("ALLOWED_ROOTS", "").split(",") if p.strip()]
AUTH_TOKEN = os.environ.get("SPEC_SERVER_TOKEN", "")
MAX_BODY_BYTES = 1024 * 1024
MAX_GOAL_BYTES = 64 * 1024
MAX_TITLE_BYTES = 256
MAX_SUBPROCESS_OUTPUT_BYTES = 64 * 1024
REQUEST_READ_TIMEOUT = 5
SLUG_RE = re.compile(r"[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_STARTUP_ERRORS: list[str] = []
try:
    PORT = int(os.environ.get("PORT", "8787"))
except ValueError:
    PORT = 8787
    _STARTUP_ERRORS.append("PORT must be an integer; refusing to start")
if not 1 <= PORT <= 65535:
    _STARTUP_ERRORS.append("PORT must be between 1 and 65535; refusing to start")
try:
    MAX_CONCURRENT_WORK = int(os.environ.get("MAX_CONCURRENT_WORK", "4"))
except ValueError:
    MAX_CONCURRENT_WORK = 4
    _STARTUP_ERRORS.append("MAX_CONCURRENT_WORK must be an integer; refusing to start")
if MAX_CONCURRENT_WORK < 0:
    _STARTUP_ERRORS.append("MAX_CONCURRENT_WORK must be >= 0; refusing to start")
    MAX_CONCURRENT_WORK = 0
_WORK = threading.Semaphore(max(MAX_CONCURRENT_WORK, 0))


def _validate_slug(slug):
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        return False
    return not (slug.startswith(("-", "_")) or slug.endswith(("-", "_")) or "--" in slug or "__" in slug)


def _safe(p):
    return os.path.realpath(os.path.abspath(p))


def _allowed(root):
    if not isinstance(root, str) or not root.strip():
        raise ValueError("root must be a non-empty string")
    root = _safe(root)
    if not ALLOWED_ROOTS:
        return root
    for base in ALLOWED_ROOTS:
        base = _safe(base)
        try:
            if os.path.commonpath((root, base)) == base:
                return root
        except ValueError:
            continue
    raise ValueError("root not in ALLOWED_ROOTS: %s" % root)


def _contained(path, root):
    """True when resolved path stays inside the resolved root."""
    path = _safe(path)
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _validate_package_path(path, root, label):
    """Validate a package-derived path against the approved root.

    Raises ValueError when the resolved path escapes root; symlinks whose
    target stays inside root are accepted. Returns True when the path
    exists as a directory, False when absent.
    """
    if not _contained(path, root):
        raise ValueError("%s escapes root" % label)
    return os.path.isdir(path)


def _safe_subprocess_env() -> dict[str, str]:
    blocked = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
    return {
        **{key: value for key, value in os.environ.items() if key not in blocked and not key.startswith("GIT_CONFIG_")},
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }


def _decode_output(value: bytearray) -> str:
    return bytes(value).decode("utf-8", "replace")


def _run(args, cwd, *, timeout=120):
    process = None
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    timed_out = False
    drain_grace_seconds = 5
    try:
        popen_kwargs = {
            "cwd": cwd,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": False,
            "env": _safe_subprocess_env(),
        }
        # A worker may spawn grandchildren which inherit the output pipes.
        # Put the whole tree in its own session so timeout cleanup can kill
        # every descendant, rather than leaving orphaned workers behind.
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen([PY] + args, **popen_kwargs)
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, stdout_buffer)
        selector.register(process.stderr, selectors.EVENT_READ, stderr_buffer)
        deadline = time.monotonic() + timeout
        killed_at = None
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                if killed_at is None:
                    killed_at = time.monotonic()
                # Bound the post-kill drain: a grandchild that inherited the
                # pipes must not hold this worker slot hostage forever.
                deadline = killed_at + drain_grace_seconds
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    for key in list(selector.get_map().values()):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    break
            for key, _ in selector.select(min(remaining, 0.25)):
                fileobj = key.fileobj
                descriptor = fileobj if isinstance(fileobj, int) else fileobj.fileno()
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = key.data
                if len(buffer) < MAX_SUBPROCESS_OUTPUT_BYTES:
                    buffer.extend(chunk[: MAX_SUBPROCESS_OUTPUT_BYTES - len(buffer)])
        process.wait(timeout=5)
        stderr = _decode_output(stderr_buffer)
        if timed_out:
            stderr = "subprocess timed out\n" + stderr
        return process.returncode, _decode_output(stdout_buffer), stderr
    except subprocess.TimeoutExpired:
        if process is not None:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
        return -1, _decode_output(stdout_buffer), "subprocess timed out"
    except Exception as e:  # pragma: no cover
        if process is not None and process.poll() is None:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
        return -1, _decode_output(stdout_buffer), str(e)


def _slug_from_goal(goal):
    """Derive a YYYY-MM-DD_build-<object> slug accepted by Spec.

    Auto-generated slugs carry a random suffix so identical goals on the same
    day map to distinct task packages; callers that pass an explicit slug keep
    idempotent retry semantics.
    """
    today = date.today().isoformat()
    g = goal.strip().lower()
    g = re.sub(r"[^a-z0-9]+", "-", g)
    g = re.sub(r"-+", "-", g).strip("-")
    if not g:
        g = "task-" + hashlib.sha1(goal.encode("utf-8")).hexdigest()[:8]
    suffix = secrets.token_hex(4)
    if len(g) > 31:
        g = g[:31].strip("-")
    return "%s_build-%s-%s" % (today, g, suffix)


def _route_from_json(output):
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return {"stage": None, "decision": None}
    stage = payload.get("stage") if isinstance(payload, dict) else None
    if not isinstance(stage, str) or not stage.isalpha():
        return {"stage": None, "decision": None}
    return {"stage": stage.lower(), "decision": None}


def _route_from_markdown(output):
    match = re.search(r"(?m)^(?:#\s*)?\S+\s+(?:·\s*)?([a-z]+)\s+(?:·\s*)?\d+/\d+", output)
    if not match:
        match = re.search(r"(?m)^\s*(?:→\s*)?/spec:([a-z]+)\s*$", output)
    if not match:
        return {"stage": None, "decision": None}
    return {"stage": match.group(1).lower(), "decision": None}


def _scripts():
    d = os.path.join(SPEC_SKILL_DIR, "scripts")
    return {
        "init": os.path.join(d, "init_spec_package.py"),
        "route": os.path.join(d, "route_spec_package.py"),
        "report": os.path.join(d, "report_spec_package.py"),
        "check": os.path.join(d, "check_spec_package.py"),
        "check_all": os.path.join(d, "check_all_spec_packages.py"),
    }


def _package_paths(root, slug):
    specs = os.path.join(root, ".spec", "specs")
    return os.path.join(specs, slug), os.path.join(specs, "archive", slug)


def _read_status_detail(path, limit=16000):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(limit + 1).strip()[:limit]
    except (OSError, UnicodeError):
        return ""


def _read_closure_source(path, limit=2 * 1024 * 1024):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(limit + 1)
    except (OSError, UnicodeError):
        return ""


def _summary_closure(summary_text):
    marker = "## 问题处置"
    if marker not in summary_text:
        return {"version": "legacy", "issue_count": None}
    section = summary_text.split(marker, 1)[1]
    fence = section.find("```json")
    if fence < 0:
        return {"version": None, "issue_count": None}
    payload_text = section[fence + len("```json") :].lstrip()
    try:
        payload, _end = json.JSONDecoder().raw_decode(payload_text)
    except json.JSONDecodeError:
        return {"version": None, "issue_count": None}
    issues = payload.get("issues") if isinstance(payload, dict) else None
    return {
        "version": payload.get("version") if isinstance(payload, dict) else None,
        "issue_count": len(issues) if isinstance(issues, list) else None,
    }


def _acquire_work():
    return _WORK.acquire(blocking=False)


def _release_work():
    _WORK.release()


def _busy():
    return 429, {"error": "server busy, retry later", "workflow_status": "failed"}


def do_start(body):
    if not isinstance(body, dict):
        return 400, {"error": "JSON object required", "workflow_status": "failed"}

    root = body.get("root") if "root" in body else body.get("project_root", "")
    goal = body.get("goal")
    if not isinstance(root, str) or not root.strip():
        return 400, {"error": "root is required", "workflow_status": "failed"}
    if not isinstance(goal, str) or not goal.strip():
        return 400, {"error": "goal is required", "workflow_status": "failed"}
    if len(goal.encode("utf-8")) > MAX_GOAL_BYTES:
        return 400, {"error": "goal is too large", "workflow_status": "failed"}

    slug = body.get("slug") if "slug" in body else _slug_from_goal(goal)
    if not _validate_slug(slug):
        return 400, {"error": "invalid slug", "workflow_status": "failed"}

    title = body.get("title") or goal.strip().splitlines()[0][:80]
    if not isinstance(title, str) or not title.strip():
        return 400, {"error": "title must be a non-empty string", "workflow_status": "failed"}
    if len(title.encode("utf-8")) > MAX_TITLE_BYTES:
        return 400, {"error": "title is too large", "workflow_status": "failed"}

    try:
        root = _allowed(root)
        for derived in _package_paths(root, slug):
            _validate_package_path(derived, root, "package")
    except ValueError as exc:
        return 400, {"error": str(exc), "workflow_status": "failed"}

    scripts = _scripts()
    init_args = [scripts["init"], "--root", root, "--slug", slug, "--title", title, "--disable-git-hooks"]
    return_code, stdout, stderr = _run(init_args, cwd=root)
    if return_code == 0:
        return 200, {"task_id": slug, "slug": slug, "title": title}
    # Match the exact init message: other failures also contain "exists"
    # (e.g. "integration branch already exists") and must stay 500.
    if "task package already exists" in (stdout + stderr):
        return 200, {"task_id": slug, "slug": slug, "title": title, "note": "exists"}
    sys.stderr.write("[spec-server] init failed slug=%s: %s\n" % (slug, (stderr or stdout)[:500]))
    return 500, {"error": "task initialization failed", "task_id": slug, "workflow_status": "failed"}


def _map_status(route, report_text=""):
    """Map any valid active-package route to running.

    Completion is resolved from the validated archive before this function is
    called, so active lifecycle stages must never depend on display wording.
    """
    stage = route.get("stage")
    if stage in {"new", "status", "run", "check", "done", "push"}:
        return "running"
    return "failed"


def do_result(params):
    slug = params.get("task_id") or params.get("slug")
    root = params.get("root") or ""
    if not isinstance(slug, str) or not slug or not isinstance(root, str) or not root:
        return 400, {"error": "task_id and root are required"}
    if not _validate_slug(slug):
        return 400, {"error": "invalid task_id"}

    try:
        root = _allowed(root)
    except ValueError as exc:
        return 400, {"error": str(exc)}

    active_package, archived_package = _package_paths(root, slug)
    try:
        if _validate_package_path(archived_package, root, "archived package"):
            summary = os.path.join(archived_package, "completion-summary.md")
            if not _contained(summary, root):
                raise ValueError("status detail escapes root")
            scripts = _scripts()
            gate_code, gate_output, gate_error = _run(
                [
                    scripts["check_all"],
                    "--root",
                    root,
                    "--slug",
                    slug,
                    "--require-archived",
                ],
                cwd=root,
            )
            if gate_code != 0:
                return 200, {
                    "task_id": slug,
                    "workflow_status": "failed",
                    "stage": "done",
                    "decision": "invalid-archive",
                    "status_detail": (gate_output or gate_error).strip()[:16000],
                    "closure": {"valid": False, "version": None, "issue_count": None},
                }
            summary_text = _read_status_detail(summary)
            closure = _summary_closure(_read_closure_source(summary))
            closure["valid"] = True
            return 200, {
                "task_id": slug,
                "workflow_status": "completed",
                "stage": "done",
                "decision": "closed",
                "status_detail": summary_text,
                "closure": closure,
            }
        active_exists = _validate_package_path(active_package, root, "package")
    except ValueError as exc:
        return 400, {"error": str(exc), "task_id": slug, "workflow_status": "failed"}
    if not active_exists:
        return 404, {"error": "task not found", "task_id": slug, "workflow_status": "failed"}

    scripts = _scripts()
    route_code, route_output, _ = _run([scripts["route"], "--root", root, "--slug", slug, "--format", "json"], cwd=root)
    route = _route_from_json(route_output) if route_code == 0 else {"stage": None, "decision": None}
    if route.get("stage") is None and route_code == 0:
        route = _route_from_markdown(route_output)

    status_code, status_output, _ = _run(
        [scripts["report"], "--root", root, "--slug", slug, "--view", "status"], cwd=root
    )
    status_detail = status_output.strip() if status_code == 0 else ""

    return 200, {
        "task_id": slug,
        "workflow_status": _map_status(route, status_detail),
        "stage": route.get("stage"),
        "decision": route.get("decision"),
        "status_detail": status_detail[:1000],
    }


def do_health():
    s = _scripts()
    ready = all(os.path.isfile(p) for p in s.values())
    if ready:
        return 200, {"code": 0, "status": "healthy"}
    return 200, {
        "code": 1,
        "status": "degraded",
        "missing": [k for k, p in s.items() if not os.path.isfile(p)],
    }


def _authorized(headers):
    """Constant-time Bearer check; /health callers never reach this.

    Compares bytes: http.server decodes header values as latin-1, so a
    non-ASCII header would make str comparison raise TypeError.
    """
    if not AUTH_TOKEN:
        return True
    supplied = (headers.get("Authorization") or "").strip()
    if not supplied.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(supplied[7:].strip().encode("utf-8"), AUTH_TOKEN.encode("utf-8"))


def _loopback_binding():
    host = HOST.strip().lower()
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _startup_guard_errors():
    errors = list(_STARTUP_ERRORS)
    if not AUTH_TOKEN and not _loopback_binding():
        errors.append(
            "SPEC_SERVER_TOKEN is required when HOST is not loopback (%s); "
            "generate one with: openssl rand -hex 32" % HOST
        )
    return errors


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = b"" if code == 204 else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if CORS_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        if body:
            self.wfile.write(body)

    @staticmethod
    def _local_path(path):
        if ROOT_PREFIX == "/":
            return path
        if path == ROOT_PREFIX:
            return "/"
        if path.startswith(ROOT_PREFIX + "/"):
            return path[len(ROOT_PREFIX) :]
        return path

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = self._local_path(parsed.path)
        if path == "/health":
            return self._send(*do_health())
        if path == "/result":
            if not _authorized(self.headers):
                return self._send(401, {"error": "unauthorized"})
            params = {
                key: values[0] for key, values in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()
            }
            if not _acquire_work():
                return self._send(*_busy())
            try:
                return self._send(*do_result(params))
            finally:
                _release_work()
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = self._local_path(parsed.path)
        if path == "/start" and not _authorized(self.headers):
            return self._send(401, {"error": "unauthorized", "workflow_status": "failed"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "invalid content length", "workflow_status": "failed"})
        if length < 0 or length > MAX_BODY_BYTES:
            return self._send(413, {"error": "request body too large", "workflow_status": "failed"})
        self.connection.settimeout(REQUEST_READ_TIMEOUT)
        try:
            raw = self.rfile.read(length) if length else b"{}"
        except socket.timeout:
            return self._send(408, {"error": "request body read timed out", "workflow_status": "failed"})
        if len(raw) != length:
            return self._send(400, {"error": "incomplete request body", "workflow_status": "failed"})
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._send(400, {"error": "invalid json", "workflow_status": "failed"})
        if path == "/start":
            if not _acquire_work():
                return self._send(*_busy())
            try:
                return self._send(*do_start(body))
            finally:
                _release_work()
        return self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[spec-server] " + (fmt % args) + "\n")


if __name__ == "__main__":
    errors = _startup_guard_errors()
    if errors:
        for err in errors:
            sys.stderr.write("spec-server: %s\n" % err)
        sys.exit(1)
    if not os.path.isdir(SPEC_SKILL_DIR):
        sys.stderr.write("SPEC_SKILL_DIR not found: %s\n" % SPEC_SKILL_DIR)
        sys.exit(1)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    auth_note = "token required" if AUTH_TOKEN else "no auth (loopback only)"
    sys.stderr.write(
        "spec server-mode on %s:%d (root %s, auth: %s, work slots: %d, spec: %s)\n"
        % (HOST, PORT, ROOT_PREFIX, auth_note, MAX_CONCURRENT_WORK, SPEC_SKILL_DIR)
    )
    sys.stderr.write(
        "  POST %s/start   body {root, goal}      -> task_id\n"
        "  GET  %s/result?task_id=&root=          -> workflow_status\n"
        "  GET  %s/health                        -> code+status\n" % (ROOT_PREFIX, ROOT_PREFIX, ROOT_PREFIX)
    )
    httpd.serve_forever()

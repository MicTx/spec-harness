#!/usr/bin/env python3
# scripts/autorun_spawn.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Autorun chain facts and next-round spawn for ``/spec:autorun``.

Subcommands:

  - ``plan``    discover the project planning documents and count feature
                checkboxes (the machine truth for chain termination);
  - ``spawn``   open a new Terminal.app window in the project path running
                the worker CLI in a full interactive session with the
                autorun prompt injected; once the next-round worker is
                confirmed running on the new window's tty, schedule the
                close of the previous round's Terminal window (window
                recycling, fail-open),
                after enforcing the round cap, the single-chain lock, and
                the next-round package presence;
  - ``status``  render the current chain state.

Exit codes are part of the contract:

  - ``0`` success;
  - ``1`` operational refusal or failure (lock held, round cap reached,
         no worker host available, no next-round package, osascript
         failure, unreadable chain state);
  - ``2`` usage error (argparse);
  - ``3`` no qualifying planning document (fail closed: the user must
         write project-level planning first).

Contract output goes to stdout (text, or JSON with ``--format json``);
diagnostics go to stderr. ``spawn`` writes only under ``.spec/autorun/``:
``chain.json`` (latest chain state), ``spawns.jsonl`` (append-only audit),
and the transient ``chain.lock``. The Terminal.app automation is
scope-limited to exactly two spawn steps — this script's autorun round
spawn and the autoplan pass spawn in ``scripts/autoplan_spawn.py`` (which
reuses this module's implementation by import) — both inheriting the
recorded overturn of the 2026-09-03 no-AppleScript policy in the
``2026-10-06_add-autorun-command`` Development Record; no other caller
may reuse it.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NO_PLAN = 3

WORKER_HOSTS: Tuple[str, ...] = ("codex", "pi", "claude")
# Env markers each host CLI sets in its tool subprocesses. Markers inherit
# down the shell chain (a codex session launched from a pi shell carries both
# PI_CODING_AGENT and CODEX_SANDBOX), so multiple markers are disambiguated
# by the nearest-ancestor walk in detect_session_host.
HOST_ENV_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("codex", "CODEX_SANDBOX"),
    ("pi", "PI_CODING_AGENT"),
    ("claude", "CLAUDECODE"),
)
DEFAULT_MAX_ROUNDS = 20
WORKER_START_TIMEOUT_SECONDS = 60
CLOSE_DELAY_SECONDS = 3
CHAIN_DIR_NAME = ".spec/autorun"
CHAIN_STATE_FILE = "chain.json"
CHAIN_SPAWNS_FILE = "spawns.jsonl"
CHAIN_LOCK_FILE = "chain.lock"

FEATURE_CHECKBOX = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s*(\S.*)$")
PLAN_CANDIDATES: Tuple[str, ...] = (
    ".spec/plan.md",
    "PLAN.md",
    "ROADMAP.md",
    "PRD.md",
    "docs/plan.md",
    "docs/roadmap.md",
    "docs/prd.md",
)
PLAN_CANDIDATE_GLOBS: Tuple[str, ...] = ("docs/plans/*.md", "docs/design/*.md")


class AutorunError(Exception):
    """Operational refusal or failure with a user-facing message."""


class NoPlanError(AutorunError):
    """No qualifying planning document was found (exit 3)."""


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_root(raw_root: str) -> Path:
    root = Path(raw_root).expanduser().resolve()
    if not root.is_dir():
        raise AutorunError(f"project root is not a directory: {root}")
    return root


def _under_root(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _candidate_paths(root: Path) -> List[Path]:
    candidates: List[Path] = [root / name for name in PLAN_CANDIDATES]
    for pattern in PLAN_CANDIDATE_GLOBS:
        candidates.extend(sorted(root.glob(pattern)))
    return candidates


def count_features(text: str) -> Dict[str, object]:
    """Count markdown feature checkboxes: ``- [ ]`` unchecked, ``- [x]`` checked."""
    checked = 0
    unchecked = 0
    unchecked_items: List[str] = []
    for line in text.splitlines():
        match = FEATURE_CHECKBOX.match(line)
        if not match:
            continue
        if match.group(1) == " ":
            unchecked += 1
            unchecked_items.append(match.group(2).strip())
        else:
            checked += 1
    return {
        "checked": checked,
        "unchecked": unchecked,
        "total": checked + unchecked,
        "unchecked_items": unchecked_items,
    }


def discover_plan_docs(root: Path, explicit: Optional[str]) -> Tuple[List[Path], List[str]]:
    """Return (qualifying docs, scanned candidates) for the planning-document scan.

    ``explicit`` is a comma-separated list of paths (relative to root or
    absolute); each must exist and stay under root. Without it, the
    conventional candidates are scanned. A candidate qualifies only when
    it contains at least one feature checkbox.
    """
    scanned: List[str] = []
    qualifying: List[Path] = []

    if explicit:
        for raw in explicit.split(","):
            raw = raw.strip()
            if not raw:
                continue
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            candidate = candidate.resolve()
            scanned.append(str(candidate))
            if not _under_root(root, candidate):
                raise AutorunError(f"--plan path escapes the project root: {raw}")
            if not candidate.is_file():
                raise AutorunError(f"--plan path does not exist: {raw}")
            qualifying.append(candidate)
    else:
        for candidate in _candidate_paths(root):
            scanned.append(str(candidate))
            if candidate.is_file():
                qualifying.append(candidate)

    docs: List[Path] = []
    for candidate in qualifying:
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"skipping unreadable planning candidate: {candidate}: {exc}", file=sys.stderr)
            continue
        if count_features(text)["total"]:
            docs.append(candidate)
    return docs, scanned


def plan_payload(root: Path, explicit: Optional[str]) -> Dict[str, object]:
    docs, scanned = discover_plan_docs(root, explicit)
    doc_reports: List[Dict[str, object]] = []
    totals = {"checked": 0, "unchecked": 0, "total": 0}
    for doc in docs:
        counts = count_features(doc.read_text(encoding="utf-8"))
        doc_reports.append(
            {
                "path": str(doc.relative_to(root)) if _under_root(root, doc) else str(doc),
                **counts,
            }
        )
        for key in totals:
            totals[key] += counts[key]  # type: ignore[operator]
    if not doc_reports:
        raise NoPlanError(
            "no qualifying planning document found; scanned: "
            + (", ".join(scanned) if scanned else "(none)")
            + " — write project-level planning (feature checkboxes) before autorun"
        )
    return {
        "project": str(root),
        "docs": doc_reports,
        "totals": totals,
        "complete": totals["unchecked"] == 0,
    }


def _chain_dir(root: Path) -> Path:
    return root / CHAIN_DIR_NAME


def _read_chain_state(root: Path) -> Optional[Dict[str, object]]:
    state_file = _chain_dir(root) / CHAIN_STATE_FILE
    if not state_file.is_file():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutorunError(f"chain state unreadable ({state_file}): {exc}") from exc
    if not isinstance(data, dict):
        raise AutorunError(f"chain state is not a JSON object: {state_file}")
    return data


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _active_package_present(root: Path) -> bool:
    specs_dir = root / ".spec" / "specs"
    if not specs_dir.is_dir():
        return False
    for entry in sorted(specs_dir.iterdir()):
        if entry.name == "archive" or not entry.is_dir():
            continue
        if (entry / "spec.md").is_file():
            return True
    return False


def _ancestor_host_tokens() -> List[str]:
    """Walk the parent-process chain, returning host-name command tokens
    nearest first (the innermost running CLI comes first)."""
    found: List[str] = []
    pid = os.getpid()
    for _ in range(64):
        try:
            probe = subprocess.run(
                ["ps", "-o", "ppid=,command=", "-p", str(pid)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            break
        line = probe.stdout.strip()
        if not line:
            break
        parts = line.split(None, 1)
        if len(parts) < 2:
            break
        try:
            pid = int(parts[0])
        except ValueError:
            break
        if pid <= 1:
            break
        for token in parts[1].split():
            name = token.rstrip("/").rsplit("/", 1)[-1]
            if name in WORKER_HOSTS:
                found.append(name)
                break
    return found


def detect_session_host(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Detect the host CLI running the current session, if identifiable.

    A single env marker wins; multiple markers (shell inheritance) resolve
    to the nearest ancestor actually running one of the marked hosts.
    Returns ``None`` when nothing identifies a host — callers fall back to
    PATH order instead of guessing.
    """
    env = os.environ if env is None else env
    marked = [host for host, marker in HOST_ENV_MARKERS if env.get(marker)]
    if len(marked) == 1:
        return marked[0]
    if len(marked) > 1:
        for ancestor in _ancestor_host_tokens():
            if ancestor in marked:
                return ancestor
    return None


def resolve_worker_host(explicit: Optional[str]) -> Tuple[str, str]:
    """Resolve the worker host and its provenance.

    Order: ``--host`` > ``SPEC_AUTORUN_HOST`` > detected current-session host
    (same-host chain continuation) > PATH order. Same-host continuation is
    the robust default: a pi main session spawns a pi worker, not whatever
    happens to sit first on PATH.
    """
    host = explicit or os.environ.get("SPEC_AUTORUN_HOST") or ""
    if host:
        if host not in WORKER_HOSTS:
            raise AutorunError(f"unknown worker host: {host} (expected one of {WORKER_HOSTS})")
        return host, "flag" if explicit else "env"
    detected = detect_session_host()
    if detected and shutil.which(detected):
        return detected, "session"
    for candidate in WORKER_HOSTS:
        if shutil.which(candidate):
            return candidate, "path"
    raise AutorunError(f"no worker host available on PATH (tried {WORKER_HOSTS})")


def build_prompt(host: str, plan_args: List[str], max_rounds: int) -> str:
    """Build the autorun prompt injected into the next-round session."""
    tail = " ".join(plan_args + [f"--max-rounds {max_rounds}"])
    if host == "claude":
        return f"/spec:autorun {tail}".strip()
    return f"$spec autorun {tail}".strip()


def build_worker_command(host: str, root: Path, prompt: str) -> List[str]:
    """Build the interactive worker argv for the next-round session.

    Every host runs its CLI's normal interactive session (visible TUI,
    session persisted), not a print/exec mode; the prompt is the initial
    message. Recorded overturn of the headless worker choice in the
    ``2026-10-06_add-autorun-command`` Development Record.
    """
    if host == "codex":
        # codex >=0.160 seatbelt denies .git writes under workspace-write, so
        # `git add`/`git commit` die with EPERM at the round's commit stage
        # (verified 2026-10-07 against codex-cli 0.160.1). Carve the repo's
        # .git back into the writable roots; everything else stays sandboxed.
        git_root = json.dumps(str(root / ".git"))
        return [
            "codex",
            "--cd",
            str(root),
            "--sandbox",
            "workspace-write",
            "-c",
            "sandbox_workspace_write.network_access=true",
            "-c",
            f"sandbox_workspace_write.writable_roots=[{git_root}]",
            "--",
            prompt,
        ]
    if host == "pi":
        return ["pi", "--mode", "text", "--", prompt]
    if host == "claude":
        return ["claude", "--dangerously-skip-permissions", prompt]
    raise AutorunError(f"unknown worker host: {host}")


def escape_applescript(text: str) -> str:
    """Escape a string for an AppleScript double-quoted literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _window_lookup_lines(tty_expression: str) -> List[str]:
    """AppleScript lines filling ``match`` with the window id hosting the tab
    whose tty equals ``tty_expression`` (a quoted literal or a variable)."""
    return [
        '\tset match to ""',
        "\trepeat with w in windows",
        "\t\trepeat with t in tabs of w",
        "\t\t\tif tty of t is {} then".format(tty_expression),
        "\t\t\t\tset match to (id of w as string)",
        "\t\t\t\texit repeat",
        "\t\t\tend if",
        "\t\tend repeat",
        "\tend repeat",
    ]


def build_terminal_command(
    root: Path, worker_command: List[str], custom_command: Optional[str] = None
) -> Tuple[List[str], str]:
    """Build the osascript argv opening a Terminal window at ``root``.

    Returns ``(osascript argv, shell command)``. The shell command is
    ``cd <root> && <worker argv>`` — or ``cd <root> && <custom command>``
    when a raw ``--command`` override is given. The AppleScript runs the
    command in a new tab and replies ``"<window id> <tab tty>"`` on
    stdout (the window is found by matching the spawned tab's tty, so no
    front-window race), so the spawn can verify the next round and
    recycle windows.
    """
    if custom_command is not None:
        tail = custom_command
    else:
        tail = " ".join(shlex.quote(part) for part in worker_command)
    shell_command = "cd {} && {}".format(shlex.quote(str(root)), tail)
    applescript = "\n".join(
        [
            'tell application "Terminal"',
            '\tset spawnedTab to do script "{}"'.format(escape_applescript(shell_command)),
            "\tset spawnedTty to tty of spawnedTab",
            *_window_lookup_lines("spawnedTty"),
            '\treturn match & " " & spawnedTty',
            "end tell",
        ]
    )
    return ["osascript", "-e", applescript], shell_command


def parse_spawn_result(stdout: str) -> Tuple[Optional[int], Optional[str]]:
    """Parse the spawn osascript reply ``"<window id> <tab tty>"``."""
    parts = stdout.strip().split()
    if len(parts) != 2:
        return None, None
    raw_id, tty = parts
    try:
        window_id = int(raw_id)
    except ValueError:
        return None, None
    if not tty.startswith("/dev/"):
        return None, None
    return window_id, tty


def controlling_tty() -> Optional[str]:
    """Return the controlling terminal of this process, or ``None``."""
    try:
        fd = os.open("/dev/tty", os.O_RDONLY)
    except OSError:
        return None
    try:
        return os.ttyname(fd)
    except OSError:
        return None
    finally:
        os.close(fd)


def worker_running_on_tty(worker: str, tty: str) -> bool:
    """Check whether a ``worker`` process runs on the given Terminal tty."""
    completed = subprocess.run(
        ["ps", "-axo", "tty=,comm="],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if completed.returncode != 0:
        return False
    tty_name = tty.rsplit("/", 1)[-1]
    for line in completed.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2:
            continue
        if fields[0].strip() == tty_name and os.path.basename(fields[1].strip()) == worker:
            return True
    return False


def build_window_lookup_applescript(tty: str) -> str:
    """Build the AppleScript finding the window id hosting the given tab tty."""
    return "\n".join(
        [
            'tell application "Terminal"',
            *_window_lookup_lines('"{}"'.format(escape_applescript(tty))),
            "\treturn match",
            "end tell",
        ]
    )


def parse_window_lookup(stdout: str) -> Optional[int]:
    """Parse the window-lookup osascript reply into a window id."""
    text = stdout.strip()
    if not text:
        return None
    try:
        return int(text.split()[0])
    except (ValueError, IndexError):
        return None


def build_close_argv(window_id: int, delay_seconds: int) -> List[str]:
    """Build the detached argv closing a Terminal window after a delay."""
    if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0:
        raise AutorunError(f"window id must be a positive integer: {window_id!r}")
    if delay_seconds < 0:
        raise AutorunError(f"close delay must be >= 0: {delay_seconds!r}")
    script = "sleep {}; osascript -e {}".format(
        delay_seconds,
        shlex.quote('tell application "Terminal" to close window id {}'.format(window_id)),
    )
    return ["/bin/sh", "-c", script]


def schedule_window_close(window_id: int, delay_seconds: int) -> None:
    """Launch the detached close helper; it outlives this process group."""
    subprocess.Popen(
        build_close_argv(window_id, delay_seconds),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def recycle_previous_window(
    prev_tty: Optional[str],
    new_window_id: Optional[int],
    new_tty: Optional[str],
    worker_name: str,
    timeout_seconds: Optional[int] = None,
    delay_seconds: Optional[int] = None,
) -> Dict[str, object]:
    """Confirm the next round and close the previous round's Terminal window.

    Fail-open: any skip condition leaves the previous window open and
    returns the recorded reason; the chain continues in the new window.
    """
    if timeout_seconds is None:
        timeout_seconds = WORKER_START_TIMEOUT_SECONDS
    if delay_seconds is None:
        delay_seconds = CLOSE_DELAY_SECONDS
    if prev_tty is None:
        return {"status": "skipped", "reason": "spawning session has no controlling Terminal"}
    if new_window_id is None or new_tty is None:
        return {"status": "skipped", "reason": "spawned window id or tty unavailable from osascript"}
    if worker_name:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if worker_running_on_tty(worker_name, new_tty):
                break
            time.sleep(1.0)
        else:
            return {
                "status": "skipped",
                "reason": "worker {} not observed on {} within {}s".format(worker_name, new_tty, timeout_seconds),
            }
    lookup = subprocess.run(
        ["osascript", "-e", build_window_lookup_applescript(prev_tty)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    prev_window_id = parse_window_lookup(lookup.stdout) if lookup.returncode == 0 else None
    if prev_window_id is None:
        return {"status": "skipped", "reason": "no Terminal window hosts {}".format(prev_tty)}
    if prev_window_id == new_window_id:
        return {"status": "skipped", "reason": "previous window is the spawned window"}
    schedule_window_close(prev_window_id, delay_seconds)
    return {"status": "scheduled", "window_id": prev_window_id, "delay_seconds": delay_seconds}


def spawn_payload(root: Path, args: argparse.Namespace) -> Dict[str, object]:
    """Run the spawn guards, open the Terminal window, and record chain state."""
    plan = plan_payload(root, args.plan)
    if plan["complete"]:
        raise AutorunError("planning documents report every feature checked; the chain is complete, nothing to spawn")
    if not _active_package_present(root):
        raise AutorunError(
            "no active task package under .spec/specs/ — spawn expects the next-round "
            "package created by /spec:new first; refusing to spawn"
        )

    chain_dir = _chain_dir(root)
    chain_dir.mkdir(parents=True, exist_ok=True)
    lock_path = chain_dir / CHAIN_LOCK_FILE
    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AutorunError(
                f"another autorun chain holds {CHAIN_LOCK_FILE} for this project; parallel chains are refused"
            ) from exc

        state = _read_chain_state(root) or {}
        last_round = state.get("round")
        try:
            last_round = int(last_round) if last_round is not None else 0
        except (TypeError, ValueError) as exc:
            raise AutorunError(f"chain state has a non-integer round: {last_round!r}") from exc
        next_round = last_round + 1
        if next_round > args.max_rounds:
            raise AutorunError(f"round cap reached: next round {next_round} exceeds --max-rounds {args.max_rounds}")

        if args.command:
            host = "custom"
            host_source = "custom"
            worker_command: List[str] = []
            worker_name = ""
        else:
            host, host_source = resolve_worker_host(args.host)
            plan_args: List[str] = []
            if args.plan:
                plan_args.append(f"--plan {args.plan}")
            prompt = build_prompt(host, plan_args, args.max_rounds)
            worker_command = build_worker_command(host, root, prompt)
            worker_name = worker_command[0]
        osascript_argv, shell_command = build_terminal_command(root, worker_command, args.command)

        record = {
            "round": next_round,
            "host": host,
            "host_source": host_source,
            "plan_docs": [doc["path"] for doc in plan["docs"]],  # type: ignore[index]
            "max_rounds": args.max_rounds,
            "spawned_at": _utc_now(),
            "shell_command": shell_command,
            "prev_tty": controlling_tty(),
        }

        if args.dry_run:
            return {
                "dry_run": True,
                "osascript": osascript_argv,
                "window_recycle": {"status": "planned"},
                **record,
            }

        completed = subprocess.run(
            osascript_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise AutorunError(f"osascript failed to open the Terminal window: {detail}")
        new_window_id, new_tty = parse_spawn_result(completed.stdout)
        record["window_recycle"] = recycle_previous_window(
            prev_tty=record["prev_tty"],
            new_window_id=new_window_id,
            new_tty=new_tty,
            worker_name=worker_name,
        )

        state_file = chain_dir / CHAIN_STATE_FILE
        state = dict(record)
        state["updated_at"] = state.pop("spawned_at")
        _atomic_write(state_file, json.dumps(state, indent=2, sort_keys=True) + "\n")
        with open(chain_dir / CHAIN_SPAWNS_FILE, "a", encoding="utf-8") as audit:
            audit.write(json.dumps(record, sort_keys=True) + "\n")
        return {"dry_run": False, "terminal": completed.stdout.strip(), **record}


def status_payload(root: Path) -> Dict[str, object]:
    state = _read_chain_state(root)
    payload: Dict[str, object] = {"project": str(root), "chain_state_file": str(_chain_dir(root) / CHAIN_STATE_FILE)}
    if state is None:
        payload["started"] = False
    else:
        payload["started"] = True
        payload.update(state)
    try:
        payload["plan"] = plan_payload(root, None)
    except NoPlanError as exc:
        payload["plan"] = {"error": str(exc)}
    return payload


def _render_plan(payload: Dict[str, object]) -> str:
    lines = [f"project: {payload['project']}"]
    totals = payload["totals"]
    for doc in payload["docs"]:  # type: ignore[union-attr]
        lines.append(
            "- {}: {}/{} checked".format(doc["path"], doc["checked"], doc["total"])  # type: ignore[index]
        )
    lines.append(
        "features: {}/{} checked ({} unchecked)".format(
            totals["checked"],
            totals["total"],
            totals["unchecked"],  # type: ignore[index]
        )
    )
    lines.append("complete: {}".format("yes" if payload["complete"] else "no"))
    return "\n".join(lines)


def _render_spawn(payload: Dict[str, object]) -> str:
    lines = ["round: {}/{}".format(payload["round"], payload["max_rounds"]), "host: {}".format(payload["host"])]
    lines.append("shell_command: {}".format(payload["shell_command"]))
    recycle = payload.get("window_recycle")
    if isinstance(recycle, dict):
        if recycle.get("status") == "scheduled":
            lines.append("close: window {} in {}s".format(recycle["window_id"], recycle["delay_seconds"]))
        elif recycle.get("status") == "planned":
            lines.append("close: planned (previous window closes after the next round is confirmed running)")
        else:
            lines.append("close: skipped ({})".format(recycle.get("reason", "unknown")))
    if payload.get("dry_run"):
        lines.append("dry-run: osascript not executed")
    else:
        lines.append("terminal: {}".format(payload.get("terminal", "")))
    return "\n".join(lines)


def _render_status(payload: Dict[str, object]) -> str:
    lines = [f"project: {payload['project']}"]
    if not payload.get("started"):
        lines.append("chain: not started")
    else:
        lines.append("round: {}".format(payload.get("round")))
        lines.append("host: {}".format(payload.get("host")))
        lines.append("updated_at: {}".format(payload.get("updated_at")))
    plan = payload.get("plan")
    if isinstance(plan, dict):
        if "error" in plan:
            lines.append("plan: {}".format(plan["error"]))
        else:
            lines.append(
                "plan: {}/{} checked, complete={}".format(
                    plan["totals"]["checked"],
                    plan["totals"]["total"],
                    plan["complete"],  # type: ignore[index]
                )
            )
    return "\n".join(lines)


def _emit(payload: Dict[str, object], render, use_json: bool) -> None:
    if use_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(payload))


def _add_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=".", help="project root (default: current directory)")
    parser.add_argument(
        "--plan",
        help="comma-separated planning document paths (default: scan conventional candidates)",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: text)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autorun_spawn.py",
        description="Autorun chain facts and next-round spawn for /spec:autorun.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    plan_parser = subparsers.add_parser("plan", help="discover planning documents and count feature checkboxes")
    _add_plan_args(plan_parser)

    spawn_parser = subparsers.add_parser("spawn", help="open the next-round Terminal window after guards")
    _add_plan_args(spawn_parser)
    spawn_parser.add_argument(
        "--host",
        choices=WORKER_HOSTS,
        help="worker host (default: SPEC_AUTORUN_HOST, else detected session host, else PATH order codex/pi/claude)",
    )
    spawn_parser.add_argument(
        "--command",
        help="raw shell command override run in the window after cd <root> (host recorded as custom)",
    )
    spawn_parser.add_argument(
        "--max-rounds",
        type=int,
        default=DEFAULT_MAX_ROUNDS,
        help=f"round cap (default: {DEFAULT_MAX_ROUNDS})",
    )
    spawn_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the osascript command without executing or recording",
    )

    status_parser = subparsers.add_parser("status", help="render the current chain state")
    status_parser.add_argument("--root", default=".", help="project root (default: current directory)")
    status_parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="output format (default: text)"
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = _resolve_root(args.root)
        if args.subcommand == "plan":
            _emit(plan_payload(root, args.plan), _render_plan, args.format == "json")
        elif args.subcommand == "spawn":
            if args.max_rounds < 1:
                raise AutorunError("--max-rounds must be >= 1")
            _emit(spawn_payload(root, args), _render_spawn, args.format == "json")
        else:
            _emit(status_payload(root), _render_status, args.format == "json")
    except NoPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_PLAN
    except AutorunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

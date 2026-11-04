# scripts/session_context_support.py
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Workspace-scoped session context discovery and bounded transcript reads.

Public seam:

  - ``discover(root, *, home=None, client="unknown", current_session=None,
    limit=12)`` returns schema_version 1 factual candidates per agent.
  - ``read(root, *, agent, session_id, home=None, max_messages=24)``
    re-resolves the id through the same scoped discovery and exports only
    sanitized user/assistant text and recognized summaries. It never
    accepts a transcript pathname and never makes a task decision.

Workspace identity comes from a read-only ``git rev-parse`` probe with all
inherited GIT_* state cleared. Provider bodies are only reached through
``session_adapters`` whitelisted store paths.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import session_adapters as adapters

CLIENT_ALIASES = {
    "claude": "claude",
    "claude-code": "claude",
    "claude-cli": "claude",
    "codex": "codex",
    "codex-cli": "codex",
    "devin": "devin",
    "devin-cli": "devin",
    "pi": "pi",
    "pi-cli": "pi",
    "pi-coding-agent": "pi",
    "cursor": "cursor",
    "cursor-agent": "cursor",
    "opencode": "opencode",
    "opencode-cli": "opencode",
    "gemini": "gemini",
    "gemini-cli": "gemini",
    "copilot": "copilot",
    "copilot-cli": "copilot",
}


def canonical_client(value) -> str:
    if not isinstance(value, str):
        return "unknown"
    return CLIENT_ALIASES.get(value.strip().lower(), "unknown")


SCHEMA_VERSION = 1
GIT_PROBE_TIMEOUT = 10.0


ADVISORIES = ("untrusted-history", "not-completion-evidence")


class SessionContextError(Exception):
    def __init__(self, reason_code: str, message: str = ""):
        super().__init__(message or reason_code)
        self.reason_code = reason_code


def _git_env() -> dict:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _probe(path: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            env=_git_env(),
            timeout=GIT_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


@dataclass
class Workspace:
    requested: Path
    root: Path
    top: Path | None
    git: bool
    branch: str | None
    head: str | None

    def anchor(self) -> dict:
        return {
            "root": str(self.root),
            "branch": self.branch,
            "head": self.head,
            "git": self.git,
        }


EXPORT_MSG_CHAR_CAP = 6000
EXPORT_TOTAL_CHAR_CAP = 24000
_OMISSION_MARKER = "\n[... omitted ...]\n"
_SAFE_RECORD_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _safe_record_id(raw) -> str:
    text = "" if raw is None else str(raw)
    if _SAFE_RECORD_ID.match(text) and ".." not in text and text not in (".", ".."):
        return text
    return "record-" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


class _Scope:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self._cache: dict = {}

    def accepts(self, cwd) -> bool:
        if not isinstance(cwd, str) or not cwd:
            return False
        if cwd in self._cache:
            return self._cache[cwd]
        self._cache[cwd] = self._accepts(cwd)
        return self._cache[cwd]

    def _accepts(self, cwd: str) -> bool:
        try:
            candidate = Path(cwd)
        except (ValueError, OSError):
            return False
        if not candidate.is_absolute():
            return False
        try:
            resolved = candidate.resolve()
        except OSError:
            return False
        workspace = self.workspace
        if not workspace.git:
            return resolved == workspace.root
        top = workspace.top
        if resolved == top:
            return True
        if top not in resolved.parents:
            return False
        existing = resolved
        steps = 0
        while not existing.is_dir() and steps < 128:
            existing = existing.parent
            steps += 1
            if existing == top:
                break
        if not existing.is_dir():
            return False
        if existing == top:
            return True
        if top not in existing.parents:
            return False
        sub_top = _probe(existing, "rev-parse", "--show-toplevel")
        if sub_top is None:
            return False
        try:
            return Path(sub_top).resolve() == top
        except OSError:
            return False

    def relation(self, cwd) -> str | None:
        try:
            resolved = Path(cwd).resolve()
        except (OSError, ValueError):
            return None
        anchor = self.workspace.top if self.workspace.git else self.workspace.root
        if resolved == anchor:
            return "root"
        if anchor in resolved.parents:
            return "descendant"
        return None

    def project_path(self, native_cwd: str):
        try:
            resolved = Path(native_cwd).resolve()
        except (OSError, ValueError):
            return None
        anchor = self.workspace.top if self.workspace.git else self.workspace.root
        if resolved == anchor:
            return "."
        if anchor in resolved.parents:
            return str(resolved.relative_to(anchor))
        return None


def _resolve_workspace(root) -> Workspace:
    requested = Path(root).expanduser()
    if not requested.exists():
        raise SessionContextError("root-missing", f"workspace root does not exist: {root}")
    try:
        canonical = requested.resolve()
    except OSError as exc:
        raise SessionContextError("root-unreadable", str(exc)) from exc
    if not canonical.is_dir():
        raise SessionContextError("root-not-directory", f"workspace root is not a directory: {root}")
    top_out = _probe(canonical, "rev-parse", "--show-toplevel")
    if top_out is None:
        return Workspace(requested, canonical, None, False, None, None)
    try:
        top = Path(top_out).resolve()
    except OSError:
        top = canonical
    branch = _probe(canonical, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        branch = None
    head = _probe(canonical, "rev-parse", "HEAD")
    return Workspace(requested, canonical, top, True, branch, head)


def _resolve_home(home) -> Path:
    return Path(home).expanduser() if home is not None else Path.home()


def _context(workspace: Workspace, home: Path, explicit_home: bool) -> adapters.ScanContext:
    scope = _Scope(workspace)
    raw = str(workspace.requested) if workspace.requested.is_absolute() else str(workspace.root)
    canonical = str(workspace.root)
    top = str(workspace.top) if workspace.top is not None else canonical
    return adapters.ScanContext(
        home=home,
        accepts=scope.accepts,
        relation=scope.relation,
        bucket_paths=tuple(dict.fromkeys((raw, canonical, top))),
        deadline=time.monotonic() + adapters.SOFT_DEADLINE_SECONDS,
        explicit_home=explicit_home,
        budget=adapters.Budget(adapters.TOTAL_DECODE_CAP),
        trust_anchor=home,
        project_path=scope.project_path,
    )


def _ts_value(value) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


_PUBLIC_CANDIDATE_FIELDS = (
    "agent",
    "session_id",
    "title",
    "updated_at",
    "branch",
    "head",
    "relation",
    "has_user",
    "complete",
    "project_path",
    "association",
    "association_confidence",
    "source_format",
    "omissions",
)
_HEAD_HEX_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_WS_RE = re.compile(r"\s")
_ABS_OR_DOT_RE = re.compile(r"^([/\\~]|[A-Za-z]:[\\/]|\s)")


def _clean_branch(value):
    if not isinstance(value, str) or not value:
        return None
    if len(value.encode("utf-8")) > 256:
        return None
    if _ABS_OR_DOT_RE.search(value) or ".." in value or _WS_RE.search(value) or adapters.sanitize_text(value) != value:
        return None
    return value


def _clean_head(value):
    if isinstance(value, str):
        lowered = value.lower()
        if _HEAD_HEX_RE.match(lowered):
            return lowered
    return None


def _clean_project_path(value):
    if not isinstance(value, str) or not value:
        return value if value == "." else None
    if len(value.encode("utf-8")) > 512:
        return None
    if (
        value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or adapters.sanitize_text(value) != value
    ):
        return None
    parts = value.replace("\\", "/").split("/")
    if _ABS_OR_DOT_RE.search(value) or any(part in ("..", "~") for part in parts):
        return None
    return value


def _public(candidate: dict) -> dict:
    out = {key: candidate[key] for key in _PUBLIC_CANDIDATE_FIELDS if key in candidate}
    omissions = []
    for field, cleaner, code in (
        ("branch", _clean_branch, "branch-withheld"),
        ("head", _clean_head, "head-withheld"),
        ("project_path", _clean_project_path, "project-path-withheld"),
    ):
        original = out.get(field)
        cleaned = cleaner(original)
        if original is not None and cleaned is None:
            omissions.append(code)
        out[field] = cleaned
    if omissions:
        out["omissions"] = omissions
        out["complete"] = False
    elif "omissions" in out:
        del out["omissions"]
    return out


def discover(root, *, home=None, client: str = "unknown", current_session=None, limit: int = 12) -> dict:
    workspace = _resolve_workspace(root)
    ctx = _context(workspace, _resolve_home(home), home is not None)
    try:
        cap = max(1, min(int(limit), adapters.HARD_CANDIDATE_CAP))
    except (TypeError, ValueError):
        cap = adapters.DEFAULT_CANDIDATE_LIMIT
    client_name = canonical_client(client)
    order = [n for n in adapters.AGENTS if n == client_name]
    order += [n for n in adapters.AGENTS if n != client_name]
    sources = []
    found = []
    complete = True
    for name in order:
        if name in adapters.UNVERIFIED_AGENTS:
            locations = adapters.UNVERIFIED_LOCATIONS.get(name, ())
            present = any((ctx.home / loc).exists() for loc in locations)
            sources.append(
                adapters.source_entry(
                    name,
                    "unsupported" if present else "absent",
                    adapters.UNVERIFIED_REASON,
                    not present,
                )
            )
            complete = complete and not present
            continue
        adapter = adapters.ADAPTERS[name]
        excluded = frozenset({current_session}) if name == client_name else frozenset()
        provider_ctx = ctx.for_provider(excluded=excluded)
        try:
            result = adapter.discover(provider_ctx)
        except adapters.AdapterError as exc:
            result = {"status": exc.status, "reason_code": exc.reason_code, "complete": False, "candidates": []}
        except Exception:
            result = {"status": "unreadable", "reason_code": "adapter-error", "complete": False, "candidates": []}
        complete = complete and result["complete"]
        sources.append({key: result[key] for key in ("status", "reason_code", "complete")} | {"agent": name})
        found.extend(result["candidates"])
    if isinstance(current_session, str) and client_name != "unknown":
        found = [c for c in found if not (c["agent"] == client_name and c["session_id"] == current_session)]
    found.sort(
        key=lambda c: (
            0 if c["agent"] == client_name else 1,
            -_ts_value(c.get("updated_at")),
            c["session_id"],
        )
    )
    emitted = found[:cap]
    if len(found) > len(emitted):
        complete = False
    status = "partial" if not complete else ("none" if not emitted else "ok")
    publics = [_public(c) for c in emitted]
    if any(p.get("omissions") for p in publics):
        complete = False
        if status == "ok":
            status = "partial"
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": workspace.anchor(),
        "sources": sources,
        "candidates": publics,
        "complete": complete,
        "status": status,
    }


def _freshness(workspace: Workspace, candidate: dict) -> str:
    if not workspace.git:
        return "unknown"
    branch = candidate.get("branch")
    head = candidate.get("head")
    if branch is None and head is None:
        return "unknown"
    fresh = True
    if head is not None:
        fresh = fresh and head == workspace.head
    if branch is not None:
        fresh = fresh and branch == workspace.branch
    return "fresh" if fresh else "stale"


def _error_result(workspace: Workspace | None, status: str, reason_code: str, message: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": workspace.anchor() if workspace else None,
        "status": status,
        "error": {"reason_code": reason_code, "message": message},
        "messages": [],
        "complete": False,
        "authority": "context-only",
        "advisories": list(ADVISORIES),
    }


def read(root, *, agent: str, session_id: str, home=None, max_messages: int = 24) -> dict:
    workspace = _resolve_workspace(root)
    ctx = _context(workspace, _resolve_home(home), home is not None)
    if agent not in adapters.AGENTS:
        return _error_result(workspace, "refused", "unknown-agent", f"unregistered agent: {agent}")
    if not adapters.valid_session_id(session_id):
        return _error_result(workspace, "refused", "invalid-session-id", "session id must be bounded ASCII")
    if agent in adapters.UNVERIFIED_AGENTS:
        return _error_result(workspace, "unavailable", adapters.UNVERIFIED_REASON, f"{agent} format is unverified")
    adapter = adapters.ADAPTERS[agent]
    ctx.target_session_id = session_id
    probe = {"flags": set(), "candidates": []}
    try:
        match = adapter.locate(ctx, session_id, probe) if hasattr(adapter, "locate") else None
    except adapters.AdapterError as exc:
        return _error_result(workspace, "unavailable", exc.reason_code, exc.reason_code)
    except Exception:
        return _error_result(workspace, "unavailable", "adapter-error", "adapter failure")
    source_status = "ok"
    source_reason = None
    if match is None:
        try:
            discovered = adapter.discover(ctx.for_provider())
        except adapters.AdapterError as exc:
            return _error_result(workspace, "unavailable", exc.reason_code, exc.reason_code)
        except Exception:
            return _error_result(workspace, "unavailable", "adapter-error", "adapter failure")
        source_status = discovered.get("status", "ok")
        source_reason = discovered.get("reason_code")
        match = next((c for c in discovered["candidates"] if c["session_id"] == session_id), None)
    if match is None:
        if probe["flags"]:
            return _error_result(
                workspace,
                "unavailable",
                sorted(probe["flags"])[0],
                "source could not be fully read",
            )
        if source_status != "ok":
            return _error_result(
                workspace,
                "unavailable",
                source_reason or "source-incomplete",
                "source could not be fully read",
            )
        return _error_result(
            workspace,
            "unknown_id",
            "unknown-session",
            f"no scoped {agent} session with id {session_id}",
        )
    try:
        payload = adapter.read_messages(ctx, match)
    except adapters.AdapterError as exc:
        return _error_result(workspace, exc.status, exc.reason_code, exc.reason_code)
    except Exception:
        return _error_result(workspace, "refused", "adapter-error", "adapter failure")
    messages = payload["messages"]
    try:
        cap = max(1, min(int(max_messages), adapters.HARD_MESSAGE_CAP))
    except (TypeError, ValueError):
        cap = adapters.DEFAULT_MESSAGE_LIMIT
    truncation = payload["truncation"]
    truncation_reasons = list(payload.get("truncation_reasons") or ([] if not truncation else [truncation]))
    complete = payload["complete"]
    if len(messages) > cap:
        messages = messages[-cap:]
        truncation = "message-limit"
        if "message-limit" not in truncation_reasons:
            truncation_reasons.append("message-limit")
        complete = False
    count = len(messages)
    if count:
        per_message = min(EXPORT_MSG_CHAR_CAP, EXPORT_TOTAL_CHAR_CAP // count)
        for message in messages:
            message["text"] = adapters.sanitize_text(adapters.mask_store_paths(message.get("text") or "", ctx))
            text = message["text"]
            if len(text) > per_message:
                head = int(per_message * 0.4)
                tail = per_message - head - len(_OMISSION_MARKER)
                message["text"] = text[:head] + _OMISSION_MARKER + text[-max(tail, 0) :]
                message["text_truncated"] = True
                if "text-clipped" not in truncation_reasons:
                    truncation_reasons.append("text-clipped")
                complete = False
            message["record_id"] = _safe_record_id(message.get("record_id"))
    cleaned = _public(match)
    if cleaned.get("omissions"):
        if "metadata-omitted" not in truncation_reasons:
            truncation_reasons.append("metadata-omitted")
        complete = False
        if truncation is None:
            truncation = "metadata-omitted"
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": workspace.anchor(),
        "status": "ok",
        "candidate": cleaned,
        "messages": messages,
        "complete": complete,
        "truncation": truncation,
        "truncation_reasons": truncation_reasons,
        "freshness": _freshness(workspace, cleaned),
        "authority": "context-only",
        "advisories": list(ADVISORIES),
    }

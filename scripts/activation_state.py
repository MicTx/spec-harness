#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded admission-assessment records shared by the assessor and the guard.

One record per Claude session, stored inside the Git directory (never the
working tree) so it cannot dirty the project or be committed. A record holds
only the decision, a task id, and a scope digest; prompts, tool arguments, and
paths are never stored. Without Git the record lives in a per-project temp
directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import time
from pathlib import Path

DECISIONS = ("direct", "create", "resume", "defer")
MAX_STATE_BYTES = 4096
#: A "direct" verdict is only trusted for a short window: nothing else bounds it if the user keeps
#: talking in one long session. create/resume are anchored by a package instead.
DIRECT_TTL_SECONDS = 2 * 3600
SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def safe_key(value: object) -> str:
    text = value if isinstance(value, str) else ""
    return "".join(ch if ch in SAFE else "_" for ch in text)[:96] or "unknown"


def valid_session(value: object) -> bool:
    """Any non-empty printable id up to 256 characters. The file name is a hash, so the id never
    reaches the filesystem and nothing about its spelling can traverse or collide."""
    return isinstance(value, str) and 0 < len(value) <= 256 and value.isprintable()


def state_dir(root: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()) / "spec-activation"
    except (OSError, subprocess.SubprocessError):
        pass
    digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"spec-activation-{digest}"


def state_path(root: Path, session: str) -> Path:
    digest = hashlib.sha256(session.encode("utf-8", errors="replace")).hexdigest()[:40]
    return state_dir(root) / f"{digest}.json"


def _trusted_dir(directory: Path) -> bool:
    """The state directory must be ours alone: owned by this user and not group/world writable.

    Inside the Git directory this holds by construction; the non-Git fallback lives under a shared
    temp root where another local user could pre-create it and plant an assessment.
    """
    try:
        info = directory.lstat()
    except OSError:
        return False
    return (
        not stat.S_ISLNK(info.st_mode)
        and stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) & 0o022 == 0
    )


class UnsafeState(OSError):
    """The state location is, or sits under, a symlink; nothing is read or written."""


def _refuse_links(path: Path) -> None:
    """Refuse the state dir, its file, and the dir's parent when any is a symlink.

    The record is written inside the Git directory; a planted link there would
    redirect the write (or a read) to an arbitrary location.
    """
    for candidate in (path, path.parent):
        if candidate.is_symlink():
            raise UnsafeState(f"refusing to follow a symlink in the activation state path: {candidate.name}")


def write_assessment(root: Path, session: str, *, decision: str, task_id: str, scope_digest: str) -> None:
    if decision not in DECISIONS:
        raise ValueError("decision must be one of " + ", ".join(DECISIONS))
    path = state_path(root, session)
    _refuse_links(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _refuse_links(path)
    if not _trusted_dir(path.parent):
        raise UnsafeState("the activation state directory is not private to this user")
    record = {"decision": decision, "task": safe_key(task_id), "scope": safe_key(scope_digest)}
    # O_EXCL | O_NOFOLLOW: never write through a link planted at a guessable temp name.
    tmp = path.with_name(path.name + f".{os.getpid()}.{time.time_ns()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(tmp, flags, 0o600)
    except OSError as exc:
        raise UnsafeState(f"could not create the activation state temp file safely: {exc.__class__.__name__}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True))
        os.replace(tmp, path)
    finally:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()


def read_assessment(root: Path, session: str) -> dict[str, str] | None:
    path = state_path(root, session)
    try:
        _refuse_links(path)
        if not _trusted_dir(path.parent):
            return None
        if path.stat().st_size > MAX_STATE_BYTES:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("decision") not in DECISIONS:
        return None
    return {key: str(record.get(key, "")) for key in ("decision", "task", "scope")}


def invalidate_direct(root: Path, session: str) -> None:
    """A new user request voids a `direct` verdict; a package-backed verdict stays.

    PreToolUse carries no task or scope field, so a fresh UserPromptSubmit is the only real
    signal that the work may have changed. Best effort: a failure here leaves the TTL as backstop.
    """
    path = state_path(root, session)
    try:
        _refuse_links(path)
        record = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and record.get("decision") == "direct":
            path.unlink()
    except (OSError, ValueError):
        pass


def assessment_age_seconds(root: Path, session: str) -> float | None:
    try:
        return max(0.0, time.time() - state_path(root, session).stat().st_mtime)
    except OSError:
        return None

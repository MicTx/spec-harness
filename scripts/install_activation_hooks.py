#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Incremental activation wiring for the Claude activation guard.

Registers ``hooks/claude_activation_guard.py`` into a Claude settings file the
user owns. This is a one-time, explicit choice, never a per-task chore:

    --mode on        register UserPromptSubmit + PreToolUse (write / dispatch tools)
    --mode off       remove only the entries this script owns
    --mode preserve  change nothing (default); reports the current state

Ownership and safety:
- Identity is the relative marker ``hooks/claude_activation_guard.py``, so a
  moved install root is recognized and replaced instead of duplicated.
- Foreign hooks, permissions, and unknown fields are left as they are. A broken
  or non-object settings file is refused, never rewritten.
- Writes are atomic (temp file + replace) after a timestamped backup, so a
  failed write leaves the original intact. Re-running is idempotent.
- It registers only; nothing here starts a process, runs a model, or touches Git.

The ``Bash`` tool is deliberately outside the PreToolUse matcher: arbitrary
shell cannot be parsed reliably, so the guard does not claim to cover it.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from safe_open_support import copy_private, replace_preserving_mode  # noqa: E402

MARKER = "hooks/claude_activation_guard.py"
GUARD_REL = "hooks/claude_activation_guard.py"
ASSESSOR_REL = "scripts/assess_spec_activation.py"
STATE_REL = "scripts/activation_state.py"
GATED_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Agent|Task|SendMessage"
TIMEOUT_SECONDS = 10
MODES = ("on", "off", "preserve")
EVENTS = ("UserPromptSubmit", "PreToolUse")


class WiringError(Exception):
    pass


def settings_path(scope: str, project_root: Path | None) -> Path:
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    if scope == "project":
        if project_root is None:
            raise WiringError("--scope project requires --project-root")
        return project_root / ".claude" / "settings.json"
    raise WiringError(f"unknown scope: {scope}")


def resolve_target(path: Path) -> Path:
    """The file to read and replace. A symlinked settings file (common with dotfile
    managers) keeps its link: we update the target, never swap the link for a copy.
    A dangling link is refused rather than materialised."""
    if path.is_symlink():
        target = path.resolve()
        if not target.exists():
            raise WiringError(f"settings is a dangling symlink (refusing to create its target): {path}")
        return target
    return path


def load_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WiringError(f"settings is not valid JSON (refusing to write): {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WiringError(f"settings top level is not an object (refusing to write): {path}")
    return payload


def _entry(root: Path, rel: str) -> dict[str, Any]:
    # shlex.quote: the root is user-supplied and the host runs this through a shell, so `$()`, backticks
    # and quotes in a directory name must stay literal.
    return {"type": "command", "command": f"python3 {shlex.quote(str(root / rel))}", "timeout": TIMEOUT_SECONDS}


def _is_ours(command: Any) -> bool:
    """Ownership is the registered command shape — `python3 <quoted path>/hooks/claude_activation_guard.py` —
    parsed as a shell would, not any text that merely mentions the file. Older quoting (`python3 "<path>"`)
    parses to the same two words, so earlier installs are still recognised."""
    if not isinstance(command, str):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return len(words) == 2 and words[0] == "python3" and words[1].endswith("/" + MARKER)


def _split(items: Any) -> tuple[list[Any], list[Any]]:
    if items is not None and not isinstance(items, list):
        raise WiringError("a hooks event value is not a list (refusing to overwrite it)")
    own: list[Any] = []
    kept: list[Any] = []
    for item in items or []:
        inner = item.get("hooks") if isinstance(item, dict) else None
        if not isinstance(inner, list):
            kept.append(item)
            continue
        ours = [e for e in inner if isinstance(e, dict) and _is_ours(e.get("command"))]
        rest = [e for e in inner if e not in ours]
        own.extend(ours)
        if rest:
            kept.append({**item, "hooks": rest})
    return own, kept


def apply_on(payload: dict[str, Any], root: Path) -> dict[str, Any]:
    hooks = payload.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise WiringError("settings.hooks is not an object (refusing to write)")
    for event in EVENTS:
        _own, kept = _split(hooks.get(event))
        group: dict[str, Any] = {"hooks": [_entry(root, GUARD_REL)]}
        if event == "PreToolUse":
            group = {"matcher": GATED_MATCHER, **group}
        hooks[event] = [group, *kept]
    return payload


def apply_off(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return payload, 0
    removed = 0
    for event in list(hooks):
        own, kept = _split(hooks.get(event))
        removed += len(own)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:
        payload.pop("hooks", None)
    return payload, removed


def registered(payload: dict[str, Any]) -> dict[str, bool]:
    hooks = payload.get("hooks") if isinstance(payload.get("hooks"), dict) else {}
    return {event: bool(_split(hooks.get(event))[0]) for event in EVENTS}


def write_settings(path: Path, payload: dict[str, Any]) -> None:
    path = resolve_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        copy_private(path, path.with_name(path.name + f".bak-{time.time_ns()}"))
    # settings often hold credentials: keep the file's mode instead of taking the umask default
    replace_preserving_mode(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def run(mode: str, root: Path, scope: str, project_root: Path | None, dry_run: bool) -> dict[str, Any]:
    path = settings_path(scope, project_root)
    resolve_target(path)  # refuse a dangling symlink before anything else
    payload = load_settings(path)
    result: dict[str, Any] = {
        "mode": mode,
        "scope": scope,
        "settings": str(path),
        "dry_run": dry_run,
        "changed": False,
    }
    if mode == "preserve":
        result["registered"] = registered(payload)
        return result
    if mode == "on":
        for rel in (GUARD_REL, ASSESSOR_REL, STATE_REL):
            if not (root / rel).is_file():
                raise WiringError(f"cannot register: {rel} is missing under {root}")
        updated = apply_on(json.loads(json.dumps(payload)), root)
    else:
        if not path.is_file():
            result["registered"] = {event: False for event in EVENTS}
            return result
        updated, _removed = apply_off(json.loads(json.dumps(payload)))
    result["changed"] = updated != payload
    result["registered"] = registered(updated)
    if result["changed"] and not dry_run:
        write_settings(path, updated)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register or remove the Claude activation guard (opt-in).")
    parser.add_argument("--mode", choices=MODES, default="preserve")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="installed skill root")
    parser.add_argument("--scope", choices=("user", "project"), default="user")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(
            args.mode,
            Path(args.root).resolve(),
            args.scope,
            Path(args.project_root).resolve() if args.project_root else None,
            args.dry_run,
        )
    except (WiringError, OSError) as exc:
        print(f"install_activation_hooks: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

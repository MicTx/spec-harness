#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Register Claude Code hooks from a slot manifest.

- Relative `slots/<name>/hooks/<script>` markers identify old entries across
  reinstalls and migrations so --remove can reclaim them.
- Repeated installs update rather than duplicate entries. Foreign matcher groups
  and legacy bare entries are preserved; settings are backed up before writing,
  and corrupt settings are rejected.
- The hook schema stores matcher groups under events and entries inside `hooks`.
  Reinstallation reclaims this slot's old bare entries, which Claude Code ignored,
  and rewrites them as valid groups.
- Keep this script beside slot_registry.py for manifest loading and validation.

Usage:
  python3 scripts/install_slot_hooks.py --slot team-loop [--scope user|project]
  python3 scripts/install_slot_hooks.py --slot team-loop --remove
  python3 scripts/install_slot_hooks.py --slot team-loop --dry-run
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from slot_registry import REPO_ROOT, SlotError, load_manifest, validate_slot  # noqa: E402

HOOK_TIMEOUT_SECONDS = 20


def _atomic_write_private(path: Path, data: bytes, mode: int) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def copy_private(source: Path, destination: Path) -> None:
    _atomic_write_private(destination, source.read_bytes(), source.stat().st_mode & 0o777)


def replace_preserving_mode(path: Path, data: bytes) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    _atomic_write_private(path, data, mode)


class InstallError(Exception):
    pass


def slot_dir_for(root: Path, slot_name: str) -> Path:
    target = root / "slots" / slot_name
    if not target.is_dir():
        raise InstallError(f"slot not found: {slot_name} ({target})")
    problems = validate_slot(target)
    if problems:
        raise InstallError(f"invalid slot manifest; refusing installation: {slot_name}: " + "; ".join(problems))
    return target


def settings_path(scope: str, project_root: Optional[Path]) -> Path:
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    if scope == "project":
        if project_root is None:
            raise InstallError("--scope project requires --project-root")
        target = project_root / ".claude" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    raise InstallError(f"unknown scope: {scope}")


def load_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"settings are corrupt; refusing to write: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InstallError(f"settings must be a top-level object; refusing to write: {path}")
    return payload


def relative_markers(slot_name: str, hook_files: list[Path]) -> list[str]:
    """Identify this slot's hooks independently of the installation root.

    Include the directory marker to reclaim entries for scripts removed from
    the manifest; otherwise --remove would leave dangling hook registrations.
    """
    markers = [f"slots/{slot_name}/hooks/{f.name}" for f in hook_files]
    markers.append(f"slots/{slot_name}/hooks/")
    return markers


def _entry_command(entry: Any) -> str:
    if isinstance(entry, dict):
        cmd = entry.get("command", "")
        return cmd if isinstance(cmd, str) else ""
    return ""


def _is_own(entry: dict[str, Any], markers: set[str]) -> bool:
    return any(m in _entry_command(entry) for m in markers)


def _split_items(items: Any, markers: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate this slot's entries from foreign event entries.

    The official schema places entries in matcher-group hooks, for example:
      "Stop": [{"hooks": [{"type": "command", "command": "..."}]}]
    Older installers wrote bare entries that Claude Code ignored. Preserve
    foreign entries in their original groups or positions so their installers
    remain idempotent; reclaim only entries bearing this slot's marker.
    """
    if not isinstance(items, list):
        return [], []
    own_entries: list[dict[str, Any]] = []
    kept_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            kept_items.append(item)
            continue
        inner = item.get("hooks")
        if isinstance(inner, list):
            # Keep foreign entries in their original matcher group.
            foreign = [e for e in inner if isinstance(e, dict) and not _is_own(e, markers)]
            own_entries.extend(e for e in inner if isinstance(e, dict) and _is_own(e, markers))
            if foreign:
                kept_items.append({**item, "hooks": foreign})
        elif _is_own(item, markers):
            own_entries.append(item)  # reclaim the slot's legacy bare entry
        else:
            kept_items.append(item)  # preserve foreign bare entries
    return own_entries, kept_items


def apply_install(
    payload: dict[str, Any], slot_name: str, slot_dir: Path, hook_events: dict[str, list[str]]
) -> tuple[dict[str, Any], int]:
    """Resolve manifest hook paths from the slot root and write absolute commands."""
    hooks = payload.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError("settings.hooks is not an object; refusing to write")
    markers = set(relative_markers(slot_name, [slot_dir / rel for rels in hook_events.values() for rel in rels]))
    added = 0
    for event, rel_scripts in hook_events.items():
        own_entries, kept_items = _split_items(hooks.get(event), markers)
        new_group: dict[str, Any] = {"hooks": []}
        for rel in rel_scripts:
            new_group["hooks"].append(
                {"type": "command", "command": f'python3 "{slot_dir / rel}"', "timeout": HOOK_TIMEOUT_SECONDS}
            )
            added += 1
        # Place this slot's group first; preserve foreign order and omit empty groups.
        rewritten = [new_group, *kept_items] if kept_items or new_group["hooks"] else kept_items
        if rewritten:
            hooks[event] = rewritten
        else:
            hooks.pop(event, None)
    return payload, added


def apply_remove(
    payload: dict[str, Any], slot_name: str, slot_dir: Path, hook_events: dict[str, list[str]]
) -> tuple[dict[str, Any], int]:
    markers = set(relative_markers(slot_name, [slot_dir / rel for rels in hook_events.values() for rel in rels]))
    hooks = payload.get("hooks")
    removed = 0
    if not isinstance(hooks, dict):
        return payload, 0
    for event in list(hooks):
        own_entries, kept_items = _split_items(hooks.get(event), markers)
        removed += len(own_entries)
        if kept_items:
            hooks[event] = kept_items
        else:
            hooks.pop(event, None)
    if not hooks:
        payload.pop("hooks", None)
    return payload, removed


def write_settings(path: Path, payload: dict[str, Any]) -> None:
    backup = None
    if path.is_file():
        backup = path.with_name(path.name + f".bak-{int(time.time())}")
        copy_private(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    # settings often hold credentials: keep the file's mode instead of taking the umask default
    replace_preserving_mode(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_slot_hooks.py", description="Register or remove Claude Code hooks from a slot manifest"
    )
    parser.add_argument("--slot", required=True)
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--scope", choices=["user", "project"], default="user")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--remove", action="store_true", help="Remove hook entries registered by this slot")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        root = Path(args.root).resolve()
        slot_dir = slot_dir_for(root, args.slot)
        manifest = load_manifest(slot_dir)
        hook_events = {event: list(rels) for event, rels in manifest["hooks"].items()}

        path = settings_path(args.scope, Path(args.project_root).resolve() if args.project_root else None)
        payload = load_settings(path)
        if args.remove:
            payload, changed = apply_remove(copy.deepcopy(payload), args.slot, slot_dir, hook_events)
            action = "removed"
        else:
            payload, changed = apply_install(payload, args.slot, slot_dir, hook_events)
            action = "installed"
        result = {"ok": True, "action": action, "count": changed, "settings": str(path), "dryRun": bool(args.dry_run)}
        if not args.dry_run:
            write_settings(path, payload)
            result["dryRun"] = False
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (InstallError, SlotError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

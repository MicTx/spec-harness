#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""List, show, and validate slot manifests at `slots/<name>/manifest.json`.

Each self-contained slot declares its scripts, hooks, docs, and tests in its
manifest. Validation checks required fields, existing assets, contained paths,
and known Claude Code hook events. It does not load slots dynamically; see
references/slots.md.

Usage:
  python3 scripts/slot_registry.py list [--root .]
  python3 scripts/slot_registry.py show --slot team-loop [--root .]
  python3 scripts/slot_registry.py validate [--root .]   # exit 2 if any slot is invalid
"""

from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = (Path(__file__).resolve().parent.parent).resolve()
SLOTS_DIR_NAME = "slots"

REQUIRED_FIELDS = ("name", "version", "summary", "scripts", "hooks")
KNOWN_HOOK_EVENTS = (
    "UserPromptSubmit",
    "Stop",
    "StopFailure",
    "SubagentStop",
    "TeammateIdle",
    "TaskCompleted",
    "TaskCreated",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "PermissionDenied",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "PostCompact",
    "Notification",
    "PreModelSwitch",
    "PostModelSwitch",
)


class SlotError(Exception):
    """A slot manifest or path is invalid."""


def slots_root(root: Path) -> Path:
    return root / SLOTS_DIR_NAME


def _contained(slot_dir: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(slot_dir.resolve())
    except (ValueError, OSError):
        return False
    return True


def _validate_asset_path(slot_dir: Path, rel: object, kind: str) -> tuple[Path | None, str | None]:
    """Validate a manifest asset path before constructing or opening it.

    Manifest paths are untrusted input.  Keeping this check in one place avoids
    ``Path / rel`` TypeErrors and prevents symlinked assets from quietly
    resolving outside the slot.
    """
    if not isinstance(rel, str):
        return None, f"{kind} path must be a string: {rel!r}"
    if not rel or "\x00" in rel or any(ord(char) < 32 or ord(char) == 127 for char in rel):
        return None, f"{kind} path is empty or contains invalid control characters: {rel!r}"
    candidate_rel = Path(rel)
    if candidate_rel.is_absolute() or ".." in candidate_rel.parts:
        return None, f"{kind} path escapes the slot directory: {rel}"
    candidate = slot_dir / candidate_rel
    if not _contained(slot_dir, candidate):
        return None, f"{kind} path escapes the slot directory: {rel}"
    try:
        relative = candidate.relative_to(slot_dir)
        current = slot_dir
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None, f"{kind} must not be accessed through a symlink: {rel}"
    except (OSError, ValueError):
        return None, f"{kind} path is inaccessible: {rel}"
    return candidate, None


def _check_asset(slot_dir: Path, rel: object, kind: str) -> str | None:
    target, problem = _validate_asset_path(slot_dir, rel, kind)
    if problem:
        return problem
    assert target is not None
    if not target.is_file() or not stat.S_ISREG(target.stat().st_mode):
        return f"{kind} does not exist or is not a regular file: {rel}"
    return None


def load_manifest(slot_dir: Path) -> dict[str, Any]:
    manifest_path = slot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SlotError(f"missing manifest.json: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SlotError(f"failed to parse manifest.json {manifest_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SlotError(f"manifest.json must be a top-level object: {manifest_path}")
    return payload


def validate_slot(slot_dir: Path) -> list[str]:
    """Return validation problems; an empty list means the slot is valid."""
    problems: list[str] = []
    if slot_dir.is_symlink():
        return [f"slot directory must not be a symlink: {slot_dir}"]
    if not slot_dir.is_dir():
        return [f"slot directory does not exist or is not a directory: {slot_dir}"]
    try:
        manifest = load_manifest(slot_dir)
    except SlotError as exc:
        return [str(exc)]

    for field in REQUIRED_FIELDS:
        if field not in manifest:
            problems.append(f"missing required field: {field}")
    if problems:
        return problems

    if not isinstance(manifest.get("name"), str):
        problems.append("name must be a string")
    if not isinstance(manifest.get("version"), str):
        problems.append("version must be a string")
    if not isinstance(manifest.get("summary"), str):
        problems.append("summary must be a string")
    if isinstance(manifest.get("name"), str) and manifest["name"] != slot_dir.name:
        problems.append(f"name does not match directory name: {manifest['name']!r} != {slot_dir.name!r}")

    if not isinstance(manifest.get("scripts"), list) or not manifest["scripts"]:
        problems.append("scripts must be a non-empty list")
    else:
        for rel in manifest["scripts"]:
            problem = _check_asset(slot_dir, rel, "script")
            if problem:
                problems.append(problem)

    hooks = manifest.get("hooks")
    if not isinstance(hooks, dict):
        problems.append("hooks must be an object mapping events to script lists; use an empty object for no hooks")
    else:
        for event, entries in hooks.items():
            if event not in KNOWN_HOOK_EVENTS:
                problems.append(f"unknown hook event: {event}")
            if not isinstance(entries, list) or not entries:
                problems.append(f"hooks.{event} must be a non-empty list")
                continue
            for rel in entries:
                problem = _check_asset(slot_dir, rel, "hook")
                if problem:
                    problems.append(problem)

    docs = manifest.get("docs", [])
    if not isinstance(docs, list):
        problems.append("docs must be a list")
    else:
        for rel in docs:
            problem = _check_asset(slot_dir, rel, "doc")
            if problem:
                problems.append(problem)

    tests = manifest.get("tests")
    if tests is not None:
        if not isinstance(tests, (str, list)):
            problems.append("tests must be a directory string or list")
        else:
            test_paths = [tests] if isinstance(tests, str) else tests
            for rel in test_paths:
                if not isinstance(rel, str):
                    problems.append(f"tests path must be a string: {rel!r}")
                    continue
                target, problem = _validate_asset_path(slot_dir, rel, "tests")
                if problem:
                    problems.append(problem)
                elif target is None or not target.is_dir():
                    problems.append(f"tests directory does not exist: {rel}")

    return problems


def discover_slots(root: Path) -> list[Path]:
    base = slots_root(root)
    if not base.is_dir():
        return []
    return sorted(
        p
        for p in base.iterdir()
        if (p.is_dir() or p.is_symlink()) and ((p / "manifest.json").is_file() or p.is_symlink())
    )


def slot_info(slot_dir: Path, root: Path) -> dict[str, Any]:
    manifest = load_manifest(slot_dir)
    problems = validate_slot(slot_dir)
    return {
        "name": manifest.get("name", slot_dir.name),
        "version": manifest.get("version", "?"),
        "summary": manifest.get("summary", ""),
        "triggers": manifest.get("triggers", []),
        "requires": manifest.get("requires", []),
        "dir": str(slot_dir),
        "valid": not problems,
        "problems": problems,
    }


def _emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slot_registry.py", description="Spec slot registry")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("list", "List all slots and their validity"),
        ("validate", "Validate each slot; exit 2 if any is invalid"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--root", default=str(REPO_ROOT))
    p = sub.add_parser("show", help="Show one slot")
    p.add_argument("--slot", required=True)
    p.add_argument("--root", default=str(REPO_ROOT))
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    slots = discover_slots(root)
    try:
        if args.command == "list":
            _emit([slot_info(p, root) for p in slots])
            return 0
        if args.command == "show":
            target = root / SLOTS_DIR_NAME / args.slot
            if not target.is_dir():
                print(f"error: slot not found: {args.slot}", file=sys.stderr)
                return 2
            _emit(slot_info(target, root))
            return 0
        if args.command == "validate":
            if not slots:
                print("No registered slots", file=sys.stderr)
                return 0
            all_valid = True
            for slot_dir in slots:
                info = slot_info(slot_dir, root)
                state = "OK" if info["valid"] else "INVALID"
                print(f"[{state}] {info['name']} v{info['version']}")
                for problem in info["problems"]:
                    print(f"  - {problem}", file=sys.stderr)
                all_valid = all_valid and info["valid"]
            return 0 if all_valid else 2
    except SlotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

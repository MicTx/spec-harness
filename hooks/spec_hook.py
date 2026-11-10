#!/usr/bin/env python3
"""Canonical session hook dispatcher; host protocol lives in adapters.py."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS = Path(os.environ.get("SPEC_SCRIPTS_DIR", Path(__file__).resolve().parent.parent / "scripts"))
HOOKS = Path(__file__).resolve().parent
for directory in (SCRIPTS, HOOKS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import charter  # noqa: E402
import memory  # noqa: E402
import package  # noqa: E402
import route  # noqa: E402

GIT_GROUP = re.compile(r"\bgit\b.*\b(commit|push)\b")
NO_VERIFY = re.compile(r"(?:^|\s)--no-verify(?:\s|$)")


@dataclass
class Action:
    context: str = ""
    deny: bool = False
    block: bool = False
    reason: str = ""


def _active(event: dict) -> tuple[Path, str] | None:
    try:
        root = package.resolve_root(event.get("cwd") or Path.cwd())
        return root, package.resolve_slug(root)
    except (OSError, ValueError):
        return None


def _write_state(package: Path, values: dict) -> None:
    state = memory.snapshot_read(package)
    state.update(values)
    (package / "state.json").write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")


def _git_command(command: str) -> str:
    match = GIT_GROUP.search(command)
    return match.group(1) if match else ""


def handle(name: str, event: dict) -> Action:
    if name == "pre_tool":
        command = str(event.get("command", ""))
        git_action = _git_command(command)
        if git_action and NO_VERIFY.search(command):
            return Action(deny=True, reason="Spec never permits Git --no-verify.")
        active = _active(event)
        if active and git_action == "push":
            root, slug = active
            _directory, spec, _tasks = package.read_package(root, slug)
            if package.parse_charter(spec).get("V4") != "push":
                return Action(deny=True, reason="Git push is not pre-authorized by charter V4.")
        return Action()
    active = _active(event)
    if not active:
        return Action()
    root, slug = active
    directory, spec, tasks = package.read_package(root, slug)
    if name == "post_edit":
        path = str(event.get("file_path", ""))
        if not path:
            return Action()
        try:
            relative = str(Path(path).resolve().relative_to(root))
        except ValueError:
            relative = path
        if relative == ".spec" or relative.startswith(".spec/"):
            return Action()
        brief = route.decide(root, slug)
        current = brief.get("task") or {}
        allowed = current.get("boundary", []) if isinstance(current, dict) else []
        if not any(package.path_matches(relative, pattern) for pattern in allowed):
            return Action(context=f"Boundary warning: {relative} is outside current task; amend or revert.")
        return Action()
    brief = route.decide(root, slug)
    if name == "stop":
        if event.get("continuing"):
            return Action()
        progress = brief["progress"]
        state = memory.snapshot_read(directory)
        if brief["state"] == "deliver":
            message = "All work is verified; run spec.py done to deliver."
            return Action(block=True, reason=message)
        if brief["state"] == "parked-only":
            return Action(context="parked-only: present every parked item in one batch.")
        if progress != state.get("last_progress"):
            _write_state(directory, {"last_progress": progress, "stalls": 0, "terminal": ""})
            return Action(block=True, reason=route.next_card(root, slug)["next"])
        stalls = int(state.get("stalls", 0)) + 1
        if stalls < 2:
            _write_state(directory, {"stalls": stalls})
            return Action(block=True, reason="No progress: change strategy using the latest failure signature.")
        _write_state(directory, {"stalls": stalls, "terminal": "stalled"})
        return Action(context="stalled: no new evidence after two attempts; report failure signatures.")
    if name in {"start", "prompt", "subagent"}:
        if name == "prompt":
            progress = brief["progress"]
            state = memory.snapshot_read(directory)
            if state.get("last_prompt_progress") == progress:
                return Action()
            _write_state(directory, {"last_prompt_progress": progress})
        prefix = route.brief(root, slug)
        if name == "start" and event.get("source") == "compact":
            prefix += "\nContinue the frozen plan; do not replan."
        if name == "start":
            prefix += "\nCharter: " + charter.summary_line(root)
            lessons = memory.lessons_top(root)
            if lessons:
                prefix += "\nLessons:\n" + "\n".join(lessons)
        if name == "subagent":
            scope = [line[2:] for line in spec.splitlines() if line.startswith(("- in:", "- out:"))]
            prefix += "\nScope: " + "; ".join(scope)
        return Action(context=prefix[:1500])
    if name in {"pre_compact", "end"}:
        memory.snapshot_write(directory, event, brief)
    return Action()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="claude")
    parser.add_argument("event")
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
        from adapters import decode, encode

        event = decode(args.host, payload)
        active = _active(event)
        if args.event == "stop" and active and not event.get("continuing"):
            if memory.seen(package.package_dir(*active), event):
                return 0
        action = handle(args.event, event)
        output = encode(args.host, args.event, action)
        if output:
            print(json.dumps(output, ensure_ascii=False))
    except Exception:
        if args.event == "pre_tool":
            print(json.dumps({"decision": "block", "reason": "Spec hook failed closed for an irreversible command."}))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

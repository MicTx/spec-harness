#!/usr/bin/env python3
"""Canonical session hook dispatcher; host protocol lives in adapters.py."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
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

SHELLS = {"sh", "bash", "zsh", "dash"}
WRAPPERS = {"command", "exec", "nohup", "sudo"}
GIT_DENY = "Spec owns Git actions while a package is active; use spec.py verify/check or /spec:push."


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


def _delivery_binding(event: dict) -> bool:
    if _active(event):
        return True
    try:
        root = package.resolve_root(event.get("cwd") or Path.cwd())
        package.resolve_archived_slug(root)
        return True
    except (OSError, ValueError):
        return False


def _write_state(package: Path, values: dict) -> None:
    state = memory.snapshot_read(package)
    state.update(values)
    (package / "state.json").write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")


def _shell_segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    tokens = list(lexer)
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(char in ";&|()" for char in token):
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)
    return [segment for segment in segments if segment]


def _strip_wrappers(tokens: list[str]) -> list[str]:
    index = 0
    while index < len(tokens):
        token = os.path.basename(tokens[index])
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            index += 1
            continue
        if token == "env":
            index += 1
            while index < len(tokens):
                if tokens[index].startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index]):
                    index += 1
                else:
                    break
            continue
        if token in WRAPPERS:
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            continue
        break
    return tokens[index:]


def _shell_payload(tokens: list[str]) -> str:
    for index, token in enumerate(tokens[1:], 1):
        if token.startswith("-") and not token.startswith("--") and "c" in token[1:]:
            return tokens[index + 1] if index + 1 < len(tokens) else ""
    return ""


def _git_action(args: list[str]) -> str:
    value_options = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
    index = 0
    while index < len(args):
        token = args[index]
        if token in value_options:
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            return token
    return ""


def _git_invocations(command: str, depth: int = 0) -> list[tuple[str, list[str]]]:
    if depth > 3:
        return []
    result: list[tuple[str, list[str]]] = []
    for segment in _shell_segments(command):
        tokens = _strip_wrappers(segment)
        if not tokens:
            continue
        if os.path.basename(tokens[0]) in SHELLS:
            payload = _shell_payload(tokens)
            if payload:
                result.extend(_git_invocations(payload, depth + 1))
            continue
        if os.path.basename(tokens[0]) not in {"git", "git.exe"}:
            continue
        args = tokens[1:]
        result.append((_git_action(args), args))
    return result


def _git_bypass_reason(invocations: list[tuple[str, list[str]]]) -> str:
    for action, args in invocations:
        if any(token == "--no-verify" or token.startswith("--no-verify=") for token in args):
            return "Spec never permits Git --no-verify."
        if action == "commit" and any(
            token.startswith("-") and not token.startswith("--") and "n" in token[1:] for token in args
        ):
            return "Spec never permits Git commit -n/--no-verify."
        if any(token.startswith("core.hooksPath=") for token in args) and "-c" in args:
            return "Spec never permits overriding Git hooks."
    return ""


def handle(name: str, event: dict) -> Action:
    if name == "pre_tool":
        command = str(event.get("command", ""))
        invocations = _git_invocations(command)
        bypass = _git_bypass_reason(invocations)
        if bypass:
            return Action(deny=True, reason=bypass)
        owns_git = any(action in {"commit", "push"} for action, _args in invocations)
        if owns_git and _delivery_binding(event):
            return Action(
                deny=True,
                reason=GIT_DENY,
            )
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
            message = "All work is verified; run spec.py check to deliver."
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

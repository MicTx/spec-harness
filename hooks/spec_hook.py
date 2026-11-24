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
# Hard host ceiling for any injected context; the tier budget is usually lower.
CONTEXT_LIMIT = 1500


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
    """Return True when this branch owns an active or archived package."""
    try:
        root = package.resolve_root(event.get("cwd") or Path.cwd())
    except (OSError, ValueError):
        return False
    try:
        package.resolve_slug(root)
        return True
    except (OSError, ValueError):
        pass
    try:
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


def _session_id(event: dict) -> str:
    for key in ("session_id", "sessionId", "conversation_id"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _short_line(value: str, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _did_execute(directory: Path, event: dict) -> bool:
    state = memory.snapshot_read(directory)
    session = _session_id(event)
    counts = state.get("turn_started", {})
    start = counts.get(session, 0) if isinstance(counts, dict) else 0
    work = [row for row in package.runs(directory)[start:] if row.get("kind") in {"gate", "run", "park"}]
    return bool(work and not work[-1].get("probe"))


def _context_extras(root: Path, name: str, event: dict, spec: str, card: dict) -> list[str]:
    """Reserve a small host-only tail before rendering the shared projection.

    Compact recovery remains in the route's Pending line; it is not copied
    from state here. Small Charter/Lessons summaries stay visible without
    allowing a long goal or lesson avalanche to crowd out Next and Spark.
    """
    extras: list[str] = []
    if name == "start":
        if event.get("source") == "compact":
            extras.append("Continue the frozen plan; do not replan.")
        extras.append("Charter: " + _short_line(charter.summary_line(root), 90))
        task = card.get("task") or {}
        lessons = memory.lessons_top(root, paths=list(task.get("boundary") or []))
        if lessons:
            extras.append("Lessons: " + _short_line(lessons[-1], 80))
    elif name == "subagent":
        scope = [line[2:] for line in spec.splitlines() if line.startswith(("- in:", "- out:"))]
        extras.append("Scope: " + _short_line("; ".join(scope), 200))
    return extras


def handle(name: str, event: dict) -> Action:
    # A continuation is the hook's own follow-up, not a new progress attempt.
    # Release it before package reads, replay bookkeeping, or any projection.
    if name == "stop" and (event.get("continuing") or event.get("aborted") or event.get("paused")):
        return Action()
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
        if name in {"start", "subagent"}:
            try:
                root = package.resolve_root(event.get("cwd") or Path.cwd())
            except (OSError, ValueError):
                return Action()
            card = route.archived_card(root) or route.worktree_card(root)
            if card:
                context = f"Spec {card['slug']}: {card['state']}\n{card['next']}"
                if card.get("worktree"):
                    context += f"\nWorktree: {card['worktree']}"
                return Action(context=context[:1500])
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
        progress = brief.get("progress_token", brief["progress"])
        state = memory.snapshot_read(directory)
        if brief["state"] == "plan":
            return Action()
        if not _did_execute(directory, event):
            return Action(reason=route.next_card(root, slug, projection=brief)["next"] if brief.get("task") else "")
        if brief["state"] == "parked-only":
            return Action(
                context="parked-only: "
                + (
                    "List what you already tried and present up to four decisions with evidence and options."
                    if brief.get("decisions")
                    else "waiting by decision; do not ask again."
                )
            )
        if brief["recovery"].get("exhausted") and not brief.get("ready"):
            return Action(context="stalled: gate recovery budget exhausted; report attempts and remaining blocker.")
        if brief["state"] == "deliver" and state.get("last_progress") != progress:
            _write_state(directory, {"last_progress": progress, "stalls": 0, "terminal": ""})
            message = "All work is verified — you've earned the gate; run spec.py check to deliver."
            return Action(block=True, reason=message)
        if brief["state"] == "check-failed":
            item = brief["gate"].get("action") or {}
            token = [brief["recovery"]["round"], item.get("code"), item.get("subject")]
            if state.get("gate_resume") == token:
                return Action(context="stalled: no recorded recovery attempt; inspect evidence before retrying.")
            _write_state(directory, {"gate_resume": token})
            return Action(block=True, reason=route.brief(root, slug, projection=brief))
        if progress != state.get("last_progress"):
            _write_state(directory, {"last_progress": progress, "stalls": 0, "terminal": ""})
            return Action(block=True, reason=route.next_card(root, slug, projection=brief)["next"])
        stalls = int(state.get("stalls", 0)) + 1
        if stalls < 2:
            _write_state(directory, {"stalls": stalls})
            return Action(
                block=True,
                reason=(
                    "No progress yet — a great moment to diverge: change strategy using the latest failure signature."
                ),
            )
        _write_state(directory, {"stalls": stalls, "terminal": "stalled"})
        return Action(
            context=(
                "stalled: no new evidence after two attempts; report failure signatures "
                "— a fresh angle often cracks what force cannot."
                " Choose the recovery yourself; do not ask the user to choose."
            )
        )
    if name in {"start", "prompt", "subagent"}:
        if name == "prompt":
            session = _session_id(event)
            previous = memory.snapshot_read(directory).get("turn_started") or {}
            previous.pop(session, None)
            previous[session] = len(package.runs(directory))
            _write_state(directory, {"turn_started": dict(list(previous.items())[-8:])})
            if not memory.prompt_fresh(directory, session, memory.prompt_token(directory, brief)):
                return Action()
        # One decision, one card: the route renderer owns the brief budget
        # and its priority order, so start/subagent cost exactly one route
        # computation per event and never render a second Spark line.
        card = route.next_card(root, slug, projection=brief)
        limit = min(CONTEXT_LIMIT, route.brief_budget())
        extras = _context_extras(root, name, event, spec, card)
        # At most 230 characters of host context: reserve them before the
        # renderer allocates space, rather than clipping Next/Spark afterward.
        reserve = sum(len(line) + 1 for line in extras)
        prefix = route.brief(root, slug, projection=card, budget=limit - reserve)
        return Action(context="\n".join([prefix, *extras]))
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
        if args.event == "stop":
            if event.get("continuing"):
                return 0
            active = _active(event)
            if active and memory.seen(package.package_dir(*active), event):
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

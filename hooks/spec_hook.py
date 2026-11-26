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

SHELLS = {"sh", "bash", "zsh", "dash"}
WRAPPERS = {"command", "exec", "nohup", "sudo"}
GIT_DENY = "Spec owns Git actions while a package is active; use spec.py verify/check or /spec:push."
GIT_PROTECT = "Spec protects checkpointed work while a package is active; use spec.py verify/check or /spec:push."
# Hard host ceiling for any injected context; the tier budget is usually lower.
CONTEXT_LIMIT = 1500


@dataclass
class Action:
    context: str = ""
    deny: bool = False
    block: bool = False
    reason: str = ""


def _spec_root(event: dict) -> Path | None:
    """Return the nearest repository root that contains `.spec`, else None.

    The walk stops at the first directory holding `.git` (directory or gitfile,
    so linked worktrees count). A nested repository without `.spec` ends the
    search: a submodule inside a Spec repository is not a Spec repository.
    """
    try:
        current = Path(event.get("cwd") or Path.cwd()).resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate if (candidate / ".spec").exists() else None
    return None


def _runtime():
    """Import the Spec runtime lazily so non-Spec events stay cheap."""
    import charter
    import memory
    import package
    import route

    return charter, memory, package, route


def _active(package, event: dict) -> tuple[Path, str] | None:
    try:
        root = package.resolve_root(event.get("cwd") or Path.cwd())
        return root, package.resolve_slug(root)
    except (OSError, ValueError):
        return None


def _git_dir(root: Path) -> Path | None:
    """Return the repository's git dir, following a worktree gitfile."""
    marker = root / ".git"
    try:
        if marker.is_dir():
            return marker
        if marker.is_file():
            for line in marker.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("gitdir:"):
                    target = Path(line.split(":", 1)[1].strip())
                    return target if target.is_absolute() else (root / target).resolve()
    except OSError:
        return None
    return None


def _head_branch(root: Path) -> str:
    """Return the checked-out branch name without spawning git."""
    git_dir = _git_dir(root)
    if git_dir is None:
        return ""
    try:
        head = git_dir.joinpath("HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if head.startswith("ref: refs/heads/"):
        return head.split("ref: refs/heads/", 1)[1].strip()
    return ""


def _spec_branch(spec_path: Path, *, active: bool = False) -> str:
    try:
        text = spec_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^- branch:\s*`?([^\s`;]+)`?", text, re.MULTILINE)
    except OSError:
        return ""
    if not match:
        return ""
    branch = match.group(1)
    if re.search(r"(?m)^- kind: quick\s*$", text):
        return branch if active else ""
    if branch in {"main", "master", "develop"} or branch.startswith("release/"):
        return ""
    return branch


def _binding_slugs(spec_root: Path) -> tuple[bool, bool]:
    """Return (active_bound, archived_bound) for the checked-out branch.

    Filesystem-only mirror of package.resolve_slug/resolve_archived_slug for
    the pre_tool gate: read HEAD once and match ``- branch:`` lines instead of
    spawning ``git branch --show-current``.
    """
    branch = _head_branch(spec_root)
    if not branch:
        return False, False
    specs = spec_root / ".spec" / "specs"
    active = archived = False
    try:
        children = list(specs.iterdir())
    except OSError:
        return False, False
    for child in children:
        if child.name == "archive" or not child.is_dir():
            continue
        if _spec_branch(child / "spec.md", active=True) == branch:
            active = True
            break
    archive = specs / "archive"
    try:
        archived_children = list(archive.iterdir())
    except OSError:
        archived_children = []
    for child in archived_children:
        if child.is_dir() and _spec_branch(child / "spec.md") == branch:
            archived = True
            break
    return active, archived


def _write_state(memory, package_dir: Path, values: dict) -> None:
    state = memory.snapshot_read(package_dir)
    state.update(values)
    (package_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")


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


def _git_action_index(args: list[str]) -> int:
    """Index of the git subcommand token, skipping global options."""
    value_options = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
    index = 0
    while index < len(args):
        token = args[index]
        if token in value_options:
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            return index
    return -1


def _git_action(args: list[str]) -> str:
    index = _git_action_index(args)
    return args[index] if index >= 0 else ""


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


def _short_flags(flags: list[str], letter: str) -> bool:
    """True when a combined short-option cluster contains `letter`."""
    return any(token.startswith("-") and not token.startswith("--") and letter in token[1:] for token in flags)


def _git_destructive_reason(invocations: list[tuple[str, list[str]]]) -> str:
    """Return a denial reason for forms that can destroy checkpointed work."""
    for action, args in invocations:
        index = _git_action_index(args)
        rest = args[index + 1 :] if index >= 0 else []
        operands = [token for token in rest if not token.startswith("-")]
        flags = [token for token in rest if token.startswith("-")]
        if action == "reset" and "--hard" in flags:
            return GIT_PROTECT + " git reset --hard discards uncommitted work."
        if action == "clean":
            force = "--force" in flags or _short_flags(flags, "f")
            dry = "--dry-run" in flags or _short_flags(flags, "n")
            if force and not dry:
                return GIT_PROTECT + " forced git clean deletes untracked files."
        if action == "stash":
            subcommand = operands[0] if operands else "push"
            if subcommand not in {"list", "show"}:
                return GIT_PROTECT + " mutating git stash can drop checkpointed work."
        if action == "branch":
            force_delete = "-D" in flags or _short_flags(flags, "D")
            delete = "-d" in flags or "--delete" in flags or _short_flags(flags, "d") or _short_flags(flags, "D")
            force = "--force" in flags or _short_flags(flags, "f") or _short_flags(flags, "F")
            if force_delete or (delete and force):
                return GIT_PROTECT + " forced git branch delete can drop unmerged work."
        if action == "worktree":
            subcommand = operands[0] if operands else ""
            if subcommand == "remove":
                return GIT_PROTECT + " git worktree remove can delete the bound worktree."
    return ""


def _raw_git_scan(command: str) -> dict:
    """Conservative raw-text facts about a command shlex could not lex."""
    text = command.lower()
    return {
        "git": bool(re.search(r"\bgit\b", text)),
        "commit_push": bool(re.search(r"\bgit\b[^;&|]*\b(commit|push)\b", text)),
        "bypass": bool(re.search(r"--no-verify|core\.hookspath|\bgit\b[^;&|]*\bcommit\b[^;&|]*\s-n\b", text)),
        "destructive": bool(
            re.search(
                r"\breset\b[^;&|]*--hard|\bclean\b[^;&|]*-[a-z]*f|\bstash\b|\bbranch\b[^;&|]*-[a-z]*d|\bworktree\b[^;&|]*\bremove\b",
                text,
            )
        ),
    }


def _session_id(event: dict) -> str:
    for key in ("session_id", "sessionId", "conversation_id"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _short_line(value: str, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _did_execute(memory, package, directory: Path, event: dict) -> bool:
    state = memory.snapshot_read(directory)
    session = _session_id(event)
    counts = state.get("turn_started", {})
    start = counts.get(session, 0) if isinstance(counts, dict) else 0
    return any(
        row.get("kind") in {"gate", "run", "park", "unblock"} and not row.get("probe")
        for row in package.runs(directory)[start:]
    )


def _retire_legacy_git_hooks(root: Path) -> list[str]:
    """Built-in repair at session start: legacy Spec Git hooks call a checker this runtime no longer ships."""
    try:
        import gitx

        retired = gitx.legacy_git_hooks(root, retire=True)
        remaining = gitx.legacy_git_hooks(root)
    except (OSError, ValueError) as exc:
        return [f"Spec could not retire legacy Git hooks ({exc}); run spec.py doctor --fix."]
    notes = []
    if retired:
        names = ", ".join(sorted(Path(path).name.split(".spec-retired-")[0] for path in retired))
        notes.append(f"Spec retired legacy Git hooks ({names}; kept as *.spec-retired-*); session guards replace them.")
    if remaining:
        names = ", ".join(sorted(Path(path).name for path in remaining))
        notes.append(
            f"Spec could not retire Git hooks ({names}); they block commits. "
            "Check their permissions, then run spec.py doctor --fix."
        )
    return notes


def _context_extras(memory, root: Path, name: str, event: dict, spec: str, card: dict, policy: str) -> list[str]:
    """Reserve a small host-only tail before rendering the shared projection.

    Compact recovery remains in the route's Pending line; it is not copied
    from state here. The small Charter summary cannot crowd out Next.
    """
    extras: list[str] = []
    if name == "start":
        if event.get("source") == "compact":
            extras.append("Continue the frozen plan; do not replan.")
        extras.extend(_retire_legacy_git_hooks(root))
        extras.append("Charter: " + _short_line(policy, 90))
    return extras


def _pre_tool(event: dict) -> Action:
    command = str(event.get("command", ""))
    spec_root = _spec_root(event)
    if spec_root is None:
        return Action()
    try:
        invocations = _git_invocations(command)
    except Exception:
        return _pre_tool_fallback(event, spec_root)
    bypass = _git_bypass_reason(invocations)
    if bypass:
        return Action(deny=True, reason=bypass)
    # Resolve the package binding only when the command can touch Git state;
    # harmless commands never pay for HEAD/spec.md reads.
    owns_git = any(action in {"commit", "push"} for action, _args in invocations)
    destructive_actions = any(
        action in {"reset", "clean", "stash", "branch", "worktree"} for action, _args in invocations
    )
    if owns_git or destructive_actions:
        active, archived = _binding_slugs(spec_root)
        if owns_git and (active or archived):
            return Action(deny=True, reason=GIT_DENY)
        if destructive_actions and active:
            destructive = _git_destructive_reason(invocations)
            if destructive:
                return Action(deny=True, reason=destructive)
    return Action()


def _pre_tool_fallback(event: dict, spec_root: Path | None = None) -> Action:
    """Decide pre_tool when lexing failed: raw-text scan inside a Spec repo."""
    command = str(event.get("command", ""))
    if spec_root is None:
        spec_root = _spec_root(event)
    if spec_root is None:
        return Action()
    scan = _raw_git_scan(command)
    if not scan["git"]:
        return Action()
    active, archived = _binding_slugs(spec_root)
    if scan["bypass"]:
        return Action(deny=True, reason="Spec never permits Git hook bypass flags.")
    if scan["commit_push"] and (active or archived):
        return Action(deny=True, reason=GIT_DENY)
    if scan["destructive"] and active:
        return Action(deny=True, reason=GIT_PROTECT + " an unparsable command names destructive Git.")
    return Action()


def handle(name: str, event: dict) -> Action:
    if name not in {"start", "prompt", "pre_tool", "stop", "pre_compact", "end"}:
        return Action()
    # A continuation is the hook's own follow-up, not a new progress attempt.
    # Release it before package reads, replay bookkeeping, or any projection.
    if name == "stop" and (event.get("continuing") or event.get("aborted") or event.get("paused")):
        return Action()
    if name == "pre_tool":
        return _pre_tool(event)
    charter, memory, package, route = _runtime()
    active = _active(package, event)
    if not active:
        if name == "start":
            try:
                root = package.resolve_root(event.get("cwd") or Path.cwd())
            except (OSError, ValueError):
                return Action()
            notes = _retire_legacy_git_hooks(root)
            card = route.archived_card(root) or route.worktree_card(root)
            if card:
                notes.append(f"Spec {card['slug']}: {card['state']}\n{card['next']}")
                if card.get("worktree"):
                    notes.append(f"Worktree: {card['worktree']}")
            return Action(context="\n".join(notes)[:1500])
        return Action()
    root, slug = active
    directory, spec, tasks = package.read_package(root, slug)
    brief = route.decide(root, slug)
    if name == "stop":
        if "pending" in event:
            memory.snapshot_write(directory, event, brief)
        progress = brief.get("progress_token", brief["progress"])
        state = memory.snapshot_read(directory)
        if brief["state"] == "plan":
            return Action()
        if not _did_execute(memory, package, directory, event):
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
        if brief["state"] == "deliver" and state.get("last_progress") != progress:
            _write_state(memory, directory, {"last_progress": progress, "stalls": 0, "terminal": ""})
            message = "All work is verified — you've earned the gate; run spec.py check to deliver."
            return Action(block=True, reason=message)
        if brief["state"] == "check-failed":
            item = brief["gate"].get("action") or {}
            token = [*progress[:1], progress[2], item.get("code"), item.get("subject")]
            if state.get("gate_resume") == token:
                return Action(context="stalled: the same delivery blocker remains without a new checkpoint.")
            _write_state(memory, directory, {"gate_resume": token})
            return Action(block=True, reason=route.brief(root, slug, projection=brief))
        if progress != state.get("last_progress"):
            _write_state(memory, directory, {"last_progress": progress, "stalls": 0, "terminal": ""})
            return Action(block=True, reason=route.next_card(root, slug, projection=brief)["next"])
        stalls = int(state.get("stalls", 0)) + 1
        if stalls < 2:
            _write_state(memory, directory, {"stalls": stalls})
            return Action(
                block=True,
                reason=(
                    "No progress yet — a great moment to diverge: change strategy using the latest failure signature."
                ),
            )
        _write_state(memory, directory, {"stalls": stalls, "terminal": "stalled"})
        return Action(
            context=(
                "stalled: no new evidence after two attempts; report failure signatures "
                "— a fresh angle often cracks what force cannot."
                " Choose the recovery yourself; do not ask the user to choose."
            )
        )
    if name in {"start", "prompt"}:
        if name == "prompt":
            session = _session_id(event)
            previous = memory.snapshot_read(directory).get("turn_started") or {}
            previous.pop(session, None)
            previous[session] = len(package.runs(directory))
            _write_state(memory, directory, {"turn_started": dict(list(previous.items())[-8:])})
            if not memory.prompt_fresh(directory, session, memory.prompt_token(directory, brief)):
                return Action()
        # One decision, one card: the route renderer owns the brief budget
        # and its priority order, so each event computes one route projection.
        card = route.next_card(root, slug, projection=brief)
        limit = min(CONTEXT_LIMIT, route.BRIEF_BUDGET)
        policy = charter.summary_line(root, package.frozen_charter(directory, spec)) if name == "start" else ""
        extras = _context_extras(memory, root, name, event, spec, card, policy)
        # Reserve host context before the renderer allocates the shared budget.
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
    event: dict = {}
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
        from adapters import decode, encode

        event = decode(args.host, payload)
        if _spec_root(event) is None:
            return 0
        if args.event == "stop":
            if event.get("continuing"):
                return 0
            charter, memory, package, route = _runtime()
            active = _active(package, event)
            if active and memory.seen(package.package_dir(*active), event):
                return 0
        action = handle(args.event, event)
        output = encode(args.host, args.event, action)
        if output:
            print(json.dumps(output, ensure_ascii=False))
    except Exception:
        if args.event == "pre_tool":
            try:
                action = _pre_tool_fallback(event)
            except Exception:
                action = Action()
            if action.deny:
                print(json.dumps({"decision": "block", "reason": action.reason}, ensure_ascii=False))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

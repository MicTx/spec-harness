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
DELIVER_NEXT = "All work is verified — run spec.py check to deliver."
REPLAN_NEXT = "Replan from the evidence: choose a distinct testable approach and preserve the frozen acceptance."
STOP_EVIDENCE = {"gate", "run", "park", "unblock", "amend", "park-decision"}


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


def _begin_prompt(memory, package, directory: Path, event: dict, brief: dict) -> bool:
    """A replay is not user authority, and a new turn need not change the routing brief."""
    session = _session_id(event)
    state = memory.snapshot_read(directory)
    prompt_id = event.get("prompt_id") or event.get("generation_id") or event.get("message_id")
    key = [session, prompt_id] if isinstance(prompt_id, str) and 0 < len(prompt_id) <= 256 else None
    recent = state.get("recent_prompts", [])
    recent = recent if isinstance(recent, list) else []
    if key and key in recent:
        return False
    fresh = memory.prompt_fresh(directory, session, memory.prompt_token(directory, brief))
    starts = state.get("turn_started")
    previous = {k: v for k, v in starts.items() if k != session} if isinstance(starts, dict) else {}
    starts = dict(list({**previous, session: len(package.runs(directory))}.items())[-8:])
    updates: dict[str, object] = {"turn_started": starts}
    if key:
        updates["recent_prompts"] = [*recent[-15:], key]
    _write_state(memory, directory, updates)
    return fresh


def _turn_start(state: dict, event: dict, row_count: int) -> int:
    starts = state.get("turn_started")
    start = starts.get(_session_id(event), 0) if isinstance(starts, dict) else 0
    return start if type(start) is int and 0 <= start <= row_count else row_count


def _gate_blockers(row: dict) -> frozenset[tuple[str, str]]:
    return frozenset(
        (str(item.get("code", "")), str(item.get("subject", "")))
        for item in row.get("findings", [])
        if isinstance(item, dict) and item.get("severity") == "block"
    )


def _gate_diagnostics(row: dict) -> frozenset[str]:
    return frozenset(
        str(item["signature"])
        for item in row.get("findings", [])
        if isinstance(item, dict) and item.get("code") == "verify-failed" and item.get("signature")
    )


def _new_gate_evidence(current: dict, past: list[dict]) -> bool:
    blockers = _gate_blockers(current)
    if not blockers:
        return False
    similar = [
        row for row in past if row.get("kind") == "gate" and not row.get("probe") and _gate_blockers(row) == blockers
    ]
    if not similar:
        return True
    tree = current.get("product_tree")
    diagnostics = _gate_diagnostics(current)
    return bool(tree and diagnostics) and not any(
        row.get("product_tree") == tree or _gate_diagnostics(row) == diagnostics for row in similar
    )


def _new_execution_evidence(current: dict, prior: list[dict]) -> bool:
    """Reject repeats across the ledger, independent of callbacks, prompt reentry and timestamps."""
    kind = current.get("kind", "run")
    if kind == "gate":
        return _new_gate_evidence(current, prior)
    if kind != "run":
        return False
    if current.get("exit") == 0:
        return bool(current.get("checkpoint"))
    fields = ("tree_fp", "sig")
    if not all(current.get(field) for field in fields):
        return False
    return not any(
        row.get("kind", "run") == "run"
        and row.get("exit") not in (None, 0)
        and (row.get("task"), row.get("cmd")) == (current.get("task"), current.get("cmd"))
        and any(row.get(field) == current[field] for field in fields)
        for row in prior
    )


def _replan_reason(route, root: Path, slug: str, brief: dict, task: str) -> str:
    """Ask the agent to plan from evidence without changing goal, proof or authority."""
    goal = str(brief.get("goal") or "").removeprefix("goal:").strip()
    scoped = [item for item in brief.get("criteria", []) if item.get("task") == task and not item.get("passed")]
    acceptance = "; ".join(_short_line(f"{item['id']} {item['text']}", 112) for item in scoped[:2])
    lines = [REPLAN_NEXT, f"Evidence: .spec/specs/{slug}/runs.jsonl"]
    if goal:
        lines.append("Goal: " + _short_line(goal, 140))
    if acceptance:
        lines.append("Criteria: " + acceptance)
    if task:
        lines.append(f"Task: {_short_line(task, 80)}. Test a new hypothesis or finish an independent ready card.")
    prefix = "\n".join(lines)
    next_step = route.brief(root, slug, projection=brief, budget=route.BRIEF_BUDGET - len(prefix) - 1)
    return (prefix + "\n" + next_step)[: route.BRIEF_BUDGET]


def _stop_action(memory, package, route, root: Path, slug: str, directory: Path, event: dict) -> Action:
    state = memory.snapshot_read(directory)
    rows = package.runs(directory)
    start = _turn_start(state, event, len(rows))
    consumed = state.get("stop_evidence", 0)
    if event.get("continuing") and state.get("stop_session") != _session_id(event):
        return Action()
    if type(consumed) is int and 0 < consumed <= len(rows):
        start = max(start, consumed)
    elif event.get("continuing"):
        return Action()
    pending = "pending" in event
    index = next(
        (
            i
            for i in range(len(rows) - 1, start - 1, -1)
            if rows[i].get("kind", "run") in STOP_EVIDENCE and not rows[i].get("probe")
        ),
        None,
    )
    # A pending snapshot can be updated independently of whether Stop resumes.
    if index is None and not pending:
        if consumed and not _session_id(event) and not event.get("continuing"):
            return Action(context="stalled: no new execution evidence.")
        return Action()
    brief = route.decide(root, slug)
    if pending:
        memory.snapshot_write(directory, event, brief)
    if index is None or brief["state"] == "plan":
        return Action()
    action = _settle(route, root, slug, brief, rows[index], rows[:index])
    _write_state(
        memory, directory, {"stop_evidence": len(rows), "stop_session": _session_id(event) if action.block else None}
    )
    return action


def _settle(route, root: Path, slug: str, brief: dict, current: dict, prior: list[dict]) -> Action:
    state = brief["state"]
    if state == "parked-only":
        context = (
            "List what you already tried and present up to four decisions with evidence and options."
            if brief.get("decisions")
            else "waiting by decision; do not ask again."
        )
        return Action(context="parked-only: " + context)
    blocked = brief["recovery"].get("blocked")
    if state == "stalled" and blocked:
        return Action(context=("stalled: " + route.brief(root, slug, projection=brief))[: route.BRIEF_BUDGET])
    if (
        current.get("blocked")
        and brief.get("ready")
        and not any(
            row.get("task") == current.get("task") and row.get("cmd") == current.get("cmd") and row.get("blocked")
            for row in prior
        )
    ):
        return Action(block=True, reason=route.brief(root, slug, projection=brief))
    if not _new_execution_evidence(current, prior):
        return Action(
            context="stalled: no new execution evidence; report the blocker and do not ask the user to choose a fix."
        )
    if state == "deliver":
        return Action(block=True, reason=DELIVER_NEXT)
    if current.get("exit") == 0 and current.get("checkpoint"):
        return Action(block=True, reason=route.brief(root, slug, projection=brief))
    gate_subject = (brief["gate"].get("action") or {}).get("subject", "")
    task = current.get("task") or gate_subject
    return Action(block=True, reason=_replan_reason(route, root, slug, brief, task))


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
            extras.append("Keep the frozen goal and acceptance; adapt the next step to the evidence.")
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
    if name == "stop" and (event.get("aborted") or event.get("paused")):
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
    directory = package.package_dir(root, slug)
    if name == "stop":
        return _stop_action(memory, package, route, root, slug, directory, event)
    brief = route.decide(root, slug)
    if name in {"start", "prompt"}:
        _, spec, _ = package.read_package(root, slug)
        if name == "prompt" and not _begin_prompt(memory, package, directory, event, brief):
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

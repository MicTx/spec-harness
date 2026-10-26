#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Spec admission policy: should this work get a record at all?

Decides ``direct`` / ``create`` / ``resume`` / ``defer`` from validated,
structured facts about the work's shape. It is the layer before package
creation, so it works with no ``.spec`` tree; stage routing and package
orchestration both need an existing package and stay untouched.

Contract:
- Facts come from the main session after it has read the request and the code
  (JSON on stdin or ``--facts``). Natural language is never parsed here and no
  keyword scoring exists. A missing fact is ``unknown`` and defers.
- Package identity and branch state are read here, read-only. A caller's claim
  that a package matches is verified against the recorded integration branch.
- Shape facts are self-reported by the caller after it reads the request and the code.
  Verdict-shaped facts are NOT inputs: ``opt_out``, ``reversible_small`` and ``read_only``
  are rejected as unknown fields, so no decision can rest on the caller's own claim of
  being exempt. A user's explicit opt-out is honored at the host layer (the human allows
  the denied call or disables activation), never by the agent asserting it here.
  Only package identity and branch/worktree state are read from disk; this is guidance
  for a cooperating agent, not enforcement.
- ``defer`` means evidence or safety is insufficient. It never widens
  authority: this script creates no package, runs no Git mutation, spawns no
  agent, and writes no file.
- Output is one bounded JSON object (decision, reason codes, a facts digest).
  A direct 1/1 assessment also gets constant shape guidance; free-text inputs are never echoed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from activation_state import valid_session, write_assessment  # noqa: E402
from spec_package_support import (  # noqa: E402
    PROTECTED_BRANCHES,
    PROTECTED_PREFIXES,
    active_package_slugs,
    current_git_branch,
    git_worktree_available,
    is_development_record_slug,
    resolve_specs_child,
    resolve_specs_root,
    select_active_package,
)

FACT_VERSION = 1
MAX_INPUT_BYTES = 8192
MAX_ID_LENGTH = 128
UNKNOWN = "unknown"
ROLES = ("main", "worker")
BOUND_MODES = ("branch", "branch-convention", "single-non-git")
DIRECT_SHAPE_CHECK = (
    "Direct assumes one independently testable behavior, not one umbrella feature. "
    "Count each behavior that could pass or fail separately before editing; "
    "multiple files for the same behavior still count as one. "
    "If the request has several such behaviors, re-assess with the actual acceptance_surfaces count."
)

COUNT_FACTS = ("acceptance_surfaces", "implementers")
PLAIN_COUNTS = ("idle_members", "files_touched")
# Verdict-shaped keys ("read_only", "reversible_small", "opt_out") are deliberately
# absent: the live sweep measured models self-reporting them to reach ``direct``.
FLAGS = ("scope_grew", "handoff_needed", "teammates_running")
ALLOWED = {"version", "role", "task_id", "package_slug", *COUNT_FACTS, *PLAIN_COUNTS, *FLAGS}


class FactError(ValueError):
    """Invalid input. Messages name the field, never the offending value."""


def _count(name: str, value: Any, allow_unknown: bool) -> Any:
    if allow_unknown and value == UNKNOWN:
        return UNKNOWN
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10**6:
        raise FactError(f"{name} must be a non-negative integer" + (" or 'unknown'" if allow_unknown else ""))
    return value


def validate_facts(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise FactError("facts must be a JSON object")
    extra = sorted(set(raw) - ALLOWED)
    if extra:
        raise FactError("unknown fact field: " + ", ".join(extra[:5]))
    if raw.get("version") != FACT_VERSION or isinstance(raw.get("version"), bool):
        raise FactError(f"version must be {FACT_VERSION}")
    facts: dict[str, Any] = {"version": FACT_VERSION}
    for name in COUNT_FACTS:
        facts[name] = _count(name, raw[name], True) if name in raw else UNKNOWN
    for name in PLAIN_COUNTS:
        if name in raw:
            facts[name] = _count(name, raw[name], False)
    for name in FLAGS:
        if name in raw:
            if not isinstance(raw[name], bool):
                raise FactError(f"{name} must be a boolean")
            facts[name] = raw[name]
    role = raw.get("role", "main")
    if role not in ROLES:
        raise FactError("role must be 'main' or 'worker'")
    facts["role"] = role
    if "task_id" in raw:
        task_id = raw["task_id"]
        if not isinstance(task_id, str) or not 0 < len(task_id) <= MAX_ID_LENGTH or not task_id.isprintable():
            raise FactError(f"task_id must be 1-{MAX_ID_LENGTH} printable characters")
        facts["task_id"] = task_id
    if "package_slug" in raw:
        slug = raw["package_slug"]
        if not isinstance(slug, str) or "/" in slug or not is_development_record_slug(slug):
            raise FactError("package_slug must be a Development Record slug")
        facts["package_slug"] = slug
    return facts


def _worktree_dirty(root: Path) -> bool:
    """True when business files differ from HEAD; pure `.spec/` bookkeeping is ignored.

    Parsed with ``-z`` so renames contribute BOTH paths: moving a file out of
    `.spec/` into business code is a business change even though the origin is
    bookkeeping. `--no-optional-locks` keeps the check read-only.
    """
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            timeout=5,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return True  # unreadable state is not provably clean
    if result.returncode != 0:
        return True
    fields = [field for field in result.stdout.decode("utf-8", errors="surrogateescape").split("\0") if field]
    index = 0
    while index < len(fields):
        record = fields[index]
        status, paths = record[:2], [record[3:]]
        index += 1
        if ("R" in status or "C" in status) and index < len(fields):
            paths.append(fields[index])
            index += 1
        if any(not path.startswith(".spec/") for path in paths):
            return True
    return False


def _protected(branch: str) -> bool:
    return bool(branch) and (branch in PROTECTED_BRANCHES or branch.startswith(PROTECTED_PREFIXES))


def package_state(root: Path) -> dict[str, Any]:
    """Read-only identity facts: active slugs and which one the branch binds."""
    state: dict[str, Any] = {"active": [], "bound": None, "unreadable": False, "git": False, "branch": ""}
    try:
        specs_root = resolve_specs_root(root)
        active = active_package_slugs(resolve_specs_child(specs_root, "specs"))
        state["git"] = git_worktree_available(root)
        state["branch"] = current_git_branch(root) if state["git"] else ""
        state["active"] = active
        if active:
            selection = select_active_package(
                root, specs_root, active, current_branch=state["branch"], git_available=state["git"]
            )
            if selection.slug and selection.mode in BOUND_MODES:
                state["bound"] = selection.slug
    except (OSError, UnicodeError, ValueError):
        state["unreadable"] = True
    return state


def _result(decision: str, reasons: list[str], facts: dict[str, Any], package: str = "") -> dict[str, Any]:
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    result = {
        "version": FACT_VERSION,
        "decision": decision,
        "reasons": reasons,
        "package": package,
        "facts_digest": digest,
    }
    if (
        decision == "direct"
        and facts["role"] == "main"
        and facts["acceptance_surfaces"] == 1
        and facts["implementers"] == 1
    ):
        result["shape_check"] = DIRECT_SHAPE_CHECK
    return result


def _needs_record(facts: dict[str, Any]) -> tuple[bool | None, list[str]]:
    surfaces, implementers = facts["acceptance_surfaces"], facts["implementers"]
    if facts.get("handoff_needed") or (surfaces != UNKNOWN and surfaces >= 2):
        return True, ["independent_acceptance_surfaces" if surfaces != UNKNOWN and surfaces >= 2 else "handoff_needed"]
    if implementers != UNKNOWN and implementers >= 2:
        return True, ["multiple_implementers"]
    if surfaces == UNKNOWN:
        return None, ["unknown_acceptance_surfaces"]
    if implementers == UNKNOWN:
        return None, ["unknown_implementers"]
    return False, ["single_acceptance_single_implementer"]


def assess(raw_facts: Any, root: Path) -> dict[str, Any]:
    facts = validate_facts(raw_facts)
    root = Path(root)
    if facts["role"] == "worker":
        return _result("direct", ["worker_consumes_assignment"], facts)

    claim = facts.get("package_slug")
    if claim:
        state = package_state(root)
        if state["unreadable"]:
            return _result("defer", ["package_state_unreadable"], facts)
        if state["bound"] == claim:
            return _result("resume", ["matching_active_package"], facts, claim)
        return _result("defer", ["package_identity_mismatch"], facts)

    needed, reasons = _needs_record(facts)
    if needed is None:
        return _result("defer", reasons, facts)
    # "Small" and "reversible" describe how a change is made, not how much of
    # the scope it carries: they never outvoted multiple acceptance surfaces,
    # multiple implementers, or a growing scope, and since the live sweep
    # measured models self-reporting them to reach ``direct`` they are no
    # longer inputs at all (see FLAGS above).
    if not needed:
        return _result("direct", reasons, facts)

    state = package_state(root)
    if state["unreadable"]:
        return _result("defer", ["package_state_unreadable"], facts)
    if state["bound"]:
        return _result("defer", reasons + ["active_package_unclaimed"], facts)
    if state["active"] and not state["bound"]:
        return _result("defer", reasons + ["foreign_active_package"], facts)
    blockers: list[str] = []
    if state["git"]:
        if _protected(state["branch"]):
            blockers.append("protected_branch")
        if _worktree_dirty(root):
            blockers.append("dirty_tree")
            if facts.get("teammates_running"):
                blockers.append("unsafe_state")
    if blockers:
        return _result("defer", reasons + blockers, facts)
    return _result("create", reasons, facts)


def _read_input(inline: str | None) -> Any:
    if inline is not None:
        text = inline
        if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise FactError("input too large")
    else:
        data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise FactError("input too large")
        text = data.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FactError("invalid JSON input") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assess whether work needs a Spec record (read-only).")
    parser.add_argument("--root", default=".", help="project root to read package/branch identity from")
    parser.add_argument("--facts", help="facts as a JSON string; default reads stdin")
    parser.add_argument(
        "--session",
        help="Claude session id: also record the bounded decision so the PreToolUse guard can proceed",
    )
    args = parser.parse_args(argv)
    try:
        if args.session is not None and not valid_session(args.session):
            raise FactError("session must be 1-96 characters of letters, digits, '-', '_' or '.'")
        root = Path(args.root).resolve()
        raw = _read_input(args.facts)
        result = assess(raw, root)
        if args.session is not None:
            facts = validate_facts(raw)
            if facts["role"] == "worker":
                # A worker claim must never persist a `direct` that opens the
                # recording session's gate (a main session asserting it is the
                # same unlock hole as the removed verdict facts).
                raise FactError("worker assessments are advisory; --session recording is main-session only")
            write_assessment(
                root,
                args.session,
                decision=result["decision"],
                task_id=facts.get("task_id", ""),
                scope_digest=result["facts_digest"],
            )
    except (FactError, OSError) as exc:
        print(f"assess_spec_activation: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

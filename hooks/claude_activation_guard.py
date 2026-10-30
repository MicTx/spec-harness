#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Claude Code activation guard: admission is assessed before substantive work.

Wired (when the user opts in) to two events:

- ``UserPromptSubmit``: injects a short reminder that the work's shape must be
  assessed with ``scripts/assess_spec_activation.py`` before the first
  substantive edit or implementation hand-off. Never blocks.
- ``PreToolUse`` on write / dispatch tools: denies the call until an assessment
  for this session and request exists. A matching active package still needs a
  fresh resume assessment after each main-session prompt.

What this is: a reminder gate for a cooperating agent, NOT a security boundary. Shape facts are
reported by the agent itself. Every new main-session prompt voids the previous decision;
`direct` also has a TTL. Resume confirms package identity, not coverage of a new goal.
Verdict-shaped facts (opt-out, reversible-small, read-only) are not accepted by the assessor,
so the agent cannot self-certify an exemption. It stops an agent from forgetting to assess,
not one that under-reports the work's shape.

Boundaries, stated rather than implied:
- Coverage is the supported tool list below. ``Bash``, MCP, and custom tools are
  not parsed and are not claimed.
- Workers (subagents / teammates) are never gated and never asked to create a
  package: only the main coordinator decides, and a worker consumes its
  assignment. A worker is recognised by ``agent_id`` (the host sends it only
  inside a subagent or in-process teammate) or ``teammate_name``. ``agent_type``
  alone is not a worker: the host also sends it on the main thread of a session
  started with ``--agent`` or the settings ``agent`` key. A split-pane teammate runs as its
  own process; its SessionStart/UserPromptSubmit payloads carry neither ``agent_id`` nor
  ``teammate_name`` (probed on Claude Code 2.1.296; PreToolUse not probed), so its prompts
  are handled like a main session's.
- State is a bounded decision record (decision, task id, scope digest) stored
  inside the Git directory, keyed by session. Prompts and tool arguments are
  never persisted or echoed.
- Internal errors, malformed or oversized input fail open with a stderr
  diagnostic, so unrelated work is never locked. This hook never runs a model,
  starts an agent, or runs a Git mutation.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = (Path(__file__).resolve().parent.parent / "scripts").resolve()
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from activation_state import (  # noqa: E402
    DECISIONS,  # noqa: F401 - re-exported for callers of this module
    DIRECT_TTL_SECONDS,
    assessment_age_seconds,
    invalidate_assessment,
    write_assessment,  # noqa: F401 - re-exported for callers of this module
)
from activation_state import read_assessment as _read_assessment  # noqa: E402

MAX_INPUT_BYTES = 262144
GATED_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "Agent", "Task", "SendMessage"})
WORKER_FIELDS = ("agent_id", "teammate_name")
ASSESSOR = "scripts/assess_spec_activation.py"


def _is_worker(payload: dict[str, Any]) -> bool:
    return any(payload.get(field) for field in WORKER_FIELDS)


def _bound_package(root: Path) -> str | None:
    try:
        from assess_spec_activation import package_state

        return package_state(root)["bound"]
    except Exception:  # noqa: BLE001 - identity unreadable means "not provably matched"
        return None


def _emit(event: str, **fields: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}, ensure_ascii=False))


def recorder_hint(session: Any) -> str:
    """Spell out that the PreToolUse gate records nothing on its own.

    The reminder above asks for an assessment, but a refused write looks like a
    bare denial unless the agent knows that ``--session`` is what persists the
    decision. Sweep 1 spent several turns per unit re-deriving that and hunting
    for the id, which the hook already has in hand.
    """
    if not isinstance(session, str) or not session:
        return (
            "Admission state is keyed by session id. Re-run the assessor with `--session <id>` "
            "so the decision is recorded; without it the next gated write is refused again."
        )
    # The hook already knows the id; naming it here is the difference between one
    # assessor call and several turns spent hunting for it (observed in sweep 1).
    return (
        f"Record it with `--session {session}`: that exact value, and it is not optional "
        "bookkeeping: it is the only thing that persists the decision the PreToolUse gate reads. "
        "An assessment run without `--session` leaves the gate closed."
    )


def _prompt_reminder() -> str:
    return (
        "Before the first substantive edit or implementation hand-off, assess this work's shape: "
        f"run `python3 <spec install root>/{ASSESSOR} --root <project>` with the shape facts you established after "
        "reading the request and the code (independent acceptance surfaces, actual implementers, resume match, "
        "scope growth after reading). Count each independently testable behavior separately, even under one "
        "umbrella feature; multiple files for the same behavior still count as one. "
        "Act on the result: direct (just do it), create (record scope first), "
        "resume (continue the matching package), defer (resolve the safety or evidence gap first). Re-assess if "
        "reading code grows the scope or the task changes. Read-only work needs no assessment. "
        "Never self-report `opt_out`, `reversible_small` or `read_only`: verdict facts are rejected, and a "
        "user's explicit opt-out is honored at the host level (the user allows the denied call or disables "
        "activation). Never create packages from a worker session."
    )


def handle_prompt(payload: dict[str, Any]) -> int:
    if _is_worker(payload):
        return 0
    cwd_value = payload.get("cwd")
    root = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else Path.cwd()
    session = payload.get("session_id")
    if isinstance(session, str):
        invalidate_assessment(root, session)  # a new request may be different work
    # One event, one context object: the host merges a single additionalContext,
    # and two JSON lines on stdout would not both be read.
    _emit("UserPromptSubmit", additionalContext=f"{_prompt_reminder()} {recorder_hint(session)}")
    return 0


def handle_tool(payload: dict[str, Any]) -> int:
    tool = payload.get("tool_name")
    if tool not in GATED_TOOLS or _is_worker(payload):
        return 0
    cwd_value = payload.get("cwd")
    root = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else Path.cwd()
    # A bound package alone is NOT an assessment: the resume decision must be
    # recorded before the first gated tool (live sweep: P3 edited first and
    # recorded the resume later, four units in a row).
    session = payload.get("session_id")
    session_key = session if isinstance(session, str) else ""
    record = _read_assessment(root, session_key)
    reason = None
    if record is None:
        reason = "no admission assessment recorded for this session, or a new request voided it (new request)"
        bound = _bound_package(root)
        if bound:
            reason += f"; the active package `{bound}` matches this branch - record a resume assessment before editing"
    elif record["decision"] == "defer":
        reason = "the last assessment deferred: resolve the safety or evidence gap, then re-assess"
    elif record["decision"] == "direct":
        age = assessment_age_seconds(root, session_key)
        if age is not None and age > DIRECT_TTL_SECONDS:
            reason = "the direct assessment expired; assess again if the work is still ongoing"
    if reason is None:
        return 0
    # The denial reason is the only channel that provably reaches the model in a
    # non-interactive session: UserPromptSubmit's additionalContext is not
    # injected in headless runs (probed 2026-10-24). So the exact recording
    # command - including `--session`, without which the assessor records
    # nothing and this gate stays shut - belongs here.
    record_hint = f"--session {session_key}" if session_key else "--session <this session id>"
    _emit(
        "PreToolUse",
        permissionDecision="deny",
        permissionDecisionReason=(
            f"Spec admission: {reason}. Run `python3 <spec install root>/{ASSESSOR} --root <project> "
            f"{record_hint}` with the work's shape facts, then retry. Count each independently testable behavior "
            "separately, not umbrella features; multiple files for the same behavior still count as one. "
            "`--session` is what persists the "
            "decision; without it the next write is refused again. A user's explicit opt-out is honored "
            "only by the user allowing this call or disabling activation - the agent cannot self-certify "
            "it. direct/create/resume proceed; defer asks you to resolve the gap first."
        ),
    )
    return 0


def main() -> int:
    data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        print("spec activation guard: input too large; ignoring event", file=sys.stderr)
        return 0
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except ValueError:
        print("spec activation guard: invalid JSON input; ignoring event", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        print("spec activation guard: invalid event shape; ignoring event", file=sys.stderr)
        return 0
    try:
        if os.environ.get("SPEC_ACTIVATION_GUARD_FORCE_ERROR") == "1":
            raise RuntimeError("forced for test")
        event = payload.get("hook_event_name")
        if event == "UserPromptSubmit":
            return handle_prompt(payload)
        if event == "PreToolUse":
            return handle_tool(payload)
    except Exception as exc:  # noqa: BLE001 - never lock unrelated work on our own failure
        print(f"spec activation guard: internal error ({type(exc).__name__}); allowing the event", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

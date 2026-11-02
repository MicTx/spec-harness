#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Claude Code activation guard: admission is assessed before substantive work.

Wired (when the user opts in) to two events:

- ``UserPromptSubmit``: attempts to invalidate the prior session assessment, then injects
  a reminder for authorized implementation work. Never blocks. Invalidation
  may delete the old machine-local state file, even for a read-only request.
  The event supplies no trusted request identity or read-only signal with
  which to retain per-request revocation without that filesystem mutation.
- ``PreToolUse`` on write / dispatch tools: denies the call until an assessment
  for this session and request exists. A matching active package still needs a
  fresh resume assessment after each main-session prompt.

What this is: a reminder gate for a cooperating agent, NOT a security boundary. Shape facts are
reported by the agent itself. Every new main-session prompt attempts to void the previous decision;
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
import shlex
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
    valid_session,
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
    """Keep the read-only exit ahead of the conditional recording instructions."""
    session_arg = shlex.quote(session) if valid_session(session) else "<this session id>"
    return (
        "If the user or host forbids filesystem writes, including machine-local state, do not record an "
        "assessment or retry a denied call by writing one. Continue with local read-only tools or stop and "
        "report the limitation. A denied Agent/Task/SendMessage call is not proven read-only by its prompt, "
        "tool arguments, or the agent's claim. If the host supports explicit authorization of that specific "
        "call within the permitted scope, seek it there; otherwise stop. Never bypass the write gate. "
        "Only if the user and host permit both the implementation or implementation hand-off and writing "
        "assessment state: "
        f"run `python3 <spec install root>/{ASSESSOR} --root <project> --session {session_arg}` "
        "with established shape facts. The --session flag creates or replaces machine-local state; "
        "without it the gate remains closed. Assessment does not grant host permission."
    )


def _prompt_reminder() -> str:
    return (
        "Read-only work needs no assessment. The UserPromptSubmit hook attempts to invalidate the previous session "
        "assessment before this reminder and may already have deleted its machine-local state file, even "
        "for a read-only request. If the prohibition includes host-hook state, disclose this limitation. "
        "Before the first substantive edit or implementation hand-off, and only when the user and host "
        "permit assessment-state writes, assess the work's shape after reading the request and code. "
        "Count each independently testable behavior separately, even under one umbrella feature; multiple "
        "files for the same behavior still count as one. Act on the result: direct, create, resume, or defer; "
        "re-assess if the scope grows or the task changes. Never self-report `opt_out`, `reversible_small` "
        "or `read_only`; the host level handles an explicit opt-out. Never create packages from a worker session."
    )


def handle_prompt(payload: dict[str, Any]) -> int:
    if _is_worker(payload):
        return 0
    cwd_value = payload.get("cwd")
    root = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else Path.cwd()
    session = payload.get("session_id")
    invalidated = invalidate_assessment(root, session) if isinstance(session, str) else True
    warning = ""
    if not invalidated:
        # Later hook processes have no shared request generation to consult.
        # Do not claim the previous decision was revoked when unlink failed.
        warning = (
            "The previous assessment could not be invalidated and may still authorize later tool calls. "
            "Stop and report this limitation; do not use gated write or dispatch tools until the host "
            "resolves it. Under strict read-only restrictions, do not repair or write the state. "
            "This hook cannot enforce revocation across processes in this failure. "
        )
        print(f"spec activation guard: {warning}", file=sys.stderr)
    # One event, one context object: the host merges a single additionalContext,
    # and two JSON lines on stdout would not both be read.
    _emit("UserPromptSubmit", additionalContext=f"{warning}{_prompt_reminder()} {recorder_hint(session)}")
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
            reason += f"; active package `{bound}` matches this branch but is not an assessment"
    elif record["decision"] == "defer":
        reason = "the last assessment deferred because the safety or evidence gap is unresolved"
    elif record["decision"] == "direct":
        age = assessment_age_seconds(root, session_key)
        if age is not None and age > DIRECT_TTL_SECONDS:
            reason = "the direct assessment expired"
    if reason is None:
        return 0
    # The denial reason reaches the model even when the prompt reminder is not
    # injected. It must include the read-only exit before conditional recording.
    _emit(
        "PreToolUse",
        permissionDecision="deny",
        permissionDecisionReason=(
            f"Spec admission: {reason}. {recorder_hint(session)} "
            "When assessment is authorized, count each independently testable behavior separately, "
            "not umbrella features; multiple files for the same behavior still count as one. "
            "A user opt-out is handled at the host level, never by an agent's self-certification."
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

#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Claude Code TeammateIdle / TaskCompleted hook for teammate handoff quality.

The hook can return exit 2 and stderr to request further work. It locates the
teammate's in_flight task in active runs and asks for a non-terminal report and
handoff to the main session. Once reported, the worker may exit, but its report
does not release capacity. The main session must confirm termination with the host
before recording a terminal result. No active run or matching task allows exit.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from loop_control import find_active_runs  # noqa: E402
from loop_state import LoopStateError, LoopStore, find_run_root  # noqa: E402


def fail_closed(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _agent_matches(teammate: str, agent: str) -> bool:
    """Match teammate and agentId by equality or a prefix followed by a separator.

    Unanchored substring matching could assign review-worker's task to worker.
    """
    if not teammate or not agent:
        return False
    if teammate == agent:
        return True
    if agent.startswith(teammate):
        return len(agent) > len(teammate) and agent[len(teammate)] in "-_/.:@"
    if teammate.startswith(agent):
        return len(teammate) > len(agent) and teammate[len(agent)] in "-_/.:@"
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    event = payload.get("hook_event_name")
    if event not in ("TeammateIdle", "TaskCompleted", "SubagentStop"):
        # Without a recognized event, this guard cannot confirm it was invoked.
        return 0

    teammate = payload.get("teammate_name") or payload.get("agent_id") or ""
    cwd_value = payload.get("cwd")
    cwd = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else Path.cwd()

    root = find_run_root(cwd)
    if root is None:
        return 0
    try:
        active, corrupt = find_active_runs(root)
    except (OSError, LoopStateError) as exc:
        return fail_closed(f"spec team-loop gate: cannot safely read run state: {exc}")
    if corrupt:
        # As with the Stop guard, fail closed and provide a repair entrypoint.
        names = "; ".join(p.name for p in corrupt[:5])
        return fail_closed(
            f"spec team-loop gate: corrupt run state detected ({names}); in_flight tasks cannot be verified. "
            f"Repair it with python3 {SKILL_DIR}/scripts/loop_state.py status --run-dir <run-dir>"
        )
    if not active:
        return 0

    matches = []  # (run_dir, round, task_id, task)
    load_failed: list[str] = []
    for run_dir in active:
        try:
            store = LoopStore(run_dir).load()
        except (OSError, LoopStateError) as exc:
            load_failed.append(f"{run_dir.name}: {exc}")
            continue
        for rnd in store.state["rounds"]:
            for tid, task in rnd.get("tasks", {}).items():
                if task["status"] != "in_flight":
                    continue
                agent = str(task.get("agentId") or "")
                if _agent_matches(teammate, agent):
                    matches.append((run_dir, rnd["index"], tid, task))

    if load_failed:
        return fail_closed(
            "spec team-loop gate: cannot read these active runs; in_flight tasks are unverified: "
            + "; ".join(load_failed)
        )

    if not matches:
        return 0

    if len(matches) > 1:
        # Report every match instead of assigning another teammate's task by mistake.
        listed = "; ".join(f"r{r}/{tid}@{rd.name}" for rd, r, tid, _ in matches)
        return fail_closed(
            f"spec team-loop gate: teammate {teammate} matches multiple in_flight tasks ({listed}). "
            "Resolve the identity ambiguity, for example by using distinct agentId names, before proceeding."
        )

    run_dir, round_index, tid, task = matches[0]
    if task.get("workerReport"):
        # Let the worker end; the disk task remains in_flight until the coordinator
        # confirms actual host termination. Idle is not proof of process exit.
        return 0
    fence = ""
    if task.get("reservationToken"):
        fence = " --reservation " + shlex.quote(task["reservationToken"])
    elif task.get("attempts", 0) > 1:
        fence = " --submission " + shlex.quote(task.get("submissionId") or "")
    return fail_closed(
        f"spec team-loop gate: task r{round_index}/{tid} in run {run_dir.name} is still in_flight. "
        "Write the actual work and verification to a JSON object file worker-report.json "
        "(if it exists, choose another filename and adjust the command). "
        "Submit only a non-terminal handoff and notify the main session; "
        "do not record the task result or cancel it yourself:\n"
        f"python3 {shlex.quote(str(SKILL_DIR / 'scripts' / 'loop_state.py'))} task report "
        f"--run-dir {shlex.quote(str(run_dir))} "
        f"--round {round_index} --id {shlex.quote(tid)} --result @worker-report.json{fence}\n"
        "The handoff does not release capacity; the main session must verify with the host "
        "that the worker has stopped before recording a terminal result."
    )


if __name__ == "__main__":
    sys.exit(main())

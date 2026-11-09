#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Claude Code Stop hook for team-loop convergence.

The hook reads Stop event JSON from stdin and blocks with a JSON reason on stdout.
An empty response with exit 0 allows stopping. When stop_hook_active is true, it
allows stopping to avoid recursive blocking; the host may impose other limits, so
this hook cannot guarantee that an unconverged run will continue.

It discovers active runs from the project root. No active run allows stopping;
multiple or corrupt runs fail closed. An unconverged run receives next actions,
a stopped run receives finish instructions, and a persisted converged run allows stopping.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from loop_control import find_active_runs, stale_tasks, termination_check  # noqa: E402
from loop_state import LoopStateError, LoopStore, find_run_root, utc_now  # noqa: E402


def emit_block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason, "systemMessage": reason[:400]}, ensure_ascii=False))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    event = payload.get("hook_event_name")
    if isinstance(event, str) and event and event != "Stop":
        return 0
    if payload.get("stop_hook_active") is True:
        return 0

    cwd_value = payload.get("cwd")
    cwd = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else Path.cwd()

    root = find_run_root(cwd)
    if root is None:
        return 0
    try:
        active, corrupt = find_active_runs(root)
    except (OSError, LoopStateError) as exc:
        emit_block(
            f"spec team-loop: cannot safely read run state ({exc}). Repair it or confirm retirement before ending."
        )
        return 0

    if corrupt:
        # Corrupt runs fail closed with a repair entrypoint.
        names = "; ".join(p.name for p in corrupt[:5])
        emit_block(
            f"spec team-loop: corrupt run state detected ({names}). "
            f"Investigate and repair each run before ending: python3 {SKILL_DIR}/scripts/loop_state.py "
            "status --run-dir <run-dir>. Alternatively, confirm the run is retired and remove its directory "
            "under .agents/runtime/loop/."
        )
        return 0

    if not active:
        return 0
    if len(active) > 1:
        names = "; ".join(p.name for p in active[:5])
        emit_block(
            f"spec team-loop: multiple active runs in this project ({names}). "
            "Converge or explicitly finish/cancel each run before ending; resolve the ambiguity first."
        )
        return 0

    run_dir = active[0]
    run_arg = shlex.quote(str(run_dir))
    state_script = shlex.quote(str(SKILL_DIR / "scripts" / "loop_state.py"))
    control_script = shlex.quote(str(SKILL_DIR / "scripts" / "loop_control.py"))
    try:
        store = LoopStore(run_dir).load()
    except (OSError, LoopStateError) as exc:
        emit_block(
            f"spec team-loop: corrupt active run state ({run_dir}: {exc}). "
            f"Repair it or investigate with python3 {state_script} resume --run-dir {run_arg} before ending."
        )
        return 0

    try:
        verdict = termination_check(store, utc_now())
    except (OSError, LoopStateError) as exc:
        # An invalid STOP sentinel must fail closed rather than crash and allow stopping.
        emit_block(
            f"spec team-loop: termination cannot be evaluated safely ({run_dir}: {exc}). "
            "Verify the STOP path, file type, and ownership before handling it manually; "
            "this guard does not provide a deletion command."
        )
        return 0
    if verdict["stop"]:
        reasons = "; ".join(verdict["reasons"])
        if verdict["verdict"] == "converged":
            emit_block(
                f"spec team-loop: run meets convergence conditions ({reasons}). "
                f"Run python3 {state_script} finish converged --run-dir {run_arg} "
                '--summary "<outcome summary>" to record the terminal state before ending.'
            )
        else:
            emit_block(
                f"spec team-loop: run meets a managed stop condition ({reasons}). "
                f"Verify and stop host workers, then run python3 {state_script} "
                f"finish failed --run-dir {run_arg} --confirmed-stopped "
                "(or use cancelled) and record the reason; do not stop silently."
            )
        return 0

    open_rounds = [r["index"] for r in store.state["rounds"] if r["status"] == "open"]
    pending = [
        f"r{r['index']}/{tid}"
        for r in store.state["rounds"]
        for tid, task in r.get("tasks", {}).items()
        if task["status"] == "pending"
    ]
    in_flight = [
        f"r{r['index']}/{tid}"
        for r in store.state["rounds"]
        for tid, task in r.get("tasks", {}).items()
        if task["status"] == "in_flight"
    ]
    reserved = [
        f"r{r['index']}/{tid}"
        for r in store.state["rounds"]
        for tid, task in r.get("tasks", {}).items()
        if task["status"] == "in_flight" and task.get("dispatchPhase") == "reserved"
    ]
    in_flight = [key for key in in_flight if key not in reserved]
    parts = []
    if reserved:
        parts.append(
            f"check unattached reservations {', '.join(reserved)}; "
            "attach the worker or confirm it has stopped before abandon"
        )
    if not open_rounds:
        parts.append('round start: python3 …/loop_state.py round start --goal "<round goal>"')
    if in_flight:
        parts.append(
            f"wait for in-flight tasks {', '.join(in_flight)}; "
            "the main session records task result after host confirmation"
        )
        try:
            stale = stale_tasks(store, utc_now())
        except (OSError, LoopStateError):
            stale = []
        if stale:
            parts.append(
                f"stale heartbeats for in-flight tasks {', '.join(stale)} are only a suspicion; "
                "confirm each worker has stopped with the host before running "
                f"python3 {control_script} stale --reap --task rN/id --confirmed-stopped --run-dir {run_arg}"
            )
    if pending:
        parts.append(f"dispatch pending tasks {', '.join(pending)} (reserve each task first)")
    if open_rounds and not (pending or in_flight or reserved):
        parts.append(
            "all tasks are terminal: close the round based on actual review; "
            "if any failed, use round end --decision fail"
        )
    if not parts:
        parts.append("advance to the next round or finish converged according to the termination decision")
    emit_block(
        f"spec team-loop: run {store.state['runId']} has not converged "
        f"(goal: {store.state['goal'][:80]}). Next: {'; '.join(parts)}. "
        f"Status: python3 {state_script} status --run-dir {run_arg}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

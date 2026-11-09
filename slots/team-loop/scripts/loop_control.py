#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""agents-team-loop controls for retry backoff, concurrency, staleness, termination, and interruption.

Design references are in references/prior-art.md:
- Exponential backoff with jitter follows AWS's established pattern.
- Admission is only a concurrency snapshot; loop_state reserves capacity on disk.
- A stale heartbeat only suggests loss of contact; confirm host termination before reaping.
- Termination conditions combine maxRounds, runTimeout, budgetTasks, external STOP, and allDone.
- External termination uses a STOP sentinel in the run directory.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Optional

# Import from the same directory when running this script directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loop_state import (  # noqa: E402
    STOP_FILENAME,
    LoopStateError,
    LoopStore,
    backoff_delay,
    find_run_root,
    list_orphan_run_dirs,
    list_run_dirs,
    utc_now,
    write_stop_sentinel,
)

# ---------------------------------------------------------------------------
# Retry backoff
# ---------------------------------------------------------------------------


def should_retry(task: dict[str, Any], retry_cfg: dict[str, Any]) -> bool:
    return task["status"] == "pending" and task["attempts"] < int(retry_cfg["maxAttempts"])


def retry_ready(task: dict[str, Any], now: float) -> bool:
    until = task.get("backoffUntil")
    return until is None or now >= float(until)


# ---------------------------------------------------------------------------
# Concurrency limit
# ---------------------------------------------------------------------------


def admission(store: LoopStore, now: float) -> dict[str, Any]:
    """Return an advisory quota snapshot without reserving or checking task backoff.

    A task reservation must succeed before dispatch to the host.
    """
    cfg = store.state["config"]
    cap = int(cfg["concurrency"])
    in_flight = 0
    stale: list[str] = []
    for rnd in store.state["rounds"]:
        for tid, task in rnd.get("tasks", {}).items():
            if task["status"] == "in_flight":
                in_flight += 1
                if _is_stale(task, cfg, now):
                    stale.append(f"r{rnd['index']}/{tid}")
    if store.state["status"] != "active":
        return {"admit": False, "reason": "run is not active", "inFlight": in_flight, "cap": cap, "stale": stale}
    if int(store.state["counters"]["tasksSpawned"]) >= int(cfg["budgetTasks"]):
        return {"admit": False, "reason": "budgetTasks exhausted", "inFlight": in_flight, "cap": cap, "stale": stale}
    if now - float(store.state["createdAt"]) > float(cfg["timeoutSec"]) or external_stop_requested(store.run_dir):
        return {"admit": False, "reason": "runTimeout or STOP", "inFlight": in_flight, "cap": cap, "stale": stale}
    if in_flight >= cap:
        return {
            "admit": False,
            "reason": f"concurrency limit reached inFlight={in_flight} cap={cap}: wait before spawning",
            "inFlight": in_flight,
            "cap": cap,
            "stale": stale,
        }
    return {
        "admit": True,
        "reason": "advisory only; reserve the specific task first",
        "inFlight": in_flight,
        "cap": cap,
        "stale": stale,
    }


def _is_stale(task: dict[str, Any], cfg: dict[str, Any], now: float) -> bool:
    hb = task.get("lastHeartbeat")
    if hb is None:
        return False
    return now - float(hb) > float(cfg["stalenessSec"])


def stale_tasks(store: LoopStore, now: float) -> list[str]:
    cfg = store.state["config"]
    out = []
    for rnd in store.state["rounds"]:
        for tid, task in rnd.get("tasks", {}).items():
            if task["status"] == "in_flight" and task.get("dispatchPhase") != "reserved" and _is_stale(task, cfg, now):
                out.append(f"r{rnd['index']}/{tid}")
    return out


def reap_stale_tasks(
    store: LoopStore,
    now: float,
    reason: str = "",
    *,
    task_key: Optional[str] = None,
    confirmed_stopped: bool = False,
) -> dict[str, Any]:
    """Reap only one explicitly confirmed host-stopped worker, never on age alone."""
    if not task_key or not confirmed_stopped:
        raise LoopStateError("reaping requires --task rN/id and caller confirmation with --confirmed-stopped")
    for rnd in store.state["rounds"]:
        for tid, task in rnd.get("tasks", {}).items():
            if f"r{rnd['index']}/{tid}" != task_key:
                continue
            if (
                task["status"] != "in_flight"
                or task.get("dispatchPhase") == "reserved"
                or not _is_stale(task, store.state["config"], now)
            ):
                raise LoopStateError("the specified task is not a stale attached worker eligible for reaping")
            detail = reason or "host worker confirmed stopped; heartbeat timed out"
            outcome = store.task_result(
                rnd["index"],
                tid,
                "fail",
                detail,
                submission=task.get("submissionId"),
                reservation=task.get("reservationToken"),
                confirmed_stopped=True,
            )
            return {"reaped": [{"task": task_key, "outcome": outcome}]}
    raise LoopStateError(f"task does not exist: {task_key}")


# ---------------------------------------------------------------------------
# Composable termination conditions (AutoGen-style AND composition)
# ---------------------------------------------------------------------------


def external_stop_requested(run_dir: Path) -> bool:
    target = run_dir / STOP_FILENAME
    if not target.exists() and not target.is_symlink():
        return False
    if target.is_symlink() or not target.is_file():
        raise LoopStateError(f"STOP sentinel must be a regular file, not a symlink: {target}")
    return True


def termination_check(store: LoopStore, now: float) -> dict[str, Any]:
    """Evaluate hard stops and the latest closed round, without aborting last-budget work."""
    st = store.state
    cfg = st["config"]
    rounds = st["rounds"]
    latest = rounds[-1] if rounds else None
    open_rounds = [r for r in rounds if r["status"] == "open"]
    all_tasks = [(r, tid, task) for r in rounds for tid, task in r.get("tasks", {}).items()]
    live = [(r, tid, task) for r, tid, task in all_tasks if task["status"] == "in_flight"]
    pending = [(r, tid, task) for r, tid, task in all_tasks if task["status"] == "pending"]
    failed = [f"r{r['index']}/{tid}" for r, tid, task in all_tasks if task["status"] == "failed" and r is latest]
    # Exhaustion is a task/round failure, not a run-wide hard stop. A decision
    # labelled pass cannot erase a failed task in that same round either.
    latest_passed = bool(latest and latest.get("decision") == "pass" and latest.get("tasks") and not failed)
    reasons: list[str] = []

    if len(rounds) > int(cfg["maxRounds"]):
        reasons.append(f"maxRounds={cfg['maxRounds']} exceeded (current rounds: {len(rounds)})")
    elif len(rounds) == int(cfg["maxRounds"]) and not open_rounds and latest and not latest_passed:
        reasons.append(f"maxRounds={cfg['maxRounds']} exhausted and the last round did not pass")
    if now - float(st["createdAt"]) > float(cfg["timeoutSec"]):
        reasons.append(f"runTimeout={cfg['timeoutSec']}s exceeded")
    spawned = int(st["counters"]["tasksSpawned"])
    budget = int(cfg["budgetTasks"])
    if spawned > budget:
        reasons.append(f"budgetTasks={budget} exceeded (spawned {spawned})")
    elif spawned >= budget and not live and (pending or (latest and not open_rounds and not latest_passed)):
        reasons.append(f"budgetTasks={budget} exhausted with tasks or a new round still needed")
    if external_stop_requested(store.run_dir):
        reasons.append("external stop: STOP sentinel exists")
    if reasons:
        if failed:
            reasons.append("failed tasks exhausted retries: " + ", ".join(failed))
        return {"stop": True, "verdict": "stopped", "reasons": reasons}
    if latest and latest["status"] == "closed" and latest_passed and not open_rounds and not live and not pending:
        return {"stop": True, "verdict": "converged", "reasons": ["last round closed with pass and all tasks terminal"]}
    return {"stop": False, "verdict": "continue", "reasons": []}


# ---------------------------------------------------------------------------
# Cooperative interruption
# ---------------------------------------------------------------------------


def interrupt_run(store: LoopStore, reason: str) -> dict[str, Any]:
    """Interrupt cooperatively and return in-flight or unattached reservations; verify host termination separately."""
    affected = store.run_interrupted(reason)
    return {
        "status": "interrupted",
        "inFlight": affected,
        "hint": "verify and stop attached workers with the host; investigate unknown reservations first. "
        "resume does not automatically release in-flight tasks",
    }


# ---------------------------------------------------------------------------
# Run discovery for Stop hooks and status
# ---------------------------------------------------------------------------


def find_active_runs(root: Path) -> tuple[list[Path], list[Path]]:
    """Return (active run directories, corrupt run directories).

    Corrupt runs are reported to callers for fail-closed repair guidance. A directory
    with activity traces such as events.jsonl or .lock but no run.json is also corrupt.
    """
    active: list[Path] = []
    corrupt: list[Path] = []
    for run_dir in list_run_dirs(root):
        try:
            store = LoopStore(run_dir).load()
        except LoopStateError:
            corrupt.append(run_dir)
            continue
        if store.state["status"] == "active":
            active.append(run_dir)
    # Missing run.json with activity traces is corrupt, not an absent run.
    corrupt.extend(list_orphan_run_dirs(root))
    return (
        sorted(active, key=lambda p: p.stat().st_mtime, reverse=True),
        sorted(corrupt, key=lambda p: p.stat().st_mtime, reverse=True),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _emit_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loop_control.py", description="agents-team-loop control CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("admit", help="check concurrency admission")
    p.add_argument("--run-dir", required=True)

    p = sub.add_parser("stale", help="detect stale workers (--reap records failure for retry)")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--reap", action="store_true", help="reap one stale worker confirmed stopped by the host")
    p.add_argument("--task", help="target task rN/id")
    p.add_argument("--confirmed-stopped", action="store_true", help="confirm that the host worker has stopped")
    p.add_argument("--reason", default="", help="reason written to lastError when reaping")

    p = sub.add_parser("terminate", help="evaluate termination conditions")
    p.add_argument("--run-dir", required=True)

    p = sub.add_parser("interrupt", help="interrupt cooperatively")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--reason", default="manual interrupt")

    p = sub.add_parser("backoff", help="calculate backoff seconds after attempt N")
    p.add_argument("--attempt", type=int, required=True)
    p.add_argument("--run-dir", default=None, help="optionally use this run's retry config; defaults otherwise")
    p.add_argument("--seed", type=int, default=None, help="optional random seed for reproducibility")

    p = sub.add_parser("stop", help="request an external stop by writing the STOP sentinel")
    p.add_argument("--run-dir", required=True)

    p = sub.add_parser("active", help="list active runs under root")
    p.add_argument("--root", default=".")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "backoff":
            rng = random.Random(args.seed)
            from loop_state import CONFIG

            retry_cfg = CONFIG["retry"]
            config_source = "default"
            if args.run_dir:
                store = LoopStore(Path(args.run_dir))
                if not store.state_path.is_file():
                    raise LoopStateError(f"run does not exist: {args.run_dir}")
                store.load()
                retry_cfg = store.state["config"]["retry"]
                config_source = str(store.run_dir)
            _emit_json(
                {
                    "attempt": args.attempt,
                    "delaySec": backoff_delay(args.attempt, retry_cfg, rng),
                    "retryConfigSource": config_source,
                }
            )
        elif args.command == "active":
            root = find_run_root(Path(args.root)) or Path(args.root).resolve()
            active, corrupt = find_active_runs(root)
            _emit_json({"active": [str(p) for p in active], "corrupt": [str(p) for p in corrupt]})
        else:
            store = LoopStore(Path(args.run_dir))
            if not store.state_path.is_file():
                raise LoopStateError(f"run does not exist: {args.run_dir}")
            store.load()
            now = utc_now()
            if args.command == "admit":
                _emit_json(admission(store, now))
            elif args.command == "stale":
                if args.reap:
                    _emit_json(
                        reap_stale_tasks(
                            store, now, reason=args.reason, task_key=args.task, confirmed_stopped=args.confirmed_stopped
                        )
                    )
                else:
                    _emit_json({"stale": stale_tasks(store, now)})
            elif args.command == "terminate":
                _emit_json(termination_check(store, now))
            elif args.command == "interrupt":
                _emit_json(interrupt_run(store, args.reason))
            elif args.command == "stop":
                write_stop_sentinel(
                    store.run_dir,
                    {"ts": now, "source": "loop_control stop"},
                )
                _emit_json({"ok": True, "sentinel": str(store.run_dir / STOP_FILENAME)})
        return 0
    except LoopStateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

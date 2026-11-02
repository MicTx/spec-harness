#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""agents-team-loop 控制原语：重试退避、并发上限、心跳 staleness、可组合终止、中断。

缺口3 的答案。设计对齐（见 references/prior-art.md）：
- 指数退避 + 抖动：业界标准（AWS Architecture Blog "Exponential Backoff and Jitter"）。
- 并发上限：admit 仅为快照，实际磁盘预约由 loop_state 的 CAS 完成。
- 心跳 staleness：只识别疑似失联；须先确认宿主已停止才能回收。
- 可组合终止：直接采纳 AutoGen TerminationCondition 的组合思想
  （AND 组合：maxRounds / runTimeout / budgetTasks / external STOP / allDone）。
- ExternalTermination：run 目录下的 STOP 哨兵文件。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Optional

# 同目录导入（脚本直接运行场景）
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
# 重试退避
# ---------------------------------------------------------------------------


def should_retry(task: dict[str, Any], retry_cfg: dict[str, Any]) -> bool:
    return task["status"] == "pending" and task["attempts"] < int(retry_cfg["maxAttempts"])


def retry_ready(task: dict[str, Any], now: float) -> bool:
    until = task.get("backoffUntil")
    return until is None or now >= float(until)


# ---------------------------------------------------------------------------
# 并发上限
# ---------------------------------------------------------------------------


def admission(store: LoopStore, now: float) -> dict[str, Any]:
    """返回一般性配额快照；不预约容量，也不检查指定任务的 backoff。

    真正宿主 dispatch 前必须成功 task reserve。
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
        return {"admit": False, "reason": "run 非 active", "inFlight": in_flight, "cap": cap, "stale": stale}
    if int(store.state["counters"]["tasksSpawned"]) >= int(cfg["budgetTasks"]):
        return {"admit": False, "reason": "budgetTasks 已用尽", "inFlight": in_flight, "cap": cap, "stale": stale}
    if now - float(store.state["createdAt"]) > float(cfg["timeoutSec"]) or external_stop_requested(store.run_dir):
        return {"admit": False, "reason": "runTimeout 或 STOP", "inFlight": in_flight, "cap": cap, "stale": stale}
    if in_flight >= cap:
        return {
            "admit": False,
            "reason": f"并发已满 inFlight={in_flight} cap={cap}：先 wait 再 spawn",
            "inFlight": in_flight,
            "cap": cap,
            "stale": stale,
        }
    return {"admit": True, "reason": "仅供参考；须对指定任务先 reserve", "inFlight": in_flight, "cap": cap, "stale": stale}


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
    store: LoopStore, now: float, reason: str = "", *, task_key: Optional[str] = None,
    confirmed_stopped: bool = False,
) -> dict[str, Any]:
    """Reap only one explicitly confirmed host-stopped worker, never on age alone."""
    if not task_key or not confirmed_stopped:
        raise LoopStateError("回收须指定 --task rN/id，并由调用者确认宿主已停止 --confirmed-stopped")
    for rnd in store.state["rounds"]:
        for tid, task in rnd.get("tasks", {}).items():
            if f"r{rnd['index']}/{tid}" != task_key:
                continue
            if (task["status"] != "in_flight" or task.get("dispatchPhase") == "reserved"
                    or not _is_stale(task, store.state["config"], now)):
                raise LoopStateError("指定任务不是可回收的 stale 已登记 worker")
            detail = reason or "调用者确认宿主 worker 已停止；心跳超时"
            outcome = store.task_result(rnd["index"], tid, "fail", detail,
                                        submission=task.get("submissionId"),
                                        reservation=task.get("reservationToken"), confirmed_stopped=True)
            return {"reaped": [{"task": task_key, "outcome": outcome}]}
    raise LoopStateError(f"任务不存在: {task_key}")


# ---------------------------------------------------------------------------
# 可组合终止条件（AutoGen 风格 AND 组合）
# ---------------------------------------------------------------------------


def external_stop_requested(run_dir: Path) -> bool:
    target = run_dir / STOP_FILENAME
    if not target.exists() and not target.is_symlink():
        return False
    # LoopState's no-follow boundary treats a symlink as corrupt state rather
    # than silently following an attacker-controlled stop target.
    from loop_state import _assert_regular_nosymlink

    _assert_regular_nosymlink(target)
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
    failed = [f"r{r['index']}/{tid}" for r, tid, task in all_tasks
              if task["status"] == "failed" and r is latest]
    # Exhaustion is a task/round failure, not a run-wide hard stop. A decision
    # labelled pass cannot erase a failed task in that same round either.
    latest_passed = bool(latest and latest.get("decision") == "pass" and latest.get("tasks") and not failed)
    reasons: list[str] = []

    if len(rounds) > int(cfg["maxRounds"]):
        reasons.append(f"maxRounds={cfg['maxRounds']} 超限（当前 {len(rounds)} 轮）")
    elif (len(rounds) == int(cfg["maxRounds"]) and not open_rounds
          and latest and not latest_passed):
        reasons.append(f"maxRounds={cfg['maxRounds']} 已用尽且最后一轮未通过")
    if now - float(st["createdAt"]) > float(cfg["timeoutSec"]):
        reasons.append(f"runTimeout={cfg['timeoutSec']}s 超时")
    spawned = int(st["counters"]["tasksSpawned"])
    budget = int(cfg["budgetTasks"])
    if spawned > budget:
        reasons.append(f"budgetTasks={budget} 超支（已 spawn {spawned}）")
    elif spawned >= budget and not live and (pending or (latest and not open_rounds and not latest_passed)):
        reasons.append(f"budgetTasks={budget} 预算已用尽且仍需派发任务或新一轮")
    if external_stop_requested(store.run_dir):
        reasons.append("外部停止：STOP 哨兵文件存在")
    if reasons:
        if failed:
            reasons.append("存在重试耗尽的 failed 任务: " + ", ".join(failed))
        return {"stop": True, "verdict": "stopped", "reasons": reasons}
    if (latest and latest["status"] == "closed" and latest_passed
            and not open_rounds and not live and not pending):
        return {"stop": True, "verdict": "converged", "reasons": ["最后一轮 pass 关闭且全部任务终态"]}
    return {"stop": False, "verdict": "continue", "reasons": []}


# ---------------------------------------------------------------------------
# 中断（协作式）
# ---------------------------------------------------------------------------


def interrupt_run(store: LoopStore, reason: str) -> dict[str, Any]:
    """协作中断并返回在途或未登记预约；是否停止须向宿主核实。"""
    affected = store.run_interrupted(reason)
    return {
        "status": "interrupted",
        "inFlight": affected,
        "hint": "向宿主核对并停止已登记 worker；未知预约先核对。resume 不自动释放在途任务",
    }


# ---------------------------------------------------------------------------
# run 发现（供 Stop hook / status 使用）
# ---------------------------------------------------------------------------


def find_active_runs(root: Path) -> tuple[list[Path], list[Path]]:
    """返回 (活跃 run 目录, 损坏 run 目录)。

    损坏的 run 不静默跳过：交由调用方 fail-closed 地给出修复指引，
    避免状态损坏时任务被静默丢弃（对齐 spec 守卫的 fail-closed 哲学）。
    有 events.jsonl/.lock 等痕迹却缺 run.json 的目录同样 fail-closed，
    不会被守卫当「无 run」放行。
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
    # 缺 run.json 但有活动痕迹的目录：不是无 run，fail-closed 归入损坏。
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
    parser = argparse.ArgumentParser(prog="loop_control.py", description="agents-team-loop 控制原语 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("admit", help="并发准入判定")
    p.add_argument("--run-dir", required=True)

    p = sub.add_parser("stale", help="staleness 检测（--reap 时按失败回收进重试路径）")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--reap", action="store_true", help="回收一个已由宿主确认停止的 stale worker")
    p.add_argument("--task", help="目标任务 rN/id")
    p.add_argument("--confirmed-stopped", action="store_true", help="调用者确认该宿主 worker 已停止")
    p.add_argument("--reason", default="", help="reap 时写入 lastError 的原因")

    p = sub.add_parser("terminate", help="终止条件族评估")
    p.add_argument("--run-dir", required=True)

    p = sub.add_parser("interrupt", help="协作中断")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--reason", default="manual interrupt")

    p = sub.add_parser("backoff", help="计算第 N 次失败后的退避秒数")
    p.add_argument("--attempt", type=int, required=True)
    p.add_argument("--run-dir", default=None, help="可选：用该 run 的 retry 配置计算（缺省用默认配置）")
    p.add_argument("--seed", type=int, default=None, help="可选随机种子（可复现）")

    p = sub.add_parser("stop", help="外部停止：写 STOP 哨兵")
    p.add_argument("--run-dir", required=True)

    p = sub.add_parser("active", help="列出 root 下活跃 run")
    p.add_argument("--root", default=".")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "backoff":
            rng = random.Random(args.seed)
            from loop_state import default_config

            retry_cfg = default_config()["retry"]
            config_source = "default"
            if args.run_dir:
                store = LoopStore(Path(args.run_dir))
                if not store.exists():
                    raise LoopStateError(f"run 不存在: {args.run_dir}")
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
            if not store.exists():
                raise LoopStateError(f"run 不存在: {args.run_dir}")
            store.load()
            now = utc_now()
            if args.command == "admit":
                _emit_json(admission(store, now))
            elif args.command == "stale":
                if args.reap:
                    _emit_json(reap_stale_tasks(store, now, reason=args.reason, task_key=args.task,
                                                  confirmed_stopped=args.confirmed_stopped))
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

#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""agents-team-loop 控制原语：重试退避、并发上限、心跳 staleness、可组合终止、中断。

缺口3 的答案。设计对齐（见 references/prior-art.md）：
- 指数退避 + 抖动：业界标准（AWS Architecture Blog "Exponential Backoff and Jitter"）。
- 并发上限：信号量语义在磁盘状态上的投影（admit/reject + 建议 wait）。
- 心跳 staleness：wait 轮询间隔化的看门狗；stale 任务按失败进入重试路径。
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
    find_run_root,
    list_run_dirs,
    utc_now,
    write_stop_sentinel,
)

# ---------------------------------------------------------------------------
# 重试退避
# ---------------------------------------------------------------------------


def backoff_delay(attempt: int, retry_cfg: dict[str, Any], rng: random.Random) -> float:
    """第 attempt 次（1 基）失败后的退避秒数：指数封顶 + 全抖动。

    jitter=0 时确定性返回 min(maxSec, baseSec * 2**(attempt-1))；
    否则 full jitter：uniform(raw*(1-jitter), raw)，raw=uniform(0, ceiling)。
    """
    base = float(retry_cfg.get("baseSec", 5.0))
    cap = float(retry_cfg.get("maxSec", 60.0))
    jitter = float(retry_cfg.get("jitter", 0.3))
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    if jitter <= 0.0:
        return round(ceiling, 3)
    raw = rng.uniform(0.0, ceiling)
    low = raw * (1.0 - jitter)
    return round(rng.uniform(low, raw), 3) if raw > 0 else 0.0


def should_retry(task: dict[str, Any], retry_cfg: dict[str, Any]) -> bool:
    return task["status"] == "pending" and task["attempts"] < int(retry_cfg["maxAttempts"])


def retry_ready(task: dict[str, Any], now: float) -> bool:
    until = task.get("backoffUntil")
    return until is None or now >= float(until)


# ---------------------------------------------------------------------------
# 并发上限
# ---------------------------------------------------------------------------


def admission(store: LoopStore, now: float) -> dict[str, Any]:
    """判定当前还能否再 spawn 一个任务。

    返回 {admit: bool, reason, inFlight, cap}。agent 协议：admit=false 时必须先 wait。
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
    if in_flight >= cap:
        return {
            "admit": False,
            "reason": f"并发已满 inFlight={in_flight} cap={cap}：先 wait 再 spawn",
            "inFlight": in_flight,
            "cap": cap,
            "stale": stale,
        }
    return {"admit": True, "reason": "", "inFlight": in_flight, "cap": cap, "stale": stale}


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
            if task["status"] == "in_flight" and _is_stale(task, cfg, now):
                out.append(f"r{rnd['index']}/{tid}")
    return out


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
    """评估终止条件族。

    返回 {stop: bool, reasons: [...], verdict: "converged"|"stopped"|"continue"}。
    - allDone 且外部未请求停止 -> converged（自然收敛）
    - maxRounds / runTimeout / budgetTasks / external STOP -> stopped（管理停止）
    """
    st = store.state
    cfg = st["config"]
    reasons: list[str] = []

    open_rounds = [r for r in st["rounds"] if r["status"] == "open"]
    all_tasks_terminal = True
    any_task = False
    failed_tasks: list[str] = []
    for rnd in st["rounds"]:
        for tid, task in rnd.get("tasks", {}).items():
            any_task = True
            if task["status"] not in ("done", "failed", "cancelled"):
                all_tasks_terminal = False
            if task["status"] == "failed":
                failed_tasks.append(f"r{rnd['index']}/{tid}")

    rounds_used = len(st["rounds"])
    if rounds_used > int(cfg["maxRounds"]):
        reasons.append(f"maxRounds={cfg['maxRounds']} 超限（当前 {rounds_used} 轮）")
    elif rounds_used == int(cfg["maxRounds"]) and open_rounds:
        reasons.append(f"maxRounds={cfg['maxRounds']} 已用尽且仍有 open round（当前 {rounds_used} 轮）")
    if now - float(st["createdAt"]) > float(cfg["timeoutSec"]):
        reasons.append(f"runTimeout={cfg['timeoutSec']}s 超时")
    spawned = int(st["counters"]["tasksSpawned"])
    budget = int(cfg["budgetTasks"])
    nonterminal = [
        f"r{r['index']}/{tid}"
        for r in st["rounds"]
        for tid, task in r.get("tasks", {}).items()
        if task["status"] in ("pending", "in_flight")
    ]
    if spawned > budget:
        reasons.append(f"budgetTasks={budget} 超支（已 spawn {spawned}）")
    elif spawned >= budget and nonterminal:
        reasons.append(f"budgetTasks={budget} 预算已用尽，仍有未完任务 {', '.join(nonterminal)}")
    if external_stop_requested(store.run_dir):
        reasons.append("外部停止：STOP 哨兵文件存在")
    if failed_tasks:
        reasons.append("存在重试耗尽的 failed 任务: " + ", ".join(failed_tasks))

    if reasons:
        return {"stop": True, "verdict": "stopped", "reasons": reasons}
    if any_task and all_tasks_terminal and not open_rounds:
        # 所有轮都已关闭且全部任务 done/cancelled -> 自然收敛
        all_closed_pass = bool(st["rounds"]) and all(
            r["decision"] in ("pass", None) or r["status"] == "closed" for r in st["rounds"]
        )
        hard_failed = bool(failed_tasks)
        if all_closed_pass and not hard_failed:
            return {"stop": True, "verdict": "converged", "reasons": ["全部任务完成且无 open round"]}
    return {"stop": False, "verdict": "continue", "reasons": []}


# ---------------------------------------------------------------------------
# 中断（协作式）
# ---------------------------------------------------------------------------


def interrupt_run(store: LoopStore, reason: str) -> dict[str, Any]:
    """协作中断：状态转 interrupted 并返回 in_flight 清单（agent 随后 close_agent）。"""
    affected = store.run_interrupted(reason)
    return {
        "status": "interrupted",
        "inFlight": affected,
        "hint": "对清单内 agent 执行 close_agent，之后 loop_state.py resume 续跑",
    }


# ---------------------------------------------------------------------------
# run 发现（供 Stop hook / status 使用）
# ---------------------------------------------------------------------------


def find_active_runs(root: Path) -> tuple[list[Path], list[Path]]:
    """返回 (活跃 run 目录, 损坏 run 目录)。

    损坏的 run 不静默跳过：交由调用方 fail-closed 地给出修复指引，
    避免状态损坏时任务被静默丢弃（对齐 spec 守卫的 fail-closed 哲学）。
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

    p = sub.add_parser("stale", help="staleness 检测")
    p.add_argument("--run-dir", required=True)

    p = sub.add_parser("terminate", help="终止条件族评估")
    p.add_argument("--run-dir", required=True)

    p = sub.add_parser("interrupt", help="协作中断")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--reason", default="manual interrupt")

    p = sub.add_parser("backoff", help="计算第 N 次失败后的退避秒数")
    p.add_argument("--attempt", type=int, required=True)
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

            _emit_json(
                {
                    "attempt": args.attempt,
                    "delaySec": backoff_delay(args.attempt, default_config()["retry"], rng),
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

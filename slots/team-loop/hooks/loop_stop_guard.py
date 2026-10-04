#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Claude Code Stop hook：spec team-loop 插槽收敛守卫。

协议（官方 hooks 文档，Stop 事件）：
- stdin JSON 含 stop_hook_active / last_assistant_message / background_tasks 等字段。
- stdout JSON {"decision": "block", "reason": ...} 阻止熄火；exit 0 无输出放行。
- Claude Code 在 8 次连续 block 后强制放行；stop_hook_active=true 时本 hook 静默，
  避免每轮双重执法。

行为（抄 spec 的 claude_stop_guard.py 骨架，判据换成 loop 收敛）：
- 从 cwd 向上找项目根，列出活跃 run。
- 无活跃 run -> 放行（绝不误伤普通会话）。
- 活跃 run 未收敛 -> block，reason 携带下一步动作（保证每轮 block 之间有实质推进）。
- 多个活跃 run -> block 一次并列出，要求 agent 收敛歧义（fail closed）。
- 活跃 run 命中终止条件（stopped）-> block 并给出 finish 指令；
  自然收敛（converged 状态已在 loop_state 侧落盘）-> 放行。
"""

from __future__ import annotations

import json
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
        emit_block(f"spec team-loop：无法安全读取 run 状态（{exc}）。请修复状态或确认已废弃后再结束会话。")
        return 0

    if corrupt:
        # 损坏的 run 不静默放行：block 一次并列出修复入口（fail-closed）
        names = "；".join(p.name for p in corrupt[:5])
        emit_block(
            f"spec team-loop：存在状态损坏的 run（{names}）。"
            f"请逐个排查修复后再结束：python3 {SKILL_DIR}/scripts/loop_state.py status --run-dir <run目录>，"
            "或确认已废弃后删除其目录（.agents/runtime/loop/ 下对应 run）。"
        )
        return 0

    if not active:
        return 0
    if len(active) > 1:
        names = "；".join(p.name for p in active[:5])
        emit_block(
            f"spec team-loop：当前项目有多个活跃 run（{names}）。"
            "请逐个收敛或显式 finish/cancel 后再结束；歧义未消除前不放行。"
        )
        return 0

    run_dir = active[0]
    try:
        store = LoopStore(run_dir).load()
    except (OSError, LoopStateError) as exc:
        emit_block(
            f"spec team-loop：活跃 run 状态损坏（{run_dir}：{exc}）。"
            f"请修复或运行 python3 {SKILL_DIR}/scripts/loop_state.py resume --run-dir {run_dir} 排查后再结束。"
        )
        return 0

    try:
        verdict = termination_check(store, utc_now())
    except (OSError, LoopStateError) as exc:
        # 例如 STOP 哨兵为 symlink：终止判定自身异常时必须 fail-closed，
        # 未捕获崩溃会让守卫以非 0 非 2 退出码静默放行未收敛 run。
        emit_block(
            f"spec team-loop：终止判定无法安全评估（{run_dir}：{exc}）。"
            f"请修复状态（如移除异常的 STOP 哨兵：python3 -c \"import os; os.unlink('{run_dir}/STOP')\"）"
            "后再结束会话。"
        )
        return 0
    if verdict["stop"]:
        reasons = "；".join(verdict["reasons"])
        if verdict["verdict"] == "converged":
            emit_block(
                f"spec team-loop：run 已满足收敛条件（{reasons}）。"
                f"执行 python3 {SKILL_DIR}/scripts/loop_state.py finish converged --run-dir {run_dir} "
                '--summary "<成果摘要>" 落终态后再结束。'
            )
        else:
            emit_block(
                f"spec team-loop：run 命中管理停止条件（{reasons}）。"
                f"请执行 python3 {SKILL_DIR}/scripts/loop_state.py finish failed|cancelled --run-dir {run_dir} "
                "写明原因落终态，或调整配置后续跑；不得静默熄火。"
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
    parts = []
    if not open_rounds:
        parts.append('round start：python3 …/loop_state.py round start --goal "<本轮目标>"')
    if in_flight:
        parts.append(f"wait 在途任务 {', '.join(in_flight)} 并记录 task result")
        try:
            stale = stale_tasks(store, utc_now())
        except (OSError, LoopStateError):
            stale = []
        if stale:
            parts.append(
                f"心跳超时的在途任务 {', '.join(stale)}：worker 已死时执行 "
                f"python3 {SKILL_DIR}/scripts/loop_control.py stale --reap --run-dir {run_dir} 回收"
            )
    if pending:
        parts.append(f"重派 pending 任务 {', '.join(pending)}（先过 admit 并发判定）")
    if not parts:
        parts.append("按终止判定推进下一轮或 finish converged")
    emit_block(
        f"spec team-loop：run {store.state['runId']} 未收敛"
        f"（goal：{store.state['goal'][:80]}）。下一步：{'；'.join(parts)}。"
        f"状态详情：python3 {SKILL_DIR}/scripts/loop_state.py status --run-dir {run_dir}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

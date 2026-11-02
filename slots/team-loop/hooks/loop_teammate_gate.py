#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Claude Code TeammateIdle / TaskCompleted hook：teammate 质量门禁。

协议（官方 hooks 文档）：
- TeammateIdle：teammate 即将空闲。exit 2 + stderr -> 反馈送回 teammate，令其继续工作；
  JSON {"continue": false, "stopReason": ...} -> 彻底停止 teammate。
- TaskCompleted：任务被标记完成时触发，可在关闭前强制验收。

行为：在活跃 run 中定位 teammate 对应的 in_flight 任务；
- 未交接时要求 worker 仅提交非终态报告并通知主会话；
- 已交接后放行，以免阻止 worker 结束；该报告不会释放并发额度。
- 主会话须从宿主核实执行结束后才记录终态；无活跃 run 或无对应任务则放行。
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
    """teammate 与 agentId 的对应判定：等值优先，其次前缀+分隔符。

    无锚点子串匹配会把名为 worker 的 teammate 错配到 review-worker 的
    任务上，并诱导它替别人的任务记 result。
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
        # 事件字段缺失时无法确认本门禁被触发，放行（守卫事件面由注册方约束）。
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
        return fail_closed(f"spec team-loop 门禁：无法安全读取 run 状态：{exc}")
    if corrupt:
        # 与 stop 守卫同哲学：损坏的 run 不静默跳过，fail-closed 列出修复入口。
        names = "；".join(p.name for p in corrupt[:5])
        return fail_closed(
            f"spec team-loop 门禁：存在状态损坏的 run（{names}），无法核验 in_flight 任务。"
            f"请修复：python3 {SKILL_DIR}/scripts/loop_state.py status --run-dir <run目录>"
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
            "spec team-loop 门禁：以下活跃 run 状态无法读取，in_flight 任务未核验：" + "；".join(load_failed)
        )

    if not matches:
        return 0

    if len(matches) > 1:
        # 多匹配不静默取首个：列出全部，避免指示 teammate 替别人的任务记 result。
        listed = "；".join(f"r{r}/{tid}@{rd.name}" for rd, r, tid, _ in matches)
        return fail_closed(
            f"spec team-loop 门禁：teammate {teammate} 命中多个 in_flight 任务（{listed}），"
            "身份歧义需先收敛（agentId 命名加区分度）再放行。"
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
        f"spec team-loop 门禁：run {run_dir.name} 的任务 r{round_index}/{tid} 仍是 in_flight。"
        "请将真实工作与验证记录写入 JSON 对象文件 worker-report.json（若文件已存在则另选文件名并调整命令），"
        "仅提交非终态交接并通知主会话，不要自行记录 task result 或取消任务：\n"
        f"python3 {shlex.quote(str(SKILL_DIR / 'scripts' / 'loop_state.py'))} task report --run-dir {shlex.quote(str(run_dir))} "
        f"--round {round_index} --id {shlex.quote(tid)} --result @worker-report.json{fence}\n"
        "交接不释放额度；主会话须向宿主核实该 worker 已结束后才记录终态。"
    )


if __name__ == "__main__":
    sys.exit(main())

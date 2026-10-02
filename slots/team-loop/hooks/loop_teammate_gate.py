#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""Claude Code TeammateIdle / TaskCompleted hook：teammate 质量门禁。

协议（官方 hooks 文档）：
- TeammateIdle：teammate 即将空闲。exit 2 + stderr -> 反馈送回 teammate，令其继续工作；
  JSON {"continue": false, "stopReason": ...} -> 彻底停止 teammate。
- TaskCompleted：任务被标记完成时触发，可在关闭前强制验收。

行为：在活跃 run 中定位 teammate 对应的 in_flight 任务；
- 任务未记录 ok 结果 -> exit 2，反馈「先跑 verify 并记录 task result」；
- 无活跃 run 或无对应任务 -> 放行（exit 0）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from loop_control import find_active_runs  # noqa: E402
from loop_state import LoopStateError, LoopStore, find_run_root  # noqa: E402


def fail_closed(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    event = payload.get("hook_event_name")
    if event not in ("TeammateIdle", "TaskCompleted", "SubagentStop"):
        return 0

    teammate = payload.get("teammate_name") or payload.get("agent_id") or ""
    cwd_value = payload.get("cwd")
    cwd = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else Path.cwd()

    root = find_run_root(cwd)
    if root is None:
        return 0
    try:
        active, _corrupt = find_active_runs(root)
    except (OSError, LoopStateError) as exc:
        return fail_closed(f"spec team-loop 门禁：无法安全读取 run 状态：{exc}")
    if not active:
        return 0

    matches = []  # (run_dir, round, task_id, task)
    for run_dir in active:
        try:
            store = LoopStore(run_dir).load()
        except (OSError, LoopStateError):
            continue
        for rnd in store.state["rounds"]:
            for tid, task in rnd.get("tasks", {}).items():
                if task["status"] != "in_flight":
                    continue
                agent = str(task.get("agentId") or "")
                if teammate and (teammate in agent or agent in teammate if agent else False):
                    matches.append((run_dir, rnd["index"], tid, task))

    if not matches:
        return 0

    run_dir, round_index, tid, task = matches[0]
    # TaskCompleted/TeammateIdle 语义：任务在 run 状态里还挂着 in_flight
    return fail_closed(
        f"spec team-loop 门禁：run {run_dir.name} 中任务 r{round_index}/{tid}（{task.get('description', '')[:80]}）"
        f"仍是 in_flight。先完成工作并运行验证，然后记录结果："
        f"python3 {SKILL_DIR}/scripts/loop_state.py task result --run-dir {run_dir} "
        f'--round {round_index} --id {tid} --outcome ok --result "<验证证据>"。'
    )


if __name__ == "__main__":
    sys.exit(main())

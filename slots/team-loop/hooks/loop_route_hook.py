#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""Claude Code UserPromptSubmit hook：代码触发路由注入。

协议（官方 hooks 文档）：
- stdin JSON 含 prompt 字段与公共字段。
- exit 0 + stdout JSON 的 hookSpecificOutput.additionalContext 会以 system reminder
  注入 Claude 上下文（≤10K 字符），不下发可见消息。
- 永不 block：路由是建议，不是门禁。

行为：对 prompt 跑 loop_route 判定；推荐 loop 时注入
「加载 spec 技能并激活 team-loop 插槽按建议配置初始化 run」的上下文。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ROUTE_SCRIPT = SKILL_DIR / "scripts" / "loop_route.py"
ROUTE_TIMEOUT_SECONDS = 10
CONTEXT_CAP = 9000  # 官方 10K 上限留余量


def build_additional_context(decision: dict) -> str:
    config = decision.get("suggestedConfig") or {}
    cfg_lines = json.dumps(config, ensure_ascii=False)
    context = (
        "[spec team-loop 插槽提示] 检测到循环型任务特征"
        f"（{decision.get('reason', '')}）。请加载 spec 技能并激活 team-loop 插槽"
        "（协议：spec 安装根 slots/team-loop/README.md），"
        "并按以下流程接管：\n"
        '1. `python3 <spec 安装根>/slots/team-loop/scripts/loop_state.py init --goal "<本任务目标>" --mode '
        f"{decision.get('mode', 'until-converged')} --config '{cfg_lines}'`\n"
        "2. 按 README 协议逐轮推进（round_start -> 并发受控 spawn -> wait+心跳 -> task result -> 终止判定）。\n"
        "3. 未收敛前 Stop hook 会阻止熄火；需要暂停用 `loop_control.py interrupt`，恢复用 `loop_state.py resume`。"
    )
    return context[:CONTEXT_CAP]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return 0

    if not ROUTE_SCRIPT.is_file():
        return 0
    try:
        completed = subprocess.run(
            [sys.executable, str(ROUTE_SCRIPT), "--json"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=ROUTE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return 0  # 路由器故障不阻塞用户提示
    try:
        decision = json.loads(completed.stdout)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(decision, dict) or not decision.get("loopRecommended"):
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": build_additional_context(decision),
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

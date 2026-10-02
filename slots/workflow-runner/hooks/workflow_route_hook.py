#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""UserPromptSubmit hook：workflow-runner 路由建议注入。

协议（官方 hooks 文档）：
- stdin JSON 含 prompt 字段与公共字段。
- exit 0 + stdout JSON 的 hookSpecificOutput.additionalContext 会以 system reminder
  注入 Claude 上下文（≤10K 字符），不下发可见消息。
- 永不 block：路由是建议，不是门禁。

行为：对 prompt 跑 workflow_route 判定；推荐托管时注入
「按 workflow-runner 协议使用仓库自有 fan-out 驱动」的上下文。

安全（评审回补）：
- prompt 通过 stdin 传给路由脚本，不进 argv（防进程列表泄露与超长截断）。
- 任何解析/运行时异常一律静默 exit 0（fail-open）。
- prompt 字段做 isinstance(str) 守卫，非字符串直接放行。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ROUTE_SCRIPT = SKILL_DIR / "scripts" / "workflow_route.py"
ROUTE_TIMEOUT_SECONDS = 10
CONTEXT_CAP = 9000  # 官方 10K 上限留余量


def build_additional_context(decision: dict) -> str:
    config = decision.get("suggestedConfig") or {}
    return (
        "[spec workflow-runner 插槽提示] 检测到确定性编排型任务特征"
        f"（{decision.get('reason', '')}）。请加载 spec 技能并按 workflow-runner 协议 "
        "使用仓库自有 slots/workflow-runner/scripts/workflow_fanout.py 驱动执行段；"
        "后端不可用时按 references/orchestration.md 走普通 sidecar 或 team-loop；"
        "执行面固定为仓库驱动，不随会话工具列表变化。"
        f"建议模式：{decision.get('mode', '')}；建议配置：{json.dumps(config, ensure_ascii=False)}。"
        "routing、验收与 done/push 门禁保持在主会话。"
    )


def _route_prompt(prompt: str) -> dict | None:
    """prompt 走 stdin（不进 argv）；任何失败返回 None（fail-open）。"""
    try:
        result = subprocess.run(
            [sys.executable, str(ROUTE_SCRIPT), "--json"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=ROUTE_TIMEOUT_SECONDS,
        )
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError, ValueError, TypeError):
        return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — hook 永不 block，任何输入异常静默放行
        return 0

    if not isinstance(payload, dict):
        return 0

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return 0

    decision = _route_prompt(prompt)
    if not isinstance(decision, dict) or not decision.get("workflowRecommended"):
        return 0

    context = build_additional_context(decision)
    output = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context[:CONTEXT_CAP]}}
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

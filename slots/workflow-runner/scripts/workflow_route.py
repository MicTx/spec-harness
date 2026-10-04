#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""workflow-runner 路由决策：任务文本 -> 是否需要仓库自有确定性编排 + 建议模式。

判定为启发式评分（可解释、可测试），与 team-loop 的 loop_route.py 同构：
- fan-out 形状：批量独立目标 / pipeline over items
- 评审形状：多维度并行评审 -> 验证 -> 合流
- 视角形状：judge panel / 多视角独立分析
- 收敛形状：评审-修复-再验证闭环

永不抛错，永远返回结构化结果。激活只是建议：最终执行仍由主会话依据
仓库自有驱动与 team-loop 契约决定，后端不可用时显式降级为普通 sidecar 编排。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Optional

MODES = ("batch-fanout", "parallel-review", "perspective-panel", "review-fix-loop", "none")

FANOUT_PATTERNS = [
    r"批量",
    r"所有.{0,8}(章节|文件|模块|项目|条目|页面)都",
    r"每个.{0,8}(逐一|分别|独立)",
    r"\d+\s*(个|篇|章|项|条).{0,10}(独立|不同|分别)",
    r"\bfan[- ]?out\b",
    r"\bpipeline\b.{0,20}\b(over|each|items)\b",
    r"逐(个|条|项)处理",
    r"\bfor each\b.{0,20}\b(spawn|run|review|handle|process)\b",
]
REVIEW_PATTERNS = [
    r"(多维度|多个维度|多角度).{0,8}(评审|审查|审阅)",
    r"并行.{0,6}(评审|审查|review)",
    r"评审.{0,8}(维度|dimension)",
    r"\breview\b.{0,16}\b(dimension|lens|perspective)s?\b",
    r"多视角.{0,6}(评审|分析|review)",
    r"\badversarial\w*\b.{0,16}\bverif\w+\b",
    r"(找|排查).{0,10}(bug|缺陷|问题).{0,8}(全|彻底|全面)",
    r"\breview the\b.{0,40}\b(correctness|security|perf)\b",
    r"(全面|彻底|全量).{0,4}(审查|审阅|review|audit)",
    r"\bfull\b.{0,8}\b(audit|review|sweep)\b",
]
PANEL_PATTERNS = [
    r"judge panel",
    r"多(个)?(独立)?(方案|视角|观点).{0,8}(对比|评分|评判|合流)",
    r"\d+\s*个(独立)?(方案|视角|观点).{0,8}(对比|评分|评判|择优)",
    r"独立.{0,6}(评审人|评审员|裁判)",
    r"perspective.{0,10}(sweep|panel)",
    r"(方案|设计).{0,6}(竞赛|打分|择优)",
]
LOOP_PATTERNS = [
    r"评审.{0,8}后.{0,8}(修复|改)",
    r"(修复|改正).{0,6}(后.{0,4}再|并复)(审|验|测)",
    r"review.{0,10}fix.{0,25}\b(loop|again|re-?run|converge)\b",
    r"修复.{0,10}再.{0,6}(评审|验证)",
    r"直到.{0,10}(评审|审查|所有).{0,8}(通过|无问题|收敛)",
    r"\biterate\b.{0,20}\buntil\b.{0,12}\b(converge|pass|green|done)\b",
]
WORKFLOW_EXPLICIT = [
    r"\bworkflow\b.{0,20}(工具|编排|run)",
    r"ultracode",
    r"用.{0,4}workflow.{0,10}(跑|编排|托管)",
]


def _score(text: str, patterns: list[str]) -> int:
    return sum(1 for pat in patterns if re.search(pat, text, re.IGNORECASE))


def route(text: str) -> dict[str, Any]:
    """对任务文本做路由判定。永不抛错，永远返回结构化结果。"""
    text = (text or "").strip()
    if not text:
        return {
            "workflowRecommended": False,
            "mode": "none",
            "score": 0,
            "reason": "空文本",
            "suggestedConfig": None,
            "suggestedSurface": None,
        }

    fanout_hits = _score(text, FANOUT_PATTERNS)
    review_hits = _score(text, REVIEW_PATTERNS)
    panel_hits = _score(text, PANEL_PATTERNS)
    loop_hits = _score(text, LOOP_PATTERNS)
    explicit_hits = _score(text, WORKFLOW_EXPLICIT)

    # 五类形态同权 2x：任一形态的单个典型信号都应能过门禁（与 loop_route.py 的 loop_hits*2 对齐）
    total = (fanout_hits + review_hits + panel_hits + loop_hits + explicit_hits) * 2
    recommended = total >= 2

    mode = "batch-fanout"
    if loop_hits:
        mode = "review-fix-loop"
    elif panel_hits:
        # panel 的显式「对比/评分/择优」信号优先于泛化 fan-out/review 信号
        mode = "perspective-panel"
    elif review_hits:
        mode = "parallel-review"
    if not recommended:
        mode = "none"

    config: Optional[dict[str, Any]] = None
    if recommended:
        config = {
            "mode": mode,
            "pattern": {
                "batch-fanout": "pipeline(items, stageA, stageB) — 每条链独立推进，无阶段间屏障",
                "parallel-review": "dimensions -> findings -> adversarial verify -> synthesize（review-changes 模式）",
                "perspective-panel": "N 个独立方案 -> 并行 judge 评分 -> 从胜者综合并嫁接次优想法",
                "review-fix-loop": "find -> dedup vs seen -> 多 lens 判定 -> loop-until-dry 收敛",
            }[mode],
            "defaultEffort": "low",
            "verifyEffort": "high",
            "agentBudgetGuideline": "单项任务 <10 agents；确需更大规模由用户显式提升",
        }

    # 唯一执行面由仓库拥有；pi/codex 只是可插拔 worker CLI 后端，不是宿主编排引擎。
    # 路由不枚举宿主身份，避免同一任务因宿主工具列表而改变编排语义。
    surface: Optional[list[dict[str, str]]] = None
    if recommended:
        surface = [
            {
                "surface": "subprocess-fanout",
                "owner": "spec-harness",
                "how": "slots/workflow-runner/scripts/workflow_fanout.py --backend <pi|codex>",
            },
        ]

    reasons: list[str] = []
    if fanout_hits:
        reasons.append(f"批量独立目标 x{fanout_hits}")
    if review_hits:
        reasons.append(f"多维并行评审 x{review_hits}")
    if panel_hits:
        reasons.append(f"独立视角/裁判组 x{panel_hits}")
    if loop_hits:
        reasons.append(f"评审-修复闭环 x{loop_hits}")
    if explicit_hits:
        reasons.append(f"显式 workflow 词 x{explicit_hits}")

    return {
        "workflowRecommended": recommended,
        "mode": mode,
        "score": total,
        "reason": "；".join(reasons) if reasons else "无 workflow 形态特征",
        "suggestedConfig": config,
        "suggestedSurface": surface,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="workflow_route.py", description="workflow-runner 路由判定")
    parser.add_argument("--text", default=None, help="任务文本；缺省读 stdin")
    parser.add_argument("--file", default=None, help="从文件读任务文本")
    parser.add_argument("--json", action="store_true", help="输出 JSON（供 hook 消费）")
    args = parser.parse_args(argv)

    if args.text is not None:
        text = args.text
    elif args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            print(f"[workflow-route] 无法读取文件: {exc}", file=sys.stderr)
            return 2
    else:
        text = sys.stdin.read()

    decision = route(text)
    if args.json:
        print(json.dumps(decision, ensure_ascii=False, indent=2))
    else:
        flag = "推荐" if decision["workflowRecommended"] else "不推荐"
        print(f"[workflow-route] {flag} mode={decision['mode']} score={decision['score']}（{decision['reason']}）")
        if decision["suggestedConfig"]:
            print(json.dumps(decision["suggestedConfig"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

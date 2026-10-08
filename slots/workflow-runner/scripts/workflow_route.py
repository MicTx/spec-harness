#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""workflow-runner 路由决策：任务文本 -> 是否需要仓库自有确定性编排 + 建议模式。

判定为启发式评分（可解释、可测试），与 team-loop 的 loop_route.py 同构：
- fan-out 形状：批量独立目标 / pipeline over items
- 评审形状：多维度并行评审 -> 验证 -> 合流
- 视角形状：judge panel / 多视角独立分析
- 收敛形状：评审-修复-再验证闭环

形态信号分强弱：强信号是自带可并行/可托管形态语义的组合形态（单个即过阈）；
弱信号是泛化词——裸「批量」与裸收敛标记（如 iterate until converge、直到通过），
只有与其他形态信号共现才有判读价值，单独不推荐。
score = 2 × 强信号命中数 + 1 × 弱信号命中数；推荐须 score >= 2 且强信号命中数 >= 1。
mode 推导只看强信号（loop > panel > review > fanout），未推荐时 mode=none；
仅 WORKFLOW_EXPLICIT 命中时沿用 batch-fanout 兼容 mode，不新增 explicit mode。

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

FANOUT_STRONG_PATTERNS = [
    # 组合形态信号：自带可并行/可托管语义，单个即过门禁。
    r"所有.{0,8}(章节|文件|模块|项目|条目|页面)都",
    r"每个.{0,8}(逐一|分别|独立)",
    r"\d+\s*(个|篇|章|项|条).{0,10}(独立|不同|分别)",
    r"\bfan[- ]?out\b",
    r"\bpipeline\b.{0,20}\b(over|each|items)\b",
    r"逐(个|条|项)处理",
    r"\bfor each\b.{0,20}\b(spawn|run|review|handle|process)\b",
    # 「批量 + 实质工作动词」：可托管 fan-out 形态；动词表是语料驱动的显式常量，扩表走代码评审。
    r"批量.{0,12}(梳理|评审|审查|审阅|实现|重构|处理|分析|翻译|校验|测试|迁移|抽取|盘点)",
]
# 泛化弱信号：单独出现（如「批量更新版本号」「先批量看一眼」）不推荐，仅共现补分。
FANOUT_WEAK_PATTERNS = [
    r"批量",
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
# 评审-修复复合形态：自带「评审 -> 修复 -> 再收敛」闭环语义，单个即过门禁（强信号）。
# R3 收窄裁定（plans/03 F10）：收敛词（直到/iterate until…）永不单独进强表。
LOOP_STRONG_PATTERNS = [
    r"评审.{0,8}后.{0,8}(修复|改)",
    r"(修复|改正).{0,6}(后.{0,4}再|并复)(审|验|测)",
    r"review.{0,16}fix.{0,25}\b(loop|again|re-?run|converge)\b",
    r"修复.{0,10}再.{0,6}(评审|验证)",
    r"(fix|修复|改正).{0,40}(再审|复审|重审|review again|re-?review|re-?check)",
]
# 裸收敛标记：仅表「循环到通过/收敛」意图，不带评审-修复复合语义，单独不推荐（弱信号）。
LOOP_WEAK_PATTERNS = [
    r"直到.{0,10}(通过|收敛|完成|达标)",
    r"\biterate\b.{0,20}\buntil\b.{0,12}\b(converge|pass|green|done)\b",
    r"\bretry\b.{0,30}\buntil\b",
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

    fanout_strong_hits = _score(text, FANOUT_STRONG_PATTERNS)
    fanout_weak_hits = _score(text, FANOUT_WEAK_PATTERNS)
    review_hits = _score(text, REVIEW_PATTERNS)
    panel_hits = _score(text, PANEL_PATTERNS)
    loop_strong_hits = _score(text, LOOP_STRONG_PATTERNS)
    loop_weak_hits = _score(text, LOOP_WEAK_PATTERNS)
    explicit_hits = _score(text, WORKFLOW_EXPLICIT)

    # 强弱分权：review/panel/explicit 词表本身即组合形态语义，全部为强信号；
    # fan-out 与 loop 各拆强弱两表——loop 强表是评审-修复复合形态，loop 弱表是裸收敛标记
    # （iterate until…/直到通过/直到收敛）。任一强信号单命中即过门禁（与 loop_route.py
    # 的 loop_hits*2 对齐），弱信号单独计 1 分不过阈，仅在共现时补足加权分。
    strong_hits = fanout_strong_hits + review_hits + panel_hits + loop_strong_hits + explicit_hits
    weak_hits = fanout_weak_hits + loop_weak_hits
    total = strong_hits * 2 + weak_hits
    recommended = total >= 2 and strong_hits >= 1

    # mode 推导只看强信号（loop > panel > review > fanout）；弱信号不参与 mode 选择。
    # 仅 WORKFLOW_EXPLICIT 命中时沿用 batch-fanout 兼容 mode（不新增 explicit mode）。
    mode = "batch-fanout"
    if loop_strong_hits:
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
    if fanout_strong_hits:
        reasons.append(f"批量独立目标 x{fanout_strong_hits}")
    if review_hits:
        reasons.append(f"多维并行评审 x{review_hits}")
    if panel_hits:
        reasons.append(f"独立视角/裁判组 x{panel_hits}")
    if loop_strong_hits:
        reasons.append(f"评审-修复闭环 x{loop_strong_hits}")
    if explicit_hits:
        reasons.append(f"显式 workflow 词 x{explicit_hits}")
    if fanout_weak_hits:
        reasons.append(f"泛化批量(弱) x{fanout_weak_hits}")
    if loop_weak_hits:
        reasons.append(f"裸收敛(弱) x{loop_weak_hits}")

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

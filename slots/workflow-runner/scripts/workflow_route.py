#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Recommend repository-owned deterministic orchestration and a mode for task text.

Heuristic scoring recognizes fan-out, multidimensional review, independent perspectives,
and review-fix loops. Each strong signal contributes two points; weak signals such as
generic batch terms or bare convergence markers contribute one. A recommendation
requires at least one strong signal and a score of two. Mode selection uses only strong
signals, in loop > panel > review > fanout order; explicit-only requests retain the
batch-fanout compatibility mode. The result is advisory: the main session decides how
to execute and handles any unavailable capability without automatic substitution.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_fanout import _platform_support_error  # noqa: E402

MODES = ("batch-fanout", "parallel-review", "perspective-panel", "review-fix-loop", "none")

FANOUT_STRONG_PATTERNS = [
    # Each composite parallel or managed-work signal crosses the threshold alone.
    r"所有.{0,8}(章节|文件|模块|项目|条目|页面)都",
    r"每个.{0,8}(逐一|分别|独立)",
    r"\d+\s*(个|篇|章|项|条).{0,10}(独立|不同|分别)",
    r"\bfan[- ]?out\b",
    r"\bpipeline\b.{0,20}\b(over|each|items)\b",
    r"逐(个|条|项)处理",
    r"\bfor each\b.{0,20}\b(spawn|run|review|handle|process)\b",
    # Generic batch plus an action verb is a strong fan-out signal; review additions to this list.
    r"批量.{0,12}(梳理|评审|审查|审阅|实现|重构|处理|分析|翻译|校验|测试|迁移|抽取|盘点)",
]
# A generic batch term is weak and only adds weight alongside another signal.
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
# Composite review-fix-reverify signals are strong; bare convergence is not.
LOOP_STRONG_PATTERNS = [
    r"评审.{0,8}后.{0,8}(修复|改)",
    r"(修复|改正).{0,6}(后.{0,4}再|并复)(审|验|测)",
    r"review.{0,16}fix.{0,25}\b(loop|again|re-?run|converge)\b",
    r"修复.{0,10}再.{0,6}(评审|验证)",
    r"(fix|修复|改正).{0,40}(再审|复审|重审|review again|re-?review|re-?check)",
]
# Bare convergence markers are weak and do not recommend workflow by themselves.
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
    """Return a structured routing decision for task text."""
    text = (text or "").strip()
    if not text:
        return {
            "workflowRecommended": False,
            "mode": "none",
            "score": 0,
            "reason": "empty text",
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

    # Review, panel, and explicit signals are strong; fan-out and loop have separate
    # strong and weak tables. Weak signals add weight but cannot recommend alone.
    strong_hits = fanout_strong_hits + review_hits + panel_hits + loop_strong_hits + explicit_hits
    weak_hits = fanout_weak_hits + loop_weak_hits
    total = strong_hits * 2 + weak_hits
    recommended = total >= 2 and strong_hits >= 1

    # Mode depends only on strong signals; explicit-only requests retain batch-fanout.
    mode = "batch-fanout"
    if loop_strong_hits:
        mode = "review-fix-loop"
    elif panel_hits:
        # Explicit panel comparison and scoring take precedence over fan-out/review.
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
                "batch-fanout": (
                    "pipeline(items, stageA, stageB) — each chain progresses independently, with no stage barrier"
                ),
                "parallel-review": (
                    "dimensions -> findings -> adversarial verification -> synthesis (review-changes mode)"
                ),
                "perspective-panel": (
                    "N independent proposals -> parallel judge scoring -> synthesize the winner with runner-up ideas"
                ),
                "review-fix-loop": "find -> dedup vs seen -> multi-lens decision -> loop until dry",
            }[mode],
            "defaultEffort": "low",
            "verifyEffort": "high",
            "agentBudgetGuideline": (
                "fewer than 10 agents per item; the user must explicitly raise this for larger runs"
            ),
        }

    # The repository owns the execution surface; pi/codex are replaceable worker CLIs.
    # Host identity and tool inventory do not alter routing semantics.
    surface: Optional[list[dict[str, Any]]] = None
    if recommended:
        # Shape recommendations remain platform-independent; execution
        # availability uses the very same fail-closed probe as the driver.
        try:
            unavailable = _platform_support_error()
        except OSError:
            unavailable = "verified process-group cleanup capability could not be probed"
        surface = [
            {
                "surface": "subprocess-fanout",
                "owner": "spec-harness",
                "how": "slots/workflow-runner/scripts/workflow_fanout.py --backend <pi|codex>",
                "available": unavailable is None,
                "unavailableReason": unavailable,
                "fallback": (
                    "continue in the main session locally and serially; the host must explicitly authorize "
                    "and supervise sidecars, with no driver-equivalent cleanup guarantee; "
                    "do not take over workers whose status is unknown"
                )
                if unavailable
                else None,
            },
        ]

    reasons: list[str] = []
    if fanout_strong_hits:
        reasons.append(f"batch independent targets x{fanout_strong_hits}")
    if review_hits:
        reasons.append(f"multidimensional parallel review x{review_hits}")
    if panel_hits:
        reasons.append(f"independent perspectives/judge panel x{panel_hits}")
    if loop_strong_hits:
        reasons.append(f"review-fix loop x{loop_strong_hits}")
    if explicit_hits:
        reasons.append(f"explicit workflow terms x{explicit_hits}")
    if fanout_weak_hits:
        reasons.append(f"generic batch (weak) x{fanout_weak_hits}")
    if loop_weak_hits:
        reasons.append(f"bare convergence (weak) x{loop_weak_hits}")

    return {
        "workflowRecommended": recommended,
        "mode": mode,
        "score": total,
        "reason": "; ".join(reasons) if reasons else "no workflow shape signals",
        "suggestedConfig": config,
        "suggestedSurface": surface,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="workflow_route.py", description="workflow-runner routing decision")
    parser.add_argument("--text", default=None, help="task text; read stdin if omitted")
    parser.add_argument("--file", default=None, help="read task text from a file")
    parser.add_argument("--json", action="store_true", help="output JSON for hooks")
    args = parser.parse_args(argv)

    if args.text is not None:
        text = args.text
    elif args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            print(f"[workflow-route] cannot read file: {exc}", file=sys.stderr)
            return 2
    else:
        text = sys.stdin.read()

    decision = route(text)
    if args.json:
        print(json.dumps(decision, ensure_ascii=False, indent=2))
    else:
        flag = "recommended" if decision["workflowRecommended"] else "not recommended"
        print(f"[workflow-route] {flag} mode={decision['mode']} score={decision['score']} ({decision['reason']})")
        if decision["suggestedConfig"]:
            print(json.dumps(decision["suggestedConfig"], ensure_ascii=False))
        for surface in decision["suggestedSurface"] or []:
            if not surface["available"]:
                print(
                    f"[workflow-route] execution surface unavailable: {surface['unavailableReason']}; "
                    f"{surface['fallback']}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Recommend a managed team loop and configuration from task text.

Any session or script can call `loop_route.py --text "..."` for a structured decision.
Strong convergence signals and explicit round counts independently cross the recommendation
threshold. Generic loop and team terms are weak signals and only add weight alongside a
strong signal. Composite review-fix requests defer to workflow-runner before scoring;
bare convergence requests remain eligible for team-loop. The independently maintained
pattern tables are checked for behavioral consistency by tests/test_route_cross_consistency.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Optional

MODES = ("until-converged", "fixed-rounds", "none")

# Strong convergence signals independently cross the recommendation threshold.
LOOP_STRONG_PATTERNS = [
    r"直至",
    r"直到.{0,12}(通过|收敛|完成|达标)",
    r"到.{0,10}为止",
    r"一轮轮",
    r"自?[主自].{0,6}(推进|完成)",
    # A loop or iteration paired with convergence is a strong signal.
    r"(迭代|循环|多轮|反复).{0,6}收敛",
    r"\biterat\w*\b.{0,30}\buntil\b",
    r"\bretry\b.{0,30}\buntil\b",
    r"\buntil\b.{0,16}\b(pass|converge|green|done)\b",
]
# Generic loop terms are weak: they only add weight alongside strong signals or explicit rounds.
LOOP_WEAK_PATTERNS = [
    r"循环",
    r"多轮",
    r"迭代",
    r"反复",
    r"\bloop\b",
    r"\blooping\b",
]
TEAM_PATTERNS = [
    r"agents-team",
    r"spawn_agent",
    r"子代理",
    r"并行代理",
    r"多代理",
    r"\bsubagent",
    r"\bteam\b.{0,10}\bmode\b",
]
# Defer composite review-fix requests to workflow-runner. Bare convergence stays here.
# The separate pattern tables intentionally mirror each other's routing behavior.
REVIEW_FIX_DEFER_PATTERNS = [
    r"评审.{0,8}后.{0,8}(修复|改)",
    r"(修复|改正).{0,6}(后.{0,4}再|并复)(审|验|测)",
    r"review.{0,16}fix.{0,25}\b(loop|again|re-?run|converge)\b",
    r"修复.{0,10}再.{0,6}(评审|验证)",
    r"(fix|修复|改正).{0,40}(再审|复审|重审|review again|re-?review|re-?check)",
]
ROUND_EXPLICIT = re.compile(r"(\d+)\s*(轮|rounds?)", re.IGNORECASE)


def _score(text: str, patterns: list[str]) -> int:
    return sum(1 for pat in patterns if re.search(pat, text, re.IGNORECASE))


def route(text: str) -> dict[str, Any]:
    """Return a structured routing decision for task text."""
    text = (text or "").strip()
    if not text:
        return {"loopRecommended": False, "mode": "none", "score": 0, "reason": "empty text", "suggestedConfig": None}

    # Review-fix requests belong to workflow-runner; bare convergence is unaffected.
    if _score(text, REVIEW_FIX_DEFER_PATTERNS):
        return {
            "loopRecommended": False,
            "mode": "none",
            "score": 0,
            "reason": "review-fix loops belong to workflow-runner (2026-10-04 decision)",
            "suggestedConfig": None,
        }

    # A strong convergence signal or explicit round count is required; weak terms only add weight.
    strong_hits = _score(text, LOOP_STRONG_PATTERNS)
    weak_hits = _score(text, LOOP_WEAK_PATTERNS)
    team_hits = _score(text, TEAM_PATTERNS)
    explicit_rounds = ROUND_EXPLICIT.search(text)

    total = strong_hits * 2 + weak_hits + team_hits + (2 if explicit_rounds else 0)
    recommended = total >= 2 and (strong_hits >= 1 or explicit_rounds is not None)

    mode = "until-converged"
    if explicit_rounds and strong_hits == 0:
        mode = "fixed-rounds"
    if not recommended:
        mode = "none"

    config: Optional[dict[str, Any]] = None
    if recommended:
        config = {
            "mode": mode,
            "maxRounds": 8,
            "concurrency": 3,
            "retry": {"maxAttempts": 3, "baseSec": 5.0, "maxSec": 60.0, "jitter": 0.3},
            "stalenessSec": 300.0,
            "timeoutSec": 7200.0,
            "budgetTasks": 64,
        }
        if explicit_rounds:
            config["maxRounds"] = min(100, max(1, int(explicit_rounds.group(1))))

    reasons = []
    if strong_hits:
        reasons.append(f"loop/convergence signal (strong) x{strong_hits}")
    if team_hits:
        reasons.append(f"explicit team terms x{team_hits}")
    if explicit_rounds:
        reasons.append(f"explicit rounds {explicit_rounds.group(0)}")
    if weak_hits:
        reasons.append(f"loop terms (weak) x{weak_hits}")

    return {
        "loopRecommended": recommended,
        "mode": mode,
        "score": total,
        "reason": "; ".join(reasons) if reasons else "no loop signals",
        "suggestedConfig": config,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="loop_route.py", description="agents-team-loop routing decision")
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
            print(f"[loop-route] cannot read file: {exc}", file=sys.stderr)
            return 2
    else:
        text = sys.stdin.read()

    decision = route(text)
    if args.json:
        print(json.dumps(decision, ensure_ascii=False, indent=2))
    else:
        flag = "recommended" if decision["loopRecommended"] else "not recommended"
        print(f"[loop-route] {flag} mode={decision['mode']} score={decision['score']} ({decision['reason']})")
        if decision["suggestedConfig"]:
            print(json.dumps(decision["suggestedConfig"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

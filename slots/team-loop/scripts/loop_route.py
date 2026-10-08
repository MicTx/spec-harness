#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""agents-team-loop 路由决策：任务文本 -> 是否进入受管 loop + 建议配置。

代码触发半边：任何会话/脚本都可以 `loop_route.py --text "..."` 拿到结构化判定，
无需人工记忆该加载哪个 skill。

判定为启发式评分（可解释、可测试），不做魔法：
- loop 形状：循环/多轮/迭代直至/直到通过/retry until 等收敛语义
- 显式团队词：agents-team/spawn_agent/子代理/并行代理
- 评审-修复让出：带「再审/复审」复合语义的文本先于通用打分让出

批量 fan-out 与评审-修复闭环形态归 workflow-runner（仓库自有执行面裁定）；
批量让出以删词实现（2026-10-04），评审-修复让出以显式早退实现（2026-10-08 补完，
词表与 workflow 侧 LOOP 强表行为级镜像、源码级独立——slot 自包含契约不允许跨 slot
import，两侧一致性由 tests/test_route_cross_consistency.py 行为级钉住）。
本脚本不再对这两类形态做推荐，避免双 slot 同文本撞车。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Optional

MODES = ("until-converged", "fixed-rounds", "none")

LOOP_PATTERNS = [
    r"循环",
    r"多轮",
    r"迭代",
    r"反复",
    r"直至",
    r"直到.{0,12}(通过|收敛|完成|达标)",
    r"到.{0,10}为止",
    r"一轮轮",
    r"自?[主自].{0,6}(推进|完成)",
    r"\bloop\b",
    r"\blooping\b",
    r"\biterat\w*\b.{0,30}\buntil\b",
    r"\bretry\b.{0,30}\buntil\b",
    r"\buntil\b.{0,16}\b(pass|converge|green|done)\b",
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
# 评审-修复复合形态让出（2026-10-04 裁定，2026-10-08 补完显式分支）：
# 与 workflow 侧 LOOP 强表同语义镜像；收敛词（直到/iterate until…）永不进本表，
# 裸收敛文本仍由本 slot 承接（until-converged）。
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
    """对任务文本做路由判定。永不抛错，永远返回结构化结果。"""
    text = (text or "").strip()
    if not text:
        return {"loopRecommended": False, "mode": "none", "score": 0, "reason": "空文本", "suggestedConfig": None}

    # 评审-修复复合形态让出（2026-10-04 裁定）：命中即归 workflow-runner，
    # 不再进入通用打分——避免双 slot 对同文本双推荐；裸收敛语义不受影响。
    if _score(text, REVIEW_FIX_DEFER_PATTERNS):
        return {
            "loopRecommended": False,
            "mode": "none",
            "score": 0,
            "reason": "评审-修复闭环归 workflow-runner（2026-10-04 裁定）",
            "suggestedConfig": None,
        }

    loop_hits = _score(text, LOOP_PATTERNS)
    team_hits = _score(text, TEAM_PATTERNS)
    explicit_rounds = ROUND_EXPLICIT.search(text)

    total = loop_hits * 2 + team_hits + (2 if explicit_rounds else 0)
    recommended = total >= 2

    mode = "until-converged"
    if explicit_rounds and loop_hits == 0:
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
    if loop_hits:
        reasons.append(f"循环/收敛语义 x{loop_hits}")
    if team_hits:
        reasons.append(f"显式团队词 x{team_hits}")
    if explicit_rounds:
        reasons.append(f"显式轮数 {explicit_rounds.group(0)}")

    return {
        "loopRecommended": recommended,
        "mode": mode,
        "score": total,
        "reason": "；".join(reasons) if reasons else "无循环特征",
        "suggestedConfig": config,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="loop_route.py", description="agents-team-loop 路由判定")
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
            print(f"[loop-route] 无法读取文件: {exc}", file=sys.stderr)
            return 2
    else:
        text = sys.stdin.read()

    decision = route(text)
    if args.json:
        print(json.dumps(decision, ensure_ascii=False, indent=2))
    else:
        flag = "推荐" if decision["loopRecommended"] else "不推荐"
        print(f"[loop-route] {flag} mode={decision['mode']} score={decision['score']}（{decision['reason']}）")
        if decision["suggestedConfig"]:
            print(json.dumps(decision["suggestedConfig"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

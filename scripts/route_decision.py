#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""spec 编排路由决策：任务文本 -> 五路由判定 + lane 建议。

把 `### 5.4 编排策略` 的 route 词表从纯约定升级为机器可判定入口：
`/spec:new` 定范围后、`/spec:run` 执行前，任何会话都可以运行

    python3 scripts/route_decision.py --text "<goal>"

拿到结构化 JSON（route / score / reason / lanes），代替凭记忆写 5.4。
判定为显式启发式评分（可解释、可测试），不做魔法：
- local: 小而局部、单文件、无并行收益
- explore: 理解缺失、结构不清、需要多面并行探察
- build: 实现具体、ownership 可按模块/文件干净切分
- review: 代码已存在、风险评审是主要需求
- external: 需要隔离 git 状态、长时间运行或多终端监控

契约详见 references/orchestration.md；lane 建议是草案，
主会话仍按 assignment contract 细化后才会委派。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# 单源词表：与门禁接受的编排 token 共用 spec_package_support.ORCHESTRATION_ROUTES，
# 避免两份字面量静默漂移（route 判出的 token 必须是门禁认可的 token）。
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from spec_package_support import ORCHESTRATION_ROUTES as ROUTES  # noqa: E402

# 小而局部：没有跨模块/并行信号，直接主线程做。
LOCAL_PATTERNS = [
    r"一行",
    r"错别字",
    r"typo",
    r"改个?配[置定]",
    r"小修",
    r"顺手",
    r"单[个一]文件",
    r"\bfix a typo\b",
    r"\bone[- ]liner\b",
]

# 探察/理解缺失信号。
EXPLORE_PATTERNS = [
    r"梳理",
    r"摸清",
    r"盘点",
    r"调研",
    r"理解",
    r"结构不清",
    r"不熟",
    r"陌生",
    r"探[索察]",
    r"多面",
    r"全景",
    r"链路",
    r"架构摸底",
    r"\bexplore\b",
    r"\bmap\b",
    r"\bhow does\b",
    r"\bunderstand\b",
    r"\bunfamiliar\b",
]

# 可并行实现信号。
BUILD_PATTERNS = [
    r"实现",
    r"落地",
    r"开发",
    r"补齐",
    r"重构?实现",
    r"模块(边界|清晰)",
    r"可(拆|并行)",
    r"前端.{0,6}后端",
    r"切片",
    r"分工",
    r"\bimplement\b",
    r"\bsplit\b",
    r"\bbuild\b",
]

# 评审信号。
REVIEW_PATTERNS = [
    r"评审",
    r"审查",
    r"审阅",
    r"复核",
    r"挑刺",
    r"批判",
    r"安全审查",
    r"回归",
    r"代码已(写完|存在)",
    r"\breview\b",
    r"\baudit\b",
    r"\bpr\b.{0,10}\breview\b",
]

# 外部执行信号。
EXTERNAL_PATTERNS = [
    r"隔离.{0,6}(分支|git)",
    r"worktree",
    r"长时间运行",
    r"长时",
    r"多终端",
    r"tmux",
    r"后台跑",
    r"锁定.{0,6}(机器|环境)",
    r"\bisolat\w*\b",
    r"\blong[- ]running\b",
]

# 单通道推理信号：主线程与子 agent 共用同一网关/模型池，准入失败
# （queue_full / 429 / circuit_open）概率升高。检测只用于触发 lane 死亡
# 协议与切片最小化；效率优先，绝不输出并发数量限制。
SINGLE_CHANNEL_PATTERNS = [
    r"单网关",
    r"单一网关",
    r"单模型",
    r"单一模型",
    r"共享(模型)?池",
    r"单池",
    r"通道受限",
    r"受限通道",
    r"推理通道受限",
    r"\bsingle[- ]gateway\b",
    r"\bsingle[- ]model\b",
    r"\bshared (model )?pool\b",
    r"\blimited gateway\b",
]


def _hits(text: str, patterns: list[str]) -> int:
    return sum(1 for pat in patterns if re.search(pat, text, re.IGNORECASE))


def _suggest_lanes(text: str) -> list[str]:
    """从文本里挑 2-4 个可并行 surface 作为 lane 草案。"""
    matched: list[str] = []
    pairs = [
        ("API / 接口", r"(API|接口)"),
        ("前端", r"前端|frontend|UI"),
        ("后端 / 数据", r"后端|backend|数据(模型|库)"),
        ("测试", r"测试|test"),
        ("认证 / 安全", r"认证|auth|安全|secret"),
        ("计费 / 支付", r"计费|billing|支付|payment"),
        ("文档", r"文档|docs?\b"),
    ]
    for label, pat in pairs:
        if re.search(pat, text, re.IGNORECASE):
            matched.append(label)
        if len(matched) >= 4:
            break
    return matched


def _risks(text: str) -> list[str]:
    """命中的信任边界/破坏性信号；非空时必须加安全评审 lane。"""
    labels = [
        ("认证 / 信任边界", r"认证|auth|login|session"),
        ("计费 / 支付", r"计费|billing|支付|payment"),
        ("密钥 / 令牌", r"密钥|secret|token|credential"),
        ("数据迁移", r"迁移|migration|schema"),
        ("破坏性操作", r"删(库|除)|破坏性|destructive|drop\b"),
    ]
    return [label for label, pat in labels if re.search(pat, text, re.IGNORECASE)]


def _score_map(text: str) -> dict[str, int]:
    """启发式分值表；供 decide() 与覆盖重算共用同一打分。"""
    cleaned = (text or "").strip()
    scores = {
        "local": _hits(cleaned, LOCAL_PATTERNS),
        "explore": _hits(cleaned, EXPLORE_PATTERNS),
        "build": _hits(cleaned, BUILD_PATTERNS),
        "review": _hits(cleaned, REVIEW_PATTERNS),
        "external": _hits(cleaned, EXTERNAL_PATTERNS),
    }
    if "并行" in cleaned or "多代理" in cleaned or "并发" in cleaned:
        scores["explore"] += 1
        scores["build"] += 1
    return scores


def infer_channel_profile(text: str) -> dict[str, Any]:
    """推断 advisory 通道画像；只携带共享池事实，绝不携带数量限制。

    单池通道上准入失败（queue_full/429/circuit_open）可重试；效率优先，
    fan-out 保持并行，失败由 lane 死亡协议（salvage→至多 1 次
    re-dispatch→主线程吸收）兜底。此字段是协议触发器，不是配额：
    check_spec_package.py 不消费它。
    """
    cleaned = text or ""
    hit = next(
        (pat for pat in SINGLE_CHANNEL_PATTERNS if re.search(pat, cleaned, re.IGNORECASE)),
        None,
    )
    if hit is not None:
        note = (
            f"single-channel signal: /{hit}/; keep parallel dispatch, "
            "apply lane-death protocol + slice minimization "
            "(see orchestration.md Constrained-channel dispatch)"
        )
        return {"shared_pool": True, "note": note}
    return {
        "shared_pool": False,
        "note": "no single-channel signal; default parallel dispatch",
    }


def _route_dependent_fields(route: str, text: str, scores: dict[str, int], risks: list[str]) -> dict[str, Any]:
    """按最终 route 重算 lanes/reason；score 取该 route 的启发式分。

    覆盖（--route 或包内合法 5.4 route）之后，字段必须与有效 route 自洽：
    local 清空实现 lanes，review/build/explore 按文本重建 lanes；风险
    信号非空时独立保留安全评审 lane。
    """
    lanes: list[str] = []
    if route in ("explore", "build", "review"):
        lanes = _suggest_lanes(text)
    if risks and "安全评审" not in lanes:
        lanes = lanes + ["安全评审"]
    reason = f"scores={scores}; route={route}"
    if route == "external":
        reason += "; escalated: isolation/long-running signal"
    return {"route": route, "score": scores.get(route, 0), "reason": reason, "lanes": lanes}


def decide(text: str) -> dict[str, Any]:
    """对任务文本做路由判定。永不抛错，永远返回结构化结果。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return {
            "route": "local",
            "score": 0,
            "reason": "空文本；默认主线程直接做",
            "lanes": [],
            "risks": [],
            "channel_profile": infer_channel_profile(""),
        }

    scores = _score_map(cleaned)

    route = max(scores, key=lambda key: (scores[key], ROUTES.index(key)))
    # 全零信号按字数粗分：小文本 local，大文本 explore（理解先行）。
    if sum(scores.values()) == 0:
        route = "local" if len(cleaned) < 40 else "explore"
    # external 是升级条件而不是并列竞争：命中隔离/长运行信号时直接升级。
    if scores["external"] > 0 and route not in ("external", "local"):
        route = "external"

    lanes: list[str] = []
    if route in ("explore", "build", "review"):
        lanes = _suggest_lanes(cleaned)
    risks = _risks(cleaned)
    # 风险信号非空即要求独立安全评审 lane；业务面 lane（认证/安全）不能替代。
    if risks and "安全评审" not in lanes:
        lanes = lanes + ["安全评审"]

    reason = f"scores={scores}; route={route}" + (
        "; escalated: isolation/long-running signal" if route == "external" else ""
    )
    return {
        "route": route,
        "score": scores[route],
        "reason": reason,
        "lanes": lanes,
        "risks": risks,
        "channel_profile": infer_channel_profile(cleaned),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Route decision for spec orchestration: task text -> one of " + " / ".join(ROUTES)),
        epilog="Output is a single JSON object: route, score, reason, lanes (draft lane suggestions).",
    )
    parser.add_argument("--text", required=True, help="Task text / package goal to route.")
    parser.add_argument(
        "--route",
        help="Explicit route override; must be one of: " + " / ".join(ROUTES),
    )
    parser.add_argument(
        "--root",
        help="Project root containing the task package (consumed with --slug to add package context).",
    )
    parser.add_argument(
        "--slug",
        help="Task package slug; with --root, the open tasks and any valid 5.4 route join the decision.",
    )
    return parser.parse_args(argv)


def _package_context(root: Path, slug: str) -> tuple[str, str | None]:
    """读取任务包：拼接目标 + 未完成任务/boundary 文本，并提取合法 5.4 route。

    只消费未完成任务标题与 boundary，避免已完成任务的噪音；5.4 route
    非法或缺席时不作为覆盖（门禁会在 run 前拦截非法策略）。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from spec_package_support import (  # noqa: E402
        TOP_LEVEL_BULLET_FIELD,
        _orchestration_top_level_lines,
        extract_title_from_content,
        parse_task_records,
        read_regular_text,
        resolve_specs_child,
        resolve_specs_root,
    )

    specs_root = resolve_specs_root(root, None)
    package_dir = resolve_specs_child(specs_root, "specs", slug)
    spec_content = read_regular_text(package_dir / "spec.md")
    tasks_content = read_regular_text(package_dir / "tasks.md")

    goal = f"{slug}"
    title = extract_title_from_content(spec_content, slug)
    if title:
        goal = title

    open_fragments: list[str] = []
    try:
        records = parse_task_records(tasks_content)
    except Exception:  # noqa: BLE001 - context is advisory; routing must never crash
        records = []
    for record in records:
        if record.checked:
            continue
        open_fragments.append(record.title)
        if record.boundary.strip():
            open_fragments.append(record.boundary)
        if record.verify.strip():
            open_fragments.append(record.verify)

    explicit_route: str | None = None
    route_bullets = [
        line
        for line in _orchestration_top_level_lines(spec_content)
        if (match := TOP_LEVEL_BULLET_FIELD.match(line)) is not None and match.group(1).strip() == "route"
    ]
    if len(route_bullets) == 1:
        token = TOP_LEVEL_BULLET_FIELD.match(route_bullets[0]).group(2).strip().strip("`").strip()
        if token in ROUTES:
            explicit_route = token

    context_text = goal if not open_fragments else goal + "：" + "；".join(open_fragments)
    return context_text, explicit_route


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.route is not None and args.route not in ROUTES:
        print(
            f"route_decision: invalid --route {args.route!r}; expected one of: " + " / ".join(ROUTES),
            file=sys.stderr,
        )
        return 2
    text = args.text
    package_route = None
    if args.root and args.slug:
        try:
            context_text, package_route = _package_context(Path(args.root), args.slug)
            text = f"{args.text}；{context_text}" if args.text else context_text
        except Exception as exc:  # noqa: BLE001 - missing/invalid package degrades to text-only
            print(f"route_decision: package context unavailable ({exc}); falling back to --text", file=sys.stderr)
    else:
        package_route = None

    override_route = args.route or package_route
    heuristic = decide(text)
    risks = heuristic["risks"]
    channel_profile = infer_channel_profile(text)
    scores = _score_map(text)
    if override_route is not None and override_route in ROUTES:
        result = {
            **_route_dependent_fields(override_route, text, scores, risks),
            "risks": risks,
            "channel_profile": channel_profile,
            "override": True,
        }
        if package_route and not args.route:
            result["source"] = "package-5.4"
    else:
        result = {**heuristic, "channel_profile": channel_profile, "override": False}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

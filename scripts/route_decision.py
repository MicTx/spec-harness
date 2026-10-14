#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""spec 编排路由决策：单一入口，按包结构判定五路由 + lane 建议。

F12（AUD-11）收敛后的路由语义：
- 路由输入是包结构（未完成任务数、boundary 覆盖模块数、依赖图宽度、风险标签），
  不是目标文本——同一任务的长短描述得到相同路由。
- 零结构信号一律 local；仅强结构信号给非 local（阈值是固定常量，可测试）。
- `--text` 降级为只读诊断（人工对照用），不作为路由输入。
- 历史 `### 5.4 编排策略` 的 route 解析兼容保留：合法 5.4 route 仍是显式覆盖。

包结构判定为确定性规则（可解释、可测试），不做魔法：
- local: 小而局部、无跨模块并行收益（零强结构信号）
- review: boundary/verify 命中信任边界/破坏性风险标签（认证/计费/密钥/迁移/破坏性）
- explore: 多任务宽边界，或依赖图纠缠（单任务依赖过宽，理解先行）
- build: 中等规模、可切分（多任务多模块且依赖宽度受控）
- external: 升级条件；结构路由不自动给出，由显式 5.4 route 或 --route 覆盖到达

`/spec:new` 定范围后、`/spec:run` 执行前，任何会话都可以运行

    python3 scripts/route_decision.py --root <project> --slug <slug>

拿到结构化 JSON（route / basis / structure / score / reason / lanes / risks），
代替凭记忆写 5.4。文本面保留只读诊断入口

    python3 scripts/route_decision.py --text "<goal>"   # 对照用，不是路由输入

契约详见 references/orchestration.md；lane 建议是草案（boundary 模块即所有权面），
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


# ---------------------------------------------------------------------------
# 包结构路由（F12/AUD-11：单一入口按包结构决策；文本只做只读诊断）
# ---------------------------------------------------------------------------

# 固定阈值（无使用证据不做配置化；调整须随测试与试点实录一起评审）：
# 多任务宽边界或依赖纠缠给 explore；中等规模可切分给 build；风险标签给 review；
# 其余一律 local。仅强结构信号给非 local。
EXPLORE_MIN_TASKS = 6
EXPLORE_MIN_MODULES = 4
EXPLORE_TANGLE_DEP_WIDTH = 3
BUILD_MIN_TASKS = 4
BUILD_MIN_MODULES = 3
BUILD_MAX_DEP_WIDTH = 2
LANE_DRAFT_CAP = 4

# boundary 里提及但不计入代码面的簿记树（包文档/链状态/loop 运行时状态）。
_NON_CODE_MODULE_ROOTS = frozenset({".spec", ".agents"})

_PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+")


def _boundary_modules(boundary: str) -> set[str]:
    """从 boundary 提取覆盖模块：路径 token 归一到首段（slots 归到 slot 目录）。

    `.spec/`、`.agents/` 是包簿记/运行时状态树，不是代码面，不计数；
    裸文件名（无路径分隔）推不出模块归属，跳过。
    """
    modules: set[str] = set()
    for token in _PATH_TOKEN_RE.findall(boundary or ""):
        parts = [part for part in token.split("/") if part]
        if not parts or parts[0] in _NON_CODE_MODULE_ROOTS:
            continue
        if parts[0].startswith("-"):
            # 以 - 开头的是命令行旗标（--slug/--root 等），不是路径/模块。
            continue
        if parts[0] == "slots" and len(parts) >= 2:
            modules.add("/".join(parts[:2]))
        else:
            modules.add(parts[0])
    return modules


def collect_package_structure(root: Path, slug: str) -> dict[str, Any]:
    """读任务包结构：未完成任务数、boundary 覆盖模块、依赖宽度、风险标签、5.4 route。

    只统计未完成任务（剩余工作形状）；tasks 解析失败按空任务集处理（路由永不
    抛错），但包文档缺失/不可读会向外抛——路由输入必须可解释，不在这褢
    兜底猜文本。boundary_text 仅内部供 channel_profile 推断，不随 JSON 输出。
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
        task_dependencies,
    )

    specs_root = resolve_specs_root(root, None)
    package_dir = resolve_specs_child(specs_root, "specs", slug)
    spec_content = read_regular_text(package_dir / "spec.md")
    tasks_content = read_regular_text(package_dir / "tasks.md")

    title = extract_title_from_content(spec_content, slug) or slug

    try:
        records = parse_task_records(tasks_content)
    except Exception:  # noqa: BLE001 - structure is derived data; routing must never crash
        records = []
    open_records = [record for record in records if not record.checked]

    modules: set[str] = set()
    for record in open_records:
        modules.update(_boundary_modules(record.boundary))
    dep_width = max((len(task_dependencies(record)) for record in open_records), default=0)

    boundary_text = "；".join(
        f"{record.boundary} {record.verify}"
        for record in open_records
        if record.boundary.strip() or record.verify.strip()
    )
    risk_labels = _risks(boundary_text)

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

    return {
        "title": title,
        "task_count": len(open_records),
        "boundary_modules": sorted(modules),
        "module_count": len(modules),
        "dep_width": dep_width,
        "risk_labels": risk_labels,
        "boundary_text": boundary_text,
        "explicit_route": explicit_route,
    }


def decide_by_structure(structure: dict[str, Any]) -> dict[str, Any]:
    """按包结构做路由判定。永不抛错，永远返回结构化结果；依据=包结构。"""
    task_count = int(structure.get("task_count") or 0)
    modules = list(structure.get("boundary_modules") or [])
    module_count = len(modules)
    dep_width = int(structure.get("dep_width") or 0)
    risks = list(structure.get("risk_labels") or [])

    signals: list[str] = []
    if risks:
        route = "review"
        signals.append(f"风险标签 {risks}")
    elif task_count >= EXPLORE_MIN_TASKS and module_count >= EXPLORE_MIN_MODULES:
        route = "explore"
        signals.append(f"任务数 {task_count}≥{EXPLORE_MIN_TASKS} × 模块数 {module_count}≥{EXPLORE_MIN_MODULES}")
    elif dep_width >= EXPLORE_TANGLE_DEP_WIDTH:
        route = "explore"
        signals.append(f"依赖宽度 {dep_width}≥{EXPLORE_TANGLE_DEP_WIDTH}（纠缠，理解先行）")
    elif task_count >= BUILD_MIN_TASKS and module_count >= BUILD_MIN_MODULES and dep_width <= BUILD_MAX_DEP_WIDTH:
        route = "build"
        signals.append(
            f"任务数 {task_count}≥{BUILD_MIN_TASKS} × 模块数 {module_count}≥{BUILD_MIN_MODULES}"
            f" × 依赖宽度 {dep_width}≤{BUILD_MAX_DEP_WIDTH}"
        )
    else:
        route = "local"

    # score = 强结构信号命中数（可解释：每项都是独立可复核的阈值判定）。
    score = sum(
        (
            bool(risks),
            task_count >= EXPLORE_MIN_TASKS,
            module_count >= EXPLORE_MIN_MODULES,
            dep_width >= EXPLORE_TANGLE_DEP_WIDTH,
            task_count >= BUILD_MIN_TASKS,
            module_count >= BUILD_MIN_MODULES,
        )
    )

    # lane 草案 = boundary 模块（互不相交的所有权面，确定性所有权检查的基砆）。
    lanes: list[str] = []
    if route in ("explore", "build", "review"):
        lanes = modules[:LANE_DRAFT_CAP]
    # 风险信号非空即要求独立安全评审 lane；业务面 lane 不能替代。
    if risks and "安全评审" not in lanes:
        lanes = lanes + ["安全评审"]

    reason = "依据=包结构：" + ("；".join(signals) if signals else "无强结构信号，一律 local")
    return {
        "route": route,
        "basis": "package-structure",
        "score": score,
        "reason": reason,
        "lanes": lanes,
        "risks": risks,
        "structure": {
            "task_count": task_count,
            "boundary_modules": modules,
            "module_count": module_count,
            "dep_width": dep_width,
            "risk_labels": risks,
        },
    }


def _apply_structure_override(result: dict[str, Any], route: str) -> dict[str, Any]:
    """显式覆盖（--route 或合法 5.4 route）后重算 route 相关字段，保持自洽。"""
    lanes: list[str] = []
    if route in ("explore", "build", "review"):
        lanes = list(result["structure"]["boundary_modules"])[:LANE_DRAFT_CAP]
    if result["risks"] and "安全评审" not in lanes:
        lanes = lanes + ["安全评审"]
    reason = result["reason"] + f"；显式覆盖 route={route}"
    if route == "external":
        reason += "（升级条件：隔离/长时运行由显式声明到达，结构路由不自动给出）"
    return {**result, "route": route, "lanes": lanes, "override": True, "reason": reason}


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
    # 零信号一律 local（F12/AUD-11）：不再按字数分流——同一修复的长短
    # 描述必须得到相同路由，文本长度不是路由信号；零分时上面的索引平局
    # 决胜会漂到 external，这里显式归 local。
    if scores[route] == 0:
        route = "local"
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
        description=(
            "Route decision for spec orchestration: package structure -> one of "
            + " / ".join(ROUTES)
            + " (single entry); --text is a read-only diagnostic, not a routing input"
        ),
        epilog=(
            "Output is a single JSON object: route, basis, structure (package mode), "
            "score, reason, lanes (draft ownership surfaces)."
        ),
    )
    parser.add_argument(
        "--text",
        help="Read-only diagnostic: text heuristic for side-by-side comparison (NOT the routing input).",
    )
    parser.add_argument(
        "--route",
        help="Explicit route override; must be one of: " + " / ".join(ROUTES),
    )
    parser.add_argument(
        "--root",
        help=(
            "Project root containing the task package; with --slug this is the single "
            "routing entry, decided from package structure."
        ),
    )
    parser.add_argument(
        "--slug",
        help=(
            "Task package slug; with --root the decision reads open-task count, boundary "
            "module coverage, dependency width, and risk labels."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.route is not None and args.route not in ROUTES:
        print(
            f"route_decision: invalid --route {args.route!r}; expected one of: " + " / ".join(ROUTES),
            file=sys.stderr,
        )
        return 2
    if bool(args.root) != bool(args.slug):
        print("route_decision: --root and --slug must be given together", file=sys.stderr)
        return 2
    if not args.root and args.text is None:
        print(
            "route_decision: nothing to route — pass --root + --slug for package-structure "
            "routing (the single entry) or --text for a read-only diagnostic",
            file=sys.stderr,
        )
        return 2

    if args.root:
        # 包结构路由：唯一路由入口。包不可读时 fail closed——路由结果必须可由
        # 包结构解释，不许退回猜文本（那正是 AUD-11 要消除的非确定语义）。
        try:
            structure = collect_package_structure(Path(args.root), args.slug)
        except Exception as exc:  # noqa: BLE001 - never guess from text
            print(
                f"route_decision: package structure unavailable ({exc}); refusing to route from text",
                file=sys.stderr,
            )
            return 1
        result = decide_by_structure(structure)
        result["channel_profile"] = infer_channel_profile(structure["boundary_text"])
        package_route = structure["explicit_route"]
        override_route = args.route or package_route
        if override_route is not None:
            result = _apply_structure_override(result, override_route)
            if package_route and not args.route:
                result["source"] = "package-5.4"
        else:
            result["override"] = False
    else:
        # 只读诊断面：文本启发式对照，不作为路由输入。
        text = (args.text or "").strip()
        heuristic = decide(text)
        risks = heuristic["risks"]
        channel_profile = infer_channel_profile(text)
        if args.route is not None:
            scores = _score_map(text)
            result = {
                **_route_dependent_fields(args.route, text, scores, risks),
                "basis": "text-diagnostic",
                "risks": risks,
                "channel_profile": channel_profile,
                "override": True,
            }
        else:
            result = {**heuristic, "basis": "text-diagnostic", "override": False}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

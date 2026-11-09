#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""spec 编排路由决策：按包结构判定五路由之一（AUD-11 单一入口）。

路由输入是包结构（未完成任务数、boundary 模块、依赖宽度、风险标签），不是目标
文本：零信号 local；风险标签 review；宽边界或依赖纠缠 explore；中等可切分
build；external 只由显式 5.4 route / --route 覆盖到达。委派前运行：

    python3 scripts/route_decision.py --root <project> --slug <slug>

输出单个 JSON 对象 {"route", "reason", "risks"}；契约见 references/orchestration.md。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import spec_package_support as _sps  # noqa: E402

ROUTES = _sps.ORCHESTRATION_ROUTES

_RISK_LABELS = (
    ("认证 / 信任边界", r"认证|auth|login|session"),
    ("计费 / 支付", r"计费|billing|支付|payment"),
    ("密钥 / 令牌", r"密钥|secret|token|credential"),
    ("数据迁移", r"迁移|migration|schema"),
    ("破坏性操作", r"删(库|除)|破坏性|destructive|drop\b"),
)

# 固定阈值（无使用证据不做配置化；调整须随测试与试点实录一起评审）。
EXPLORE_MIN_TASKS = 6
EXPLORE_MIN_MODULES = 4
EXPLORE_TANGLE_DEP_WIDTH = 3
BUILD_MIN_TASKS = 4
BUILD_MIN_MODULES = 3
BUILD_MAX_DEP_WIDTH = 2

# boundary 里提及但不计入代码面的簿记树（包文档/链状态/loop 运行时状态）。
_NON_CODE_MODULE_ROOTS = frozenset({".spec", ".agents"})
_PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+")


def _risks(text: str) -> list[str]:
    """命中的信任边界/破坏性信号；非空时必须加安全评审 lane。"""
    return [label for label, pat in _RISK_LABELS if re.search(pat, text, re.IGNORECASE)]


def _explicit_route_from_spec(spec_content: str) -> str | None:
    """Top-level `- route: <token>` in the 5.4 section; exactly one else None."""
    lines = _sps.fold_section_key_anchors(spec_content).splitlines()
    in_section = False
    route_tokens: list[str] = []
    for line in lines:
        if _sps.SECTION_ORCHESTRATION.matches_prefix(line):
            in_section = True
            continue
        if in_section and re.match(r"^#{1,3} ", line):
            break
        if not in_section or line[:1] in (" ", "\t"):
            continue
        match = _sps.TOP_LEVEL_BULLET_FIELD.match(line)
        if match and match.group(1).strip() == "route":
            route_tokens.append(match.group(2).strip())
    if len(route_tokens) != 1:
        return None
    token = route_tokens[0].strip("`").strip()
    return token if token in ROUTES else None


def _boundary_modules(boundary: str) -> set[str]:
    """boundary 路径 token 归一到首段（slots 归到 slot 目录）；.spec/.agents 与裸文件名不计。"""
    modules: set[str] = set()
    for token in _PATH_TOKEN_RE.findall(boundary or ""):
        parts = [part for part in token.split("/") if part]
        if not parts or parts[0] in _NON_CODE_MODULE_ROOTS or parts[0].startswith("-"):
            continue
        modules.add("/".join(parts[:2]) if parts[0] == "slots" and len(parts) >= 2 else parts[0])
    return modules


def collect_package_structure(root: Path, slug: str) -> dict:
    """读未完成任务结构：任务数、boundary 模块、依赖宽度、风险标签、5.4 route。

    tasks 解析失败按空任务集（路由不抛错）；包文档缺失/不可读向外抛，
    不在这里兜底猜文本。
    """
    specs_root = _sps.resolve_specs_root(root, None)
    package_dir = _sps.resolve_specs_child(specs_root, "specs", slug)
    spec_content = _sps.read_regular_text(package_dir / "spec.md")
    tasks_content = _sps.read_regular_text(package_dir / "tasks.md")

    try:
        records = _sps.parse_task_records(tasks_content)
    except Exception:  # noqa: BLE001 - structure is derived data; routing must never crash
        records = []
    open_records = [record for record in records if not record.checked]

    modules: set[str] = set().union(*(_boundary_modules(record.boundary) for record in open_records))
    boundary_text = "；".join(
        f"{record.boundary} {record.verify}"
        for record in open_records
        if record.boundary.strip() or record.verify.strip()
    )
    return {
        "title": _sps.extract_title_from_content(spec_content, slug) or slug,
        "task_count": len(open_records),
        "boundary_modules": sorted(modules),
        "module_count": len(modules),
        "dep_width": max((len(_sps.task_dependencies(record)) for record in open_records), default=0),
        "risk_labels": _risks(boundary_text),
        "explicit_route": _explicit_route_from_spec(spec_content),
    }


def _structural_route(structure: dict) -> tuple[str, str]:
    """(route, 依据)：risk→review，宽边界/依赖纠缠→explore，可切分→build，否则 local。"""
    tasks = int(structure.get("task_count") or 0)
    modules = int(structure.get("module_count") or 0)
    dep_width = int(structure.get("dep_width") or 0)
    risks = list(structure.get("risk_labels") or [])
    if risks:
        return "review", f"风险标签 {risks}"
    if tasks >= EXPLORE_MIN_TASKS and modules >= EXPLORE_MIN_MODULES:
        return "explore", f"任务数 {tasks}≥{EXPLORE_MIN_TASKS} × 模块数 {modules}≥{EXPLORE_MIN_MODULES}"
    if dep_width >= EXPLORE_TANGLE_DEP_WIDTH:
        return "explore", f"依赖宽度 {dep_width}≥{EXPLORE_TANGLE_DEP_WIDTH}（纠缠，理解先行）"
    if tasks >= BUILD_MIN_TASKS and modules >= BUILD_MIN_MODULES and dep_width <= BUILD_MAX_DEP_WIDTH:
        return (
            "build",
            f"任务数 {tasks}≥{BUILD_MIN_TASKS} × 模块数 {modules}≥{BUILD_MIN_MODULES}"
            f" × 依赖宽度 {dep_width}≤{BUILD_MAX_DEP_WIDTH}",
        )
    return "local", "无强结构信号，一律 local"


def decide_by_structure(structure: dict) -> str:
    """按包结构做路由判定：risk→review，宽边界/依赖纠缠→explore，可切分→build，否则 local。"""
    return _structural_route(structure)[0]


def _reason(structure: dict, route: str, override: bool) -> str:
    reason = "依据=包结构：" + _structural_route(structure)[1]
    if override:
        reason += f"；显式覆盖 route={route}"
        if route == "external":
            reason += "（升级条件：隔离/长时运行由显式声明到达，结构路由不自动给出）"
    return reason


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package-structure route decision -> " + " / ".join(ROUTES),
        epilog='Output: {"route", "reason", "risks"}',
    )
    parser.add_argument("--route", help="Explicit route override; must be one of: " + " / ".join(ROUTES))
    parser.add_argument("--root", help="Project root containing the task package")
    parser.add_argument("--slug", help="Task package slug; decision reads open tasks, boundary, deps, risks")
    return parser.parse_args(argv)


def _err(message: str, code: int) -> int:
    print(f"route_decision: {message}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.route is not None and args.route not in ROUTES:
        return _err(f"invalid --route {args.route!r}; expected one of: " + " / ".join(ROUTES), 2)
    if not (args.root and args.slug):
        return _err("pass --root and --slug", 2)
    # 包不可读时 fail closed——路由结果必须可由包结构解释，不许退回猜文本
    # （那正是 AUD-11 要消除的非确定语义）。
    try:
        structure = collect_package_structure(Path(args.root), args.slug)
    except Exception as exc:  # noqa: BLE001 - never guess from text
        return _err(f"package structure unavailable ({exc}); refusing to route from text", 1)
    override = args.route or structure["explicit_route"]
    route = override if override is not None else decide_by_structure(structure)
    payload = {
        "route": route,
        "reason": _reason(structure, route, override is not None),
        "risks": structure["risk_labels"],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Route a Spec package to the next stage (new/run/status/check/done)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from check_spec_package import compute_gate_results
from dashboard_support import (
    HEALTH_BLOCKED,
    HEALTH_OK,
    HEALTH_RISK,
    Dashboard,
    default_pipeline,
    project_gap_message,
    task_strip_from_records,
    worst_health,
)
from handoff_support import freshness, parse_handoff, read_handoff, validate_handoff
from report_spec_package import parse_tasks
from spec_package_support import (
    SpecControlError,
    add_specs_dir_arg,
    extract_title_from_content,
    falsified_path_entries,
    parse_task_records,
    read_regular_text,
    ready_task_ids,
    resolve_specs_child,
    resolve_specs_root,
    select_active_package,
    task_dependencies,
    validate_branch_bound_package,
    validate_slug,
)
from spec_package_support import (
    active_package_slugs as active_packages,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", help="Specific task package slug to inspect")
    parser.add_argument("--root", default=".", help="Project root containing .spec (default: current directory)")
    add_specs_dir_arg(parser)
    parser.add_argument("--ascii", action="store_true", help="Render dashboard glyphs as ASCII fallback")
    parser.add_argument("--compact", action="store_true", help="Render metrics as a single line")
    parser.add_argument("--format", choices=("markdown", "json"), default=None)
    parser.add_argument("--brief", action="store_true", help="Compact JSON read: stage, next task, blockers")
    return parser.parse_args()


def _read_package(package_dir: Path) -> tuple[str, str, str | None]:
    """(spec, tasks, checklist); checklist.md is optional since v2 (F11)."""
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise ValueError(f"task package not found: {package_dir}")
    checklist_path = package_dir / "checklist.md"
    spec = read_regular_text(package_dir / "spec.md")
    tasks = read_regular_text(package_dir / "tasks.md")
    return spec, tasks, read_regular_text(checklist_path) if checklist_path.exists() else None


def _task_records(r, tasks_content: str) -> tuple[list, set[str], str | None, str | None]:
    """(records, ready, inquiry_task, current_task); contract errors keep an honest empty slice."""
    try:
        records = parse_task_records(tasks_content)
        ready = ready_task_ids(records)
        inquiry = next((t.title for t in records if t.id in ready & set(r.autonomous_task_ids)), None)
        current = inquiry or next((t.title for t in records if t.id in ready), None)
    except SpecControlError:
        records, ready, inquiry, current = [], set(), None, None
    return records, ready, inquiry, current


def _decide(root: Path, slug: str, package_dir: Path, spec: str, tasks_content: str, checklist: str | None) -> dict:
    """The one source of truth for alerts, stage, health, next action and projections."""
    r = compute_gate_results(spec, tasks_content, checklist, slug=slug, root=root, package_dir=package_dir)
    _, tasks = parse_tasks(tasks_content)
    checkpoint_pending = (package_dir / "update-checkpoint.json").exists()

    blocked_texts = [task.text for task in tasks if task.is_blocked]
    alerts = [f"任务受阻：{text}" for text in blocked_texts]
    if checkpoint_pending:
        alerts.insert(0, "遗留 update-checkpoint.json（机制已退役）：用 git 确认无需恢复后删除该文件")
    if r.blocked_tasks and not blocked_texts:
        alerts.append("项目中存在受阻任务，具体原因已记录在任务清单")
    for prefix, items in (
        ("需要确认需求", r.human_questions),
        ("自主调查", r.autonomous_questions),
        ("调查契约尚未满足", r.question_errors),
        ("项目定义不完整", [project_gap_message(g) for g in r.spec_clarification_gaps]),
        ("任务依赖尚未满足", r.task_contract_errors),
        ("验证范围尚未满足", r.verification_scope_errors),
    ):
        alerts.extend(f"{prefix}：{item}" for item in items)

    scoping = bool(r.human_questions or r.question_errors or r.spec_clarification_gaps)
    if r.blocked_tasks or scoping:
        stage, pipe = "status", ("new" if scoping else "run")
        health = HEALTH_BLOCKED if r.blocked_tasks else HEALTH_RISK
    elif r.all_tasks_done:
        if r.overall_ok:
            stage, health, pipe = "done", HEALTH_OK, "done"
        else:
            stage, health, pipe = "check", HEALTH_RISK, "check"
            if r.missing_boundary or r.missing_verify:
                alerts.append(
                    f"任务执行说明不完整：缺影响范围 {r.missing_boundary} 项，缺验证方式 {r.missing_verify} 项"
                )
            if not r.success_criteria_ok:
                alerts.append(f"项目尚有 {r.success_criteria_total - r.success_criteria_checked} 项成功标准未达成")
            if not alerts:
                alerts.append("项目尚未满足最终验收要求")
    else:
        stage, health, pipe = "run", HEALTH_OK, "run"
    if checkpoint_pending:
        health = worst_health(health, HEALTH_RISK)

    records, ready, inquiry_task, current_task = _task_records(r, tasks_content)
    return {
        "slug": slug,
        "title": extract_title_from_content(spec, slug),
        "r": r,
        "tasks": tasks,
        "records": records,
        "ready": ready,
        "alerts": alerts,
        "stage": stage,
        "health": health,
        "pipe": pipe,
        "next_action": _next_action(r, tasks, inquiry_task, current_task),
        "current_task": current_task,
    }


def _next_action(r, tasks, inquiry_task: str | None, current_task: str | None) -> str:
    if r.blocked_tasks:
        return next((f"解除阻塞：{task.text}" for task in tasks if task.is_blocked), "处理任务阻塞")
    for errors, template in (
        (r.question_errors, "修复调查契约：{}"),
        (r.human_questions, "确认需求：{}"),
        (r.spec_clarification_gaps, "补齐项目定义：{}"),
        (r.task_contract_errors, "修正任务依赖：{}"),
    ):
        if errors:
            item = errors[0] if "项目定义" not in template else project_gap_message(errors[0])
            return template.format(item)
    if r.all_tasks_done:
        return "整理交付结果并完成归档" if r.overall_ok else "修复验收未通过项"
    return (f"自主调查：{inquiry_task}" if inquiry_task else current_task) or "继续实现项目任务"


def _brief_task(record, ready: set[str]) -> dict:
    if record.checked:
        state = "done"
    elif record.blocked:
        state = "blocked"
    elif record.block_text.startswith("- [>"):
        state = "manual-in-progress"
    else:
        state = "ready" if record.id in ready else "pending"
    return {
        "id": record.id,
        "title": record.title,
        "state": state,
        "boundary": record.boundary,
        "verify": record.verify,
        "depends_on": list(task_dependencies(record)),
    }


def _handoff_view(package_dir: Path, root: Path, tasks_content: str) -> dict:
    """One-line handoff conclusion, plus a structured fresh/stale verdict for --brief."""
    try:
        content = read_handoff(package_dir)
    except (OSError, UnicodeError, ValueError):
        return {
            "line": "handoff：不可读（超大/非 UTF-8/非常规文件），运行 spec_handoff.py validate 诊断",
            "fresh": "unreadable",
        }
    if content is None:
        return {"line": None, "fresh": "absent"}
    if validate_handoff(content):
        return {"line": "handoff：结构非法，运行 spec_handoff.py validate 诊断", "fresh": "unknown"}
    doc = parse_handoff(content)
    if doc.latest is None:
        return {"line": "handoff：无条目，运行 spec_handoff.py validate 诊断", "fresh": "unknown"}
    label, detail = freshness(doc, root, tasks_content)
    view = {"line": f"handoff：最新 {doc.latest.timestamp}（{label}，{doc.latest.collab}）", "fresh": label}
    if label != "fresh":
        view["stale_reason"] = detail
    return view


def route_for_package(
    root: Path,
    slug: str,
    specs_dir_name: str | None = None,
    *,
    ascii_mode: bool = False,
    compact: bool = False,
    output_format: str = "markdown",
    brief: bool = False,
) -> str:
    slug = validate_slug(slug)
    package_dir = resolve_specs_child(resolve_specs_root(root, specs_dir_name), "specs", slug)
    spec, tasks_content, checklist = _read_package(package_dir)
    v = _decide(root, slug, package_dir, spec, tasks_content, checklist)
    r, handoff = v["r"], _handoff_view(package_dir, root, tasks_content)

    if output_format == "json" or brief:
        payload = {
            "slug": v["slug"],
            "stage": v["stage"],
            "health": v["health"],
            "completed": r.completed_tasks,
            "total": r.total_tasks,
            "currentTask": v["current_task"],
            "dependencyErrors": r.task_contract_errors,
            "orchestrationErrors": r.orchestration_errors,
            "verificationScope": r.verification_scope,
            "humanQuestions": r.human_questions,
            "autonomousQuestions": r.autonomous_questions,
            "questionErrors": r.question_errors,
            "nextAction": v["next_action"],
        }
        if brief:
            payload.update(
                title=v["title"],
                falsifiedPaths=falsified_path_entries(spec),
                branch_binding=validate_branch_bound_package(root, slug, spec, allow_legacy_convention=True)[1],
                ready=sorted(v["ready"]),
                tasks=[_brief_task(t, v["ready"]) for t in v["records"]],
                alerts=v["alerts"],
                handoff=handoff,
            )
        elif handoff["line"]:
            payload["handoff"] = handoff["line"]
        return json.dumps(payload, ensure_ascii=False)

    strip, strip_labels = task_strip_from_records(v["tasks"])
    gate_gap_total = (
        len(r.spec_clarification_gaps)
        + r.missing_boundary
        + r.missing_verify
        + r.success_criteria_total
        - r.success_criteria_checked
    )
    return Dashboard(
        slug=slug,
        stage=v["stage"],
        title=v["title"],
        done=r.completed_tasks,
        total=r.total_tasks,
        health=v["health"],
        pipeline=default_pipeline(v["pipe"]),
        strip=strip,
        strip_labels=strip_labels,
        metrics={
            "阻塞": "1" if r.blocked_tasks else "0",
            "待确认": str(len(r.human_questions)),
            "门禁": "通过" if r.overall_ok else f"缺口 {gate_gap_total}",
            "证据": str(r.evidence_filled_count),
            "范围": r.verification_scope or "",
        },
        alerts=v["alerts"],
        next_step=v["next_action"],
        detail=[handoff["line"]] if handoff["line"] else [],
        detail_title="交接" if handoff["line"] else "交付信息",
        ascii_mode=ascii_mode,
        compact=compact,
    ).render()


def _aggregate(args: argparse.Namespace, stage: str, title: str, health: str, step: str, **extra) -> str:
    """Aggregate state (no package bound): the JSON payload or the same dashboard."""
    alerts, metrics = extra.pop("alerts", []), extra.pop("metrics", {})
    if args.brief or args.format == "json":
        payload = {"slug": None, "stage": stage, "health": health, "alerts": alerts, "nextStep": step, **extra}
        return json.dumps(payload, ensure_ascii=False)
    return Dashboard(
        slug="spec",
        stage=stage,
        title=title,
        health=health,
        alerts=alerts,
        metrics=metrics,
        next_step=step,
        ascii_mode=args.ascii,
        compact=args.compact,
    ).render()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    if args.brief and args.format == "markdown":
        print("error: --brief 隐含 JSON 输出，不能与 --format markdown 同时使用", file=sys.stderr)
        return 1
    args.format = args.format or ("json" if args.brief else "markdown")
    try:
        specs_root = resolve_specs_root(root, args.specs_dir)
        specs_dir = resolve_specs_child(specs_root, "specs")
        slug = args.slug.strip() if args.slug else None
        if slug is None:
            packages = active_packages(specs_dir)
            if not packages:
                print(
                    _aggregate(
                        args,
                        "new",
                        "当前没有进行中的项目",
                        HEALTH_OK,
                        "描述要完成的目标，skill 会直接建立任务并开始推进",
                    )
                )
                return 0
            selection = select_active_package(root, specs_root, packages)
            slug = selection.slug
            if not slug:
                warning = selection.warning or f"无法唯一选择活动任务包：{'、'.join(packages)}"
                print(
                    _aggregate(
                        args,
                        "status",
                        "任务包分支未绑定",
                        HEALTH_RISK,
                        "切换到任务包记录的 integration branch，或显式指定 --slug 只读查看",
                        alerts=[warning],
                        metrics={"门禁": "分支绑定"},
                        branch=selection.current_branch,
                        candidates=list(packages),
                    )
                )
                return 0
        options = {"ascii_mode": args.ascii, "compact": args.compact, "output_format": args.format, "brief": args.brief}
        print(route_for_package(root, slug, args.specs_dir, **options))
        return 0
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

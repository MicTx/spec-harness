#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Render the internal spec stage decision that is compatible with Claude Code and Codex-style skill invocation.
"""

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
)
from handoff_support import freshness, parse_handoff, read_handoff, validate_handoff
from report_spec_package import parse_tasks
from spec_package_support import (
    SpecControlError,
    active_package_slugs,
    add_specs_dir_arg,
    extract_title_from_content,
    parse_task_records,
    read_regular_text,
    ready_task_ids,
    resolve_specs_child,
    resolve_specs_root,
    select_active_package,
    validate_slug,
)
from update_checkpoint_support import CheckpointStatus, detect_update_checkpoint


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render an internal spec stage decision from current Spec package state."
    )
    parser.add_argument("--slug", help="Specific task package slug to inspect")
    parser.add_argument(
        "--root",
        default=".",
        help="Project root where the .spec directory exists (default: current directory)",
    )
    add_specs_dir_arg(parser)
    parser.add_argument("--ascii", action="store_true", help="Render dashboard glyphs as ASCII fallback")
    parser.add_argument("--compact", action="store_true", help="Render metrics as a single line")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser.parse_args()


# Shared enumeration contract; alias kept for existing importers.
active_packages = active_package_slugs


def checkpoint_alert(status: CheckpointStatus) -> str:
    """Explicit blocking line for an unresolved update checkpoint."""
    if status.state == "active" and status.checkpoint is not None:
        return f"存在未完成 update checkpoint：更新「{status.checkpoint.intent}」尚未确认完成或回滚"
    return "存在未完成 update checkpoint：检查点记录损坏，需人工恢复"


def _route_unreadable_checkpoint(slug: str, status: CheckpointStatus, output_format: str) -> str:
    """Render recovery without inventing progress from an unreadable triad."""
    alert = checkpoint_alert(status)
    next_step = (
        "处理未完成 update checkpoint：rollback 回滚更新，或 complete 确认保留"
        if status.state == "active"
        else "处理未完成 update checkpoint：检查点记录损坏，需人工恢复；恢复有效记录后再 rollback 或 complete"
    )
    if output_format == "json":
        return json.dumps(
            {
                "slug": slug,
                "stage": "status",
                "health": HEALTH_BLOCKED,
                "completed": None,
                "total": None,
                "currentTask": None,
                "dependencyErrors": [],
                "updateCheckpoint": status.state,
                "alerts": [alert, "任务记录缺失或不可读，任务进度未知"],
                "nextStep": next_step,
            },
            ensure_ascii=False,
        )
    # Recovery has no trustworthy task strip or counters. Keep the normal
    # project/attention/next-step headings without rendering a fictitious 0/0.
    # No glyphs or metric grid are needed, so this also serves --ascii/--compact.
    return (
        f"# {slug}\n\n"
        f"状态：{HEALTH_BLOCKED}（status）\n\n"
        "已完成：未知（任务记录缺失或不可读）\n\n"
        f"## 需要关注\n\n- {alert}\n\n"
        f"## 接下来\n\n{next_step}\n"
    )


def route_for_package(
    root: Path,
    slug: str,
    specs_dir_name: str | None = None,
    *,
    ascii_mode: bool = False,
    compact: bool = False,
    output_format: str = "markdown",
) -> str:
    slug = validate_slug(slug)
    specs_root = resolve_specs_root(root, specs_dir_name)
    package_dir = resolve_specs_child(specs_root, "specs", slug)
    if package_dir.is_symlink():
        raise ValueError(f"task package directory must not be a symlink: {package_dir}")
    if not package_dir.is_dir():
        raise ValueError(f"task package not found: {package_dir}")

    # Detect before reading the possibly half-applied triad. Path validation
    # stays outside the recovery fallback: a checkpoint never permits escape
    # from the trusted specs root, even through an individual triad member.
    checkpoint_status = detect_update_checkpoint(package_dir)
    checkpoint_pending = checkpoint_status.interrupted
    triad_paths = [
        resolve_specs_child(specs_root, "specs", slug, name) for name in ("spec.md", "tasks.md", "checklist.md")
    ]
    try:
        spec_content, tasks_content, checklist_content = [read_regular_text(path) for path in triad_paths]
    except (OSError, UnicodeError, ValueError):
        if checkpoint_pending:
            return _route_unreadable_checkpoint(slug, checkpoint_status, output_format)
        raise

    # Single computation: compute_gate_results replaces 8 separate function
    # calls that overall_check_passed would recompute internally.
    r = compute_gate_results(spec_content, tasks_content, checklist_content, slug=slug)
    _, tasks = parse_tasks(tasks_content)
    strip, strip_labels = task_strip_from_records(tasks)
    title = extract_title_from_content(spec_content, slug)

    blocked = r.blocked_tasks
    blocked_tasks = [task.text for task in tasks if task.is_blocked]
    all_done = r.all_tasks_done
    check_passed = r.overall_ok

    # An unresolved checkpoint remains a hard block even when the triad is
    # readable; route only detects it and never resolves it.

    alerts: list[str] = []
    if checkpoint_pending:
        alerts.append(checkpoint_alert(checkpoint_status))
    if blocked_tasks:
        alerts.extend(f"任务受阻：{task}" for task in blocked_tasks)
    elif blocked:
        alerts.append("项目中存在受阻任务，具体原因已记录在任务清单")
    alerts.extend(f"需要确认需求：{question}" for question in r.pending_questions)
    alerts.extend(f"项目定义不完整：{project_gap_message(gap)}" for gap in r.spec_clarification_gaps)
    # 编排策略结构错误在执行前拦截：非法 5.4 route 不得继续 stage=run。
    alerts.extend(f"编排策略尚未满足：{error}" for error in r.orchestration_errors)
    alerts.extend(f"验证范围尚未满足：{error}" for error in r.verification_scope_errors)

    orchestration_blocked = bool(r.orchestration_errors)
    if checkpoint_pending or blocked or r.pending_questions or r.spec_clarification_gaps or orchestration_blocked:
        next_stage = "status"
        health = HEALTH_BLOCKED if (blocked or checkpoint_pending) else HEALTH_RISK
        # Still scoping when clarification is missing; otherwise stalled mid-run.
        pipeline = default_pipeline(
            "new" if (r.pending_questions or r.spec_clarification_gaps or orchestration_blocked) else "run"
        )
    elif all_done and check_passed:
        next_stage = "done"
        health = HEALTH_OK
        pipeline = default_pipeline("done")
    elif all_done:
        next_stage = "check"
        health = HEALTH_RISK
        pipeline = default_pipeline("check")
        if r.missing_boundary or r.missing_verify:
            alerts.append(f"任务执行说明不完整：缺影响范围 {r.missing_boundary} 项，缺验证方式 {r.missing_verify} 项")
        if r.unchecked_items:
            alerts.append(f"项目尚有 {r.unchecked_items} 项验收未完成")
        if not check_passed and len(alerts) == 0:
            alerts.append("项目尚未满足最终验收要求")
    else:
        next_stage = "run"
        health = HEALTH_OK
        pipeline = default_pipeline("run")

    try:
        records = parse_task_records(tasks_content)
        ready = ready_task_ids(records)
        current_task = next((record.title for record in records if record.id in ready), None)
    except SpecControlError:
        current_task = None
    if checkpoint_pending:
        next_action = "处理未完成 update checkpoint：rollback 回滚更新，或 complete 确认保留"
    elif blocked:
        next_action = next((f"解除阻塞：{task.text}" for task in tasks if task.is_blocked), "处理任务阻塞")
    elif r.pending_questions:
        next_action = f"确认需求：{r.pending_questions[0]}"
    elif r.spec_clarification_gaps:
        next_action = f"补齐项目定义：{project_gap_message(r.spec_clarification_gaps[0])}"
    elif orchestration_blocked:
        next_action = f"修正编排策略：{r.orchestration_errors[0]}"
    elif all_done and check_passed:
        next_action = "整理交付结果并完成归档"
    elif all_done:
        next_action = "修复验收未通过项"
    else:
        next_action = current_task or "继续实现项目任务"
    gate_gap_total = len(r.spec_clarification_gaps) + r.missing_boundary + r.missing_verify + r.unchecked_items
    handoff_line = None
    try:
        handoff_content = read_handoff(package_dir)
    except (OSError, UnicodeError, ValueError):
        handoff_content = None
        handoff_line = "handoff：不可读（超大/非 UTF-8/非常规文件），运行 spec_handoff.py validate 诊断"
    if handoff_content is not None and handoff_line is None:
        if validate_handoff(handoff_content):
            handoff_line = "handoff：结构非法，运行 spec_handoff.py validate 诊断"
        else:
            doc = parse_handoff(handoff_content)
            if doc.latest is not None:
                label, _detail = freshness(doc, root, tasks_content)
                handoff_line = f"handoff：最新 {doc.latest.timestamp}（{label}，{doc.latest.collab}）"
    dash = Dashboard(
        slug=slug,
        stage=next_stage,
        title=title,
        done=r.completed_tasks,
        total=r.total_tasks,
        health=health,
        pipeline=pipeline,
        strip=strip,
        strip_labels=strip_labels,
        metrics={
            "阻塞": "1" if (blocked or checkpoint_pending) else "0",
            "待确认": str(len(r.pending_questions)),
            "门禁": "通过" if check_passed else f"缺口 {gate_gap_total}",
            "证据": str(r.evidence_filled_count),
            "范围": r.verification_scope or "",
        },
        alerts=alerts,
        next_step=next_action,
        detail=[handoff_line] if handoff_line else [],
        detail_title="交接" if handoff_line else "交付信息",
        ascii_mode=ascii_mode,
        compact=compact,
    )
    if output_format == "json":
        return json.dumps(
            {
                "slug": slug,
                "stage": next_stage,
                "health": health,
                "completed": r.completed_tasks,
                "total": r.total_tasks,
                "currentTask": current_task,
                "dependencyErrors": r.task_contract_errors,
                "orchestrationErrors": r.orchestration_errors,
                "verificationScope": r.verification_scope,
                "updateCheckpoint": checkpoint_status.state,
                **({"handoff": handoff_line} if handoff_line else {}),
            },
            ensure_ascii=False,
        )
    return dash.render()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    try:
        specs_root = resolve_specs_root(root, args.specs_dir)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        specs_dir = resolve_specs_child(specs_root, "specs")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.slug:
        try:
            slug = validate_slug(args.slug.strip())
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        package_dir = specs_dir / slug
        if not package_dir.exists():
            print(f"error: task package not found: {package_dir}", file=sys.stderr)
            return 1
        try:
            print(
                route_for_package(
                    root,
                    slug,
                    args.specs_dir,
                    ascii_mode=args.ascii,
                    compact=args.compact,
                    output_format=args.format,
                )
            )
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    packages = active_packages(specs_dir)
    if not packages:
        dash = Dashboard(
            slug="spec",
            stage="new",
            title="当前没有进行中的项目",
            health=HEALTH_OK,
            next_step="描述要完成的目标，skill 会直接建立任务并开始推进",
            ascii_mode=args.ascii,
            compact=args.compact,
        )
        print(dash.render())
        return 0

    try:
        selection = select_active_package(root, specs_root, packages)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: cannot bind active task package: {exc}", file=sys.stderr)
        return 1
    if selection.slug:
        try:
            print(
                route_for_package(
                    root,
                    selection.slug,
                    args.specs_dir,
                    ascii_mode=args.ascii,
                    compact=args.compact,
                    output_format=args.format,
                )
            )
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    dash = Dashboard(
        slug="spec",
        stage="status",
        title="任务包分支未绑定",
        health=HEALTH_RISK,
        alerts=[selection.warning or f"无法唯一选择活动任务包：{'、'.join(packages)}"],
        metrics={"门禁": "分支绑定"},
        next_step="切换到任务包记录的 integration branch，或显式指定 --slug 只读查看",
        ascii_mode=args.ascii,
        compact=args.compact,
    )
    print(dash.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())

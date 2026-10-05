#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Render status/tasks views for a Spec package with Claude/Codex-compatible stage guidance.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
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
from handoff_support import handoff_brief
from spec_package_support import (
    active_package_slugs,
    add_specs_dir_arg,
    checklist_complete,
    count_tasks,
    extract_title_from_content,
    read_regular_text,
    resolve_specs_child,
    resolve_specs_root,
    validate_slug,
)
from update_checkpoint_support import CheckpointStatus, detect_update_checkpoint

PENDING_TOKEN = " "
COMPLETED_TOKEN = "x"
IN_PROGRESS_TOKEN = ">"
BLOCKED_TOKEN = "!"
META_SECTION_TITLE = "使用规则"


@dataclass
class Task:
    section: str
    text: str
    indent: int
    marker: str
    boundary: str = ""
    verify: str = ""

    @property
    def is_completed(self) -> bool:
        return self.marker.lower() == COMPLETED_TOKEN

    @property
    def is_blocked(self) -> bool:
        return not self.is_completed and (self.marker == BLOCKED_TOKEN or "阻塞" in self.text)

    @property
    def is_explicit_in_progress(self) -> bool:
        return self.marker == IN_PROGRESS_TOKEN

    @property
    def is_pending(self) -> bool:
        return not self.is_completed and not self.is_blocked and not self.is_explicit_in_progress


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render status/tasks views from a Spec package.")
    parser.add_argument(
        "--slug",
        help=(
            "Task package slug, e.g. 2026-06-12_payment-recovery. "
            "Optional for --view status: omit to render a multi-package overview."
        ),
    )
    parser.add_argument(
        "--view",
        required=True,
        choices=("status", "tasks"),
        help="View to render",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root where the .spec directory exists (default: current directory)",
    )
    add_specs_dir_arg(parser)
    parser.add_argument("--ascii", action="store_true", help="Render dashboard glyphs as ASCII fallback")
    parser.add_argument("--compact", action="store_true", help="Render metrics as a single line")
    return parser.parse_args()


def status_package_slugs(specs_dir: Path) -> list[str]:
    """Discover complete packages plus interrupted updates with an incomplete triad."""
    packages = set(active_package_slugs(specs_dir))
    if not specs_dir.exists():
        return []
    for path in sorted(specs_dir.iterdir()):
        if path.name == "archive" or path.name.startswith(".") or path.is_symlink() or not path.is_dir():
            continue
        try:
            validate_slug(path.name)
            status = detect_update_checkpoint(path)
        except (OSError, UnicodeError, ValueError):
            continue
        if status.interrupted:
            packages.add(path.name)
    return sorted(packages)


def parse_tasks(tasks_content: str) -> tuple[list[str], list[Task]]:
    sections: list[str] = []
    tasks: list[Task] = []
    current_section = ""

    for raw_line in tasks_content.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            current_section = line[3:].strip()
            if current_section != META_SECTION_TITLE:
                sections.append(current_section)
            continue

        task_match = re.match(r"^(\s*)-\s\[(.)\]\s+(.*)$", line)
        if task_match and current_section and current_section != META_SECTION_TITLE:
            indent = len(task_match.group(1))
            marker = task_match.group(2)
            text = task_match.group(3).strip()
            tasks.append(Task(section=current_section, text=text, indent=indent, marker=marker))
            continue

        detail_match = re.match(r"^\s*-\s+(boundary|verify):\s*(.*)$", line)
        if detail_match and tasks:
            key = detail_match.group(1)
            value = detail_match.group(2).strip()
            task = tasks[-1]
            if key == "boundary":
                task.boundary = value
            else:
                task.verify = value

    return sections, tasks


def tasks_by_section(tasks: list[Task], sections: list[str]) -> dict[str, list[Task]]:
    grouped: dict[str, list[Task]] = {section: [] for section in sections}
    for task in tasks:
        grouped.setdefault(task.section, []).append(task)
    return grouped


def find_primary_task(tasks: list[Task]) -> Task | None:
    for task in tasks:
        if task.is_explicit_in_progress:
            return task
    for task in tasks:
        if task.is_pending:
            return task
    return None


def section_progress(section_tasks: list[Task]) -> tuple[int, int]:
    total = len(section_tasks)
    completed = sum(1 for task in section_tasks if task.is_completed)
    return completed, total


def overall_state(
    tasks: list[Task],
    pending_questions: list[str],
    checklist_content: str,
    gate_gap_detected: bool,
) -> str:
    all_completed = all(task.is_completed for task in tasks) if tasks else False
    if any(task.is_blocked for task in tasks):
        return HEALTH_BLOCKED
    if gate_gap_detected:
        return HEALTH_RISK
    if pending_questions:
        return HEALTH_RISK
    if all_completed and not checklist_complete(checklist_content):
        return HEALTH_RISK
    return HEALTH_OK


def status_next_step(
    primary_task: Task | None,
    blocked_tasks: list[Task],
    pending_questions: list[str],
    all_completed: bool,
    checklist_content: str,
    gate_gaps: list[str],
    *,
    standalone_blocked: str | None = None,
    dependency_errors_list: list[str] | None = None,
    orchestration_errors_list: list[str] | None = None,
    checkpoint_pending: bool = False,
    checkpoint_status: CheckpointStatus | None = None,
    gate_passed: bool = True,
) -> str:
    """Priority ordering mirrors route_spec_package: checkpoint > blocked >
    pending > definition gaps (incl. dependency errors) > orchestration >
    acceptance/execution."""
    if checkpoint_pending:
        if checkpoint_status is not None and checkpoint_status.state == "corrupted":
            return "处理未完成 update checkpoint：检查点记录损坏，需人工恢复"
        return "处理未完成 update checkpoint：rollback 回滚更新，或 complete 确认保留"
    if blocked_tasks:
        return f"解除阻塞：{blocked_tasks[0].text}"
    if standalone_blocked:
        return "处理任务阻塞：解决 tasks.md 中的独立阻塞状态行"
    if pending_questions:
        return f"确认需求：{pending_questions[0]}"
    if gate_gaps:
        first_gap = gate_gaps[0]
        if dependency_errors_list and first_gap in dependency_errors_list:
            return f"修正任务依赖：{first_gap}"
        return f"补齐项目定义：{project_gap_message(first_gap)}"
    if orchestration_errors_list:
        return f"修正编排策略：{orchestration_errors_list[0]}"
    if primary_task and not all_completed:
        return primary_task.text
    if all_completed and not checklist_complete(checklist_content):
        return "完成剩余验收项"
    if all_completed and not gate_passed:
        return "修复验收未通过项"
    return "整理交付结果并完成归档"


def checkpoint_alert(status: CheckpointStatus | None) -> str:
    if status is not None and status.state == "active" and status.checkpoint is not None:
        return f"存在未完成 update checkpoint：更新「{status.checkpoint.intent}」尚未确认完成或回滚"
    if status is not None and status.state == "corrupted":
        return "存在未完成 update checkpoint：检查点记录损坏，需人工恢复"
    return "存在未完成 update checkpoint：需确认完成、回滚或人工恢复"


def render_unreadable_checkpoint(slug: str, status: CheckpointStatus) -> str:
    alert = checkpoint_alert(status)
    next_step = (
        "处理未完成 update checkpoint：rollback 回滚更新，或 complete 确认保留"
        if status.state == "active"
        else "处理未完成 update checkpoint：检查点记录损坏，需人工恢复"
    )
    return (
        f"# {slug}\n\n"
        f"状态：{HEALTH_BLOCKED}（status）\n\n"
        "已完成：未知（任务记录缺失或不可读）\n\n"
        f"## 需要关注\n\n- {alert}\n- 任务记录缺失或不可读，任务进度未知\n\n"
        f"## 接下来\n\n- {next_step}\n"
    )


def render_status(
    slug: str,
    title: str,
    tasks_content: str,
    spec_content: str,
    checklist_content: str,
    sections: list[str],
    tasks: list[Task],
    *,
    ascii_mode: bool = False,
    compact: bool = False,
    checkpoint_pending: bool = False,
    checkpoint_status: CheckpointStatus | None = None,
    handoff_line: str | None = None,
) -> str:
    primary_task = find_primary_task(tasks)
    blocked_tasks = [task for task in tasks if task.is_blocked]
    total_tasks, completed_tasks = count_tasks(tasks_content)
    r = compute_gate_results(spec_content, tasks_content, checklist_content, slug=slug)
    pending_questions = list(r.pending_questions)
    checkpoint_pending = checkpoint_pending or bool(checkpoint_status and checkpoint_status.interrupted)

    orchestration_errors = list(r.orchestration_errors)
    dependency_errors_list = list(r.task_contract_errors)
    ordered_gate_gaps = list(r.spec_clarification_gaps)
    standalone_blocked_text: str | None = None
    if r.blocked_tasks and not blocked_tasks:
        standalone_blocked_text = next(
            (line.strip() for line in tasks_content.splitlines() if re.match(r"^\s*(?:-\s*)?阻塞[：:]", line)),
            "阻塞",
        )

    health = overall_state(tasks, pending_questions, checklist_content, bool(ordered_gate_gaps))
    if checkpoint_pending or r.blocked_tasks:
        health = worst_health(health, HEALTH_BLOCKED)
    if orchestration_errors or (r.all_tasks_done and not r.overall_ok):
        health = worst_health(health, HEALTH_RISK)

    strip, strip_labels = task_strip_from_records(tasks)

    alerts: list[str] = []
    if checkpoint_pending:
        alerts.append(checkpoint_alert(checkpoint_status))
    alerts.extend(f"任务受阻：{task.text}" for task in blocked_tasks)
    if standalone_blocked_text:
        alerts.append(f"任务受阻：{standalone_blocked_text}")
    alerts.extend(f"需要确认需求：{question}" for question in pending_questions)
    for gap in ordered_gate_gaps:
        if gap in dependency_errors_list:
            alerts.append(f"修正任务依赖：{gap}")
        else:
            alerts.append(f"项目定义不完整：{project_gap_message(gap)}")
    alerts.extend(f"编排策略尚未满足：{error}" for error in orchestration_errors)
    alerts.extend(f"验证范围尚未满足：{error}" for error in r.verification_scope_errors)
    if r.all_tasks_done and not r.overall_ok and not alerts:
        alerts.append("项目尚未满足最终验收要求")

    all_completed = r.all_tasks_done
    if checkpoint_pending or r.blocked_tasks:
        active_stage = "run"
    elif all_completed and r.overall_ok:
        active_stage = "done"
    elif all_completed:
        active_stage = "check"
    else:
        active_stage = "run"

    current = primary_task.text if primary_task else None

    next_step = status_next_step(
        primary_task,
        blocked_tasks,
        pending_questions,
        all_completed,
        checklist_content,
        ordered_gate_gaps,
        standalone_blocked=standalone_blocked_text,
        dependency_errors_list=dependency_errors_list,
        orchestration_errors_list=orchestration_errors,
        checkpoint_pending=checkpoint_pending,
        checkpoint_status=checkpoint_status,
        gate_passed=r.overall_ok,
    )

    dash = Dashboard(
        slug=slug,
        stage="status",
        title=title,
        done=completed_tasks,
        total=total_tasks,
        health=health,
        pipeline=default_pipeline(active_stage),
        strip=strip,
        strip_labels=strip_labels,
        metrics={
            "阻塞": "1" if (checkpoint_pending or r.blocked_tasks) else "0",
            "待确认": str(len(pending_questions)),
            "门禁": "通过" if r.overall_ok else "未过",
            "证据": str(r.evidence_filled_count),
            "范围": r.verification_scope or "",
        },
        current=current,
        alerts=alerts,
        next_step=next_step,
        detail=[handoff_line] if handoff_line else [],
        detail_title="交接" if handoff_line else "交付信息",
        ascii_mode=ascii_mode,
        compact=compact,
    )
    return dash.render()


def display_marker(task: Task, primary_task: Task | None) -> str:
    if task.is_completed:
        return COMPLETED_TOKEN
    if task.is_blocked:
        return BLOCKED_TOKEN
    if primary_task and task is primary_task:
        return IN_PROGRESS_TOKEN
    return PENDING_TOKEN


def render_tasks(slug: str, sections: list[str], tasks: list[Task]) -> str:
    grouped = tasks_by_section(tasks, sections)
    primary_task = find_primary_task(tasks)
    missing_verify_tasks = [task for task in tasks if not task.verify]
    lines = [f"# {slug} - 任务列表", ""]

    for index, section in enumerate(sections):
        section_tasks = grouped.get(section, [])
        completed, total = section_progress(section_tasks)
        lines.append(f"## {section}（{completed} / {total}）")
        for task in section_tasks:
            marker = display_marker(task, primary_task)
            lines.append(f"- [{marker}] {task.text}")
            if task.boundary:
                lines.append(f"  - boundary: {task.boundary}")
            if task.verify:
                lines.append(f"  - verify: {task.verify}")
        if index != len(sections) - 1:
            lines.append("")

    if missing_verify_tasks:
        lines.extend(
            [
                "",
                "## 风险提示",
                f"- 验证说明缺失任务：{len(missing_verify_tasks)} 个（请先补齐再执行）",
            ]
        )

    return "\n".join(lines)


def package_status_summary(
    slug: str,
    tasks_content: str,
    spec_content: str,
    checklist_content: str,
    sections: list[str],
    tasks: list[Task],
    *,
    checkpoint_status: CheckpointStatus | None = None,
) -> dict[str, str]:
    total_tasks, completed_tasks = count_tasks(tasks_content)
    blocked_tasks = [task for task in tasks if task.is_blocked]
    primary_task = find_primary_task(tasks)
    r = compute_gate_results(spec_content, tasks_content, checklist_content, slug=slug)
    pending_questions = list(r.pending_questions)
    dependency_errors_list = list(r.task_contract_errors)
    ordered_gate_gaps = list(r.spec_clarification_gaps)
    checkpoint_pending = bool(checkpoint_status and checkpoint_status.interrupted)
    standalone_blocked = bool(r.blocked_tasks) and not blocked_tasks

    state = overall_state(tasks, pending_questions, checklist_content, bool(ordered_gate_gaps))
    if checkpoint_pending or r.blocked_tasks:
        state = worst_health(state, HEALTH_BLOCKED)
    if r.orchestration_errors or (r.all_tasks_done and not r.overall_ok):
        state = worst_health(state, HEALTH_RISK)

    first_error = r.orchestration_errors[0] if r.orchestration_errors else "无"
    blocked_label = (
        blocked_tasks[0].text if blocked_tasks else ("tasks.md 存在独立阻塞状态行" if standalone_blocked else "无")
    )
    dependency_label = dependency_errors_list[0] if dependency_errors_list else "无"
    checkpoint_label = checkpoint_alert(checkpoint_status) if checkpoint_pending else "无"
    definition_gaps = [gap for gap in ordered_gate_gaps if gap not in dependency_errors_list]
    acceptance_failed = (
        r.all_tasks_done
        and not r.overall_ok
        and not (
            checkpoint_pending or r.blocked_tasks or pending_questions or ordered_gate_gaps or r.orchestration_errors
        )
    )
    return {
        "slug": slug,
        "title": extract_title_from_content(spec_content, slug),
        "progress": f"{completed_tasks}/{total_tasks}",
        "state": state,
        "primary": primary_task.text if primary_task else "无",
        "blocked": blocked_label,
        "pending": pending_questions[0] if pending_questions else "无",
        "dependency": dependency_label,
        "needs_work": "是" if (definition_gaps or acceptance_failed) else "否",
        "gate_failed": "是" if not r.overall_ok else "否",
        "orchestration": first_error,
        "verification_scope": r.verification_scope or "历史包未声明",
        "checkpoint": checkpoint_label,
        "acceptance": "验收未通过" if acceptance_failed else "无",
    }


def render_multi_status(
    root: Path,
    specs_root: Path,
    packages: list[str],
    *,
    ascii_mode: bool = False,
    compact: bool = False,
) -> str:
    if not packages:
        dash = Dashboard(
            slug="spec",
            stage="status",
            title="当前没有进行中的项目",
            health=HEALTH_OK,
            next_step="描述要完成的目标，skill 会直接建立任务并开始推进",
            ascii_mode=ascii_mode,
            compact=compact,
        )
        return dash.render()

    rows: list[str] = []
    done_sum = 0
    total_sum = 0
    blocked_packages = 0
    task_blocked_packages = 0
    pending_packages = 0
    gate_packages = 0
    failed_gate_packages = 0
    orchestration_packages = 0
    dependency_packages = 0
    checkpoint_packages = 0
    corrupted_checkpoint_packages = 0
    read_failure_packages = 0
    worst = HEALTH_OK
    primary_line: str | None = None
    for slug in packages:
        try:
            package_dir = resolve_specs_child(specs_root, "specs", slug)
            checkpoint_status = detect_update_checkpoint(package_dir)
        except (OSError, UnicodeError, ValueError) as exc:
            rows.append(f"- {slug}：状态读取失败（{exc}）")
            read_failure_packages += 1
            blocked_packages += 1
            worst = worst_health(worst, HEALTH_BLOCKED)
            continue
        try:
            spec_content = read_regular_text(package_dir / "spec.md")
            tasks_content = read_regular_text(package_dir / "tasks.md")
            checklist_content = read_regular_text(package_dir / "checklist.md")
        except (OSError, UnicodeError, ValueError) as exc:
            if checkpoint_status.interrupted:
                rows.append(f"- {slug}：已完成 未知；阻塞 {checkpoint_alert(checkpoint_status)}；任务记录不可读")
                checkpoint_packages += 1
                if checkpoint_status.state == "corrupted":
                    corrupted_checkpoint_packages += 1
                blocked_packages += 1
                worst = worst_health(worst, HEALTH_BLOCKED)
                continue
            rows.append(f"- {slug}：状态读取失败（{exc}）")
            read_failure_packages += 1
            blocked_packages += 1
            worst = worst_health(worst, HEALTH_BLOCKED)
            continue
        sections, tasks = parse_tasks(tasks_content)
        title = extract_title_from_content(spec_content, slug)
        if not tasks:
            row = f"- {title}：尚未拆出可执行任务"
            if checkpoint_status.interrupted:
                row += f"；阻塞 {checkpoint_alert(checkpoint_status)}"
                checkpoint_packages += 1
                if checkpoint_status.state == "corrupted":
                    corrupted_checkpoint_packages += 1
                blocked_packages += 1
            else:
                read_failure_packages += 1
                blocked_packages += 1
            rows.append(row)
            worst = worst_health(worst, HEALTH_BLOCKED)
            continue
        summary = package_status_summary(
            slug,
            tasks_content,
            spec_content,
            checklist_content,
            sections,
            tasks,
            checkpoint_status=checkpoint_status,
        )
        state = summary["state"]
        worst = worst_health(worst, state)
        total_tasks, completed_tasks = count_tasks(tasks_content)
        done_sum += completed_tasks
        total_sum += total_tasks
        if summary["blocked"] != "无" or summary["checkpoint"] != "无":
            blocked_packages += 1
        if summary["blocked"] != "无":
            task_blocked_packages += 1
        if summary["checkpoint"] != "无":
            checkpoint_packages += 1
            if checkpoint_status.state == "corrupted":
                corrupted_checkpoint_packages += 1
        if summary["pending"] != "无":
            pending_packages += 1
        if summary["needs_work"] == "是":
            gate_packages += 1
        if summary["gate_failed"] == "是":
            failed_gate_packages += 1
        if primary_line is None and summary["primary"] != "无":
            primary_line = f"{summary['title']}：{summary['primary']}"
        row = f"- {summary['title']}：已完成 {summary['progress']}"
        if summary["primary"] != "无":
            row += f"；当前 {summary['primary']}"
        if summary["checkpoint"] != "无":
            row += f"；阻塞 {summary['checkpoint']}"
        if summary["blocked"] != "无":
            row += f"；阻塞 {summary['blocked']}"
        if summary["pending"] != "无":
            row += f"；待确认 {summary['pending']}"
        if summary["dependency"] != "无":
            row += f"；任务依赖错误 {summary['dependency']}"
            dependency_packages += 1
        if summary["orchestration"] != "无":
            row += f"；编排策略尚未满足：{summary['orchestration']}"
            orchestration_packages += 1
        if summary["acceptance"] != "无":
            row += f"；{summary['acceptance']}"
        row += f"；范围 {summary['verification_scope']}"
        brief = handoff_brief(package_dir, root, tasks_content)
        if brief:
            row += f"；{brief}"
        rows.append(row)

    detail = rows

    alerts = []
    if checkpoint_packages:
        alerts.append(f"{checkpoint_packages} 个项目存在未完成 update checkpoint")
    if read_failure_packages:
        alerts.append(f"{read_failure_packages} 个项目状态读取失败或尚无可执行任务")
    if task_blocked_packages:
        alerts.append(f"{task_blocked_packages} 个项目有任务阻塞")
    if pending_packages:
        alerts.append(f"{pending_packages} 个项目需要确认需求")
    if gate_packages:
        alerts.append(f"{gate_packages} 个项目定义或验收尚未完整")
    if orchestration_packages:
        alerts.append(f"{orchestration_packages} 个项目编排策略尚未满足")
    if dependency_packages:
        alerts.append(f"{dependency_packages} 个项目任务依赖存在错误")

    if corrupted_checkpoint_packages:
        next_step = "处理未完成 update checkpoint：检查点记录损坏，需人工恢复"
    elif checkpoint_packages:
        next_step = "处理未完成 update checkpoint：rollback 回滚更新，或 complete 确认保留"
    elif read_failure_packages:
        next_step = "修复项目记录读取失败或补齐可执行任务"
    elif task_blocked_packages:
        next_step = "处理项目任务阻塞"
    elif pending_packages:
        next_step = "确认项目需求"
    elif gate_packages:
        next_step = "修复项目定义或验收缺口"
    elif dependency_packages:
        next_step = "修正项目任务依赖"
    elif orchestration_packages:
        next_step = "修正项目编排策略"
    else:
        next_step = "继续推进当前项目任务"

    dash = Dashboard(
        slug="spec",
        stage="status",
        title="进行中的项目",
        done=done_sum,
        total=total_sum,
        health=worst,
        metrics={
            "阻塞": str(blocked_packages),
            "待确认": str(pending_packages),
            "门禁": f"待补 {failed_gate_packages}" if failed_gate_packages else "通过",
            "证据": "无",
        },
        current=primary_line,
        alerts=alerts,
        next_step=next_step,
        detail=detail,
        detail_title="项目概览",
        ascii_mode=ascii_mode,
        compact=compact,
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

    if not args.slug:
        if args.view != "status":
            print("error: --slug is required for --view tasks", file=sys.stderr)
            return 1
        packages = status_package_slugs(specs_dir)
        print(
            render_multi_status(
                root,
                specs_root,
                packages,
                ascii_mode=args.ascii,
                compact=args.compact,
            )
        )
        return 0

    try:
        slug = validate_slug(args.slug.strip())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        spec_path = resolve_specs_child(specs_root, "specs", slug, "spec.md")
        tasks_path = resolve_specs_child(specs_root, "specs", slug, "tasks.md")
        checklist_path = resolve_specs_child(specs_root, "specs", slug, "checklist.md")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    checkpoint_status = detect_update_checkpoint(spec_path.parent) if args.view == "status" else None
    for path in (spec_path, tasks_path, checklist_path):
        if not path.exists():
            if checkpoint_status is not None and checkpoint_status.interrupted:
                print(render_unreadable_checkpoint(slug, checkpoint_status))
                return 0
            print(f"error: required file missing: {path}", file=sys.stderr)
            return 1

    try:
        spec_content = read_regular_text(spec_path)
        tasks_content = read_regular_text(tasks_path)
        checklist_content = read_regular_text(checklist_path)
    except (OSError, UnicodeError, ValueError) as exc:
        if checkpoint_status is not None and checkpoint_status.interrupted:
            print(render_unreadable_checkpoint(slug, checkpoint_status))
            return 0
        print(f"error: invalid task package file: {exc}", file=sys.stderr)
        return 1
    title = extract_title_from_content(spec_content, slug)
    sections, tasks = parse_tasks(tasks_content)

    if not tasks:
        if checkpoint_status is not None and checkpoint_status.interrupted:
            print(render_unreadable_checkpoint(slug, checkpoint_status))
            return 0
        print("error: no actionable tasks found in tasks.md", file=sys.stderr)
        return 1

    if args.view == "status":
        output = render_status(
            slug,
            title,
            tasks_content,
            spec_content,
            checklist_content,
            sections,
            tasks,
            ascii_mode=args.ascii,
            compact=args.compact,
            checkpoint_status=checkpoint_status,
            handoff_line=handoff_brief(spec_path.parent, root, tasks_content),
        )
    else:
        output = render_tasks(slug, sections, tasks)

    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
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
    SpecControlError,
    active_package_slugs,
    add_specs_dir_arg,
    count_tasks,
    extract_title_from_content,
    parse_task_records,
    read_regular_text,
    ready_task_ids,
    resolve_specs_child,
    resolve_specs_root,
    task_title_signals_blocked,
    validate_slug,
)

PENDING_TOKEN = " "
COMPLETED_TOKEN = "x"
IN_PROGRESS_TOKEN = ">"
BLOCKED_TOKEN = "!"
# tasks.md meta/rule block, bilingual: only task checkboxes outside it count.
META_SECTION_TITLES = ("使用规则", "Usage Rules")
BLOCKED_LINE_RE = re.compile(r"^\s*(?:-\s*)?(?:阻塞|blocked)[：:]")


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
        return not self.is_completed and (self.marker == BLOCKED_TOKEN or task_title_signals_blocked(self.text))

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


@dataclass(frozen=True)
class CheckpointStatus:
    """A leftover ``update-checkpoint.json`` from the retired update mechanism."""

    path: Path
    interrupted: bool


def detect_update_checkpoint(package_dir: Path) -> CheckpointStatus:
    path = package_dir / "update-checkpoint.json"
    return CheckpointStatus(path, path.exists())


def status_package_slugs(specs_dir: Path) -> list[str]:
    """Discover complete packages plus historical checkpoints with an
    incomplete triad (retired mechanism, read-only recognition)."""
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
            if current_section not in META_SECTION_TITLES:
                sections.append(current_section)
            continue

        task_match = re.match(r"^(\s*)-\s\[(.)\]\s+(.*)$", line)
        if task_match and current_section and current_section not in META_SECTION_TITLES:
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
    """Select a next-action candidate, not evidence that a task is active."""
    for task in tasks:
        if task.is_explicit_in_progress and not task.is_blocked:
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
    gate_gap_detected: bool,
    gate_passed: bool = True,
) -> str:
    all_completed = all(task.is_completed for task in tasks) if tasks else False
    if any(task.is_blocked for task in tasks):
        return HEALTH_BLOCKED
    if gate_gap_detected:
        return HEALTH_RISK
    if pending_questions:
        return HEALTH_RISK
    if all_completed and not gate_passed:
        return HEALTH_RISK
    return HEALTH_OK


def status_next_step(
    primary_task: Task | None,
    blocked_tasks: list[Task],
    pending_questions: list[str],
    all_completed: bool,
    checklist_content: str | None,
    gate_gaps: list[str],
    *,
    standalone_blocked: str | None = None,
    dependency_errors_list: list[str] | None = None,
    orchestration_errors_list: list[str] | None = None,
    question_errors_list: list[str] | None = None,
    inquiry_next_step: str | None = None,
    checkpoint_pending: bool = False,
    gate_passed: bool = True,
    spec_content: str = "",
) -> str:
    """Priority ordering mirrors route_spec_package: blocked > pending >
    definition gaps (incl. dependency errors) > orchestration >
    acceptance/execution. A historical checkpoint is advisory-only while
    other work remains, but becomes the next step once otherwise converged;
    check/done still refuse while the file exists."""
    if blocked_tasks:
        return f"Unblock task: {blocked_tasks[0].text}"
    if standalone_blocked:
        return "Resolve the standalone blocked status in tasks.md"
    if question_errors_list:
        return f"Fix the inquiry contract: {question_errors_list[0]}"
    if pending_questions:
        return f"Confirm requirements: {pending_questions[0]}"
    if gate_gaps:
        first_gap = gate_gaps[0]
        if dependency_errors_list and first_gap in dependency_errors_list:
            return f"Fix task dependencies: {first_gap}"
        return f"Complete the project definition: {project_gap_message(first_gap)}"
    if orchestration_errors_list:
        return f"Fix the orchestration strategy: {orchestration_errors_list[0]}"
    if inquiry_next_step and not all_completed:
        return inquiry_next_step
    if primary_task and not all_completed:
        return primary_task.text
    if all_completed and not gate_passed:
        return "Fix failed acceptance checks"
    if checkpoint_pending:
        # Converged except for the historical checkpoint: resolving it is the
        # only step left (check/done refuse while the file exists).
        return f"Resolve the legacy update checkpoint: {checkpoint_recovery_guidance()}"
    return "Prepare delivery and archive the record"


def checkpoint_recovery_guidance() -> str:
    return "Use git to confirm recovery is unnecessary, remove update-checkpoint.json, then rerun the gates"


def checkpoint_alert(status: CheckpointStatus | None) -> str:
    return f"Legacy update-checkpoint.json (mechanism retired): {checkpoint_recovery_guidance()}"


def render_unreadable_checkpoint(slug: str, status: CheckpointStatus) -> str:
    alert = checkpoint_alert(status)
    next_step = f"Resolve the unfinished legacy update checkpoint: {checkpoint_recovery_guidance()}; then rerun status"
    return (
        f"# {slug}\n\n"
        "Status: blocked (status)\n\n"
        "Completed: unknown (task record missing or unreadable)\n\n"
        f"## Attention\n\n- {alert}\n- Task record missing or unreadable; progress unknown\n\n"
        f"## Next\n\n- {next_step}\n"
    )


def render_status(
    slug: str,
    title: str,
    tasks_content: str,
    spec_content: str,
    checklist_content: str | None,
    sections: list[str],
    tasks: list[Task],
    *,
    ascii_mode: bool = False,
    compact: bool = False,
    checkpoint_pending: bool = False,
    checkpoint_status: CheckpointStatus | None = None,
    handoff_line: str | None = None,
    root: Path | None = None,
    package_dir: Path | None = None,
) -> str:
    primary_task = find_primary_task(tasks)
    blocked_tasks = [task for task in tasks if task.is_blocked]
    total_tasks, completed_tasks = count_tasks(tasks_content)
    r = compute_gate_results(
        spec_content, tasks_content, checklist_content, slug=slug, root=root, package_dir=package_dir
    )
    pending_questions = list(r.human_questions)
    inquiry_next_step = None
    if r.autonomous_task_ids:
        try:
            records = parse_task_records(tasks_content)
            ready = ready_task_ids(records)
            inquiry = ready & set(r.autonomous_task_ids)
            inquiry_task = next((record for record in records if record.id in inquiry), None)
            ready_task = next((record for record in records if record.id in ready), None)
            inquiry_next_step = (
                f"Autonomous inquiry: {inquiry_task.title}"
                if inquiry_task
                else ready_task.title
                if ready_task
                else None
            )
        except SpecControlError:
            pass
    checkpoint_pending = checkpoint_pending or bool(checkpoint_status and checkpoint_status.interrupted)

    orchestration_errors = list(r.orchestration_errors)
    dependency_errors_list = list(r.task_contract_errors)
    # Same order as route_spec_package: definition gaps first, dependency
    # errors second; both outrank acceptance/execution steps.
    ordered_gate_gaps = [*r.spec_clarification_gaps, *dependency_errors_list]
    standalone_blocked_text: str | None = None
    if r.blocked_tasks and not blocked_tasks:
        standalone_blocked_text = next(
            (line.strip() for line in tasks_content.splitlines() if BLOCKED_LINE_RE.match(line)),
            "blocked task marker in tasks.md",
        )

    health = overall_state(tasks, pending_questions, bool(ordered_gate_gaps), gate_passed=r.overall_ok)
    if r.blocked_tasks:
        health = worst_health(health, HEALTH_BLOCKED)
    if checkpoint_pending:
        # Advisory-level attention mirroring route; gates still refuse.
        health = worst_health(health, HEALTH_RISK)
    if orchestration_errors or r.question_errors or (r.all_tasks_done and not r.overall_ok):
        health = worst_health(health, HEALTH_RISK)

    strip, strip_labels = task_strip_from_records(tasks)

    alerts: list[str] = []
    if checkpoint_pending:
        alerts.append(checkpoint_alert(checkpoint_status))
    alerts.extend(f"Task blocked: {task.text}" for task in blocked_tasks)
    if standalone_blocked_text:
        alerts.append(f"Task blocked: {standalone_blocked_text}")
    alerts.extend(f"Requirements need confirmation: {question}" for question in pending_questions)
    alerts.extend(f"Autonomous inquiry: {question}" for question in r.autonomous_questions)
    alerts.extend(f"Inquiry contract requirements not met: {error}" for error in r.question_errors)
    for gap in ordered_gate_gaps:
        if gap in dependency_errors_list:
            alerts.append(f"Fix task dependencies: {gap}")
        else:
            alerts.append(f"Project definition incomplete: {project_gap_message(gap)}")
    alerts.extend(f"Orchestration requirements not met: {error}" for error in orchestration_errors)
    alerts.extend(f"Verification scope requirements not met: {error}" for error in r.verification_scope_errors)
    if r.all_tasks_done and not r.overall_ok and not alerts:
        alerts.append("Project does not meet final acceptance requirements")

    all_completed = r.all_tasks_done
    if r.blocked_tasks:
        active_stage = "run"
    elif all_completed and r.overall_ok:
        active_stage = "done"
    elif all_completed:
        active_stage = "check"
    else:
        active_stage = "run"

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
        question_errors_list=r.question_errors,
        inquiry_next_step=inquiry_next_step,
        checkpoint_pending=checkpoint_pending,
        gate_passed=r.overall_ok,
        spec_content=spec_content,
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
            "Blocked": "1" if (checkpoint_pending or r.blocked_tasks) else "0",
            "Pending decisions": str(len(pending_questions)),
            "Gate": "Passed" if r.overall_ok else "Failed",
            "Evidence": str(r.evidence_filled_count),
            "Scope": r.verification_scope or "",
        },
        alerts=alerts,
        next_step=next_step,
        detail=[handoff_line] if handoff_line else [],
        detail_title="Handoff" if handoff_line else "Delivery Details",
        ascii_mode=ascii_mode,
        compact=compact,
    )
    return dash.render()


def display_marker(task: Task, primary_task: Task | None = None) -> str:
    """Preserve the recorded state; the legacy primary argument is not evidence."""
    if task.is_completed:
        return COMPLETED_TOKEN
    if task.is_blocked:
        return BLOCKED_TOKEN
    if task.is_explicit_in_progress:
        return IN_PROGRESS_TOKEN
    return PENDING_TOKEN


def render_tasks(slug: str, sections: list[str], tasks: list[Task]) -> str:
    grouped = tasks_by_section(tasks, sections)
    missing_verify_tasks = [task for task in tasks if not task.verify]
    lines = [f"# {slug} - Task List", ""]

    for index, section in enumerate(sections):
        section_tasks = grouped.get(section, [])
        completed, total = section_progress(section_tasks)
        lines.append(f"## {section} ({completed} / {total})")
        for task in section_tasks:
            marker = display_marker(task)
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
                "## Attention",
                f"- Tasks missing verification instructions: {len(missing_verify_tasks)} "
                "(fill them in before execution)",
            ]
        )

    return "\n".join(lines)


def package_status_summary(
    slug: str,
    tasks_content: str,
    spec_content: str,
    checklist_content: str | None,
    sections: list[str],
    tasks: list[Task],
    *,
    checkpoint_status: CheckpointStatus | None = None,
    root: Path | None = None,
    package_dir: Path | None = None,
) -> dict[str, str]:
    total_tasks, completed_tasks = count_tasks(tasks_content)
    blocked_tasks = [task for task in tasks if task.is_blocked]
    active_tasks = [task.text for task in tasks if task.is_explicit_in_progress and not task.is_blocked]
    r = compute_gate_results(
        spec_content, tasks_content, checklist_content, slug=slug, root=root, package_dir=package_dir
    )
    pending_questions = list(r.human_questions)
    dependency_errors_list = list(r.task_contract_errors)
    # Same order as route_spec_package: definition gaps first, dependency
    # errors second; both outrank acceptance/execution steps.
    ordered_gate_gaps = [*r.spec_clarification_gaps, *dependency_errors_list]
    checkpoint_pending = bool(checkpoint_status and checkpoint_status.interrupted)
    standalone_blocked = bool(r.blocked_tasks) and not blocked_tasks

    state = overall_state(tasks, pending_questions, bool(ordered_gate_gaps), gate_passed=r.overall_ok)
    if r.blocked_tasks:
        state = worst_health(state, HEALTH_BLOCKED)
    if checkpoint_pending:
        # Advisory-level attention mirroring route; gates still refuse.
        state = worst_health(state, HEALTH_RISK)
    if r.orchestration_errors or r.question_errors or (r.all_tasks_done and not r.overall_ok):
        state = worst_health(state, HEALTH_RISK)

    first_error = r.orchestration_errors[0] if r.orchestration_errors else ""
    if blocked_tasks:
        blocked_label = blocked_tasks[0].text
    elif standalone_blocked:
        blocked_label = "tasks.md contains a standalone blocked status"
    else:
        blocked_label = ""
    dependency_label = dependency_errors_list[0] if dependency_errors_list else ""
    checkpoint_label = checkpoint_alert(checkpoint_status) if checkpoint_pending else ""
    definition_gaps = [gap for gap in ordered_gate_gaps if gap not in dependency_errors_list]
    acceptance_failed = (
        r.all_tasks_done
        and not r.overall_ok
        and not (
            checkpoint_pending
            or r.blocked_tasks
            or pending_questions
            or ordered_gate_gaps
            or dependency_errors_list
            or r.orchestration_errors
        )
    )
    return {
        "slug": slug,
        "title": extract_title_from_content(spec_content, slug),
        "progress": f"{completed_tasks}/{total_tasks}",
        "state": state,
        "primary": ", ".join(active_tasks) if active_tasks else "",
        "blocked": blocked_label,
        "pending": pending_questions[0] if pending_questions else "",
        "inquiry": r.autonomous_questions[0] if r.autonomous_questions else "",
        "question_error": r.question_errors[0] if r.question_errors else "",
        "dependency": dependency_label,
        "needs_work": "yes" if (definition_gaps or acceptance_failed) else "no",
        "gate_failed": "yes" if not r.overall_ok else "no",
        "orchestration": first_error,
        "verification_scope": r.verification_scope or "not declared in legacy package",
        "checkpoint": checkpoint_label,
        "acceptance": "acceptance failed" if acceptance_failed else "",
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
            title="No active projects",
            health=HEALTH_OK,
            next_step="Describe the goal so the skill can create tasks and start work",
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
    question_error_packages = 0
    gate_packages = 0
    failed_gate_packages = 0
    orchestration_packages = 0
    dependency_packages = 0
    checkpoint_packages = 0
    read_failure_packages = 0
    worst = HEALTH_OK
    primary_lines: list[str] = []
    for slug in packages:
        try:
            package_dir = resolve_specs_child(specs_root, "specs", slug)
            checkpoint_status = detect_update_checkpoint(package_dir)
        except (OSError, UnicodeError, ValueError) as exc:
            rows.append(f"- {slug}: failed to read status ({exc})")
            read_failure_packages += 1
            blocked_packages += 1
            worst = worst_health(worst, HEALTH_BLOCKED)
            continue
        try:
            spec_content = read_regular_text(package_dir / "spec.md")
            tasks_content = read_regular_text(package_dir / "tasks.md")
            # checklist.md is optional since v2 packages (F11).
            checklist_path = package_dir / "checklist.md"
            checklist_content: str | None = read_regular_text(checklist_path) if checklist_path.exists() else None
        except (OSError, UnicodeError, ValueError) as exc:
            if checkpoint_status.interrupted:
                alert = checkpoint_alert(checkpoint_status)
                rows.append(f"- {slug}: completed unknown; blocked: {alert}; task record unreadable")
                checkpoint_packages += 1
                blocked_packages += 1
                worst = worst_health(worst, HEALTH_BLOCKED)
                continue
            rows.append(f"- {slug}: failed to read status ({exc})")
            read_failure_packages += 1
            blocked_packages += 1
            worst = worst_health(worst, HEALTH_BLOCKED)
            continue
        sections, tasks = parse_tasks(tasks_content)
        title = extract_title_from_content(spec_content, slug)
        if not tasks:
            row = f"- {title}: no actionable tasks defined"
            if checkpoint_status.interrupted:
                row += f"; legacy: {checkpoint_alert(checkpoint_status)}"
                checkpoint_packages += 1
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
            root=root,
            package_dir=package_dir,
        )
        state = summary["state"]
        worst = worst_health(worst, state)
        # Join the user-supplied titles directly: replacing punctuation in the
        # summary would rewrite titles that legitimately contain it.
        active_titles = [task.text for task in tasks if task.is_explicit_in_progress and not task.is_blocked]
        active_display = ", ".join(active_titles) if active_titles else ""
        total_tasks, completed_tasks = count_tasks(tasks_content)
        done_sum += completed_tasks
        total_sum += total_tasks
        if summary["blocked"] or summary["checkpoint"]:
            blocked_packages += 1
        if summary["blocked"]:
            task_blocked_packages += 1
        if summary["checkpoint"]:
            checkpoint_packages += 1
        if summary["pending"]:
            pending_packages += 1
        if summary["question_error"]:
            question_error_packages += 1
        if summary["needs_work"] == "yes":
            gate_packages += 1
        if summary["gate_failed"] == "yes":
            failed_gate_packages += 1
        if summary["primary"]:
            primary_lines.append(f"{summary['title']}: {active_display}")
        row = f"- {summary['title']}: completed {summary['progress']}"
        if summary["primary"]:
            row += f"; current: {active_display}"
        if summary["checkpoint"]:
            row += f"; legacy: {summary['checkpoint']}"
        if summary["blocked"]:
            row += f"; blocked: {summary['blocked']}"
        if summary["pending"]:
            row += f"; pending confirmation: {summary['pending']}"
        if summary["inquiry"]:
            row += f"; autonomous inquiry: {summary['inquiry']}"
        if summary["question_error"]:
            row += f"; inquiry contract requirements not met: {summary['question_error']}"
        if summary["dependency"]:
            row += f"; task dependency error: {summary['dependency']}"
            dependency_packages += 1
        if summary["orchestration"]:
            row += f"; orchestration requirements not met: {summary['orchestration']}"
            orchestration_packages += 1
        if summary["acceptance"]:
            row += "; acceptance failed"
        scope = summary["verification_scope"]
        row += f"; scope: {scope}"
        brief = handoff_brief(package_dir, root, tasks_content)
        if brief:
            row += f"; {brief}"
        rows.append(row)

    detail = rows

    alerts = []
    if checkpoint_packages:
        alerts.append(
            f"{checkpoint_packages} projects have legacy update checkpoints (mechanism retired): "
            f"{checkpoint_recovery_guidance()}"
        )
    if read_failure_packages:
        alerts.append(f"{read_failure_packages} projects have unreadable status or no actionable tasks")
    if task_blocked_packages:
        alerts.append(f"{task_blocked_packages} projects have blocked tasks")
    if pending_packages:
        alerts.append(f"{pending_packages} projects need requirements confirmation")
    if question_error_packages:
        alerts.append(f"{question_error_packages} projects do not meet the inquiry contract")
    if gate_packages:
        alerts.append(f"{gate_packages} projects have definition or acceptance gaps")
    if orchestration_packages:
        alerts.append(f"{orchestration_packages} projects do not meet orchestration requirements")
    if dependency_packages:
        alerts.append(f"{dependency_packages} projects have task dependency errors")

    if checkpoint_packages:
        next_step = f"Resolve the legacy update checkpoint: {checkpoint_recovery_guidance()}"
    elif read_failure_packages:
        next_step = "Fix unreadable project records or add actionable tasks"
    elif task_blocked_packages:
        next_step = "Resolve blocked project tasks"
    elif question_error_packages:
        next_step = "Fix the inquiry contract"
    elif pending_packages:
        next_step = "Confirm project requirements"
    elif dependency_packages:
        next_step = "Fix project task dependencies"
    elif gate_packages:
        next_step = "Fix project definition or acceptance gaps"
    elif orchestration_packages:
        next_step = "Fix the project orchestration strategy"
    else:
        next_step = "Continue current project tasks"

    dash = Dashboard(
        slug="spec",
        stage="status",
        title="Active Projects",
        done=done_sum,
        total=total_sum,
        health=worst,
        metrics={
            "Blocked": str(blocked_packages),
            "Pending decisions": str(pending_packages),
            "Gate": f"Pending {failed_gate_packages}" if failed_gate_packages else "Passed",
            "Evidence": "n/a",
        },
        current_items=primary_lines,
        alerts=alerts,
        next_step=next_step,
        detail=detail,
        detail_title="Project Overview",
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
    for path in (spec_path, tasks_path):
        if not path.exists():
            if checkpoint_status is not None and checkpoint_status.interrupted:
                print(render_unreadable_checkpoint(slug, checkpoint_status))
                return 0
            print(f"error: required file missing: {path}", file=sys.stderr)
            return 1

    try:
        spec_content = read_regular_text(spec_path)
        tasks_content = read_regular_text(tasks_path)
        # checklist.md is optional since v2 packages (F11).
        checklist_content: str | None = read_regular_text(checklist_path) if checklist_path.exists() else None
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
            root=root,
            package_dir=spec_path.parent,
            handoff_line=handoff_brief(spec_path.parent, root, tasks_content),
        )
    else:
        output = render_tasks(slug, sections, tasks)

    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

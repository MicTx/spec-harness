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

UNREADABLE_HANDOFF = "handoff: unreadable (too large, non-UTF-8, or not a regular file); run spec_handoff.py validate"


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
        ready = set(r.ready_task_ids)
        inquiry = next((t.title for t in records if t.id in ready and t.id in r.autonomous_task_ids), None)
        current = inquiry or next((t.title for t in records if t.id in ready), None)
    except SpecControlError:
        records, ready, inquiry, current = [], set(), None, None
    return records, ready, inquiry, current


def _decide(root: Path, slug: str, package_dir: Path, spec: str, tasks_content: str, checklist: str | None) -> dict:
    """The one source of truth for alerts, stage, health, next action and projections."""
    r = compute_gate_results(spec, tasks_content, checklist, slug=slug, root=root, package_dir=package_dir)
    _, tasks = parse_tasks(tasks_content)
    checkpoint_pending = (package_dir / "update-checkpoint.json").exists()

    records, ready, inquiry_task, current_task = _task_records(r, tasks_content)
    alerts = [f"Task blocked: {record.title}" for record in records if record.blocked]
    if r.blocked_tasks and not alerts:
        alerts.append("Tasks are blocked; see the task list for details")
    if checkpoint_pending:
        alerts.insert(0, "Retired update-checkpoint.json remains; confirm no recovery is needed, then delete it")
    for prefix, items in (
        ("Requirements need confirmation", r.human_questions),
        ("Independent investigation", r.autonomous_questions),
        ("Investigation contract not satisfied", r.question_errors),
        ("Project definition incomplete", [project_gap_message(g) for g in r.spec_clarification_gaps]),
        ("Task dependencies not satisfied", r.task_contract_errors),
        ("Verification scope requirements not met", r.verification_scope_errors),
    ):
        alerts.extend(f"{prefix}: {item}" for item in items)

    if r.missing_boundary or r.missing_verify:
        gap = f"{r.missing_boundary} missing boundaries, {r.missing_verify} missing verification methods"
        alerts.append(f"Task instructions incomplete: {gap}")
    if not r.success_criteria_ok and (r.all_tasks_done or r.autonomous_questions):
        alerts.append(f"{r.success_criteria_total - r.success_criteria_checked} success criteria remain unmet")
    if r.branch_error:
        alerts.append(f"Branch binding: {r.branch_error}")
    if r.branch_error or r.question_errors:
        current_task, ready = None, set()
    no_ready = not ready and not r.all_tasks_done
    bad_tasks = r.invalid_verify or r.missing_boundary or r.missing_verify or r.task_contract_errors
    attention = bool(r.package_blocked or (no_ready and bad_tasks))
    if attention:
        reason = r.package_blocked or "invalid or incomplete task instructions"
        alerts.append(f"Recovery blocked: {reason}")
    scoping = bool(r.human_questions or r.question_errors or r.spec_clarification_gaps or r.branch_error or attention)
    if scoping:
        stage, health, pipe = "status", HEALTH_BLOCKED if r.blocked_tasks else HEALTH_RISK, "new"
    elif r.blocked_tasks:
        stage, health, pipe = ("run" if ready or r.autonomous_unbound_questions else "status"), HEALTH_BLOCKED, "run"
    elif r.all_tasks_done and r.overall_ok:
        stage, health, pipe = "done", HEALTH_OK, "done"
    elif r.all_tasks_done and r.autonomous_questions:
        stage, health, pipe = "run", HEALTH_RISK, "run"
    else:
        stage, health, pipe = ("check", HEALTH_RISK, "check") if r.all_tasks_done else ("run", HEALTH_OK, "run")
    if checkpoint_pending:
        health = worst_health(health, HEALTH_RISK)
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
    for errors, template, is_project_gap in (
        ([r.branch_error] if r.branch_error else [], "Switch to the package branch: {}", False),
        (r.authority_question_errors, "Confirm requirements: {}", False),
        (r.question_errors, "Fix investigation contract: {}", False),
        (r.human_questions, "Confirm requirements: {}", False),
        (r.spec_clarification_gaps, "Complete the project definition: {}", True),
        (r.task_contract_errors, "Fix task dependencies: {}", False),
        ([r.package_blocked] if r.package_blocked else [], "Resolve blocked package: {}", False),
        (r.invalid_verify if not r.ready_tasks else [], "Fix invalid verify: {}", False),
        (
            ["incomplete task instructions"] if not r.ready_tasks and (r.missing_boundary or r.missing_verify) else [],
            "Fix {}",
            False,
        ),
    ):
        if errors:
            item = project_gap_message(errors[0]) if is_project_gap else errors[0]
            return template.format(item)
    if current_task or r.autonomous_unbound_questions:
        prefix = "" if current_task and not inquiry_task else "Investigate independently: "
        return f"{prefix}{current_task or r.autonomous_unbound_questions[0]}"
    if r.blocked_tasks:
        return next((f"Unblock task: {task.text}" for task in tasks if task.is_blocked), "Resolve blocked tasks")
    if r.all_tasks_done:
        return "Prepare delivery and archive the record" if r.overall_ok else "Fix failed acceptance checks"
    return "Continue project tasks"


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
    try:
        content = read_handoff(package_dir)
    except (OSError, UnicodeError, ValueError):
        return {"line": UNREADABLE_HANDOFF, "fresh": "unreadable"}
    if content is None:
        return {"line": None, "fresh": "absent"}
    if validate_handoff(content):
        return {"line": "handoff: invalid structure; run spec_handoff.py validate", "fresh": "unknown"}
    doc = parse_handoff(content)
    if doc.latest is None:
        return {"line": "handoff: no entries; run spec_handoff.py validate", "fresh": "unknown"}
    label, detail = freshness(doc, root, tasks_content)
    view = {"line": f"handoff: latest {doc.latest.timestamp} ({label}, {doc.latest.collab})", "fresh": label}
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
            "verificationScope": r.verification_scope,
            "humanQuestions": r.human_questions,
            "autonomousQuestions": r.autonomous_questions,
            "questionErrors": r.question_errors,
            "branchError": r.branch_error,
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
    gate_gap_total = len(r.spec_clarification_gaps) + r.missing_boundary + r.missing_verify
    gate_gap_total += r.success_criteria_total - r.success_criteria_checked
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
            "Blocked": "1" if r.blocked_tasks else "0",
            "Pending decisions": str(len(r.human_questions)),
            "Gate": "passed" if r.overall_ok else f"{gate_gap_total} gaps",
            "Evidence": str(r.evidence_filled_count),
            "Scope": r.verification_scope or "",
        },
        alerts=v["alerts"],
        next_step=v["next_action"],
        detail=[handoff["line"]] if handoff["line"] else [],
        detail_title="Handoff" if handoff["line"] else "Delivery Details",
        ascii_mode=ascii_mode,
        compact=compact,
    ).render()


def _aggregate(args: argparse.Namespace, stage: str, title: str, health: str, step: str, **extra) -> str:
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
        print("error: --brief implies JSON output and cannot be combined with --format markdown", file=sys.stderr)
        return 1
    args.format = args.format or ("json" if args.brief else "markdown")
    try:
        specs_root = resolve_specs_root(root, args.specs_dir)
        specs_dir = resolve_specs_child(specs_root, "specs")
        slug = args.slug.strip() if args.slug else None
        if slug is None:
            packages = active_packages(specs_dir)
            if not packages:
                message = "Describe the goal; the skill will create tasks and begin work"
                print(_aggregate(args, "new", "No active project", HEALTH_OK, message))
                return 0
            slug = (selection := select_active_package(root, specs_root, packages)).slug
            if not slug:
                warning = selection.warning or f"Cannot select a unique active task package: {', '.join(packages)}"
                print(
                    _aggregate(
                        args,
                        "status",
                        "Task package branch is not bound",
                        HEALTH_RISK,
                        "Switch to the package's integration branch, or pass --slug for read-only inspection",
                        alerts=[warning],
                        metrics={"Gate": "branch binding"},
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

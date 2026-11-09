#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Six delivery checks, shared by the CLI, completion and status views."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dashboard_support import HEALTH_BLOCKED, HEALTH_OK, HEALTH_RISK, Dashboard, task_strip_from_records
from spec_package_support import (
    SpecControlError,
    add_specs_dir_arg,
    autonomous_question_contract,
    checklist_complete,
    clarification_gaps,
    count_task_detail_gaps,
    count_tasks,
    dependency_errors,
    extract_pending_questions,
    extract_title_from_content,
    parse_task_records,
    ready_task_ids,
    resolve_specs_child,
    resolve_specs_root,
    section_checkboxes_by_prefix,
    spec_format_version,
    validate_branch_bound_package,
    validate_slug,
    verification_scope,
    verification_scope_errors,
    workflow_ledger_errors,
)
from spec_verify import classify_verify, duplicate_verify_error, evaluate_runs_gate, runs_protocol_enabled


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--slug", required=True)
    add_specs_dir_arg(parser)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--ascii", action="store_true")
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args()


def parse_tasks(tasks_content: str) -> tuple[int, int, int, int]:
    return (*count_tasks(tasks_content), *count_task_detail_gaps(tasks_content))


def package_blocker(tasks_content: str) -> str:
    """Unowned blocker lines are global; indented task details belong to that task."""
    in_task = False
    for line in tasks_content.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            in_task = False
        elif re.match(r"-\s*\[.\]\s+", stripped):
            in_task = True
        elif re.match(r"(?i)(?:-\s*)?(?:blocked|阻塞)[：:]", stripped):
            if not in_task or not line[:1].isspace():
                return stripped
    return ""


def has_blocked_tasks(tasks_content: str) -> bool:
    return bool(
        package_blocker(tasks_content)
        or re.search(r"(?mi)^\s*-\s*\[!\]|^\s*-\s*\[[ >]\]\s*(?:blocked|阻塞)[：:]", tasks_content)
    )


def is_likely_template(tasks_content: str) -> bool:
    return bool(re.search(r"核心功能 A|core feature A|test_core_feature_a|test_critical_path", tasks_content, re.I))


@dataclass(frozen=True)
class GateResults:
    total_tasks: int = 0
    completed_tasks: int = 0
    missing_boundary: int = 0
    missing_verify: int = 0
    blocked_tasks: bool = False
    package_blocked: str = ""
    invalid_verify: list[str] = field(default_factory=list)
    template_warning: bool = False
    spec_clarification_gaps: list[str] = field(default_factory=list)
    task_contract_errors: list[str] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)
    human_questions: list[str] = field(default_factory=list)
    autonomous_questions: list[str] = field(default_factory=list)
    autonomous_task_ids: list[str] = field(default_factory=list)
    autonomous_unbound_questions: list[str] = field(default_factory=list)
    question_errors: list[str] = field(default_factory=list)
    authority_question_errors: list[str] = field(default_factory=list)
    autonomous_question_errors: list[str] = field(default_factory=list)
    verification_scope: str | None = None
    verification_scope_errors: list[str] = field(default_factory=list)
    success_criteria_total: int = 0
    success_criteria_checked: int = 0
    ready_tasks: int = 0
    ready_task_ids: frozenset[str] = frozenset()
    runs_protocol: bool = False
    runs_gate_ok: bool | None = None
    runs_errors: list[str] = field(default_factory=list)
    runs_detail: str = ""
    branch_error: str = ""
    advisories: list[str] = field(default_factory=list)

    @property
    def all_tasks_done(self) -> bool:
        return self.total_tasks > 0 and self.total_tasks == self.completed_tasks and not self.blocked_tasks

    @property
    def success_criteria_ok(self) -> bool:
        return self.success_criteria_total > 0 and self.success_criteria_total == self.success_criteria_checked

    @property
    def overall_ok(self) -> bool:
        return all(passed for _label, passed, _detail in gate_rows(self, None) if passed is not None)

    @property
    def orchestration_errors(self) -> list[str]:
        return []  # Routing hints never gate delivery.

    @property
    def evidence_filled_count(self) -> int:
        return int(self.runs_gate_ok is True)


def compute_gate_results(
    spec_content: str,
    tasks_content: str,
    checklist_content: str | None = None,
    slug: str | None = None,
    root: Path | None = None,
    package_dir: Path | None = None,
    *,
    archived_root: Path | None = None,
) -> GateResults:
    total, completed = count_tasks(tasks_content)
    blocker = package_blocker(tasks_content)
    try:
        records = parse_task_records(tasks_content)
        missing_boundary = sum(not record.boundary for record in records)
        missing_verify = sum(not record.verify for record in records)
        contract_errors = dependency_errors(records)
        ready = ready_task_ids(records) if not contract_errors else set()
        invalid_verify = [
            record.id
            for record in records
            if runs_protocol_enabled(spec_content)
            and (not classify_verify(record.verify) or duplicate_verify_error(record.id, record.block_text))
        ]
        ready -= set(invalid_verify)
    except SpecControlError as exc:
        records, contract_errors, ready, invalid_verify = [], [str(exc)], set(), []
        missing_boundary = missing_verify = 0
    invalid_records = bool(contract_errors and not records)
    if blocker:
        ready.clear()
    questions, human, question_errors = autonomous_question_contract(spec_content, tasks_content)
    authority_errors = [f"{title}: user authority or answer required" for title in human] if question_errors else []
    answer_errors = [f"{item.text}: closing requires answer: <conclusion>" for item in questions if item.closed]
    autonomous_errors = [error for error in question_errors if error in answer_errors]
    known = {record.id for record in records}
    if not invalid_records:
        binding_errors = [
            f"{item.text}: unknown inquiry task {item.task}"
            for item in questions
            if not item.closed and item.task and item.task not in known
        ]
        question_errors.extend(binding_errors)
        autonomous_errors.extend(binding_errors)
    completed_ids = {record.id for record in records if record.checked}
    pending = extract_pending_questions(spec_content)
    success_total, success_checked = section_checkboxes_by_prefix(spec_content, "## 6.")
    # Only legacy packages can replace an empty §6 with their old checklist.
    if not success_total and checklist_content and spec_format_version(spec_content) < 2:
        success_total = 1
        success_checked = int(checklist_complete(checklist_content))
    scope_errors = verification_scope_errors(spec_content)
    definition_errors = clarification_gaps(spec_content)
    definition_errors.extend(workflow_ledger_errors(spec_content, tasks_content))
    definition_errors.extend(scope_errors)
    runs_ok, runs_errors, runs_detail = evaluate_runs_gate(spec_content, tasks_content, package_dir, root)
    if invalid_records and runs_ok is False:
        runs_errors, runs_detail = contract_errors, "Task contract prevents evidence evaluation"
    branch_error = ""
    if root is not None and slug is not None:
        bound, detail = validate_branch_bound_package(root, slug, spec_content, allow_legacy_convention=True)
        if not bound:
            branch_error = detail
    return GateResults(
        total_tasks=total,
        completed_tasks=completed,
        missing_boundary=missing_boundary,
        missing_verify=missing_verify,
        blocked_tasks=bool(blocker) or has_blocked_tasks(tasks_content) or any(record.blocked for record in records),
        package_blocked=blocker,
        invalid_verify=invalid_verify,
        template_warning=is_likely_template(tasks_content),
        spec_clarification_gaps=definition_errors,
        task_contract_errors=contract_errors,
        pending_questions=pending,
        human_questions=human,
        autonomous_questions=[item.text for item in questions if not item.closed],
        autonomous_task_ids=[item.task for item in questions if not item.closed and item.task],
        autonomous_unbound_questions=[
            item.text for item in questions if not item.closed and (not item.task or item.task in completed_ids)
        ],
        question_errors=question_errors,
        authority_question_errors=authority_errors,
        autonomous_question_errors=autonomous_errors,
        verification_scope=verification_scope(spec_content),
        verification_scope_errors=scope_errors,
        success_criteria_total=success_total,
        success_criteria_checked=success_checked,
        ready_tasks=len(ready),
        ready_task_ids=frozenset(ready),
        runs_protocol=runs_protocol_enabled(spec_content),
        runs_gate_ok=runs_ok,
        runs_errors=runs_errors,
        runs_detail=runs_detail,
        branch_error=branch_error,
    )


def gate_rows(r: GateResults, slug: str | None = None) -> list[tuple[str, bool | None, str]]:
    task_errors = [*r.task_contract_errors]
    if not r.all_tasks_done:
        task_errors.append(f"tasks complete {r.completed_tasks}/{r.total_tasks}")
    if r.missing_boundary or r.missing_verify:
        task_errors.append(f"missing boundary: {r.missing_boundary}; missing verify: {r.missing_verify}")
    return [
        (
            "Project definition",
            not r.spec_clarification_gaps and not r.template_warning,
            "; ".join(r.spec_clarification_gaps)
            or ("tasks are still the initialization template" if r.template_warning else "filled"),
        ),
        ("Tasks and boundaries", not task_errors, "; ".join(task_errors) or "complete"),
        (
            "Open questions",
            not r.pending_questions and not r.question_errors,
            "; ".join([*r.pending_questions, *r.question_errors]) or "closed",
        ),
        (
            "Success criteria",
            r.success_criteria_ok,
            f"[SPEC-SUCCESS-001] success criteria {r.success_criteria_checked}/{r.success_criteria_total}",
        ),
        ("Runs evidence", r.runs_gate_ok, "; ".join(r.runs_errors) or r.runs_detail),
        ("Branch binding", not r.branch_error, r.branch_error or "bound, or not a Git project"),
    ]


def gate_failure_details(r: GateResults, slug: str | None = None) -> dict[str, str]:
    return {label: detail for label, passed, detail in gate_rows(r, slug) if passed is False}


def gate_recovery_routes(r: GateResults) -> dict[str, str]:
    """Associate each failed gate with advisory owners; never grant authority."""
    failures = gate_failure_details(r)
    routes: dict[str, set[str]] = {}

    def put(gate: str, stage: str) -> None:
        if gate in failures:
            routes.setdefault(gate, set()).add(stage)

    if r.spec_clarification_gaps or r.template_warning:
        put("Project definition", "update")
    if r.task_contract_errors or r.missing_boundary or r.missing_verify or not r.total_tasks:
        put("Tasks and boundaries", "update")
    if r.blocked_tasks:
        put("Tasks and boundaries", "environment")
    if r.invalid_verify and not r.ready_tasks and not r.all_tasks_done:
        put("Tasks and boundaries", "update")
    if r.ready_tasks and not r.branch_error and not r.package_blocked:
        put("Tasks and boundaries", "run")
    if r.human_questions:
        put("Open questions", "authority")
    suspended = r.branch_error or r.package_blocked
    if r.autonomous_questions:
        put("Open questions", "environment" if suspended else "run")
    if r.autonomous_question_errors:
        put("Open questions", "update")
    if not r.success_criteria_ok:
        put("Success criteria", "update" if not r.success_criteria_total else "environment" if suspended else "run")
    for error in r.runs_errors:
        if error in r.task_contract_errors:
            put("Runs evidence", "update")
        elif error.startswith("[SPEC-EVID-003]"):
            put("Runs evidence", "environment")
        elif error.startswith("[SPEC-VERIFY-002]") or error.endswith(
            "verify requires a backtick command or a manual: reason"
        ):
            put("Runs evidence", "update")
        else:
            put("Runs evidence", "environment" if suspended else "run")
    if r.branch_error:
        put("Branch binding", "environment")
    if "Tasks and boundaries" in failures and "Tasks and boundaries" not in routes:
        put("Tasks and boundaries", "environment")
    order = ("update", "run", "environment", "authority")
    return {gate: "+".join(stage for stage in order if stage in stages) for gate, stages in routes.items()}


def convergence_state(results: GateResults) -> tuple[bool, list[str]]:
    gaps = list(gate_failure_details(results).values())
    return not gaps, gaps


def convergence_lines(
    converged: bool, gaps: list[str], recovery: dict[str, str] | None = None, *, compact: bool = False
) -> list[str]:
    return [
        f"- Convergence: {'converged' if converged else 'not converged'}",
        *(f"- Gap: {gap}" for gap in gaps),
        *(f"- Recovery: {gate} -> {stage}" for gate, stage in (recovery or {}).items()),
    ]


def overall_check_passed(
    spec_content: str,
    tasks_content: str,
    checklist_content: str | None = None,
    slug: str | None = None,
    root: Path | None = None,
    package_dir: Path | None = None,
    **kwargs,
) -> bool:
    results = compute_gate_results(spec_content, tasks_content, checklist_content, slug, root, package_dir, **kwargs)
    return results.overall_ok


def package_physical_state_errors(package_dir: Path) -> list[str]:
    return []  # A retired update checkpoint is a doctor advisory, not a second delivery state.


def result_label(passed: bool, partial: bool = False) -> str:
    return "pass" if passed else "partial pass" if partial else "needs fixes"


def render(
    slug: str,
    title: str,
    spec_content: str,
    tasks_content: str,
    checklist_content: str | None = None,
    results: GateResults | None = None,
    *,
    ascii_mode: bool = False,
    compact: bool = False,
    archived: bool = False,
) -> str:
    r = results or compute_gate_results(spec_content, tasks_content, checklist_content, slug)
    failures = gate_failure_details(r, slug)
    try:
        records = parse_task_records(tasks_content)
        ready = next((record.title for record in records if record.id in r.ready_task_ids), None)
        strip, strip_labels = task_strip_from_records(records)
    except SpecControlError:
        ready, strip, strip_labels = None, [], []
    next_step = ready or (
        "Finalize the delivery record and archive" if r.overall_ok else "Fix the failing acceptance items"
    )
    if r.branch_error:
        next_step = f"Switch to the package branch: {r.branch_error}"
    elif r.package_blocked:
        next_step = f"Resolve package blocker: {r.package_blocked}"
    dash = Dashboard(
        slug=slug,
        title=title,
        stage="check",
        done=r.completed_tasks,
        total=r.total_tasks,
        health=HEALTH_BLOCKED if r.blocked_tasks else HEALTH_OK if r.overall_ok else HEALTH_RISK,
        strip=strip,
        strip_labels=strip_labels,
        metrics={"Scope": r.verification_scope or "", "Evidence": str(r.evidence_filled_count)},
        alerts=[f"{label}: {detail}" for label, detail in failures.items()],
        next_step=next_step,
        detail=["Package state: archived (frozen)"] if archived else [],
        compact=compact,
        ascii_mode=ascii_mode,
    )
    rendered = dash.render()
    lines = convergence_lines(*convergence_state(r), gate_recovery_routes(r), compact=compact)
    return rendered + "\n" + "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    try:
        slug = validate_slug(args.slug)
        specs = resolve_specs_root(root, args.specs_dir)
        package = resolve_specs_child(specs, "specs", slug)
        if not package.exists():
            archived = resolve_specs_child(specs, "specs", "archive", slug)
            if archived.is_dir():
                payload = dict(
                    slug=slug, overall=True, converged=True, gaps=[], gateFailures={}, recovery={}, archived=True
                )
                print(
                    json.dumps(payload, ensure_ascii=False) if args.format == "json" else f"{slug}: archived (frozen)"
                )
                return 0
        spec = (package / "spec.md").read_text(encoding="utf-8")
        tasks = (package / "tasks.md").read_text(encoding="utf-8")
        checklist_path = package / "checklist.md"
        checklist = checklist_path.read_text(encoding="utf-8") if checklist_path.exists() else None
        results = compute_gate_results(spec, tasks, checklist, slug, root, package)
    except (OSError, ValueError, SpecControlError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    converged, gaps = convergence_state(results)
    if args.format == "json":
        print(
            json.dumps(
                {
                    "slug": slug,
                    "overall": results.overall_ok,
                    "converged": converged,
                    "gaps": gaps,
                    "overallGaps": gaps,
                    "gateFailures": gate_failure_details(results),
                    "recovery": gate_recovery_routes(results),
                    "verificationScope": results.verification_scope,
                    "openDecisions": results.human_questions,
                    "tasksDone": results.all_tasks_done,
                    "archived": False,
                },
                ensure_ascii=False,
            )
        )
    else:
        title = extract_title_from_content(spec, slug)
        print(render(slug, title, spec, tasks, checklist, results, ascii_mode=args.ascii, compact=args.compact))
    return 0 if results.overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())

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
    resolve_specs_child,
    resolve_specs_root,
    section_checkboxes_by_prefix,
    validate_branch_bound_package,
    validate_slug,
    verification_scope,
    verification_scope_errors,
    workflow_ledger_errors,
)
from spec_verify import evaluate_runs_gate, runs_protocol_enabled


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


def has_blocked_tasks(tasks_content: str) -> bool:
    return bool(re.search(r"(?mi)^\s*-\s*\[!\]|^\s*(?:-\s*)?(?:blocked|阻塞)[：:]", tasks_content))


def is_likely_template(tasks_content: str) -> bool:
    return bool(re.search(r"核心功能 A|core feature A|test_core_feature_a|test_critical_path", tasks_content, re.I))


@dataclass(frozen=True)
class GateResults:
    total_tasks: int = 0
    completed_tasks: int = 0
    missing_boundary: int = 0
    missing_verify: int = 0
    blocked_tasks: bool = False
    template_warning: bool = False
    spec_clarification_gaps: list[str] = field(default_factory=list)
    task_contract_errors: list[str] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)
    human_questions: list[str] = field(default_factory=list)
    autonomous_questions: list[str] = field(default_factory=list)
    autonomous_task_ids: list[str] = field(default_factory=list)
    question_errors: list[str] = field(default_factory=list)
    verification_scope: str | None = None
    verification_scope_errors: list[str] = field(default_factory=list)
    success_criteria_total: int = 0
    success_criteria_checked: int = 0
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
    total, completed, missing_boundary, missing_verify = parse_tasks(tasks_content)
    try:
        contract_errors = dependency_errors(parse_task_records(tasks_content))
    except SpecControlError as exc:
        contract_errors = [str(exc)]
    questions, human, question_errors = autonomous_question_contract(spec_content, tasks_content)
    pending = extract_pending_questions(spec_content)
    success_total, success_checked = section_checkboxes_by_prefix(spec_content, "## 6.")
    # N-1 packages remain readable until the migration script's removal release.
    if not success_total and checklist_content:
        success_total = 1
        success_checked = int(checklist_complete(checklist_content))
    scope_errors = verification_scope_errors(spec_content)
    definition_errors = clarification_gaps(spec_content)
    definition_errors.extend(workflow_ledger_errors(spec_content, tasks_content))
    definition_errors.extend(scope_errors)
    runs_ok, runs_errors, runs_detail = evaluate_runs_gate(spec_content, tasks_content, package_dir, root)
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
        blocked_tasks=has_blocked_tasks(tasks_content),
        template_warning=is_likely_template(tasks_content),
        spec_clarification_gaps=definition_errors,
        task_contract_errors=contract_errors,
        pending_questions=pending,
        human_questions=human,
        autonomous_questions=[item.text for item in questions if not item.closed],
        autonomous_task_ids=[item.task for item in questions if not item.closed and item.task],
        question_errors=question_errors,
        verification_scope=verification_scope(spec_content),
        verification_scope_errors=scope_errors,
        success_criteria_total=success_total,
        success_criteria_checked=success_checked,
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


def convergence_state(results: GateResults) -> tuple[bool, list[str]]:
    gaps = list(gate_failure_details(results).values())
    return not gaps, gaps


def convergence_lines(converged: bool, gaps: list[str], *, compact: bool = False) -> list[str]:
    return [
        f"- Convergence: {'converged' if converged else 'not converged'}",
        *(f"- Gap: {gap}" for gap in gaps),
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
    ready = next((record.title for record in parse_task_records(tasks_content) if not record.checked), None)
    try:
        strip, strip_labels = task_strip_from_records(parse_task_records(tasks_content))
    except SpecControlError:
        strip, strip_labels = [], []
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
        next_step=ready
        or ("Finalize the delivery record and archive" if r.overall_ok else "Fix the failing acceptance items"),
        detail=["Package state: archived (frozen)"] if archived else [],
        compact=compact,
        ascii_mode=ascii_mode,
    )
    return dash.render() + "\n" + "\n".join(convergence_lines(*convergence_state(r), compact=compact))


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
                payload = {"slug": slug, "overall": True, "converged": True, "gaps": [], "archived": True}
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
                    "verificationScope": results.verification_scope,
                    "openDecisions": results.human_questions,
                    "tasksDone": results.all_tasks_done,
                    "archived": False,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(
            render(
                slug,
                extract_title_from_content(spec, slug),
                spec,
                tasks,
                checklist,
                results,
                ascii_mode=args.ascii,
                compact=args.compact,
            )
        )
    return 0 if results.overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())

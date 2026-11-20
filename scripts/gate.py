"""Final delivery gates: frozen plan, tasks, evidence, criteria and boundaries."""

from __future__ import annotations

from pathlib import Path

import gitx
import ui_review
from evidence import evaluate_runs
from package import (
    Task,
    amend_authorized,
    append_run,
    changed_paths,
    criteria_status,
    dependency_errors,
    extract_branch,
    goal_fingerprint,
    now,
    package_baseline,
    parse_charter,
    parse_ledger,
    parse_tasks,
    path_matches,
    read_package,
    runs,
    tree_fingerprint,
)


def boundary_audit(root: Path, slug: str) -> list[str]:
    package, spec, tasks = read_package(root, slug)
    records = parse_tasks(tasks)
    baseline = next(
        (
            record
            for record in reversed(runs(package))
            if record.get("kind") == "baseline" and record.get("task") == "package"
        ),
        {},
    )
    preexisting = set(baseline.get("dirty", []))
    patterns = [pattern for record in records for pattern in record.globs]
    base = baseline.get("head", "") or gitx.head(root)
    errors = []
    for path in changed_paths(root, base):
        if path.startswith(".spec/") or path in preexisting:
            continue
        if not any(path_matches(path, pattern) for pattern in patterns):
            errors.append(f"changed path outside all task boundaries: {path}")
    return errors


def _command_table(package: Path, task_ids: list[str]) -> dict[str, str]:
    """Rebuild the command table accepted at plan-go.

    plan-go's recording lint appends one ``kind: baseline`` record per machine
    task. Filtering by the package baseline's ``task_ids`` drops records that
    belong to no frozen card, and the last record per task wins so a corrected
    round supersedes a failed earlier round's command.
    """
    known = set(task_ids)
    accepted: dict[str, str] = {}
    for record in runs(package):
        task = record.get("task")
        if record.get("kind") != "baseline" or not isinstance(task, str) or task not in known:
            continue
        accepted[task] = str(record.get("cmd") or "")
    return accepted


def _amended_commands(package: Path, task_id: str, v2: str) -> set[str]:
    """Commands a ``kind: amend`` record authorizes for *task_id*.

    The empty string marks an authorization that names no command (a
    hand-written record); it authorizes whatever the card now carries.
    """
    authorized: set[str] = set()
    for record in runs(package):
        if record.get("kind") != "amend" or record.get("task") != task_id:
            continue
        if amend_authorized(str(record.get("by") or ""), v2):
            authorized.add(str(record.get("cmd") or ""))
    return authorized


def freeze_errors(package: Path, spec: str, records: list[Task]) -> list[str]:
    """Post-Go plan integrity: frozen Goal, frozen task set, frozen commands.

    Historical v3 archives predate ``goal_fp`` and the package baseline's
    ``task_ids``; when either is missing the whole block stays silent instead
    of re-judging history.
    """
    baseline = package_baseline(package)
    goal_fp = str(baseline.get("goal_fp") or "")
    task_ids = [str(item) for item in baseline.get("task_ids") or []]
    if not goal_fp or not task_ids:
        return []
    errors = []
    if goal_fingerprint(spec) != goal_fp:
        errors.append("problem definition changed after plan-go")
    current = {record.id for record in records}
    errors.extend(
        f"{task_id}: removed after plan-go; open a new package" for task_id in task_ids if task_id not in current
    )
    accepted = _command_table(package, task_ids)
    v2 = parse_charter(spec).get("V2", "strict")
    frozen = set(task_ids)
    for task in records:
        if task.id not in frozen or not task.command:
            continue
        if accepted.get(task.id, task.command) == task.command:
            continue
        allowed = _amended_commands(package, task.id, v2)
        if task.command in allowed or "" in allowed:
            continue
        if v2 == "strict":
            errors.append(f"{task.id}: verify changed after plan-go")
        else:
            errors.append(f"{task.id}: use plan-amend --revise")
    return errors


def compute(root: Path, slug: str, final: bool = False) -> dict:
    package, spec, tasks = read_package(root, slug)
    errors = []
    has_git = gitx.toplevel(root) is not None
    fingerprint = tree_fingerprint(root) if has_git else None
    ui_status = ui_review.review_status(
        root,
        slug,
        spec=spec,
        tree_fp=fingerprint,
        tree_required=has_git,
    )
    errors.extend(f"UI review: {item}" for item in ui_status["errors"])
    if "spec-format: 3" not in spec:
        errors.append("only v3 packages are supported")
    if not package_baseline(package):
        errors.append("package has not passed plan-go")
    try:
        records = parse_tasks(tasks)
        errors.extend(dependency_errors(records))
        incomplete = [record.id for record in records if not record.checked and not record.blocked]
        errors.extend(f"unfinished task: {task_id}" for task_id in incomplete)
        errors.extend(f"blocked task: {record.id}" for record in records if record.blocked)
        errors.extend(freeze_errors(package, spec, records))
    except ValueError as exc:
        errors.append(str(exc))
    errors.extend(evaluate_runs(root, slug, final=final, tree_fp=fingerprint if has_git else None))
    errors.extend(
        f"criterion not proven: {key}" for key, _text, _task, passed in criteria_status(spec, package) if not passed
    )
    errors.extend(boundary_audit(root, slug))
    try:
        branch = extract_branch(spec)
        if gitx.branch(root) != branch:
            errors.append(f"branch binding: expected {branch}")
    except ValueError as exc:
        errors.append(str(exc))
    parked = parse_ledger(spec, "Parked", "P")
    return {
        "passed": not errors and not parked,
        "errors": errors,
        "parked": parked,
        "ui_review": ui_status,
    }


def record(root: Path, slug: str, result: dict) -> dict:
    """Persist one check result so the next route can resume from its failure."""
    package, _spec, _tasks = read_package(root, slug)
    entry = {
        "kind": "gate",
        "passed": bool(result.get("passed")),
        "errors": [str(item) for item in result.get("errors", [])],
        "parked": [str(item.get("id", "")) for item in result.get("parked", []) if isinstance(item, dict)],
        "head": gitx.head(root),
        "ts": now(),
    }
    append_run(package, entry)
    return entry


def gate_rows(root: Path, slug: str, final: bool = False) -> list[dict]:
    result = compute(root, slug, final)
    return [{"gate": "delivery", "passed": result["passed"], "detail": "; ".join(result["errors"]) or "passed"}]

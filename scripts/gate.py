"""Final delivery gates: frozen plan, tasks, evidence, criteria and boundaries."""

from __future__ import annotations

from pathlib import Path

import gitx
from evidence import evaluate_runs
from package import (
    changed_paths,
    criteria_status,
    dependency_errors,
    extract_branch,
    parse_ledger,
    parse_tasks,
    path_matches,
    read_package,
    runs,
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


def compute(root: Path, slug: str, final: bool = False) -> dict:
    package, spec, tasks = read_package(root, slug)
    errors = []
    if "spec-format: 3" not in spec:
        errors.append("only v3 packages are supported")
    try:
        records = parse_tasks(tasks)
        errors.extend(dependency_errors(records))
        incomplete = [record.id for record in records if not record.checked and not record.blocked]
        errors.extend(f"unfinished task: {task_id}" for task_id in incomplete)
        errors.extend(f"blocked task: {record.id}" for record in records if record.blocked)
    except ValueError as exc:
        errors.append(str(exc))
    errors.extend(evaluate_runs(root, slug, final=final))
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
    return {"passed": not errors and not parked, "errors": errors, "parked": parked}


def gate_rows(root: Path, slug: str, final: bool = False) -> list[dict]:
    result = compute(root, slug, final)
    return [{"gate": "delivery", "passed": result["passed"], "detail": "; ".join(result["errors"]) or "passed"}]

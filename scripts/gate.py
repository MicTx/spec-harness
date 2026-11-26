"""Delivery verdict, structured findings and the gate ledger."""

from __future__ import annotations

import os
from pathlib import Path

import gitx
import local_evidence
from package import (
    append_run,
    changed_paths,
    criteria,
    dependency_errors,
    extract_branch,
    foreign_index_paths,
    latest_gate,
    now,
    package_baseline,
    package_kind,
    parse_tasks,
    path_matches,
    pending_parked,
    product_delta,
    read_package,
    record_digest,
    refresh_product_tree,
    runs,
    task_records,
    tree_fingerprint,
    verify_groups,
)
from package import frozen_commands as freeze_errors


def _outside(paths, patterns) -> list[str]:
    return [
        path
        for path in paths
        if (not path.startswith(".spec/") or path.startswith(".spec/architecture/"))
        and not any(path_matches(path, pattern) for pattern in patterns)
    ]


def boundary_audit(root: Path, slug: str) -> list[str]:
    directory, _, tasks = read_package(root, slug)
    baseline = package_baseline(directory)
    patterns = [pattern for task in parse_tasks(tasks) for pattern in task.globs]
    paths = set(changed_paths(root, baseline.get("head") or gitx.head(root))) - set(baseline.get("dirty", []))
    return [f"changed path outside all task boundaries: {path}" for path in _outside(sorted(paths), patterns)]


def finding(
    code: str, detail: str, subject: str = "package", *, owner="agent", action="inspect", severity="block", **data
):
    return dict(code=code, detail=detail, subject=subject, owner=owner, action=action, severity=severity, **data)


def add(result: dict, *items: dict) -> dict:
    result["findings"].extend(items)
    result["errors"] = [item["detail"] for item in result["findings"] if item["severity"] == "block"]
    result["passed"] = not result["errors"] and not result["parked"]
    return result


def _quick_findings(root: Path, baseline: dict, records: list, snapshot: dict) -> list[dict]:
    base = baseline["head"]
    if gitx.head(root) != base or gitx.git(root, "diff", "--cached", "--quiet", check=False).returncode:
        return [finding("quick-index", "keep the original HEAD and an unstaged index", action="isolate-change")]
    allowed = {path.removeprefix("new:") for task in records for path in task.globs}
    changed = {os.fsdecode(name) for name in product_delta(root, snapshot, base)}
    if not changed:
        return [finding("quick-no-change", "make the scoped change before checking", action="run-task")]
    outside = changed - allowed
    for name in changed & allowed:
        path = root / name
        if path.resolve() != path or path.exists() and not path.is_file():
            outside.add(name)
    return [
        finding("quick-boundary", f"not a scoped regular-file change: {name}", name, action="isolate-change")
        for name in sorted(outside)
    ]


def compute(root: Path, slug: str, *, fingerprint: str | None = None, snapshot: dict | None = None) -> dict:
    directory, spec, tasks = read_package(root, slug)
    baseline = package_baseline(directory)
    quick = baseline.get("finish") == "worktree"
    has_git = gitx.toplevel(root) is not None
    if quick and has_git and snapshot is None:
        with gitx.temporary_index(root) as env:
            _, current = refresh_product_tree(root, env)
            return compute(root, slug, fingerprint=current, snapshot=env)
    if fingerprint is None and has_git:
        fingerprint = tree_fingerprint(root)
    parked = pending_parked(directory, spec)
    result = dict(findings=[], parked=parked, tree_fp=fingerprint, verification="pending")
    items = []
    try:
        bound = extract_branch(spec)
        if has_git and gitx.branch(root) != bound:
            items.append(finding("branch-binding", f"branch binding: expected {bound}", bound, action="bound-worktree"))
    except ValueError as exc:
        items.append(finding("branch-binding", str(exc), action="bound-worktree"))
    if "spec-format: 3" not in spec or not baseline:
        message = "only v3 packages are supported" if "spec-format: 3" not in spec else "package has not passed plan-go"
        return add(result, *items, finding("plan-required", message, action="plan"))
    try:
        records = task_records(directory, tasks)
    except ValueError as exc:
        return add(result, *items, finding("plan-integrity", str(exc), action="restore-plan"))
    items.extend(
        finding("plan-integrity", error, action="restore-plan")
        for error in [*dependency_errors(records), *freeze_errors(directory, spec, records)]
    )
    blocked = [task for task in records if task.blocked]
    unfinished = [task for task in records if not quick and not task.checked and not task.blocked]
    items.extend(finding("task-blocked", f"blocked tasks: {task.id}", task.id, action="unblock") for task in blocked)
    items.extend(
        finding("task-unfinished", f"unfinished tasks: {task.id}", task.id, action="run-task") for task in unfinished
    )
    proof = criteria(spec)
    ids = {task.id for task in records}
    keys = [key for key, _, _ in proof]
    if not records or not proof or len(keys) != len(set(keys)) or any(task not in ids for _, _, task in proof):
        items.append(finding("plan-integrity", "criteria or task graph is incomplete", action="restore-plan"))
    if quick:
        if len(records) != 1 or len(proof) != 1 or package_kind(spec) != "quick" or snapshot is None:
            return add(result, *items, finding("plan-integrity", "invalid quick plan", action="restore-plan"))
        items.extend(_quick_findings(root, baseline, records, snapshot))
        if ":" in (fingerprint or ""):
            items.append(finding("quick-boundary", "nested or special-file changes require a standard plan"))
    else:
        items.extend(_git_findings(root, slug, baseline, records) if has_git else [])
    return add(result, *items)


def _git_findings(root: Path, slug: str, baseline: dict, records: list) -> list[dict]:
    patterns = tuple(pattern for task in records for pattern in task.globs)
    base = baseline.get("head") or gitx.head(root)
    committed = gitx.git(root, "diff", "--name-only", "--no-renames", "-z", base, "HEAD", text=False).stdout
    outside = _outside((os.fsdecode(raw) for raw in committed.split(b"\0") if raw), patterns)
    staged = foreign_index_paths(root, (*patterns, f".spec/specs/{slug}"), local=local_evidence.is_local(root))
    residual = [path for path in _outside(gitx.dirty_paths(root), patterns) if path not in staged]
    groups = (
        ("commit", outside, "changed path", "isolate-change", "block"),
        ("index", staged, "staged path", "isolate-change", "block"),
        ("residual", residual, "untouched worktree", "leave-untouched", "warning"),
    )
    return [
        finding(f"boundary-{code}", f"{text} outside task boundaries: {path}", path, action=action, severity=level)
        for code, paths, text, action, level in groups
        for path in paths
    ]


def proof_findings(records: list, checks: object) -> list[dict]:
    expected = list(verify_groups(records).items())
    if not isinstance(checks, list) or not expected or any(not command for command, _ in expected):
        return [finding("final-proof", "final command batch is missing or invalid")]
    identities = [(item.get("cmd"), item.get("tasks")) for item in checks if isinstance(item, dict)]
    if identities != expected or len(identities) != len(checks):
        return [finding("final-proof", "final command batch does not match the frozen task commands")]
    return [
        finding(
            "verify-failed", f"{task}: final verify did not pass", task, action="fix-verify", signature=check.get("sig")
        )
        for check in checks
        if type(check.get("exit")) is not int or check["exit"] != 0
        for task in check["tasks"]
    ]


def archive_digest(directory: Path) -> str:
    names = ("spec.md", "tasks.md", "completion-summary.md")
    files = [(directory / name).read_text(encoding="utf-8") for name in names]
    ledger = [row for row in runs(directory) if row.get("kind") not in {"delivery", "handoff"}]
    return record_digest([directory.name, files, ledger])


def archive_proof_errors(directory: Path, spec: str, tasks: str, staged_tree: str, receipt: str) -> list[str]:
    final = latest_gate(directory, execution=True)
    if not receipt or archive_digest(directory) != receipt:
        return ["archive does not match its delivery receipt"]
    if final.get("final") is not True or final.get("passed") is not True or final.get("errors") or final.get("parked"):
        return ["passing final gate record is missing"]
    if final.get("product_tree") != staged_tree:
        return ["delivery receipt does not match the Git product tree"]
    cards = parse_tasks(tasks)
    if errors := dependency_errors(cards) + freeze_errors(directory, spec, cards):
        return errors
    return [item["detail"] for item in proof_findings(cards, final.get("checks"))]


def record(root: Path, slug: str, result: dict, *, final=False, probe=False) -> dict:
    directory, _, _ = read_package(root, slug)
    entry = {key: result[key] for key in ("passed", "errors", "findings", "tree_fp")}
    entry.update(
        kind="gate",
        parked=[item["id"] for item in result["parked"]],
        head=gitx.head(root),
        final=final,
        probe=probe,
        ts=now(),
    )
    if final:
        entry.update(product_tree=result["product_tree"], checks=result["checks"])
    append_run(directory, entry)
    return entry

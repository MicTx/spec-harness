"""Delivery verdict, structured findings and the gate ledger."""

from __future__ import annotations

import os
from pathlib import Path

import gitx
import local_evidence
import ui_review
from evidence import evidence_status
from package import (
    append_run,
    changed_paths,
    criteria,
    dependency_errors,
    extract_branch,
    foreign_index_paths,
    latest_runs,
    now,
    package_baseline,
    parse_tasks,
    path_matches,
    pending_parked,
    read_package,
    runs,
    tree_fingerprint,
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


def compute(root: Path, slug: str, final: bool = False) -> dict:
    directory, spec, tasks = read_package(root, slug)
    has_git = gitx.toplevel(root) is not None
    fingerprint = tree_fingerprint(root) if has_git else None
    baseline = package_baseline(directory)
    parked = pending_parked(directory, spec)
    result = dict(findings=[], parked=parked, ui_review={}, tree_fp=fingerprint)
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
        records = parse_tasks(tasks)
    except ValueError as exc:
        return add(result, *items, finding("plan-integrity", str(exc), action="restore-plan"))
    items.extend(
        finding("plan-integrity", error, action="restore-plan")
        for error in [*dependency_errors(records), *freeze_errors(directory, spec, records)]
    )
    blocked = [task for task in records if task.blocked]
    unfinished = [task for task in records if not task.checked and not task.blocked]
    items.extend(finding("task-blocked", f"blocked tasks: {task.id}", task.id, action="unblock") for task in blocked)
    items.extend(
        finding("task-unfinished", f"unfinished tasks: {task.id}", task.id, action="run-task") for task in unfinished
    )
    proof = criteria(spec)
    ids = {task.id for task in records}
    keys = [key for key, _, _ in proof]
    if not records or not proof or len(keys) != len(set(keys)) or any(task not in ids for _, _, task in proof):
        items.append(finding("plan-integrity", "criteria or task graph is incomplete", action="restore-plan"))
    latest = latest_runs(directory)
    ledger = runs(directory)
    restart = next((i for i in range(len(ledger) - 1, -1, -1) if ledger[i].get("kind") == "gate-restart"), -1)
    attempts = [row for row in ledger[restart + 1 :] if row.get("kind") == "gate-repair"]
    for task in records:
        if task.checked and task.command:
            row = latest.get(task.id, {})
            code, detail = evidence_status(task, row, fingerprint, final)
            if code:
                changed = row.get("tree_fp") and row.get("tree_fp") != fingerprint
                for attempt in reversed(attempts):
                    if attempt.get("finding") == ["verify-failed", task.id]:
                        changed = changed and attempt.get("tree_fp") != fingerprint
                        break
                refresh = code == "evidence-refresh" or code == "verify-failed" and changed and not final
                items.append(
                    finding(
                        code,
                        detail,
                        task.id,
                        owner="machine" if refresh else "agent",
                        action="reverify" if refresh else "fix-verify",
                        signature=row.get("sig", ""),
                    )
                )
    if has_git:
        patterns = tuple(pattern for task in records for pattern in task.globs)
        base = baseline.get("head") or gitx.head(root)
        committed = gitx.git(root, "diff", "--name-only", "--no-renames", "-z", base, "HEAD", text=False).stdout
        outside = _outside((os.fsdecode(raw) for raw in committed.split(b"\0") if raw), patterns)
        items.extend(
            finding(
                "boundary-commit", f"changed path outside all task boundaries: {path}", path, action="isolate-change"
            )
            for path in outside
        )
        staged = foreign_index_paths(
            root, (*patterns, f".spec/specs/{slug}", ".spec/charter.md"), local=local_evidence.is_local(root)
        )
        items.extend(
            finding("boundary-index", f"staged path outside all task boundaries: {path}", path, action="isolate-change")
            for path in staged
        )
        residual = _outside(gitx.dirty_paths(root), patterns)
        items.extend(
            finding(
                "boundary-residual",
                f"untouched worktree path outside task boundaries: {path}",
                path,
                severity="warning",
                action="leave-untouched",
            )
            for path in residual
            if path not in staged
        )
    ui = ui_review.review_status(root, slug, spec=spec, tree_fp=fingerprint, tree_required=has_git)
    result["ui_review"] = ui
    if ui["errors"]:
        items.append(finding("ui-proof", "UI review: " + "; ".join(ui["errors"]), action="review-ui"))
    if ui["activation"]["advisory"]:
        items.append(
            finding(
                "ui-heuristic",
                "Possible UI paths: " + ", ".join(ui["activation"]["advisory"]),
                severity="warning",
                action="review-if-relevant",
            )
        )
    return add(result, *items)


def can_finish(result: dict, *, retry: bool = False) -> bool:
    allowed = {"reverify", "fix-verify"} if retry else {"reverify"}
    return not result["parked"] and all(
        item["severity"] != "block" or item["action"] in allowed for item in result["findings"]
    )


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
    if final and result["passed"]:
        entry["ui_review"] = {
            "version": 1,
            **{key: result["ui_review"][key] for key in ("required", "criteria", "record_sha256")},
        }
    append_run(directory, entry)
    return entry

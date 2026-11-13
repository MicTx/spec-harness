"""Execute each v3 verify once, write evidence and own task/checkpoint state."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import gitx
import route
from package import (
    append_run,
    checkpoint_commit,
    dependency_errors,
    failure_signature,
    latest_runs,
    now,
    parse_tasks,
    read_package,
    require_execution_started,
    runs,
    subprocess_output,
    tick,
    topological_tasks,
    tree_fingerprint,
)
from package import unblock as unblock_package
from runtime_budget import project_verify_timeout


def _matching_failures(package: Path, task_id: str, signature: str) -> list[dict]:
    return [
        record
        for record in runs(package)
        if record.get("task") == task_id and record.get("sig") == signature and record.get("exit") not in (None, 0)
    ]


def ladder_directive(package: Path, task_id: str, signature: str) -> str:
    """One directive per failure rung; each invites divergence, never scolds."""
    attempt = len(_matching_failures(package, task_id, signature))
    if attempt == 0:
        return "Fresh evidence in hand — read the failure output and make the smallest fix that could work."
    if attempt == 1:
        return (
            "Same failure signature — time to diverge: build a minimal reproduction "
            "or add diagnostics somewhere new instead of editing the same place."
        )
    if attempt == 2:
        return (
            "Same failure signature three times — be bold: compare the last green checkpoint "
            "and change implementation path."
        )
    return (
        "No new evidence after changing path: park the task with evidence "
        "and point the creative energy at another ready task."
    )


def should_block(package: Path, task_id: str, signature: str) -> bool:
    return len(_matching_failures(package, task_id, signature)) >= 3


def _selected_tasks(records: list, selected: list[str]) -> list:
    errors = dependency_errors(records)
    if errors:
        raise ValueError("; ".join(errors))
    requested = set(selected)
    chosen = [task for task in records if task.id in requested]
    unknown = requested - {task.id for task in chosen}
    if unknown:
        raise ValueError("unknown task id: " + ", ".join(sorted(unknown)))
    malformed = [task.id for task in chosen if not task.command]
    if malformed:
        raise ValueError("non-machine task id: " + ", ".join(malformed))
    unavailable = []
    for task in chosen:
        if task.blocked:
            unavailable.append(f"{task.id}: blocked")
        elif task.checked:
            unavailable.append(f"{task.id}: already checked")
        else:
            missing = [dep for dep in task.depends_on if not next(rec for rec in records if rec.id == dep).checked]
            if missing:
                unavailable.append(f"{task.id}: dependencies not complete: {', '.join(missing)}")
    if unavailable:
        raise ValueError("verify requires ready tasks; " + "; ".join(unavailable))
    return chosen


def _all_tasks(records: list) -> tuple[list, list[str]]:
    ordered = topological_tasks(records)
    completed = {task.id for task in records if task.checked}
    unavailable = {task.id for task in records if task.blocked}
    skipped: list[str] = []
    runnable = []
    for task in ordered:
        if task.checked:
            continue
        missing = [dep for dep in task.depends_on if dep not in completed]
        if task.id in unavailable or missing:
            unavailable.add(task.id)
            if task.id not in skipped:
                skipped.append(task.id)
        elif not task.command:
            unavailable.add(task.id)
            skipped.append(task.id)
        else:
            runnable.append(task)
            completed.add(task.id)
    return runnable, skipped


def _final_tasks(records: list) -> list:
    errors = dependency_errors(records)
    if errors:
        raise ValueError("; ".join(errors))
    blocked = [task.id for task in records if task.blocked]
    unfinished = [task.id for task in records if not task.checked]
    problems = []
    if blocked:
        problems.append("blocked tasks: " + ", ".join(blocked))
    if unfinished:
        problems.append("unfinished tasks: " + ", ".join(unfinished))
    if problems:
        raise ValueError("done requires a complete task DAG; " + "; ".join(problems))
    return [task for task in topological_tasks(records) if task.command]


def run_tasks(root: Path, slug: str, selected: list[str] | None = None, final: bool = False) -> dict:
    package, _spec, tasks = require_execution_started(root, slug)
    records = parse_tasks(tasks)
    skipped: list[str] = []
    if final:
        chosen = _final_tasks(records)
    elif selected is None:
        chosen, skipped = _all_tasks(records)
    else:
        if not selected:
            raise ValueError("choose task ids or --all")
        chosen = _selected_tasks(records, selected)
    results = []
    for task in chosen:
        started = time.monotonic()
        timeout = False
        verify_timeout = project_verify_timeout()
        try:
            completed = subprocess.run(task.command, shell=True, cwd=root, capture_output=True, timeout=verify_timeout)
            exit_code = completed.returncode
            stdout, stderr = subprocess_output(completed)
            signature = failure_signature(completed.stdout, completed.stderr)
        except subprocess.TimeoutExpired as exc:
            timeout = True
            exit_code = 124
            stdout_tail = (exc.stdout or b"")[-4096:]
            stderr_tail = (exc.stderr or b"")[-4096:]
            stdout, stderr = subprocess_output(
                subprocess.CompletedProcess(task.command, exit_code, stdout_tail, stderr_tail)
            )
            stderr = f"Spec verify timed out after {verify_timeout} seconds\n{stderr}"
            signature = failure_signature(stdout_tail, stderr_tail)
        previous = [
            record for record in runs(package) if record.get("task") == task.id and record.get("kind", "run") == "run"
        ]
        record = {
            "kind": "run",
            "task": task.id,
            "cmd": task.command,
            "exit": exit_code,
            "sig": signature,
            "attempt": len(previous) + 1,
            "head": gitx.head(root),
            "duration": round(time.monotonic() - started, 3),
            "final": final,
            "ts": now(),
        }
        directive = ""
        block_task = False
        if exit_code == 0:
            had_failure = any(item.get("exit") not in (None, 0) for item in previous)
            tick(root, slug, task.id)
            if not final:
                record["checkpoint"] = checkpoint_commit(root, slug, task.title, task.globs)
            if had_failure:
                directive = (
                    'Hard-won green — celebrate by recording one reusable lesson with spec.py learn "<when>" "<do>".'
                )
        else:
            directive = ladder_directive(package, task.id, signature)
            if timeout:
                directive = f"Verify timed out. {directive}"
            block_task = not final and should_block(package, task.id, signature)
            if block_task:
                record["blocked"] = True
            print(f"--- {task.id} stdout ---\n{stdout[-4096:]}\n--- stderr ---\n{stderr[-4096:]}")
        record["tree_fp"] = tree_fingerprint(root)
        if block_task:
            tick(root, slug, task.id, blocked=True)
            append_run(package, record)
            checkpoint_commit(root, slug, f"block {task.id}", ())
        else:
            append_run(package, record)
        results.append({**record, "directive": directive})
        if exit_code != 0:
            break
    return {
        "results": results,
        "ready": all(item["exit"] == 0 for item in results),
        "skipped": skipped,
        "next": route.next_card(root, slug),
    }


def unblock_task(root: Path, slug: str, task_id: str, reason: str) -> dict:
    reason = reason.strip()
    if not reason:
        raise ValueError("unblock requires a non-empty reason")
    package, _spec, _tasks = require_execution_started(root, slug)
    unblock_package(root, slug, task_id)
    append_run(
        package,
        {
            "kind": "unblock",
            "task": task_id,
            "reason": reason,
            "ts": now(),
        },
    )
    checkpoint = checkpoint_commit(root, slug, f"unblock {task_id}", ())
    return {"slug": slug, "task": task_id, "unblocked": True, "reason": reason, "checkpoint": checkpoint}


def evaluate_runs(root: Path, slug: str, final: bool = False) -> list[str]:
    package, _spec, tasks = read_package(root, slug)
    latest = latest_runs(package)
    # Hook --index checks may still evaluate an active package on a
    # checkout-index tree that has no .git; the fingerprint dimension is
    # unverifiable there and must degrade instead of failing.
    fingerprint = tree_fingerprint(root) if gitx.toplevel(root) is not None else None
    errors = []
    for task in parse_tasks(tasks):
        if not task.command:
            continue
        record = latest.get(task.id, {})
        if record.get("exit") != 0:
            errors.append(f"{task.id}: latest verify did not pass")
        elif record.get("cmd") != task.command or (fingerprint is not None and record.get("tree_fp") != fingerprint):
            errors.append(f"{task.id}: evidence is stale for the current command or tree")
        elif final and not record.get("final"):
            errors.append(f"{task.id}: final-tree verify has not run")
    return errors

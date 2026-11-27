"""Execute each v3 verify once, write evidence and own task/checkpoint state."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import gitx
import route
from package import (
    append_run,
    checkpoint_commit,
    checkpoint_paths,
    checkpoint_receipt,
    dependency_errors,
    failure_signature,
    frozen_commands,
    now,
    package_baseline,
    render_task_marks,
    require_execution_started,
    runs,
    subprocess_output,
    task_records,
    topological_tasks,
    tree_fingerprint,
    verify_groups,
)
from runtime_budget import project_verify_timeout

# Hard cap on failed verifies per task between unblocks: distinct failures never converge by signature.
TASK_FAILURE_LIMIT = 8
LADDER = (
    "Read the failure output and make the smallest fix that could work.",
    "Build a minimal reproduction or add diagnostics instead of repeating the same edit.",
    "Compare the last green checkpoint and change implementation path.",
    "No new evidence: park the task with evidence and continue any independent work.",
)


def ladder(rows: list[dict], task_id: str, signature: str) -> tuple[str, bool]:
    """Escalate on repeats of one signature or on total failures; block at either cap."""
    total = matches = 0
    for row in reversed(rows):
        if row.get("task") != task_id:
            continue
        if row.get("kind") == "unblock":
            break
        if row.get("kind", "run") == "run" and row.get("exit") not in (None, 0) and "checkpoint_error" not in row:
            total += 1
            matches += row.get("sig") == signature
            if matches >= 3 or total + 1 >= TASK_FAILURE_LIMIT:
                return LADDER[3], True
    return LADDER[min(2, max(matches, total // 2))], False


def _index_command(root: Path, *args: str) -> str:
    result = gitx.git(root, *args, check=False)
    if result.returncode:
        raise ValueError(f"index snapshot ({args[0]}) failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _checkpoint_error(exc: Exception) -> str:
    detail = ""
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        detail = route._single_line(str(stderr or exc.stdout or ""), 200)
    return route._single_line(str(exc) + (f" ({detail})" if detail else ""), 200)


def _mirror_mark(directory: Path, task_id: str, mark: str) -> None:
    path = directory / "tasks.md"
    text = path.read_text(encoding="utf-8")
    updated = render_task_marks(text, {task_id: mark})
    if updated != text:
        path.write_text(updated, encoding="utf-8")


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
        if task.id in unavailable or missing or not task.command:
            unavailable.add(task.id)
            skipped.append(task.id)
        else:
            runnable.append(task)
            completed.add(task.id)
    return runnable, skipped


def _execute(root: Path, command: str) -> tuple[dict, str, str]:
    started = time.monotonic()
    timeout = project_verify_timeout()
    try:
        completed = subprocess.run(command, shell=True, cwd=root, capture_output=True, timeout=timeout)
        notice = ""
    except subprocess.TimeoutExpired as exc:
        completed = subprocess.CompletedProcess(command, 124, (exc.stdout or b"")[-4096:], (exc.stderr or b"")[-4096:])
        notice = f"Spec verify timed out after {timeout} seconds\n"
    stdout, stderr = subprocess_output(completed)
    result = dict(
        cmd=command,
        exit=completed.returncode,
        sig=failure_signature(completed.stdout, completed.stderr, "timeout" if notice else str(completed.returncode)),
        duration=round(time.monotonic() - started, 3),
    )
    return result, stdout, notice + stderr


def final_batch(root: Path, records: list) -> list[dict]:
    checks = []
    for command, tasks in verify_groups(records).items():
        result, stdout, stderr = _execute(root, command)
        checks.append(dict(result, tasks=tasks))
        if result["exit"] != 0:
            detail = f"--- {', '.join(tasks)} stdout ---\n{stdout[-4096:]}\n--- stderr ---\n{stderr[-4096:]}"
            print(detail, file=sys.stderr)
    return checks


def run_tasks(root: Path, slug: str, selected: list[str] | None = None) -> dict:
    package, _spec, tasks = require_execution_started(root, slug)
    if package_baseline(package).get("finish") == "worktree":
        raise ValueError("quick verifies at delivery; run spec.py check after editing")
    records = task_records(package, tasks)
    if selected is None:
        chosen, skipped = _all_tasks(records)
    elif selected:
        chosen, skipped = _selected_tasks(records, selected), []
    else:
        raise ValueError("choose task ids or --all")
    results = []
    drift = frozen_commands(package, _spec, records)
    if drift or (chosen and not package_baseline(package).get("task_ids")):
        raise ValueError("; ".join(drift) or "no frozen verify commands; restore the package baseline")
    for task in chosen:
        # Snapshot before the verify runs so a checkpoint failure restores the
        # index that checkpoint_paths just validated (no foreign staged paths),
        # not the index the verify command itself may have dirtied.
        checkpoint_paths(root, slug, task.globs)
        index_before = _index_command(root, "write-tree")
        head = gitx.head(root)
        verified, stdout, stderr = _execute(root, task.command)
        exit_code, signature = verified["exit"], verified["sig"]
        history = runs(package)
        attempt = 1 + sum(row.get("task") == task.id and row.get("kind", "run") == "run" for row in history)
        record = {
            **verified,
            "kind": "run",
            "task": task.id,
            "attempt": attempt,
            "head": head,
            "final": False,
            "ts": now(),
            "tree_fp": tree_fingerprint(root),
        }
        directive, mark = "", ""
        if exit_code == 0:
            try:
                if gitx.head(root) != head:
                    raise ValueError("HEAD changed during verification")
                receipt = checkpoint_receipt(slug, record)
                record["checkpoint"] = checkpoint_commit(root, slug, task.title, task.globs, receipt=receipt)
                mark = "x"
            except (OSError, ValueError, subprocess.CalledProcessError) as exc:
                if gitx.head(root) == head:
                    try:
                        _index_command(root, "read-tree", index_before)
                    except (OSError, ValueError, subprocess.CalledProcessError) as restore_exc:
                        record["restore_error"] = _checkpoint_error(restore_exc)
                record.update(checkpoint_error=_checkpoint_error(exc), verify_exit=0, exit=1)
                directive = (
                    f"{task.id}: checkpoint commit failed ({record['checkpoint_error']}); "
                    f"task stays unticked. Fix the commit blocker, then retry spec.py verify {task.id}."
                )
                print(directive, file=sys.stderr)
        else:
            directive, blocked = ladder(history, task.id, signature)
            if stderr.startswith("Spec verify timed out"):
                directive = f"Verify timed out. {directive}"
            if blocked:
                record["blocked"], mark = True, "!"
            print(f"--- {task.id} stdout ---\n{stdout[-4096:]}\n--- stderr ---\n{stderr[-4096:]}", file=sys.stderr)
        if record["exit"] != 0:
            record["directive"] = directive
        append_run(package, record)
        if mark:
            _mirror_mark(package, task.id, mark)
        results.append({**record, "directive": directive})
        if record["exit"] != 0:
            break
    ready = all(item["exit"] == 0 for item in results)
    return dict(results=results, ready=ready, skipped=skipped, next=route.next_card(root, slug))


def unblock_task(root: Path, slug: str, task_id: str, reason: str) -> dict:
    reason = reason.strip()
    if not reason:
        raise ValueError("unblock requires a non-empty reason")
    package, _spec, tasks = require_execution_started(root, slug)
    if not any(task.id == task_id and task.blocked for task in task_records(package, tasks)):
        raise ValueError(f"task is not blocked: {task_id}")
    append_run(package, {"kind": "unblock", "task": task_id, "reason": reason, "ts": now()})
    _mirror_mark(package, task_id, " ")
    return {"slug": slug, "task": task_id, "unblocked": True, "reason": reason}

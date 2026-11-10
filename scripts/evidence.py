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
    failure_signature,
    latest_runs,
    now,
    parse_tasks,
    read_package,
    runs,
    subprocess_output,
    tick,
    tree_fingerprint,
)


def ladder_directive(package: Path, task_id: str, signature: str) -> str:
    same = [
        record
        for record in runs(package)
        if record.get("task") == task_id and record.get("sig") == signature and record.get("exit") not in (None, 0)
    ]
    attempt = len(same)
    if attempt == 0:
        return "Read the failure output and make the smallest fix."
    if attempt == 1:
        return (
            "Same failure signature: build a minimal reproduction or add diagnostics instead of editing the same place."
        )
    if attempt == 2:
        return "Same failure signature three times: compare the last green checkpoint and change implementation path."
    return "No new evidence after changing path: park the task with evidence and continue another ready task."


def run_tasks(root: Path, slug: str, selected: list[str] | None = None, final: bool = False) -> dict:
    package, spec, tasks = read_package(root, slug)
    records = parse_tasks(tasks)
    chosen = [task for task in records if (selected is None or task.id in selected)]
    if selected is not None:
        malformed = [task.id for task in chosen if not task.command]
        if malformed:
            raise ValueError("non-machine task id: " + ", ".join(malformed))
    chosen = [task for task in chosen if task.command]
    if selected is not None and {task.id for task in chosen} != set(selected):
        raise ValueError("unknown or non-machine task id")
    results = []
    for task in chosen:
        started = time.monotonic()
        completed = subprocess.run(task.command, shell=True, cwd=root, capture_output=True, timeout=600)
        stdout, stderr = subprocess_output(completed)
        signature = failure_signature(completed.stdout, completed.stderr)
        previous = [
            record for record in runs(package) if record.get("task") == task.id and record.get("kind", "run") == "run"
        ]
        record = {
            "kind": "run",
            "task": task.id,
            "cmd": task.command,
            "exit": completed.returncode,
            "sig": signature,
            "attempt": len(previous) + 1,
            "head": gitx.head(root),
            "duration": round(time.monotonic() - started, 3),
            "final": final,
            "ts": now(),
        }
        directive = ""
        if completed.returncode == 0:
            had_failure = any(item.get("exit") not in (None, 0) for item in previous)
            tick(root, slug, task.id)
            if not final:
                record["checkpoint"] = checkpoint_commit(root, slug, task.title, task.globs)
            if had_failure:
                directive = 'Record one reusable lesson with spec.py learn "<when>" "<do>".'
        else:
            directive = ladder_directive(package, task.id, signature)
            print(f"--- {task.id} stdout ---\n{stdout[-4096:]}\n--- stderr ---\n{stderr[-4096:]}")
        record["tree_fp"] = tree_fingerprint(root)
        append_run(package, record)
        results.append({**record, "directive": directive})
    return {
        "results": results,
        "ready": all(item["exit"] == 0 for item in results),
        "next": route.next_card(root, slug),
    }


def evaluate_runs(root: Path, slug: str, final: bool = False) -> list[str]:
    package, _spec, tasks = read_package(root, slug)
    latest = latest_runs(package)
    fingerprint = tree_fingerprint(root)
    errors = []
    for task in parse_tasks(tasks):
        if not task.command:
            continue
        record = latest.get(task.id, {})
        if record.get("exit") != 0:
            errors.append(f"{task.id}: latest verify did not pass")
        elif record.get("cmd") != task.command or record.get("tree_fp") != fingerprint:
            errors.append(f"{task.id}: evidence is stale for the current command or tree")
        elif final and not record.get("final"):
            errors.append(f"{task.id}: final-tree verify has not run")
    return errors

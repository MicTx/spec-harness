"""One next-action projection for CLI and hooks; no worktree fingerprint."""

from __future__ import annotations

from pathlib import Path

import gitx
from memory import last_failures, progress_tuple
from package import criteria_status, parse_ledger, parse_tasks, read_package, ready_tasks, resolve_slug


def decide(root: Path, slug: str | None = None, *, fingerprint: bool = False) -> dict:
    del fingerprint
    selected = slug or resolve_slug(root)
    package, spec, tasks = read_package(root, selected)
    records = parse_tasks(tasks)
    ready = ready_tasks(records)
    parked = parse_ledger(spec, "Parked", "P")
    latest = criteria_status(spec, package)
    all_done = bool(records) and all(record.checked for record in records)
    evidence_failures = last_failures(package)
    unblocked_left = any(not record.checked and not record.blocked for record in records)
    if ready:
        state = "run"
    elif all_done and all(item[3] for item in latest) and not parked and not evidence_failures:
        state = "deliver"
    elif evidence_failures or any(record.blocked for record in records):
        state = "stalled"
    elif parked and not unblocked_left:
        state = "parked-only"
    else:
        state = "plan"
    task = ready[0] if ready else None
    result = {
        "slug": selected,
        "state": state,
        "task": {
            "id": task.id,
            "title": task.title,
            "boundary": task.globs,
            "verify": task.command,
            "expect": task.expect,
        }
        if task
        else None,
        "ready": [record.id for record in ready],
        "parked": parked,
        "criteria": [
            {"id": key, "text": text, "task": task_id, "passed": passed} for key, text, task_id, passed in latest
        ],
        "failures": evidence_failures,
        "progress": list(progress_tuple(package, gitx.head(root))),
    }
    return result


def next_card(root: Path, slug: str | None = None) -> dict:
    brief = decide(root, slug)
    if brief["task"]:
        task = brief["task"]
        brief["next"] = f"{task['id']}: {task['title']}; verify: {task['verify']}"
    elif brief["state"] == "deliver":
        brief["next"] = "Run spec.py done for final-tree verification and archive."
    elif brief["state"] == "parked-only":
        brief["next"] = "Present all parked decisions in one batch."
    elif brief["state"] == "stalled":
        brief["next"] = "Report the failure signature and change strategy; the task is blocked without new evidence."
    else:
        brief["next"] = "Finish planning: run plan-lint, resolve gaps, then plan-go."
    return brief


def brief(root: Path, slug: str | None = None) -> str:
    data = next_card(root, slug)
    _directory, spec, _tasks = read_package(root, data["slug"])
    goal = next((line[2:] for line in spec.splitlines() if line.startswith("- goal:")), "")
    lines = [f"Spec {data['slug']}: {data['state']}", goal, f"Next: {data['next']}"]
    lines.extend(f"Failure: {item}" for item in data["failures"])
    lines.extend(f"Parked: {item['id']} {item['text']}" for item in data["parked"])
    return "\n".join(lines)[:1450]

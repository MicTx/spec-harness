"""One next-action projection for CLI and session hooks.

The projection is read-only. `spec.py check` writes a `gate` record into the
package's runs ledger; this module reads the latest record so a failed delivery
gate becomes a first-class lifecycle state instead of being forgotten between
turns. `gate.compute` remains the authority; this module never re-implements a
gate rule.
"""

from __future__ import annotations

from pathlib import Path

import gitx
import ui_review
from memory import last_failures, progress_tuple
from package import (
    criteria_status,
    latest_gate,
    package_baseline,
    parse_charter,
    parse_ledger,
    parse_tasks,
    read_archived_package,
    read_package,
    ready_tasks,
    resolve_archived_slug,
    resolve_slug,
    worktree_packages,
)

STAGE_FOR_STATE = {
    "plan": "plan",
    "run": "run",
    "deliver": "check",
    "check-failed": "check",
    "stalled": "run",
    "parked-only": "plan",
    "push": "push",
    "delivered": "done",
    "elsewhere": "run",
}


def _gate_failures(package: Path, head: str) -> list[str]:
    """Return current gate errors recorded by the latest check on this head."""
    record = latest_gate(package)
    if not record or record.get("passed"):
        return []
    recorded_head = str(record.get("head") or "")
    if recorded_head and head and recorded_head != head:
        return []
    return [str(item) for item in record.get("errors", []) if str(item).strip()]


def gate_next(errors: list[str]) -> str:
    """Turn the first actionable gate error into the next repair action."""
    text = " ".join(errors)
    if "UI review" in text:
        return "Complete the UI/UX review record with real flows and screenshots, then rerun spec.py check."
    if "changed path outside" in text:
        return "Move the change inside the owning task boundary or amend the plan, then rerun spec.py check."
    if "branch binding" in text:
        return "Switch to the package's bound branch, then rerun spec.py check."
    if "stale" in text:
        return "Refresh evidence with spec.py verify --all, then rerun spec.py check."
    if "criterion not proven" in text:
        return "Re-run the criterion's task verify, then rerun spec.py check."
    first = errors[0] if errors else "delivery gate failed"
    return f"Fix the recorded delivery-gate failure ({first}), then rerun spec.py check."


def _state_for(
    *,
    frozen: bool,
    ready: list,
    all_done: bool,
    criteria_passed: bool,
    parked: list[dict[str, str]],
    unblocked_left: bool,
    evidence_failures: list[str],
    ui_failures: list[str],
    gate_failures: list[str],
    any_blocked: bool,
) -> str:
    if not frozen:
        return "plan"
    if ready:
        return "run"
    if evidence_failures or ui_failures or any_blocked:
        return "stalled"
    if gate_failures:
        return "check-failed"
    if parked and not unblocked_left:
        return "parked-only"
    if all_done and criteria_passed and not parked:
        return "deliver"
    # Remaining reachable case: some machine task cannot become ready but is
    # not blocked (for example a malformed task without a verify command).
    return "stalled"


def decide(root: Path, slug: str | None = None) -> dict:
    selected = slug or resolve_slug(root)
    package, spec, tasks = read_package(root, selected)
    records = parse_tasks(tasks)
    frozen = bool(package_baseline(package))
    ready = ready_tasks(records) if frozen else []
    parked = parse_ledger(spec, "Parked", "P")
    latest = criteria_status(spec, package)
    all_done = bool(records) and all(record.checked for record in records)
    criteria_passed = all(item[3] for item in latest)
    evidence_failures = last_failures(package)
    ui_status = (
        ui_review.review_status(root, selected, spec=spec, tree_required=False)
        if frozen
        else {"required": False, "errors": []}
    )
    ui_failures = ui_status.get("errors", [])
    head = gitx.head(root)
    gate_failures = _gate_failures(package, head) if frozen else []
    any_blocked = any(record.blocked for record in records)
    unblocked_left = any(not record.checked and not record.blocked for record in records)
    progress = progress_tuple(package, head)
    state = _state_for(
        frozen=frozen,
        ready=ready,
        all_done=all_done,
        criteria_passed=criteria_passed,
        parked=parked,
        unblocked_left=unblocked_left,
        evidence_failures=evidence_failures,
        ui_failures=ui_failures,
        gate_failures=gate_failures,
        any_blocked=any_blocked,
    )
    task = ready[0] if ready else None
    failures = evidence_failures + [f"gate: {item}" for item in gate_failures]
    goal = next((line[2:] for line in spec.splitlines() if line.startswith("- goal:")), "")
    result = {
        "slug": selected,
        "state": state,
        "stage": STAGE_FOR_STATE.get(state, "run"),
        "goal": goal,
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
        "failures": failures,
        "gate": {"passed": not gate_failures, "errors": gate_failures},
        "ui_review": ui_status,
        # `progress` keeps its original 3-field public shape; the hook uses
        # `progress_token` so a failed gate still counts as fresh evidence.
        "progress": list(progress[:3]),
        "progress_token": list(progress),
    }
    return result


def next_card(root: Path, slug: str | None = None) -> dict:
    brief = decide(root, slug)
    if brief["task"]:
        task = brief["task"]
        brief["next"] = f"{task['id']}: {task['title']}; verify: {task['verify']}"
    elif brief["state"] == "deliver":
        brief["next"] = "Run spec.py check for the delivery gate; a pass verifies the final tree and archives."
    elif brief["state"] == "check-failed":
        brief["next"] = gate_next(brief["gate"]["errors"])
    elif brief["state"] == "stalled" and brief.get("ui_review", {}).get("errors"):
        brief["next"] = "Write UI/UX review findings and real-operation screenshots back, then rerun spec.py check."
    elif brief["state"] == "parked-only":
        brief["next"] = "Present all parked decisions in one batch."
    elif brief["state"] == "stalled":
        brief["next"] = "Report the failure signature and change strategy; the task is blocked without new evidence."
    elif brief["state"] == "plan":
        brief["next"] = "Finish planning: run plan-lint, resolve gaps, then plan-go."
    else:
        brief["next"] = "Inspect the package state and continue."
    return brief


def archived_card(root: Path) -> dict | None:
    """Project the archived delivery bound to the current branch, if any.

    This keeps the push link of the lifecycle visible in a fresh session; the
    hook only injects it on start/subagent events so it never blocks a stop.
    """
    try:
        slug = resolve_archived_slug(root)
        _directory, spec, _tasks = read_archived_package(root, slug)
    except (OSError, ValueError):
        return None
    if "spec-format: 3" not in spec:
        return None
    finish = parse_charter(spec).get("V4", "commit")
    if finish == "push":
        state = "push"
        next_text = "Delivery is archived; run spec.py push to finish the authorized merge and push."
    else:
        state = "delivered"
        next_text = f"Delivery complete (V4 {finish}); no active package on this branch."
    return {
        "slug": slug,
        "state": state,
        "stage": STAGE_FOR_STATE[state],
        "archived": True,
        "next": next_text,
    }


def worktree_card(root: Path) -> dict | None:
    """Point a primary-worktree session at the only active linked worktree."""
    candidates = worktree_packages(root)
    if len(candidates) != 1:
        return None
    item = candidates[0]
    return {
        "slug": item["slug"],
        "state": "elsewhere",
        "stage": STAGE_FOR_STATE["elsewhere"],
        "worktree": item["worktree"],
        "next": f"Active package is in worktree {item['worktree']}; continue there with spec.py next.",
    }


def completion_card(slug: str, finish: str = "commit", push_failed: bool = False) -> dict:
    """Project the lifecycle link after `done`/`check` archived the package."""
    if finish == "push" and push_failed:
        return {
            "slug": slug,
            "state": "push",
            "stage": "push",
            "archived": True,
            "next": "Delivery archived but push failed; fix the remote and run spec.py push.",
        }
    return {
        "slug": slug,
        "state": "delivered",
        "stage": "done",
        "archived": True,
        "next": f"Delivery complete (V4 {finish}); no active package.",
    }


def brief(root: Path, slug: str | None = None) -> str:
    data = next_card(root, slug)
    lines = [f"Spec {data['slug']}: {data['state']}", data.get("goal", ""), f"Next: {data['next']}"]
    lines.extend(f"Failure: {item}" for item in data["failures"])
    lines.extend(f"Parked: {item['id']} {item['text']}" for item in data["parked"])
    if data.get("worktree"):
        lines.append(f"Worktree: {data['worktree']}")
    return "\n".join(line for line in lines if line)[:1450]

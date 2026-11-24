"""Read-only next-action projection for CLI and host hooks."""

from __future__ import annotations

import os
import re
from pathlib import Path

import gitx
import plan
import ui_review
from memory import last_failures, progress_tuple, snapshot_pending
from package import (
    HANDOFF_CHOICES,
    HANDOFF_ID,
    PARK_CHOICES,
    criteria_status,
    effective_finish,
    extract_branch,
    gate_actions,
    gate_recovery,
    handoff_completed,
    latest_gate,
    latest_runs,
    package_baseline,
    package_dir,
    package_kind,
    park_decisions,
    parse_tasks,
    pending_parked,
    read_archived_package,
    read_package,
    ready_tasks,
    repair_origin,
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

SPARK_MARKER = "Spark:"

VERIFY_TRUNCATION_NOTE = "[verify truncated; run spec.py next for full command]"
VERIFY_BRIEF_LIMIT = 120

PROMPT_TIER_ENV = "SPEC_PROMPT_TIER"
PROMPT_TIER_VALUES = ("full", "compact", "aggressive")
BRIEF_BUDGET = 1450
COMPACT_BRIEF_BUDGET = 900
AGGRESSIVE_BRIEF_BUDGET = 700
INSTALL_MARKER = Path(__file__).resolve().parent.parent / ".spec-skill-install"

RECOVERY_PENDING_LIMIT = 5
RECOVERY_DIRECTIVE_LIMIT = 400


def prompt_tier(marker: Path | None = None) -> str:
    """Resolve the prompt tier: env override, then install marker, else full."""
    value = os.environ.get(PROMPT_TIER_ENV, "")
    if value in PROMPT_TIER_VALUES:
        return value
    if marker is not None:
        try:
            text = marker.read_text(encoding="utf-8")
        except OSError:
            text = ""
        match = re.search(r"^tier=(full|compact|aggressive)$", text, flags=re.MULTILINE)
        if match:
            return match.group(1)
    return "full"


def brief_budget() -> int:
    return {"aggressive": AGGRESSIVE_BRIEF_BUDGET, "compact": COMPACT_BRIEF_BUDGET}.get(
        prompt_tier(INSTALL_MARKER), BRIEF_BUDGET
    )


ENCOURAGE = {
    "plan": "Spark: sketch two rival designs before converging — the bolder fork may be the winner.",
    "run": (
        "Spark: a creative implementation inside the boundary beats a mechanical one; "
        "a failed verify is a clue, not a verdict."
    ),
    "deliver": "Spark: everything is green — run the gate and make it permanent.",
    "check-failed": "Spark: repair is creative work; fix the first gate error your way.",
    "stalled": "Spark: stalls crack open from a new angle — reread the signature, then try a different path.",
    "parked-only": "Spark: parked items are seeds for the next spec, not dead ends.",
    "push": "Spark: one authorized step left — finish it proudly.",
    "delivered": "Spark: well delivered; let the lessons seed the next bold spec.",
    "elsewhere": "Spark: pick it up there and keep the momentum.",
}


def spark(state: str) -> str:
    """Return the state's encouragement line, if it has one."""
    return ENCOURAGE.get(state, "")


def _gate_action(item: dict) -> str:
    subject = _single_line(item.get("subject"), 80)
    return {
        "bound-worktree": f"Use the package's bound worktree ({subject}); do not move user changes.",
        "restore-plan": "Restore the frozen plan or apply an authorized amendment before verifying.",
        "isolate-change": f"Inspect ownership of {subject}; leave user changes intact and isolate the delivery.",
        "review-ui": "Operate the relevant UI, capture real evidence and write spec.py ui-review --file.",
        "fix-verify": f"Investigate and fix the failing verify for {subject} within its task boundary.",
        "reverify": "Run spec.py check to refresh evidence and verify the final tree.",
        "unblock": f'Investigate {subject}; then use spec.py unblock {subject} --reason "<new evidence>".',
    }.get(item.get("action"), "Inspect the recorded delivery evidence and repair within the charter.")


def _single_line(value: object, limit: int) -> str:
    """Render untrusted ledger text as bounded, single-line prose."""
    if not isinstance(value, str):
        return ""
    cleaned = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value)
    text = " ".join(cleaned.split())
    if limit > 0 and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _latest_failed_run(package: Path, task_id: str, command: str = "") -> dict:
    record = latest_runs(package).get(task_id, {})
    return record if record.get("exit") not in (None, 0) and record.get("cmd") in (None, "", command) else {}


def _recovery_projection(
    package: Path, *, task: dict | None, blocked: list[dict], final_failed: dict | None = None
) -> dict:
    """Project pending clues for the ready task and current failure directives."""
    recovery: dict = {"pending": snapshot_pending(package, task)}
    source = task or (blocked[0] if blocked else final_failed)
    if source:
        record = _latest_failed_run(package, source["id"], source.get("verify", ""))
        directive = _single_line(record.get("directive"), RECOVERY_DIRECTIVE_LIMIT)
        if directive:
            recovery["directive"] = directive
            recovery["task"] = source["id"]
        if record:
            recovery["evidence"] = f".spec/specs/{package.name}/runs.jsonl"
            if not task and not blocked and final_failed:
                recovery["task"] = source["id"]
                recovery["final_failed"] = True
    if blocked:
        recovery["blocked"] = [item["id"] for item in blocked]
    return recovery


def decide(root: Path, slug: str | None = None) -> dict:
    selected = slug or resolve_slug(root)
    package, spec, tasks = read_package(root, selected)
    records = parse_tasks(tasks)
    frozen = bool(package_baseline(package))
    head = gitx.head(root)
    ledger = latest_gate(package) if frozen else {}
    active_gate = latest_gate(package, execution=True) if ledger.get("probe") else ledger
    ready = ready_tasks(records) if frozen else []
    if frozen:
        try:
            bound = extract_branch(spec)
        except ValueError:
            bound = "the recorded branch"
        if gitx.branch(root) != bound:
            ledger = {
                "errors": [f"branch binding: expected {bound}"],
                "findings": [
                    dict(code="branch-binding", action="bound-worktree", subject=bound, owner="agent", severity="block")
                ],
            }
    parked = pending_parked(package, spec)
    finish, requested = effective_finish(package, spec)
    if (
        active_gate.get("head") == head
        and finish == requested
        and any(item.get("code") == "handoff-blocked" for item in active_gate.get("findings", []))
    ):
        problem = next(item for item in active_gate["findings"] if item["code"] == "handoff-blocked")
        status = "waiting" if park_decisions(package).get(HANDOFF_ID, {}).get("choice") == "wait" else "open"
        parked.append(dict(id=HANDOFF_ID, text=problem["detail"], status=status))
    if ledger.get("head") and ledger["head"] != head:
        ledger = {}
    actions = gate_actions(ledger)
    actions = [
        item
        for item in actions
        if not (
            item["action"] == "run-task"
            and not any(t.id == item["subject"] and not t.checked for t in records)
            or item["action"] == "choose-handoff"
            and finish != requested
        )
    ]
    primary = next((item for item in actions if item.get("code") != "recovery-exhausted"), None)
    budget = gate_recovery(package, primary)
    evidence_failures = last_failures(package)
    ui_status = ui_review.review_status(root, selected, spec=spec, tree_required=False) if frozen else {}
    latest = criteria_status(spec, package)
    progress = progress_tuple(package, head)
    gate_failures = [item["detail"] for item in actions] if "findings" in ledger else ledger.get("errors", [])
    decision_only = (
        parked
        and (not primary or primary["owner"] == "user")
        and not (evidence_failures or ui_status.get("errors") or any(task.blocked for task in records))
    )
    if not frozen:
        state = "plan"
    elif primary and primary["action"] in {"bound-worktree", "restore-plan", "isolate-change"}:
        state, ready = "check-failed", []
    elif ready:
        state = "run"
    elif decision_only:
        state = "parked-only"
    elif budget["exhausted"]:
        state = "stalled"
    elif primary or gate_failures and "findings" not in ledger:
        state = "check-failed"
    elif ui_status.get("errors") or evidence_failures or not records or not all(t.checked for t in records):
        state = "stalled"
    else:
        state = "deliver"
    decisions = [
        dict(
            item,
            recommended="wait",
            options=[dict(id=k, consequence=v) for k, v in choices.items()],
            evidence=f".spec/specs/{selected}/runs.jsonl",
            attempted=active_gate.get("errors", []),
        )
        for item in parked
        if state == "parked-only" and item["status"] == "open"
        for choices in [HANDOFF_CHOICES if item["id"] == HANDOFF_ID else PARK_CHOICES]
    ]
    task = ready[0] if ready else None
    failures = [f"gate: {item}" for item in gate_failures] + evidence_failures
    suggestions: list = []
    if not frozen:
        try:
            suggestions = plan.lint(root, selected, static=True, suggest=True).get("suggestions", [])[:3]
        except ValueError:
            suggestions = []
    goal = next((line[2:] for line in spec.splitlines() if line.startswith("- goal:")), "")
    blocked = [
        {"id": record.id, "verify": record.command} for record in records if record.blocked and not record.checked
    ]
    final_failed = None
    if frozen and not ready and not blocked:
        for record in records:
            if record.checked and record.command:
                latest_failure = _latest_failed_run(package, record.id, record.command)
                if latest_failure.get("final") is True:
                    final_failed = {"id": record.id, "verify": record.command}
                    break
    result = {
        "slug": selected,
        "kind": package_kind(spec),
        "repair_of": repair_origin(spec),
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
        "decisions": decisions[:4] if not ready else [],
        "criteria": [
            {"id": key, "text": text, "task": task_id, "passed": passed} for key, text, task_id, passed in latest
        ],
        "failures": failures,
        "suggestions": suggestions,
        "gate": {
            "passed": not gate_failures and not parked,
            "errors": gate_failures,
            "findings": ledger.get("findings", []),
            "action": primary,
        },
        "ui_review": ui_status,
        # `progress` keeps its original 3-field public shape; the hook uses
        # `progress_token` so a failed gate still counts as fresh evidence.
        "progress": list(progress[:3]),
        "progress_token": list(progress),
    }
    result["recovery"] = dict(
        _recovery_projection(package, task=result["task"], blocked=[] if ready else blocked, final_failed=final_failed),
        **budget,
    )
    return result


def _next_without_spark(data: dict) -> str:
    text = str(data.get("next") or "")
    encouragement = spark(str(data.get("state") or ""))
    if encouragement and text.endswith(encouragement):
        return text[: -len(encouragement)].rstrip()
    return text


def _task_next(brief: dict) -> str:
    """Keep the public CLI next action complete; only the brief abbreviates."""
    task = brief["task"]
    return f"{task['id']}: {task['title']}; verify: {task['verify']}"


def _blocked_next(brief: dict) -> str:
    """Stalled card: name every blocked task with its real unblock command."""
    recovery = brief.get("recovery") or {}
    blocked = [str(item) for item in recovery.get("blocked") or [] if str(item)]
    if blocked:
        commands = "; ".join(f'spec.py unblock {item} --reason "<new evidence>"' for item in blocked[:3])
        ledger = f".spec/specs/{brief['slug']}/runs.jsonl"
        return (
            f"Blocked without ready work: {commands}. "
            f"Evidence ledger: {ledger}. Pick the recovery path yourself; only parked items reach the user."
        )
    return (
        "Report the failure signature and change strategy; the task is blocked without new evidence."
        " Pick the recovery path yourself; only parked items reach the user."
    )


def _next_for_state(brief: dict) -> str:
    if brief["task"]:
        return _task_next(brief)
    state = brief["state"]
    if state == "deliver":
        return "Run spec.py check for the delivery gate; a pass verifies the final tree and archives."
    recovery = brief.get("recovery") or {}
    if state == "parked-only":
        return (
            "Every ask carries its evidence ledger: attempts, recommendation, alternatives and consequences."
            if brief["decisions"]
            else "Keep delivery pending as requested; do not ask again."
        )
    if recovery.get("exhausted"):
        return "Recovery budget exhausted. Report the attempted strategies and remaining blocker; do not retry blindly."
    if state == "check-failed":
        item = brief["gate"].get("action")
        if not item:
            first = _single_line(next(iter(brief["gate"]["errors"]), "delivery gate failed"), 100)
            return f"Recorded diagnostic ({first}); inspect with spec.py check --check-only."
        text = _gate_action(item)
        if item.get("action") not in {"reverify", "bound-worktree", "plan"}:
            text += ' Then run spec.py check --attempt "<strategy and evidence>".'
        return text
    if state == "stalled":
        if recovery.get("final_failed"):
            return f"Final verification failed for {recovery['task']}; fix it, then run spec.py check."
        if brief.get("ui_review", {}).get("errors"):
            return "Run spec.py check to start recovery of the UI/UX review."
        return _blocked_next(brief)
    if state == "plan":
        return "Finish planning: run plan-lint, resolve gaps, then plan-go."
    return "Inspect the package state and continue."


def next_card(
    root: Path,
    slug: str | None = None,
    decision: dict | None = None,
    *,
    projection: dict | None = None,
) -> dict:
    """Project once; a caller may reuse a decision or a finished card."""
    source = projection if projection is not None else decision
    if source is not None and source.get("next"):
        # Already a finished card: return a copy without recomputing anything.
        return dict(source)
    brief = dict(source) if source is not None else decide(root, slug)
    if not isinstance(brief.get("recovery"), dict):
        brief["recovery"] = _recovery_projection(
            package_dir(root, str(brief["slug"])), task=brief.get("task"), blocked=[]
        )
    brief["next"] = _next_for_state(brief)
    directive = (brief.get("recovery") or {}).get("directive")
    if isinstance(directive, str) and directive and "Last failure rung" not in brief["next"]:
        brief["next"] = f"{brief['next']} Last failure rung: {directive}"
    if brief.get("kind") == "repair":
        brief["next"] = (
            f"Repair of {brief['repair_of']}: thin planning, full gate; "
            "keep the change consistent with the archived spec. " + brief["next"]
        )
    if brief.get("suggestions"):
        brief["next"] = (
            f"plan-lint --suggest lists {len(brief['suggestions'])} refinement(s) for this plan. " + brief["next"]
        )
    encouragement = spark(brief["state"])
    if encouragement:
        brief["next"] = f"{brief['next']} {encouragement}"
    return brief


def _truncate_line(line: str, limit: int) -> str:
    if limit <= 0:
        return ""
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _brief_action(data: dict, limit: int) -> str:
    """Fit prose around an atomic command; never cut a shell command in half."""
    task = data.get("task")
    if task:
        identifier = _single_line(task.get("id"), 80)
        prefix = f"Next: {identifier}"
        command = task.get("verify", "")
        lookup = f"; {VERIFY_TRUNCATION_NOTE}"
        suffix = f"; verify: {command}"
        if (
            not isinstance(command, str)
            or not command.isprintable()
            or len(command) > VERIFY_BRIEF_LIMIT
            or len(prefix + suffix) > limit
        ):
            suffix = lookup
        if len(prefix + suffix) > limit:
            return f"Next: {_truncate_line(identifier, max(1, limit - 34))}; [truncated] spec.py next"
        room = limit - len(prefix + suffix) - 2
        title = _single_line(task.get("title"), max(room, 1)) if room >= 8 else ""
        return prefix + (": " + title if title else "") + suffix
    recovery = data.get("recovery") or {}
    blocked = recovery.get("blocked") or []
    if recovery.get("exhausted"):
        action = "Next: report the exhausted recovery budget and remaining blocker."
    elif data.get("state") == "stalled" and blocked:
        action = f'Next: spec.py unblock {blocked[0]} --reason "<new evidence>"'
    elif data.get("state") == "stalled" and recovery.get("final_failed") and not recovery.get("exhausted"):
        action = f"Next: fix {recovery['task']}; then run spec.py check"
    else:
        action = "Next: " + _next_without_spark(data).split(" Last failure rung:", 1)[0]
    if len(action) <= limit and action.isprintable():
        return action
    return "Next: [action truncated; run spec.py next for full card]"


def _render_brief(data: dict, budget: int) -> str:
    """Reserve atomic commands, failure context and Spark before optional prose."""
    if budget <= 0:
        return ""
    state = str(data.get("state") or "")
    tail = spark(state)
    header = f"Spec {_single_line(data.get('slug'), 80)}: {state}"
    recovery = data.get("recovery") or {}
    failures = data.get("failures") or []
    pending = recovery.get("pending") or []
    details: dict[str, str] = {}
    if failures:
        details["failure"] = "Failure: " + _single_line(failures[0], 320)
    if pending:
        details["pending"] = "Pending: [snapshot; advisory] " + " | ".join(pending[:RECOVERY_PENDING_LIMIT])
    directive = recovery.get("directive")
    if directive:
        details["recovery"] = "Recovery: " + _single_line(directive, RECOVERY_DIRECTIVE_LIMIT)

    caps = {key: min(len(text), 80 if key == "pending" else 64) for key, text in details.items()}
    action_limit = 96
    action = _brief_action(data, action_limit)
    fixed = [header, action, *([tail] if tail else [])]
    remaining = budget - sum(map(len, fixed)) - sum(caps.values()) - (len(fixed) + len(details) - 1)
    if remaining < 0:
        for key in reversed(caps):
            shrink = min(caps[key], -remaining)
            caps[key] -= shrink
            remaining += shrink
            if remaining >= 0:
                break
        if remaining < 0:
            minimal = "Next: spec.py next"
            room = budget - len(tail) - len(minimal) - 2
            if room >= 6:
                return "\n".join([_truncate_line(header, room), minimal, tail]).rstrip()
            return _truncate_line(tail or minimal, budget)

    # Restore full short recovery prose before optional goal/title text.
    for key, target in (("recovery", 180), ("pending", 220), ("failure", 180)):
        if key in caps:
            extra = min(remaining, max(0, min(len(details[key]), target) - caps[key]))
            caps[key] += extra
            remaining -= extra
    expanded = _brief_action(data, min(240, action_limit + remaining))
    extra = len(expanded) - len(action)
    if 0 <= extra <= remaining:
        action = expanded
        remaining -= extra
    lines = [header, action]
    lines.extend(_truncate_line(text, caps[key]) for key, text in details.items() if caps[key] > 0)
    optional = ["Failure: " + _single_line(item, 160) for item in failures[1:3]]
    optional.extend(
        f"Parked: {item['id']} {_single_line(item['text'], 160)}" for item in (data.get("parked") or [])[:3]
    )
    if data.get("worktree"):
        optional.append("Worktree: " + str(data["worktree"]))
    if data.get("goal"):
        optional.append(_single_line(data["goal"], max(80, budget // 4)))
    for text in optional:
        if remaining < 24:
            break
        line = _truncate_line(text, remaining - 1)
        lines.append(line)
        remaining -= len(line) + 1
    if tail:
        lines.append(tail)
    return "\n".join(lines)


def brief(
    root: Path,
    slug: str | None = None,
    decision: dict | None = None,
    *,
    projection: dict | None = None,
    budget: int | None = None,
) -> str:
    return _render_brief(next_card(root, slug, decision, projection=projection), budget or brief_budget())


def archived_card(root: Path) -> dict | None:
    try:
        slug = resolve_archived_slug(root)
        _directory, spec, _tasks = read_archived_package(root, slug)
    except (OSError, ValueError):
        return None
    if "spec-format: 3" not in spec:
        return None
    finish, requested = effective_finish(_directory, spec)
    pending = finish == "push" or finish == "merge" and not handoff_completed(_directory, "merge")
    state = "push" if pending else "delivered"
    if pending:
        next_text = f"Delivery is archived; run spec.py push to retry the authorized {finish} handoff."
    elif finish != requested:
        next_text = "Committed locally by decision; no merge or push performed."
    else:
        next_text = f"Delivery complete (V4 {finish}); no active package on this branch."
    return dict(slug=slug, state=state, stage=STAGE_FOR_STATE[state], archived=True, next=f"{next_text} {spark(state)}")


def worktree_card(root: Path) -> dict | None:
    candidates = worktree_packages(root)
    if len(candidates) != 1:
        return None
    item = candidates[0]
    next_text = f"Active package is in worktree {item['worktree']}; continue there with spec.py next."
    return dict(
        slug=item["slug"],
        state="elsewhere",
        stage=STAGE_FOR_STATE["elsewhere"],
        worktree=item["worktree"],
        next=f"{next_text} {spark('elsewhere')}",
    )


def completion_card(slug: str, finish: str = "commit", push_failed: bool = False) -> dict:
    if push_failed:
        next_text = f"Delivery archived but {finish} failed; retry the authorized handoff with spec.py push."
    else:
        next_text = f"Delivery complete (V4 {finish}); no active package."
    state = "push" if push_failed else "delivered"
    return dict(slug=slug, state=state, stage=STAGE_FOR_STATE[state], archived=True, next=f"{next_text} {spark(state)}")

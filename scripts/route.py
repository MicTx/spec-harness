"""One next-action projection for CLI and session hooks.

The projection is read-only. `spec.py check` writes a `gate` record into the
package's runs ledger; this module reads the latest record so a failed delivery
gate becomes a first-class lifecycle state instead of being forgotten between
turns. `gate.compute` remains the authority; this module never re-implements a
gate rule. Every projected card ends with a state-keyed `Spark:` encouragement
line (see `ENCOURAGE`) so the full lifecycle invites divergent thinking.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import gitx
import plan
import ui_review
from memory import last_failures, progress_tuple, snapshot_pending
from package import (
    STALE_EVIDENCE_SUFFIX,
    criteria_status,
    latest_gate,
    latest_runs,
    package_baseline,
    package_dir,
    package_kind,
    parse_charter,
    parse_ledger,
    parse_tasks,
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

# A long verify command is never shown half-cut in a brief: a truncated
# command looks executable but is not. The marker names the read-only CLI
# projection that always carries the full command.
VERIFY_TRUNCATION_NOTE = "[verify truncated; run spec.py next for full command]"
VERIFY_BRIEF_LIMIT = 120

# Prompt tier: `full` keeps the standard brief budget, `compact` trims it for
# token-tight hosts. The installer records the tier as a `tier=` line in the
# installed skill's .spec-skill-install marker; a valid SPEC_PROMPT_TIER env
# value overrides the marker, and anything unknown falls back to `full`.
PROMPT_TIER_ENV = "SPEC_PROMPT_TIER"
PROMPT_TIER_VALUES = ("full", "compact", "aggressive")
BRIEF_BUDGET = 1450
COMPACT_BRIEF_BUDGET = 900
AGGRESSIVE_BRIEF_BUDGET = 700
INSTALL_MARKER = Path(__file__).resolve().parent.parent / ".spec-skill-install"

# Bounds for the additive recovery block. They keep a corrupt or hostile
# snapshot from growing the card without limit; `memory.snapshot_pending`
# already sanitizes, and this is a second, renderer-side guarantee.
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
    """Return the session-brief truncation budget for the active prompt tier."""
    tier = prompt_tier(INSTALL_MARKER)
    if tier == "aggressive":
        return AGGRESSIVE_BRIEF_BUDGET
    if tier == "compact":
        return COMPACT_BRIEF_BUDGET
    return BRIEF_BUDGET


# One encouraging line per lifecycle state, appended to every next-action
# card and session brief. The channel is additive only: it invites divergent
# thinking but never overrides the gate, boundary, or ownership semantics the
# card already carries.
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
    """Turn the gate errors into one executable repair action.

    Stale-only failures are curable: `spec.py check` re-runs the final
    verification and refreshes the evidence. When a non-stale error is also
    present, that concrete repair is named first; a no-op `verify --all` is
    never offered after every task is already checked.
    """
    cleaned = [error for error in errors if str(error).strip()]
    stale = [error for error in cleaned if error.rstrip().endswith(STALE_EVIDENCE_SUFFIX)]
    if cleaned and len(stale) == len(cleaned):
        return "Rerun spec.py check — it self-heals stale evidence through the final re-verify."
    concrete = [error for error in cleaned if error not in stale]
    text = " ".join(concrete)
    if "UI review" in text:
        return "Complete the UI/UX review record with real flows and screenshots, then rerun spec.py check."
    if "changed path outside" in text:
        return "Move the change inside the owning task boundary or amend the plan, then rerun spec.py check."
    if "branch binding" in text:
        return "Switch to the package's bound branch, then rerun spec.py check."
    if "criterion not proven" in text:
        return "Re-run the criterion's task verify, then rerun spec.py check."
    first = concrete[0] if concrete else (cleaned[0] if cleaned else "delivery gate failed")
    return f"Fix the recorded delivery-gate failure ({first}), then rerun spec.py check."


def _single_line(value: object, limit: int) -> str:
    """Normalize untrusted snapshot/ledger text to one bounded readable line.

    Control characters are stripped before whitespace folding so they can
    never forge a new `Next:`/`Failure:` line in the brief; the result is
    plain advisory prose, never executed.
    """
    if not isinstance(value, str):
        return ""
    cleaned = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value)
    text = " ".join(cleaned.split())
    if limit > 0 and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _latest_failed_run(package: Path, task_id: str, command: str = "") -> dict:
    """Return the task's latest failed run record, if it is still current.

    `latest_runs` keeps only the last record per task, so a later success
    erases the failure rung naturally. A recorded command that no longer
    matches the live card is stale evidence for another command and is
    ignored instead of quoting its directive against the current task.
    """
    record = latest_runs(package).get(task_id, {})
    if not isinstance(record, dict) or record.get("kind", "run") != "run":
        return {}
    if record.get("exit") in (None, 0):
        return {}
    recorded_cmd = record.get("cmd")
    if isinstance(recorded_cmd, str) and recorded_cmd and command and recorded_cmd != command:
        return {}
    return record


def _recovery_projection(
    package: Path, *, task: dict | None, blocked: list[dict], final_failed: dict | None = None
) -> dict:
    """Read advisory clues for the live ready task, never snapshot authority.

    A blocked or final-failed task may supply a failure directive, but never
    pending clues: those apply only to the current ready task. A successful
    or superseded run naturally drops its old directive.
    """
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
        "criteria": [
            {"id": key, "text": text, "task": task_id, "passed": passed} for key, text, task_id, passed in latest
        ],
        "failures": failures,
        "suggestions": suggestions,
        "gate": {"passed": not gate_failures, "errors": gate_failures},
        "ui_review": ui_status,
        # `progress` keeps its original 3-field public shape; the hook uses
        # `progress_token` so a failed gate still counts as fresh evidence.
        "progress": list(progress[:3]),
        "progress_token": list(progress),
    }
    # Additive recovery block, computed once here so `next_card`, `brief` and
    # the session hook reuse the same projection: one decide per event, one
    # snapshot read, no re-derivation while rendering.
    result["recovery"] = _recovery_projection(
        package,
        task=result["task"],
        blocked=[] if ready else blocked,
        final_failed=final_failed,
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
    if state == "check-failed":
        return gate_next(brief["gate"]["errors"])
    if state == "stalled" and brief.get("ui_review", {}).get("errors"):
        return "Write UI/UX review findings and real-operation screenshots back, then rerun spec.py check."
    if state == "parked-only":
        return (
            "Present all parked decisions in one batch; do not reopen answered questions."
            " Every ask carries its evidence ledger."
        )
    if state == "stalled":
        recovery = brief.get("recovery") or {}
        if recovery.get("final_failed"):
            return (
                f"Final verification failed for {recovery['task']}; fix the failure, then run spec.py done "
                "to re-verify the final tree and retry delivery."
            )
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
    """Project one next-action card; reuse a caller's projection when given.

    `decision` is the historical third positional parameter (a `decide`
    result); `projection` accepts either a `decide` result or a card this
    function already returned. Both reuse the caller's single computation:
    no second `decide`, no snapshot re-read, no re-appended Spark line.
    """
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
        # A command stays verbatim or is wholly replaced by a lookup. Even a
        # short command may need the lookup when its surrounding title grows.
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
    if data.get("state") == "stalled" and blocked and not data.get("ready"):
        action = f'Next: spec.py unblock {blocked[0]} --reason "<new evidence>"'
    elif data.get("state") == "stalled" and recovery.get("final_failed"):
        action = f"Next: fix {recovery['task']}; then run spec.py done"
    else:
        action = "Next: " + _next_without_spark(data).split(" Last failure rung:", 1)[0]
    if len(action) <= limit and action.isprintable():
        return action
    # Arbitrary gate errors may themselves contain command fragments. Do not
    # disguise a prefix of them as an executable Next action.
    return "Next: [action truncated; run spec.py next for full card]"


def _render_brief(data: dict, budget: int) -> str:
    """Reserve the execution core before spending space on goals and parked text.

    Header, atomic Next, first failure, matching pending clue and full Spark
    all get a floor. Grow recovery prose before task titles; optional rows
    only spend the remainder. Hooks reserve their own tail via `budget`, so
    no final prefix slice is needed and neither Spark nor a command is cut.
    """
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

    # The practical hook floor is >=470 chars. Start with small, meaningful
    # portions so a long failure never starves a pending clue (or vice versa).
    caps = {key: min(len(text), 80 if key == "pending" else 64) for key, text in details.items()}
    action_limit = 96
    action = _brief_action(data, action_limit)
    fixed = [header, action, *([tail] if tail else [])]
    remaining = budget - sum(map(len, fixed)) - sum(caps.values()) - (len(fixed) + len(details) - 1)
    if remaining < 0:
        # A caller may ask for an exceptionally tiny custom budget. Shrink
        # prose first, never an emitted command, and retain the full Spark
        # whenever it fits at all. Normal tier/hook budgets avoid this path.
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
    """Render the session brief; reuse a projected card when one is supplied.

    `budget` (a hook may pass its tier budget minus its own extras) overrides
    the tier default; `projection` reuses a card from `next_card` so one event
    renders exactly one projection.
    """
    source = projection if projection is not None else decision
    if source is not None and source.get("next"):
        data = dict(source)
    else:
        data = next_card(root, slug, decision=decision, projection=projection)
    limit = budget if isinstance(budget, int) and budget > 0 else brief_budget()
    return _render_brief(data, limit)


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
    encouragement = spark(state)
    if encouragement:
        next_text = f"{next_text} {encouragement}"
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
    next_text = f"Active package is in worktree {item['worktree']}; continue there with spec.py next."
    encouragement = spark("elsewhere")
    if encouragement:
        next_text = f"{next_text} {encouragement}"
    return {
        "slug": item["slug"],
        "state": "elsewhere",
        "stage": STAGE_FOR_STATE["elsewhere"],
        "worktree": item["worktree"],
        "next": next_text,
    }


def completion_card(slug: str, finish: str = "commit", push_failed: bool = False) -> dict:
    """Project the lifecycle link after `done`/`check` archived the package."""
    if finish == "push" and push_failed:
        next_text = "Delivery archived but push failed; fix the remote and run spec.py push."
    else:
        next_text = f"Delivery complete (V4 {finish}); no active package."
    encouragement = spark("push" if (finish == "push" and push_failed) else "delivered")
    if encouragement:
        next_text = f"{next_text} {encouragement}"
    return {
        "slug": slug,
        "state": "push" if (finish == "push" and push_failed) else "delivered",
        "stage": "push" if (finish == "push" and push_failed) else "done",
        "archived": True,
        "next": next_text,
    }

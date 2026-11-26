"""Hook-owned session snapshots and compact recovery clues."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from package import latest_runs, now, runs

PENDING_LIMIT = 5
PENDING_CLUE_MAX = 160
PROMPT_SESSIONS = 8
# Reject clues that could impersonate hook protocol lines.
PROTOCOL_PREFIXES = ("Next:", "Pending:", "Parked:", "Failure:", "Spec ", "Scope:", "Charter:")


def _clean_pending(value: object) -> list[str]:
    """Coerce any host payload into bounded, single-line string clues."""
    if not isinstance(value, list):
        return []
    clues: list[str] = []
    for item in value[: PENDING_LIMIT * 4]:
        if not isinstance(item, str):
            continue
        prefix = item[: PENDING_CLUE_MAX * 8]
        if any(line.strip().startswith(PROTOCOL_PREFIXES) for line in prefix.splitlines()):
            continue
        text = "".join(char if char.isprintable() else " " for char in prefix)
        clue = " ".join(text.split())[:PENDING_CLUE_MAX]
        if clue and clue not in clues:
            clues.append(clue)
        if len(clues) >= PENDING_LIMIT:
            break
    return clues


def _pending_task(state: dict) -> str:
    if "pending_task" in state:
        anchor = state["pending_task"]
        if isinstance(anchor, dict):
            anchor = anchor.get("id")
    else:
        # Older snapshots carried the owner only in their task projection.
        task = state.get("task")
        anchor = task.get("id", "") if isinstance(task, dict) else ""
    return anchor if isinstance(anchor, str) else ""


def snapshot_write(package: Path, event: dict, brief: dict) -> None:
    task = brief.get("task") or {}
    task_id = task.get("id") if isinstance(task, dict) else ""
    attempts = len(
        [
            record
            for record in runs(package)
            if record.get("kind", "run") == "run" and task_id and record.get("task") == task_id
        ]
    )
    previous = snapshot_read(package)
    # Omitted clues preserve their original task anchor; empty clears them.
    explicit = "pending" in event
    pending = _clean_pending(event.get("pending")) if explicit else _clean_pending(previous.get("pending"))
    anchor = task_id if explicit else _pending_task(previous)
    if not isinstance(anchor, str) or not pending:
        anchor = ""
    data = {
        **previous,
        "updated": now(),
        "task": task or None,
        "attempts": attempts,
        "last_failures": brief.get("failures") or last_failures(package),
        "progress": brief.get("progress"),
        "pending": pending,
        "pending_task": anchor,
    }
    (package / "state.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot_pending(package: Path, task: dict | None) -> list[str]:
    """Read advisory clues only for the task that saved them."""
    if not isinstance(task, dict):
        return []
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        return []
    state = snapshot_read(package)
    if _pending_task(state) != task_id:
        return []
    return _clean_pending(state.get("pending"))


def snapshot_read(package: Path) -> dict:
    try:
        value = json.loads((package / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def progress_tuple(package: Path, head: str, done: int) -> tuple[int, int, str, str]:
    """Include gate outcome without treating timestamps as progress."""
    records = runs(package)
    signatures = len({record["sig"] for record in records if record.get("exit") not in (None, 0) and record.get("sig")})
    execution_gates = (row for row in reversed(records) if row.get("kind") == "gate" and not row.get("probe"))
    gate = next(execution_gates, {})
    gate_sig = ""
    if gate:
        payload = json.dumps(
            {"passed": gate.get("passed"), "head": gate.get("head"), "errors": gate.get("errors", [])},
            sort_keys=True,
        )
        gate_sig = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return done, signatures, head, gate_sig


def last_failures(package: Path) -> list[str]:
    latest = latest_runs(package)
    return [
        f"{task}: {record.get('sig') or 'verify failed without a signature'}"
        for task, record in latest.items()
        if record.get("exit") not in (None, 0)
    ][-3:]


def seen(package: Path, event: dict) -> bool:
    path = package / "state.json"
    state = snapshot_read(package)
    key = json.dumps(event, sort_keys=True, default=str)
    current = time.time()
    duplicate = state.get("seen_key") == key and current - float(state.get("seen_at", 0)) < 2
    if duplicate:
        return True
    state.update({"seen_key": key, "seen_at": current})
    path.write_text(json.dumps(state) + "\n", encoding="utf-8")
    return False


def prompt_token(package: Path, brief: dict) -> str:
    """Deduplicate meaningful routing changes rather than ledger timestamps."""
    unblock = next((record for record in reversed(runs(package)) if record.get("kind") == "unblock"), {})
    payload = {
        "progress": brief.get("progress_token") or brief.get("progress"),
        "state": brief.get("state"),
        "task": brief.get("task"),
        "ready": brief.get("ready"),
        "parked": brief.get("parked"),
        "recovery": brief.get("recovery"),
        "unblock": {key: unblock.get(key) for key in ("task", "reason", "ts")},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def prompt_fresh(package: Path, session: str, token: str) -> bool:
    """Deduplicate per session; hosts without an ID use the legacy marker."""
    state = snapshot_read(package)
    if session:
        key = hashlib.sha256(session.encode()).hexdigest()
        saved = state.get("prompt_tokens")
        tokens = (
            {key: value for key, value in saved.items() if isinstance(value, str) and len(value) == 64}
            if isinstance(saved, dict)
            else {}
        )
        if tokens.get(key) == token:
            return False
        tokens.pop(key, None)
        tokens[key] = token
        state["prompt_tokens"] = dict(list(tokens.items())[-PROMPT_SESSIONS:])
    else:
        if state.get("last_prompt_progress") == token:
            return False
        state["last_prompt_progress"] = token
    (package / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True

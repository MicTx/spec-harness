"""Loop continuity pins: compact restore, gate-aware dedup, delivery routing,
brief avalanche survival, and single route projections per event."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from tests.v3_support import cli, git, make_package, repo, started_package

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
for entry in (str(HOOKS), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import gitx  # noqa: E402
import memory  # noqa: E402
import package as disk  # noqa: E402
import route  # noqa: E402
from spec_hook import Action, handle  # noqa: E402

EVENT_BASE = {"cwd": None, "session_id": "s1"}

VERIFY_HEAD = "VHMARK7Q"
VERIFY_TAIL = "Z4VTMARK"
LONG_VERIFY = f"true {VERIFY_HEAD}" + "A" * 4000 + f"{VERIFY_TAIL}"


def _event(root: Path, **extra) -> dict:
    return {**EVENT_BASE, "cwd": str(root), **extra}


def _write_state(package: Path, values: dict) -> None:
    state = memory.snapshot_read(package)
    state.update(values)
    (package / "state.json").write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")


def _block(package: Path, task: str, command: str) -> None:
    disk.append_run(package, {"kind": "run", "task": task, "cmd": command, "exit": 1, "blocked": True})


def _gate(
    package: Path,
    root: Path,
    errors: list[str],
    passed: bool = False,
    timestamp: str = "2026-11-20T00:00:00+00:00",
) -> None:
    disk.append_run(
        package,
        {"kind": "gate", "passed": passed, "head": gitx.head(root), "errors": errors, "ts": timestamp},
    )


def _next_json(root: Path) -> dict:
    result = cli(root, "next")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _avalanche(root: Path) -> Path:
    """Long goal + long verify + failing evidence + parked row + many lessons."""
    package = make_package(root, verify=LONG_VERIFY)
    spec = package / "spec.md"
    text = spec.read_text(encoding="utf-8")
    text = text.replace("- goal: Change the fixture value", "- goal: " + "G" * 1600)
    text = text.replace("## Parked\n", "## Parked\n- P1 | PARKANCHOR " + "P" * 1600 + "\n")
    spec.write_text(text, encoding="utf-8")
    tasks = package / "tasks.md"
    tasks.write_text(
        tasks.read_text(encoding="utf-8").replace("Change value", "Change value " + "T" * 1600),
        encoding="utf-8",
    )
    lessons = root / ".spec" / "lessons.md"
    lessons.parent.mkdir(parents=True, exist_ok=True)
    lessons.write_text(
        "# Spec Lessons\n"
        + "\n".join(f"- L{i} | when: filler {'l' * 140}" for i in range(1, 50))
        + "\n- L50 | when: avalanche | do: LMARK keep the readable lesson\n",
        encoding="utf-8",
    )
    assert cli(root, "plan-go").returncode == 0
    disk.append_run(
        package,
        {
            "kind": "run",
            "task": "task-value",
            "cmd": LONG_VERIFY,
            "exit": 1,
            "sig": "FAILLADDER avalanche signature " + "F" * 1600,
            "attempt": 1,
            "head": gitx.head(root),
            "duration": 0.1,
            "ts": "2026-11-20T00:00:00+00:00",
            "tree_fp": "0" * 64,
        },
    )
    return package


def _pending_texts(card: dict) -> list[str]:
    recovery = card.get("recovery")
    assert isinstance(recovery, dict), card.get("recovery")
    pending = recovery.get("pending")
    assert isinstance(pending, list), pending
    return pending


def _two_tasks(root: Path, verify: str = "true") -> Path:
    package = make_package(root, verify=verify)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- C1 | Fixture works | task-value",
            "- C1 | Fixture works | task-value\n- C2 | Independent works | task-next",
        ),
        encoding="utf-8",
    )
    tasks = package / "tasks.md"
    tasks.write_text(
        tasks.read_text(encoding="utf-8")
        + "- [ ] Check the independent result\n"
        + "  - id: task-next\n  - boundary: app.py\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    result = cli(root, "plan-go")
    assert result.returncode == 0, result.stderr
    return package


def _assert_budgeted_core(text: str, state: str, *anchors: str) -> None:
    assert len(text) <= route.BRIEF_BUDGET, (len(text), text)
    assert "Next:" in text
    assert text.count("Next:") == 1
    assert "Spark:" not in text
    for anchor in anchors:
        assert anchor in text, (anchor, text)
    # Optional fields can be omitted, but never push essentials after them.
    for optional in ("goal:", "Charter:", "Lessons:"):
        if optional in text:
            assert text.index("Next:") < text.index(optional)
            for anchor in anchors:
                assert text.index(anchor) < text.index(optional), (anchor, optional, text)


# --- compact snapshots: pending clues survive, stay bounded and advisory ---


def test_pre_compact_pending_survives_end_event_without_pending(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)

    handle(
        "pre_compact",
        _event(root, pending=["try the reverse index first", "ask about the cache budget"]),
    )
    # An `end` event without a pending key must not erase the saved clues.
    handle("end", _event(root))

    state = memory.snapshot_read(package)
    assert state["pending"] == ["try the reverse index first", "ask about the cache budget"]


def test_explicit_empty_pending_clears_saved_clues(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)

    handle("pre_compact", _event(root, pending=["stale direction"]))
    handle("end", _event(root, pending=[]))

    assert memory.snapshot_read(package)["pending"] == []
    assert _pending_texts(_next_json(root)) == []


def test_compact_start_context_carries_pending_block(tmp_path):
    root = tmp_path
    repo(root)
    started_package(root)

    handle("pre_compact", _event(root, pending=["resume task-value from the failing signature"]))
    action = handle("start", _event(root, source="compact"))

    assert isinstance(action.context, str)
    assert "Pending:" in action.context
    assert "resume task-value from the failing signature" in action.context
    assert "advisory" in action.context.lower()
    assert "do not replan" in action.context


def test_next_card_projects_pending_readonly_for_matching_task(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)

    handle("pre_compact", _event(root, pending=["check the reverse index"]))
    card = _next_json(root)
    assert _pending_texts(card) == ["check the reverse index"]

    # Neither the Python nor CLI projection changes snapshot/evidence/tasks.
    before = {name: (package / name).read_bytes() for name in ("state.json", "runs.jsonl", "tasks.md", "spec.md")}
    assert _pending_texts(route.next_card(root)) == ["check the reverse index"]
    assert _pending_texts(_next_json(root)) == ["check the reverse index"]
    assert {name: (package / name).read_bytes() for name in before} == before


def test_snapshot_write_preserves_missing_and_explicitly_clears_pending(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path)
    projection = route.decide(tmp_path)
    memory.snapshot_write(package, {"pending": ["direct snapshot clue"]}, projection)
    memory.snapshot_write(package, {}, projection)
    assert memory.snapshot_read(package)["pending"] == ["direct snapshot clue"]
    assert _pending_texts(_next_json(tmp_path)) == ["direct snapshot clue"]
    memory.snapshot_write(package, {"pending": []}, projection)
    assert memory.snapshot_read(package)["pending"] == []


def test_end_after_new_ready_task_keeps_pending_anchored_to_old_task(tmp_path):
    repo(tmp_path)
    package = _two_tasks(tmp_path)
    handle("pre_compact", _event(tmp_path, pending=["only task-value owns this clue"]))
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert route.next_card(tmp_path)["task"]["id"] == "task-next"
    handle("end", _event(tmp_path))

    assert memory.snapshot_read(package)["pending"] == ["only task-value owns this clue"]
    assert _pending_texts(_next_json(tmp_path)) == []
    context = handle("start", _event(tmp_path, source="compact")).context
    assert "task-next" in context
    assert "only task-value owns this clue" not in context
    # An explicit new clue is correctly attributed to the now-current task.
    handle("pre_compact", _event(tmp_path, pending=["now resume task-next"]))
    assert _pending_texts(_next_json(tmp_path)) == ["now resume task-next"]


def test_end_after_task_change_does_not_rebind_pending_to_new_task(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)

    handle("pre_compact", _event(root, pending=["resume task-value from the failing signature"]))
    assert cli(root, "verify", "task-value").returncode == 0
    # `end` on a package whose current task moved on must keep the clue list
    # anchored to the task that owned it, not rebind to "no current task".
    handle("end", _event(root))

    state = memory.snapshot_read(package)
    assert state["pending"] == ["resume task-value from the failing signature"]
    # No matching current task anymore: nothing is restored into the card.
    assert _pending_texts(_next_json(root)) == []
    action = handle("start", _event(root, source="compact"))
    assert "Pending:" not in (action.context or "")
    assert "resume task-value" not in (action.context or "")


def test_legacy_snapshot_without_anchor_falls_back_to_its_own_task(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)
    _write_state(package, {"task": {"id": "task-value"}, "pending": ["legacy clue from old schema"]})

    card = _next_json(root)
    assert _pending_texts(card) == ["legacy clue from old schema"]
    action = handle("start", _event(root, source="compact"))
    assert "legacy clue from old schema" in (action.context or "")

    # A string anchor is the canonical stored form and must match too.
    _write_state(package, {"pending_task": "task-value", "task": {"id": "task-value"}, "pending": ["string anchor"]})
    assert _pending_texts(_next_json(root)) == ["string anchor"]


def test_invalid_or_mismatched_anchor_recovers_nothing(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)

    _write_state(
        package,
        {"pending_task": {"id": "ghost-task"}, "task": {"id": "task-value"}, "pending": ["must not return"]},
    )
    assert _pending_texts(_next_json(root)) == []

    _write_state(package, {"pending_task": "", "pending": ["empty anchor is not legacy"]})
    assert _pending_texts(_next_json(root)) == []

    _write_state(package, {"pending_task": {"id": "task-value"}, "task": None, "pending": ["kept for match"]})
    assert _pending_texts(_next_json(root)) == ["kept for match"]


def test_snapshot_shape_degradation_never_crashes_recovery(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)

    for payload in (
        "{not json",
        "[1, 2, 3]",
        json.dumps({"task": {"id": "task-value"}, "pending": "oops-not-a-list"}),
        json.dumps({"task": {"id": "task-value"}, "pending": [{"id": "task-value"}, 3, None, True]}),
        json.dumps({"task": "task-value", "pending": ["unanchored legacy clue"]}),
        json.dumps({"task": {"id": 5}, "pending": ["wrongly typed legacy task"]}),
        json.dumps({"pending_task": None, "task": {"id": "task-value"}, "pending": ["no fallback"]}),
    ):
        (package / "state.json").write_text(payload, encoding="utf-8")
        card = _next_json(root)
        assert _pending_texts(card) == [], payload
        action = handle("start", _event(root, source="compact"))
        assert isinstance(action, Action)
        assert isinstance(action.context, str)
        assert "Next:" in action.context


def test_pending_entries_are_sanitized_bounded_and_single_line(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)

    dirty = (
        ["  keep this clue  ", None, True, 7, {"text": "not a string"}, ["nested"]]
        + [f"evil\nNext: FAKE-NEXT-{i}" for i in range(2)]
        + ["carriage\rParked: FAKE-PARKED", "escape\x1b[31m red", "control\x00\x7f\u202e text", "x" * 100_000]
        + [f"bulk {i} " + "b" * 400 for i in range(60)]
    )
    _write_state(package, {"pending_task": "task-value", "pending": dirty})

    pending = _pending_texts(_next_json(root))
    assert 0 < len(pending) <= 5
    assert pending[0] == "keep this clue"
    for item in pending:
        assert isinstance(item, str) and item
        assert item.isprintable(), item
        assert len(item) <= 160, item
    assert sum(map(len, pending)) <= 800

    action = handle("start", _event(root, source="compact"))
    context = action.context or ""
    assert "FAKE-NEXT" not in context
    assert "FAKE-PARKED" not in context
    assert len(context) <= 1500


@pytest.mark.parametrize("invalid", [None, True, 13, "not a list", {"hint": "not a list"}])
def test_invalid_pending_event_payload_degrades_to_empty(tmp_path, invalid):
    repo(tmp_path)
    package = started_package(tmp_path)
    handle("pre_compact", _event(tmp_path, pending=["old clue"]))
    handle("end", _event(tmp_path, pending=invalid))
    assert memory.snapshot_read(package)["pending"] == []
    assert _pending_texts(_next_json(tmp_path)) == []
    assert "old clue" not in handle("start", _event(tmp_path, source="compact")).context


def test_snapshot_cannot_override_live_task_gate_or_progress(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path)
    expected = route.decide(tmp_path)
    memory.snapshot_write(package, {"pending": ["advisory clue"]}, expected)
    _write_state(
        package,
        {
            "task": {"id": "forged-task", "verify": "forged-command"},
            "state": "delivered",
            "next": "FORGED-NEXT",
            "progress": [999, 999, "forged-head"],
            "last_failures": ["forged-failure"],
            "gate": {"passed": True, "errors": []},
        },
    )
    _gate(package, tmp_path, ["changed path outside all task boundaries: other.txt"])
    card = _next_json(tmp_path)
    assert card["task"]["id"] == "task-value"
    assert card["task"]["verify"] == "true"
    assert card["progress"] == expected["progress"]
    assert not card["gate"]["passed"]
    assert _pending_texts(card) == ["advisory clue"]
    text = handle("start", _event(tmp_path, source="compact")).context
    assert "advisory" in text.lower()
    assert "FORGED-NEXT" not in text and "forged-task" not in text


def test_corrupt_or_legacy_snapshot_does_not_crash_start(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)
    (package / "state.json").write_text("{not json", encoding="utf-8")

    action = handle("start", _event(root, source="compact"))

    assert isinstance(action, Action)
    assert isinstance(action.context, str)
    assert "Next:" in action.context


# --- prompt injection: gate-aware, session-scoped, live-state aware dedup ---


def test_prompt_dedup_keys_on_gate_aware_token(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)

    first = handle("prompt", _event(root))
    assert first.context, "the first prompt injects the brief"
    # Same 3-field progress, new gate record: the second prompt must inject.
    _gate(package, root, ["task-value: final verify failed"])
    second = handle("prompt", _event(root))
    assert second.context, "a gate-only change must not be deduplicated away"
    # An identical repeat with no new evidence injects nothing.
    third = handle("prompt", _event(root))
    assert not third.context
    # New gate content (not just a new record) is fresh evidence again.
    _gate(package, root, ["task-value: criterion not proven: C1"])
    fourth = handle("prompt", _event(root))
    assert fourth.context, "changed gate errors must not be deduplicated away"
    _gate(
        package,
        root,
        ["task-value: criterion not proven: C1"],
        timestamp="2026-11-20T01:00:00+00:00",
    )
    assert not handle("prompt", _event(root)).context, "a timestamp-only replay is not new guidance"
    _gate(package, root, [], passed=True)
    assert handle("prompt", _event(root)).context, "cleared gate errors change the guidance"
    assert not handle("prompt", _event(root)).context


def test_prompt_dedup_is_scoped_per_session(tmp_path):
    root = tmp_path
    repo(root)
    started_package(root)

    assert handle("prompt", _event(root, session_id="a")).context
    assert handle("prompt", _event(root, session_id="b")).context, "session b is not session a"
    assert not handle("prompt", _event(root, session_id="a")).context, "session a repeat stays deduplicated"
    assert not handle("prompt", _event(root, session_id="b")).context


def test_prompt_reinjects_when_live_task_or_state_moves(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)

    assert handle("prompt", _event(root)).context

    tasks = package / "tasks.md"
    tasks.write_text(
        tasks.read_text(encoding="utf-8").replace("- verify: `true`", "- verify: `test -f app.py`"),
        encoding="utf-8",
    )
    changed_command = handle("prompt", _event(root))
    assert changed_command.context, "a changed verify command must re-inject"

    _block(package, "task-value", "test -f app.py")
    blocked = handle("prompt", _event(root))
    assert blocked.context, "a blocked task must re-inject"

    disk.append_run(
        package,
        {"kind": "unblock", "task": "task-value", "reason": "fresh evidence", "ts": "2026-11-20T01:00:00+00:00"},
    )
    unblocked = handle("prompt", _event(root))
    assert unblocked.context, "an unblock record must re-inject"


def test_prompt_task_identity_change_reinjects_with_unchanged_progress(tmp_path):
    repo(tmp_path)
    package = _two_tasks(tmp_path)
    before = route.decide(tmp_path)
    assert handle("prompt", _event(tmp_path)).context
    _block(package, "task-value", "true")
    after = route.decide(tmp_path)
    assert after["progress_token"] == before["progress_token"]
    assert after["state"] == before["state"] == "run"
    assert after["task"]["id"] == "task-next"
    switched = handle("prompt", _event(tmp_path))
    assert "task-next" in switched.context
    assert not handle("prompt", _event(tmp_path)).context


def test_prompt_parked_only_transition_reinjects_with_unchanged_progress(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    before = route.decide(tmp_path)
    assert before["state"] == "deliver"
    assert handle("prompt", _event(tmp_path)).context
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("## Parked\n", "## Parked\n- P1 | Wait for irreversible choice\n"),
        encoding="utf-8",
    )
    after = route.decide(tmp_path)
    assert after["progress_token"] == before["progress_token"]
    assert after["state"] == "parked-only"
    assert "parked-only" in handle("prompt", _event(tmp_path)).context
    assert not handle("prompt", _event(tmp_path)).context


def test_prompt_state_changes_reinject_beyond_gate_signature(tmp_path):
    root = tmp_path
    repo(root)
    started_package(root)

    assert handle("prompt", _event(root)).context
    # A passing verify moves done-count and lifecycle state; the next prompt
    # must notice even though no gate record exists.
    assert cli(root, "verify", "task-value").returncode == 0
    after = handle("prompt", _event(root))
    assert after.context


def test_legacy_three_field_progress_state_still_works(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)

    legacy = route.decide(root)["progress"]
    _write_state(package, {"last_prompt_progress": legacy})

    # A legacy marker never suppresses a different session's first injection.
    fresh = handle("prompt", _event(root, session_id="legacy-fresh"))
    assert fresh.context
    # And the legacy marker is upgraded so the immediate repeat is quiet.
    assert not handle("prompt", _event(root, session_id="legacy-fresh")).context
    # Hosts without a session id still upgrade harmlessly and notice new gates.
    handle("prompt", {"cwd": str(root)})
    assert not handle("prompt", {"cwd": str(root)}).context
    _gate(package, root, ["criterion not proven: C1"])
    assert handle("prompt", {"cwd": str(root)}).context


# --- gate routing: preflight, real blockers, unknown honesty ---


def test_stop_does_not_start_from_frozen_planning_without_execution(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert not handle("stop", _event(tmp_path)).block


def test_probe_and_read_only_session_do_not_arm_check_loop(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    handle("prompt", _event(tmp_path))
    assert cli(tmp_path, "check", "--check-only").returncode == 0
    assert not handle("stop", _event(tmp_path)).block


def test_diagnostic_only_session_without_host_session_id_does_not_resume(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    event = {"cwd": str(tmp_path)}
    handle("prompt", event)
    assert handle("stop", event) == Action()


def test_legacy_diagnostic_text_does_not_authorize_an_action(tmp_path):
    repo(tmp_path)
    directory = started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    for text in ("outdated verification", "UI review: fictional error", "changed path outside scope"):
        _gate(directory, tmp_path, [text])
        card = _next_json(tmp_path)
        assert card["gate"]["action"] is None
        assert "spec.py check --check-only" in card["next"]
        assert text in card["gate"]["errors"]


def test_ready_delivery_uses_check_when_verify_all_has_no_work(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert cli(tmp_path, "check", "--check-only").returncode == 0
    card = _next_json(tmp_path)
    assert card["gate"]["action"] is None
    assert card["state"] == "deliver"
    assert card["ready"] == []
    assert "spec.py check" in card["next"]
    assert "verify --all" not in card["next"]
    before = (package / "runs.jsonl").read_bytes()
    result = cli(tmp_path, "verify", "--all")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["results"] == []
    assert (package / "runs.jsonl").read_bytes() == before


def test_route_surfaces_recorded_gate_and_its_repair_action(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)
    assert cli(root, "verify", "task-value").returncode == 0
    (root / "other.txt").write_text("staged\n")
    git(root, "add", "other.txt")
    assert cli(root, "check", "--check-only").returncode == 1

    card = route.next_card(root)

    assert card["state"] == "check-failed"
    assert {item["code"] for item in card["gate"]["findings"]} == {"boundary-index"}
    assert "Inspect ownership of other.txt" in card["next"]

    _gate(package, root, [], passed=True)
    card = route.next_card(root)
    assert card["state"] != "check-failed"
    assert card["gate"]["errors"] == []


# --- brief budgets: actionable context first, no half commands, no avalanche ---


def test_brief_keeps_next_under_goal_avalanche(tmp_path):
    repo(tmp_path)
    _avalanche(tmp_path)
    brief = route.brief(tmp_path)
    _assert_budgeted_core(brief, "run", "task-value", "FAILLADDER", "spec.py next")
    assert "G" * 400 not in brief
    # Never show a syntactically plausible but cut-off shell command.
    assert VERIFY_HEAD not in brief and VERIFY_TAIL not in brief
    assert not re.search(r"A{80}", brief)
    assert "truncat" in brief.lower() or "full" in brief.lower()
    for card in (route.next_card(tmp_path), _next_json(tmp_path)):
        assert card["task"]["verify"] == LONG_VERIFY
        assert isinstance(card.get("recovery"), dict)


def test_start_injection_respects_the_single_brief_budget(tmp_path):
    repo(tmp_path)
    _avalanche(tmp_path)
    handle("pre_compact", _event(tmp_path, pending=["RESUME-CLUE check the failing case"]))
    context = handle("start", _event(tmp_path, source="compact")).context
    _assert_budgeted_core(context, "run", "task-value", "FAILLADDER", "spec.py next", "RESUME-CLUE")
    assert "do not replan" in context
    assert "advisory" in context.lower()
    assert VERIFY_HEAD not in context and VERIFY_TAIL not in context
    assert not re.search(r"A{80}", context)


def test_parked_brief_retains_decision_id_before_optional_avalanche(tmp_path):
    repo(tmp_path)
    package = _avalanche(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert _next_json(tmp_path)["state"] == "parked-only"
    for context in (route.brief(tmp_path), handle("start", _event(tmp_path, source="compact")).context):
        _assert_budgeted_core(context, "parked-only", "P1", "PARKANCHOR")
        assert "parked" in context.lower()
    assert memory.snapshot_read(package).get("pending", []) == []


def test_repeated_start_injections_do_not_snowball(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root)
    spec = package / "spec.md"
    text = spec.read_text(encoding="utf-8")
    spec.write_text(
        text.replace("- goal: Change the fixture value", "- goal: " + "G" * 1600),
        encoding="utf-8",
    )

    lengths = [len(handle("start", _event(root)).context or "") for _ in range(4)]

    assert max(lengths) <= 1500
    assert lengths[-1] <= lengths[0] + 1, lengths


def test_eight_start_injections_stay_byte_identical(tmp_path):
    repo(tmp_path)
    package = _avalanche(tmp_path)
    handle("pre_compact", _event(tmp_path, pending=["RESUME-CLUE check the failing case"]))
    before = {name: (package / name).read_bytes() for name in ("spec.md", "tasks.md", "runs.jsonl")}
    contexts = []
    briefs = []
    for _ in range(8):
        briefs.append(route.brief(tmp_path))
        contexts.append(handle("start", _event(tmp_path, source="compact")).context)
        handle("end", _event(tmp_path))
    assert len(set(contexts)) == len(set(briefs)) == 1
    _assert_budgeted_core(contexts[0], "run", "task-value", "RESUME-CLUE", "FAILLADDER")
    assert memory.snapshot_read(package)["pending"] == ["RESUME-CLUE check the failing case"]
    assert {name: (package / name).read_bytes() for name in before} == before


# --- one route projection per event ---


def test_start_and_subagent_compute_route_once(tmp_path, monkeypatch):
    root = tmp_path
    repo(root)
    started_package(root)

    calls = {"n": 0}
    original = route.decide

    def counting(root_arg, slug=None):
        calls["n"] += 1
        return original(root_arg, slug)

    monkeypatch.setattr(route, "decide", counting)

    handle("start", _event(root))
    assert calls["n"] == 1, calls

    calls["n"] = 0
    handle("subagent", _event(root))
    assert calls["n"] == 0, calls

    calls["n"] = 0
    handle("stop", _event(root, continuing=True))
    assert calls["n"] == 0, calls

    calls["n"] = 0
    handle("stop", _event(root))
    assert calls["n"] == 1, calls


# --- durable failure ladder: every next card shows the latest directive ---


def test_next_card_tracks_the_failure_ladder_until_unblock(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root, verify="test ! -e fail-marker")
    (root / "fail-marker").write_text("fail\n", encoding="utf-8")

    ladder = ("smallest fix", "minimal reproduction", "change implementation path", "park the task with evidence")
    for rung, phrase in enumerate(ladder, start=1):
        result = cli(root, "verify", "task-value")
        assert result.returncode == 1, (rung, result.stderr)
        record = disk.latest_runs(package)["task-value"]
        assert phrase in record["directive"]
        before = (package / "runs.jsonl").read_bytes()
        card = _next_json(root)
        assert record["directive"] in card["next"], (rung, card)
        assert (package / "runs.jsonl").read_bytes() == before
        assert phrase in handle("start", _event(root)).context

    assert "spec.py unblock task-value --reason" in card["next"]
    assert "runs.jsonl" in card["next"]
    rejected = cli(root, "verify", "task-value")
    assert rejected.returncode == 1 and "blocked" in rejected.stderr
    (root / "fail-marker").unlink()
    unblocked = cli(root, "unblock", "task-value", "--reason", "new evidence: marker was the reproduction")
    assert unblocked.returncode == 0, unblocked.stderr
    assert _next_json(root)["task"]["id"] == "task-value"
    assert cli(root, "verify", "task-value").returncode == 0
    recovered = _next_json(root)
    assert recovered["state"] == "deliver"
    assert "park the task with evidence" not in json.dumps(recovered)


def test_success_and_legacy_records_do_not_leak_old_directives(tmp_path):
    root = tmp_path
    repo(root)
    package = started_package(root, verify="test ! -e fail-marker")
    (root / "fail-marker").write_text("fail\n", encoding="utf-8")

    assert cli(root, "verify", "task-value").returncode == 1
    assert cli(root, "verify", "task-value").returncode == 1
    (root / "fail-marker").unlink()
    assert cli(root, "verify", "task-value").returncode == 0

    card = _next_json(root)
    text = json.dumps(card, ensure_ascii=False)
    assert "smallest fix" not in text
    assert "minimal reproduction" not in text

    # Pre-directive legacy run records keep the projection machine-readable.
    disk.append_run(package, {"kind": "run", "task": "task-value", "exit": 1, "sig": "legacy-signature"})
    legacy_card = _next_json(root)
    assert legacy_card["state"] in {"stalled", "check-failed", "run"}
    assert "minimal reproduction" not in json.dumps(legacy_card)


def test_new_ready_task_does_not_inherit_previous_tasks_failure_directive(tmp_path):
    repo(tmp_path)
    _two_tasks(tmp_path, verify="test ! -e fail-marker")
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    assert "smallest fix" in _next_json(tmp_path)["next"]
    for _ in range(3):
        assert cli(tmp_path, "verify", "task-value").returncode == 1
    next_card = _next_json(tmp_path)
    assert next_card["task"]["id"] == "task-next"
    assert "smallest fix" not in json.dumps(next_card)
    assert not next_card["recovery"].get("directive")


def test_failed_final_verification_recovers_through_check(tmp_path, monkeypatch):
    repo(tmp_path)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    command = 'python3 -c "from app import VALUE; assert VALUE == 0"'
    package = started_package(tmp_path, verify=command)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    (tmp_path / "app.py").write_text("VALUE = 7\n", encoding="utf-8")
    assert cli(tmp_path, "check").returncode == 1
    failed = disk.latest_runs(package)["task-value"]
    assert failed["final"] is True and failed["exit"] != 0
    assert disk.parse_tasks((package / "tasks.md").read_text())[0].checked
    before = {name: (package / name).read_bytes() for name in ("tasks.md", "runs.jsonl")}
    card = _next_json(tmp_path)
    assert card["task"] is None and card["state"] == "check-failed"
    assert card["recovery"]["task"] == "task-value"
    assert card["recovery"]["final_failed"] is True
    assert "spec.py check" in card["next"]
    assert "verify --all" not in card["next"]
    assert {name: (package / name).read_bytes() for name in before} == before
    context = handle("start", _event(tmp_path, source="compact")).context
    _assert_budgeted_core(context, "check-failed", "task-value", "spec.py check")
    (tmp_path / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    delivered = cli(tmp_path, "check")
    assert delivered.returncode == 0, delivered.stderr
    final_card = _next_json(tmp_path)
    assert final_card["state"] == "delivered"
    assert "smallest fix" not in json.dumps(final_card)


def test_final_failure_recovery_matches_current_command_and_task_priority(tmp_path):
    repo(tmp_path)
    package = _two_tasks(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    disk.append_run(
        package,
        {"kind": "run", "task": "task-value", "cmd": "true", "exit": 1, "final": True, "directive": "FINAL_RUNG"},
    )
    # Independent ready work keeps priority over a checked task's failed final.
    ready = _next_json(tmp_path)
    assert ready["task"]["id"] == "task-next"
    assert "FINAL_RUNG" not in json.dumps(ready)
    _block(package, "task-next", "true")
    blocked = _next_json(tmp_path)
    assert "spec.py unblock task-next" in blocked["next"]
    assert not blocked["recovery"].get("final_failed")
    assert cli(tmp_path, "unblock", "task-next", "--reason", "fixture evidence").returncode == 0
    assert cli(tmp_path, "verify", "task-next").returncode == 0
    assert "FINAL_RUNG" in _next_json(tmp_path)["next"]
    tasks = package / "tasks.md"
    tasks.write_text(tasks.read_text().replace("- verify: `true`", "- verify: `false`", 1), encoding="utf-8")
    superseded = _next_json(tmp_path)
    assert "FINAL_RUNG" not in json.dumps(superseded)
    assert not superseded["recovery"].get("final_failed")

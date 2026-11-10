import io
import json
import sys
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

import memory  # noqa: E402
from spec_hook import Action, handle, main  # noqa: E402


def event(root: Path, **values):
    return {"cwd": str(root), **values}


def test_stop_without_active_package_releases(tmp_path):
    assert handle("stop", {"cwd": str(tmp_path)}) == Action()


def test_stop_with_progress_blocks_once_then_stalls_after_two_attempts(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path, verify="false")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    first = handle("stop", event(tmp_path))
    second = handle("stop", event(tmp_path))
    third = handle("stop", event(tmp_path))
    assert first.block and second.block
    assert not third.block
    state = json.loads((package / "state.json").read_text())
    assert state["terminal"] == "stalled"


def test_parked_only_is_a_release_state(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    tasks = package / "tasks.md"
    tasks.write_text(tasks.read_text().replace("- [ ]", "- [x]"), encoding="utf-8")
    assert cli(tmp_path, "decide", "Publish?", "--park", "--by", "user pending").returncode == 0
    action = handle("stop", event(tmp_path))
    assert not action.block
    assert action.context.startswith("parked-only:")


def test_delivered_archive_releases_and_deliver_state_asks_for_done(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert handle("stop", event(tmp_path)).block
    assert cli(tmp_path, "done").returncode == 0
    assert handle("stop", event(tmp_path)) == Action()


def test_pre_tool_hard_gates(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    denied = handle("pre_tool", event(tmp_path, command="git commit --no-verify"))
    push = handle("pre_tool", event(tmp_path, command="git push origin main"))
    assert denied.deny and "never permits" in denied.reason
    assert push.deny and "not pre-authorized" in push.reason


def test_post_edit_and_start_injections(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    warning = handle("post_edit", event(tmp_path, file_path=str(tmp_path / "other.py")))
    context = handle("start", event(tmp_path))
    assert "Boundary warning" in warning.context
    assert SLUG in context.context and "Charter:" in context.context


def test_end_writes_snapshot(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    handle("end", event(tmp_path))
    state = json.loads((package / "state.json").read_text())
    assert state["task"]["id"] == "task-value"


def test_claude_continuing_stop_releases_and_skips_state_machine(tmp_path, monkeypatch, capsys):
    repo(tmp_path)
    package = make_package(tmp_path, verify="false")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))
    first = main(["--host", "claude", "stop"])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path), "stop_hook_active": True})))
    continuing = main(["--host", "claude", "stop"])
    assert first == 0 and continuing == 0
    first_output = capsys.readouterr().out
    assert "task-value" in first_output
    assert first_output
    assert not capsys.readouterr().out
    state = json.loads((package / "state.json").read_text())
    assert state["stalls"] == 0


def test_duplicate_stop_does_not_rewrite_state_and_other_events_are_not_deduped(tmp_path, monkeypatch, capsys):
    repo(tmp_path)
    package = make_package(tmp_path, verify="false")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    payload = {"cwd": str(tmp_path), "session_id": "same"}
    calls = []
    original_seen = memory.seen

    def seen(package_path, event):
        calls.append(event)
        return original_seen(package_path, event)

    monkeypatch.setattr(memory, "seen", seen)
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    first = main(["--host", "claude", "stop"])
    state_path = package / "state.json"
    before = state_path.read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    second = main(["--host", "claude", "stop"])
    assert first == second == 0
    assert state_path.read_text() == before
    assert len(calls) == 2
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    main(["--host", "claude", "end"])
    assert len(calls) == 2

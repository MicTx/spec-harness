import io
import json
import sys
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo, started_package

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

from spec_hook import Action, handle, main  # noqa: E402


def event(root: Path, **values):
    return {"cwd": str(root), **values}


def test_stop_without_active_package_releases(tmp_path):
    assert handle("stop", {"cwd": str(tmp_path)}) == Action()


def test_stop_replans_from_failed_verify_but_not_when_idle(tmp_path):
    repo(tmp_path)
    started_package(tmp_path, verify="test ! -e fail-marker")
    (tmp_path / "fail-marker").write_text("fail\n")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    first = handle("stop", event(tmp_path))
    assert first.block and "replan" in first.reason.lower()
    for _ in range(3):
        idle = handle("stop", event(tmp_path))
        assert not idle.block


def test_parked_only_is_a_release_state(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "decide", "Publish?", "--park", "--by", "user pending").returncode == 0
    action = handle("stop", event(tmp_path))
    assert not action.block
    assert action.context.startswith("parked-only:")


def test_delivered_archive_releases_and_deliver_state_asks_for_done(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert handle("stop", event(tmp_path)).block
    assert cli(tmp_path, "done").returncode == 0
    assert handle("stop", event(tmp_path)) == Action()


def test_pre_tool_hard_gates(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    denied = handle("pre_tool", event(tmp_path, command="git commit --no-verify"))
    push = handle("pre_tool", event(tmp_path, command="git push origin main"))
    commit = handle("pre_tool", event(tmp_path, command="git commit -m work"))
    separated = handle("pre_tool", event(tmp_path, command="git commit --no-verify; echo done"))
    short = handle("pre_tool", event(tmp_path, command="git commit -n"))
    shell = handle("pre_tool", event(tmp_path, command="sh -c 'git commit --no-verify'"))
    hooks_override = handle(
        "pre_tool",
        event(tmp_path, command="git -c core.hooksPath=/dev/null commit -m work"),
    )
    assert denied.deny and "never permits" in denied.reason
    assert push.deny and commit.deny and "Spec owns Git" in push.reason
    assert separated.deny and short.deny and shell.deny
    assert hooks_override.deny and "overriding Git hooks" in hooks_override.reason


def test_pre_tool_allows_direct_git_without_active_package(tmp_path):
    assert not handle("pre_tool", event(tmp_path, command="git commit -m work")).deny


def test_pre_tool_allows_read_only_git_while_a_package_is_active(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert not handle("pre_tool", event(tmp_path, command="git log --grep commit")).deny


def test_pre_tool_still_blocks_git_after_the_package_is_archived(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "done").returncode == 0

    result = handle("pre_tool", event(tmp_path, command="git push origin delivery"))

    assert result.deny and "Spec owns Git" in result.reason


def test_start_injection_and_removed_post_edit(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    planning = handle("start", event(tmp_path))
    assert planning.context.startswith(f"Spec {SLUG}: plan")
    assert cli(tmp_path, "plan-go").returncode == 0
    warning = handle("post_edit", event(tmp_path, file_path=str(tmp_path / "other.py")))
    context = handle("start", event(tmp_path))
    assert warning == Action()
    assert SLUG in context.context and "Charter:" in context.context


def test_stop_preserves_explicit_pending_clues(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path)
    handle("stop", event(tmp_path, pending=["continue the fixture"]))
    state = json.loads((package / "state.json").read_text())
    assert state["task"]["id"] == "task-value"
    assert state["pending"] == ["continue the fixture"]


def test_claude_continuing_stop_releases_without_new_evidence(tmp_path, monkeypatch, capsys):
    repo(tmp_path)
    started_package(tmp_path, verify="test ! -e fail-marker")
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))
    assert main(["--host", "claude", "stop"]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path), "stop_hook_active": True})))
    assert main(["--host", "claude", "stop"]) == 0
    assert not capsys.readouterr().out


def test_claude_reentry_can_continue_after_new_checkpoint(tmp_path, monkeypatch, capsys):
    repo(tmp_path)
    started_package(tmp_path, verify="test ! -e fail-marker")
    marker = tmp_path / "fail-marker"
    marker.write_text("fail\n", encoding="utf-8")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    payload = {"cwd": str(tmp_path), "session_id": "same"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert main(["--host", "claude", "stop"]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"

    marker.unlink()
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({**payload, "stop_hook_active": True})))
    assert main(["--host", "claude", "stop"]) == 0
    response = json.loads(capsys.readouterr().out or "{}")
    assert response.get("decision") == "block"
    assert "spec.py check" in response["reason"]


def test_replayed_stop_returns_no_follow_up_or_state_change(tmp_path, monkeypatch, capsys):
    repo(tmp_path)
    package = started_package(tmp_path, verify="test ! -e fail-marker")
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    payload = json.dumps({"cwd": str(tmp_path), "session_id": "same"})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert main(["--host", "claude", "stop"]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    state_path = package / "state.json"
    before = state_path.read_bytes()
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert main(["--host", "claude", "stop"]) == 0
    assert not capsys.readouterr().out
    assert state_path.read_bytes() == before

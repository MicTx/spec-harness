import json
import sys
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

from spec_hook import Action, handle  # noqa: E402


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

import sys
from pathlib import Path

from tests.v3_support import cli, make_package, repo

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

from spec_hook import Action, handle  # noqa: E402


def test_stop_permits_only_the_three_terminal_states(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert handle("stop", {"cwd": str(tmp_path)}).reason.startswith("task-value:")
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert handle("stop", {"cwd": str(tmp_path)}).reason.startswith("All work is verified")
    assert cli(tmp_path, "done").returncode == 0
    assert handle("stop", {"cwd": str(tmp_path)}) == Action()


def test_unexplained_stop_is_blocked_until_two_failed_attempts(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path, verify="false")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    assert handle("stop", {"cwd": str(tmp_path)}).block
    assert handle("stop", {"cwd": str(tmp_path)}).block
    release = handle("stop", {"cwd": str(tmp_path)})
    assert not release.block
    assert release.context.startswith("stalled:")
    assert (package / "state.json").is_file()

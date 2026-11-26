"""An explicit check can retry; an unchanged Stop event cannot spin the agent."""

import json
import sys
from pathlib import Path

from tests.v3_support import cli, repo, started_package

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

from spec_hook import handle  # noqa: E402


def test_repeat_failure_after_unrelated_edits_does_not_continue_stop(tmp_path):
    repo(tmp_path)
    started_package(tmp_path, verify="grep -qx 'VALUE = 0' app.py")
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    app = tmp_path / "app.py"
    app.write_text("VALUE = 7\n")
    assert cli(tmp_path, "check").returncode == 1
    first = handle("stop", {"cwd": str(tmp_path), "session_id": "first"})
    assert first.block and "spec.py check" in first.reason

    app.write_text("VALUE = 7\n# unrelated change\n")
    assert cli(tmp_path, "check").returncode == 1
    cold = handle("stop", {"cwd": str(tmp_path), "session_id": "new-session"})
    assert not cold.block and cold.context.startswith("stalled:")
    app.write_text("VALUE = 0\n")
    assert cli(tmp_path, "check").returncode == 0


def test_no_manual_recovery_budget_flags_remain(tmp_path):
    repo(tmp_path)
    directory = started_package(tmp_path)
    before = (directory / "runs.jsonl").read_bytes()
    for flag in ("--attempt", "--retry", "--restart"):
        result = cli(tmp_path, "check", flag, "unused")
        assert result.returncode == 2 and "unrecognized arguments" in result.stderr
    assert (directory / "runs.jsonl").read_bytes() == before
    next_card = json.loads(cli(tmp_path, "next").stdout)
    assert set(next_card["recovery"]) == {"pending"}

import json
import sys
from pathlib import Path

from tests.v3_support import cli, repo, started_package

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

from spec_hook import Action, handle  # noqa: E402


def test_idle_package_does_not_auto_start_and_delivered_package_releases(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert not handle("stop", {"cwd": str(tmp_path)}).block
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert handle("stop", {"cwd": str(tmp_path)}).reason.startswith("All work is verified")
    assert cli(tmp_path, "done").returncode == 0
    assert handle("stop", {"cwd": str(tmp_path)}) == Action()


def test_failed_verify_requests_replanning_but_an_idle_stop_releases(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path, verify="test ! -e fail-marker")
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    followup = handle("stop", {"cwd": str(tmp_path)})
    assert followup.block and "Replan" in followup.reason
    release = handle("stop", {"cwd": str(tmp_path)})
    assert not release.block
    assert release.context.startswith("stalled:")
    assert (package / "state.json").is_file()


def test_latest_failure_blocks_deliver_even_when_tasks_are_checked(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    runs = [json.loads(line) for line in (package / "runs.jsonl").read_text().splitlines()]
    latest = next(record for record in reversed(runs) if record.get("kind") == "run")
    latest["exit"] = 1
    (package / "runs.jsonl").write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in runs) + "\n",
        encoding="utf-8",
    )
    brief = cli(tmp_path, "next")
    assert brief.returncode == 0
    card = json.loads(brief.stdout)
    assert card["state"] == "run" and card["task"]["id"] == "task-value"
    assert not card["criteria"][0]["passed"]
    action = handle("stop", {"cwd": str(tmp_path)})
    assert action.block
    assert "task-value:" in action.reason
    assert cli(tmp_path, "decide", "Publish?", "--park", "--by", "user pending").returncode == 0
    parked = cli(tmp_path, "next")
    assert parked.returncode == 0
    assert json.loads(parked.stdout)["state"] == "run"

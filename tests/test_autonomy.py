import json

from tests.v3_support import SLUG, cli, make_package, repo


def test_reversible_decision_is_logged_without_user_question(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    result = cli(tmp_path, "decide", "Use the existing helper", "--by", "V1")
    assert result.returncode == 0
    spec = (tmp_path / ".spec/specs" / SLUG / "spec.md").read_text()
    assert "by: V1" in spec and "## Decisions" in spec


def test_parked_item_does_not_preempt_ready_task(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "decide", "Publish?", "--park", "--by", "user pending").returncode == 0
    result = json.loads(cli(tmp_path, "next").stdout)
    assert result["state"] == "run"
    assert result["task"]["id"] == "task-value"


def test_irreversible_decision_requires_user_authority(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    result = cli(tmp_path, "decide", "Delete remote data", "--by", "agent")
    assert result.returncode == 1

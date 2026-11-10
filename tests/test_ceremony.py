from tests.v3_support import SLUG, cli, make_package, repo


def test_v3_lifecycle_uses_four_model_side_calls(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    calls = [
        cli(tmp_path, "plan-lint", "--static"),
        cli(tmp_path, "plan-go"),
        cli(tmp_path, "verify", "task-value"),
        cli(tmp_path, "done"),
    ]
    assert [result.returncode for result in calls] == [0, 0, 0, 0]
    assert (tmp_path / ".spec/specs/archive" / SLUG / "completion-summary.md").is_file()

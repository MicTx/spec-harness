import json

from tests.v3_support import SLUG, cli, make_package, repo


def test_static_lint_accepts_a_complete_v3_plan(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    result = cli(tmp_path, "plan-lint", "--static")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ready"] is True


def test_lint_rejects_a_verify_that_does_not_discriminate(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path, verify="false", expect="red")
    tasks = package / "tasks.md"
    tasks.write_text(tasks.read_text().replace("`false`", "`true`"))
    result = cli(tmp_path, "plan-lint")
    assert result.returncode == 1
    assert any(gap["rule"] == "PLAN-001" for gap in json.loads(result.stdout)["gaps"])


def test_go_records_baseline_and_merges_charter(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    result = cli(tmp_path, "plan-go")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".spec/charter.md").is_file()
    assert '"kind": "baseline"' in (tmp_path / ".spec/specs" / SLUG / "runs.jsonl").read_text()

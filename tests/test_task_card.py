"""Pin tests for spec.py task-card — TDD red/green task scaffolding."""

from pathlib import Path

from tests.v3_support import cli, make_package, repo

ROOT = Path(__file__).resolve().parents[1]


def test_single_card(tmp_path):
    result = cli(
        tmp_path, "task-card", "--id", "task-foo", "--boundary", "app.py", "--verify", "true", "--title", "Change value"
    )

    assert result.returncode == 0, result.stderr
    text = result.stdout
    assert "- [ ] Change value" in text
    assert "- id: task-foo" in text
    assert "- boundary: app.py" in text
    assert "- verify: `true`" in text
    assert "- expect: green" in text


def test_tdd_pair(tmp_path):
    result = cli(tmp_path, "task-card", "--tdd", "--id", "task-foo", "--boundary", "tests/test_foo.py")

    assert result.returncode == 0, result.stderr
    assert "- id: task-foo-test" in result.stdout
    assert "- boundary: new:tests/test_foo.py" in result.stdout
    assert "- expect: red" in result.stdout
    assert "- depends-on: task-foo-test" in result.stdout


def test_tdd_pair_passes_plan_lint(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    pair = cli(
        tmp_path,
        "task-card",
        "--tdd",
        "--id",
        "task-foo",
        "--boundary",
        "app.py",
        "--test-boundary",
        "tests/test_foo.py",
    ).stdout
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- C1 | Fixture works | task-value\n",
            "- C1 | Fixture works | task-foo\n- C2 | Red-first pair lands | task-foo-test\n",
        ),
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(pair, encoding="utf-8")

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 0, result.stdout + result.stderr


def test_id_guard_rejects_invalid_ids(tmp_path):
    result = cli(tmp_path, "task-card", "--id", "task_bad_id", "--boundary", "app.py", "--verify", "true")

    assert result.returncode == 1
    assert "id" in result.stderr.lower()


def test_id_guard_allows_valid_ids(tmp_path):
    result = cli(tmp_path, "task-card", "--id", "task-good", "--boundary", "app.py", "--verify", "true")

    assert result.returncode == 0, result.stderr
    assert "- id: task-good" in result.stdout


def test_docs_red_first_pattern():
    plan = (ROOT / "references" / "plan.md").read_text(encoding="utf-8")
    assert "Red-first" in plan
    assert "task-card" in plan

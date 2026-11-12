import json
import subprocess

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


def test_plan_go_freezes_once_and_requires_the_bound_branch(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    before = cli(tmp_path, "next")
    assert before.returncode == 0
    assert '"state": "plan"' in before.stdout
    assert cli(tmp_path, "plan-go").returncode == 0
    assert "- frozen:" in (tmp_path / ".spec/specs" / SLUG / "spec.md").read_text()
    assert cli(tmp_path, "plan-go").returncode == 1

    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    result = cli(tmp_path, "--slug", SLUG, "verify", "task-value")
    assert result.returncode == 1
    assert "package is bound to" in result.stderr


def test_plan_go_rejects_a_protected_branch_binding(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(spec.read_text(encoding="utf-8").replace(f"spec/{SLUG}", "main"), encoding="utf-8")
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)

    result = cli(tmp_path, "--slug", SLUG, "plan-go")

    assert result.returncode == 1
    assert "protected branch" in result.stderr


def test_lint_does_not_accept_only_nested_files_for_a_single_star_boundary(tmp_path):
    repo(tmp_path)
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "src" / "deep" / "nested.py").write_text("NESTED = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/deep/nested.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "nested source"], cwd=tmp_path, check=True)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("- in: tests/**", "- in: tests/**\n- in: src/**"),
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [ ] Narrow\n  - id: task-value\n  - boundary: src/*.py\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 1
    assert any(gap["rule"] == "PLAN-002" for gap in json.loads(result.stdout)["gaps"])


def test_lint_rejects_a_boundary_wider_than_an_exact_scope(tmp_path):
    repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("A = True\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("B = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "src"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "source"], cwd=tmp_path, check=True)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("- in: tests/**", "- in: tests/**\n- in: src/a.py"),
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [ ] Too wide\n  - id: task-value\n  - boundary: src/**\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 1
    assert any(gap["rule"] == "PLAN-003" for gap in json.loads(result.stdout)["gaps"])

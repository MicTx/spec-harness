import json
import subprocess
import sys
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo, started_package

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import evidence  # noqa: E402
from runtime_budget import project_verify_timeout  # noqa: E402


def test_tree_fingerprint_tracks_product_content_not_commit_identity(tmp_path):
    from package import tree_fingerprint

    repo(tmp_path)
    make_package(tmp_path)
    before = tree_fingerprint(tmp_path)
    subprocess.run(["git", "add", ".spec"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "package metadata"], cwd=tmp_path, check=True, capture_output=True)

    assert tree_fingerprint(tmp_path) == before
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    changed = tree_fingerprint(tmp_path)
    assert changed != before
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "product change"], cwd=tmp_path, check=True, capture_output=True)
    assert tree_fingerprint(tmp_path) == changed
    (tmp_path / "app.py").unlink()
    assert tree_fingerprint(tmp_path) != changed


def test_package_only_checkpoint_preserves_other_task_evidence_freshness(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    (package / "tasks.md").write_text(
        "- [ ] First\n"
        "  - id: task-one\n"
        "  - boundary: app.py\n"
        "  - verify: `true`\n"
        "  - expect: green\n"
        "- [ ] Second\n"
        "  - id: task-two\n"
        "  - boundary: app.py\n"
        "  - verify: `true`\n"
        "  - expect: green\n",
        encoding="utf-8",
    )
    spec_path = package / "spec.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace(
            "- C1 | Fixture works | task-value",
            "- C1 | First passes | task-one\n- C2 | Second passes | task-two",
        ),
        encoding="utf-8",
    )

    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "--all").returncode == 0
    check = cli(tmp_path, "check", "--check-only")
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["passed"] is True

    done = cli(tmp_path, "done")
    assert done.returncode == 0, done.stderr
    runs = [
        json.loads(line)
        for line in (tmp_path / ".spec/specs/archive" / SLUG / "runs.jsonl").read_text().splitlines()
        if line
    ]
    final = [record for record in runs if record.get("final") is True and "task" in record]
    assert {record["task"] for record in final} == {"task-one", "task-two"}


def test_verify_ticks_and_creates_checkpoint(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    result = cli(tmp_path, "verify", "task-value")
    assert result.returncode == 0, result.stderr
    tasks = (tmp_path / ".spec/specs" / SLUG / "tasks.md").read_text()
    assert "- [x] Change value" in tasks
    runs = [json.loads(line) for line in (tmp_path / ".spec/specs" / SLUG / "runs.jsonl").read_text().splitlines()]
    assert runs[-1]["exit"] == 0
    assert runs[-1]["sig"]


def test_checkpoint_skips_ignored_optional_memory_files(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    (tmp_path / ".gitignore").write_text(".spec/charter.md\n.spec/lessons.md\n")
    (tmp_path / ".spec/charter.md").write_text("# ignored\n")
    (tmp_path / ".spec/lessons.md").write_text("# ignored\n")
    result = cli(tmp_path, "verify", "task-value")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".spec/charter.md").read_text() == "# ignored\n"


def test_failure_changes_strategy_instead_of_repeating(tmp_path):
    repo(tmp_path)
    started_package(tmp_path, verify="test ! -e fail-marker")
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    first = cli(tmp_path, "verify", "task-value")
    second = cli(tmp_path, "verify", "task-value")
    assert "smallest fix" in first.stdout
    assert "minimal reproduction" in second.stdout


def test_final_evidence_is_explicitly_marked(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    result = cli(tmp_path, "done")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".spec/specs/archive" / SLUG / "completion-summary.md").is_file()


def test_timeout_is_recorded_as_failure_evidence(tmp_path, monkeypatch):
    repo(tmp_path)
    package = started_package(tmp_path)
    tasks = package / "tasks.md"
    tasks.write_text(
        tasks.read_text(encoding="utf-8").replace(
            "- verify: `true`",
            "- verify: `python3 -c 'import time; time.sleep(3)'`",
        ),
        encoding="utf-8",
    )

    class StubSubprocess:
        TimeoutExpired = subprocess.TimeoutExpired
        CompletedProcess = subprocess.CompletedProcess

        @staticmethod
        def run(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("stub", 0.01)

    monkeypatch.setattr(evidence, "subprocess", StubSubprocess)
    result = evidence.run_tasks(tmp_path, SLUG, ["task-value"])
    record = result["results"][0]
    assert result["ready"] is False
    assert record["exit"] == 124
    assert record["sig"]
    assert record["directive"].startswith("Verify timed out.")
    assert (package / "runs.jsonl").is_file()


def test_project_verify_budget_matches_release_builder(tmp_path, monkeypatch):
    repo(tmp_path)
    started_package(tmp_path)
    captured = {}

    class StubSubprocess:
        TimeoutExpired = subprocess.TimeoutExpired
        CompletedProcess = subprocess.CompletedProcess

        @staticmethod
        def run(*_args, **kwargs):
            captured["timeout"] = kwargs["timeout"]
            return subprocess.CompletedProcess("true", 0, b"", b"")

    monkeypatch.setattr(evidence, "subprocess", StubSubprocess)
    assert evidence.run_tasks(tmp_path, SLUG, ["task-value"])["ready"] is True
    assert captured["timeout"] == project_verify_timeout() == 1800


def test_verify_requires_plan_go(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)

    result = cli(tmp_path, "verify", "task-value")

    assert result.returncode == 1
    assert "has not passed plan-go" in result.stderr
    assert "- [ ] Change value" in (package / "tasks.md").read_text()


def test_verify_rejects_a_task_before_its_dependency(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    (package / "tasks.md").write_text(
        "- [ ] First\n"
        "  - id: first\n"
        "  - boundary: app.py\n"
        "  - verify: `true`\n"
        "  - expect: green\n"
        "- [ ] Second\n"
        "  - id: second\n"
        "  - boundary: app.py\n"
        "  - verify: `true`\n"
        "  - expect: green\n"
        "  - depends-on: first\n",
        encoding="utf-8",
    )
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- C1 | Fixture works | task-value",
            "- C1 | First passes | first\n- C2 | Second passes | second",
        ),
        encoding="utf-8",
    )
    assert cli(tmp_path, "plan-go").returncode == 0

    result = cli(tmp_path, "verify", "second")

    assert result.returncode == 1
    assert "dependencies not complete: first" in result.stderr
    assert "- [ ] First" in (package / "tasks.md").read_text()
    assert "- [ ] Second" in (package / "tasks.md").read_text()


def test_verify_all_runs_in_dependency_order(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8")
        .replace("- in: tests/**", "- in: tests/**\n- in: order")
        .replace(
            "- C1 | Fixture works | task-value",
            "- C1 | First passes | first\n- C2 | Second passes | second",
        ),
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [ ] First\n"
        "  - id: first\n"
        "  - boundary: new:order\n"
        "  - verify: `test ! -e go-marker || printf one >> order`\n"
        "  - expect: green\n"
        "- [ ] Second\n"
        "  - id: second\n"
        "  - boundary: app.py\n"
        "  - verify: `test ! -e go-marker || printf two >> order`\n"
        "  - expect: green\n"
        "  - depends-on: first\n",
        encoding="utf-8",
    )
    assert cli(tmp_path, "plan-go").returncode == 0
    (tmp_path / "go-marker").write_text("go\n", encoding="utf-8")

    result = cli(tmp_path, "verify", "--all")

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "order").read_text() == "onetwo"
    tasks = (package / "tasks.md").read_text()
    assert "- [x] First" in tasks and "- [x] Second" in tasks


def test_repeated_signature_auto_blocks_and_can_be_explicitly_unblocked(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path, verify="test ! -e fail-marker")
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    for _ in range(4):
        assert cli(tmp_path, "verify", "task-value").returncode == 1

    assert "- [!] Change value" in (package / "tasks.md").read_text()
    brief = json.loads(cli(tmp_path, "next").stdout)
    assert brief["state"] == "stalled"

    unblocked = cli(tmp_path, "unblock", "task-value", "--reason", "new root-cause evidence")

    assert unblocked.returncode == 0, unblocked.stderr
    assert "- [ ] Change value" in (package / "tasks.md").read_text()
    (tmp_path / "fail-marker").unlink()
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "done").returncode == 0


def test_auto_block_continues_to_independent_ready_work(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path, verify="test ! -e fail-marker")
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- C1 | Fixture works | task-value",
            "- C1 | Failing path | failing\n- C2 | Independent path | independent",
        ),
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [ ] Failing path\n"
        "  - id: failing\n"
        "  - boundary: app.py\n"
        "  - verify: `test ! -e fail-marker`\n"
        "  - expect: green\n"
        "- [ ] Independent path\n"
        "  - id: independent\n"
        "  - boundary: app.py\n"
        "  - verify: `true`\n"
        "  - expect: green\n",
        encoding="utf-8",
    )
    assert cli(tmp_path, "plan-go").returncode == 0
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    for _ in range(4):
        assert cli(tmp_path, "verify", "failing").returncode == 1

    brief = json.loads(cli(tmp_path, "next").stdout)

    assert brief["state"] == "run"
    assert brief["task"]["id"] == "independent"
    assert cli(tmp_path, "verify", "independent").returncode == 0


def test_done_refuses_blocked_and_unchecked_tasks_before_archive(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path)
    tasks = package / "tasks.md"
    blocked = cli(tmp_path, "done")
    assert blocked.returncode == 1
    assert "unfinished tasks: task-value" in blocked.stderr
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()

    tasks.write_text(tasks.read_text().replace("- [ ]", "- [!]"), encoding="utf-8")
    blocked = cli(tmp_path, "done")
    assert blocked.returncode == 1
    assert "blocked tasks: task-value" in blocked.stderr
    assert "- [!] Change value" in tasks.read_text()
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_checkpoint_stages_only_concrete_paths_inside_a_narrow_boundary(tmp_path):
    repo(tmp_path)
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "src" / "a.py").write_text("VALUE = 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/a.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "track source"], cwd=tmp_path, check=True)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("- in: tests/**", "- in: tests/**\n- in: src/**"),
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [ ] Narrow staging\n  - id: task-value\n  - boundary: src/*.py\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    assert cli(tmp_path, "plan-go").returncode == 0
    (tmp_path / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "src" / "deep" / "nested.py").write_text("NESTED = True\n", encoding="utf-8")

    assert cli(tmp_path, "verify", "task-value").returncode == 0

    committed = subprocess.run(
        ["git", "show", "--format=", "--name-only", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert "src/a.py" in committed
    assert "src/deep/nested.py" not in committed
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "src/deep/nested.py" in status

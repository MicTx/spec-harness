"""The run recorder and the final-tree verification contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_spec_package import compute_gate_results  # noqa: E402
from spec_verify import (  # noqa: E402
    RUNS_FILENAME,
    classify_verify,
    compute_tree_fingerprint,
    duplicate_verify_error,
    evaluate_runs_gate,
    fresh_machine_evidence,
    latest_record_by_task,
    machine_verification_summary,
    read_runs_records,
    runs_protocol_enabled,
)

from tests.test_complete_spec_package import _COMPLETE_CHECKLIST, _COMPLETE_SPEC_EN  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SLUG = "2026-10-12_add-public-example-20"


def command(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(list(args), cwd=root, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {args}\n{result.stdout}\n{result.stderr}")
    return result


def git(root: Path, *args: str) -> str:
    return command(root, "git", *args).stdout


def init_repo(root: Path) -> Path:
    command(root, "git", "init", "-q", "-b", "main", str(root))
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    return root


def make_package(
    root: Path,
    slug: str,
    *,
    runs_protocol: bool,
    tasks_text: str,
    checklist: str | None = None,
) -> Path:
    git(root, "checkout", "-qb", f"spec/{slug}")
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    spec = _COMPLETE_SPEC_EN + f"\n- Git integration branch：`spec/{slug}`\n"
    if runs_protocol:
        spec += "\n本包启用 evidence: runs 协议。\n"
    (package / "spec.md").write_text(spec, encoding="utf-8")
    (package / "tasks.md").write_text(tasks_text, encoding="utf-8")
    (package / "checklist.md").write_text(checklist or _COMPLETE_CHECKLIST, encoding="utf-8")
    return package


def run_recorder(root: Path, slug: str, *extra: str, check: bool = True) -> subprocess.CompletedProcess:
    return command(
        root,
        sys.executable,
        str(SCRIPTS / "spec_verify.py"),
        "run",
        "--root",
        str(root),
        "--slug",
        slug,
        *extra,
        check=check,
    )


def show_cli(root: Path, slug: str, *extra: str, check: bool = True) -> subprocess.CompletedProcess:
    return command(
        root,
        sys.executable,
        str(SCRIPTS / "spec_verify.py"),
        "show",
        "--root",
        str(root),
        "--slug",
        slug,
        *extra,
        check=check,
    )


def read_records(package: Path) -> list[dict]:
    path = package / RUNS_FILENAME
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def gate_for(root: Path, package: Path, slug: str = SLUG):
    return compute_gate_results(
        (package / "spec.md").read_text(encoding="utf-8"),
        (package / "tasks.md").read_text(encoding="utf-8"),
        (package / "checklist.md").read_text(encoding="utf-8"),
        slug=slug,
        root=root,
        package_dir=package,
    )


MACHINE_TASKS = """\
- [x] Pass task
  - boundary: b1
  - verify: `python3 -c "print('ok')"` 全绿
- [x] Fail task
  - boundary: b2
  - verify: `python3 -c "import sys; sys.exit(3)"` 应失败
- [x] Manual task
  - boundary: b3
  - verify: manual: 只读复核，无需执行
"""

PASSING_TASKS = """\
- [x] Pass task one
  - boundary: b1
  - verify: `python3 -c "print('ok')"` 全绿
- [x] Pass task two
  - boundary: b2
  - verify: `python3 -c "print('also ok')"` 全绿
- [x] Manual task
  - boundary: b3
  - verify: manual: 只读复核，无需执行
"""


class TestClassifyVerify:
    def test_machine_uses_first_backtick_span(self):
        classification = classify_verify("`python3 -m pytest -q` then review `docs`")
        assert classification is not None
        assert classification.kind == "machine"
        assert classification.command == "python3 -m pytest -q"

    def test_manual_prefix_wins_over_a_backtick_in_the_reason(self):
        classification = classify_verify("manual: 只读复核 `check.py:141`")
        assert classification is not None
        assert classification.kind == "manual"
        assert classification.reason == "只读复核 `check.py:141`"

    @pytest.mark.parametrize("verify", ["", "manual:", "pytest passed"])
    def test_invalid_verify_is_not_classified(self, verify):
        assert classify_verify(verify) is None

    def test_runs_protocol_marker_is_case_sensitive(self):
        assert runs_protocol_enabled("本包启用 evidence: runs 协议")
        assert not runs_protocol_enabled("evidence: Runs")

    def test_duplicate_verify_is_detected_per_task(self):
        block = "- [x] Task\n- verify: `true`\n- verify: `false`\n"
        assert "SPEC-VERIFY-002" in duplicate_verify_error("task_0001", block)
        assert duplicate_verify_error("task_0001", "- [x] Task\n- verify: `true`") is None


class TestTreeFingerprint:
    def test_spec_changes_are_excluded(self, tmp_path):
        root = init_repo(tmp_path)
        before = compute_tree_fingerprint(root)
        (root / ".spec" / "specs" / "any").mkdir(parents=True)
        (root / ".spec" / "specs" / "any" / "tasks.md").write_text("- [ ] T\n", encoding="utf-8")
        assert compute_tree_fingerprint(root) == before

    def test_untracked_content_changes_fingerprint(self, tmp_path):
        root = init_repo(tmp_path)
        before = compute_tree_fingerprint(root)
        (root / "notes.txt").write_text("v1\n", encoding="utf-8")
        added = compute_tree_fingerprint(root)
        (root / "notes.txt").write_text("v2\n", encoding="utf-8")
        assert added != before
        assert compute_tree_fingerprint(root) != added

    def test_tracked_modification_changes_fingerprint(self, tmp_path):
        root = init_repo(tmp_path)
        before = compute_tree_fingerprint(root)
        (root / "README.md").write_text("modified\n", encoding="utf-8")
        assert compute_tree_fingerprint(root) != before

    def test_ignored_files_are_excluded(self, tmp_path):
        root = init_repo(tmp_path)
        (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-qm", "gitignore")
        before = compute_tree_fingerprint(root)
        (root / "debug.log").write_text("noise\n", encoding="utf-8")
        assert compute_tree_fingerprint(root) == before

    def test_head_movement_changes_fingerprint(self, tmp_path):
        root = init_repo(tmp_path)
        before = compute_tree_fingerprint(root)
        (root / "second.md").write_text("committed\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-qm", "second")
        assert compute_tree_fingerprint(root) != before

    def test_empty_repo_is_hashable_and_non_git_returns_none(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        command(repo, "git", "init", "-q", "-b", "main", str(repo))
        (repo / "solo.txt").write_text("no commits yet\n", encoding="utf-8")
        assert isinstance(compute_tree_fingerprint(repo), str)
        assert compute_tree_fingerprint(tmp_path) is None


class TestRunRecorder:
    def test_run_task_appends_full_field_records(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        run_recorder(root, SLUG, "--task", "task_0001")
        first_bytes = (package / RUNS_FILENAME).read_bytes()
        run_recorder(root, SLUG, "--task", "task_0001")
        assert (package / RUNS_FILENAME).read_bytes().startswith(first_bytes)
        records = read_records(package)
        assert len(records) == 2
        assert all(
            set(record) == {"task", "cmd", "exit", "duration_s", "stdout_tail_sha256", "tree_fp", "package_id", "at"}
            for record in records
        )
        record = records[-1]
        assert record["task"] == "task_0001"
        assert record["cmd"] == "python3 -c \"print('ok')\""
        assert record["exit"] == 0
        assert isinstance(record["duration_s"], float) and record["duration_s"] >= 0
        assert record["stdout_tail_sha256"] == hashlib.sha256(b"ok\n").hexdigest()
        assert record["tree_fp"] == compute_tree_fingerprint(root, package)
        assert record["package_id"] == f".spec/specs/{SLUG}"
        assert record["at"].endswith("+00:00")

    def test_non_git_machine_verify_refuses_unbound_evidence(self, tmp_path):
        package = tmp_path / ".spec" / "specs" / SLUG
        package.mkdir(parents=True)
        (package / "spec.md").write_text("evidence: runs\n", encoding="utf-8")
        (package / "tasks.md").write_text(
            '- [x] Verify\n  - boundary: source\n  - verify: `python3 -c "print(1)"`\n', encoding="utf-8"
        )
        result = run_recorder(tmp_path, SLUG, "--all", check=False)
        assert result.returncode == 1
        assert "Git" in result.stderr
        assert read_records(package) == []

    def test_manual_task_is_skipped_without_record(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        run_recorder(root, SLUG, "--task", "task_0003")
        assert read_records(package) == []

    def test_failing_command_records_nonzero_exit(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        result = run_recorder(root, SLUG, "--task", "task_0002", check=False)
        assert result.returncode == 1
        assert [record["exit"] for record in read_records(package)] == [3]
        assert gate_for(root, package).runs_gate_ok is False
        assert any("task_0002" in error and "not verified" in error for error in gate_for(root, package).runs_errors)

    def test_run_all_uses_one_final_tree_fingerprint(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        records = read_records(package)
        assert [record["task"] for record in records] == ["task_0001", "task_0002"]
        assert {record["tree_fp"] for record in records} == {compute_tree_fingerprint(root, package)}
        assert all(record["exit"] == 0 for record in records)
        assert gate_for(root, package).runs_gate_ok is True

    def test_duplicate_verify_fields_reject_ambiguous_task(self, tmp_path):
        root = init_repo(tmp_path)
        tasks = (
            "- [x] Ambiguous\n  - boundary: tests\n"
            '  - verify: `python3 -c "print(1)"`\n'
            '  - verify: `python3 -c "print(2)"`\n'
        )
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        result = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert result.returncode == 1
        assert "SPEC-VERIFY-002" in result.stdout + result.stderr
        assert read_records(package) == []
        assert gate_for(root, package).runs_gate_ok is False
        assert any("SPEC-VERIFY-002" in error for error in gate_for(root, package).runs_errors)

    def test_duplicate_task_ids_are_rejected(self, tmp_path):
        root = init_repo(tmp_path)
        tasks = (
            "- [x] First\n  - id: task-same\n  - boundary: tests\n  - verify: `true`\n"
            "- [x] Second\n  - id: task-same\n  - boundary: tests\n  - verify: `true`\n"
        )
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        result = run_recorder(root, SLUG, "--task", "task-same", check=False)
        assert result.returncode == 1
        assert "duplicate task id" in result.stderr
        assert read_records(package) == []

    def test_malformed_verify_fails(self, tmp_path):
        root = init_repo(tmp_path)
        tasks = "- [x] Bad\n  - boundary: b\n  - verify: pytest passes (no command span)\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        result = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert result.returncode == 1
        assert "backtick" in result.stderr
        assert read_records(package) == []
        assert gate_for(root, package).runs_gate_ok is False

    def test_unknown_task_is_rejected(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        result = run_recorder(root, SLUG, "--task", "task_9999", check=False)
        assert result.returncode == 1
        assert "unknown task id" in result.stderr
        assert read_records(package) == []

    def test_verify_that_changes_tree_must_not_pass(self, tmp_path):
        root = init_repo(tmp_path)
        tasks = "- [x] Change\n  - boundary: b\n  - verify: `python3 -c \"open('changed','w').write('x')\"`\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        result = run_recorder(root, SLUG, "--all", check=False)
        assert result.returncode == 1
        assert "changed the working tree" in result.stderr
        assert read_records(package)[-1]["exit"] != 0
        assert gate_for(root, package).runs_gate_ok is False


class TestRunsGate:
    def test_legacy_package_without_marker_is_not_evaluated(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(
            root, SLUG, runs_protocol=False, tasks_text="- [x] Task\n  - boundary: b\n  - verify: pytest\n"
        )
        results = gate_for(root, package)
        assert results.runs_protocol is False
        assert results.runs_gate_ok is None
        assert results.overall_ok is True

    def test_absent_record_names_unverified_task(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        results = gate_for(root, package)
        assert results.runs_gate_ok is False
        assert any("task_0001" in error and "not verified" in error for error in results.runs_errors)

    @pytest.mark.parametrize("specs_dir", ["archive/state", "docs/archive/specs", "state"])
    def test_only_specs_archive_container_freezes_evidence(self, tmp_path, specs_dir):
        root = init_repo(tmp_path)
        original = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        package = root / specs_dir / "specs" / SLUG
        package.parent.mkdir(parents=True)
        original.rename(package)
        assert not (package / RUNS_FILENAME).exists()
        results = gate_for(root, package)
        assert results.runs_gate_ok is False
        assert results.overall_ok is False
        assert any("not verified" in error for error in results.runs_errors)
        result = run_recorder(root, SLUG, "--specs-dir", specs_dir, "--all")
        assert result.returncode == 0
        assert gate_for(root, package).runs_gate_ok is True
        archive = package.parent / "archive" / SLUG
        archive.parent.mkdir()
        package.rename(archive)
        (archive / RUNS_FILENAME).unlink()
        ok, errors, detail = evaluate_runs_gate("evidence: runs", PASSING_TASKS, archive, root)
        assert ok is None and errors == [] and "frozen" in detail

    def test_passing_records_clear_the_gate(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--task", "task_0001")
        assert gate_for(root, package).runs_gate_ok is False
        run_recorder(root, SLUG, "--task", "task_0002")
        assert gate_for(root, package).runs_gate_ok is True
        assert gate_for(root, package).overall_ok is True

    def test_non_git_null_fingerprint_does_not_prove_final_tree(self, tmp_path):
        package = tmp_path / ".spec" / "specs" / SLUG
        package.mkdir(parents=True)
        tasks = '- [x] Verify\n  - boundary: source\n  - verify: `python3 -c "print(1)"`\n'
        record = {"task": "task_0001", "cmd": 'python3 -c "print(1)"', "tree_fp": None, "exit": 0}
        (package / RUNS_FILENAME).write_text(json.dumps(record) + "\n", encoding="utf-8")
        for contents in ("before", "changed after capture"):
            (tmp_path / "source.txt").write_text(contents, encoding="utf-8")
            ok, errors, _detail = evaluate_runs_gate("evidence: runs", tasks, package, tmp_path)
            assert ok is False
            assert any("stale" in error for error in errors)

    def test_manual_only_task_needs_no_record(self, tmp_path):
        root = init_repo(tmp_path)
        tasks = "- [x] Manual\n  - boundary: b\n  - verify: manual: 只读复核\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        results = gate_for(root, package)
        assert results.runs_gate_ok is True
        assert "1 manual task" in results.runs_detail
        assert machine_verification_summary(tasks, package) is None

    def test_changed_worktree_expires_evidence_until_rerun(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        (root / "README.md").write_text("changed\n", encoding="utf-8")
        results = gate_for(root, package)
        assert results.runs_gate_ok is False
        assert any("task_0001" in error and "stale evidence" in error for error in results.runs_errors)
        run_recorder(root, SLUG, "--all")
        assert gate_for(root, package).runs_gate_ok is True

    def test_changed_command_expires_evidence_even_if_tree_unchanged(self, tmp_path):
        root = init_repo(tmp_path)
        old_command = 'python3 -c "print(42)"'
        new_command = 'python3 -c "print(43)"'
        tasks = f"- [x] Verify\n  - boundary: tests\n  - verify: `{old_command}`\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        run_recorder(root, SLUG, "--all")
        fingerprint = compute_tree_fingerprint(root, package)
        (package / "tasks.md").write_text(tasks.replace(old_command, new_command), encoding="utf-8")
        assert compute_tree_fingerprint(root, package) == fingerprint
        assert gate_for(root, package).runs_gate_ok is False
        assert any("SPEC-EVID-002" in error for error in gate_for(root, package).runs_errors)
        run_recorder(root, SLUG, "--all")
        assert read_records(package)[-1]["cmd"] == new_command
        assert gate_for(root, package).runs_gate_ok is True

    def test_latest_failure_supersedes_prior_success(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        assert gate_for(root, package).runs_gate_ok is True
        record = {**read_records(package)[0], "exit": 9}
        with (package / RUNS_FILENAME).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
        assert latest_record_by_task(read_records(package))["task_0001"]["exit"] == 9
        assert gate_for(root, package).runs_gate_ok is False

    @pytest.mark.parametrize("exit_value", [False, 0.0, "0", 1, -1, None])
    def test_non_integer_zero_or_failure_never_clears_gate(self, tmp_path, exit_value):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        records = read_records(package)
        records[0]["exit"] = exit_value
        (package / RUNS_FILENAME).write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        assert gate_for(root, package).runs_gate_ok is False
        assert any("task_0001" in error for error in gate_for(root, package).runs_errors)

    @pytest.mark.parametrize("bad_bytes", [b'{"task":\n', b"[]\n", b"\xff\n"])
    def test_malformed_records_fail_closed(self, tmp_path, bad_bytes):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        (package / RUNS_FILENAME).write_bytes(bad_bytes)
        results = gate_for(root, package)
        assert results.runs_gate_ok is False
        assert any("SPEC-EVID-003" in error for error in results.runs_errors)

    def test_no_context_does_not_evaluate(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        spec = (package / "spec.md").read_text(encoding="utf-8")
        tasks = (package / "tasks.md").read_text(encoding="utf-8")
        ok, errors, detail = evaluate_runs_gate(spec, tasks, None, None)
        assert ok is None and not errors and "not evaluated" in detail

    def test_fresh_evidence_requires_matching_command_and_tree(self, tmp_path):
        root = init_repo(tmp_path)
        tasks = "- [x] Check\n  - boundary: b\n  - verify: `python3 -c \"print('ok')\"`\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        run_recorder(root, SLUG, "--all")
        spec = (package / "spec.md").read_text(encoding="utf-8")
        assert fresh_machine_evidence(spec, tasks, package, root) == [
            "`python3 -c \"print('ok')\"` -> exit 0 (runs:task_0001)"
        ]
        changed = tasks.replace("print('ok')", "print('other')")
        assert fresh_machine_evidence(spec, changed, package, root) == []
        (root / "README.md").write_text("changed\n", encoding="utf-8")
        assert fresh_machine_evidence(spec, tasks, package, root) == []

    def test_machine_summary_excludes_manual_from_denominator(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        run_recorder(root, SLUG, "--task", "task_0001")
        run_recorder(root, SLUG, "--task", "task_0002", check=False)
        assert (
            machine_verification_summary(MACHINE_TASKS, package)
            == "1/2; 1 manual task(s) excluded from the denominator"
        )


class TestShowSubcommand:
    def test_show_filters_task_then_takes_tail_in_append_order(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        run_recorder(root, SLUG, "--task", "task_0001")
        records = read_records(package)
        result = show_cli(root, SLUG, "--task", "task_0001", "--tail", "1")
        assert [json.loads(line) for line in result.stdout.splitlines()] == [records[-1]]
        result = show_cli(root, SLUG, "--tail", "2")
        assert [json.loads(line) for line in result.stdout.splitlines()] == records[-2:]
        assert read_runs_records(package) == records

    def test_show_does_not_execute_or_write(self, tmp_path):
        root = init_repo(tmp_path)
        tasks = "- [ ] boom\n  - boundary: b\n  - verify: `python3 -c \"open('marker','w').write('x')\"`\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        before = {path.name: path.read_bytes() for path in package.iterdir() if path.is_file()}
        result = show_cli(root, SLUG, "--task", "task_0001", "--tail", "1")
        assert result.returncode == 0 and result.stdout == ""
        assert not (root / "marker").exists()
        assert {path.name: path.read_bytes() for path in package.iterdir() if path.is_file()} == before

    def test_show_rejects_nonpositive_tail(self, tmp_path):
        root = init_repo(tmp_path)
        make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        result = show_cli(root, SLUG, "--tail", "0", check=False)
        assert result.returncode == 1
        assert "--tail must be positive" in result.stderr

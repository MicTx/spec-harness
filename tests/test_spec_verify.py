"""Executable verify recorder and worktree fingerprint gates (F7 / AUD-02, AUD-05).

Unit tests cover the fingerprint semantics (`.spec/` excluded, untracked
content included, tracked modifications included, empty repo survives), the
run recorder (full-field record, failure states, hollow-command lint,
timeout), the check/complete gate wiring (legacy packages keep byte-identical
conclusions, `evidence: runs` packages get named 未验证/证据过期 failures,
manual tasks are exempt), and sandbox replays of the audit's EXP-01b/EXP-02
false-completion scenarios.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_spec_package import compute_gate_results, overall_check_passed  # noqa: E402
from spec_verify import (  # noqa: E402
    RUNS_FILENAME,
    classify_verify,
    compute_tree_fingerprint,
    evaluate_runs_gate,
    fresh_machine_evidence,
    is_hollow_command,
    machine_verification_summary,
    package_identities,
    runs_protocol_enabled,
)

from tests.test_complete_spec_package import _COMPLETE_CHECKLIST, _COMPLETE_SPEC_EN  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SLUG = "2026-10-12_add-public-example-20"


# --------------------------------------------------------------------------- #
# sandbox helpers
# --------------------------------------------------------------------------- #


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


def make_package(root: Path, slug: str, *, runs_protocol: bool, tasks_text: str, checklist: str | None = None):
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


def read_records(package: Path) -> list[dict]:
    path = package / RUNS_FILENAME
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_fresh_machine_evidence_uses_only_fresh_matching_passes(tmp_path):
    root = init_repo(tmp_path)
    slug = "2026-11-05_fix-fresh-evidence"
    tasks = """\
# Tasks
- [x] Check
  - boundary: read-only
  - verify: `python3 -c "print('ok')"`
"""
    package = make_package(root, slug, runs_protocol=True, tasks_text=tasks)
    run_recorder(root, slug, "--task", "task_0001")
    spec = (package / "spec.md").read_text(encoding="utf-8")

    assert fresh_machine_evidence(spec, tasks, package, root) == [
        "`python3 -c \"print('ok')\"` -> exit 0 (runs:task_0001)"
    ]

    # A different command in the record is not evidence for this task.
    record = read_records(package)[-1]
    record["cmd"] = "python3 -c \"print('other')\""
    (package / RUNS_FILENAME).write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert fresh_machine_evidence(spec, tasks, package, root) == []

    # Restore a passing record, then move the worktree: evidence is stale.
    record["cmd"] = "python3 -c \"print('ok')\""
    (package / RUNS_FILENAME).write_text(json.dumps(record) + "\n", encoding="utf-8")
    (root / "README.md").write_text("changed\n", encoding="utf-8")
    assert fresh_machine_evidence(spec, tasks, package, root) == []


# --------------------------------------------------------------------------- #
# classification and lint
# --------------------------------------------------------------------------- #


class TestClassifyVerify:
    def test_machine_first_backtick_span_is_the_command(self):
        classification = classify_verify("`python3 -m pytest -q` 全绿后复核 `docs` 里的行文")
        assert classification is not None and classification.kind == "machine"
        assert classification.command == "python3 -m pytest -q"

    def test_manual_prefix_wins_over_backticks_in_reason(self):
        classification = classify_verify("manual: 只读复核（锚点 `check_spec_package.py:141` 与实现一致）")
        assert classification is not None and classification.kind == "manual"
        assert "check_spec_package.py:141" in classification.reason

    def test_plain_text_without_manual_marker_violates_the_contract(self):
        assert classify_verify("pytest passed") is None
        assert classify_verify("") is None

    def test_runs_protocol_marker_is_case_sensitive_substring(self):
        assert runs_protocol_enabled("本包启用 evidence: runs 协议")
        assert not runs_protocol_enabled("evidence: Runs 协议")
        assert not runs_protocol_enabled("没有任何标记")


class TestHollowCommandLint:
    @pytest.mark.parametrize("command_text", ["true", ":", "exit 0", " echo hi ", "echo"])
    def test_hollow_commands_are_refused(self, command_text):
        assert is_hollow_command(command_text)

    @pytest.mark.parametrize(
        "command_text",
        ["python3 -m pytest -q", "echo hi > evidence.txt", "echo hi | tee log", "echo a && python3 -c 'x'"],
    )
    def test_real_commands_pass_the_lint(self, command_text):
        assert not is_hollow_command(command_text)


# --------------------------------------------------------------------------- #
# fingerprint semantics
# --------------------------------------------------------------------------- #


class TestTreeFingerprint:
    def test_spec_changes_do_not_affect_fingerprint(self, tmp_path):
        root = init_repo(tmp_path)
        before = compute_tree_fingerprint(root)
        (root / ".spec" / "specs" / "any").mkdir(parents=True)
        (root / ".spec" / "specs" / "any" / "tasks.md").write_text("- [ ] T\n", encoding="utf-8")
        assert compute_tree_fingerprint(root) == before

    def test_new_untracked_file_changes_fingerprint(self, tmp_path):
        root = init_repo(tmp_path)
        before = compute_tree_fingerprint(root)
        (root / "notes.txt").write_text("untracked\n", encoding="utf-8")
        assert compute_tree_fingerprint(root) != before

    def test_untracked_content_change_changes_fingerprint(self, tmp_path):
        root = init_repo(tmp_path)
        (root / "notes.txt").write_text("v1\n", encoding="utf-8")
        before = compute_tree_fingerprint(root)
        (root / "notes.txt").write_text("v2\n", encoding="utf-8")
        assert compute_tree_fingerprint(root) != before

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

    def test_empty_repo_does_not_crash(self, tmp_path):
        command(tmp_path, "git", "init", "-q", "-b", "main", str(tmp_path))
        (tmp_path / "solo.txt").write_text("no commits yet\n", encoding="utf-8")
        assert isinstance(compute_tree_fingerprint(tmp_path), str)

    def test_non_git_directory_returns_none(self, tmp_path):
        assert compute_tree_fingerprint(tmp_path) is None


# --------------------------------------------------------------------------- #
# run recorder
# --------------------------------------------------------------------------- #


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

#: all machine commands succeed — for gate tests that need the gate clearable.
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


class TestRunRecorder:
    def test_run_task_appends_full_field_record(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        run_recorder(root, SLUG, "--task", "task_0001")
        records = read_records(package)
        assert len(records) == 1
        record = records[0]
        assert set(record) == {
            "task",
            "cmd",
            "exit",
            "duration_s",
            "stdout_tail_sha256",
            "tree_fp",
            "package_fp",
            "package_id",
            "at",
        }
        assert record["task"] == "task_0001"
        assert record["cmd"] == "python3 -c \"print('ok')\""
        assert record["exit"] == 0
        assert isinstance(record["duration_s"], float) and record["duration_s"] >= 0
        assert len(record["tree_fp"]) == 64
        assert len(record["package_fp"]) == 64
        assert record["package_id"] == f".spec/specs/{SLUG}"
        assert record["at"].endswith("+00:00")

    def test_manual_task_is_skipped_without_record(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        run_recorder(root, SLUG, "--task", "task_0003")
        assert read_records(package) == []

    def test_failing_command_records_nonzero_exit_and_fails(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        result = run_recorder(root, SLUG, "--task", "task_0002", check=False)
        assert result.returncode == 1
        records = read_records(package)
        assert [record["exit"] for record in records] == [3]

    def test_run_all_is_sequential_and_reports_each(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        result = run_recorder(root, SLUG, "--all", check=False)
        assert result.returncode == 1  # task_0002 fails
        records = read_records(package)
        assert [record["task"] for record in records] == ["task_0001", "task_0002"]
        # All records share one fingerprint: writing runs.jsonl lives under .spec/.
        assert len({record["tree_fp"] for record in records}) == 1

    def test_hollow_command_is_refused_not_recorded(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(
            root,
            SLUG,
            runs_protocol=True,
            tasks_text="- [x] Hollow\n  - boundary: b\n  - verify: `true` 无意义",
        )
        result = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert result.returncode == 1
        assert "空洞" in result.stdout
        assert read_records(package) == []

    def test_duplicate_verify_fields_are_rejected_before_either_command_runs(self, tmp_path):
        root = init_repo(tmp_path)
        tasks = (
            "- [x] Ambiguous\n  - boundary: tests\n"
            "  - verify: `python3 -c \"open('first.marker','w').write('x')\"`\n"
            "  - verify: `python3 -c \"open('second.marker','w').write('x')\"`\n"
        )
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        result = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert result.returncode == 1
        assert "SPEC-VERIFY-002" in result.stdout + result.stderr
        assert not (root / "first.marker").exists()
        assert not (root / "second.marker").exists()
        assert not (package / RUNS_FILENAME).exists()
        checked = command(
            root,
            sys.executable,
            str(SCRIPTS / "check_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            check=False,
        )
        assert checked.returncode != 0
        assert "SPEC-VERIFY-002" in checked.stdout + checked.stderr
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            check=False,
        )
        assert archived.returncode == 1
        assert package.is_dir()
        assert not (root / ".spec" / "specs" / "archive" / SLUG).exists()

    def test_duplicate_task_ids_are_rejected_before_execution(self, tmp_path):
        root = init_repo(tmp_path)
        tasks = (
            "- [x] First\n  - id: task-same\n  - boundary: tests\n"
            "  - verify: `python3 -c \"open('first.marker','w').write('x')\"`\n"
            "- [x] Second\n  - id: task-same\n  - boundary: tests\n"
            "  - verify: `python3 -c \"open('second.marker','w').write('x')\"`\n"
        )
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        result = run_recorder(root, SLUG, "--task", "task-same", check=False)
        assert result.returncode == 1
        assert "duplicate task id" in result.stderr
        assert not (root / "first.marker").exists() and not (root / "second.marker").exists()
        assert not (package / RUNS_FILENAME).exists()

    def test_contract_violation_verify_is_refused(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(
            root,
            SLUG,
            runs_protocol=True,
            tasks_text="- [x] Bad\n  - boundary: b\n  - verify: pytest passes (no command span)",
        )
        result = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert result.returncode == 1
        assert "契约错误" in result.stdout
        assert read_records(package) == []

    def test_timeout_records_abnormal_exit(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(
            root,
            SLUG,
            runs_protocol=True,
            tasks_text=('- [x] Slow\n  - boundary: b\n  - verify: `python3 -c "import time; time.sleep(30)"` 超时'),
        )
        result = run_recorder(root, SLUG, "--task", "task_0001", "--timeout", "1", check=False)
        assert result.returncode == 1
        assert "timeout" in result.stdout
        records = read_records(package)
        assert records[0]["exit"] == -1

    def test_signal_exit_is_not_reported_as_timeout(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(
            root,
            SLUG,
            runs_protocol=True,
            tasks_text="- [x] Signal\n  - boundary: b\n  - verify: `kill -HUP $$`\n",
        )
        result = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert result.returncode == 1
        assert "exit=-1" in result.stdout and "timeout" not in result.stdout
        assert read_records(package)[0]["exit"] == -1

    @pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
    def test_unsafe_runs_file_is_not_written(self, tmp_path, kind):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        sentinel = root / ".spec/sentinel"
        sentinel.write_text("unchanged\n", encoding="utf-8")
        runs_path = package / RUNS_FILENAME
        if kind == "symlink":
            runs_path.symlink_to(sentinel)
        elif kind == "hardlink":
            os.link(sentinel, runs_path)
        else:
            os.mkfifo(runs_path)
        result = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert result.returncode == 1
        assert "SPEC-EVID-003" in result.stderr and "Traceback" not in result.stderr
        assert sentinel.read_text(encoding="utf-8") == "unchanged\n"

    def test_self_referential_gate_command_is_refused(self, tmp_path):
        root = init_repo(tmp_path)
        slug = "2026-10-12_self-ref"
        package = make_package(
            root,
            slug,
            runs_protocol=True,
            tasks_text=(
                f"- [x] Self check\n  - boundary: b\n  - verify: `python3 check_spec_package.py"
                f" --root . --slug {slug} --format json` 退出 0"
            ),
        )
        result = run_recorder(root, slug, "--task", "task_0001", check=False)
        assert result.returncode == 1
        assert "自指" in result.stdout
        assert read_records(package) == []

    def test_unknown_task_id_is_rejected(self, tmp_path):
        root = init_repo(tmp_path)
        make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        result = run_recorder(root, SLUG, "--task", "task_9999", check=False)
        assert result.returncode == 1
        assert "unknown task id" in result.stderr


# --------------------------------------------------------------------------- #
# gate evaluation
# --------------------------------------------------------------------------- #


def gate_for(root: Path, package: Path, slug: str = SLUG):
    return compute_gate_results(
        (package / "spec.md").read_text(encoding="utf-8"),
        (package / "tasks.md").read_text(encoding="utf-8"),
        (package / "checklist.md").read_text(encoding="utf-8"),
        slug=slug,
        root=root,
        package_dir=package,
    )


class TestRunsGate:
    @pytest.mark.parametrize(
        "other_slug", ["2026-11-05_fix-copied-package", "2026-10-13_add-executable-verify-recorder"]
    )
    def test_copied_package_cannot_reuse_another_packages_records(self, tmp_path, other_slug):
        root = init_repo(tmp_path)
        tasks = '- [x] Verify\n  - boundary: test\n  - verify: `python3 -c "print(42)"`\n'
        source = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        run_recorder(root, SLUG, "--task", "task_0001")
        copied = source.parent / other_slug
        shutil.copytree(source, copied)
        spec = copied / "spec.md"
        spec.write_text(
            spec.read_text(encoding="utf-8").replace(f"spec/{SLUG}", f"spec/{other_slug}"), encoding="utf-8"
        )
        git(root, "checkout", "-qb", f"spec/{other_slug}")
        checked = command(
            root,
            sys.executable,
            str(SCRIPTS / "check_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            other_slug,
            check=False,
        )
        if checked.returncode == 0:
            premature = command(
                root,
                sys.executable,
                str(SCRIPTS / "complete_spec_package.py"),
                "--root",
                str(root),
                "--slug",
                other_slug,
                "--archive",
                check=False,
            )
            assert premature.returncode != 0, "Copied evidence passed both check and archive"
        assert checked.returncode != 0
        assert "SPEC-EVID-002" in checked.stdout + checked.stderr
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            other_slug,
            "--archive",
            check=False,
        )
        assert archived.returncode == 1
        assert copied.is_dir()

    @pytest.mark.parametrize("indirect", [False, True], ids=["direct", "indirect"])
    def test_package_auxiliary_script_change_invalidates_record(self, tmp_path, indirect):
        root = init_repo(tmp_path)
        script = f".spec/specs/{SLUG}/verify.sh"
        verify = f'helper={script}; bash "$helper"' if indirect else f"bash {script}"
        tasks = f"- [x] Verify\n  - boundary: test\n  - verify: `{verify}`\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        helper = package / "verify.sh"
        helper.write_text("exit 0\n", encoding="utf-8")
        run_recorder(root, SLUG, "--task", "task_0001")
        before = compute_tree_fingerprint(root)
        helper.write_text("exit 42\n", encoding="utf-8")
        assert compute_tree_fingerprint(root) == before
        result = gate_for(root, package)
        assert result.runs_gate_ok is False
        assert any("SPEC-EVID-002" in error for error in result.runs_errors)
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            check=False,
        )
        assert archived.returncode == 1 and package.is_dir()

    @pytest.mark.parametrize("kind", ["hidden", "oversized", "file_symlink", "directory_symlink"])
    def test_auxiliary_entries_cannot_bypass_check_or_archive(self, tmp_path, kind):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        hidden = package / ".hidden.sh"
        if kind == "hidden":
            hidden.write_text("exit 0\n", encoding="utf-8")
        outside = root / "target"
        outside.mkdir()
        (outside / "script.sh").write_text("exit 0\n", encoding="utf-8")
        run_recorder(root, SLUG, "--all")
        if kind == "hidden":
            hidden.write_text("exit 1\n", encoding="utf-8")
        elif kind == "oversized":
            (package / "large.bin").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        elif kind == "file_symlink":
            (package / "link.sh").symlink_to(outside / "script.sh")
        else:
            (package / "helpers").symlink_to(outside, target_is_directory=True)
        checked = command(
            root,
            sys.executable,
            str(SCRIPTS / "check_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            check=False,
        )
        assert checked.returncode != 0
        assert ("SPEC-EVID-002" if kind == "hidden" else "SPEC-EVID-003") in checked.stdout + checked.stderr
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            check=False,
        )
        assert archived.returncode == 1 and package.is_dir()
        assert "Traceback" not in checked.stderr + archived.stderr

    @pytest.mark.parametrize("kind", ["package", "specs_ancestor"])
    def test_linked_package_path_cannot_reuse_records(self, tmp_path, kind):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        if kind == "package":
            moved = package.with_name(SLUG + "-moved")
            package.rename(moved)
            package.symlink_to(moved, target_is_directory=True)
        else:
            specs = package.parent
            moved = specs.with_name("specs-moved")
            specs.rename(moved)
            specs.symlink_to(moved, target_is_directory=True)
        checked = command(
            root,
            sys.executable,
            str(SCRIPTS / "check_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            check=False,
        )
        if checked.returncode == 0:
            premature = command(
                root,
                sys.executable,
                str(SCRIPTS / "complete_spec_package.py"),
                "--root",
                str(root),
                "--slug",
                SLUG,
                "--archive",
                check=False,
            )
            assert premature.returncode != 0, "linked package passed both check and archive"
        assert checked.returncode != 0
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            check=False,
        )
        assert archived.returncode == 1
        assert package.exists()

    def test_root_alias_is_resolved_before_checking_package_links(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        init_repo(root)
        make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        alias = tmp_path / "project-alias"
        alias.symlink_to(root, target_is_directory=True)
        common = ("--root", str(alias), "--slug", SLUG)
        command(root, sys.executable, str(SCRIPTS / "spec_verify.py"), "run", *common, "--all")
        checked = command(root, sys.executable, str(SCRIPTS / "check_spec_package.py"), *common, check=False)
        assert checked.returncode == 0, checked.stdout + checked.stderr

    def test_identical_slug_in_another_custom_specs_root_cannot_reuse_evidence(self, tmp_path):
        root = init_repo(tmp_path)
        original = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        first = root / "first" / "specs" / SLUG
        first.parent.mkdir(parents=True)
        shutil.move(str(original), str(first))
        common = ("--root", str(root), "--specs-dir", "first", "--slug", SLUG)
        command(root, sys.executable, str(SCRIPTS / "spec_verify.py"), "run", *common, "--all")
        second = root / "second" / "specs" / SLUG
        second.parent.mkdir(parents=True)
        shutil.copytree(first, second)
        other = ("--root", str(root), "--specs-dir", "second", "--slug", SLUG)
        checked = command(root, sys.executable, str(SCRIPTS / "check_spec_package.py"), *other, check=False)
        assert checked.returncode != 0 and "身份不匹配" in checked.stdout + checked.stderr
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            *other,
            "--archive",
            check=False,
        )
        assert archived.returncode == 1 and second.is_dir()

    def test_spec_tools_outside_package_are_not_fingerprinted(self, tmp_path):
        root = init_repo(tmp_path)
        script = ".spec/tools/verify.sh"
        tasks = f"- [x] Verify\n  - boundary: tests\n  - verify: `bash {script}`\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        helper = root / script
        helper.parent.mkdir(parents=True)
        helper.write_text("exit 0\n", encoding="utf-8")
        run_recorder(root, SLUG, "--task", "task_0001")
        helper.write_text("exit 42\n", encoding="utf-8")
        assert gate_for(root, package).runs_gate_ok is True  # explicitly outside the evidence boundary
        failed = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert failed.returncode == 1 and gate_for(root, package).runs_gate_ok is False

    def test_python_package_cache_requires_explicit_bytecode_suppression(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
        root = init_repo(tmp_path)
        script = f".spec/specs/{SLUG}/verify.py"
        tasks = f"- [x] Verify\n  - boundary: tests\n  - verify: `python3 {script}`\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        (package / "verify.py").write_text(
            "import sys\nsys.pycache_prefix = None\nimport helper\nassert helper.VALUE == 42\n", encoding="utf-8"
        )
        helper = package / "helper.py"
        helper.write_text("VALUE = 42\n", encoding="utf-8")
        failed = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert failed.returncode == 1 and "SPEC-EVID-002" in failed.stderr
        assert "__pycache__" in failed.stderr and "helper" in failed.stderr
        assert gate_for(root, package).runs_gate_ok is False
        (package / "tasks.md").write_text(tasks.replace("python3 ", "python3 -B "), encoding="utf-8")
        run_recorder(root, SLUG, "--task", "task_0001")
        assert gate_for(root, package).runs_gate_ok is True
        helper.write_text("VALUE = 0\n", encoding="utf-8")
        assert gate_for(root, package).runs_gate_ok is False

    @pytest.mark.parametrize("change", ["mode", "line_endings"])
    def test_auxiliary_file_metadata_or_line_endings_require_rerun(self, tmp_path, change):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        helper = package / "helper.sh"
        helper.write_bytes(b"one\ntwo\n")
        run_recorder(root, SLUG, "--all")
        if change == "mode":
            os.chmod(helper, helper.stat().st_mode | 0o100)
        else:
            helper.write_bytes(b"one\r\ntwo\r\n")
        assert gate_for(root, package).runs_gate_ok is False
        run_recorder(root, SLUG, "--all")
        assert gate_for(root, package).runs_gate_ok is True

    def test_package_state_changes_do_not_stale_auxiliary_fingerprint(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        (package / "verify.sh").write_text("exit 0\n", encoding="utf-8")
        run_recorder(root, SLUG, "--all")
        assert gate_for(root, package).runs_gate_ok is True
        with (package / "spec.md").open("a", encoding="utf-8") as spec:
            spec.write("\n<!-- updated state -->\n")
        with (package / "tasks.md").open("a", encoding="utf-8") as tasks:
            tasks.write("\n<!-- updated task state -->\n")
        with (package / RUNS_FILENAME).open("a", encoding="utf-8") as runs:
            runs.write(json.dumps({"task": "task-other", "exit": 1}) + "\n")
        (package / "handoff.md").write_text("state only\n", encoding="utf-8")
        (package / "runs.jsonl.corrupt").write_text("preserved backup\n", encoding="utf-8")
        assert gate_for(root, package).runs_gate_ok is True

    @pytest.mark.parametrize("tracked", [False, True], ids=["untracked", "tracked"])
    def test_custom_specs_dir_excludes_package_state_but_not_auxiliary_files(self, tmp_path, tracked):
        root = init_repo(tmp_path)
        original = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        package = root / "custom" / "specs" / SLUG
        package.parent.mkdir(parents=True)
        shutil.move(str(original), str(package))
        helper = package / "verify.sh"
        helper.write_text("exit 0\n", encoding="utf-8")
        other = package.with_name("2026-11-05_fix-other-package")
        shutil.copytree(package, other)
        if tracked:
            git(root, "add", "custom")
            git(root, "commit", "-qm", "track package")
        common = ("--root", str(root), "--specs-dir", "custom", "--slug", SLUG)
        command(root, sys.executable, str(SCRIPTS / "spec_verify.py"), "run", *common, "--all")
        for name in ("spec.md", "tasks.md"):
            with (package / name).open("a", encoding="utf-8") as handle:
                handle.write("\n<!-- state update -->\n")
        (other / "tasks.md").write_text("other package updated\n", encoding="utf-8")
        (other / "verify.sh").write_text("other package input updated\n", encoding="utf-8")
        checked = command(root, sys.executable, str(SCRIPTS / "check_spec_package.py"), *common, check=False)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        helper.write_text("exit 1\n", encoding="utf-8")
        checked = command(root, sys.executable, str(SCRIPTS / "check_spec_package.py"), *common, check=False)
        assert checked.returncode != 0 and "SPEC-EVID-002" in checked.stdout + checked.stderr
        command(root, sys.executable, str(SCRIPTS / "spec_verify.py"), "run", *common, "--all")
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            *common,
            "--archive",
            check=False,
        )
        assert archived.returncode == 0, archived.stdout + archived.stderr

    def test_old_active_record_without_package_fingerprint_requires_rerun(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        records = read_records(package)
        for record in records:
            record.pop("package_fp", None)
        (package / RUNS_FILENAME).write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        results = gate_for(root, package)
        assert results.runs_gate_ok is False
        assert any("SPEC-EVID-002" in error for error in results.runs_errors)
        run_recorder(root, SLUG, "--all")
        assert gate_for(root, package).runs_gate_ok is True

    def test_verify_cannot_succeed_after_rewriting_its_own_package_input(self, tmp_path):
        root = init_repo(tmp_path)
        script = f".spec/specs/{SLUG}/verify.sh"
        package = make_package(
            root,
            SLUG,
            runs_protocol=True,
            tasks_text=f"- [x] Verify\n  - boundary: test\n  - verify: `bash {script}`\n",
        )
        (package / "verify.sh").write_text(f"printf 'exit 42\\n' > {script}\nexit 0\n", encoding="utf-8")
        result = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert result.returncode == 1
        assert "SPEC-EVID-002" in result.stdout + result.stderr
        assert read_records(package)[-1]["exit"] != 0
        assert gate_for(root, package).runs_gate_ok is False

    def test_unreadable_post_run_input_invalidates_previous_success_after_restoration(self, tmp_path):
        root = init_repo(tmp_path)
        script = f".spec/specs/{SLUG}/verify.sh"
        tasks = f"- [x] Verify\n  - boundary: test\n  - verify: `bash {script}`\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        (package / "verify.sh").write_text(
            f"if [ -e .spec/switch ]; then ln -s verify.sh .spec/specs/{SLUG}/bad; fi\nexit 0\n",
            encoding="utf-8",
        )
        run_recorder(root, SLUG, "--task", "task_0001")
        (root / ".spec/switch").touch()
        failed = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert failed.returncode == 1 and "SPEC-EVID-003" in failed.stderr
        (package / "bad").unlink()
        (root / ".spec/switch").unlink()
        assert read_records(package)[-1]["exit"] != 0
        assert gate_for(root, package).runs_gate_ok is False

    def test_legacy_package_without_marker_keeps_exact_conclusion(self, tmp_path):
        root = init_repo(tmp_path)
        # EXP-01b's old fixture shape: verify mentions pytest, nothing runs.
        package = make_package(
            root,
            SLUG,
            runs_protocol=False,
            tasks_text="- [x] Task\n  - boundary: b\n  - verify: pytest\n",
        )
        results = gate_for(root, package)
        assert results.runs_protocol is False
        assert results.runs_gate_ok is None  # 不适用，永不阻塞
        assert results.overall_ok is True
        assert overall_check_passed(
            (package / "spec.md").read_text(encoding="utf-8"),
            (package / "tasks.md").read_text(encoding="utf-8"),
            (package / "checklist.md").read_text(encoding="utf-8"),
            slug=SLUG,
            root=root,
            package_dir=package,
        )

    def test_runs_package_without_records_names_the_task_as_unverified(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        results = gate_for(root, package)
        assert results.runs_gate_ok is False
        assert any("task_0001" in error and "未验证" in error for error in results.runs_errors)
        assert results.overall_ok is False

    def test_passing_records_clear_the_gate(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--task", "task_0001")
        results = gate_for(root, package)
        # task_0002 has no record yet → still named.
        assert results.runs_gate_ok is False
        run_recorder(root, SLUG, "--task", "task_0002")
        results = gate_for(root, package)
        assert results.runs_gate_ok is True
        assert results.overall_ok is True

    def test_manual_task_needs_no_record(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(
            root,
            SLUG,
            runs_protocol=True,
            tasks_text="- [x] Only manual\n  - boundary: b\n  - verify: manual: 只读复核",
        )
        results = gate_for(root, package)
        assert results.runs_gate_ok is True
        assert "manual 任务 1 项" in results.runs_detail

    def test_tree_mutation_after_evidence_reports_expired(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        (root / "README.md").write_text("tampered after evidence\n", encoding="utf-8")
        results = gate_for(root, package)
        assert results.runs_gate_ok is False
        assert any("task_0001" in error and "证据过期" in error for error in results.runs_errors)

    def test_changed_verify_command_cannot_reuse_old_record_for_check_or_archive(self, tmp_path):
        root = init_repo(tmp_path)
        old_command = 'python3 -c "print(42)"'
        failing_command = 'python3 -c "raise SystemExit(42)"'
        new_command = 'python3 -c "print(43)"'
        tasks = f"- [x] Verify\n  - boundary: tests\n  - verify: `{old_command}`\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        run_recorder(root, SLUG, "--task", "task_0001")
        fingerprint = compute_tree_fingerprint(root)
        (package / "tasks.md").write_text(tasks.replace(old_command, failing_command), encoding="utf-8")
        assert compute_tree_fingerprint(root) == fingerprint

        checked = command(
            root,
            sys.executable,
            str(SCRIPTS / "check_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            check=False,
        )
        assert checked.returncode != 0
        assert "SPEC-EVID-002" in checked.stdout + checked.stderr
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            check=False,
        )
        assert archived.returncode == 1
        assert package.is_dir()
        assert not (root / ".spec" / "specs" / "archive" / SLUG).exists()

        failed = run_recorder(root, SLUG, "--task", "task_0001", check=False)
        assert failed.returncode == 1 and "exit=42" in failed.stdout
        assert gate_for(root, package).runs_gate_ok is False
        (package / "tasks.md").write_text(tasks.replace(old_command, new_command), encoding="utf-8")
        run_recorder(root, SLUG, "--task", "task_0001")
        assert gate_for(root, package).runs_gate_ok is True
        assert read_records(package)[-1]["cmd"] == new_command
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            check=False,
        )
        assert archived.returncode == 0, archived.stdout + archived.stderr

    def test_archive_uses_same_trimmed_verify_command_as_recorder(self, tmp_path):
        root = init_repo(tmp_path)
        tasks = '- [x] Verify\n  - boundary: tests\n  - verify: `  python3 -c "print(42)"  `\n'
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        run_recorder(root, SLUG, "--task", "task_0001")
        assert gate_for(root, package).runs_gate_ok is True
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            check=False,
        )
        assert archived.returncode == 0, archived.stdout + archived.stderr
        summary = (root / ".spec" / "specs" / "archive" / SLUG / "completion-summary.md").read_text(encoding="utf-8")
        assert '`python3 -c "print(42)"` -> exit 0 (runs:task_0001)' in summary

    def test_quoted_internal_whitespace_changes_the_verify_command(self, tmp_path):
        root = init_repo(tmp_path)
        old_command = "python3 -c \"print('a  b')\""
        new_command = "python3 -c \"print('a b')\""
        tasks = f"- [x] Verify\n  - boundary: tests\n  - verify: `{old_command}`\n"
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=tasks)
        run_recorder(root, SLUG, "--task", "task_0001")
        (package / "tasks.md").write_text(tasks.replace(old_command, new_command), encoding="utf-8")
        results = gate_for(root, package)
        assert results.runs_gate_ok is False
        assert any("SPEC-EVID-002" in error for error in results.runs_errors)

    def test_contract_violation_verify_fails_the_gate(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(
            root,
            SLUG,
            runs_protocol=True,
            tasks_text="- [x] Bad\n  - boundary: b\n  - verify: prose only, no command",
        )
        results = gate_for(root, package)
        assert results.runs_gate_ok is False
        assert any("task_0001" in error and "manual" in error for error in results.runs_errors)

    def test_malformed_runs_jsonl_fails_closed(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        (package / RUNS_FILENAME).write_text("{not json}\n", encoding="utf-8")
        results = gate_for(root, package)
        assert results.runs_gate_ok is False
        assert any("runs 证据不可读" in error for error in results.runs_errors)

    @pytest.mark.parametrize("bad_bytes", [b'{"task":', b"\xff\n"], ids=["malformed-json", "invalid-utf8"])
    def test_runs_protocol_cli_rejects_corrupt_evidence_without_writes(self, tmp_path, bad_bytes):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        (package / RUNS_FILENAME).write_bytes(bad_bytes)
        before = {path.relative_to(package): path.read_bytes() for path in package.rglob("*") if path.is_file()}
        checked = command(
            root,
            sys.executable,
            str(SCRIPTS / "check_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            check=False,
        )
        assert checked.returncode != 0
        assert "SPEC-EVID-003" in checked.stdout + checked.stderr
        assert "Traceback" not in checked.stdout + checked.stderr
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            check=False,
        )
        assert archived.returncode == 1
        assert "Traceback" not in archived.stdout + archived.stderr
        assert not (root / ".spec" / "specs" / "archive" / SLUG).exists()
        after = {path.relative_to(package): path.read_bytes() for path in package.rglob("*") if path.is_file()}
        assert after == before

    def test_corrupt_evidence_requires_preservation_then_rerunning_all_tasks(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        corrupt = b'{"task":\n'
        (package / RUNS_FILENAME).write_bytes(corrupt)
        run_recorder(root, SLUG, "--task", "task_0001")
        assert gate_for(root, package).runs_gate_ok is False
        damaged_bytes = (package / RUNS_FILENAME).read_bytes()
        assert damaged_bytes.startswith(corrupt)
        (package / RUNS_FILENAME).rename(package / "runs.jsonl.corrupt")
        run_recorder(root, SLUG, "--all")
        assert gate_for(root, package).runs_gate_ok is True
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            check=False,
        )
        assert archived.returncode == 0, archived.stdout + archived.stderr
        archive = root / ".spec" / "specs" / "archive" / SLUG
        assert (archive / "runs.jsonl.corrupt").read_bytes() == damaged_bytes
        assert len(read_records(archive)) == 2

    def test_non_protocol_archive_missing_run_can_be_recorded_then_retried(self, tmp_path):
        root = init_repo(tmp_path)
        task_command = 'python3 -c "print(42)"'
        tasks = f"- [x] Verify\n  - boundary: tests\n  - verify: `{task_command}`\n"
        package = make_package(root, SLUG, runs_protocol=False, tasks_text=tasks)
        archive_args = (
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            "--test-evidence",
            f"`{task_command}` -> exit 0 (runs:task_0001)",
        )
        rejected = command(root, *archive_args, check=False)
        assert rejected.returncode == 1 and "SPEC-EVID-001" in rejected.stderr
        run_recorder(root, SLUG, "--task", "task_0001")
        archived = command(root, *archive_args, check=False)
        assert archived.returncode == 0, archived.stdout + archived.stderr
        assert not package.exists()
        assert (root / ".spec" / "specs" / "archive" / SLUG / RUNS_FILENAME).is_file()

    def test_non_protocol_archive_rejects_note_for_old_verify_command(self, tmp_path):
        root = init_repo(tmp_path)
        old = 'python3 -c "print(42)"'
        new = 'python3 -c "raise SystemExit(42)"'
        tasks = f"- [x] Verify\n  - boundary: tests\n  - verify: `{old}`\n"
        package = make_package(root, SLUG, runs_protocol=False, tasks_text=tasks)
        run_recorder(root, SLUG, "--task", "task_0001")
        (package / "tasks.md").write_text(tasks.replace(old, new), encoding="utf-8")
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            "--test-evidence",
            f"`{old}` -> exit 0 (runs:task_0001)",
            check=False,
        )
        assert archived.returncode == 1
        assert "任务命令不一致" in archived.stderr
        assert package.is_dir()

    def test_non_protocol_archive_requires_only_the_explicitly_cited_task(self, tmp_path):
        root = init_repo(tmp_path)
        tasks = (
            '- [x] One\n  - boundary: tests\n  - verify: `python3 -c "print(42)"`\n'
            '- [x] Two\n  - boundary: tests\n  - verify: `python3 -c "raise SystemExit(1)"`\n'
        )
        make_package(root, SLUG, runs_protocol=False, tasks_text=tasks)
        run_recorder(root, SLUG, "--task", "task_0001")
        archived = command(
            root,
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            "--test-evidence",
            '`python3 -c "print(42)"` -> exit 0 (runs:task_0001)',
            check=False,
        )
        assert archived.returncode == 0, archived.stdout + archived.stderr

    @pytest.mark.parametrize("changed", ["tree", "auxiliary", "package_id", "legacy_record"])
    def test_non_protocol_archive_rejects_stale_explicit_evidence(self, tmp_path, changed):
        root = init_repo(tmp_path)
        task_command = 'python3 -c "print(42)"'
        tasks = f"- [x] Verify\n  - boundary: tests\n  - verify: `{task_command}`\n"
        package = make_package(root, SLUG, runs_protocol=False, tasks_text=tasks)
        helper = package / "helper.txt"
        helper.write_text("original\n", encoding="utf-8")
        run_recorder(root, SLUG, "--task", "task_0001")
        if changed == "tree":
            (root / "README.md").write_text("changed\n", encoding="utf-8")
        elif changed == "auxiliary":
            helper.write_text("changed\n", encoding="utf-8")
        else:
            record = read_records(package)[-1]
            if changed == "package_id":
                record["package_id"] = ".spec/specs/other"
            else:
                record.pop("package_fp")
                record.pop("package_id")
            (package / RUNS_FILENAME).write_text(json.dumps(record) + "\n", encoding="utf-8")
        archive_args = (
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            SLUG,
            "--archive",
            "--test-evidence",
            f"`{task_command}` -> exit 0 (runs:task_0001)",
        )
        rejected = command(root, *archive_args, check=False)
        assert rejected.returncode == 1
        assert "SPEC-EVID-002" in rejected.stderr
        assert package.is_dir()
        run_recorder(root, SLUG, "--task", "task_0001")
        archived = command(root, *archive_args, check=False)
        assert archived.returncode == 0, archived.stdout + archived.stderr

    def test_gate_row_and_convergence_name_the_gate(self, tmp_path):
        from check_spec_package import convergence_state, gate_failure_details

        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        results = gate_for(root, package)
        converged, gaps = convergence_state(results)
        assert not converged
        assert any("运行证据" in gap for gap in gaps)
        assert "运行证据" in gate_failure_details(results, SLUG)

    def test_routing_context_without_package_dir_never_blocks(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        ok, errors, detail = evaluate_runs_gate(
            (package / "spec.md").read_text(encoding="utf-8"),
            (package / "tasks.md").read_text(encoding="utf-8"),
            None,
            None,
        )
        assert ok is None and errors == [] and "适用外" in detail

    def test_anchor_head_comparison_is_replaced_by_runs_in_runs_packages(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        # Move HEAD after evidence: the recorded fingerprints embed the old
        # HEAD, so re-capture on the new HEAD; the well-formed-but-stale
        # anchor written below must not double-punish (runs is the authority).
        git(root, "add", ".")
        git(root, "commit", "-qm", "advance HEAD")
        run_recorder(root, SLUG, "--all")
        checklist = (package / "checklist.md").read_text(encoding="utf-8")
        stale_anchor = checklist.replace(
            "- 脚本验证：pytest passed",
            "- 脚本验证：pytest passed\n- 证据锚点：HEAD " + "0" * 40 + " @ 2026-10-12T00:00:00Z",
        )
        (package / "checklist.md").write_text(stale_anchor, encoding="utf-8")
        results = gate_for(root, package)
        assert results.runs_gate_ok is True
        assert results.evidence_anchor_ok is True  # 良构锚点免 HEAD 比对

    def test_malformed_anchor_still_fails_closed_in_runs_packages(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        checklist = (package / "checklist.md").read_text(encoding="utf-8")
        malformed = checklist.replace(
            "- 脚本验证：pytest passed",
            "- 脚本验证：pytest passed\n- 证据锚点：HEAD deadbeef @ 2026-10-12T00:00:00Z",
        )
        (package / "checklist.md").write_text(malformed, encoding="utf-8")
        results = gate_for(root, package)
        assert results.runs_gate_ok is True
        assert results.evidence_anchor_ok is False
        assert results.overall_ok is False

    @pytest.mark.parametrize("container", ["", "retired/2026-10-12"])
    def test_archived_package_keeps_the_identity_its_records_earned(self, tmp_path, container):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        archive = root / ".spec" / "specs" / "archive" / container / SLUG
        archive.parent.mkdir(parents=True, exist_ok=True)
        package.rename(archive)

        results = gate_for(root, archive)

        assert results.runs_gate_ok is True, results.runs_errors

    def test_archive_relaxation_is_anchored_to_the_specs_container(self, tmp_path):
        root = init_repo(tmp_path)
        archived = root / ".spec" / "specs" / "archive" / SLUG
        retired = root / ".spec" / "specs" / "archive" / "retired" / "2026-10-12" / SLUG
        elsewhere = root / "docs" / "archive" / "specs" / SLUG

        assert package_identities(root, archived) == {
            f".spec/specs/archive/{SLUG}",
            f".spec/specs/{SLUG}",
        }
        assert package_identities(root, retired) == {
            f".spec/specs/archive/retired/2026-10-12/{SLUG}",
            f".spec/specs/{SLUG}",
        }
        # An unrelated ancestor called ``archive`` is not an archive container.
        assert package_identities(root, elsewhere) == {f"docs/archive/specs/{SLUG}"}

    def test_active_package_rejects_an_archived_form_identity(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
        run_recorder(root, SLUG, "--all")
        # The relaxation is one-way: only a package that actually sits under the
        # archive container may carry the pre-archive identity. An active
        # package still demands the record it wrote in place.
        archived_form = f".spec/specs/archive/{SLUG}"
        runs = package / RUNS_FILENAME
        rewritten = [
            {**json.loads(line), "package_id": archived_form}
            for line in runs.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        runs.write_text("".join(json.dumps(record) + "\n" for record in rewritten), encoding="utf-8")

        results = gate_for(root, package)

        assert results.runs_gate_ok is False
        assert any("SPEC-EVID-002" in error and "身份" in error for error in results.runs_errors)


class TestMachineVerificationSummary:
    def test_manual_tasks_are_excluded_from_the_denominator(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(root, SLUG, runs_protocol=True, tasks_text=MACHINE_TASKS)
        run_recorder(root, SLUG, "--task", "task_0001")
        run_recorder(root, SLUG, "--task", "task_0002", check=False)
        summary = machine_verification_summary((package / "tasks.md").read_text(encoding="utf-8"), package)
        assert summary == "1/2；manual 任务 1 项不计入分母"

    def test_all_manual_package_has_no_rate_line(self, tmp_path):
        root = init_repo(tmp_path)
        package = make_package(
            root,
            SLUG,
            runs_protocol=True,
            tasks_text="- [x] Manual\n  - boundary: b\n  - verify: manual: 只读",
        )
        assert machine_verification_summary((package / "tasks.md").read_text(encoding="utf-8"), package) is None


# --------------------------------------------------------------------------- #
# sandbox replays: EXP-01b / EXP-02
# --------------------------------------------------------------------------- #


class TestAuditReplays:
    def test_replay_exp01b_failing_test_command_blocks_check_naming_task(self, tmp_path):
        """EXP-01b: code unchanged, tests fail → the chain must not pass.

        The audit showed a task whose verify said pytest could fail for real
        while check → archive → commit → push all exited 0. Under the runs
        protocol the executed failure is recorded and check names the task.
        """
        root = init_repo(tmp_path)
        slug = "2026-10-12_replay-exp01b"
        tasks = "- [x] Keep behavior\n  - boundary: src\n  - verify: `python3 -m pytest test_src.py -q` 全绿\n"
        make_package(root, slug, runs_protocol=True, tasks_text=tasks)
        (root / "test_src.py").write_text(
            "def test_broken():\n    assert False  # the bug the audit replays\n",
            encoding="utf-8",
        )
        run_recorder(root, slug, "--task", "task_0001", check=False)
        result = command(
            root,
            sys.executable,
            str(SCRIPTS / "check_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            slug,
            "--format",
            "json",
            check=False,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert payload["overall"] is False
        assert any("task_0001" in gap and "未验证" in gap for gap in payload["overallGaps"])

    def test_replay_exp02_post_evidence_tamper_reports_expired(self, tmp_path):
        """EXP-02: evidence captured, then the tree is broken → 证据过期."""
        root = init_repo(tmp_path)
        slug = "2026-10-12_replay-exp02"
        tasks = "- [x] Keep behavior\n  - boundary: src\n  - verify: `python3 -m pytest test_src.py -q` 全绿\n"
        make_package(root, slug, runs_protocol=True, tasks_text=tasks)
        (root / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "test_src.py").write_text(
            "from src import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
            encoding="utf-8",
        )
        run_recorder(root, slug, "--task", "task_0001")
        # Sabotage the working tree after the evidence was captured.
        (root / "src.py").write_text("VALUE = 2  # tampered\n", encoding="utf-8")
        result = command(
            root,
            sys.executable,
            str(SCRIPTS / "check_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            slug,
            "--format",
            "json",
            check=False,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any("task_0001" in gap and "证据过期" in gap for gap in payload["overallGaps"])

    def test_replay_exp02_rerun_after_fix_restores_the_gate(self, tmp_path):
        root = init_repo(tmp_path)
        slug = "2026-10-12_replay-exp02-fix"
        tasks = "- [x] Keep behavior\n  - boundary: src\n  - verify: `python3 -m pytest test_src.py -q` 全绿\n"
        make_package(root, slug, runs_protocol=True, tasks_text=tasks)
        (root / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "test_src.py").write_text(
            "from src import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
            encoding="utf-8",
        )
        run_recorder(root, slug, "--task", "task_0001")
        (root / "src.py").write_text("VALUE = 2\n", encoding="utf-8")
        # Fix the tree and re-capture: newest record wins.
        (root / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
        run_recorder(root, slug, "--task", "task_0001")
        result = command(
            root,
            sys.executable,
            str(SCRIPTS / "check_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            slug,
            "--format",
            "json",
            check=False,
        )
        assert result.returncode == 0


# --------------------------------------------------------------------------- #
# F3 show subcommand: bounded read-only evidence view (doc 03 §2.3)
# --------------------------------------------------------------------------- #


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


class TestShowSubcommand:
    def test_show_replaces_invalid_choice_and_reports_latest_view(self, tmp_path):
        root = init_repo(tmp_path)
        slug = "2026-10-19_add-public-example-27"
        make_package(
            root,
            slug,
            runs_protocol=True,
            tasks_text=(
                "# T\n## A\n"
                "- [ ] task one\n  - boundary: b\n  - verify: `python3 -c pass`\n"
                "- [ ] task two\n  - boundary: b\n  - verify: `python3 -c pass`\n"
            ),
        )
        run_recorder(root, slug, "--all")
        result = show_cli(root, slug)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["capability"] == "evidence"
        assert payload["counts"] == {"returned": 2, "total_matching": 2}
        assert payload["complete"] is True
        assert payload["freshness"]["status"] == "evaluated"
        # recorded right here by run: both tasks fresh against this tree
        assert set(payload["freshness"]["per_task"].values()) == {"fresh"}

    def test_show_never_executes_verify_or_writes(self, tmp_path):
        root = init_repo(tmp_path)
        slug = "2026-10-19_add-public-example-27"
        make_package(
            root,
            slug,
            runs_protocol=True,
            tasks_text="# T\n## A\n- [ ] boom\n  - boundary: b\n  - verify: `python3 -c 'raise SystemExit(3)'`\n",
        )
        before = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}
        # no runs.jsonl yet: missing (unverified), never an empty success;
        # the failing verify command is never executed on the read path
        missing = show_cli(root, slug, "--failures-only", check=False)
        assert missing.returncode == 1
        assert missing.stdout == ""
        assert "error: missing:" in missing.stderr
        after = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}
        assert after == before

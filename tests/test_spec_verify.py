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
    is_hollow_command,
    machine_verification_summary,
    runs_protocol_enabled,
)

from tests.test_complete_spec_package import _COMPLETE_CHECKLIST, _COMPLETE_SPEC_EN  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SLUG = "2026-10-12_add-executable-verify-recorder"


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
        assert set(record) == {"task", "cmd", "exit", "duration_s", "stdout_tail_sha256", "tree_fp", "at"}
        assert record["task"] == "task_0001"
        assert record["cmd"] == "python3 -c \"print('ok')\""
        assert record["exit"] == 0
        assert isinstance(record["duration_s"], float) and record["duration_s"] >= 0
        assert len(record["tree_fp"]) == 64
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
        slug = "2026-10-19_add-internal-read-surface"
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
        slug = "2026-10-19_add-internal-read-surface"
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

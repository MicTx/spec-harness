"""Planning-card append: candidate lint, lossless writes and failure recovery."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import package
import plan
import pytest
import spec as spec_cli

from tests.v3_support import SLUG, cli, git, make_package, repo

ROOT = Path(__file__).resolve().parents[1]


def _planning(root):
    repo(root)
    return make_package(root)


def _snapshot(directory):
    return {
        path.relative_to(directory).as_posix(): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in directory.rglob("*")
        if path.is_file()
    }


def _args(*extra):
    return [
        "task-card",
        "--id",
        "task-extra",
        "--boundary",
        "app.py",
        "--verify",
        "true",
        "--append",
        "--criterion",
        "The new behavior is covered",
        *extra,
    ]


def _append(root, *extra):
    return cli(root, *_args(*extra))


def test_append_single_preserves_existing_text_and_stdout(tmp_path):
    directory = _planning(tmp_path)
    tasks = directory / "tasks.md"
    spec = directory / "spec.md"
    old_tasks = tasks.read_text()
    old_spec = spec.read_text()
    generated = cli(tmp_path, "task-card", "--id", "task-extra", "--boundary", "app.py", "--verify", "true")
    assert generated.returncode == 0
    result = _append(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["appended"] == ["task-extra"]
    assert payload["criteria_added"] == ["C2"]
    assert payload["lint_mode"] == "static"
    assert payload["ready"] is True and payload["lint"]["ready"] is True
    assert tasks.read_text().startswith(old_tasks)
    assert tasks.read_text().endswith(generated.stdout)
    assert spec.read_text().split("## Criteria")[0] == old_spec.split("## Criteria")[0]
    assert spec.read_text().split("## Decisions")[1] == old_spec.split("## Decisions")[1]
    assert "- C1 | Fixture works | task-value" in spec.read_text()
    assert cli(tmp_path, "plan-lint", "--static").returncode == 0
    assert not (directory / "runs.jsonl").exists()


def test_append_tdd_pair_allocates_criteria_after_maximum(tmp_path):
    directory = _planning(tmp_path)
    spec = directory / "spec.md"
    spec.write_text(
        spec.read_text().replace(
            "- C1 | Fixture works | task-value",
            "- C9 | Keep first | task-value\n- C2 | Keep second | task-value",
        )
    )
    result = _append(tmp_path, "--tdd", "--test-boundary", "tests/test_extra.py")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["appended"] == ["task-extra-test", "task-extra"]
    assert payload["criteria_added"] == ["C10", "C11"]
    records = package.parse_tasks((directory / "tasks.md").read_text())
    assert [row.id for row in records] == ["task-value", "task-extra-test", "task-extra"]
    assert records[-1].depends_on == ("task-extra-test",)
    assert records[-2].expect == "red" and records[-1].expect == "green"
    assert [row[0] for row in package.criteria(spec.read_text())] == ["C9", "C2", "C10", "C11"]


def test_append_without_criterion_uses_existing_coverage(tmp_path):
    directory = _planning(tmp_path)
    spec = directory / "spec.md"
    spec.write_text(
        spec.read_text().replace(
            "## Decisions",
            "- C2 | Planned result | task-extra\n## Decisions",
        )
    )
    before = spec.read_bytes()
    result = cli(tmp_path, "task-card", "--id", "task-extra", "--boundary", "app.py", "--append")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["criteria_added"] == []
    assert spec.read_bytes() == before


def test_append_uncovered_task_fails_without_writing(tmp_path):
    directory = _planning(tmp_path)
    before = _snapshot(directory)
    result = cli(tmp_path, "task-card", "--id", "task-extra", "--boundary", "app.py", "--append")
    assert result.returncode == 1
    assert json.loads(result.stdout)["ready"] is False
    assert _snapshot(directory) == before


@pytest.mark.parametrize("existing,tdd", [("task-extra", False), ("task-extra", True), ("task-extra-test", True)])
def test_append_duplicate_ids_leave_entire_pair_unchanged(tmp_path, existing, tdd):
    directory = _planning(tmp_path)
    for name in ("spec.md", "tasks.md"):
        path = directory / name
        path.write_text(path.read_text().replace("task-value", existing))
    before = _snapshot(directory)
    result = _append(tmp_path, *(["--tdd"] if tdd else []))
    assert result.returncode == 1 and "duplicate task id" in result.stderr
    assert _snapshot(directory) == before


@pytest.mark.parametrize("marker", ["baseline", "frozen"])
def test_append_frozen_package_has_no_writes(tmp_path, marker):
    directory = _planning(tmp_path)
    if marker == "baseline":
        package.append_run(directory, {"kind": "baseline", "task": "package", "head": "recorded"})
    else:
        path = directory / "spec.md"
        path.write_text(path.read_text() + "- frozen: 2026-11-14T00:00:00+00:00\n")
    before = _snapshot(directory)
    result = _append(tmp_path)
    assert result.returncode == 1 and "plan-amend" in result.stderr
    assert _snapshot(directory) == before


def test_append_explicit_wrong_branch_has_no_writes(tmp_path):
    directory = _planning(tmp_path)
    before = _snapshot(directory)
    git(tmp_path, "checkout", "main")
    result = cli(tmp_path, "--slug", SLUG, *_args())
    assert result.returncode == 1 and "branch" in result.stderr
    assert _snapshot(directory) == before


def test_append_explicit_unbound_package_on_main(tmp_path):
    directory = _planning(tmp_path)
    spec = directory / "spec.md"
    spec.write_text(spec.read_text().split("## Binding")[0])
    git(tmp_path, "checkout", "main")
    before = _snapshot(directory)
    implicit = _append(tmp_path)
    assert implicit.returncode == 1
    assert _snapshot(directory) == before
    explicit = cli(tmp_path, "--slug", SLUG, *_args())
    assert explicit.returncode == 0, explicit.stdout + explicit.stderr
    assert "## Binding" not in spec.read_text()


def test_append_malformed_binding_is_not_an_unbound_plan(tmp_path):
    directory = _planning(tmp_path)
    spec = directory / "spec.md"
    spec.write_text(spec.read_text().replace(f"- branch: `spec/{SLUG}`", "- branch:"))
    before = _snapshot(directory)
    result = cli(tmp_path, "--slug", SLUG, *_args())
    assert result.returncode == 1 and "branch" in result.stderr
    assert _snapshot(directory) == before


@pytest.mark.parametrize(
    "option,value",
    [
        ("--id", "bad_id"),
        ("--expect", "blue"),
        ("--boundary", "   "),
        ("--title", "hello\n- [ ] injected"),
        ("--title", "hello\u2028world"),
        ("--boundary", "app.py\r\n  - id: injected"),
        ("--verify", "true\nfalse"),
        ("--verify", "echo `id`"),
        ("--test-boundary", "tests/test\n_other.py"),
        ("--criterion", " "),
        ("--criterion", "left | right"),
        ("--criterion", "line\rbreak"),
        ("--depends-on", "task-value\u0085injected"),
    ],
)
def test_append_rejects_unrepresentable_fields_without_writes(tmp_path, option, value):
    directory = _planning(tmp_path)
    before = _snapshot(directory)
    result = _append(tmp_path, option, value)
    assert result.returncode == 1, result.stdout + result.stderr
    assert _snapshot(directory) == before


@pytest.mark.parametrize(
    "option,value,rule",
    [
        ("--boundary", "new:outside.py", "PLAN-003"),
        ("--depends-on", "task-unknown", "PLAN-005"),
        ("--criterion", "Verify task-unknown", "PLAN-012"),
    ],
)
def test_append_static_lint_rejects_invalid_candidate_without_writes(tmp_path, option, value, rule):
    directory = _planning(tmp_path)
    before = _snapshot(directory)
    result = _append(tmp_path, option, value)
    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)
    assert payload["lint_mode"] == "static" and payload["appended"] == []
    assert any(gap["rule"] == rule for gap in payload["lint"]["gaps"])
    assert _snapshot(directory) == before


def test_append_never_executes_old_or_new_verify(tmp_path):
    repo(tmp_path)
    directory = make_package(tmp_path, verify="touch old-verify-ran")
    result = _append(tmp_path, "--verify", "printf ok | tee new-verify-ran && false", "--expect", "red")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "old-verify-ran").exists()
    assert not (tmp_path / "new-verify-ran").exists()
    assert not (directory / "runs.jsonl").exists()
    record = package.parse_tasks((directory / "tasks.md").read_text())[-1]
    assert record.command == "printf ok | tee new-verify-ran && false"


@pytest.mark.parametrize("kind", ["duplicate", "malformed", "sections"])
def test_append_ambiguous_criteria_fail_without_writes(tmp_path, kind):
    directory = _planning(tmp_path)
    spec = directory / "spec.md"
    if kind == "duplicate":
        text = spec.read_text().replace("## Decisions", "- C1 | Another | task-value\n## Decisions")
    elif kind == "malformed":
        text = spec.read_text().replace("- C1 |", "- NotAnId |")
    else:
        text = spec.read_text() + "\n## Criteria\n"
    spec.write_text(text)
    before = _snapshot(directory)
    result = _append(tmp_path)
    assert result.returncode == 1 and "Criteria" in result.stderr
    assert _snapshot(directory) == before


def test_append_creates_missing_criteria_section(tmp_path):
    directory = _planning(tmp_path)
    spec = directory / "spec.md"
    spec.write_text(spec.read_text().replace("## Criteria\n- C1 | Fixture works | task-value\n", ""))
    (directory / "tasks.md").write_text("# Draft tasks\n")
    result = _append(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["criteria_added"] == ["C1"]
    assert spec.read_text().count("## Criteria") == 1
    assert spec.read_text().index("## Criteria") < spec.read_text().index("## Decisions")


@pytest.mark.parametrize("name", ["spec.md", "tasks.md"])
def test_append_keeps_symlink_targets_untouched(tmp_path, name):
    directory = _planning(tmp_path)
    file = directory / name
    outside = tmp_path / "linked-text"
    outside.write_bytes(file.read_bytes())
    before = outside.read_bytes()
    file.unlink()
    file.symlink_to(outside)
    result = cli(tmp_path, "--slug", SLUG, *_args())
    assert result.returncode == 1 and "symlink" in result.stderr
    assert file.is_symlink() and outside.read_bytes() == before


def test_append_criterion_flag_without_append_is_an_error(tmp_path):
    result = cli(tmp_path, "task-card", "--id", "task-extra", "--boundary", "app.py", "--criterion", "Outcome")
    assert result.returncode == 1 and "--append" in result.stderr
    assert not result.stdout


def test_append_stdout_mode_remains_byte_compatible_without_a_package(tmp_path):
    result = cli(tmp_path, "task-card", "--id", "task-extra", "--boundary", "app.py", "--verify", "true")
    assert result.returncode == 0
    assert result.stdout == (
        "- [ ] task-extra\n  - id: task-extra\n  - boundary: app.py\n  - verify: `true`\n  - expect: green\n"
    )


def test_candidate_lint_is_static_and_does_not_write(tmp_path, monkeypatch):
    directory = _planning(tmp_path)
    before = _snapshot(directory)
    spec = (directory / "spec.md").read_text().replace("V4 finish: commit", "V4 finish: invalid")
    tasks = (directory / "tasks.md").read_text()
    monkeypatch.setattr(plan, "_verify", lambda *_: pytest.fail("static candidate lint ran a command"))
    result = plan.lint(tmp_path, SLUG, static=True, contents=(spec, tasks))
    assert any(gap["rule"] == "PLAN-011" for gap in result["gaps"])
    assert _snapshot(directory) == before
    assert plan.lint(tmp_path, SLUG, static=True)["ready"] is True


@pytest.mark.parametrize("static,record", [(False, False), (True, True), (False, True)])
def test_candidate_lint_disallows_execution_and_recording(tmp_path, static, record):
    directory = _planning(tmp_path)
    before = _snapshot(directory)
    with pytest.raises(ValueError, match="static"):
        plan.lint(tmp_path, SLUG, static=static, record=record, contents=("", ""))
    assert _snapshot(directory) == before


def test_append_preserves_permissions_and_crlf_prefix(tmp_path):
    directory = _planning(tmp_path)
    for name in ("spec.md", "tasks.md"):
        path = directory / name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        path.chmod(0o640)
    before = (directory / "tasks.md").read_bytes()
    result = _append(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (directory / "tasks.md").read_bytes().startswith(before)
    assert stat.S_IMODE((directory / "tasks.md").stat().st_mode) == 0o640
    assert stat.S_IMODE((directory / "spec.md").stat().st_mode) == 0o640


@pytest.mark.parametrize("failure", ["prepare", "replace"])
def test_append_io_failure_restores_original_bytes_and_modes(tmp_path, monkeypatch, capsys, failure):
    directory = _planning(tmp_path)
    (directory / "spec.md").chmod(0o640)
    before = _snapshot(directory)
    if failure == "prepare":
        real_write = Path.write_bytes

        def fail_write(path, data):
            if path.name == "tasks.md.new":
                raise OSError("injected candidate write failure")
            return real_write(path, data)

        monkeypatch.setattr(Path, "write_bytes", fail_write)
    else:
        real_replace = os.replace

        def fail_replace(source, destination):
            if Path(destination) == directory / "tasks.md":
                raise OSError("injected second replace failure")
            return real_replace(source, destination)

        monkeypatch.setattr(os, "replace", fail_replace)
    result = spec_cli.main(["--root", str(tmp_path), *_args()])
    captured = capsys.readouterr()
    assert result == 1 and "injected" in captured.err
    assert not captured.out
    assert _snapshot(directory) == before
    assert not list(directory.glob(".append-*"))


def test_append_rollback_failure_preserves_recovery_copy(tmp_path, monkeypatch, capsys):
    directory = _planning(tmp_path)
    original_spec = (directory / "spec.md").read_bytes()
    original_tasks = (directory / "tasks.md").read_bytes()
    real_replace = os.replace

    def fail_replace(source, destination):
        if Path(destination) == directory / "tasks.md" or Path(source).name.endswith(".old"):
            raise OSError("injected replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_replace)
    assert spec_cli.main(["--root", str(tmp_path), *_args()]) == 1
    captured = capsys.readouterr()
    assert "rollback" in captured.err and "backup" in captured.err
    backups = list(directory.glob(".append-*/spec.md.old"))
    assert len(backups) == 1 and backups[0].read_bytes() == original_spec
    assert (directory / "tasks.md").read_bytes() == original_tasks


def test_append_detects_concurrent_text_change(tmp_path, monkeypatch, capsys):
    directory = _planning(tmp_path)
    spec_before = (directory / "spec.md").read_bytes()
    tasks = directory / "tasks.md"
    modified = tasks.read_bytes() + b"\n# Concurrent user edit\n"
    real_lint = plan.lint

    def edit_during_lint(*args, **kwargs):
        result = real_lint(*args, **kwargs)
        tasks.write_bytes(modified)
        return result

    monkeypatch.setattr(plan, "lint", edit_during_lint)
    assert spec_cli.main(["--root", str(tmp_path), *_args()]) == 1
    assert "changed" in capsys.readouterr().err
    assert (directory / "spec.md").read_bytes() == spec_before
    assert tasks.read_bytes() == modified


def test_append_generated_verify_backticks_do_not_truncate(tmp_path):
    directory = _planning(tmp_path)
    before = _snapshot(directory)
    result = cli(
        tmp_path,
        "task-card",
        "--id",
        "task-extra",
        "--boundary",
        "app.py",
        "--tdd",
        "--test-boundary",
        "tests/test_ex`ample`.py",
        "--append",
        "--criterion",
        "Covered behavior",
    )
    assert result.returncode == 1 and "verify" in result.stderr
    assert _snapshot(directory) == before


def test_append_cleanup_failure_reports_committed_success(tmp_path, monkeypatch, capsys):
    directory = _planning(tmp_path)
    real_cleanup = spec_cli.shutil.rmtree

    def fail_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".append-"):
            raise OSError("injected cleanup failure")
        return real_cleanup(path, *args, **kwargs)

    monkeypatch.setattr(spec_cli.shutil, "rmtree", fail_cleanup)
    assert spec_cli.main(["--root", str(tmp_path), *_args()]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["appended"] == ["task-extra"]
    assert result["ready"] is True
    assert len(result["warnings"]) == 1
    assert "cleanup" in result["warnings"][0] and ".append-" in result["warnings"][0]
    assert package.parse_tasks((directory / "tasks.md").read_text())[-1].id == "task-extra"
    assert package.criteria((directory / "spec.md").read_text())[-1][2] == "task-extra"


def test_append_cleanup_failure_preserves_original_error(tmp_path, monkeypatch, capsys):
    directory = _planning(tmp_path)
    original = {name: (directory / name).read_bytes() for name in ("spec.md", "tasks.md")}
    real_replace = os.replace
    real_cleanup = spec_cli.shutil.rmtree

    def fail_replace(source, destination):
        if Path(destination) == directory / "tasks.md":
            raise OSError("injected publish failure")
        return real_replace(source, destination)

    def fail_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".append-"):
            raise OSError("injected cleanup failure")
        return real_cleanup(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_replace)
    monkeypatch.setattr(spec_cli.shutil, "rmtree", fail_cleanup)
    assert spec_cli.main(["--root", str(tmp_path), *_args()]) == 1
    result = capsys.readouterr()
    assert "injected publish failure" in result.err and "cleanup" in result.err
    assert not result.out
    assert all((directory / name).read_bytes() == data for name, data in original.items())


def test_append_docs_explain_static_and_baseline_semantics():
    text = (ROOT / "references/plan.md").read_text()
    for phrase in ("--append", "--criterion", "static", "baseline", "plan-amend"):
        assert phrase in text

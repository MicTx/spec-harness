import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import complete_spec_package as complete_module
from complete_spec_package import (
    archive_validated_package,
    build_summary,
    bullet_lines,
    git_record_labels,
    has_any_real_value,
)
from spec_package_support import count_unfinished_tasks

SPEC = """\
# Gate - 项目范围

## 1. 问题定义
- **项目目标**：验证全仓门禁
- **目标用户**：维护者
- **核心价值**：状态可信

## 2. 假设与待确认
### 2.1 已确认事实
- 已确认
### 2.2 关键假设
- 使用标准库
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不改业务代码
## 4. 最小实现路径
- 复用 checker
- 不复制规则
- 返回退出码
### 6.1 行为成效指标
- 无关改动数 <= 0 -> verify: diff
- 澄清前置率 >= 100% -> verify: spec
- 返工次数 <= 1 -> verify: notes
"""
TASKS_DONE = "- [x] Gate\n  - boundary: scripts only\n  - verify: pytest\n"
CHECKLIST_DONE = """\
## 跨载体一致性
- [x] Consistent
## 行为成效
- [x] Metrics
## 验收证据
- 脚本验证：pytest
**验收结果**：通过
"""
SUMMARY_V1 = """# Archived - 完成总结
## 交付结论
- 完成
## 假设回顾
- verified
## 交付范围
- delivered
## 简化决策
- simple
## 变更边界
- scripts
## 验证证据
- pytest
## 门禁证据
- passed
## 问题处置
```json
{"version": 1, "issues": []}
```
"""


# -- git_record_labels --


def test_git_labels_zh():
    labels = git_record_labels("zh")
    assert labels["section"] == "Git 记录"
    assert "提交" in labels["commit"]


def test_git_labels_en():
    labels = git_record_labels("en")
    assert labels["section"] == "Git Records"
    assert "Commit" in labels["commit"]


# -- has_any_real_value --


def test_has_real_value():
    assert has_any_real_value(["actual value", ""])
    assert not has_any_real_value([])
    assert not has_any_real_value(["待补充"])


# -- bullet_lines --


def test_bullet_lines_with_values():
    assert "- a\n- b" == bullet_lines(["a", "b"], "default")


def test_bullet_lines_empty_fallback():
    assert "- default" == bullet_lines([], "default")


# -- completion task gate --


def test_complete_allow_incomplete_still_requires_development_record_slug():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        package_dir = root / ".spec" / "specs" / "test-pkg"
        package_dir.mkdir(parents=True)
        (package_dir / "spec.md").write_text("# Test - 项目范围\n", encoding="utf-8")
        (package_dir / "tasks.md").write_text("- [ ] Task\n", encoding="utf-8")
        (package_dir / "checklist.md").write_text("**验收结果**：待修复\n", encoding="utf-8")
        script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--root",
                str(root),
                "--slug",
                "test-pkg",
                "--allow-incomplete",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "YYYY-MM-DD_slug" in result.stderr


def test_count_unfinished_tasks_blocks_pending_in_progress_and_blocked_states():
    tasks = """\
- [x] Done
- [ ] Pending
- [>] In progress
- [!] Blocked
"""
    assert count_unfinished_tasks(tasks) == 3


def test_complete_script_blocks_when_overall_check_gate_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        package_dir = root / ".spec" / "specs" / "test-pkg"
        package_dir.mkdir(parents=True)
        (package_dir / "spec.md").write_text(
            """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：目标
- **目标用户**：用户
- **核心价值**：价值

## 2. 假设与待确认
### 2.1 已确认事实
- 事实
### 2.2 关键假设
- 假设
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不做什么
## 4. 最小实现路径
- 路径1
- 路径2
- 路径3
### 6.1 行为成效指标
- 无关改动数 <= 0 -> verify: diff
- 澄清前置率 >= 100% -> verify: spec
- 返工次数 <= 0 -> verify: notes
""",
            encoding="utf-8",
        )
        (package_dir / "tasks.md").write_text(
            "- [x] Task A\n  - boundary: x\n  - verify: x\n",
            encoding="utf-8",
        )
        (package_dir / "checklist.md").write_text(
            "# C\n- [x] Item\n**验收结果**：通过\n",
            encoding="utf-8",
        )
        script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
        result = subprocess.run(
            [sys.executable, str(script), "--root", str(root), "--slug", "test-pkg"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "YYYY-MM-DD_slug" in result.stderr


def test_check_route_and_complete_share_acceptance_gate():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        slug = "2026-06-12_test-pkg"
        package_dir = root / ".spec" / "specs" / slug
        package_dir.mkdir(parents=True)
        (package_dir / "spec.md").write_text(
            """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：目标
- **目标用户**：用户
- **核心价值**：价值

## 2. 假设与待确认
### 2.1 已确认事实
- 事实
### 2.2 关键假设
- 假设
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不做什么
## 4. 最小实现路径
- 路径1
- 路径2
- 路径3
### 6.1 行为成效指标
- 无关改动数 <= 0 -> verify: diff
- 澄清前置率 >= 100% -> verify: spec
- 返工次数 <= 0 -> verify: notes
""",
            encoding="utf-8",
        )
        (package_dir / "tasks.md").write_text(
            "- [x] Task A\n  - boundary: x\n  - verify: x\n",
            encoding="utf-8",
        )
        (package_dir / "checklist.md").write_text(
            """\
# C
- [x] Item

## 跨载体一致性
- [x] Consistent

## 行为成效
- [x] Metrics filled

## 验收证据
- 脚本验证：pytest passed

**验收结果**：通过
""",
            encoding="utf-8",
        )
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        check_result = subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "check_spec_package.py"),
                "--root",
                str(root),
                "--slug",
                slug,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        route_result = subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "route_spec_package.py"),
                "--root",
                str(root),
                "--slug",
                slug,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        complete_result = subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "complete_spec_package.py"),
                "--root",
                str(root),
                "--slug",
                slug,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert check_result.returncode == 0
        assert "验收结果：通过" in check_result.stdout
        assert route_result.returncode == 0
        assert "- 已完成：1/1" in route_result.stdout
        assert "整理交付结果并完成归档" in route_result.stdout
        assert "/spec:" not in route_result.stdout
        assert complete_result.returncode == 0
        assert (package_dir / "completion-summary.md").exists()


# -- build_summary --


def test_build_summary_contains_all_sections():
    summary = build_summary(
        title="Test Package",
        completed_at="2026-05-02 10:00",
        result="完成",
        verified_assumptions=["假设A"],
        open_risks=[],
        delivered=["功能A"],
        not_delivered=[],
        deviations=[],
        simplifications=["选择X"],
        out_of_scope=["不做Y"],
        touched_areas=["scripts/"],
        untouched_areas=["docs/"],
        build_evidence=["py_compile 通过"],
        test_evidence=["pytest 通过"],
        manual_evidence=[],
        effect_metrics=["无关改动 0"],
        consistency_evidence=["无冲突"],
        commit_ref="abc123",
        push_ref="main",
        git_record_language="zh",
        knowledge_docs=[],
        follow_ups=["后续A"],
        rejected_extensions=["未引入Z"],
        gate_evidences=["checklist 全绿"],
    )
    assert "# Test Package - 完成总结" in summary
    assert "## 交付结论" in summary
    assert "## 假设回顾" in summary
    assert "## 交付范围" in summary
    assert "## 简化决策" in summary
    assert "## 变更边界" in summary
    assert "## 验证证据" in summary
    assert "## Git 记录" in summary
    assert "## 被拒绝的扩展提议" in summary
    assert "## 门禁证据" in summary
    assert "## 遗留事项" in summary
    assert "功能A" in summary
    assert "后续A" in summary
    assert "未引入Z" in summary
    assert "checklist 全绿" in summary


def test_follow_up_help_requires_structured_archive_disposition():
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    help_text = " ".join(result.stdout.split()).replace("- ", "-")
    assert "Closed archives reject this free-text channel" in help_text
    assert "use --issue-disposition with a typed JSON object" in help_text
    assert "Executable follow-up work must target an already archived Development Record" in help_text


def test_build_summary_defaults():
    summary = build_summary(
        title="Minimal",
        completed_at="2026-05-02",
        result="完成",
        verified_assumptions=[],
        open_risks=[],
        delivered=[],
        not_delivered=[],
        deviations=[],
        simplifications=[],
        out_of_scope=[],
        touched_areas=[],
        untouched_areas=[],
        build_evidence=[],
        test_evidence=[],
        manual_evidence=[],
        effect_metrics=[],
        consistency_evidence=[],
        commit_ref="待补充",
        push_ref="待补充",
        git_record_language="zh",
        knowledge_docs=[],
        follow_ups=[],
        rejected_extensions=[],
        gate_evidences=[],
    )
    assert "待补充" in summary


def _write_complete_package(root: Path, slug: str) -> Path:
    package_dir = root / ".spec" / "specs" / slug
    package_dir.mkdir(parents=True)
    (package_dir / "spec.md").write_text(_COMPLETE_SPEC_ZH, encoding="utf-8")
    (package_dir / "tasks.md").write_text(_COMPLETE_TASKS, encoding="utf-8")
    (package_dir / "checklist.md").write_text(_COMPLETE_CHECKLIST, encoding="utf-8")
    return package_dir


def test_complete_rejects_allow_incomplete_archive(tmp_path):
    package = tmp_path / ".spec" / "specs" / "2026-07-13_fix-incomplete"
    package.mkdir(parents=True)
    (package / "spec.md").write_text("# Incomplete\n", encoding="utf-8")
    (package / "tasks.md").write_text("- [ ] Task\n", encoding="utf-8")
    (package / "checklist.md").write_text("**验收结果**：待修复\n", encoding="utf-8")
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(tmp_path),
            "--slug",
            package.name,
            "--allow-incomplete",
            "--archive",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "cannot be combined with --archive" in result.stderr
    assert package.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_complete_rejects_fifo_required_file_without_archiving(tmp_path):
    package = _write_complete_package(tmp_path, "2026-07-13_fix-fifo")
    (package / "spec.md").unlink()
    os.mkfifo(package / "spec.md")
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"

    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--slug", package.name, "--archive"],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode != 0
    assert "not a regular file" in result.stderr
    assert package.exists()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
def test_complete_rejects_symlink_required_files(tmp_path):
    source = _write_complete_package(tmp_path, "2026-07-13_fix-source")
    package = tmp_path / ".spec" / "specs" / "2026-07-13_fix-links"
    package.mkdir()
    for name in ("spec.md", "tasks.md", "checklist.md"):
        (package / name).symlink_to(source / name)
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"

    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--slug", package.name, "--archive"],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert package.exists()
    assert not (tmp_path / ".spec" / "specs" / "archive" / package.name).exists()


def test_complete_force_replaces_existing_source_summary(tmp_path):
    package = _write_complete_package(tmp_path, "2026-07-13_fix-force-summary")
    (package / "completion-summary.md").write_text("old summary\n", encoding="utf-8")
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(tmp_path),
            "--slug",
            package.name,
            "--archive",
            "--force",
        ],
        capture_output=True,
        text=True,
    )

    summary = tmp_path / ".spec" / "specs" / "archive" / package.name / "completion-summary.md"
    assert result.returncode == 0
    assert summary.is_file()
    assert summary.read_text(encoding="utf-8") != "old summary\n"


def test_force_archive_backup_cleanup_failure_retains_recoverable_backup(tmp_path, monkeypatch, capsys):
    package = _write_complete_package(tmp_path, "2026-07-13_fix-force-backup-cleanup")
    destination = tmp_path / ".spec" / "specs" / "archive" / package.name
    destination.mkdir(parents=True)
    (destination / "sentinel.txt").write_text("old archive\n", encoding="utf-8")
    snapshots = {name: (package / name).read_text(encoding="utf-8") for name in ("spec.md", "tasks.md", "checklist.md")}
    real_remove = complete_module.remove_path

    def fail_backup(path):
        if ".backup-" in path.name:
            raise PermissionError("simulated backup cleanup failure")
        return real_remove(path)

    monkeypatch.setattr(complete_module, "remove_path", fail_backup)
    archive_validated_package(package, destination, "new summary\n", snapshots, force=True)

    backups = list(destination.parent.glob(f".{destination.name}.backup-*"))
    assert destination.is_dir()
    assert (destination / "completion-summary.md").read_text(encoding="utf-8") == "new summary\n"
    assert not package.exists()
    assert len(backups) == 1
    assert (backups[0] / "sentinel.txt").read_text(encoding="utf-8") == "old archive\n"
    assert "backup cleanup failed" in capsys.readouterr().err


def test_archive_cleanup_failure_does_not_restore_active_package(tmp_path, monkeypatch, capsys):
    package = _write_complete_package(tmp_path, "2026-07-13_fix-cleanup")
    destination = tmp_path / ".spec" / "specs" / "archive" / package.name
    destination.parent.mkdir(parents=True)
    snapshots = {name: (package / name).read_text(encoding="utf-8") for name in ("spec.md", "tasks.md", "checklist.md")}
    real_remove = complete_module.remove_path

    def fail_tombstone(path):
        if ".archiving-" in path.name:
            raise PermissionError("simulated cleanup failure")
        return real_remove(path)

    monkeypatch.setattr(complete_module, "remove_path", fail_tombstone)

    archive_validated_package(package, destination, "summary\n", snapshots, force=False)

    captured = capsys.readouterr()
    assert destination.is_dir()
    assert not package.exists()
    assert "tombstone cleanup failed" in captured.err
    assert any(".archiving-" in child.name for child in destination.parent.iterdir())


def test_archive_cleanup_residue_is_recoverable_on_retry(tmp_path, monkeypatch):
    package = _write_complete_package(tmp_path, "2026-07-13_fix-cleanup-retry")
    destination = tmp_path / ".spec" / "specs" / "archive" / package.name
    destination.parent.mkdir(parents=True)
    snapshots = {name: (package / name).read_text(encoding="utf-8") for name in ("spec.md", "tasks.md", "checklist.md")}
    real_remove = complete_module.remove_path

    def fail_once(path):
        if ".archiving-" in path.name:
            monkeypatch.setattr(complete_module, "remove_path", real_remove)
            raise PermissionError("simulated one-shot cleanup failure")
        return real_remove(path)

    monkeypatch.setattr(complete_module, "remove_path", fail_once)
    archive_validated_package(package, destination, "summary\n", snapshots, force=False)
    residue = [child for child in destination.parent.iterdir() if ".archiving-" in child.name]
    assert len(residue) == 1
    archive_validated_package(package, destination, "summary\n", snapshots, force=False)
    assert destination.is_dir()
    assert not residue[0].exists()


def test_complete_archive_copies_independent_regular_evidence(tmp_path):
    package = _write_complete_package(tmp_path, "2026-07-13_fix-safe-archive")
    (package / "evidence.txt").write_text("proof\n", encoding="utf-8")
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"

    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--slug", package.name, "--archive"],
        capture_output=True,
        text=True,
    )

    archive = tmp_path / ".spec" / "specs" / "archive" / package.name
    assert result.returncode == 0
    assert not package.exists()
    assert (archive / "completion-summary.md").is_file()
    assert (archive / "evidence.txt").read_text(encoding="utf-8") == "proof\n"
    assert all(not path.is_symlink() for path in archive.rglob("*"))


def test_complete_archive_rejects_free_text_follow_up(tmp_path):
    package = _write_complete_package(tmp_path, "2026-07-13_fix-free-text")
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(tmp_path),
            "--slug",
            package.name,
            "--archive",
            "--follow-up",
            "fix parser later",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "reject free-text unresolved fields" in result.stderr
    assert package.is_dir()


def test_complete_archive_accepts_typed_issue_disposition(tmp_path):
    package = _write_complete_package(tmp_path, "2026-07-13_fix-typed-risk")
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
    issue = (
        '{"id":"risk-vendor","summary":"vendor API compatibility",'
        '"actionable":true,"disposition":"accepted_risk",'
        '"owner":"maintainer","rationale":"legacy client support",'
        '"reviewTrigger":"next major release"}'
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(tmp_path),
            "--slug",
            package.name,
            "--archive",
            "--issue-disposition",
            issue,
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = (tmp_path / ".spec" / "specs" / "archive" / package.name / "completion-summary.md").read_text(
        encoding="utf-8"
    )
    assert '"disposition": "accepted_risk"' in summary
    assert "待补充" not in summary


# -- --git-record-language auto --


_COMPLETE_SPEC_EN = """\
# Billing System - 项目范围

## 1. 问题定义
- **项目目标**：goal
- **目标用户**：user
- **核心价值**：value

## 2. 假设与待确认
### 2.1 已确认事实
- fact
### 2.2 关键假设
- assumption
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- nothing
## 4. 最小实现路径
- path1
- path2
- path3
### 6.1 行为成效指标
- 无关改动数 <= 0 -> verify: diff
- 澄清前置率 >= 100% -> verify: spec
- 返工次数 <= 0 -> verify: notes
"""


_COMPLETE_SPEC_ZH = _COMPLETE_SPEC_EN.replace("# Billing System - 项目范围", "# 计费系统 - 项目范围")


_COMPLETE_CHECKLIST = """\
# C
- [x] Item

## 跨载体一致性
- [x] Consistent

## 行为成效
- [x] Metrics filled

## 验收证据
- 脚本验证：pytest passed

**验收结果**：通过
"""

_COMPLETE_TASKS = "- [x] Task A\n  - boundary: x\n  - verify: x\n"


def _run_complete_with_auto(spec_content: str) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        slug = "2026-06-12_auto-lang-test"
        package_dir = root / ".spec" / "specs" / slug
        package_dir.mkdir(parents=True)
        (package_dir / "spec.md").write_text(spec_content, encoding="utf-8")
        (package_dir / "tasks.md").write_text(_COMPLETE_TASKS, encoding="utf-8")
        (package_dir / "checklist.md").write_text(_COMPLETE_CHECKLIST, encoding="utf-8")
        script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
        subprocess.run(
            [sys.executable, str(script), "--root", str(root), "--slug", slug],
            check=True,
            capture_output=True,
            text=True,
        )
        return (package_dir / "completion-summary.md").read_text(encoding="utf-8")


def test_git_record_language_auto_detects_english_title():
    summary = _run_complete_with_auto(_COMPLETE_SPEC_EN)
    assert "## Git Records" in summary
    assert "## Git 记录" not in summary


def test_git_record_language_auto_detects_chinese_title():
    summary = _run_complete_with_auto(_COMPLETE_SPEC_ZH)
    assert "## Git 记录" in summary


@pytest.mark.parametrize("checkpoint_state", ["active", "corrupted"])
def test_complete_archive_rejects_interrupted_update_checkpoint(tmp_path, checkpoint_state):
    from update_checkpoint_support import begin_update_checkpoint, checkpoint_path

    slug = "2026-09-22_fix-checkpoint-blocker-gates"
    root = tmp_path
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    spec = SPEC + "\n- Git integration branch：`spec/2026-09-22_fix-checkpoint-blocker-gates`\n"
    (package / "spec.md").write_text(spec, encoding="utf-8")
    (package / "tasks.md").write_text(TASKS_DONE, encoding="utf-8")
    (package / "checklist.md").write_text(CHECKLIST_DONE, encoding="utf-8")
    begin_update_checkpoint(package, "封堵归档绕过")
    if checkpoint_state == "corrupted":
        checkpoint_path(package).write_text("{ bad json", encoding="utf-8")
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(root), "--slug", slug, "--archive", "--force"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "update checkpoint" in result.stderr
    assert not (root / ".spec" / "specs" / "archive" / slug).exists()


def test_complete_archive_rejects_standalone_blocked_status_line(tmp_path):
    slug = "2026-09-22_fix-checkpoint-blocker-gates"
    package = tmp_path / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(SPEC, encoding="utf-8")
    (package / "tasks.md").write_text(TASKS_DONE + "\n阻塞：等待外部系统\n", encoding="utf-8")
    (package / "checklist.md").write_text(CHECKLIST_DONE, encoding="utf-8")
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--slug", slug, "--archive", "--force"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / ".spec" / "specs" / "archive" / slug).exists()


# -- --process-metric: optional ## 过程指标 section --


def _run_complete_summary(root: Path, slug: str, *extra_args: str) -> str:
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(root), "--slug", slug, *extra_args],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return (root / ".spec" / "specs" / slug / "completion-summary.md").read_text(encoding="utf-8")


def test_process_metric_flag_emits_process_metrics_section(tmp_path):
    slug = "2026-09-25_add-process-metric-present"
    _write_complete_package(tmp_path, slug)
    summary = _run_complete_summary(
        tmp_path,
        slug,
        "--process-metric",
        "check 轮数：2",
        "--process-metric",
        "返工次数：1",
    )
    assert "## 过程指标" in summary
    assert "- check 轮数：2" in summary
    assert "- 返工次数：1" in summary


def test_process_metric_absent_by_default(tmp_path):
    slug = "2026-09-25_add-process-metric-absent"
    _write_complete_package(tmp_path, slug)
    summary = _run_complete_summary(tmp_path, slug)
    assert "## 过程指标" not in summary


def test_complete_archive_with_process_metric_keeps_optional_section_and_v1_closure(tmp_path):
    slug = "2026-09-25_add-process-metric-archive"
    _write_complete_package(tmp_path, slug)
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(tmp_path),
            "--slug",
            slug,
            "--archive",
            "--process-metric",
            "check 轮数：3",
            "--process-metric",
            "转人工标记：无",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    summary = (tmp_path / ".spec" / "specs" / "archive" / slug / "completion-summary.md").read_text(encoding="utf-8")
    assert "## 过程指标" in summary
    assert '"version": 1' in summary


def test_complete_archive_without_process_metric_has_no_section(tmp_path):
    slug = "2026-09-25_add-process-metric-archive-off"
    _write_complete_package(tmp_path, slug)
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--slug", slug, "--archive"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    summary = (tmp_path / ".spec" / "specs" / "archive" / slug / "completion-summary.md").read_text(encoding="utf-8")
    assert "## 过程指标" not in summary


def test_build_summary_process_metrics_default_backward_compatible():
    summary = build_summary(
        title="Minimal",
        completed_at="2026-05-02",
        result="完成",
        verified_assumptions=[],
        open_risks=[],
        delivered=[],
        not_delivered=[],
        deviations=[],
        simplifications=[],
        out_of_scope=[],
        touched_areas=[],
        untouched_areas=[],
        build_evidence=[],
        test_evidence=[],
        manual_evidence=[],
        effect_metrics=[],
        consistency_evidence=[],
        commit_ref="待补充",
        push_ref="待补充",
        git_record_language="zh",
        knowledge_docs=[],
        follow_ups=[],
        rejected_extensions=[],
        gate_evidences=[],
    )
    assert "## 过程指标" not in summary
    assert summary.index("## 门禁证据") < summary.index("## 遗留事项")
    assert summary.index("## 遗留事项") < summary.index("## 问题处置")


def test_build_summary_process_metrics_place_between_gate_evidence_and_followups():
    summary = build_summary(
        title="Placed",
        completed_at="2026-05-02",
        result="完成",
        verified_assumptions=[],
        open_risks=[],
        delivered=[],
        not_delivered=[],
        deviations=[],
        simplifications=[],
        out_of_scope=[],
        touched_areas=[],
        untouched_areas=[],
        build_evidence=[],
        test_evidence=[],
        manual_evidence=[],
        effect_metrics=[],
        consistency_evidence=[],
        commit_ref="abc123",
        push_ref="pending",
        git_record_language="zh",
        knowledge_docs=[],
        follow_ups=[],
        rejected_extensions=[],
        gate_evidences=["check exit 0"],
        process_metrics=["check 轮数：4", "转人工标记：无"],
    )
    assert summary.index("## 门禁证据") < summary.index("## 过程指标")
    assert summary.index("## 过程指标") < summary.index("## 遗留事项")
    assert "- check 轮数：4" in summary
    assert "- 转人工标记：无" in summary

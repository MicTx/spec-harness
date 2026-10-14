import os
import re
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
        evidence_notes=["构建：py_compile 通过", "测试：pytest 通过"],
        effect_metrics=["无关改动 0"],
        consistency_evidence=["无冲突"],
        commit_ref="abc123",
        push_ref="main",
        git_record_language="zh",
        agents_proposals=["+ AGENTS.md 新增：门禁摘要必须携带 runs.jsonl 锚点"],
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
    assert "## AGENTS.md 增量提议" in summary
    assert "```diff" in summary
    assert "+ AGENTS.md 新增：门禁摘要必须携带 runs.jsonl 锚点" in summary
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
        evidence_notes=[],
        effect_metrics=[],
        consistency_evidence=[],
        commit_ref="待补充",
        push_ref="待补充",
        git_record_language="zh",
        agents_proposals=[],
        follow_ups=[],
        rejected_extensions=[],
        gate_evidences=[],
    )
    assert "待补充" in summary


# -- AGENTS.md 增量提议（F15/AUD-13） --


def test_build_summary_agents_proposal_renders_single_diff_fence():
    summary = build_summary(
        title="Proposal",
        completed_at="2026-10-13 10:00",
        result="完成",
        verified_assumptions=[],
        open_risks=[],
        delivered=["功能A"],
        not_delivered=[],
        deviations=[],
        simplifications=[],
        out_of_scope=[],
        touched_areas=[],
        untouched_areas=[],
        evidence_notes=[],
        effect_metrics=[],
        consistency_evidence=[],
        commit_ref="abc123",
        push_ref="main",
        git_record_language="zh",
        agents_proposals=[
            "+ ## 工程事实",
            "+ - complete_spec_package 的蒸馏出口是 --agents-proposal，不扫描 .spec/docs/",
            "  ",
        ],
        follow_ups=[],
        rejected_extensions=[],
        gate_evidences=[],
    )
    section = summary.split("## AGENTS.md 增量提议", 1)[1].split("\n## ", 1)[0]
    assert section.count("```") == 2  # opening diff fence + closing fence only
    assert "+ ## 工程事实" in section
    assert "+ - complete_spec_package 的蒸馏出口是 --agents-proposal，不扫描 .spec/docs/" in section
    # blank-only flag values never leak stray lines into the fence
    assert "  \n" not in section


def test_build_summary_agents_proposal_default_records_none():
    summary = build_summary(
        title="NoProposal",
        completed_at="2026-10-13 10:00",
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
        evidence_notes=[],
        effect_metrics=[],
        consistency_evidence=[],
        commit_ref="待补充",
        push_ref="待补充",
        git_record_language="zh",
        agents_proposals=[],
        follow_ups=[],
        rejected_extensions=[],
        gate_evidences=[],
    )
    section = summary.split("## AGENTS.md 增量提议", 1)[1].split("## ", 1)[0]
    assert section.strip() == "无"
    assert "```diff" not in section


def test_knowledge_doc_flag_retired():
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", ".", "--slug", "x", "--knowledge-doc", "a.md"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unrecognized arguments: --knowledge-doc" in result.stderr
    # the retired helper is gone from the module surface entirely
    assert not hasattr(complete_module, "detect_knowledge_docs")


def test_complete_archive_carries_agents_proposal_and_writes_no_knowledge_docs(tmp_path):
    """Sandbox archive regression (F15): the real main flow renders the
    session-authored proposal as a diff fence in the archived summary and
    never creates files under .spec/docs/."""
    package = _write_complete_package(tmp_path, "2026-10-13_fix-distill-proposal")
    docs_dir = tmp_path / ".spec" / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "2026-01-01_old-slug_legacy-note.md").write_text("legacy\n", encoding="utf-8")
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
            "--agents-proposal",
            "+ AGENTS.md 新增：done 蒸馏出口改为增量提议，不再产出独立知识文档",
            "--agents-proposal",
            "+ - 蒸馏出口旗标：--agents-proposal（可重复，每值一行）",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    archive = tmp_path / ".spec" / "specs" / "archive" / package.name
    summary = (archive / "completion-summary.md").read_text(encoding="utf-8")
    assert "## AGENTS.md 增量提议" in summary
    assert "```diff" in summary
    assert "+ AGENTS.md 新增：done 蒸馏出口改为增量提议，不再产出独立知识文档" in summary
    assert "+ - 蒸馏出口旗标：--agents-proposal（可重复，每值一行）" in summary
    # no knowledge documents are generated for this archive: only the
    # pre-existing legacy file remains in .spec/docs/
    assert [path.name for path in docs_dir.iterdir()] == ["2026-01-01_old-slug_legacy-note.md"]
    assert "## 知识沉淀" not in summary


def _write_complete_package(root: Path, slug: str) -> Path:
    package_dir = root / ".spec" / "specs" / slug
    package_dir.mkdir(parents=True)
    (package_dir / "spec.md").write_text(_COMPLETE_SPEC_ZH, encoding="utf-8")
    (package_dir / "tasks.md").write_text(_COMPLETE_TASKS, encoding="utf-8")
    (package_dir / "checklist.md").write_text(_COMPLETE_CHECKLIST, encoding="utf-8")
    return package_dir


def _write_scoped_complete_package(root: Path, slug: str, level: str = "package") -> Path:
    package_dir = _write_complete_package(root, slug)
    scope = f"""
### 5.1 验证策略
- 范围级别：{level}
- 变更对象：changed module
- 快速检查：pytest tests/test_changed.py
- 集成检查：适用外：无直接受影响链路
- 全项目检查：适用外：发布门禁
- 升级触发：共享基础设施或跨模块契约变化
"""
    (package_dir / "spec.md").write_text(_COMPLETE_SPEC_ZH + scope, encoding="utf-8")
    checklist = (package_dir / "checklist.md").read_text(encoding="utf-8")
    checklist = checklist.replace("## 验收证据\n", f"## 验收证据\n- 验证范围：{level}\n", 1)
    (package_dir / "checklist.md").write_text(checklist, encoding="utf-8")
    return package_dir


def test_complete_cli_rejects_malformed_verification_scope(tmp_path):
    package = _write_scoped_complete_package(tmp_path, "2026-10-03_fix-invalid-scope", "unknown")
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--slug", package.name, "--archive"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "spec package has not passed check gates" in result.stderr
    assert package.exists()


def test_complete_cli_summary_records_verification_scope(tmp_path):
    package = _write_scoped_complete_package(tmp_path, "2026-10-03_fix-valid-scope")
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--slug", package.name],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    summary = (package / "completion-summary.md").read_text(encoding="utf-8")
    assert "## 验证范围" in summary
    assert "- 级别：package" in summary


def test_complete_cli_rejects_mismatched_verification_scope_evidence(tmp_path):
    package = _write_scoped_complete_package(tmp_path, "2026-10-03_fix-mismatched-scope", "project")
    checklist = (package / "checklist.md").read_text(encoding="utf-8")
    (package / "checklist.md").write_text(checklist.replace("验证范围：project", "验证范围：package"), encoding="utf-8")
    script = Path(__file__).resolve().parent.parent / "scripts" / "complete_spec_package.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--slug", package.name, "--archive"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "spec package has not passed check gates" in result.stderr
    assert package.exists()


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
def test_complete_archive_rejects_interrupted_update_checkpoint(
    tmp_path, checkpoint_state, write_historical_checkpoint
):
    slug = "2026-09-22_fix-checkpoint-blocker-gates"
    root = tmp_path
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    spec = SPEC + "\n- Git integration branch：`spec/2026-09-22_fix-checkpoint-blocker-gates`\n"
    (package / "spec.md").write_text(spec, encoding="utf-8")
    (package / "tasks.md").write_text(TASKS_DONE, encoding="utf-8")
    (package / "checklist.md").write_text(CHECKLIST_DONE, encoding="utf-8")
    checkpoint_file = write_historical_checkpoint(package, "封堵归档绕过")
    if checkpoint_state == "corrupted":
        checkpoint_file.write_text("{ bad json", encoding="utf-8")
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
        evidence_notes=[],
        effect_metrics=[],
        consistency_evidence=[],
        commit_ref="待补充",
        push_ref="待补充",
        git_record_language="zh",
        agents_proposals=[],
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
        evidence_notes=[],
        effect_metrics=[],
        consistency_evidence=[],
        commit_ref="abc123",
        push_ref="pending",
        git_record_language="zh",
        agents_proposals=[],
        follow_ups=[],
        rejected_extensions=[],
        gate_evidences=["check exit 0"],
        process_metrics=["check 轮数：4", "转人工标记：无"],
    )
    assert summary.index("## 门禁证据") < summary.index("## 过程指标")
    assert summary.index("## 过程指标") < summary.index("## 遗留事项")
    assert "- check 轮数：4" in summary
    assert "- 转人工标记：无" in summary


def test_build_summary_records_verification_scope():
    summary = build_summary(
        title="Scoped",
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
        evidence_notes=["测试：pytest tests/test_changed.py"],
        effect_metrics=[],
        consistency_evidence=[],
        commit_ref="abc123",
        push_ref="pending",
        git_record_language="zh",
        agents_proposals=[],
        follow_ups=[],
        rejected_extensions=[],
        gate_evidences=["check exit 0"],
        verification_scope_level="package",
    )
    assert "## 验证范围" in summary
    assert "- 级别：package" in summary
    assert "不扩大" not in summary


# --------------------------------------------------------------------------- #
# executable-verify protocol at archive time (F7 / AUD-02, AUD-05)
# --------------------------------------------------------------------------- #


def _runs_sandbox(tmp_path: Path, slug: str) -> tuple[Path, Path]:
    """Git repo on the package branch with a complete `evidence: runs` package."""

    def sh(*args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(list(args), cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0, f"{args}\n{result.stdout}\n{result.stderr}"
        return result

    sh("git", "init", "-q", "-b", "main", str(tmp_path))
    sh("git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid")
    sh("git", "-C", str(tmp_path), "config", "user.name", "Fixture")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    sh("git", "-C", str(tmp_path), "add", ".")
    sh("git", "-C", str(tmp_path), "commit", "-qm", "initial")
    sh("git", "-C", str(tmp_path), "checkout", "-qb", f"spec/{slug}")
    package = tmp_path / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(
        SPEC + f"\n- Git integration branch：`spec/{slug}`\n\n本包启用 evidence: runs 协议。\n",
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [x] Machine one\n  - boundary: b1\n  - verify: `python3 -c \"print('one')\"` 全绿\n"
        "- [x] Machine two\n  - boundary: b2\n  - verify: `python3 -c \"print('two')\"` 全绿\n"
        "- [x] Manual\n  - boundary: b3\n  - verify: manual: 只读复核\n",
        encoding="utf-8",
    )
    (package / "checklist.md").write_text(CHECKLIST_DONE, encoding="utf-8")
    return tmp_path, package


def test_archive_writes_machine_verification_rate(tmp_path):
    slug = "2026-10-12_rate-in-summary"
    root, package = _runs_sandbox(tmp_path, slug)
    recorder = Path(complete_module.__file__).parent / "spec_verify.py"
    for task_id in ("task_0001", "task_0002"):
        result = subprocess.run(
            [sys.executable, str(recorder), "run", "--root", str(root), "--slug", slug, "--task", task_id],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run(
        [sys.executable, str(Path(complete_module.__file__)), "--root", str(root), "--slug", slug, "--archive"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = (tmp_path / ".spec/specs/archive" / slug / "completion-summary.md").read_text(encoding="utf-8")
    assert "- 机器验证率：2/2；manual 任务 1 项不计入分母" in summary
    # The recorded evidence itself archives with the package.
    assert (tmp_path / ".spec/specs/archive" / slug / "runs.jsonl").is_file()


def test_archive_refuses_stale_runs_fingerprint(tmp_path):
    slug = "2026-10-12_stale-runs-fingerprint"
    root, package = _runs_sandbox(tmp_path, slug)
    recorder = Path(complete_module.__file__).parent / "spec_verify.py"
    result = subprocess.run(
        [sys.executable, str(recorder), "run", "--root", str(root), "--slug", slug, "--task", "task_0001"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Sabotage the worktree after evidence capture: the archive must refuse.
    (root / "README.md").write_text("tampered after evidence\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(Path(complete_module.__file__)), "--root", str(root), "--slug", slug, "--archive"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "check gates" in result.stderr
    assert not (tmp_path / ".spec/specs/archive" / slug).exists()


def test_build_summary_omits_machine_rate_for_legacy_packages():
    summary = build_summary(
        title="Legacy",
        completed_at="2026-10-12",
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
        evidence_notes=["pytest"],
        effect_metrics=[],
        consistency_evidence=[],
        commit_ref="abc123",
        push_ref="pending",
        git_record_language="zh",
        agents_proposals=[],
        follow_ups=[],
        rejected_extensions=[],
        gate_evidences=["check exit 0"],
        verification_scope_level="package",
    )
    assert "机器验证率" not in summary


# -- F9 (AUD-06): archive commit-interval recording --


def _interval_sandbox(tmp_path: Path, slug: str, *, with_main: bool = True) -> Path:
    """Git repo on the package branch; main exists unless with_main=False."""

    def sh(*args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(list(args), cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0, f"{args}\n{result.stdout}\n{result.stderr}"
        return result

    if with_main:
        sh("git", "init", "-q", "-b", "main", str(tmp_path))
    else:
        # A default branch that is not `main`, so refs/heads/main stays absent.
        sh("git", "init", "-q", "-b", "trunk", str(tmp_path))
    sh("git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid")
    sh("git", "-C", str(tmp_path), "config", "user.name", "Fixture")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    sh("git", "-C", str(tmp_path), "add", ".")
    sh("git", "-C", str(tmp_path), "commit", "-qm", "initial")
    sh("git", "-C", str(tmp_path), "checkout", "-qb", f"spec/{slug}")
    package = tmp_path / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(SPEC + f"\n- Git integration branch：`spec/{slug}`\n", encoding="utf-8")
    (package / "tasks.md").write_text(TASKS_DONE, encoding="utf-8")
    (package / "checklist.md").write_text(CHECKLIST_DONE, encoding="utf-8")
    return tmp_path


def _archived_summary(tmp_path: Path, slug: str) -> str:
    return (tmp_path / ".spec" / "specs" / "archive" / slug / "completion-summary.md").read_text(encoding="utf-8")


def test_archive_records_resolvable_commit_interval(tmp_path):
    slug = "2026-10-12_record-archive-commit-range"
    root = _interval_sandbox(tmp_path, slug)
    result = subprocess.run(
        [sys.executable, str(Path(complete_module.__file__)), "--root", str(root), "--slug", slug, "--archive"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _archived_summary(tmp_path, slug)
    match = re.search(r"^- 提交区间：([0-9a-f]{40})\.\.([0-9a-f]{40})$", summary, re.MULTILINE)
    assert match is not None, summary
    base, head = match.groups()
    # Both endpoints resolve in Git and base is an ancestor of head.
    for sha in (base, head):
        resolved = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", f"{sha}^{{commit}}"],
            capture_output=True,
            text=True,
        )
        assert resolved.returncode == 0 and resolved.stdout.strip() == sha
    ancestry = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", base, head],
        capture_output=True,
    )
    assert ancestry.returncode == 0
    # base is exactly the merge-base off main: the interval covers the whole
    # integration-branch delivery window.
    merge_base = subprocess.run(
        ["git", "-C", str(root), "merge-base", "main", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert base == merge_base


def test_archive_records_out_of_scope_interval_for_non_git_root(tmp_path):
    slug = "2026-10-12_interval-non-git"
    package = tmp_path / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(SPEC, encoding="utf-8")
    (package / "tasks.md").write_text(TASKS_DONE, encoding="utf-8")
    (package / "checklist.md").write_text(CHECKLIST_DONE, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(Path(complete_module.__file__)), "--root", str(tmp_path), "--slug", slug, "--archive"],
        capture_output=True,
        text=True,
    )
    # The interval never blocks archival: a non-Git root records 适用外.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "- 提交区间：适用外（非 Git 工作区或无提交）" in _archived_summary(tmp_path, slug)


def test_archive_records_out_of_scope_interval_without_main_branch(tmp_path):
    slug = "2026-10-12_interval-no-main"
    root = _interval_sandbox(tmp_path, slug, with_main=False)
    result = subprocess.run(
        [sys.executable, str(Path(complete_module.__file__)), "--root", str(root), "--slug", slug, "--archive"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "- 提交区间：适用外（无法解析 refs/heads/main 的 merge-base）" in _archived_summary(tmp_path, slug)


def test_build_summary_omits_interval_for_legacy_callers():
    summary = build_summary(
        title="Legacy",
        completed_at="2026-10-12",
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
        evidence_notes=["pytest"],
        effect_metrics=[],
        consistency_evidence=[],
        commit_ref="abc123",
        push_ref="pending",
        git_record_language="zh",
        agents_proposals=[],
        follow_ups=[],
        rejected_extensions=[],
        gate_evidences=["check exit 0"],
        verification_scope_level="package",
    )
    assert "提交区间" not in summary

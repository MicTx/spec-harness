from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_packages.py"
SLUG = "2026-01-01_fix-example"
SPEC_V1 = """\
# Example - 项目范围

## 1. 问题定义
- **项目目标**：修复加法。

## 6. 成功标准与验证方式
- 旧标准：加法测试通过。

## 7. 风险与约束
- 无
"""
SPEC_V2 = """\
# Example - 项目范围

<!-- spec-format: 2 -->

## 6. 成功标准与验证方式
- [x] 加法测试通过 -> verify: task_0001
"""
TASKS_DONE = """\
# Example - 任务拆解

## 实现
- [x] 加法测试通过
  - boundary: 只改加法
  - verify: `python3 -m pytest -q`
"""
CHECKLIST_DONE = """\
# Example - 验收清单

## 功能完整性
- [x] 加法测试通过

## 验收证据
- 测试：`python3 -m pytest -q` -> exit 0

**验收结果**：通过
"""


def make_package(base: Path, spec: str, tasks: str = TASKS_DONE, checklist: str | None = CHECKLIST_DONE) -> Path:
    package = base / SLUG
    package.mkdir(parents=True)
    (package / "spec.md").write_text(spec, encoding="utf-8")
    (package / "tasks.md").write_text(tasks, encoding="utf-8")
    if checklist is not None:
        (package / "checklist.md").write_text(checklist, encoding="utf-8")
    return package


def run_migration(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        check=False,
        text=True,
        capture_output=True,
    )


def snapshot(package: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in package.iterdir() if path.is_file()}


def test_default_dry_run_shows_diff_without_writing(tmp_path: Path) -> None:
    package = make_package(tmp_path / ".spec" / "specs", SPEC_V1)
    before = snapshot(package)

    result = run_migration(tmp_path)

    assert result.returncode == 0, result.stderr
    assert snapshot(package) == before
    assert SLUG in result.stdout
    assert "spec-format: 2" in result.stdout
    assert "checklist.md" in result.stdout


def test_apply_migrates_v1_and_preserves_checklist_evidence(tmp_path: Path) -> None:
    package = make_package(tmp_path / ".spec" / "specs", SPEC_V1)
    tasks_before = (package / "tasks.md").read_bytes()

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    spec = (package / "spec.md").read_text(encoding="utf-8")
    assert "<!-- spec-format: 2 -->" in spec
    assert "- [x] 加法测试通过 -> verify: task_0001" in spec
    assert "旧标准：加法测试通过" in spec
    assert "python3 -m pytest -q" in spec
    assert "验收结果" in spec
    assert (package / "tasks.md").read_bytes() == tasks_before
    assert not (package / "checklist.md").exists()

    after = snapshot(package)
    again = run_migration(tmp_path, "--apply")
    assert again.returncode == 0, again.stderr
    assert snapshot(package) == after


def test_v1_without_checklist_preserves_existing_task_bound_criteria(tmp_path: Path) -> None:
    spec = SPEC_V2.replace("<!-- spec-format: 2 -->\n\n", "")
    package = make_package(tmp_path / ".spec" / "specs", spec, checklist=None)
    tasks_before = (package / "tasks.md").read_bytes()

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    migrated = (package / "spec.md").read_text(encoding="utf-8")
    assert "<!-- spec-format: 2 -->" in migrated
    assert "- [x] 加法测试通过 -> verify: task_0001" in migrated
    assert (package / "tasks.md").read_bytes() == tasks_before
    assert not (package / "checklist.md").exists()


def test_future_format_is_not_downgraded(tmp_path: Path) -> None:
    package = make_package(tmp_path / ".spec" / "specs", SPEC_V2.replace("spec-format: 2", "spec-format: 3"))
    before = snapshot(package)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    assert snapshot(package) == before


def test_v2_checklist_free_package_is_untouched(tmp_path: Path) -> None:
    package = make_package(tmp_path / ".spec" / "specs", SPEC_V2, checklist=None)
    before = snapshot(package)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    assert snapshot(package) == before


def test_v2_marker_with_legacy_checklist_is_migrated(tmp_path: Path) -> None:
    package = make_package(tmp_path / ".spec" / "specs", SPEC_V2, checklist=CHECKLIST_DONE)
    tasks_before = (package / "tasks.md").read_bytes()
    original_six = "## 6. 成功标准与验证方式\n- [x] 加法测试通过 -> verify: task_0001"

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    spec = (package / "spec.md").read_text(encoding="utf-8")
    assert original_six in spec
    assert spec.count("spec-format:") == 1
    assert "验收结果" in spec
    assert (package / "tasks.md").read_bytes() == tasks_before
    assert not (package / "checklist.md").exists()


def test_archive_is_never_touched(tmp_path: Path) -> None:
    archive = make_package(tmp_path / ".spec" / "specs" / "archive", SPEC_V1)
    before = snapshot(archive)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    assert snapshot(archive) == before


def test_active_spec_symlink_into_archive_is_never_modified(tmp_path: Path) -> None:
    specs = tmp_path / ".spec" / "specs"
    archive = make_package(specs / "archive", SPEC_V1)
    active = make_package(specs, SPEC_V1)
    spec_path = active / "spec.md"
    spec_path.unlink()
    spec_path.symlink_to(archive / "spec.md")
    before = snapshot(archive)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode != 0
    assert snapshot(archive) == before
    assert spec_path.is_symlink()
    assert (active / "checklist.md").is_file()


def test_active_spec_hardlink_into_archive_is_not_modified(tmp_path: Path) -> None:
    specs = tmp_path / ".spec" / "specs"
    archive = make_package(specs / "archive", SPEC_V1)
    active = make_package(specs, SPEC_V1)
    spec_path = active / "spec.md"
    spec_path.unlink()
    os.link(archive / "spec.md", spec_path)
    before = snapshot(archive)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode != 0
    assert snapshot(archive) == before
    assert (active / "checklist.md").is_file()


def test_checklist_symlink_is_not_imported_or_deleted(tmp_path: Path) -> None:
    specs = tmp_path / ".spec" / "specs"
    archive = make_package(specs / "archive", SPEC_V1)
    active = make_package(specs, SPEC_V1)
    checklist_path = active / "checklist.md"
    checklist_path.unlink()
    checklist_path.symlink_to(archive / "checklist.md")
    active_spec = (active / "spec.md").read_bytes()
    before = snapshot(archive)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode != 0
    assert (active / "spec.md").read_bytes() == active_spec
    assert checklist_path.is_symlink()
    assert snapshot(archive) == before


def test_override_uses_configured_specs_root(tmp_path: Path) -> None:
    default = make_package(tmp_path / ".spec" / "specs", SPEC_V1)
    override = make_package(tmp_path / "custom" / "specs", SPEC_V1)
    before = snapshot(default)

    result = run_migration(tmp_path, "--specs-dir", "custom", "--apply")

    assert result.returncode == 0, result.stderr
    assert snapshot(default) == before
    assert "<!-- spec-format: 2 -->" in (override / "spec.md").read_text(encoding="utf-8")
    assert not (override / "checklist.md").exists()


def test_existing_success_criteria_are_not_rewritten(tmp_path: Path) -> None:
    existing = "## 6. 成功标准与验证方式\n- [x] 加法测试通过 -> verify: task_0001\n\n"
    spec = SPEC_V1.replace("## 6. 成功标准与验证方式\n- 旧标准：加法测试通过。\n\n", existing)
    package = make_package(tmp_path / ".spec" / "specs", spec)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    assert existing in (package / "spec.md").read_text(encoding="utf-8")
    assert not (package / "checklist.md").exists()


def test_incomplete_checklist_never_becomes_a_checked_acceptance(tmp_path: Path) -> None:
    pending = CHECKLIST_DONE.replace("- [x]", "- [ ]").replace("验收结果**：通过", "验收结果**：待修复")
    package = make_package(tmp_path / ".spec" / "specs", SPEC_V1, checklist=pending)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    spec = (package / "spec.md").read_text(encoding="utf-8")
    assert "- [ ] 加法测试通过 -> verify: task_0001" in spec
    assert "- [x] 加法测试通过 -> verify: task_0001" not in spec
    assert not (package / "checklist.md").exists()


def test_checked_legacy_item_does_not_complete_an_open_task(tmp_path: Path) -> None:
    package = make_package(tmp_path / ".spec" / "specs", SPEC_V1, tasks=TASKS_DONE.replace("[x]", "[ ]"))

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    spec = (package / "spec.md").read_text(encoding="utf-8")
    assert "- [ ] 加法测试通过 -> verify: task_0001" in spec
    assert "- [x] 加法测试通过 -> verify: task_0001" not in spec


def test_unmappable_checklist_is_not_deleted(tmp_path: Path) -> None:
    checklist = CHECKLIST_DONE.replace("- [x] 加法测试通过", "- [x] 独立风险评审已完成")
    package = make_package(tmp_path / ".spec" / "specs", SPEC_V1, checklist=checklist)
    before = snapshot(package)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode != 0
    assert snapshot(package) == before
    assert SLUG in result.stdout or SLUG in result.stderr


def test_missing_tasks_are_not_replaced_by_a_fake_task_id(tmp_path: Path) -> None:
    package = make_package(tmp_path / ".spec" / "specs", SPEC_V1, tasks="# No tasks\n")
    before = snapshot(package)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode != 0
    assert snapshot(package) == before


def test_existing_v1_marker_is_replaced_not_duplicated(tmp_path: Path) -> None:
    package = make_package(tmp_path / ".spec" / "specs", "<!-- spec-format: 1 -->\n" + SPEC_V1)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    spec = (package / "spec.md").read_text(encoding="utf-8")
    assert spec.count("spec-format:") == 1
    assert "<!-- spec-format: 2 -->" in spec


def test_explicit_reference_must_not_hide_a_different_checklist_item(tmp_path: Path) -> None:
    checklist = CHECKLIST_DONE.replace("- [x] 加法测试通过", "- [x] 独立风险评审已完成 -> verify: task_0001")
    existing = "## 6. 成功标准与验证方式\n- [x] 加法测试通过 -> verify: task_0001\n\n"
    spec = SPEC_V1.replace("## 6. 成功标准与验证方式\n- 旧标准：加法测试通过。\n\n", existing)
    package = make_package(tmp_path / ".spec" / "specs", spec, checklist=checklist)
    before = snapshot(package)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode != 0
    assert snapshot(package) == before


def test_archived_root_override_is_rejected(tmp_path: Path) -> None:
    archived = make_package(tmp_path / ".spec" / "specs" / "archive" / "specs", SPEC_V1)
    before = snapshot(archived)

    result = run_migration(tmp_path, "--specs-dir", ".spec/specs/archive", "--apply")

    assert result.returncode != 0
    assert snapshot(archived) == before


def test_duplicate_verify_does_not_create_a_false_acceptance(tmp_path: Path) -> None:
    tasks = TASKS_DONE + "  - verify: `echo other`\n"
    package = make_package(tmp_path / ".spec" / "specs", SPEC_V1, tasks=tasks)
    before = snapshot(package)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode != 0
    assert snapshot(package) == before


def test_unknown_checklist_item_reference_is_not_removed(tmp_path: Path) -> None:
    checklist = CHECKLIST_DONE.replace("- [x] 加法测试通过", "- [x] 加法测试通过 -> verify: task_9999")
    package = make_package(tmp_path / ".spec" / "specs", SPEC_V1, checklist=checklist)
    before = snapshot(package)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode != 0
    assert snapshot(package) == before


def test_no_section_six_does_not_put_checklist_history_inside_acceptance(tmp_path: Path) -> None:
    spec = SPEC_V1.replace("## 6. 成功标准与验证方式\n- 旧标准：加法测试通过。\n\n", "")
    package = make_package(tmp_path / ".spec" / "specs", spec)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    migrated = (package / "spec.md").read_text(encoding="utf-8")
    assert migrated.index("## 6.") < migrated.index("## 7.") < migrated.index("## 迁移前 checklist.md 原文")


def test_later_referenced_heading_is_not_part_of_six_acceptance(tmp_path: Path) -> None:
    spec = SPEC_V1.replace(
        "## 7. 风险与约束\n- 无\n",
        "## 7. 风险与约束\n- 无\n\n## 参考\n- [ ] 待查阅的参考资料\n",
    )
    package = make_package(tmp_path / ".spec" / "specs", spec)

    result = run_migration(tmp_path, "--apply")

    assert result.returncode == 0, result.stderr
    migrated = (package / "spec.md").read_text(encoding="utf-8")
    assert "- [x] 加法测试通过 -> verify: task_0001" in migrated
    assert "## 参考\n- [ ] 待查阅的参考资料" in migrated


def test_specs_dir_cannot_escape_project(tmp_path: Path) -> None:
    result = run_migration(tmp_path, "--specs-dir", "../outside", "--apply")
    assert result.returncode != 0
    assert "specs-dir" in result.stderr

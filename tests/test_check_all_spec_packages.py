from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_all_spec_packages as checker
from check_all_spec_packages import check_all_packages, check_git_index, check_git_revision

SCRIPT = Path(checker.__file__).resolve()
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
## 6. 成功标准与验证方式
- [x] 门禁通过 -> verify: task_0001
"""
TASKS_DONE = "- [x] Gate\n  - boundary: scripts only\n  - verify: pytest\n"
SUMMARY = "# 完成总结\n\n- 已完成\n"


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, check=check, capture_output=True, text=True)


def init_repo(root: Path) -> None:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Spec Test")


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD").stdout.strip()


def make_package(root: Path, slug: str, specs_dir: str = ".spec", *, passed: bool = True) -> Path:
    package = root / specs_dir / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(SPEC, encoding="utf-8")
    (package / "tasks.md").write_text(TASKS_DONE if passed else TASKS_DONE.replace("[x]", "[ ]"), encoding="utf-8")
    return package


def make_archive(root: Path, slug: str, specs_dir: str = ".spec", *, passed: bool = True) -> Path:
    package = root / specs_dir / "specs" / "archive" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(SPEC, encoding="utf-8")
    (package / "tasks.md").write_text(TASKS_DONE if passed else TASKS_DONE.replace("[x]", "[ ]"), encoding="utf-8")
    (package / "completion-summary.md").write_text(SUMMARY, encoding="utf-8")
    return package


def test_discovers_default_and_explicit_specs_roots(tmp_path: Path) -> None:
    make_package(tmp_path, "2026-07-13_fix-default")
    make_package(tmp_path, "2026-07-13_fix-custom", "spec-state", passed=False)
    failures = check_all_packages(tmp_path, explicit=["spec-state"])
    assert [failure.slug for failure in failures] == ["2026-07-13_fix-custom"]


@pytest.mark.parametrize(
    ("filename", "before", "after"),
    [
        ("spec.md", "- **项目目标**：验证全仓门禁\n", ""),
        ("spec.md", "### 2.3 待确认问题\n- 无", "### 2.3 待确认问题\n- [ ] 问题待确认"),
        ("spec.md", "- [x] 门禁通过", "- [ ] 门禁通过"),
        ("tasks.md", "  - boundary: scripts only\n", ""),
        ("tasks.md", "  - verify: pytest\n", ""),
        ("tasks.md", "- [x] Gate", "- [ ] Gate"),
        ("tasks.md", "Gate", "核心功能 A"),
    ],
)
def test_active_delivery_gates_use_shared_evaluator(tmp_path: Path, filename: str, before: str, after: str) -> None:
    slug = "2026-07-13_fix-active"
    package = make_package(tmp_path, slug)
    path = package / filename
    original = path.read_text(encoding="utf-8")
    assert before in original
    path.write_text(original.replace(before, after), encoding="utf-8")
    failures = check_all_packages(tmp_path)
    assert any(f.slug == slug and "check gates not passed" in f.reason for f in failures), failures


def test_active_evaluator_skips_duplicate_live_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = make_package(tmp_path, "2026-07-13_fix-root")
    seen: list[tuple[Path | None, Path | None]] = []

    def evaluator(*args, **kwargs):
        seen.append((kwargs.get("root"), kwargs.get("package_dir")))
        return True

    monkeypatch.setattr(checker, "overall_check_passed", evaluator)
    assert check_all_packages(tmp_path) == []
    assert seen == [(None, package)]


def test_lint_only_reports_missing_files_without_requiring_completed_work(tmp_path: Path) -> None:
    make_package(tmp_path, "2026-07-13_fix-wip", passed=False)
    missing = make_package(tmp_path, "2026-07-13_fix-missing")
    (missing / "tasks.md").unlink()
    failures = check_all_packages(tmp_path, lint_only=True)
    assert {failure.slug for failure in failures} == {"2026-07-13_fix-missing"}
    assert "tasks.md" in failures[0].reason


def test_lint_only_cli_skips_delivery_gate(tmp_path: Path) -> None:
    make_package(tmp_path, "2026-07-13_fix-wip", passed=False)
    cmd = [sys.executable, str(SCRIPT), "--root", str(tmp_path)]
    full = subprocess.run(cmd, capture_output=True, text=True)
    lint = subprocess.run([*cmd, "--lint-only"], capture_output=True, text=True)
    assert full.returncode == 1
    assert "check gates not passed" in full.stdout
    assert lint.returncode == 0, lint.stdout + lint.stderr


def test_only_trusted_spec_roots_are_discovered(tmp_path: Path) -> None:
    make_package(tmp_path, "2026-07-13_fix-parent")
    make_package(tmp_path, "2026-07-13_fix-untrusted", "hardware", passed=False)
    nested = tmp_path / "nested"
    nested.mkdir()
    init_repo(nested)
    make_package(nested, "2026-07-13_fix-nested", passed=False)
    assert check_all_packages(tmp_path) == []


def test_invalid_active_slug_and_missing_explicit_slug_are_reported(tmp_path: Path) -> None:
    make_package(tmp_path, "invalid-slug")
    failures = check_all_packages(tmp_path)
    assert any(f.slug == "invalid-slug" and "invalid" in f.reason for f in failures)
    missing = check_all_packages(tmp_path, slugs=["2026-07-13_fix-missing"])
    assert any(f.slug == "2026-07-13_fix-missing" and "not found" in f.reason for f in missing)


def test_explicit_slug_must_be_one_development_record_name(tmp_path: Path) -> None:
    (tmp_path / ".spec" / "specs" / "archive").mkdir(parents=True)
    failures = check_all_packages(tmp_path, slugs=["../../../"])
    assert any("invalid Development Record slug" in f.reason for f in failures)


def test_duplicate_active_and_archived_slug_is_rejected_without_reading_archive(tmp_path: Path) -> None:
    slug = "2026-07-13_fix-duplicate"
    make_package(tmp_path, slug)
    archive = make_archive(tmp_path, slug)
    (archive / "spec.md").write_bytes(b"\xff")
    failures = check_all_packages(tmp_path)
    assert any(f.slug == slug and "both active and archive" in f.reason for f in failures), failures


def test_duplicate_active_branch_is_rejected(tmp_path: Path) -> None:
    for slug in ("2026-09-05_fix-first", "2026-09-05_fix-second"):
        package = make_package(tmp_path, slug)
        (package / "spec.md").write_text(SPEC + "\n- Git integration branch：`spec/shared`\n", encoding="utf-8")
    failures = check_all_packages(tmp_path)
    assert any("integration branch bound to multiple active packages" in f.reason for f in failures)


def test_frozen_archives_are_not_read_even_with_explicit_slug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_repo(tmp_path)
    slug = "2026-07-13_fix-frozen"
    archived = make_archive(tmp_path, slug, passed=False)
    (archived / "spec.md").write_bytes(b"\xff")
    commit(tmp_path, "frozen archive")

    original = checker.read_regular_text

    def guarded(path: Path) -> str:
        assert archived not in path.parents, f"frozen archive unexpectedly read: {path}"
        return original(path)

    monkeypatch.setattr(checker, "read_regular_text", guarded)
    assert check_all_packages(tmp_path) == []
    assert check_all_packages(tmp_path, slugs=[slug]) == []
    assert check_git_index(tmp_path, slugs=[slug]) == []


def test_index_checks_only_staged_archive_changes_and_uses_staged_contents(tmp_path: Path) -> None:
    init_repo(tmp_path)
    frozen = make_archive(tmp_path, "2026-07-13_fix-frozen", passed=False)
    changed = make_archive(tmp_path, "2026-07-13_fix-changed")
    commit(tmp_path, "baseline")
    (frozen / "tasks.md").write_text("unstaged broken archive\n", encoding="utf-8")
    (changed / "completion-summary.md").write_text("", encoding="utf-8")
    git(tmp_path, "add", str(changed / "completion-summary.md"))
    (changed / "completion-summary.md").write_text(SUMMARY, encoding="utf-8")

    failures = check_git_index(tmp_path)
    assert any(f.slug == changed.name and "summary" in f.reason for f in failures), failures
    assert not any(f.slug == frozen.name for f in failures)
    assert check_all_packages(tmp_path) == []


def test_index_detects_staged_unchecked_active_not_worktree_fix(tmp_path: Path) -> None:
    init_repo(tmp_path)
    package = make_package(tmp_path, "2026-07-13_fix-index", passed=False)
    git(tmp_path, "add", ".spec")
    (package / "tasks.md").write_text(TASKS_DONE, encoding="utf-8")
    failures = check_git_index(tmp_path)
    assert any(f.slug == package.name and "check gates not passed" in f.reason for f in failures)


def test_index_detects_deleted_required_archive_file(tmp_path: Path) -> None:
    init_repo(tmp_path)
    archive = make_archive(tmp_path, "2026-07-13_fix-delete")
    commit(tmp_path, "baseline")
    (archive / "completion-summary.md").unlink()
    git(tmp_path, "add", "-u")
    failures = check_git_index(tmp_path)
    assert any(f.slug == archive.name and "completion-summary.md" in f.reason for f in failures)


def test_new_staged_archive_is_subject_to_delivery_gate(tmp_path: Path) -> None:
    init_repo(tmp_path)
    archive = make_archive(tmp_path, "2026-07-13_fix-new", passed=False)
    git(tmp_path, "add", ".spec")
    failures = check_git_index(tmp_path)
    assert any(f.slug == archive.name and "check gates not passed" in f.reason for f in failures)


def test_changed_archive_on_custom_trusted_root(tmp_path: Path) -> None:
    init_repo(tmp_path)
    archive = make_archive(tmp_path, "2026-07-13_fix-custom", "custom/state", passed=False)
    git(tmp_path, "add", "custom")
    assert check_git_index(tmp_path) == []
    failures = check_git_index(tmp_path, explicit=["custom/state"])
    assert any(f.slug == archive.name and "check gates not passed" in f.reason for f in failures)


def test_revision_checks_changed_archives_over_explicit_range(tmp_path: Path) -> None:
    init_repo(tmp_path)
    frozen = make_archive(tmp_path, "2026-07-13_fix-frozen", passed=False)
    base = commit(tmp_path, "baseline")
    changed = make_archive(tmp_path, "2026-07-13_fix-new", passed=False)
    first = commit(tmp_path, "archive")
    (tmp_path / "notes.txt").write_text("later\n", encoding="utf-8")
    tip = commit(tmp_path, "follow-up")

    # No baseline means only the target commit, not every archive in its tree.
    assert check_git_revision(tmp_path, tip) == []
    failures = check_git_revision(tmp_path, tip, baseline=base)
    assert any(f.slug == changed.name and "check gates not passed" in f.reason for f in failures)
    assert not any(f.slug == frozen.name for f in failures)
    assert check_git_revision(tmp_path, first, baseline=first) == []


def test_revision_reads_target_not_checkout_or_index(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    base = commit(tmp_path, "base")
    git(tmp_path, "checkout", "-qb", "feature/spec")
    slug = "2026-07-13_fix-revision"
    make_archive(tmp_path, slug)
    revision = commit(tmp_path, "archive")
    git(tmp_path, "checkout", "-q", "main")
    assert not (tmp_path / ".spec").exists()
    assert check_git_revision(tmp_path, revision, slugs=[slug], baseline=base) == []
    failures = check_git_revision(tmp_path, base, slugs=[slug])
    assert any(f.slug == slug and "not found" in f.reason for f in failures)


def test_staged_archive_checks_ignore_unrelated_slug_filter(tmp_path: Path) -> None:
    init_repo(tmp_path)
    selected = make_archive(tmp_path, "2026-07-13_fix-selected")
    unselected = make_archive(tmp_path, "2026-07-13_fix-unselected", passed=False)
    git(tmp_path, "add", ".spec")
    failures = check_git_index(tmp_path, slugs=[selected.name])
    assert any(f.slug == unselected.name and "check gates not passed" in f.reason for f in failures)


def test_git_index_uses_transaction_index_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_repo(tmp_path)
    frozen = make_archive(tmp_path, "2026-07-13_fix-frozen", passed=False)
    commit(tmp_path, "baseline")
    alternate = tmp_path / "alternate-index"
    env = {**os.environ, "GIT_INDEX_FILE": str(alternate)}
    subprocess.run(["git", "read-tree", "HEAD"], cwd=tmp_path, env=env, check=True)
    (frozen / "completion-summary.md").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", str(frozen / "completion-summary.md")], cwd=tmp_path, env=env, check=True)
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate))
    failures = check_git_index(tmp_path)
    assert any(f.slug == frozen.name and "summary" in f.reason for f in failures), failures


def test_revision_checks_merged_archive_without_explicit_baseline(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    commit(tmp_path, "base")
    git(tmp_path, "checkout", "-qb", "feature/archive")
    archive = make_archive(tmp_path, "2026-07-13_fix-merged", passed=False)
    commit(tmp_path, "archive")
    git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "main.txt").write_text("main\n", encoding="utf-8")
    commit(tmp_path, "main")
    git(tmp_path, "merge", "--no-ff", "-qm", "merge archive", "feature/archive")
    failures = check_git_revision(tmp_path, "HEAD")
    assert any(f.slug == archive.name and "check gates not passed" in f.reason for f in failures), failures


def test_revision_merge_uses_only_first_parent_delta(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    commit(tmp_path, "base")
    git(tmp_path, "checkout", "-qb", "feature/without-archive")
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    commit(tmp_path, "feature")
    git(tmp_path, "checkout", "-q", "main")
    make_archive(tmp_path, "2026-07-13_fix-earlier", passed=False)
    commit(tmp_path, "earlier archive")
    git(tmp_path, "merge", "--no-ff", "-qm", "merge feature", "feature/without-archive")
    assert check_git_revision(tmp_path, "HEAD") == []


def test_invalid_revision_range_fails_closed(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    commit(tmp_path, "base")
    failures = check_git_revision(tmp_path, "HEAD", baseline="not-a-revision")
    assert failures and "git" in failures[0].slug


def test_archive_container_and_untrusted_paths_are_ignored(tmp_path: Path) -> None:
    init_repo(tmp_path)
    for rel in (
        ".spec/specs/archive/README.md",
        ".spec/specs/archive/retired/old/note.md",
        "hardware/specs/draft-a/spec.md",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    assert check_git_index(tmp_path) == []


def test_help_removes_server_flag_and_keeps_hook_arguments() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for flag in ("--root", "--index", "--revision", "--baseline", "--slug", "--specs-dir", "--lint-only"):
        assert flag in result.stdout
    assert "--require-archived" not in result.stdout


def test_index_and_revision_are_mutually_exclusive(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path), "--index", "--revision", "HEAD"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not allowed with argument --index" in result.stderr

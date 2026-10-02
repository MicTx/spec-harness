import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_all_spec_packages as check_all_module
from check_all_spec_packages import (
    MAX_SPEC_FILE_BYTES,
    check_all_packages,
    check_git_index,
    check_git_revision,
    legacy_archive_hashes,
)

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


def make_package(root: Path, slug: str, specs_dir: str = ".spec", *, passed: bool = True) -> Path:
    package = root / specs_dir / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(SPEC, encoding="utf-8")
    (package / "tasks.md").write_text(TASKS_DONE if passed else TASKS_DONE.replace("[x]", "[ ]"), encoding="utf-8")
    (package / "checklist.md").write_text(
        CHECKLIST_DONE if passed else CHECKLIST_DONE.replace("[x]", "[ ]"), encoding="utf-8"
    )
    return package


def make_archived_package(root: Path, slug: str, *, summary: str = SUMMARY_V1) -> Path:
    active = make_package(root, slug, passed=True)
    archive_root = root / ".spec" / "specs" / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    archived = active.rename(archive_root / slug)
    (archived / "completion-summary.md").write_text(summary, encoding="utf-8")
    return archived


def test_duplicate_active_integration_branch_is_rejected(tmp_path):
    first = make_package(tmp_path, "2026-09-05_fix-first", passed=True)
    second = make_package(tmp_path, "2026-09-05_fix-second", passed=True)
    shared = "spec/shared"
    for package in (first, second):
        (package / "spec.md").write_text(SPEC + f"\n- Git integration branch：`{shared}`\n", encoding="utf-8")
    failures = check_all_packages(tmp_path)
    assert any("integration branch bound to multiple active packages" in failure.reason for failure in failures)


def test_discovers_default_and_custom_specs_roots(tmp_path):
    make_package(tmp_path, "2026-07-13_fix-default", passed=True)
    make_package(tmp_path, "2026-07-13_fix-custom", "spec-state", passed=False)

    failures = check_all_packages(tmp_path, explicit=["spec-state"])

    assert [failure.slug for failure in failures] == ["2026-07-13_fix-custom"]


def test_require_archived_rejects_even_passing_active_package(tmp_path):
    make_package(tmp_path, "2026-07-13_fix-active", passed=True)

    failures = check_all_packages(tmp_path, require_archived=True)

    assert failures[0].reason == "active package must be archived"


def test_active_gate_receives_project_root_for_evidence_freshness(tmp_path, monkeypatch):
    make_package(tmp_path, "2026-07-13_fix-root-forwarding", passed=True)
    seen = []

    def fake_overall(*args, **kwargs):
        seen.append(kwargs.get("root"))
        return True

    monkeypatch.setattr(check_all_module, "overall_check_passed", fake_overall)
    assert check_all_packages(tmp_path) == []
    assert seen == [tmp_path]


def test_archive_is_validated_and_explicit_slug_must_exist(tmp_path):
    slug = "2026-07-13_fix-archived"
    archived = make_archived_package(tmp_path, slug)

    assert check_all_packages(tmp_path, require_archived=True, slugs=[slug]) == []

    (archived / "completion-summary.md").unlink()
    failures = check_all_packages(tmp_path, slugs=[slug])
    assert any("completion-summary.md" in failure.reason for failure in failures)

    missing = check_all_packages(tmp_path, slugs=["2026-07-13_fix-missing"])
    assert missing[0].reason == "explicit package slug not found in active or archive"


def test_new_archive_cannot_downgrade_by_removing_closure_block(tmp_path):
    slug = "2026-08-22_fix-missing-closure"
    make_archived_package(tmp_path, slug, summary="# New Archive - 完成总结\n")

    failures = check_all_packages(tmp_path, slugs=[slug])
    assert any("not byte-identical to a baseline legacy summary" in failure.reason for failure in failures)


def test_archive_v1_issue_closure_is_strict(tmp_path):
    slug = "2026-07-13_fix-closure"
    summary = """# Archive - 完成总结
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
{
  "version": 1,
  "issues": [
    {
      "id": "bad",
      "summary": "unlinked work",
      "actionable": true,
      "disposition": "resolved_current",
      "taskId": "task_9999",
      "evidence": "claimed"
    }
  ]
}
```
"""
    make_archived_package(tmp_path, slug, summary=summary)

    failures = check_all_packages(tmp_path, slugs=[slug])
    assert any("taskId does not exist" in failure.reason for failure in failures)


def test_archive_cannot_duplicate_active_slug(tmp_path):
    slug = "2026-07-13_fix-duplicate"
    make_package(tmp_path, slug)
    archive = tmp_path / ".spec" / "specs" / "archive" / slug
    archive.mkdir(parents=True)
    for filename, content in {
        "spec.md": SPEC,
        "tasks.md": TASKS_DONE,
        "checklist.md": CHECKLIST_DONE,
        "completion-summary.md": "# old\n",
    }.items():
        (archive / filename).write_text(content, encoding="utf-8")

    failures = check_all_packages(tmp_path, slugs=[slug])
    assert any("both active and archive" in failure.reason for failure in failures)


def test_git_baseline_allows_unchanged_legacy_archive_but_rejects_modified_or_new(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Spec Test"], cwd=tmp_path, check=True)
    legacy_slug = "2026-08-21_fix-legacy"
    legacy = make_archived_package(tmp_path, legacy_slug, summary="# Legacy - 完成总结\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "legacy baseline"], cwd=tmp_path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)

    assert (
        check_all_packages(
            tmp_path,
            slugs=[legacy_slug],
            legacy_hashes=legacy_archive_hashes(tmp_path, "refs/heads/main"),
        )
        == []
    )

    (legacy / "completion-summary.md").write_text("# Legacy modified - 完成总结\n", encoding="utf-8")
    modified = check_all_packages(
        tmp_path,
        slugs=[legacy_slug],
        legacy_hashes=legacy_archive_hashes(tmp_path, "refs/heads/main"),
    )
    assert any("not byte-identical" in failure.reason for failure in modified)

    new_slug = "2026-08-20_fix-backdated-new"
    make_archived_package(tmp_path, new_slug, summary="# Backdated - 完成总结\n")
    added = check_all_packages(
        tmp_path,
        slugs=[new_slug],
        legacy_hashes=legacy_archive_hashes(tmp_path, "refs/heads/main"),
    )
    assert any("not byte-identical" in failure.reason for failure in added)


def test_git_baseline_read_only_compat_skips_regrade_of_byte_identical_legacy(tmp_path):
    """Storage contract: byte-identical legacy archives are read-only
    compatible. Current gates must not re-grade content that was archived
    under the gates of its day; one modified byte restores re-grading."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Spec Test"], cwd=tmp_path, check=True)
    legacy_slug = "2026-08-21_fix-legacy-regrade"
    archived = make_archived_package(tmp_path, legacy_slug)
    # Valid under older gates, failing current ones: an unresolved acceptance
    # result, exactly how pre-0.10 archives look to today's gate extractor.
    (archived / "checklist.md").write_text(
        CHECKLIST_DONE.replace("**验收结果**：通过", "**验收结果**：待修复"), encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "legacy baseline"], cwd=tmp_path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)

    # Without an authoritative baseline the archive is still re-graded.
    assert any(
        "check gates not passed" in failure.reason for failure in check_all_packages(tmp_path, slugs=[legacy_slug])
    )

    # Byte-identical to the baseline: read-only compatibility, no failures.
    assert (
        check_all_packages(
            tmp_path,
            slugs=[legacy_slug],
            legacy_hashes=legacy_archive_hashes(tmp_path, "refs/heads/main"),
        )
        == []
    )

    # One modified byte drops the exemption; current gates apply again.
    (archived / "spec.md").write_text(SPEC + "\n- 后补一行\n", encoding="utf-8")
    assert any(
        "check gates not passed" in failure.reason
        for failure in check_all_packages(
            tmp_path,
            slugs=[legacy_slug],
            legacy_hashes=legacy_archive_hashes(tmp_path, "refs/heads/main"),
        )
    )


@pytest.mark.parametrize("root_name", [".spec", ".trae"])
@pytest.mark.parametrize("target_kind", ["inside", "outside"])
def test_default_spec_root_symlink_is_rejected(tmp_path, target_kind, root_name):
    target = (
        tmp_path / f"real-{root_name[1:]}"
        if target_kind == "inside"
        else tmp_path.parent / f"{tmp_path.name}-outside-{root_name[1:]}"
    )
    (target / "specs").mkdir(parents=True)
    try:
        (tmp_path / root_name).symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    failures = check_all_packages(tmp_path)
    assert failures[0].slug == "<discovery>"
    assert "must not be a symlink" in failures[0].reason


def test_rejects_invalid_slug_and_symlink_package(tmp_path):
    make_package(tmp_path, "invalid-slug", passed=True)
    target = make_package(tmp_path, "2026-07-13_fix-target", passed=True)
    link = tmp_path / ".spec" / "specs" / "2026-07-13_fix-link"
    link.symlink_to(target, target_is_directory=True)

    failures = check_all_packages(tmp_path)
    reasons = {failure.slug: failure.reason for failure in failures}

    assert reasons["invalid-slug"] == "invalid Development Record slug"
    assert reasons["2026-07-13_fix-link"] == "package must be a non-symlink directory"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_rejects_fifo_without_blocking(tmp_path):
    package = make_package(tmp_path, "2026-07-13_fix-fifo", passed=True)
    (package / "spec.md").unlink()
    os.mkfifo(package / "spec.md")

    failures = check_all_packages(tmp_path)

    assert "not a regular file" in failures[0].reason


def test_rejects_oversized_required_file(tmp_path):
    package = make_package(tmp_path, "2026-07-13_fix-large", passed=True)
    (package / "spec.md").write_bytes(b"x" * (MAX_SPEC_FILE_BYTES + 1))

    failures = check_all_packages(tmp_path)

    assert "exceeds" in failures[0].reason


def test_git_index_detects_stale_unchecked_package(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    package = make_package(tmp_path, "2026-07-13_fix-index", passed=False)
    subprocess.run(["git", "add", ".spec"], cwd=tmp_path, check=True)
    (package / "tasks.md").write_text(TASKS_DONE, encoding="utf-8")
    (package / "checklist.md").write_text(CHECKLIST_DONE, encoding="utf-8")

    failures = check_git_index(tmp_path)

    assert failures[0].slug == "2026-07-13_fix-index"


def test_git_revision_validates_target_tree_not_current_checkout(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Spec Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("main\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-qb", "feature/spec"], cwd=tmp_path, check=True)
    slug = "2026-07-13_fix-revision"
    make_archived_package(tmp_path, slug)
    subprocess.run(["git", "add", ".spec"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "archive"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_path, check=True)

    assert not (tmp_path / ".spec").exists()
    assert (
        check_git_revision(
            tmp_path,
            "refs/heads/feature/spec",
            require_archived=True,
            slugs=[slug],
        )
        == []
    )
    missing = check_git_revision(
        tmp_path,
        "refs/heads/main",
        require_archived=True,
        slugs=[slug],
    )
    assert missing[0].reason == "explicit package slug not found in active or archive"


def test_skips_nested_git_repository(tmp_path):
    make_package(tmp_path, "2026-07-13_fix-parent", passed=True)
    nested = tmp_path / "nested"
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    make_package(nested, "2026-07-13_fix-nested", passed=False)

    assert check_all_packages(tmp_path) == []


def test_active_package_dirs_skips_dotfiles(tmp_path):
    """Dotfiles like .DS_Store must not be enumerated as packages."""
    from check_all_spec_packages import active_package_dirs

    specs_root = tmp_path / ".spec"
    packages_root = specs_root / "specs"
    packages_root.mkdir(parents=True)
    (packages_root / ".DS_Store").write_bytes(b"\x00")
    assert active_package_dirs(specs_root) == []


def test_active_package_dirs_skips_non_directories(tmp_path):
    """Stray non-directory files must not be enumerated as packages."""
    from check_all_spec_packages import active_package_dirs

    specs_root = tmp_path / ".spec"
    packages_root = specs_root / "specs"
    packages_root.mkdir(parents=True)
    (packages_root / "README.md").write_text("not a package", encoding="utf-8")
    assert active_package_dirs(specs_root) == []


def test_discover_specs_roots_ignores_untrusted_specs_dirs(tmp_path):
    """specs/ under an untrusted parent (e.g. hardware/) must not be a specs root."""
    untrusted = tmp_path / "hardware" / "specs" / "draft-a"
    untrusted.mkdir(parents=True)
    (untrusted / "spec.md").write_text("x", encoding="utf-8")
    (untrusted / "tasks.md").write_text("x", encoding="utf-8")
    (untrusted / "checklist.md").write_text("x", encoding="utf-8")

    assert check_all_packages(tmp_path) == []


# -- G6/G7: review-round gate-machinery fixes --


def test_index_mode_rejects_require_archived_combination(tmp_path):
    """--index + --require-archived must fail loudly, not drop the flag."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "check_all_spec_packages.py"
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--index", "--require-archived"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "--require-archived is not supported with --index" in result.stderr


def test_legacy_hash_pipeline_is_locale_independent_and_crlf_tolerant(tmp_path):
    """G7: both hash sides share one strict-UTF-8 + newline-normalized pipeline.

    - CRLF worktree copies of an LF-committed legacy archive must stay
      byte-identical (tolerance preserved).
    - Raw non-UTF-8 bytes must fail identically on both sides instead of
      being garbled through the process locale.
    """
    from check_all_spec_packages import _normalized_archive_text

    lf = "验收：通过\n".encode("utf-8")
    crlf = "验收：通过\r\n".encode("utf-8")
    assert _normalized_archive_text(lf) == _normalized_archive_text(crlf) == "验收：通过\n"
    # Lone CR is normalized too (universal-newline parity with text mode).
    assert _normalized_archive_text(b"a\rb") == "a\nb"
    # Strict UTF-8 on both sides: invalid bytes raise instead of locale garbling.
    with pytest.raises(UnicodeDecodeError):
        _normalized_archive_text("验收".encode("utf-8") + b"\xff\xfe")


def test_legacy_hash_matches_crlf_worktree_copy_against_lf_baseline(tmp_path):
    """End-to-end G7: autocrlf-style CRLF worktree copy of a legacy archive
    does not falsely report not-byte-identical."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Spec Test"], cwd=tmp_path, check=True)
    legacy_slug = "2026-08-21_fix-crlf"
    legacy = make_archived_package(tmp_path, legacy_slug, summary="# Legacy CRLF - 完成总结\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "legacy baseline"], cwd=tmp_path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)

    # Rewrite every archive file on disk with CRLF endings (autocrlf checkout).
    for name in ("spec.md", "tasks.md", "checklist.md", "completion-summary.md"):
        raw = (legacy / name).read_bytes()
        (legacy / name).write_bytes(raw.replace(b"\n", b"\r\n"))

    assert (
        check_all_packages(
            tmp_path,
            slugs=[legacy_slug],
            legacy_hashes=legacy_archive_hashes(tmp_path, "refs/heads/main"),
        )
        == []
    )


@pytest.mark.parametrize("checkpoint_state", ["active", "corrupted"])
def test_check_all_rejects_interrupted_update_checkpoint(tmp_path, checkpoint_state):
    from update_checkpoint_support import begin_update_checkpoint, checkpoint_path

    slug = "2026-09-22_fix-checkpoint-blocker-gates"
    package = make_package(tmp_path, slug, passed=True)
    begin_update_checkpoint(package, "封堵归档绕过")
    if checkpoint_state == "corrupted":
        checkpoint_path(package).write_text("{ bad json", encoding="utf-8")

    failures = check_all_packages(tmp_path)
    assert any(failure.slug == slug and "update checkpoint" in failure.reason for failure in failures)


def test_check_all_rejects_standalone_blocked_status_line(tmp_path):
    slug = "2026-09-22_fix-checkpoint-blocker-gates"
    package = make_package(tmp_path, slug, passed=True)
    (package / "tasks.md").write_text(TASKS_DONE + "\n阻塞：等待外部系统\n", encoding="utf-8")

    failures = check_all_packages(tmp_path)
    assert any(failure.slug == slug and "check gates not passed" in failure.reason for failure in failures)


def _summary_with_followup_issue(followup_slug: str) -> str:
    import json

    payload = {
        "version": 1,
        "issues": [
            {
                "id": "finding-followup-closed",
                "summary": "verify follow-up closure",
                "actionable": True,
                "disposition": "resolved_followup",
                "followUpSpecSlug": followup_slug,
                "evidence": "follow-up archived and graded in its own round",
            }
        ],
    }
    return SUMMARY_V1.replace('{"version": 1, "issues": []}', json.dumps(payload, ensure_ascii=False))


def _git_baseline(tmp_path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Spec Test"], cwd=tmp_path, check=True)


def _add_unchecked_function_box(package: Path) -> None:
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "## 3. 功能范围\n", "## 3. 功能范围\n### 3.1 核心功能（MVP）\n- [ ] 功能 A：描述\n", 1
        ),
        encoding="utf-8",
    )


def test_followup_spec_gate_exempts_baseline_identical_archive_with_unchecked_spec_boxes(tmp_path):
    """基线字节等价的 follow-up 归档不被当前 spec.md 功能框门追溯重评（裁定 A）。"""
    followup_slug = "2026-08-21_fix-legacy-followup"
    referencing_slug = "2026-08-21_fix-referencing"
    _git_baseline(tmp_path)
    followup = make_archived_package(tmp_path, followup_slug)
    _add_unchecked_function_box(followup)
    make_archived_package(tmp_path, referencing_slug, summary=_summary_with_followup_issue(followup_slug))
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "legacy baseline"], cwd=tmp_path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    legacy_hashes = legacy_archive_hashes(tmp_path, "refs/heads/main")

    failures = check_all_packages(tmp_path, slugs=[referencing_slug], legacy_hashes=legacy_hashes)
    assert not any("Spec gate is not passed" in failure.reason for failure in failures)
    assert not any("check gates not passed" in failure.reason for failure in failures)


def test_followup_spec_gate_still_fails_drifted_archive_with_unchecked_spec_boxes(tmp_path):
    """与基线有字节差异的 follow-up 归档仍被当前门禁（含 spec.md 功能框门）打红。"""
    followup_slug = "2026-08-21_fix-drifted-followup"
    referencing_slug = "2026-08-21_fix-drifted-referencing"
    _git_baseline(tmp_path)
    followup = make_archived_package(tmp_path, followup_slug)
    _add_unchecked_function_box(followup)
    make_archived_package(tmp_path, referencing_slug, summary=_summary_with_followup_issue(followup_slug))
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "legacy baseline"], cwd=tmp_path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    legacy_hashes = legacy_archive_hashes(tmp_path, "refs/heads/main")

    # One post-baseline byte on the follow-up archive restores full re-grading.
    (followup / "spec.md").write_text(
        (followup / "spec.md").read_text(encoding="utf-8") + "\n<!-- drift -->\n", encoding="utf-8"
    )
    failures = check_all_packages(tmp_path, slugs=[referencing_slug], legacy_hashes=legacy_hashes)
    assert any("Spec gate is not passed" in failure.reason for failure in failures)

    # Without an authoritative baseline the archive is re-graded as well.
    failures = check_all_packages(tmp_path, slugs=[referencing_slug])
    assert any("Spec gate is not passed" in failure.reason for failure in failures)

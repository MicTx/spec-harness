"""Regression coverage for frozen archives and scoped Git changes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_all_spec_packages import check_all_packages, check_git_index, check_git_revision  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _archive(repo: Path, slug: str) -> Path:
    package = repo / ".spec" / "specs" / "archive" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text("# Historical record\n", encoding="utf-8")
    (package / "tasks.md").write_text("# Historical tasks\n", encoding="utf-8")
    (package / "completion-summary.md").write_text("# Historical summary\n", encoding="utf-8")
    return package


def test_frozen_archives_do_not_require_a_baseline_or_current_gates(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Spec Test")
    archive = _archive(tmp_path, "2026-01-01_frozen")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "legacy baseline")
    assert check_all_packages(tmp_path) == []
    assert check_git_index(tmp_path) == []
    assert check_all_packages(tmp_path, slugs=[archive.name]) == []


def test_staged_archive_changes_are_checked_without_an_archive_sweep(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Spec Test")
    frozen = _archive(tmp_path, "2026-01-01_frozen")
    changed = _archive(tmp_path, "2026-01-02_changed")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "baseline")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (changed / "completion-summary.md").write_text("", encoding="utf-8")
    _git(tmp_path, "add", ".")
    staged = check_git_index(tmp_path)
    assert any(f.slug == changed.name for f in staged), staged
    assert not any(f.slug == frozen.name for f in staged)
    _git(tmp_path, "commit", "-qm", "change archive")
    ranged = check_git_revision(tmp_path, "HEAD", baseline=base)
    assert any(f.slug == changed.name for f in ranged), ranged
    assert not any(f.slug == frozen.name for f in ranged)

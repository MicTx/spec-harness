"""Repository invariant: non-frozen archives must pass current gates.

The full gate deliberately re-grades v2 archives because only legacy
four-file bundles are byte-frozen against the authoritative baseline. Without
this check a rule-changing commit can land while silently invalidating
already-delivered v2 records; the failure only surfaces when somebody runs
``check_all_spec_packages.py`` by hand.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_all_spec_packages import (  # noqa: E402
    archived_package_dirs,
    archived_package_failures,
    trusted_git,
)

SPECS_ROOT = ROOT / ".spec"
BASELINE_REFS = ("refs/remotes/origin/main", "refs/remotes/origin/master")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write_legacy_archive(repo: Path, slug: str) -> Path:
    package = repo / ".spec" / "specs" / "archive" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text("# Legacy record\n", encoding="utf-8")
    (package / "tasks.md").write_text("# Legacy tasks\n", encoding="utf-8")
    (package / "checklist.md").write_text("# Legacy checklist\n", encoding="utf-8")
    (package / "completion-summary.md").write_text("# Legacy summary\n", encoding="utf-8")
    return package


def _baseline_ref(root: Path) -> str | None:
    override = os.environ.get("SPEC_ARCHIVE_BASELINE")
    candidates = (override,) if override else BASELINE_REFS
    for ref in candidates:
        if not ref:
            continue
        result = subprocess.run(
            [trusted_git(), "rev-parse", "--verify", "--quiet", ref],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return ref
    return None


def test_repository_archives_pass_current_gates() -> None:
    """Every archived record in this repository must pass the current gates.

    Legacy archives byte-identical to the authoritative baseline stay exempt
    inside the validator. Missing baselines fail closed in CI and skip locally
    with an actionable reason instead of silently passing.
    """
    if not SPECS_ROOT.is_dir():
        pytest.skip("public export tree has no .spec archives")
    archived = archived_package_dirs(SPECS_ROOT.resolve())
    if not archived:
        pytest.skip("no archived packages")

    baseline = _baseline_ref(ROOT)
    if baseline is None:
        if os.environ.get("CI"):
            pytest.fail(
                "archived packages require the authoritative baseline; fetch origin/main or set SPEC_ARCHIVE_BASELINE"
            )
        pytest.skip("authoritative baseline ref missing; run git fetch origin")

    failures = archived_package_failures(ROOT, baseline_revision=baseline)
    assert failures == [], "\n".join(f"{failure.specs_root}:{failure.slug}: {failure.reason}" for failure in failures)


def test_baseline_identical_legacy_archive_is_exempt(tmp_path: Path) -> None:
    """A legacy bundle frozen in the baseline is not re-graded."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Spec Test")
    _write_legacy_archive(tmp_path, "2026-01-01_frozen-legacy")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "baseline")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")

    assert archived_package_failures(tmp_path, baseline_revision="refs/remotes/origin/main") == []


def test_unfrozen_v2_archive_is_reported(tmp_path: Path) -> None:
    """A v2 archive outside the legacy baseline is re-graded."""
    slug = "2026-01-02_unfrozen-v2"
    package = tmp_path / ".spec" / "specs" / "archive" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text("# V2 record\n\n- Workflow: FULL\n", encoding="utf-8")
    (package / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
    (package / "completion-summary.md").write_text("# Summary\n", encoding="utf-8")

    failures = archived_package_failures(tmp_path)
    assert any(failure.slug == slug and failure.reason == "check gates not passed" for failure in failures), failures


def test_active_package_is_not_graded(tmp_path: Path) -> None:
    """In-progress packages must not block a rule commit."""
    active = tmp_path / ".spec" / "specs" / "2026-01-02_wip"
    active.mkdir(parents=True)
    (active / "spec.md").write_text("# WIP\n", encoding="utf-8")
    (active / "tasks.md").write_text("# Tasks\n", encoding="utf-8")

    assert archived_package_failures(tmp_path) == []

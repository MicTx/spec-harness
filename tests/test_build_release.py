"""Tests for scripts/build_release.py — version parsing, export exclusion, archive integrity."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from build_release import compute_sha256, compute_tree_sha256, extract_release_notes, read_version  # noqa: E402
from export_skill_package import _collect_script_files  # noqa: E402

# --------------------------------------------------------------------------- #
# build_release.py is excluded from the runtime export
# --------------------------------------------------------------------------- #


def test_collect_excludes_build_release():
    files = _collect_script_files()
    assert "build_release.py" not in files
    assert "export_skill_package.py" not in files


# --------------------------------------------------------------------------- #
# Version parsing
# --------------------------------------------------------------------------- #


def test_read_version_returns_semver():
    version = read_version(ROOT)
    # Must be X.Y.Z
    parts = version.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_read_version_rejects_missing_file(tmp_path: Path):
    with pytest.raises(Exception):
        read_version(tmp_path)


# --------------------------------------------------------------------------- #
# Release notes extraction
# --------------------------------------------------------------------------- #


def test_extract_release_notes_finds_section(tmp_path: Path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## [Unreleased]\n\nstuff\n\n## [0.0.42] - 2025-01-01\n\n### Added\n- thing\n\n## [0.0.1]\n\nold\n",
        encoding="utf-8",
    )
    notes = extract_release_notes(tmp_path, "0.0.42")
    assert "[0.0.42]" in notes
    assert "thing" in notes
    assert "old" not in notes


def test_extract_release_notes_fallback(tmp_path: Path):
    # No CHANGELOG at all
    notes = extract_release_notes(tmp_path, "9.9.9")
    assert "9.9.9" in notes


def test_compute_tree_sha256_tracks_content_and_executable_mode(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    initial = compute_tree_sha256(tmp_path)
    assert initial == compute_tree_sha256(tmp_path)

    script.chmod(script.stat().st_mode | 0o100)
    executable = compute_tree_sha256(tmp_path)
    assert executable != initial

    (tmp_path / "a.txt").write_text("changed\n", encoding="utf-8")
    assert compute_tree_sha256(tmp_path) != executable


# --------------------------------------------------------------------------- #
# Full build smoke (skip checks to keep CI fast)
# --------------------------------------------------------------------------- #


def test_build_produces_artifacts(tmp_path: Path):
    """Run build_release.py --skip-checks and verify the artifacts."""
    dist = tmp_path / "dist"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_release.py"),
            "--output",
            str(dist),
            "--skip-checks",
            "--skip-signing",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    version = read_version(ROOT)
    archive_name = f"spec-harness-{version}"

    tarball = dist / f"{archive_name}.tar.gz"
    zip_file = dist / f"{archive_name}.zip"
    sums = dist / "SHA256SUMS"
    notes = dist / "RELEASE_NOTES.md"

    assert tarball.exists(), "tar.gz missing"
    assert zip_file.exists(), "zip missing"
    assert sums.exists(), "SHA256SUMS missing"
    assert notes.exists(), "RELEASE_NOTES.md missing"

    # Checksums are correct and cover both archives
    sums_text = sums.read_text(encoding="utf-8")
    assert archive_name + ".tar.gz" in sums_text
    assert archive_name + ".zip" in sums_text
    for line in sums_text.strip().splitlines():
        digest, _arrow, filename = line.partition("  ")
        computed = compute_sha256(dist / filename)
        assert digest == computed, f"checksum mismatch for {filename}"

    # The workflow-equivalent verifier must work from the repository root.
    sha256sum = shutil.which("sha256sum")
    if sha256sum:
        verified = subprocess.run(
            [sha256sum, "-c", "SHA256SUMS"],
            cwd=dist,
            capture_output=True,
            text=True,
        )
        assert verified.returncode == 0, verified.stderr

    with tarfile.open(tarball) as tar:
        members = {m.name: m for m in tar.getmembers()}
        assert f"{archive_name}/VERSION" in members
        assert f"{archive_name}/SKILL.md" in members
        assert f"{archive_name}/install.sh" in members
        assert f"{archive_name}/server/server.py" in members
        assert f"{archive_name}/server/install.sh" in members
        assert f"{archive_name}/server/README.md" in members
        assert f"{archive_name}/scripts/issue_closure_support.py" in members
        assert f"{archive_name}/scripts/install.sh" not in members
        # install.sh must retain its executable bit
        install_member = members[f"{archive_name}/install.sh"]
        assert install_member.mode & 0o100, "install.sh lost executable bit in tarball"

    # zip has VERSION stamp
    with zipfile.ZipFile(zip_file) as zf:
        names = zf.namelist()
        assert f"{archive_name}/VERSION" in names
        assert f"{archive_name}/SKILL.md" in names
        assert f"{archive_name}/install.sh" in names
        assert f"{archive_name}/server/server.py" in names
        assert f"{archive_name}/server/install.sh" in names
        assert f"{archive_name}/server/README.md" in names
        assert f"{archive_name}/scripts/issue_closure_support.py" in names
        assert f"{archive_name}/scripts/install.sh" not in names
        # build_release.py must NOT be in the export
        assert not any("build_release.py" in n for n in names), "build_release.py leaked into export"
        assert not any("export_skill_package.py" in n for n in names), "export_skill_package.py leaked into export"


def test_build_expect_version_mismatch(tmp_path: Path):
    """build_release.py --expect-version should fail on mismatch."""
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_release.py"),
            "--output",
            str(tmp_path / "dist"),
            "--skip-checks",
            "--skip-signing",
            "--expect-version",
            "99.99.99",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "version mismatch" in result.stderr


def test_build_releases_ruff_format_failure_raises_builderror(monkeypatch, tmp_path):
    import build_release as module

    class Result:
        returncode = 0
        stdout = "ruff 0.0"
        stderr = ""

    calls = []

    def fake_run(args, cwd, label):
        calls.append((args, label))
        if label == "ruff format":
            raise module.BuildError("ruff format failed")
        return Result()

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    monkeypatch.setattr(module, "_run_cmd", fake_run)
    with pytest.raises(module.BuildError, match="ruff format"):
        module.run_prebuild_checks(tmp_path)
    assert any(label == "ruff format" for _args, label in calls)


def test_build_releases_ruff_failure_raises_builderror(tmp_path):
    """ruff installed but check fails must raise BuildError, not 'skipped'."""
    import shutil as _shutil

    repo_copy = tmp_path / "repo-copy"
    _shutil.copytree(ROOT, repo_copy, dirs_exist_ok=True)
    # Remove scripts that smoke_test would run so we can isolate the ruff step.
    # Instead, inject a bad file and call run_prebuild_checks, expecting ruff
    # to be the first check that fails (after py_compile and smoke_test pass).
    # To avoid smoke_test failing on the copy, we test the ruff probe logic in
    # isolation by calling the ruff portion directly.
    bad_file = repo_copy / "scripts" / "_bad.py"
    bad_file.write_text('x = f"hello"\n', encoding="utf-8")

    sys.path.insert(0, str(ROOT / "scripts"))
    # py_compile should fail on the bad file first (syntax is fine for F541,
    # but we can verify the ruff probe separately):
    # Actually F541 is a lint rule, not a syntax error, so py_compile passes.
    # Test the ruff probe directly: ruff is installed, so it should NOT skip.
    import subprocess as _sp

    from build_release import PYTHON, BuildError, _run_cmd

    probe = _sp.run(
        [PYTHON, "-m", "ruff", "--version"],
        cwd=repo_copy,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.skip("ruff not installed")

    # ruff check on the bad file must raise BuildError (not 'skipped').
    with pytest.raises(BuildError, match="ruff"):
        _run_cmd(
            [PYTHON, "-m", "ruff", "check", "scripts/", "tests/"],
            repo_copy,
            "ruff",
        )

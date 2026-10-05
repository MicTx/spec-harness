"""Public-source export contract tests for Spec Harness."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from export_public_repo import PublicExportError, build_public_tree  # noqa: E402


def test_public_export_excludes_private_development_state(tmp_path: Path):
    output = tmp_path / "public"
    before = (ROOT / "README.md").read_bytes()
    manifest = build_public_tree(ROOT, output)

    assert manifest
    assert (output / "PUBLIC_MANIFEST.json").is_file()
    assert not (output / ".spec").exists()
    assert not (output / ".maintainer").exists()
    assert (output / ".github/ISSUE_TEMPLATE/bug_report.yml").is_file()
    assert (output / ".github/CODEOWNERS").is_file()
    assert (output / ".github/PULL_REQUEST_TEMPLATE.md").is_file()
    # Only the public-facing workflow ships; the self-hosted pipeline
    # files stay private.
    assert (output / ".github/workflows/ci-public.yml").is_file()
    assert ".github/workflows/ci-public.yml" in manifest
    assert not (output / ".github/workflows/ci.yml").exists()
    assert not (output / ".github/workflows/release.yml").exists()
    assert not (output / "release").exists()
    assert (output / "docs/git-workflow.en.md").is_file()
    assert "scripts/export_public_repo.py" in manifest
    assert (ROOT / "README.md").read_bytes() == before
    # write_bytes defaults to 0644; the exporter must keep the source
    # executable bit and must not hand it out to plain files.
    assert (output / "install.sh").stat().st_mode & 0o111
    assert not (output / "README.md").stat().st_mode & 0o111

    payload = json.loads((output / "PUBLIC_MANIFEST.json").read_text(encoding="utf-8"))
    assert payload["project"] == "spec-harness"
    assert set(payload["files"]) == set(manifest)


def test_public_export_rejects_source_symlink(tmp_path: Path):
    source = tmp_path / "source"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", ".spec", ".maintainer", ".zcode"))
    linked = source / "scripts" / "outside.py"
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = True\n", encoding="utf-8")
    linked.symlink_to(outside)
    with pytest.raises(PublicExportError, match="unsafe entry"):
        build_public_tree(source, tmp_path / "public")


def test_public_publish_does_not_create_on_github_lookup_failure(monkeypatch, tmp_path: Path):
    import export_public_repo

    monkeypatch.setattr(export_public_repo.shutil, "which", lambda _: "/bin/gh")

    def failed_view(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="TLS handshake timeout")

    monkeypatch.setattr(export_public_repo.subprocess, "run", failed_view)
    with pytest.raises(PublicExportError, match="lookup failed"):
        export_public_repo.publish_public_tree(tmp_path, "MicTx/spec-harness", "v0.13.9")

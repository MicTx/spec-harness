"""Public-source export contract tests for Spec Harness."""

from __future__ import annotations

import json
import os
import re
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
    assert not (output / "tests/test_host_evidence_notes.py").exists()
    assert not (output / "tests/test_module_dag.py").exists()
    assert not (output / "tests/test_plans_root.py").exists()
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


_DIR_ASSIGN_RE = re.compile(r'^(\w+)\s*=\s*\w*ROOT\w*\s*/\s*"([^"/]+)"', re.MULTILINE)
_SYS_PATH_RE = re.compile(r"sys\.path\.insert\(\s*\d+\s*,\s*str\((\w+)\)")
# the inline form: sys.path.insert(0, str(ROOT / <quoted dir>))
_SYS_PATH_INLINE_RE = re.compile(r'sys\.path\.insert\(\s*\d+\s*,\s*str\(\s*\w*ROOT\w*\s*/\s*"([^"/]+)"')
_CI_IGNORE_RE = re.compile(r"--ignore=(\S+)")


def test_public_export_tests_only_import_exported_dirs(tmp_path: Path):
    """An exported test must not put an unexported directory on sys.path.

    Such a test aborts collection for the whole public suite (bench/ did).
    """
    output = tmp_path / "public"
    build_public_tree(ROOT, output)
    offenders = []
    for test in sorted((output / "tests").glob("test_*.py")):
        text = test.read_text(encoding="utf-8")
        dirs = dict(_DIR_ASSIGN_RE.findall(text))
        targets = [dirs.get(name) for name in _SYS_PATH_RE.findall(text)] + _SYS_PATH_INLINE_RE.findall(text)
        for target in targets:
            if target and not (output / target).is_dir():
                offenders.append(f"{test.name} -> {target}/")
    assert offenders == []


def test_public_tree_suite_collects(tmp_path: Path):
    """The exported suite must collect under the public CI command; one import error stops the whole job."""
    output = tmp_path / "public"
    build_public_tree(ROOT, output)
    workflow = (output / ".github" / "workflows" / "ci-public.yml").read_text(encoding="utf-8")
    ignores = [f"--ignore={path}" for path in _CI_IGNORE_RE.findall(workflow)]
    assert ignores, "ci-public.yml no longer lists its pytest ignores; update this test"
    collect = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
    completed = subprocess.run(
        [*collect, "tests/", "slots/", *ignores],
        cwd=output,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0, completed.stdout[-2000:] + completed.stderr[-2000:]


def test_ci_public_ignores_only_name_exported_files(tmp_path: Path):
    output = tmp_path / "public"
    build_public_tree(ROOT, output)
    workflow = (ROOT / ".github" / "workflows" / "ci-public.yml").read_text(encoding="utf-8")
    ignored = re.findall(r"--ignore=(\S+)", workflow)
    assert ignored
    assert [path for path in ignored if not (output / path).is_file()] == []

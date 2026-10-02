"""Public-source export contract tests for Spec Harness."""

from __future__ import annotations

import json
import shutil
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
    assert not (output / ".github").exists()
    assert not (output / "release").exists()
    assert "scripts/export_public_repo.py" in manifest
    assert (ROOT / "README.md").read_bytes() == before

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

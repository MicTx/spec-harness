"""Installer signing behavior for keyless source trees (AUD-04 / cluster F3).

A fresh clone of the source tree carries no signing credentials: the
`.watermark-*` files are gitignored and no `SPEC_SIGNING_*` variables are
exported. The installer must treat that as the normal case — auto-apply
`--skip-signing` (export path) or copy as-is (release layout) with a visible
one-line notice — while signing stays enforced where it belongs: manual
exporter invocations and the release build path.

Every test here drives a controlled source-tree copy so the local machine's
real `.watermark-*` credentials can never leak a pass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"
EXPORTER = ROOT / "scripts" / "export_skill_package.py"

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0 or not Path("/bin/bash").exists(),
    reason="bash not available",
)

IGNORE_WHEN_COPYING = shutil.ignore_patterns(
    ".git",
    ".spec",
    "release",
    ".watermark-identity.json",
    ".watermark-key",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
)

KEYLESS_NOTICE_EXPORT = "no signing credentials found - exporting an unsigned skill package"
KEYLESS_NOTICE_COPY = "no signing credentials found - installing the unsigned source tree as-is"


def source_tree_copy(tmp_path: Path, name: str = "srctree") -> Path:
    tree = tmp_path / name
    shutil.copytree(ROOT, tree, ignore=IGNORE_WHEN_COPYING)
    return tree


def run_installer(
    source_dir: Path,
    home: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(home),
            "INSTALL_HOSTS": "claude",
            "SOURCE_DIR": str(source_dir),
            "CLAUDE_SKILLS_DIR": str(home / ".claude" / "skills"),
            "CLAUDE_COMMANDS_DIR": str(home / ".claude" / "commands"),
            "CLAUDE_AGENTS_DIR": str(home / ".claude" / "agents"),
            "BACKUP_ROOT": str(home / ".spec-skill-backups"),
        }
    )
    for var in ("SPEC_SIGNING_IDENTITY", "SPEC_SIGNING_KEY", "SPEC_SKIP_SIGNING"):
        environment.pop(var, None)
    if extra_env:
        environment.update(extra_env)
    return subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        timeout=180,
    )


def throwaway_credentials(tmp_path: Path) -> dict[str, str]:
    identity = tmp_path / "identity.json"
    key = tmp_path / "key"
    identity.write_text(
        '{"holder":"Sandbox Probe","contact":"probe@example.invalid",'
        '"channel":"probe-only","commercial":"not-permitted"}',
        encoding="utf-8",
    )
    # Base64 keeps the key ASCII-clean; raw bytes can strip below the
    # 32-byte minimum in skill_watermark.load_key.
    key.write_bytes(b"c" * 48)
    return {"SPEC_SIGNING_IDENTITY": str(identity), "SPEC_SIGNING_KEY": str(key)}


@requires_bash
def test_keyless_source_tree_installs_unsigned(tmp_path):
    tree = source_tree_copy(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(tree, home)
    assert result.returncode == 0, result.stderr
    assert KEYLESS_NOTICE_EXPORT in result.stdout
    installed = home / ".claude" / "skills" / "spec" / "SKILL.md"
    assert installed.is_file()
    # Unsigned export: the installed SKILL.md is byte-identical to the source.
    assert installed.read_text(encoding="utf-8") == (tree / "SKILL.md").read_text(encoding="utf-8")


@requires_bash
def test_credentials_present_still_signs(tmp_path):
    tree = source_tree_copy(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(tree, home, throwaway_credentials(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "no signing credentials" not in result.stdout
    installed = home / ".claude" / "skills" / "spec" / "SKILL.md"
    # Signed export: the entry file stays byte-identical; the watermark
    # separator rides in NOTICE (AUD-17).
    assert installed.read_text(encoding="utf-8") == (tree / "SKILL.md").read_text(encoding="utf-8")
    signed_notice = (home / ".claude" / "skills" / "spec" / "NOTICE").read_text(encoding="utf-8")
    assert "\u2063" in signed_notice
    assert "Sandbox Probe" not in signed_notice


@requires_bash
def test_partial_credentials_fail_loudly(tmp_path):
    tree = source_tree_copy(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    creds = throwaway_credentials(tmp_path)
    result = run_installer(tree, home, {"SPEC_SIGNING_IDENTITY": creds["SPEC_SIGNING_IDENTITY"]})
    assert result.returncode != 0
    # Neither silent skip nor the old dead-end message: an actionable error.
    assert "signing identity and key are required" in (result.stderr + result.stdout)
    assert "no signing credentials found" not in result.stdout


@requires_bash
def test_unsigned_release_layout_copies_with_notice(tmp_path):
    # Release layouts ship without scripts/export_skill_package.py; an
    # unsigned one plus no credentials takes the copy path, not a refusal.
    tree = source_tree_copy(tmp_path, name="rellayout")
    (tree / "scripts" / "export_skill_package.py").unlink()
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(tree, home)
    assert result.returncode == 0, result.stderr
    assert KEYLESS_NOTICE_COPY in result.stdout
    installed = home / ".claude" / "skills" / "spec" / "SKILL.md"
    assert installed.read_text(encoding="utf-8") == (tree / "SKILL.md").read_text(encoding="utf-8")


@requires_bash
def test_signed_release_layout_installs_without_notice(tmp_path):
    # A signed distribution (the release payload) must keep installing
    # silently on the copy path. The signature carrier is NOTICE; the entry
    # SKILL.md stays clean (AUD-17).
    tree = source_tree_copy(tmp_path, name="signedlayout")
    (tree / "scripts" / "export_skill_package.py").unlink()
    (tree / "NOTICE").write_text("distribution notice\n" + "\u2063watermark-trailer\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(tree, home)
    assert result.returncode == 0, result.stderr
    assert "no signing credentials" not in result.stdout
    installed = home / ".claude" / "skills" / "spec"
    assert (installed / "SKILL.md").read_text(encoding="utf-8") == (tree / "SKILL.md").read_text(encoding="utf-8")
    assert (installed / "NOTICE").read_text(encoding="utf-8") == (tree / "NOTICE").read_text(encoding="utf-8")


def test_manual_exporter_keyless_still_refuses(tmp_path):
    # Release-path enforcement seam: calling the exporter directly without
    # credentials and without --skip-signing must keep failing.
    tree = source_tree_copy(tmp_path, name="exportroot")
    environment = dict(os.environ)
    for var in ("SPEC_SIGNING_IDENTITY", "SPEC_SIGNING_KEY"):
        environment.pop(var, None)
    result = subprocess.run(
        ["python3", str(EXPORTER), "--root", str(tree), "--output", str(tmp_path / "out"), "--force"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )
    assert result.returncode != 0
    assert "signing identity and key are required" in result.stderr
    assert "install.sh applies it automatically" in result.stderr

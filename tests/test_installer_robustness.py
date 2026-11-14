"""Installer robustness pins: hostile paths, symlinks, shell idioms, overwrite policy."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0,
    reason="bash not available",
)


def sandbox_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "BACKUP_ROOT": str(home / ".spec-skill-backups"),
        "INSTALL_HOSTS": "codex",
    }
    if extra:
        env.update(extra)
    return env


def run_installer(home: Path, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=sandbox_env(home, extra),
        timeout=300,
    )


def shared_target(home: Path) -> Path:
    return home / ".codex" / "skills" / "spec"


def test_static_no_postfix_arithmetic_idiom():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "++" not in text
    assert not re.search(r"(?m)^\s*\(\(", text)


@requires_bash
def test_installer_accepts_home_with_spaces_and_brackets(tmp_path):
    home = tmp_path / "my home [v1] (x)"
    home.mkdir()

    result = run_installer(home)

    assert result.returncode == 0, result.stderr
    assert (shared_target(home) / "SKILL.md").is_file()


@requires_bash
def test_symlink_target_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    real = tmp_path / "elsewhere"
    real.mkdir()
    target = shared_target(home)
    target.parent.mkdir(parents=True)
    target.symlink_to(real)

    result = run_installer(home)

    assert result.returncode != 0
    assert "symlink" in result.stderr


@requires_bash
def test_dangling_symlink_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = shared_target(home)
    target.parent.mkdir(parents=True)
    target.symlink_to(tmp_path / "missing")

    result = run_installer(home)

    assert result.returncode != 0
    assert "symlink" in result.stderr


@requires_bash
def test_user_owned_target_requires_force(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = shared_target(home)
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("user skill\n", encoding="utf-8")

    refused = run_installer(home)
    assert refused.returncode != 0
    assert "Refusing to overwrite" in refused.stderr
    assert "FORCE=1" in refused.stderr

    forced = run_installer(home, {"FORCE": "1"})
    assert forced.returncode == 0, forced.stderr
    assert (shared_target(home) / "install-manifest.json").is_file()

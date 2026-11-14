"""Shared .agents/skills host install contracts (OpenSpec #689/#2223 parity)."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0,
    reason="bash not available",
)


def sandbox_env(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "BACKUP_ROOT": str(home / ".spec-skill-backups"),
        "INSTALL_HOSTS": "agents",
    }


def run_installer(home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=sandbox_env(home),
        timeout=300,
    )


def shared_target(home: Path) -> Path:
    return home / ".agents" / "skills" / "spec"


def test_static_shared_host_registered():
    text = INSTALLER.read_text(encoding="utf-8")
    assert re.search(r"SKILL_ONLY_HOSTS=\([^)]*\bagents\b", text)
    assert ".agents/skills" in text
    assert "all|agents|claude" in text
    sys.path.insert(0, str(ROOT / "hooks"))
    import adapters

    assert "agents" in adapters.HOSTS
    assert adapters.capabilities("agents") == ()


@requires_bash
def test_installer_writes_shared_skill(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    result = run_installer(home)

    assert result.returncode == 0, result.stderr
    target = shared_target(home)
    assert (target / "SKILL.md").is_file()
    assert (target / "scripts" / "spec.py").is_file()
    manifest = json.loads((target / "install-manifest.json").read_text(encoding="utf-8"))
    assert manifest["host"] == "agents"
    assert manifest["version"]


@requires_bash
def test_installer_is_idempotent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home).returncode == 0
    first = hashlib.sha256((shared_target(home) / "SKILL.md").read_bytes()).hexdigest()

    second = run_installer(home)

    assert second.returncode == 0, second.stderr
    assert hashlib.sha256((shared_target(home) / "SKILL.md").read_bytes()).hexdigest() == first


def test_readmes_list_shared_host():
    for name in ("README.md", "README-en.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "`agents`" in text, name
        assert ".agents/skills" in text, name

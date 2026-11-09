"""install-manifest.json: the single install record the doctor reads (C9).

install.sh writes ``{host, path, profile, version}`` into every host install
directory. The doctor must be able to answer "where is the skill installed
and which profile is it" from that file alone, never by re-deriving host
directories.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"

sys.path.insert(0, str(ROOT / "scripts"))
from doctor_spec_environment import Doctor  # noqa: E402

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0 or not Path("/bin/bash").exists(),
    reason="bash not available",
)


def sandbox_env(home: Path, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "BACKUP_ROOT": str(home / ".spec-skill-backups"),
    }
    for var in ("INSTALL_HOSTS", "INSTALL_PROFILE", "SOURCE_DIR", "FORCE", "INSTALL_DIR"):
        env.pop(var, None)
    env.update({k: v for k, v in os.environ.items() if k in ("TMPDIR",)})
    env.update({"INSTALL_HOSTS": "claude"})
    if extra_env:
        env.update(extra_env)
    return env


def run_installer(home: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=sandbox_env(home, extra_env),
        timeout=300,
    )


def read_manifest(target: Path) -> dict:
    return json.loads((target / "install-manifest.json").read_text(encoding="utf-8"))


def expected_version() -> str:
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError("pyproject.toml has no project version")


@requires_bash
def test_default_install_writes_manifest(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home)
    assert result.returncode == 0, result.stderr

    target = home / ".claude" / "skills" / "spec"
    manifest = read_manifest(target)
    assert set(manifest) == {"host", "path", "profile", "version"}
    assert manifest["host"] == "claude"
    assert manifest["path"] == str(target)
    assert manifest["profile"] == "core"
    assert manifest["version"] == expected_version()


@requires_bash
def test_full_profile_install_records_full(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, {"INSTALL_PROFILE": "full"})
    assert result.returncode == 0, result.stderr
    manifest = read_manifest(home / ".claude" / "skills" / "spec")
    assert manifest["profile"] == "full"


@requires_bash
def test_reinstall_refreshes_manifest_in_place(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    first = run_installer(home)
    assert first.returncode == 0, first.stderr
    target = home / ".claude" / "skills" / "spec"

    second = run_installer(home, {"INSTALL_PROFILE": "full"})
    assert second.returncode == 0, second.stderr
    manifest = read_manifest(target)
    assert manifest["path"] == str(target)
    assert manifest["profile"] == "full"
    assert manifest["version"] == expected_version()
    # The only stale copy is the pre-upgrade backup; the live install owns the
    # manifest the doctor will read.
    stale = [p for p in home.rglob("install-manifest.json") if p != target / "install-manifest.json"]
    backups = home / ".spec-skill-backups"
    assert all(backups in path.parents for path in stale), stale


@requires_bash
def test_doctor_reads_profile_from_the_manifest(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home)
    assert result.returncode == 0, result.stderr
    target = home / ".claude" / "skills" / "spec"

    doctor = Doctor(skill_root=target, project_root=tmp_path)
    assert doctor.install_manifest()["host"] == "claude"
    assert doctor.install_profile() == "core"
    # The core face passes without the extension components.
    doctor.check_skill_layout()
    layout = next(check for check in doctor.checks if check.id == "skill.layout")
    assert layout.status == "ok"
    assert "profile=core" in layout.detail

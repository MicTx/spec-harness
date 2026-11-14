"""Upgrade-preview pins: DRY_RUN reports the upgrade decision without writing."""

from __future__ import annotations

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


def target(home: Path) -> Path:
    return home / ".codex" / "skills" / "spec"


def _tree(home: Path) -> set[str]:
    return {
        str(path.relative_to(home)) for path in home.rglob("*") if not str(path.relative_to(home)).startswith("Library")
    }


@requires_bash
def test_preview_fresh_install_reports_and_writes_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    before = _tree(home)

    result = run_installer(home, {"DRY_RUN": "1"})

    assert result.returncode == 0, result.stderr
    assert "would install" in result.stdout
    assert "codex/skills/spec" in result.stdout.replace("\\", "/")
    assert _tree(home) == before


@requires_bash
def test_preview_reports_upgrade_in_place(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home).returncode == 0

    result = run_installer(home, {"DRY_RUN": "1"})

    assert result.returncode == 0, result.stderr
    assert "would upgrade" in result.stdout
    assert (target(home) / "install-manifest.json").is_file()


@requires_bash
def test_preview_reports_foreign_user_skill(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target(home).mkdir(parents=True)
    (target(home) / "SKILL.md").write_text("user skill\n", encoding="utf-8")

    refused = run_installer(home, {"DRY_RUN": "1"})

    assert refused.returncode == 0, refused.stderr
    assert "would refuse user-owned" in refused.stdout
    assert "FORCE=1" in refused.stdout
    forced = run_installer(home, {"DRY_RUN": "1", "FORCE": "1"})
    assert "would back up and replace" in forced.stdout
    assert (target(home) / "SKILL.md").read_text(encoding="utf-8") == "user skill\n"


@requires_bash
def test_preview_reports_symlink_refusal(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target(home).parent.mkdir(parents=True)
    target(home).symlink_to(elsewhere)

    result = run_installer(home, {"DRY_RUN": "1"})

    assert result.returncode == 0, result.stderr
    assert "would refuse symlink" in result.stdout


def test_docs_document_preview():
    for name in ("README.md", "README-en.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "DRY_RUN" in text, name

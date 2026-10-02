"""Installer contracts for the orchestration assets.

The exported skill carries `agents/orchestrator.md` and `agents/planner.md` as
portable subagent contracts. Claude Code does not discover subagents from a
skill's own `agents/` directory, so the installer must publish them into the
native `~/.claude/agents/` registry (installer-owned, marker-backed, refusing
user-owned files) for the sidecar lanes to be callable by name.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0 or not Path("/bin/bash").exists(),
    reason="bash not available",
)

AGENT_NAMES = ("orchestrator", "planner", "reviewer", "confirmer")


def run_installer(home: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(home),
            "INSTALL_HOSTS": "claude",
            "CLAUDE_SKILLS_DIR": str(home / ".claude" / "skills"),
            "CLAUDE_COMMANDS_DIR": str(home / ".claude" / "commands"),
            "CLAUDE_AGENTS_DIR": str(home / ".claude" / "agents"),
            "BACKUP_ROOT": str(home / ".spec-skill-backups"),
        }
    )
    environment.pop("SOURCE_DIR", None)
    environment.pop("SPEC_SIGNING_IDENTITY", None)
    environment.pop("SPEC_SIGNING_KEY", None)
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


def marker_path(home: Path, name: str) -> Path:
    return home / ".claude" / "agents" / f"{name}.md.spec-skill-install"


def _watermark_env(tmp_path: Path) -> dict[str, str]:
    identity = tmp_path / "identity.json"
    key = tmp_path / "key"
    identity.write_text(
        '{"holder":"Rights Holder Example","contact":"holder@example.invalid",'
        '"channel":"example-channel-only","commercial":"not-permitted"}',
        encoding="utf-8",
    )
    key.write_bytes(b"k" * 32)
    return {"SPEC_SIGNING_IDENTITY": str(identity), "SPEC_SIGNING_KEY": str(key)}


@requires_bash
def test_installer_publishes_native_claude_agents(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, _watermark_env(tmp_path))
    assert result.returncode == 0, result.stderr
    installed = (home / ".claude" / "skills" / "spec" / "SKILL.md").read_text(encoding="utf-8")
    assert installed.split("\u2063", 1)[0] == (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "Rights Holder Example" not in installed
    assert not (home / ".claude" / "skills" / "spec" / "scripts" / "skill_watermark.py").exists()
    for name in AGENT_NAMES:
        agent = home / ".claude" / "agents" / f"{name}.md"
        assert agent.is_file(), f"native agent file missing: {agent}"
        source = ROOT / "agents" / f"{name}.md"
        assert agent.read_text(encoding="utf-8") == source.read_text(encoding="utf-8"), name
        marker = marker_path(home, name)
        assert marker.is_file(), f"ownership marker missing for {name}"
        assert "installed_by=spec/install.sh" in marker.read_text(encoding="utf-8")


@requires_bash
def test_installer_refuses_unmarked_source_without_key(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    unmarked = tmp_path / "unmarked-skill"
    export = subprocess.run(
        [
            "python3",
            str(ROOT / "scripts" / "export_skill_package.py"),
            "--root",
            str(ROOT),
            "--output",
            str(unmarked),
            "--force",
            "--skip-signing",
        ],
        capture_output=True,
        text=True,
    )
    assert export.returncode == 0, export.stderr
    (unmarked / "scripts" / "route_spec_package.py").unlink()
    result = run_installer(home, {"SOURCE_DIR": str(unmarked)})
    assert result.returncode != 0
    assert "distribution signature" in result.stderr
    assert not (home / ".claude" / "skills" / "spec").exists()


@requires_bash
def test_installer_keeps_existing_mark_without_local_key(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    marked = tmp_path / "marked-skill"
    export = subprocess.run(
        [
            "python3",
            str(ROOT / "scripts" / "export_skill_package.py"),
            "--root",
            str(ROOT),
            "--output",
            str(marked),
            "--force",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, **_watermark_env(tmp_path)},
    )
    assert export.returncode == 0, export.stderr
    (marked / "scripts" / "route_spec_package.py").unlink()
    result = run_installer(home, {"SOURCE_DIR": str(marked)})
    assert result.returncode == 0, result.stderr
    installed = (home / ".claude" / "skills" / "spec" / "SKILL.md").read_text(encoding="utf-8")
    assert "\u2063" in installed
    assert "holder@example.invalid" not in installed


@requires_bash
def test_installer_refuses_to_overwrite_user_owned_native_agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    agents_dir = home / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    user_agent = agents_dir / "orchestrator.md"
    user_agent.write_text("# user's own orchestrator\n", encoding="utf-8")

    result = run_installer(home, _watermark_env(tmp_path))
    assert result.returncode != 0, "installer must refuse a user-owned native agent file"
    assert "user-owned" in result.stderr or "refusing" in result.stderr.lower()
    assert user_agent.read_text(encoding="utf-8") == "# user's own orchestrator\n"


@requires_bash
def test_installer_replaces_installer_owned_native_agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    first = run_installer(home, _watermark_env(tmp_path))
    assert first.returncode == 0, first.stderr
    agents_dir = home / ".claude" / "agents"
    drifted = agents_dir / "planner.md"
    drifted.write_text("# drifted copy\n", encoding="utf-8")

    second = run_installer(home, _watermark_env(tmp_path))
    assert second.returncode == 0, second.stderr
    source = ROOT / "agents" / "planner.md"
    assert drifted.read_text(encoding="utf-8") == source.read_text(encoding="utf-8"), (
        "installer-owned drift not replaced"
    )


@requires_bash
def test_installer_force_backs_up_user_owned_native_agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    agents_dir = home / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    user_agent = agents_dir / "planner.md"
    user_agent.write_text("# user's own planner\n", encoding="utf-8")

    result = run_installer(home, extra_env={"FORCE": "1", **_watermark_env(tmp_path)})
    assert result.returncode == 0, result.stderr
    source = ROOT / "agents" / "planner.md"
    assert user_agent.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    backups = list((home / ".spec-skill-backups").rglob("planner.md*"))
    assert backups, "user-owned agent must be backed up under BACKUP_ROOT"
    assert any("# user's own planner" in path.read_text(encoding="utf-8") for path in backups)


@requires_bash
def test_installer_updates_agent_when_source_changes(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    first = run_installer(home, _watermark_env(tmp_path))
    assert first.returncode == 0, first.stderr
    agents_dir = home / ".claude" / "agents"
    agent = agents_dir / "orchestrator.md"

    # Simulate source evolution: installer-owned copies must refresh on upgrade.
    marker = marker_path(home, "orchestrator")
    marker.write_text(marker.read_text(encoding="utf-8") + "version=0.0.0-test\n", encoding="utf-8")
    agent.write_text("# stale installed copy\n", encoding="utf-8")

    second = run_installer(home, _watermark_env(tmp_path))
    assert second.returncode == 0, second.stderr
    assert agent.read_text(encoding="utf-8") == (ROOT / "agents" / "orchestrator.md").read_text(encoding="utf-8")


@requires_bash
def test_installer_replaces_orphan_native_agent_marker(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    first = run_installer(home, _watermark_env(tmp_path))
    assert first.returncode == 0, first.stderr
    agent = home / ".claude" / "agents" / "planner.md"
    marker = marker_path(home, "planner")
    assert marker.is_file()
    agent.unlink()
    second = run_installer(home, _watermark_env(tmp_path))
    assert second.returncode == 0, second.stderr
    assert agent.is_file()
    assert agent.read_text(encoding="utf-8") == (ROOT / "agents" / "planner.md").read_text(encoding="utf-8")
    assert marker.is_file()

"""Default install-path host detection (AUD-16 / planning F14).

With no explicit ``INSTALL_HOSTS`` the installer installs only hosts with a
detectable presence on the machine: CLI on PATH, default host root present,
or an installer-owned skill target from an earlier install (upgrade path).
An explicit value — including ``all`` — must bypass detection entirely.
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

#: Every host root the installer may create, as <host token, relative root>.
ALL_HOST_ROOTS = (
    ("claude", ".claude"),
    ("claude-desktop", ".claude-desktop"),
    ("codex", ".codex"),
    ("devin", ".config/devin"),
    ("cursor", ".cursor"),
    ("gemini", ".gemini"),
    ("grok", ".grok"),
    ("opencode", ".config/opencode"),
    ("openclaw", ".openclaw"),
    ("hermes", ".hermes"),
    ("pi", ".pi/agent"),
    ("zcode", ".zcode"),
    ("dsh", ".dsh"),
)


def sandbox_env(home: Path, stub_dir: Path | None = None) -> dict[str, str]:
    """A hermetic install environment: sandbox HOME, controlled PATH.

    PATH keeps only the system dirs (for bash/mktemp/python3/sed and
    friends) plus the optional stub dir, so real host CLIs on the running
    machine can never leak into detection. Host-relevant env vars are
    scrubbed the same way.
    """
    path = "/usr/bin:/bin:/usr/sbin:/sbin"
    if stub_dir is not None:
        path = f"{stub_dir}:{path}"
    env = {
        "HOME": str(home),
        "PATH": path,
        "BACKUP_ROOT": str(home / ".spec-skill-backups"),
    }
    for var in (
        "INSTALL_HOSTS",
        "SOURCE_DIR",
        "SPEC_SIGNING_IDENTITY",
        "SPEC_SIGNING_KEY",
        "SPEC_SKIP_SIGNING",
        "CLAUDE_SKILLS_DIR",
        "CLAUDE_COMMANDS_DIR",
        "CLAUDE_AGENTS_DIR",
        "CODEX_SKILLS_DIR",
        "DEVIN_SKILLS_DIR",
        "CURSOR_SKILLS_DIR",
        "ZCODE_SKILLS_DIR",
        "DSH_SKILLS_DIR",
        "DSH_HOME",
        "CLAUDE_DESKTOP_SKILLS_DIR",
        "GEMINI_SKILLS_DIR",
        "GROK_SKILLS_DIR",
        "OPENCODE_SKILLS_DIR",
        "OPENCLAW_SKILLS_DIR",
        "PI_SKILLS_DIR",
        "PI_DIR",
        "HERMES_HOME",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "INSTALL_DIR",
        "FORCE",
    ):
        env.pop(var, None)
    env.update({k: v for k, v in os.environ.items() if k in ("TMPDIR",)})
    return env


def run_installer(home: Path, stub_dir: Path | None = None, extra_env: dict[str, str] | None = None):
    env = sandbox_env(home, stub_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


def install_roots(home: Path) -> list[str]:
    return [rel for _, rel in ALL_HOST_ROOTS if (home / rel).is_dir()]


def cli_stub(stub_dir: Path, name: str) -> None:
    stub_dir.mkdir(parents=True, exist_ok=True)
    stub = stub_dir / name
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)


@requires_bash
def test_auto_detect_installs_only_cli_host(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    stubs = tmp_path / "stubs"
    cli_stub(stubs, "claude")
    result = run_installer(home, stubs)
    assert result.returncode == 0, result.stderr
    roots = install_roots(home)
    assert roots == [".claude"], roots
    assert (home / ".claude" / "skills" / "spec" / "SKILL.md").is_file()
    # The skipped set is visible in the installer output (AUD-16 requirement).
    assert "Skipping hosts not detected" in result.stdout
    assert "Codex" in result.stdout
    assert "INSTALL_HOSTS=all" in result.stdout


@requires_bash
def test_auto_detect_installs_directory_only_host(tmp_path):
    home = tmp_path / "home"
    (home / ".gemini").mkdir(parents=True)
    result = run_installer(home)
    assert result.returncode == 0, result.stderr
    roots = install_roots(home)
    assert roots == [".gemini"], roots


@requires_bash
def test_auto_detect_includes_pi_directory_probe(tmp_path):
    # Pi left SKILL_ONLY_HOSTS in F4 (skill + native prompt-template
    # commands), but the default-detect candidate loop must still visit it:
    # the documented contract installs any host whose default root exists.
    home = tmp_path / "home"
    (home / ".pi" / "agent").mkdir(parents=True)
    result = run_installer(home)
    assert result.returncode == 0, result.stderr
    roots = install_roots(home)
    assert roots == [".pi/agent"], roots
    assert (home / ".pi" / "agent" / "skills" / "spec" / "SKILL.md").is_file()
    # The native prompt-template command surface came along (F4 layout).
    assert (home / ".pi" / "agent" / "prompts" / "spec.md").is_file()


@requires_bash
def test_auto_detect_includes_pi_marker_upgrade_path(tmp_path):
    # An earlier pi install into an env-overridden root leaves the default
    # probe root absent and no pi CLI on PATH; the ownership marker alone
    # must keep pi in the upgrade set, like every other host.
    home = tmp_path / "home"
    home.mkdir()
    custom = home / "custom" / "pi-skills"
    first = run_installer(
        home,
        extra_env={
            "INSTALL_HOSTS": "pi",
            "PI_SKILLS_DIR": str(custom),
            "PI_DIR": str(home / "custom" / "pi-root"),
            "FORCE": "1",
        },
    )
    assert first.returncode == 0, first.stderr
    assert (custom / "spec" / "SKILL.md").is_file()
    assert ".pi" not in install_roots(home)

    second = run_installer(
        home,
        extra_env={
            "PI_SKILLS_DIR": str(custom),
            "PI_DIR": str(home / "custom" / "pi-root"),
            "FORCE": "1",
        },
    )
    assert second.returncode == 0, second.stderr
    assert (custom / "spec" / "SKILL.md").is_file(), "marker-owned pi must be reinstalled"
    assert ".pi" not in install_roots(home)
    # Detection reports the marker-rescued host instead of skipping it.
    assert "detected hosts: pi" in second.stdout


@requires_bash
def test_auto_detect_no_host_installs_nothing_with_guidance(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home)
    assert result.returncode == 0, result.stderr
    assert install_roots(home) == []
    assert "nothing was installed" in result.stdout
    assert "INSTALL_HOSTS=all" in result.stdout


@requires_bash
def test_marker_upgrade_set_survives_detection(tmp_path):
    # An earlier install into an env-overridden root leaves the default
    # probe roots empty and no CLI on PATH; the ownership marker alone must
    # keep the host in the upgrade set.
    home = tmp_path / "home"
    home.mkdir()
    stubs = tmp_path / "stubs"
    cli_stub(stubs, "claude")
    custom = home / "custom" / "claude-skills"
    first = run_installer(
        home,
        stubs,
        extra_env={
            "INSTALL_HOSTS": "claude",
            "CLAUDE_SKILLS_DIR": str(custom),
            "CLAUDE_COMMANDS_DIR": str(home / "custom" / "claude-commands"),
            "CLAUDE_AGENTS_DIR": str(home / "custom" / "claude-agents"),
            "FORCE": "1",
        },
    )
    assert first.returncode == 0, first.stderr
    assert (custom / "spec" / "SKILL.md").is_file()
    assert ".claude" not in install_roots(home)

    # Upgrade: same routing env, but no explicit INSTALL_HOSTS — the
    # default-root probes miss, and only the ownership marker keeps the
    # custom-root install in the set.
    second = run_installer(
        home,
        stubs,
        extra_env={
            "CLAUDE_SKILLS_DIR": str(custom),
            "CLAUDE_COMMANDS_DIR": str(home / "custom" / "claude-commands"),
            "CLAUDE_AGENTS_DIR": str(home / "custom" / "claude-agents"),
            "FORCE": "1",
        },
    )
    assert second.returncode == 0, second.stderr
    assert (custom / "spec" / "SKILL.md").is_file(), "marker-owned host must be reinstalled"
    assert ".claude" not in install_roots(home)
    # Detection reports the marker-rescued host instead of skipping it.
    assert "detected hosts: claude" in second.stdout


@requires_bash
def test_explicit_all_bypasses_detection(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, extra_env={"INSTALL_HOSTS": "all"})
    assert result.returncode == 0, result.stderr
    roots = set(install_roots(home))
    assert roots == {rel for _, rel in ALL_HOST_ROOTS}, roots
    assert "Skipping hosts not detected" not in result.stdout


@requires_bash
def test_explicit_single_host_bypasses_detection(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, extra_env={"INSTALL_HOSTS": "codex"})
    assert result.returncode == 0, result.stderr
    assert install_roots(home) == [".codex"], install_roots(home)


@requires_bash
def test_unknown_explicit_host_still_rejected(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, extra_env={"INSTALL_HOSTS": "bogus"})
    assert result.returncode != 0
    assert "unknown INSTALL_HOSTS entry: bogus" in result.stderr

"""Skill-only host symlink awareness (installer upgrade path).

Users commonly alias a skill-only host's ``spec`` directory to another
host's install (Magpie-managed ``~/.claude/skills/spec``, a Codex copy,
...). The installer must keep such a link alive: when the link points at a
target this run keeps current, the host is skipped as already current;
every other guard (dangling, chained, foreign) keeps failing closed.
"""

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
        "INSTALL_HOSTS": "claude,codex",
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


def claude_target(home: Path) -> Path:
    return home / ".claude" / "skills" / "spec"


def codex_target(home: Path) -> Path:
    return home / ".codex" / "skills" / "spec"


def backup_root(home: Path) -> Path:
    return home / ".spec-skill-backups"


@requires_bash
def test_install_with_symlinked_skill_only_host_succeeds(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # Seed: first install creates the claude + codex copies.
    seeded = run_installer(home)
    assert seeded.returncode == 0, seeded.stderr
    assert claude_target(home).is_dir()

    # Alias codex to the claude install (a Magpie-style fan-out link).
    import shutil

    shutil.rmtree(codex_target(home))
    codex_target(home).parent.mkdir(parents=True, exist_ok=True)
    codex_link = codex_target(home)
    codex_link.symlink_to(claude_target(home), target_is_directory=True)

    upgraded = run_installer(home)

    assert upgraded.returncode == 0, upgraded.stderr
    # The link survives untouched.
    assert codex_link.is_symlink()
    assert codex_link.resolve() == claude_target(home).resolve()
    # Only the real target was rewritten; the link host reported already current.
    assert "already current" in upgraded.stdout
    # No backup happened for the link host: the codex backup namespace stays empty.
    codex_backups = backup_root(home) / "_Users_dawud_home_codex_skills_spec"
    assert not codex_backups.exists() or not any(codex_backups.iterdir())
    # The claude target was upgraded in place (marker still installer-owned).
    assert (claude_target(home) / ".spec-skill-install").is_file()


@requires_bash
def test_preview_reports_already_current_for_symlinked_host(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home).returncode == 0
    import shutil

    shutil.rmtree(codex_target(home))
    codex_target(home).symlink_to(claude_target(home), target_is_directory=True)

    preview = run_installer(home, {"DRY_RUN": "1"})

    assert preview.returncode == 0, preview.stderr
    assert "already current" in preview.stdout
    assert "would refuse symlink" not in preview.stdout


@requires_bash
def test_dangling_symlink_still_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    codex_target(home).parent.mkdir(parents=True)
    codex_target(home).symlink_to(home / ".claude" / "skills" / "spec")

    refused = run_installer(home)

    assert refused.returncode != 0
    assert "symlink" in refused.stderr


@requires_bash
def test_chained_symlink_still_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home).returncode == 0
    # agents -> codex(link) -> claude(real): a link pointing at a link.
    import shutil

    shutil.rmtree(codex_target(home))
    agents_link = home / ".agents" / "skills" / "spec"
    agents_link.parent.mkdir(parents=True, exist_ok=True)
    agents_link.symlink_to(codex_target(home), target_is_directory=False)
    codex_target(home).symlink_to(claude_target(home), target_is_directory=True)

    refused = run_installer(home, {"INSTALL_HOSTS": "claude,codex,agents"})

    assert refused.returncode != 0
    assert "symlink" in refused.stderr


@requires_bash
def test_foreign_symlink_target_still_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_text("user skill\n", encoding="utf-8")
    codex_target(home).parent.mkdir(parents=True)
    codex_target(home).symlink_to(elsewhere, target_is_directory=True)

    refused = run_installer(home)

    assert refused.returncode != 0
    assert "symlink" in refused.stderr or "Refusing to overwrite" in refused.stderr


def point_at_entity(link: Path, entity: Path) -> None:
    """Replace whatever is at `link` with a symlink to the shared entity."""
    import shutil

    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.exists():
        shutil.rmtree(link)
    link.symlink_to(entity, target_is_directory=True)


@requires_bash
def test_preview_matches_real_install_on_fanout_links(tmp_path):
    """DRY_RUN must reach the same verdict as a real install for every host."""
    import shutil

    home = tmp_path / "home"
    home.mkdir()
    entity = tmp_path / "magpie" / "library" / "skills" / "spec"
    assert run_installer(home, {"INSTALL_HOSTS": "claude"}).returncode == 0
    # Relocate the seeded claude install to the shared entity, then fan out.
    entity.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(claude_target(home)), str(entity))
    hosts = ["claude", "codex", "gemini", "agents"]
    for host in hosts:
        point_at_entity(home / f".{host}" / "skills" / "spec", entity)

    real = run_installer(home, {"INSTALL_HOSTS": ",".join(hosts)})
    assert real.returncode == 0, real.stderr
    preview = run_installer(home, {"INSTALL_HOSTS": ",".join(hosts), "DRY_RUN": "1"})
    assert preview.returncode == 0, preview.stderr

    real_current = [line for line in real.stdout.splitlines() if "already current via symlink" in line]
    preview_current = [line for line in preview.stdout.splitlines() if "already current via symlink" in line]
    assert len(preview_current) == len(real_current), (real.stdout, preview.stdout)
    assert "would refuse symlink" not in preview.stdout
    for host in hosts:
        link = home / f".{host}" / "skills" / "spec"
        assert link.is_symlink(), host
        assert link.resolve() == entity.resolve(), host


@requires_bash
def test_preview_still_refuses_chained_and_dangling_links(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    entity = tmp_path / "magpie" / "library" / "skills" / "spec"
    assert run_installer(home, {"INSTALL_HOSTS": "claude"}).returncode == 0
    import shutil

    shutil.move(str(claude_target(home)), str(entity))
    claude_target(home).symlink_to(entity, target_is_directory=True)
    # chains -> claude link -> entity
    chain = home / ".gemini" / "skills" / "spec"
    chain.parent.mkdir(parents=True, exist_ok=True)
    chain.symlink_to(claude_target(home), target_is_directory=True)
    # dangling
    dangling = home / ".codex" / "skills" / "spec"
    dangling.parent.mkdir(parents=True, exist_ok=True)
    dangling.symlink_to(tmp_path / "missing", target_is_directory=True)

    preview = run_installer(home, {"INSTALL_HOSTS": "claude,gemini,codex", "DRY_RUN": "1"})

    assert preview.returncode == 0, preview.stderr
    refused = [line for line in preview.stdout.splitlines() if "would refuse symlink" in line]
    assert len(refused) == 2, preview.stdout
    assert chain.is_symlink() and dangling.is_symlink()


@requires_bash
def test_shared_agents_link_through_claude_preserves_magpie_chain(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    entity = tmp_path / "magpie" / "library" / "skills" / "spec"
    assert run_installer(home, {"INSTALL_HOSTS": "claude"}).returncode == 0
    import shutil

    entity.parent.mkdir(parents=True)
    shutil.move(str(claude_target(home)), str(entity))
    claude_target(home).symlink_to(entity, target_is_directory=True)
    shared = home / ".agents/skills/spec"
    shared.parent.mkdir(parents=True)
    shared.symlink_to(claude_target(home), target_is_directory=True)

    preview = run_installer(home, {"INSTALL_HOSTS": "claude,agents", "DRY_RUN": "1"})
    assert preview.returncode == 0, preview.stderr
    assert "already current via symlink: Shared Agents" in preview.stdout
    result = run_installer(home, {"INSTALL_HOSTS": "claude,agents"})
    assert result.returncode == 0, result.stderr
    assert shared.is_symlink() and claude_target(home).is_symlink()
    assert shared.resolve() == entity.resolve()

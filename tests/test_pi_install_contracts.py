"""Pi native command surface (F4) — install contracts.

The Pi assembly face writes, alongside the skill copy, native prompt
templates into <pi-root>/agent/prompts: ``spec.md`` (/spec) and
``spec:<stage>.md`` (/spec:<stage>, colon layout — validated end to end on
Pi 1.2.0; empirics recorded in the 2026-10-14_normalize-command-registration
package evidence). This suite pins, under a sandbox HOME:

* template sets per profile (full/core), counts parsed from install.sh's
  stage lists — never hardcoded, so USER_STAGES changes cannot make these
  assertions lie;
* the thin-forwarder shape: quoted frontmatter description (unquoted
  ``Spec: ...`` scalars break Pi's YAML frontmatter parser and silently
  drop the template), argument-hint, literal ``$@``, contract-index
  pointer, no workflow restatement;
* description single-source: every stage description equals the
  ``stage_description`` case parsed out of install.sh;
* sidecar markers on every template (stage stamps included);
* idempotent reinstall (zero net change);
* downgrade sweep of installer-owned extended templates, user-authored
  files spared;
* FORCE protection for user-owned same-name templates;
* relocation via PI_DIR (both skills and prompts follow);
* no fake ``spec-<stage>`` alias skills under the Pi skills dir — the exact
  pseudo-command anti-pattern this installer once swept on Claude;
* the exporter never ships Pi templates (install-generated artifacts stay
  out of the export tree).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"
EXPORTER = ROOT / "scripts" / "export_skill_package.py"

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0 or not Path("/bin/bash").exists(),
    reason="bash not available",
)


def parse_stage_list(name: str) -> tuple[str, ...]:
    """Read a stage list straight out of install.sh — the single source."""
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(rf"{name}=\(([^)]*)\)", text)
    assert match, f"stage list {name} not found in install.sh"
    return tuple(match.group(1).split())


def parse_stage_descriptions() -> dict[str, str]:
    """Parse the stage_description() cases out of install.sh."""
    text = INSTALLER.read_text(encoding="utf-8")
    block = re.search(r"stage_description\(\) \{.*?\n\}", text, re.DOTALL)
    assert block, "stage_description() not found in install.sh"
    cases = re.findall(r"\n    ([a-z]+)\) printf '%s\\n' \"([^\"]+)\"", block.group(0))
    assert cases, "no stage_description cases parsed"
    return dict(cases)


def sandbox_env(home: Path, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "BACKUP_ROOT": str(home / ".spec-skill-backups"),
        "SPEC_SKIP_SIGNING": "1",
    }
    for var in (
        "INSTALL_HOSTS",
        "INSTALL_PROFILE",
        "SOURCE_DIR",
        "SPEC_SIGNING_IDENTITY",
        "PI_DIR",
        "PI_SKILLS_DIR",
        "PI_PROMPTS_DIR",
        "FORCE",
    ):
        env.pop(var, None)
    if extra_env:
        env.update(extra_env)
    return env


def run_installer(home: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = sandbox_env(home, {"INSTALL_HOSTS": "pi"})
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


def prompts_dir(home: Path) -> Path:
    return home / ".pi" / "agent" / "prompts"


def template_names(home: Path) -> list[str]:
    directory = prompts_dir(home)
    return sorted(path.name for path in directory.glob("*.md")) if directory.is_dir() else []


def expected_names(stages: tuple[str, ...]) -> list[str]:
    return sorted(["spec.md"] + [f"spec:{stage}.md" for stage in stages])


def template_manifest(home: Path) -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(prompts_dir(home).glob("*.md"))}


# ---------------------------------------------------------------------------
# Sets and markers
# ---------------------------------------------------------------------------


@requires_bash
def test_full_profile_writes_colon_template_set(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, {"INSTALL_PROFILE": "full"})
    assert result.returncode == 0, result.stderr

    stages = parse_stage_list("USER_STAGES")
    assert template_names(home) == expected_names(stages)
    # The skill copy rides along, unchanged from the skill-only era.
    assert (home / ".pi" / "agent" / "skills" / "spec" / "SKILL.md").is_file()
    # Colon layout is the empirical decision: spec:<stage>.md -> /spec:<stage>.
    assert all(":" in name for name in template_names(home) if name != "spec.md")


@requires_bash
def test_core_profile_writes_core_stage_set(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, {"INSTALL_PROFILE": "core"})
    assert result.returncode == 0, result.stderr

    core = parse_stage_list("CORE_STAGES")
    user = parse_stage_list("USER_STAGES")
    assert core != user  # guard the guard: the lists must differ for this to mean anything
    assert template_names(home) == expected_names(core)


@requires_bash
def test_every_template_carries_a_stage_stamped_marker(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home, {"INSTALL_PROFILE": "full"}).returncode == 0

    stages = parse_stage_list("USER_STAGES")
    for name in expected_names(stages):
        marker = prompts_dir(home) / f"{name}.spec-skill-install"
        assert marker.is_file(), f"missing marker for {name}"
        text = marker.read_text(encoding="utf-8")
        assert "installed_by=spec/install.sh" in text
        stage = "base" if name == "spec.md" else name[len("spec:") : -len(".md")]
        assert f"stage={stage}" in text, text


# ---------------------------------------------------------------------------
# Shape contracts
# ---------------------------------------------------------------------------


@requires_bash
def test_template_shape_is_thin_forwarder(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home, {"INSTALL_PROFILE": "full"}).returncode == 0

    for name in template_names(home):
        text = (prompts_dir(home) / name).read_text(encoding="utf-8")
        # Quoted frontmatter: unquoted "Spec: ..." breaks Pi's YAML parser.
        assert 'description: "' in text, name
        assert 'argument-hint: "' in text, name
        # Mechanical argument pass-through and a contract pointer.
        assert "$@" in text, name
        assert "references/commands.md" in text, name
        assert "Use the installed skill `spec`" in text, name
        # Thin forwarder only: no workflow restatement sneaks into the
        # always-loaded surface (F1 slimming rule).
        assert "- [" not in text, name
        assert "\n1. " not in text, name


@requires_bash
def test_descriptions_single_source_from_stage_description(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home, {"INSTALL_PROFILE": "full"}).returncode == 0

    descriptions = parse_stage_descriptions()
    for stage, expected in descriptions.items():
        if stage in ("route", "base"):
            continue  # non-user-stage cases; base is covered below
        if stage not in parse_stage_list("USER_STAGES"):
            continue
        path = prompts_dir(home) / f"spec:{stage}.md"
        assert path.is_file()
        assert f'description: "{expected}"' in path.read_text(encoding="utf-8"), stage


# ---------------------------------------------------------------------------
# Idempotency and sweeps
# ---------------------------------------------------------------------------


@requires_bash
def test_reinstall_is_zero_net_change(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home, {"INSTALL_PROFILE": "full"}).returncode == 0
    before = template_manifest(home)

    result = run_installer(home, {"INSTALL_PROFILE": "full"})
    assert result.returncode == 0, result.stderr
    assert template_manifest(home) == before


@requires_bash
def test_core_downgrade_sweeps_installer_owned_extended_templates(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home, {"INSTALL_PROFILE": "full"}).returncode == 0

    # A user-authored extended-stage template (no marker) must survive.
    user_file = prompts_dir(home) / "spec:organize.md"
    marker = prompts_dir(home) / "spec:organize.md.spec-skill-install"
    marker.unlink()
    user_file.write_text('---\ndescription: "mine"\n---\nmy own command\n', encoding="utf-8")

    result = run_installer(home, {"INSTALL_PROFILE": "core"})
    assert result.returncode == 0, result.stderr

    core = parse_stage_list("CORE_STAGES")
    extended = [stage for stage in parse_stage_list("USER_STAGES") if stage not in core]
    for stage in extended:
        if stage == "organize":
            continue
        assert not (prompts_dir(home) / f"spec:{stage}.md").exists(), stage
        assert not (prompts_dir(home) / f"spec:{stage}.md.spec-skill-install").exists(), stage
    assert template_names(home) == sorted(expected_names(core) + ["spec:organize.md"])
    assert user_file.read_text(encoding="utf-8").startswith('---\ndescription: "mine"')


# ---------------------------------------------------------------------------
# Protection and relocation
# ---------------------------------------------------------------------------


@requires_bash
def test_user_owned_template_refuses_without_force(tmp_path):
    home = tmp_path / "home"
    (prompts_dir(home)).mkdir(parents=True)
    victim = prompts_dir(home) / "spec.md"
    victim.write_text('---\ndescription: "user command"\n---\nkeep me\n', encoding="utf-8")

    result = run_installer(home, {"INSTALL_PROFILE": "core"})
    assert result.returncode != 0
    assert "refusing to replace user-owned target" in (result.stdout + result.stderr)
    assert victim.read_text(encoding="utf-8").startswith('---\ndescription: "user command"')
    # Refusal happens in preflight: not even the skill copy is written.
    assert not (home / ".pi" / "agent" / "skills" / "spec").exists()


@requires_bash
def test_pi_dir_relocates_skills_and_prompts(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    alt = home / "alt-pi"
    result = run_installer(home, {"INSTALL_PROFILE": "core", "PI_DIR": str(alt), "FORCE": "1"})
    assert result.returncode == 0, result.stderr
    core = parse_stage_list("CORE_STAGES")
    assert sorted(path.name for path in (alt / "agent" / "prompts").glob("*.md")) == expected_names(core)
    assert (alt / "agent" / "skills" / "spec" / "SKILL.md").is_file()
    assert not (home / ".pi").exists()


# ---------------------------------------------------------------------------
# Anti-patterns
# ---------------------------------------------------------------------------


@requires_bash
def test_no_fake_alias_skills_in_pi_skills_dir(tmp_path):
    """The pseudo-command anti-pattern: spec-<stage>/spec:<stage> alias
    skill directories under the Pi skills dir must never appear. Pi loads
    prompt templates as the native command face; alias skills would
    re-create the duplicate-entry mess this package exists to remove."""
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home, {"INSTALL_PROFILE": "full"}).returncode == 0

    skills = home / ".pi" / "agent" / "skills"
    entries = sorted(path.name for path in skills.iterdir())
    assert entries == ["spec"], entries


@requires_bash
def test_export_tree_stays_free_of_pi_templates(tmp_path):
    """Pi templates are install-generated artifacts (Claude command files
    set the precedent): the exporter must never ship them."""
    output = tmp_path / "export"
    result = subprocess.run(
        [sys.executable, str(EXPORTER), "--output", str(output), "--force", "--skip-signing"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    leaked = [str(path) for path in output.rglob("*") if "spec:" in path.name or "prompts" in path.parts]
    assert leaked == []

"""INSTALL_PROFILE=core|full — planning F16 sub-step 1 (AUD-18).

The default install collapses to the kernel surface: core stage command
files plus the runtime tree minus extension components (``slots/``,
``agents/``). ``INSTALL_PROFILE=full`` must restore the pre-change install
byte-for-byte, and an unknown value fails loudly. Three evidence layers:

* exporter-level: the profile flag filters the payload tree, full stays
  byte-identical to the flagless default (release pipelines unchanged);
* sandbox (``-k sandbox``): real ``bash install.sh`` runs under a temp
  HOME — default core layout, full parity, loud rejection, downgrade sweep,
  and the no-exporter copy path;
* smoke (``-k smoke``): the shipped smoke's core subset passes on a core
  install, and its default profile is still the full sequence.
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
EXPORTER = ROOT / "scripts" / "export_skill_package.py"
PARITY = ROOT / "scripts" / "verify_install_parity.py"

CORE_STAGES = ("new", "run", "check", "done", "push")
EXTENDED_STAGES = ("goal", "autoplan", "autorun", "update", "status", "doctor", "organize")
CORE_EXCLUDED_DIRS = ("agents", "server", "slots")
KEPT_COMPONENT_DIRS = ("hooks", "references")
# F16 sub-step 2: chain-family scripts are extension surface. The list is
# triplicated (payload_contract.py, install.sh copy path, smoke layout) —
# this module pins all three to the same members.
CORE_EXCLUDED_SCRIPTS = (
    "autorun_spawn.py",
    "autoplan_spawn.py",
    "autoplan_gate.py",
    "chain_spawn_support.py",
    "chain_recovery.py",
    "sweep_chain_windows.py",
)

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0 or not Path("/bin/bash").exists(),
    reason="bash not available",
)


def run_export(output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(EXPORTER), "--output", str(output), "--force", "--skip-signing", *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def tree_manifest(root: Path, skip_names: frozenset[str] = frozenset({".spec-skill-install"})) -> dict[str, bytes]:
    manifest: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not any(part in ("__pycache__",) for part in path.parts):
            relative = path.relative_to(root).as_posix()
            if relative in skip_names or any(part in skip_names for part in path.parts):
                continue
            manifest[relative] = path.read_bytes()
    return manifest


def sandbox_env(home: Path, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Hermetic installer environment: sandbox HOME, controlled PATH.

    Real host CLIs and host-relevant env (including any inherited
    INSTALL_PROFILE) can never leak into the assertions.
    """
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "BACKUP_ROOT": str(home / ".spec-skill-backups"),
        # Deterministic payload: never sign from a checkout that happens to
        # carry maintainer signing files.
        "SPEC_SKIP_SIGNING": "1",
    }
    for var in (
        "INSTALL_HOSTS",
        "INSTALL_PROFILE",
        "SOURCE_DIR",
        "SPEC_SIGNING_IDENTITY",
        "SPEC_SIGNING_KEY",
        "FORCE",
        "INSTALL_DIR",
    ):
        env.pop(var, None)
    env.update({k: v for k, v in os.environ.items() if k in ("TMPDIR",)})
    if extra_env:
        env.update(extra_env)
    return env


def run_installer(home: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = sandbox_env(home, {"INSTALL_HOSTS": "claude"})
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


def claude_stage_commands(home: Path) -> list[str]:
    commands_dir = home / ".claude" / "commands" / "spec"
    return sorted(path.name for path in commands_dir.glob("*.md")) if commands_dir.is_dir() else []


# ---------------------------------------------------------------------------
# Exporter-level profile behavior
# ---------------------------------------------------------------------------


def test_export_core_excludes_extension_components(tmp_path):
    output = tmp_path / "core-export"
    result = run_export(output, "--profile", "core")
    assert result.returncode == 0, result.stderr
    for name in ("SKILL.md", "pyproject.toml", "LICENSE"):
        assert (output / name).is_file(), name
    for name in KEPT_COMPONENT_DIRS:
        assert (output / name).is_dir(), name
    assert (output / "scripts" / "init_spec_package.py").is_file()
    assert (output / "hooks" / "pre-commit").is_file()
    for excluded in CORE_EXCLUDED_DIRS:
        assert not (output / excluded).exists(), excluded
    for excluded in CORE_EXCLUDED_SCRIPTS:
        assert not (output / "scripts" / excluded).exists(), excluded
    assert "profile: core" in result.stdout


def test_export_full_profile_is_byte_identical_to_default(tmp_path):
    """full must equal the flagless default — release pipelines unchanged."""
    default_output = tmp_path / "default-export"
    full_output = tmp_path / "full-export"
    assert run_export(default_output).returncode == 0
    assert run_export(full_output, "--profile", "full").returncode == 0
    assert tree_manifest(default_output) == tree_manifest(full_output)


def test_extension_classification_stays_pinned_across_carriers():
    """payload_contract, install.sh copy path, and the smoke layout assertion
    all carry the core-exclusion lists; they must never drift apart."""
    import re

    sys.path.insert(0, str(ROOT / "scripts"))
    import payload_contract  # noqa: E402

    assert payload_contract.CORE_EXCLUDED_DIRS == CORE_EXCLUDED_DIRS
    assert sorted(payload_contract.CORE_EXCLUDED_SCRIPTS) == sorted(CORE_EXCLUDED_SCRIPTS)

    installer_text = (ROOT / "install.sh").read_text(encoding="utf-8")
    installer_block = re.search(
        r"CORE_EXCLUDED_DIRS=\(([^)]*)\)\nCORE_EXCLUDED_SCRIPTS=\(([^)]*)\)",
        installer_text,
        re.DOTALL,
    )
    assert installer_block, "install.sh core-exclusion arrays not found"
    installer_dirs = installer_block.group(1).split()
    installer_scripts = installer_block.group(2).split()
    assert tuple(installer_dirs) == CORE_EXCLUDED_DIRS
    assert tuple(installer_scripts) == CORE_EXCLUDED_SCRIPTS

    smoke_text = (ROOT / "scripts" / "smoke_test_spec_skill.py").read_text(encoding="utf-8")
    for name in CORE_EXCLUDED_SCRIPTS:
        assert f'"{name}"' in smoke_text, f"smoke layout must pin scripts/{name}"


def test_export_rejects_unknown_profile(tmp_path):
    output = tmp_path / "bogus-export"
    result = run_export(output, "--profile", "bogus")
    assert result.returncode != 0
    assert "invalid choice" in result.stderr
    assert not output.exists()


def test_parity_tool_profile_aware_for_core_installs(tmp_path):
    """A core install fails the default (full) audit and passes --profile core."""
    core_output = tmp_path / "core-export"
    assert run_export(core_output, "--profile", "core").returncode == 0

    default_audit = subprocess.run(
        [sys.executable, str(PARITY), "--install", str(core_output), "--repo", str(ROOT)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert default_audit.returncode != 0
    assert (
        "missing shipped file: agents/" in default_audit.stdout
        or "missing shipped file: slots/" in default_audit.stdout
    )

    core_audit = subprocess.run(
        [sys.executable, str(PARITY), "--install", str(core_output), "--repo", str(ROOT), "--profile", "core"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert core_audit.returncode == 0, core_audit.stdout
    assert "PASS" in core_audit.stdout


# ---------------------------------------------------------------------------
# Sandbox installs (-k sandbox)
# ---------------------------------------------------------------------------


@requires_bash
def test_sandbox_default_profile_installs_core(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home)
    assert result.returncode == 0, result.stderr
    assert "Install profile: core (default)" in result.stdout
    assert "INSTALL_PROFILE=full" in result.stdout

    skill = home / ".claude" / "skills" / "spec"
    for name in ("SKILL.md", "pyproject.toml") + KEPT_COMPONENT_DIRS:
        assert (skill / name).exists(), name
    for excluded in CORE_EXCLUDED_DIRS:
        assert not (skill / excluded).exists(), excluded
    for excluded in CORE_EXCLUDED_SCRIPTS:
        assert not (skill / "scripts" / excluded).exists(), excluded

    assert claude_stage_commands(home) == sorted(f"{stage}.md" for stage in CORE_STAGES)
    assert not (home / ".claude" / "agents").exists()


@requires_bash
def test_sandbox_full_profile_restores_complete_tree(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, {"INSTALL_PROFILE": "full"})
    assert result.returncode == 0, result.stderr
    assert "Install profile: full" in result.stdout

    skill = home / ".claude" / "skills" / "spec"
    for excluded in CORE_EXCLUDED_DIRS:
        assert (skill / excluded).is_dir(), excluded
    for excluded in CORE_EXCLUDED_SCRIPTS:
        assert (skill / "scripts" / excluded).is_file(), excluded
    assert claude_stage_commands(home) == sorted(f"{stage}.md" for stage in CORE_STAGES + EXTENDED_STAGES)
    for name in ("orchestrator", "planner", "reviewer", "confirmer"):
        assert (home / ".claude" / "agents" / f"{name}.md").is_file(), name

    # full == current export behavior, byte-for-byte (upgrade-safety lock).
    reference = tmp_path / "reference-export"
    assert run_export(reference).returncode == 0
    assert tree_manifest(skill) == tree_manifest(reference)


@requires_bash
def test_sandbox_invalid_profile_fails_loudly(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, {"INSTALL_PROFILE": "bogus"})
    assert result.returncode != 0
    assert "unknown INSTALL_PROFILE: bogus" in result.stderr
    assert "core or full" in result.stderr
    assert not (home / ".claude").exists()


@requires_bash
def test_sandbox_core_upgrade_sweeps_full_artifacts(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    first = run_installer(home, {"INSTALL_PROFILE": "full"})
    assert first.returncode == 0, first.stderr

    downgrade = run_installer(home)
    assert downgrade.returncode == 0, downgrade.stderr

    skill = home / ".claude" / "skills" / "spec"
    for excluded in CORE_EXCLUDED_DIRS:
        assert not (skill / excluded).exists(), excluded
    for excluded in CORE_EXCLUDED_SCRIPTS:
        assert not (skill / "scripts" / excluded).exists(), excluded
    assert claude_stage_commands(home) == sorted(f"{stage}.md" for stage in CORE_STAGES)
    # Swept artifacts are backed up, not deleted outright.
    backups = home / ".spec-skill-backups"
    swept = {path.name for path in backups.rglob("goal.md*")}
    assert swept, "extended stage commands must land in backups on downgrade"
    for name in ("orchestrator", "planner"):
        assert list(backups.rglob(f"{name}.md*")), f"agent {name} must land in backups on downgrade"
    # The former full tree (server/, chain scripts) lands in the whole-tree
    # backup, not in the live skill.
    assert list(backups.rglob("server.py")), "server/ must land in backups on downgrade"
    assert list(backups.rglob("autorun_spawn.py")), "chain scripts must land in backups on downgrade"


@requires_bash
def test_sandbox_copy_path_prunes_core_components(tmp_path):
    """Release trees ship no exporter; core filtering prunes the staged copy."""
    release_tree = tmp_path / "release-tree"
    assert run_export(release_tree).returncode == 0
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, {"SOURCE_DIR": str(release_tree)})
    assert result.returncode == 0, result.stderr

    skill = home / ".claude" / "skills" / "spec"
    for excluded in CORE_EXCLUDED_DIRS:
        assert not (skill / excluded).exists(), excluded
    for excluded in CORE_EXCLUDED_SCRIPTS:
        assert not (skill / "scripts" / excluded).exists(), excluded
    assert (skill / "hooks" / "pre-commit").is_file()
    assert claude_stage_commands(home) == sorted(f"{stage}.md" for stage in CORE_STAGES)


@requires_bash
def test_sandbox_doctor_is_profile_aware(tmp_path):
    """F16 sub-step 2 followup: core installs audit clean against the core
    face (no extension-absence false positives); full installs keep the full
    audit and carry their profile stamp."""
    home = tmp_path / "home"
    home.mkdir()
    core = run_installer(home)
    assert core.returncode == 0, core.stderr
    core_skill = home / ".claude" / "skills" / "spec"
    assert "profile=core" in (core_skill / ".spec-skill-install").read_text(encoding="utf-8")

    core_doctor = subprocess.run(
        [
            sys.executable,
            str(core_skill / "scripts" / "doctor_spec_environment.py"),
            "--root",
            str(home),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=sandbox_env(home),
        timeout=120,
    )
    assert core_doctor.returncode == 0, f"{core_doctor.stdout}\n{core_doctor.stderr}"
    payload = json.loads(core_doctor.stdout)
    layout = next(check for check in payload["checks"] if check["id"] == "skill.layout")
    assert layout["status"] == "ok"
    assert "profile=core" in layout["detail"]
    commands = next(check for check in payload["checks"] if check["id"] == "claude.commands")
    assert commands["status"] == "ok"
    assert "5 个阶段命令文件" in commands["detail"]
    assert all(check["id"] != "claude.agents" for check in payload["checks"])

    full_home = tmp_path / "home-full"
    full_home.mkdir()
    full = run_installer(full_home, {"INSTALL_PROFILE": "full"})
    assert full.returncode == 0, full.stderr
    full_skill = full_home / ".claude" / "skills" / "spec"
    assert "profile=full" in (full_skill / ".spec-skill-install").read_text(encoding="utf-8")

    full_doctor = subprocess.run(
        [
            sys.executable,
            str(full_skill / "scripts" / "doctor_spec_environment.py"),
            "--root",
            str(full_home),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=sandbox_env(full_home),
        timeout=120,
    )
    assert full_doctor.returncode == 0, f"{full_doctor.stdout}\n{full_doctor.stderr}"
    full_payload = json.loads(full_doctor.stdout)
    full_layout = next(check for check in full_payload["checks"] if check["id"] == "skill.layout")
    assert full_layout["status"] == "ok"
    assert "profile=full" in full_layout["detail"]
    full_commands = next(check for check in full_payload["checks"] if check["id"] == "claude.commands")
    assert full_commands["status"] == "ok"
    assert "12 个阶段命令文件" in full_commands["detail"]


# ---------------------------------------------------------------------------
# Smoke profile (-k smoke)
# ---------------------------------------------------------------------------


@requires_bash
def test_smoke_core_subset_passes_on_core_install(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    install = run_installer(home)
    assert install.returncode == 0, install.stderr

    smoke = home / ".claude" / "skills" / "spec" / "scripts" / "smoke_test_spec_skill.py"
    result = subprocess.run(
        [sys.executable, str(smoke), "--profile", "core"],
        capture_output=True,
        text=True,
        env=sandbox_env(home),
        timeout=600,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "core gate chain ok" in result.stdout


def test_smoke_default_profile_is_full_and_rejects_unknown():
    smoke = ROOT / "scripts" / "smoke_test_spec_skill.py"
    bogus = subprocess.run(
        [sys.executable, str(smoke), "--profile", "bogus"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert bogus.returncode != 0
    assert "invalid choice" in bogus.stderr

    source = smoke.read_text(encoding="utf-8")
    assert 'default="full"' in source

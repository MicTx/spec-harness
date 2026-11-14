"""Installer and exporter compatibility aliases share one five-stage command payload."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"
EXPORTER = ROOT / "scripts" / "export_skill_package.py"
CORE_STAGES = ("plan", "run", "repair", "check", "push", "doctor")
RETIRED_STAGES = (
    "goal",
    "autoplan",
    "autorun",
    "update",
    "status",
    "organize",
    "assemble",
    "combat",
    "marshal",
    "new",
    "done",
)
RETIRED_SCRIPTS = (
    "autoplan_gate.py",
    "chain_state.py",
    "route_decision.py",
    "slot_registry.py",
    "install_slot_hooks.py",
)
RETIRED_TREES = (
    "slots",
    "references/orchestration.md",
    "references/slots.md",
    "agents/orchestrator.md",
    "agents/planner.md",
    "agents/reviewer.md",
    "agents/confirmer.md",
    "hooks/claude_activation_guard.py",
)

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0 or not Path("/bin/bash").exists(),
    reason="bash not available",
)


def run_export(output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(EXPORTER), "--output", str(output), "--force", *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def tree_manifest(root: Path) -> dict[str, bytes]:
    manifest: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        if relative in (".spec-skill-install", "install-manifest.json") or any(
            part in (".spec-skill-install", "install-manifest.json") for part in path.parts
        ):
            continue
        manifest[relative] = path.read_bytes()
    return manifest


def sandbox_env(home: Path, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "BACKUP_ROOT": str(home / ".spec-skill-backups"),
    }
    for var in ("INSTALL_HOSTS", "INSTALL_PROFILE", "SOURCE_DIR", "FORCE", "INSTALL_DIR"):
        env.pop(var, None)
    env.update({key: value for key, value in os.environ.items() if key in ("TMPDIR",)})
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
    directory = home / ".claude" / "commands" / "spec"
    return sorted(path.name for path in directory.glob("*.md")) if directory.is_dir() else []


def assert_kernel_tree(root: Path) -> None:
    required = (
        "SKILL.md",
        "install.sh",
        "agents/openai.yaml",
        "hooks/pre-commit",
        "hooks/pre-push",
        "references/exec.md",
        "scripts/spec.py",
        "scripts/package.py",
    )
    for relative in required:
        assert (root / relative).is_file(), relative
    for relative in RETIRED_TREES:
        assert not (root / relative).exists(), relative
    for name in RETIRED_SCRIPTS:
        assert not (root / "scripts" / name).exists(), name
    assert not (root / "scripts" / "payload_contract.py").exists()
    assert sorted(path.name for path in (root / "agents").iterdir()) == ["openai.yaml"]


def test_profile_flags_are_compatibility_aliases(tmp_path):
    outputs = {}
    for profile, args in (("default", ()), ("core", ("--profile", "core")), ("full", ("--profile", "full"))):
        output = tmp_path / profile
        result = run_export(output, *args)
        assert result.returncode == 0, result.stderr
        assert_kernel_tree(output)
        outputs[profile] = tree_manifest(output)
    assert outputs["default"] == outputs["core"] == outputs["full"]
    assert "profile:" not in run_export(tmp_path / "stdout").stdout


def test_export_rejects_unknown_profile(tmp_path):
    output = tmp_path / "bogus-export"
    result = run_export(output, "--profile", "bogus")
    assert result.returncode != 0
    assert "invalid choice" in result.stderr
    assert not output.exists()


@requires_bash
def test_release_payload_install_excludes_maintainer_scripts(tmp_path):
    home = tmp_path / "home"
    release = tmp_path / "release"
    shutil.copytree(
        ROOT,
        release,
        ignore=shutil.ignore_patterns(".git", ".spec", ".maintainer", "tests", "release"),
    )
    (release / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (release / "BUILD_INFO").write_text("version=1.0.0\n", encoding="utf-8")
    (release / "scripts" / "export_skill_package.py").unlink()
    home.mkdir()
    env = sandbox_env(home, {"INSTALL_HOSTS": "claude"})
    result = subprocess.run(
        ["bash", str(release / "install.sh")],
        cwd=release,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    skill = home / ".claude" / "skills" / "spec"
    assert (skill / "VERSION").is_file()
    assert (skill / "BUILD_INFO").is_file()
    assert not (skill / "scripts" / "payload_contract.py").exists()
    assert not (skill / "scripts" / "export_public_repo.py").exists()


def test_payload_contract_has_one_copy_dir_source():
    sys.path.insert(0, str(ROOT / "scripts"))
    import payload_contract  # noqa: E402

    assert payload_contract.COPY_DIRS == ("agents", "hooks", "references")
    assert payload_contract.PROFILE_VALUES == ("core", "full")
    assert not hasattr(payload_contract, "CORE_EXCLUDED_DIRS")
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "CORE_EXCLUDED_DIRS" not in installer
    assert "CORE_EXCLUDED_SCRIPTS" not in installer
    core = re.search(r"^CORE_STAGES=\(([^)]*)\)", installer, re.M).group(1).split()
    assert tuple(core) == CORE_STAGES


@requires_bash
@pytest.mark.parametrize("profile", ["core", "full"])
def test_sandbox_profiles_install_identical_kernel(tmp_path, profile):
    home = tmp_path / f"home-{profile}"
    home.mkdir()
    result = run_installer(home, {"INSTALL_PROFILE": profile})
    assert result.returncode == 0, result.stderr
    assert f"Install profile: {profile}" in result.stdout
    assert "six-stage command kernel" in result.stdout

    skill = home / ".claude" / "skills" / "spec"
    assert_kernel_tree(skill)
    assert claude_stage_commands(home) == sorted(f"{stage}.md" for stage in CORE_STAGES)
    assert f"profile={profile}" in (skill / ".spec-skill-install").read_text(encoding="utf-8")
    assert not (home / ".claude" / "agents").exists()
    settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "spec_hook.py" in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    reference = tmp_path / f"reference-{profile}"
    assert run_export(reference, "--profile", profile).returncode == 0
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
def test_sandbox_upgrade_sweeps_installer_owned_retired_artifacts(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    skill = home / ".claude" / "skills" / "spec"
    commands = home / ".claude" / "commands" / "spec"
    agents = home / ".claude" / "agents"
    for stage in RETIRED_STAGES:
        for directory in (commands, agents):
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{stage}.md"
            path.write_text("# retired\n", encoding="utf-8")
            (directory / f"{stage}.md.spec-skill-install").write_text(
                "installed_by=spec/install.sh\n", encoding="utf-8"
            )
    (agents / "orchestrator.md").write_text("# retired\n", encoding="utf-8")
    (agents / "orchestrator.md.spec-skill-install").write_text("installed_by=spec/install.sh\n", encoding="utf-8")
    skill.mkdir(parents=True)
    for name in RETIRED_SCRIPTS:
        (skill / "scripts").mkdir(exist_ok=True)
        (skill / "scripts" / name).write_text("# retired\n", encoding="utf-8")
    (skill / "slots").mkdir()
    (skill / "references" / "orchestration.md").parent.mkdir(parents=True, exist_ok=True)
    (skill / "references" / "orchestration.md").write_text("# retired\n", encoding="utf-8")
    (skill / ".spec-skill-install").write_text("installed_by=spec/install.sh\n", encoding="utf-8")

    result = run_installer(home)
    assert result.returncode == 0, result.stderr
    assert_kernel_tree(skill)
    for stage in RETIRED_STAGES:
        assert not (commands / f"{stage}.md").exists(), stage
    backups = home / ".spec-skill-backups"
    assert list(backups.rglob("chain_state.py")), "old skill tree must be backed up"
    assert list(backups.rglob("goal.md*")), "retired command must be backed up"
    assert list(backups.rglob("orchestrator.md*")), "retired agent must be backed up"


@requires_bash
def test_sandbox_doctor_is_kernel_aware(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home).returncode == 0
    skill = home / ".claude" / "skills" / "spec"
    doctor = subprocess.run(
        [
            sys.executable,
            str(skill / "scripts" / "doctor.py"),
            "--root",
            str(home),
            "--host",
            "claude",
        ],
        capture_output=True,
        text=True,
        env=sandbox_env(home),
        timeout=120,
    )
    assert doctor.returncode == 0, f"{doctor.stdout}\n{doctor.stderr}"
    payload = json.loads(doctor.stdout)
    assert payload["hosts"] == [{"host": "claude", "wired": True, "config": payload["hosts"][0]["config"]}]


@requires_bash
def test_installer_preserves_user_settings_both_ways(tmp_path):
    import json as _json

    for profile in ("core", "full"):
        for preexisting in (False, True):
            home = tmp_path / f"home-{profile}-{preexisting}"
            home.mkdir()
            settings = home / ".claude" / "settings.json"
            if preexisting:
                settings.parent.mkdir(parents=True)
                settings.write_text(_json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "sentinel": "keep-me"}))
            result = run_installer(home, {"INSTALL_PROFILE": profile, "PYTHONDONTWRITEBYTECODE": "1"})
            assert result.returncode == 0, result.stderr[-300:]
            current = _json.loads(settings.read_text(encoding="utf-8"))
            assert "spec_hook.py" in current["hooks"]["Stop"][0]["hooks"][0]["command"]
            if preexisting:
                assert current["permissions"] == {"allow": ["Bash(ls)"]}
                assert current["sentinel"] == "keep-me"


@requires_bash
def test_repeated_installer_run_keeps_one_owned_hook_per_event(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    first = run_installer(home, {"PYTHONDONTWRITEBYTECODE": "1"})
    assert first.returncode == 0, first.stderr[-300:]
    settings = home / ".claude" / "settings.json"
    installed = json.loads(settings.read_text(encoding="utf-8"))
    assert all(len(entries) == 1 for entries in installed["hooks"].values())
    installed_hook = home / ".claude" / "skills" / "spec" / "hooks" / "spec_hook.py"
    assert str(installed_hook) in installed["hooks"]["Stop"][0]["hooks"][0]["command"]

    second = run_installer(home, {"PYTHONDONTWRITEBYTECODE": "1"})
    assert second.returncode == 0, second.stderr[-300:]
    assert json.loads(settings.read_text(encoding="utf-8")) == installed

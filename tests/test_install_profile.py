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
    "chain_lifecycle.py",
    "chain_session.py",
    "sweep_chain_windows.py",
)

CORE_KEPT_AGENT_FILES = ("openai.yaml",)


def assert_excluded_dir_absent(base: Path, name: str) -> None:
    """A core-excluded component is absent; agents/ may keep only its discovery metadata."""
    target = base / name
    if name == "agents" and target.exists():
        assert sorted(entry.name for entry in target.iterdir()) == list(CORE_KEPT_AGENT_FILES), name
        return
    assert not target.exists(), name


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
        assert_excluded_dir_absent(output, excluded)
    for excluded in CORE_EXCLUDED_SCRIPTS:
        assert not (output / "scripts" / excluded).exists(), excluded
    assert "profile: core" in result.stdout


@pytest.mark.parametrize("profile", ["core", "full"])
def test_exported_instructions_admit_missing_optional_capabilities(tmp_path, profile):
    output = tmp_path / profile
    result = run_export(output, "--profile", profile)
    assert result.returncode == 0, result.stderr
    skill = (output / "SKILL.md").read_text(encoding="utf-8")
    run = (output / "references/stages/run.md").read_text(encoding="utf-8")
    assert "Before slot routing, check that the relevant slot is installed" in skill
    assert "Never silently claim managed execution" in skill
    assert "core omits slots and agent role documents" in run
    assert (output / "slots/team-loop/README.md").exists() is (profile == "full")


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
    assert tuple(f"agents/{name}" for name in CORE_KEPT_AGENT_FILES) == payload_contract.CORE_KEPT_FILES
    kept_block = re.search(r"CORE_KEPT_FILES=\(([^)]*)\)", installer_text)
    assert kept_block, "install.sh CORE_KEPT_FILES array not found"
    assert tuple(kept_block.group(1).split()) == payload_contract.CORE_KEPT_FILES
    installer_block = re.search(
        r"CORE_EXCLUDED_DIRS=\(([^)]*)\)\n(?:#[^\n]*\n)*(?:CORE_KEPT_FILES=\([^)]*\)\n)?"
        r"CORE_EXCLUDED_SCRIPTS=\(([^)]*)\)",
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
        assert_excluded_dir_absent(skill, excluded)
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
        assert_excluded_dir_absent(skill, excluded)
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
        assert_excluded_dir_absent(skill, excluded)
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


# ---------------------------------------------------------------------------
# Discovery metadata survives core (proactive admission)
# ---------------------------------------------------------------------------

ACTIVATION_RUNTIME_FILES = (
    "hooks/claude_activation_guard.py",
    "scripts/assess_spec_activation.py",
    "scripts/activation_state.py",
    "scripts/install_activation_hooks.py",
)


def test_export_core_keeps_discovery_metadata_but_no_agent_contracts(tmp_path):
    output = tmp_path / "core-export"
    assert run_export(output, "--profile", "core").returncode == 0
    assert (output / "agents" / "openai.yaml").is_file()
    shipped = sorted(p.name for p in (output / "agents").iterdir())
    assert shipped == list(CORE_KEPT_AGENT_FILES), shipped
    for contract in ("orchestrator.md", "planner.md", "reviewer.md", "confirmer.md"):
        assert not (output / "agents" / contract).exists(), contract


def test_export_core_ships_the_activation_runtime_without_slots(tmp_path):
    output = tmp_path / "core-export"
    assert run_export(output, "--profile", "core").returncode == 0
    for rel in ACTIVATION_RUNTIME_FILES:
        assert (output / rel).is_file(), rel
    assert not (output / "slots").exists()
    assert not (output / "server").exists()


def test_full_export_is_unchanged_by_the_core_metadata_rule(tmp_path):
    default_output = tmp_path / "default-export"
    full_output = tmp_path / "full-export"
    assert run_export(default_output).returncode == 0
    assert run_export(full_output, "--profile", "full").returncode == 0
    assert tree_manifest(default_output) == tree_manifest(full_output)
    assert (full_output / "agents" / "orchestrator.md").is_file()


@requires_bash
def test_sandbox_core_install_keeps_metadata_and_does_not_wire_settings(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home)
    assert result.returncode == 0, result.stderr
    skill = home / ".claude" / "skills" / "spec"
    assert (skill / "agents" / "openai.yaml").is_file()
    assert not (skill / "agents" / "orchestrator.md").exists()
    assert not (home / ".claude" / "agents").exists()
    assert not (home / ".claude" / "settings.json").exists()
    for rel in ACTIVATION_RUNTIME_FILES:
        assert (skill / rel).is_file(), rel


@requires_bash
def test_sandbox_core_copy_path_prunes_agents_but_keeps_metadata(tmp_path):
    release_tree = tmp_path / "release-tree"
    assert run_export(release_tree).returncode == 0
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, {"SOURCE_DIR": str(release_tree)})
    assert result.returncode == 0, result.stderr
    skill = home / ".claude" / "skills" / "spec"
    assert (skill / "agents" / "openai.yaml").is_file()
    assert not (skill / "agents" / "orchestrator.md").exists()


@requires_bash
def test_sandbox_full_to_core_downgrade_keeps_metadata(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home, {"INSTALL_PROFILE": "full"}).returncode == 0
    assert run_installer(home).returncode == 0
    skill = home / ".claude" / "skills" / "spec"
    assert (skill / "agents" / "openai.yaml").is_file()
    assert not (skill / "agents" / "planner.md").exists()


@requires_bash
def test_sandbox_install_reports_unwired_activation_and_the_one_time_command(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home)
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "not wired by default" in output
    assert "install_activation_hooks.py --mode on" in output
    assert not (home / ".claude" / "settings.json").exists()


def _fingerprint_dir(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_file():
            out[rel] = path.read_bytes()
        else:
            out[rel + "/"] = b""
    return out


def _source_runtime_fingerprint() -> dict:
    root = ROOT / ".spec"
    out = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] == "artifacts" or any(part in ("__pycache__", ".pytest_cache") for part in relative.parts):
            continue
        if path.is_symlink():
            out[str(relative)] = ("symlink", os.readlink(path))
        elif path.is_file():
            out[str(relative)] = ("file", path.read_bytes())
        else:
            out[str(relative)] = ("directory", b"")
    return out


def _seed_claude_home(home: Path, cwd: Path):
    import json as _json

    enc = "".join(c if c.isalnum() or c in "_-" else "-" for c in str(cwd))
    bucket = home / ".claude" / "projects" / enc
    bucket.mkdir(parents=True)
    records = [
        {
            "type": "user",
            "cwd": str(cwd),
            "sessionId": "sid-1",
            "uuid": "u1",
            "timestamp": "2026-11-02T10:00:00Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "export alpha"}]},
        },
        {
            "type": "assistant",
            "cwd": str(cwd),
            "sessionId": "sid-1",
            "uuid": "u2",
            "parentUuid": "u1",
            "timestamp": "2026-11-02T10:01:00Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "alpha next step"}]},
        },
    ]
    with open(bucket / "sid-1.jsonl", "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(_json.dumps(record) + "\n")


@requires_bash
def test_installed_cli_recovers_context_core_and_full(tmp_path):
    import json as _json

    for profile in ("core", "full"):
        home = tmp_path / f"home-{profile}"
        home.mkdir()
        before_source = _source_runtime_fingerprint()
        result = run_installer(home, {"INSTALL_PROFILE": profile, "PYTHONDONTWRITEBYTECODE": "1"})
        assert result.returncode == 0, result.stderr[-300:]
        script = home / ".claude" / "skills" / "spec" / "scripts" / "spec_context.py"
        assert script.is_file(), profile
        if profile == "core":
            assert all(not (script.parent / name).exists() for name in CORE_EXCLUDED_SCRIPTS)

        target = tmp_path / f"target-{profile}"
        target.mkdir()
        native_home = tmp_path / f"native-{profile}"
        _seed_claude_home(native_home, target)
        before_target = _fingerprint_dir(target)
        before_native = _fingerprint_dir(native_home)

        env = dict(os.environ)
        env["HOME"] = str(native_home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        discovered = subprocess.run(
            [
                sys.executable,
                "-B",
                str(script),
                "discover",
                "--root",
                ".",
                "--home",
                str(native_home),
                "--client",
                "devin",
            ],
            cwd=target,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert discovered.returncode == 0, discovered.stderr[-300:]
        payload = _json.loads(discovered.stdout)
        assert payload["candidates"] and payload["candidates"][0]["agent"] == "claude"
        read = subprocess.run(
            [
                sys.executable,
                "-B",
                str(script),
                "read",
                "--root",
                ".",
                "--home",
                str(native_home),
                "--agent",
                "claude",
                "--session",
                payload["candidates"][0]["session_id"],
            ],
            cwd=target,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert read.returncode == 0, read.stderr[-300:]
        body = _json.loads(read.stdout)
        assert [m["text"] for m in body["messages"]] == ["export alpha", "alpha next step"]
        assert body["authority"] == "context-only"
        assert "untrusted" in " ".join(body["advisories"])
        assert any("completion" in advisory or "evidence" in advisory for advisory in body["advisories"])
        assert _fingerprint_dir(target) == before_target
        assert _fingerprint_dir(native_home) == before_native
        assert _source_runtime_fingerprint() == before_source
        assert not (target / ".spec").exists()
        assert not list(script.parent.rglob("__pycache__"))


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
                original = settings.read_bytes()
            result = run_installer(home, {"INSTALL_PROFILE": profile, "PYTHONDONTWRITEBYTECODE": "1"})
            assert result.returncode == 0, result.stderr[-300:]
            if preexisting:
                assert settings.read_bytes() == original
            else:
                assert not settings.exists()


@requires_bash
def test_generated_wrappers_load_recovery_contract(tmp_path):
    for host, install_env in (("claude", {}), ("pi", {"INSTALL_HOSTS": "pi"})):
        home = tmp_path / f"home-{host}"
        home.mkdir()
        result = run_installer(home, {**install_env, "PYTHONDONTWRITEBYTECODE": "1"})
        assert result.returncode == 0, result.stderr[-300:]
        if host == "claude":
            wrappers = home / ".claude" / "commands" / "spec"
            texts = [p.read_text() for p in wrappers.glob("*.md")]
            assert not (home / ".claude" / "commands" / "spec.md").exists()
            skill = (home / ".claude" / "skills" / "spec" / "SKILL.md").read_text()
            assert skill.index("spec_context.py discover") < skill.index("scripts/route_spec_package.py")
        else:
            pi_prompts = home / ".pi" / "agent" / "prompts"
            texts = (
                [p.read_text() for p in pi_prompts.glob("*.md") if not p.name.endswith(".spec-skill-install")]
                if pi_prompts.exists()
                else []
            )
        assert texts, host
        for text in texts:
            normalized = " ".join(text.lower().split())
            assert normalized.index("installed skill") < normalized.index("native session recovery")
            assert normalized.index("native session recovery") < normalized.index("before stage analysis")

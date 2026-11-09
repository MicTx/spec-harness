"""Doctor contracts: five probes plus two advisories (C9).

The doctor reads each install's own ``install-manifest.json`` instead of
re-deriving host directories, so these tests drive fixture skill roots and
isolated HOMEs only.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from doctor_spec_environment import (  # noqa: E402  # type: ignore
    MANIFEST_NAME,
    PROBE_IDS,
    REQUIRED_RUNTIME_FILES,
    Doctor,
)

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def check_by_id(doctor: Doctor, check_id: str):
    for check in doctor.checks:
        if check.id == check_id:
            return check
    raise AssertionError(f"doctor did not run check: {check_id}")


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def make_skill(root: Path, *, profile: str | None = None, version: str = "9.9.9") -> Path:
    """A skill root carrying every required runtime file (valid Python stubs)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "spec"\nversion = "{version}"\n', encoding="utf-8")
    for name in REQUIRED_RUNTIME_FILES:
        path = root / name
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 1\n" if name.endswith(".py") else "fixture\n", encoding="utf-8")
    if profile is not None:
        (root / MANIFEST_NAME).write_text(
            json.dumps({"host": "claude", "path": str(root), "profile": profile, "version": version}),
            encoding="utf-8",
        )
    return root


def run_doctor_cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "doctor_spec_environment.py"), *args],
        capture_output=True,
        text=True,
        env=env or dict(os.environ),
    )


def test_probe_registry_matches_record_sites_and_order():
    source = (ROOT / "scripts" / "doctor_spec_environment.py").read_text(encoding="utf-8")
    recorded = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "record"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            recorded.add(node.args[0].value)

    assert len(PROBE_IDS) == len(set(PROBE_IDS)), "PROBE_IDS must not repeat an id"
    assert set(PROBE_IDS) == recorded, (
        f"PROBE_IDS and self.record() sites drifted apart: {sorted(set(PROBE_IDS) ^ recorded)}"
    )
    assert PROBE_IDS == (
        "python.runtime",
        "git.available",
        "skill.layout",
        "project.hooks",
        "hooks.stop",
        "advice.checkpoint",
        "advice.activation",
    ), "PROBE_IDS must follow run() execution order"


def test_doctor_stays_within_the_500_line_budget():
    lines = (ROOT / "scripts" / "doctor_spec_environment.py").read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 500, f"doctor grew past its C9 budget: {len(lines)} lines"


def test_python_runtime_probe_passes():
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_python_runtime()
    assert check_by_id(doctor, "python.runtime").status == "ok"


def test_git_probe_reports_missing_git(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_git_available()
    check = check_by_id(doctor, "git.available")
    assert check.status == "fail"
    assert "git" in check.detail


def test_layout_probe_reports_missing_runtime_files(tmp_path):
    skill = make_skill(tmp_path / "skill")
    (skill / "install.sh").unlink()
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_layout()
    check = check_by_id(doctor, "skill.layout")
    assert check.status == "fail"
    assert "install.sh" in check.detail


def test_layout_probe_detects_broken_script_compile(tmp_path):
    skill = make_skill(tmp_path / "skill")
    (skill / "scripts" / "bad.py").write_text("def (:\n", encoding="utf-8")
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_layout()
    check = check_by_id(doctor, "skill.layout")
    assert check.status == "fail"
    assert "bad.py" in check.detail


def test_layout_probe_does_not_write_bytecode(tmp_path):
    skill = make_skill(tmp_path / "skill")
    before = {p.relative_to(skill): p.read_bytes() for p in skill.rglob("*") if p.is_file()}
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_layout()
    assert check_by_id(doctor, "skill.layout").status == "ok"
    assert {p.relative_to(skill): p.read_bytes() for p in skill.rglob("*") if p.is_file()} == before
    assert not any(skill.rglob("__pycache__"))


def test_manifest_selects_the_core_runtime_face(tmp_path):
    core = make_skill(tmp_path / "core", profile="core")
    core_doctor = Doctor(skill_root=core, project_root=tmp_path)
    assert core_doctor.install_profile() == "core"
    face = core_doctor.required_runtime_files()
    assert "agents/openai.yaml" in face
    assert not any(name.startswith(("agents/orchestrator", "slots/")) for name in face)
    core_doctor.check_skill_layout()
    assert check_by_id(core_doctor, "skill.layout").status == "ok"

    full = make_skill(tmp_path / "full", profile="full")
    assert Doctor(skill_root=full, project_root=tmp_path).required_runtime_files() == REQUIRED_RUNTIME_FILES


def test_missing_or_bogus_manifest_defaults_to_full(tmp_path):
    skill = make_skill(tmp_path / "skill")
    assert Doctor(skill_root=skill, project_root=tmp_path).install_profile() == "full"
    (skill / MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    assert Doctor(skill_root=skill, project_root=tmp_path).install_profile() == "full"
    (skill / MANIFEST_NAME).write_text(json.dumps({"profile": "bogus"}), encoding="utf-8")
    assert Doctor(skill_root=skill, project_root=tmp_path).install_profile() == "full"


@requires_git
def test_project_hooks_warn_when_not_installed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks()
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "warn"
    assert "managed Git hooks are not installed" in check.detail


@requires_git
def test_project_hooks_ok_when_rendered_gate_points_at_this_skill(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    hook = repo / ".git" / "hooks" / "pre-commit"
    checker = ROOT / "scripts" / "check_all_spec_packages.py"
    hook.write_text(f'#!/bin/sh\nchecker="{checker}"\nexec "$checker" --root .\n', encoding="utf-8")
    hook.chmod(0o755)
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks()
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "ok"
    assert "point to the current skill" in check.detail


@requires_git
def test_project_hooks_fail_on_stale_pointer(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / ".git" / "hooks" / "pre-commit").write_text(
        '#!/bin/sh\nchecker="/gone/skills/spec/scripts/check_all_spec_packages.py"\n', encoding="utf-8"
    )
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks()
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "fail"
    assert "points to a missing" in check.detail
    assert "install_git_hooks.py" in check.fix_note


@requires_git
def test_project_hooks_fail_on_missing_exec_bit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f'#!/bin/sh\nchecker="{ROOT / "scripts" / "check_all_spec_packages.py"}"\n', encoding="utf-8")
    hook.chmod(0o644)
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks()
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "fail"
    assert "not executable" in check.detail


@requires_git
def test_project_hooks_ignore_project_owned_hooks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\necho project-owned\n", encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks()
    assert check_by_id(doctor, "project.hooks").status == "warn"


def test_project_hooks_info_outside_a_work_tree(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    doctor = Doctor(skill_root=ROOT, project_root=plain)
    doctor.check_project_hooks()
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "info"
    assert "is not a Git working tree" in check.detail


def test_stop_hook_ok_when_settings_reference_the_guard(isolated_home, tmp_path):
    settings = isolated_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "claude_stop_guard.py"}]}]}}),
        encoding="utf-8",
    )
    doctor = Doctor(skill_root=ROOT, project_root=tmp_path)
    doctor.check_stop_hook()
    check = check_by_id(doctor, "hooks.stop")
    assert check.status == "ok"
    assert str(settings) in check.detail


def test_stop_hook_warns_when_unwired(isolated_home, tmp_path):
    doctor = Doctor(skill_root=ROOT, project_root=tmp_path)
    doctor.check_stop_hook()
    check = check_by_id(doctor, "hooks.stop")
    assert check.status == "warn"
    assert "claude_stop_guard" in check.detail


def test_checkpoint_leftover_is_an_advisory(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "update-checkpoint.json").write_text("{}", encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=project)
    doctor.run()
    check = check_by_id(doctor, "advice.checkpoint")
    assert check.status == "info"
    assert "update-checkpoint.json" in check.detail
    assert doctor.failing() == []


def test_retired_activation_hook_is_an_advisory(isolated_home, tmp_path):
    settings = isolated_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"hooks": {"Stop": ["hooks/claude_activation_guard.py"]}}', encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=tmp_path)
    doctor.run()
    check = check_by_id(doctor, "advice.activation")
    assert check.status == "info"
    assert "claude_activation_guard.py" in check.detail
    assert doctor.failing() == []


def test_cli_json_contract(isolated_home, tmp_path):
    skill = make_skill(tmp_path / "skill")
    project = tmp_path / "project"
    project.mkdir()
    completed = run_doctor_cli("--root", str(project), "--skill-root", str(skill), "--format", "json")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["skillVersion"] == "9.9.9"
    assert payload["exitCode"] == 0
    assert {"id", "title", "status", "detail", "fixNote"} == set(payload["checks"][0].keys())
    assert set(payload["summary"]) == {"fail", "warn", "fixed", "ok", "info"}
    assert {check["id"] for check in payload["checks"]} >= {"python.runtime", "git.available", "skill.layout"}


def test_cli_exit_code_tracks_failures(isolated_home, tmp_path):
    skill = make_skill(tmp_path / "skill")
    (skill / "install.sh").unlink()
    completed = run_doctor_cli("--root", str(tmp_path), "--skill-root", str(skill), "--format", "json")
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["exitCode"] == 1


def test_cli_rejects_bad_roots(isolated_home, tmp_path):
    assert run_doctor_cli("--root", str(tmp_path / "missing")).returncode == 2
    assert run_doctor_cli("--skill-root", str(tmp_path / "missing")).returncode == 2


def test_cli_accepts_retired_host_flag(isolated_home, tmp_path):
    completed = run_doctor_cli("--root", str(tmp_path), "--host", "not-a-host")
    assert completed.returncode in (0, 1), completed.stderr

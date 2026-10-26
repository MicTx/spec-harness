"""Doctor diagnostics for activation (task-install-diagnostics, doctor half).

Four states must stay distinguishable and none may be inferred from another:

1. files missing           -> the guard is not installed
2. files present, unwired  -> installed but no settings entry (not automatic)
3. wired, target broken    -> settings point at a path that no longer works
4. wired and runnable      -> still "never live-verified": only a recorded live
                              run can say otherwise, and doctor never claims it

All settings live under a temporary HOME; the real one is never read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCTOR = ROOT / "scripts" / "doctor_spec_environment.py"
INSTALLER = ROOT / "scripts" / "install_activation_hooks.py"
CHECK_ID = "claude.activation"


def make_skill_root(base: Path, with_guard=True):
    root = base / "skill"
    (root / "hooks").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "agents").mkdir()
    if with_guard:
        for rel in (
            "hooks/claude_activation_guard.py",
            "scripts/assess_spec_activation.py",
            "scripts/activation_state.py",
        ):
            (root / rel).write_text((ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
    return root


def doctor(home, skill_root, project):
    env = {**os.environ, "HOME": str(home)}
    completed = subprocess.run(
        [
            sys.executable,
            str(DOCTOR),
            "--root",
            str(project),
            "--skill-root",
            str(skill_root),
            "--host",
            "claude",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    checks = json.loads(completed.stdout)["checks"]
    return next((c for c in checks if c["id"] == CHECK_ID), None), completed


def install(home, skill_root, mode):
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        [sys.executable, str(INSTALLER), "--mode", mode, "--root", str(skill_root)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture()
def env(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    return home, project, tmp_path


def test_missing_files_report_not_installed(env):
    home, project, base = env
    check, _ = doctor(home, make_skill_root(base, with_guard=False), project)
    assert check is not None
    assert check["status"] == "info"
    assert "未安装" in check["detail"]


def test_files_without_wiring_report_installed_but_not_automatic(env):
    home, project, base = env
    check, _ = doctor(home, make_skill_root(base), project)
    assert check["status"] == "info"
    assert "未接线" in check["detail"]
    assert "install_activation_hooks.py" in (check["detail"] + check.get("fixNote", ""))


def test_wired_and_runnable_is_still_not_live_verified(env):
    home, project, base = env
    skill = make_skill_root(base)
    assert install(home, skill, "on").returncode == 0
    check, _ = doctor(home, skill, project)
    assert check["status"] in {"ok", "info"}
    assert "已接线" in check["detail"]
    assert "未经 live 验证" in check["detail"]
    assert "live 已验证" not in check["detail"]


def test_wired_but_target_missing_is_flagged(env):
    home, project, base = env
    skill = make_skill_root(base)
    install(home, skill, "on")
    (skill / "hooks/claude_activation_guard.py").unlink()
    check, _ = doctor(home, skill, project)
    assert check["status"] == "warn"
    assert "不可执行" in check["detail"] or "缺失" in check["detail"]


def test_half_wired_is_reported_as_warn(env):
    home, project, base = env
    skill = make_skill_root(base)
    install(home, skill, "on")
    path = home / ".claude" / "settings.json"
    payload = json.loads(path.read_text())
    payload["hooks"].pop("PreToolUse")
    path.write_text(json.dumps(payload))
    check, _ = doctor(home, skill, project)
    assert check["status"] == "warn"
    assert "PreToolUse" in check["detail"]


def test_off_returns_to_unwired(env):
    home, project, base = env
    skill = make_skill_root(base)
    install(home, skill, "on")
    install(home, skill, "off")
    check, _ = doctor(home, skill, project)
    assert "未接线" in check["detail"]


def test_wiring_to_a_different_install_root_is_flagged(env):
    home, project, base = env
    skill = make_skill_root(base)
    other = base / "other-root"
    shutil_target = make_skill_root(base / "o")
    install(home, shutil_target, "on")
    check, _ = doctor(home, skill, project)
    assert check["status"] == "warn"
    assert "其他" in check["detail"] or "不一致" in check["detail"]
    assert not other.exists()


def test_broken_settings_are_reported_not_crashed(env):
    home, project, base = env
    skill = make_skill_root(base)
    path = home / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken")
    check, completed = doctor(home, skill, project)
    assert check["status"] == "warn"
    assert "settings" in check["detail"]
    assert completed.returncode in (0, 1)


def test_doctor_never_modifies_settings(env):
    home, project, base = env
    skill = make_skill_root(base)
    install(home, skill, "on")
    path = home / ".claude" / "settings.json"
    before = path.read_bytes()
    doctor(home, skill, project)
    assert path.read_bytes() == before
    assert not list(path.parent.glob("settings.json.bak-*"))


def test_fix_mode_does_not_wire_activation(env):
    home, project, base = env
    skill = make_skill_root(base)
    subprocess.run(
        [
            sys.executable,
            str(DOCTOR),
            "--root",
            str(project),
            "--skill-root",
            str(skill),
            "--host",
            "claude",
            "--fix",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        timeout=120,
    )
    assert not (home / ".claude" / "settings.json").exists()


def test_live_verification_is_never_inferred(env):
    home, project, base = env
    skill = make_skill_root(base)
    install(home, skill, "on")
    (project / ".spec" / "specs").mkdir(parents=True)
    (project / ".spec" / "specs" / "live-runs.jsonl").write_text('{"live": true}\n')
    check, _ = doctor(home, skill, project)
    assert "未经 live 验证" in check["detail"]


def test_a_root_with_a_single_quote_is_not_misreported_as_another_install_root(env):
    home, project, base = env
    skill = make_skill_root(base / "it's here")
    assert install(home, skill, "on").returncode == 0
    check, _ = doctor(home, skill, project)
    assert check["status"] == "info" and "已接线" in check["detail"], check

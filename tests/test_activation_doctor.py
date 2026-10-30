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


def doctor(home, skill_root, project, host="claude"):
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
            host,
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


# --- scope, host filter and compile check (week review, reproduced before the fix) -----


def install_project(home, skill_root, project, mode="on"):
    return subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--mode",
            mode,
            "--root",
            str(skill_root),
            "--scope",
            "project",
            "--project-root",
            str(project),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        timeout=30,
    )


def test_project_scope_wiring_is_reported_as_wired(env):
    home, project, base = env
    skill = make_skill_root(base)
    assert install_project(home, skill, project).returncode == 0
    check, _ = doctor(home, skill, project)
    assert check["status"] == "info", check
    assert "已接线" in check["detail"] and "项目级" in check["detail"], check
    assert "未接线" not in check["detail"]
    assert not (home / ".claude" / "settings.json").exists()


def test_incomplete_project_wiring_hints_the_project_command(env):
    home, project, base = env
    skill = make_skill_root(base)
    assert install_project(home, skill, project).returncode == 0
    path = project / ".claude" / "settings.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["hooks"].pop("PreToolUse")
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()
    check, _ = doctor(home, skill, project)
    assert check["status"] == "warn" and "PreToolUse" in check["detail"], check
    assert "--scope project" in check.get("fixNote", ""), check
    assert path.read_bytes() == before


def test_activation_check_respects_the_host_scope(env):
    home, project, base = env
    skill = make_skill_root(base)
    assert install(home, skill, "on").returncode == 0
    check, _ = doctor(home, skill, project, host="pi")
    assert check is None, check


def test_a_guard_that_does_not_compile_is_flagged(env):
    home, project, base = env
    skill = make_skill_root(base)
    assert install(home, skill, "on").returncode == 0
    (skill / "hooks/claude_activation_guard.py").write_text("def broken(:\n", encoding="utf-8")
    check, _ = doctor(home, skill, project)
    assert check["status"] == "warn", check
    assert "编译" in check["detail"], check
    assert not list((skill / "hooks").glob("__pycache__")), "the compile check must not write bytecode"


def test_deeply_nested_project_settings_do_not_crash_doctor(env):
    """Project settings come from the repository: a pathological file is reported, never a traceback."""
    home, project, base = env
    skill = make_skill_root(base)
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text("[" * 100000, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(DOCTOR), "--root", str(project), "--skill-root", str(skill), "--host", "claude"]
        + ["--format", "json"],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        timeout=120,
    )
    assert "Traceback" not in completed.stderr, completed.stderr[-400:]
    check = next(c for c in json.loads(completed.stdout)["checks"] if c["id"] == CHECK_ID)
    assert check["status"] == "warn" and "settings" in check["detail"], check


def test_a_rewritten_guard_command_is_not_reported_as_wired(env):
    """Only the installer's own command shape is wiring; `--mode off` would not remove anything else either."""
    home, project, base = env
    skill = make_skill_root(base)
    assert install(home, skill, "on").returncode == 0
    path = home / ".claude" / "settings.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for event in ("UserPromptSubmit", "PreToolUse"):
        for group in payload["hooks"][event]:
            for entry in group["hooks"]:
                entry["command"] += " && echo extra"
    path.write_text(json.dumps(payload), encoding="utf-8")
    check, _ = doctor(home, skill, project)
    assert "已接线（" not in check["detail"], check

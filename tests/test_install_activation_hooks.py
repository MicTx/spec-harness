"""Incremental activation wiring (task-install-diagnostics, installer half).

``scripts/install_activation_hooks.py`` registers the activation guard into a
Claude settings file the user owns. Contract: ``on`` / ``off`` / ``preserve``
(default), only entries it owns are touched, unknown fields and third-party
hooks and permissions stay byte-equivalent, a broken file is refused, writes
are atomic with a backup, installs are idempotent and survive path moves, and a
failed write leaves the original intact. Every test uses a temporary HOME.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install_activation_hooks.py"
MARKER = "hooks/claude_activation_guard.py"

FOREIGN = {
    "permissions": {"allow": ["Bash(git status)"], "deny": ["Bash(rm -rf *)"], "defaultMode": "default"},
    "env": {"KEEP": "1"},
    "model": "opus",
    "unknownFutureField": {"nested": [1, 2, 3]},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo third-party-pre"}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo third-party-prompt"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "echo third-party-stop"}]}],
    },
}


def run(home, mode, *extra, root=None, scope="user", project=None):
    env = {**os.environ, "HOME": str(home)}
    args = [sys.executable, str(SCRIPT), "--mode", mode, "--root", str(root or ROOT), "--scope", scope, *extra]
    if project is not None:
        args += ["--project-root", str(project)]
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)


def settings_path(home):
    return home / ".claude" / "settings.json"


def write_settings(home, payload):
    path = settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def commands(payload, event):
    out = []
    for group in payload.get("hooks", {}).get(event, []):
        out.extend(entry.get("command", "") for entry in group.get("hooks", []))
    return out


def own(payload, event):
    return [c for c in commands(payload, event) if MARKER in c]


@pytest.fixture()
def home(tmp_path):
    (tmp_path / "h").mkdir()
    return tmp_path / "h"


def test_on_registers_both_events_in_the_official_schema(home):
    completed = run(home, "on")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(settings_path(home).read_text())
    for event in ("UserPromptSubmit", "PreToolUse"):
        groups = payload["hooks"][event]
        assert all(isinstance(g.get("hooks"), list) and g["hooks"] for g in groups)
        assert len(own(payload, event)) == 1
    pre = next(g for g in payload["hooks"]["PreToolUse"] if any(MARKER in e["command"] for e in g["hooks"]))
    assert pre.get("matcher"), "PreToolUse must narrow to the supported write/dispatch tools"
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Agent", "Task", "SendMessage"):
        assert tool in pre["matcher"]
    assert "Bash" not in pre["matcher"].split("|")


def test_default_mode_is_preserve_and_writes_nothing(home):
    completed = run(home, "preserve")
    assert completed.returncode == 0
    assert not settings_path(home).exists()
    status = json.loads(completed.stdout)
    assert status["mode"] == "preserve" and status["changed"] is False


def test_preserve_leaves_an_existing_file_byte_identical(home):
    path = write_settings(home, FOREIGN)
    before = path.read_bytes()
    run(home, "preserve")
    assert path.read_bytes() == before
    assert not list(path.parent.glob("settings.json.bak-*"))


def test_on_keeps_every_foreign_field_and_hook(home):
    write_settings(home, FOREIGN)
    assert run(home, "on").returncode == 0
    payload = json.loads(settings_path(home).read_text())
    for key in ("permissions", "env", "model", "unknownFutureField"):
        assert payload[key] == FOREIGN[key]
    assert "echo third-party-pre" in commands(payload, "PreToolUse")
    assert "echo third-party-prompt" in commands(payload, "UserPromptSubmit")
    assert commands(payload, "Stop") == ["echo third-party-stop"]
    assert not own(payload, "Stop")


def test_on_twice_is_idempotent(home):
    run(home, "on")
    first = settings_path(home).read_bytes()
    second = run(home, "on")
    assert second.returncode == 0
    assert settings_path(home).read_bytes() == first
    payload = json.loads(first)
    assert len(own(payload, "PreToolUse")) == 1 and len(own(payload, "UserPromptSubmit")) == 1


def test_off_removes_only_its_own_entries(home):
    write_settings(home, FOREIGN)
    run(home, "on")
    assert run(home, "off").returncode == 0
    payload = json.loads(settings_path(home).read_text())
    assert not own(payload, "PreToolUse") and not own(payload, "UserPromptSubmit")
    assert payload["hooks"] == FOREIGN["hooks"]
    for key in ("permissions", "env", "model", "unknownFutureField"):
        assert payload[key] == FOREIGN[key]


def test_off_on_a_clean_file_removes_the_empty_hooks_section(home):
    run(home, "on")
    run(home, "off")
    payload = json.loads(settings_path(home).read_text())
    assert "hooks" not in payload


def test_off_without_a_settings_file_is_a_noop(home):
    completed = run(home, "off")
    assert completed.returncode == 0
    assert not settings_path(home).exists()
    assert json.loads(completed.stdout)["changed"] is False


def test_install_survives_a_moved_install_root(home, tmp_path):
    first = tmp_path / "old-root"
    second = tmp_path / "new-root"
    for target in (first, second):
        (target / "hooks").mkdir(parents=True)
        (target / "scripts").mkdir()
        (target / "hooks/claude_activation_guard.py").write_text("# guard\n")
        (target / "scripts/assess_spec_activation.py").write_text("# assess\n")
        (target / "scripts/activation_state.py").write_text("# state\n")
    run(home, "on", root=first)
    run(home, "on", root=second)
    payload = json.loads(settings_path(home).read_text())
    assert len(own(payload, "PreToolUse")) == 1
    assert str(second) in own(payload, "PreToolUse")[0] and str(first) not in own(payload, "PreToolUse")[0]
    run(home, "off", root=second)
    assert not own(json.loads(settings_path(home).read_text()), "PreToolUse")


@pytest.mark.parametrize("content", ["{broken", "[]", '"str"', "null"])
def test_broken_or_non_object_settings_are_refused_untouched(home, content):
    path = settings_path(home)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    completed = run(home, "on")
    assert completed.returncode == 2
    assert path.read_text(encoding="utf-8") == content
    assert not list(path.parent.glob("settings.json.bak-*"))


def test_non_object_hooks_section_is_refused(home):
    write_settings(home, {"hooks": ["x"]})
    before = settings_path(home).read_bytes()
    assert run(home, "on").returncode == 2
    assert settings_path(home).read_bytes() == before


def test_write_creates_a_backup_and_leaves_no_temp_files(home):
    path = write_settings(home, FOREIGN)
    original = path.read_bytes()
    run(home, "on")
    backups = list(path.parent.glob("settings.json.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert not list(path.parent.glob("*.tmp"))


def test_failed_write_leaves_the_original_intact(home):
    path = write_settings(home, FOREIGN)
    before = path.read_bytes()
    path.parent.chmod(0o500)
    try:
        completed = run(home, "on")
    finally:
        path.parent.chmod(0o700)
    assert completed.returncode != 0
    assert path.read_bytes() == before


def test_dry_run_reports_without_writing(home):
    write_settings(home, FOREIGN)
    before = settings_path(home).read_bytes()
    completed = run(home, "on", "--dry-run")
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["dry_run"] is True
    assert settings_path(home).read_bytes() == before


def test_project_scope_writes_only_the_project_file(home, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    completed = run(home, "on", scope="project", project=project)
    assert completed.returncode == 0, completed.stderr
    assert (project / ".claude" / "settings.json").is_file()
    assert not settings_path(home).exists()


def test_project_scope_requires_a_project_root(home):
    assert run(home, "on", scope="project").returncode == 2


def test_mode_is_required_to_be_explicit_for_changes(home):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT)],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        timeout=30,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["mode"] == "preserve"
    assert not settings_path(home).exists()


def test_a_missing_guard_script_refuses_to_register(home, tmp_path):
    empty = tmp_path / "empty-root"
    (empty / "scripts").mkdir(parents=True)
    completed = run(home, "on", root=empty)
    assert completed.returncode == 2
    assert not settings_path(home).exists()


def test_registered_command_runs_the_real_guard(home):
    run(home, "on")
    payload = json.loads(settings_path(home).read_text())
    command = own(payload, "UserPromptSubmit")[0]
    event = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "x", "cwd": str(home)})
    completed = subprocess.run(command, shell=True, input=event, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0 and "assess_spec_activation.py" in completed.stdout


def test_permissions_allow_and_deny_survive_on_and_off_byte_for_byte(home):
    """The installer must never add, drop, or reorder a user's permission rules."""
    write_settings(home, FOREIGN)
    original = json.loads(settings_path(home).read_text())["permissions"]
    run(home, "on")
    assert json.loads(settings_path(home).read_text())["permissions"] == original
    run(home, "off")
    assert json.loads(settings_path(home).read_text())["permissions"] == original
    run(home, "on")
    assert json.dumps(json.loads(settings_path(home).read_text())["permissions"], sort_keys=False) == json.dumps(
        original, sort_keys=False
    )


def test_a_file_without_permissions_never_gains_any(home):
    write_settings(home, {"model": "opus"})
    run(home, "on")
    assert "permissions" not in json.loads(settings_path(home).read_text())


# --- a symlinked settings file (dotfile managers) must stay a symlink ----------------


def link_settings(home, tmp_path, payload):
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir(parents=True)
    real.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    link = settings_path(home)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)
    return link, real


def test_symlinked_settings_stay_symlinks_and_the_target_is_updated(home, tmp_path):
    link, real = link_settings(home, tmp_path, FOREIGN)
    assert run(home, "on").returncode == 0
    assert link.is_symlink(), "the user's symlink must not be replaced by a regular file"
    assert link.resolve() == real.resolve()
    payload = json.loads(real.read_text())
    assert len(own(payload, "PreToolUse")) == 1
    assert payload["permissions"] == FOREIGN["permissions"]


def test_symlinked_settings_off_restores_the_target(home, tmp_path):
    link, real = link_settings(home, tmp_path, FOREIGN)
    run(home, "on")
    run(home, "off")
    assert link.is_symlink()
    assert json.loads(real.read_text())["hooks"] == FOREIGN["hooks"]


def test_dangling_symlink_is_refused_without_creating_anything(home, tmp_path):
    link = settings_path(home)
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "missing" / "settings.json")
    completed = run(home, "on")
    assert completed.returncode == 2
    assert not (tmp_path / "missing").exists()


def test_backup_of_a_symlinked_settings_is_a_real_copy_of_the_content(home, tmp_path):
    link, real = link_settings(home, tmp_path, FOREIGN)
    original = real.read_bytes()
    run(home, "on")
    backups = list(link.parent.glob("settings.json.bak-*")) + list(real.parent.glob("settings.json.bak-*"))
    assert len(backups) == 1 and not backups[0].is_symlink() and backups[0].read_bytes() == original


# --- ownership is the registered command, not any text that mentions the marker -----


def test_a_foreign_command_that_merely_mentions_the_marker_is_left_alone(home):
    foreign = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": f"echo docs about {MARKER}"}]}]}}
    write_settings(home, foreign)
    run(home, "on")
    run(home, "off")
    assert json.loads(settings_path(home).read_text()) == foreign


def test_a_non_list_event_value_is_refused_not_overwritten(home):
    write_settings(home, {"hooks": {"PreToolUse": "not-a-list"}})
    before = settings_path(home).read_bytes()
    completed = run(home, "on")
    assert completed.returncode == 2
    assert settings_path(home).read_bytes() == before


def test_a_root_with_shell_metacharacters_cannot_inject_through_the_command(home, tmp_path):
    """Run the registered command exactly as a shell would, in a directory we then inspect."""
    root = tmp_path / 'we$(touch PWNED)ird `touch TICKED` "q"'
    for rel in ("hooks/claude_activation_guard.py", "scripts/assess_spec_activation.py", "scripts/activation_state.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("import sys\nsys.stdin.read()\nprint('RAN')\n", encoding="utf-8")
    assert run(home, "on", root=root).returncode == 0
    command = own(json.loads(settings_path(home).read_text()), "UserPromptSubmit")[0]
    workdir = tmp_path / "work"
    workdir.mkdir()
    done = subprocess.run(command, shell=True, input="{}", capture_output=True, text=True, timeout=30, cwd=workdir)
    assert done.stdout.strip() == "RAN", done.stderr
    assert not (workdir / "PWNED").exists() and not (workdir / "TICKED").exists(), "the shell expanded the root"


def test_installs_written_with_the_older_quoting_are_still_recognised_and_migrated(home):
    legacy = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": f'python3 "/old/root/{MARKER}"', "timeout": 10}]}
            ],
            "PreToolUse": [
                {"matcher": "Edit", "hooks": [{"type": "command", "command": f'python3 "/old/root/{MARKER}"'}]}
            ],
        }
    }
    write_settings(home, legacy)
    assert run(home, "on").returncode == 0
    payload = json.loads(settings_path(home).read_text())
    assert len(own(payload, "UserPromptSubmit")) == 1 and len(own(payload, "PreToolUse")) == 1
    assert "/old/root/" not in json.dumps(payload)
    run(home, "off")
    assert "hooks" not in json.loads(settings_path(home).read_text())

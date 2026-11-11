import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import install_hooks  # noqa: E402
from adapters import HOSTS, capabilities, render_config  # noqa: E402
from install_hooks import install_session_hooks  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from doctor import main as doctor_main  # noqa: E402


def test_every_installer_host_is_in_adapter_matrix():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    expected = "claude claude-desktop codex devin cursor gemini grok opencode openclaw hermes pi zcode dsh".split()
    hosts = {host for host in expected if re.search(rf"\b{re.escape(host)}\b", text)}
    assert hosts == set(expected)
    assert hosts == set(HOSTS)


def test_twelve_hosts_have_hooks_and_desktop_does_not():
    enabled = [host for host in HOSTS if capabilities(host)]
    assert len(enabled) == 12
    assert capabilities("claude-desktop") == ()


def test_doctor_defaults_exclude_desktop_without_hook_support(tmp_path, capsys):
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(tmp_path))
        assert doctor_main(["--root", str(tmp_path), "--host", "claude-desktop"]) == 1
        assert json.loads(capsys.readouterr().out)["hosts"][0]["host"] == "claude-desktop"
        assert doctor_main(["--root", str(tmp_path)]) == 1
        assert all(item["host"] != "claude-desktop" for item in json.loads(capsys.readouterr().out)["hosts"])


def test_claude_config_uses_protocol_shape_and_bash_matcher(tmp_path):
    rendered = render_config("claude", tmp_path / "spec_hook.py")
    pre = rendered["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "Bash"
    assert pre["hooks"][0]["type"] == "command"
    assert "--host claude pre_tool" in pre["hooks"][0]["command"]


def test_config_install_is_idempotent_and_preserves_custom_hooks(tmp_path):
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir()
    target.write_text(
        json.dumps({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "custom"}]}]}}),
        encoding="utf-8",
    )
    first = install_session_hooks("claude", home=tmp_path)
    before = target.read_text(encoding="utf-8")
    second = install_session_hooks("claude", home=tmp_path)
    assert first == second
    assert "custom" in before
    assert before == target.read_text(encoding="utf-8")


def test_fresh_config_install_records_ownership_and_stays_idempotent(tmp_path):
    target = install_session_hooks("claude", home=tmp_path)
    first = target.read_text(encoding="utf-8")
    first_settings = json.loads(first)
    assert first_settings["x-spec-owned"]["commands"] == [
        f"python3 {ROOT / 'hooks' / 'spec_hook.py'} --host claude {event}" for event in capabilities("claude")
    ]
    assert all(len(entries) == 1 for entries in first_settings["hooks"].values())

    install_session_hooks("claude", home=tmp_path)
    second_settings = json.loads(target.read_text(encoding="utf-8"))
    assert second_settings == first_settings
    assert target.read_text(encoding="utf-8") == first


def test_config_install_migrates_legacy_ownership_without_external_hook_event_keys(tmp_path, monkeypatch):
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir()
    legacy = "python3 /tmp/spec_hook.py --host claude stop"
    target.write_text(
        json.dumps(
            {
                "hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": legacy}]}]},
                "x-spec-owned": {"PreToolUse": "old", "Stop": legacy},
            }
        ),
        encoding="utf-8",
    )
    installed_root = tmp_path / "installed" / "spec"
    monkeypatch.setattr(install_hooks, "HOOKS", installed_root / "hooks")

    install_session_hooks("claude", home=tmp_path, installed_root=installed_root)
    settings = json.loads(target.read_text(encoding="utf-8"))
    assert legacy not in target.read_text(encoding="utf-8")
    assert "PreToolUse" not in settings["x-spec-owned"]
    assert all("PreToolUse" not in key for key in settings if key != "hooks")
    hook_path = installed_root / "hooks" / "spec_hook.py"
    assert settings["x-spec-owned"] == {
        "commands": [f"python3 {hook_path} --host claude {event}" for event in capabilities("claude")]
    }
    assert all(str(installed_root) in command for command in settings["x-spec-owned"]["commands"])


def test_same_second_installs_preserve_distinct_backups(tmp_path, monkeypatch):
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir()
    first_content = json.dumps({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"command": "first"}]}]}})
    second_content = json.dumps({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"command": "second"}]}]}})
    target.write_text(first_content, encoding="utf-8")
    fixed_now = datetime.now(timezone.utc).replace(microsecond=0)

    class FixedDatetime:
        @staticmethod
        def now(_timezone):
            return fixed_now

    monkeypatch.setattr(install_hooks, "datetime", FixedDatetime)
    install_session_hooks("claude", home=tmp_path)
    target.write_text(second_content, encoding="utf-8")
    install_session_hooks("claude", home=tmp_path)

    backups = sorted(target.parent.glob(target.name + ".spec-backup-*"))
    assert len(backups) == 2
    assert {backup.read_text(encoding="utf-8") for backup in backups} == {first_content, second_content}


def test_shim_install_copies_dispatcher_and_rejects_foreign_file(tmp_path):
    install_session_hooks("opencode", home=tmp_path)
    target = tmp_path / ".config" / "opencode" / "plugins" / "spec.ts"
    assert "spec-harness" in target.read_text(encoding="utf-8")
    assert target.with_name("spec_hook.py").is_file()
    target.write_text("// custom\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-Spec shim"):
        install_session_hooks("opencode", home=tmp_path)

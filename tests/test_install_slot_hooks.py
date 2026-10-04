"""install_slot_hooks 安装器测试：官方 matcher 组 schema、旧格式迁移、幂等、保留他人、回滚。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# install_slot_hooks 依赖同目录的 slot_registry：把 scripts/ 放进 sys.path
sys.path.insert(0, str(REPO_ROOT / "scripts"))
spec_loader = importlib.util.spec_from_file_location(
    "install_slot_hooks", REPO_ROOT / "scripts" / "install_slot_hooks.py"
)
install_slot_hooks = importlib.util.module_from_spec(spec_loader)
loader_spec = importlib.util.spec_from_file_location("slot_registry", REPO_ROOT / "scripts" / "slot_registry.py")
sys.modules.setdefault("slot_registry", importlib.util.module_from_spec(loader_spec))
loader_spec.loader.exec_module(sys.modules["slot_registry"])
spec_loader.loader.exec_module(install_slot_hooks)

SLOT_EVENTS = {
    "Stop": ["hooks/loop_stop_guard.py"],
    "TeammateIdle": ["hooks/loop_teammate_gate.py"],
    "TaskCompleted": ["hooks/loop_teammate_gate.py"],
}
SLOT_DIR = REPO_ROOT / "slots" / "team-loop"


def collect_commands(items: list[Any]) -> list[str]:
    """事件数组（官方 matcher 组与旧版裸条目混排）里的全部 command。"""
    cmds: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        inner = item.get("hooks")
        if isinstance(inner, list):
            cmds.extend(e["command"] for e in inner if isinstance(e, dict) and "command" in e)
        elif "command" in item:
            cmds.append(item["command"])
    return cmds


def test_install_writes_matcher_group_schema():
    """官方 schema：事件值是 matcher 组数组 [{matcher?, hooks: [条目, ...]}]，缺 hooks 包装会被整组忽略。"""
    payload, added = install_slot_hooks.apply_install({}, "team-loop", SLOT_DIR, SLOT_EVENTS)
    assert added == 3
    for event, rels in SLOT_EVENTS.items():
        groups = payload["hooks"][event]
        assert groups, event
        assert all(isinstance(g, dict) and isinstance(g.get("hooks"), list) and g["hooks"] for g in groups), event
        entries = [e for g in groups for e in g["hooks"]]
        assert len(entries) == len(rels), event
        assert all(e["type"] == "command" and e["timeout"] == 20 for e in entries), event
    entry = payload["hooks"]["Stop"][0]["hooks"][0]
    assert entry["command"] == f'python3 "{SLOT_DIR / "hooks" / "loop_stop_guard.py"}"'


def test_install_migrates_legacy_bare_entries():
    """旧版安装器写出的裸条目（无 hooks 包装、含旧安装根）重装时迁移为 matcher 组且不留重复。"""
    legacy_stop = 'python3 "/old/root/slots/team-loop/hooks/loop_stop_guard.py"'
    base = {"hooks": {"Stop": [{"type": "command", "command": legacy_stop, "timeout": 20}]}}
    payload, added = install_slot_hooks.apply_install(base, "team-loop", SLOT_DIR, SLOT_EVENTS)
    assert added == 3
    groups = payload["hooks"]["Stop"]
    assert len(groups) == 1
    assert collect_commands(groups) == [f'python3 "{SLOT_DIR / "hooks" / "loop_stop_guard.py"}"']


def test_idempotent_no_duplicates():
    first, _ = install_slot_hooks.apply_install({}, "team-loop", SLOT_DIR, SLOT_EVENTS)
    second, _ = install_slot_hooks.apply_install(json.loads(json.dumps(first)), "team-loop", SLOT_DIR, SLOT_EVENTS)
    for event in SLOT_EVENTS:
        own = [c for c in collect_commands(second["hooks"][event]) if "slots/team-loop/hooks/" in c]
        assert len(own) == 1, event


def test_preserves_foreign_entries():
    base = {"hooks": {"Stop": [{"type": "command", "command": "echo other-guard"}]}}
    payload, _ = install_slot_hooks.apply_install(json.loads(json.dumps(base)), "team-loop", SLOT_DIR, SLOT_EVENTS)
    items = payload["hooks"]["Stop"]
    assert {"type": "command", "command": "echo other-guard"} in items  # 他人条目原样保留
    assert any("loop_stop_guard" in c for c in collect_commands(items))


def test_remove_recognizes_relocated_paths():
    """相对标记身份：旧安装根的裸条目也能被 --remove 回收（路径无关）。"""
    relocated_stop = 'python3 "/old/installed/path/slots/team-loop/hooks/loop_stop_guard.py"'
    base = {
        "hooks": {
            "Stop": [
                {"type": "command", "command": relocated_stop},
                {"type": "command", "command": "echo keep-me"},
            ]
        }
    }
    payload, removed = install_slot_hooks.apply_remove(base, "team-loop", SLOT_DIR, SLOT_EVENTS)
    assert removed == 1
    assert collect_commands(payload["hooks"]["Stop"]) == ["echo keep-me"]


def test_remove_strips_only_own_entries_in_mixed_group():
    """官方组内混入他人条目时 --remove 只回收自己的，不整组丢弃。"""
    base = {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {"type": "command", "command": 'python3 "/x/slots/team-loop/hooks/loop_stop_guard.py"'},
                        {"type": "command", "command": "echo keep-me"},
                    ]
                }
            ]
        }
    }
    payload, removed = install_slot_hooks.apply_remove(base, "team-loop", SLOT_DIR, SLOT_EVENTS)
    assert removed == 1
    assert collect_commands(payload["hooks"]["Stop"]) == ["echo keep-me"]


def test_remove_cleans_empty_hooks_section():
    payload, removed = install_slot_hooks.apply_remove(
        {"hooks": {"Stop": [{"type": "command", "command": 'python3 "/x/slots/team-loop/hooks/loop_stop_guard.py"'}]}},
        "team-loop",
        SLOT_DIR,
        SLOT_EVENTS,
    )
    assert removed == 1
    assert "hooks" not in payload


def test_corrupted_settings_rejected(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{broken", encoding="utf-8")
    try:
        install_slot_hooks.load_settings(settings)
        raise AssertionError("应当拒绝损坏的 settings")
    except install_slot_hooks.InstallError:
        pass


def test_unknown_slot_rejected(tmp_path):
    try:
        install_slot_hooks.slot_dir_for(tmp_path, "no-such-slot")
        raise AssertionError("应当拒绝未知插槽")
    except install_slot_hooks.InstallError:
        pass


def test_write_settings_creates_backup(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    payload, _ = install_slot_hooks.apply_install({}, "team-loop", SLOT_DIR, SLOT_EVENTS)
    install_slot_hooks.write_settings(settings, payload)
    install_slot_hooks.write_settings(settings, payload)
    backups = list(settings.parent.glob("settings.json.bak-*"))
    assert len(backups) == 1

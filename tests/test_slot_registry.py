"""slot_registry 注册器测试：合法清单、缺文件、路径逃逸、未知事件、名字不一致。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("slot_registry", REPO_ROOT / "scripts" / "slot_registry.py")
slot_registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(slot_registry)

VALID_MANIFEST = {
    "name": "demo",
    "version": "1.0.0",
    "summary": "演示插槽",
    "scripts": ["scripts/tool.py"],
    "hooks": {"Stop": ["hooks/guard.py"]},
    "docs": ["README.md"],
    "tests": "tests",
}


def make_slot(tmp_path, manifest=None, files=("scripts/tool.py", "hooks/guard.py", "README.md")):
    slot_dir = tmp_path / "slots" / "demo"
    for rel in files:
        target = slot_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# demo\n", encoding="utf-8")
    (slot_dir / "tests").mkdir(parents=True, exist_ok=True)
    (slot_dir / "manifest.json").write_text(
        json.dumps(manifest or VALID_MANIFEST, ensure_ascii=False), encoding="utf-8"
    )
    return slot_dir


def test_valid_slot_has_no_problems(tmp_path):
    slot_dir = make_slot(tmp_path)
    assert slot_registry.validate_slot(slot_dir) == []
    info = slot_registry.slot_info(slot_dir, tmp_path)
    assert info["valid"] is True and info["name"] == "demo"


def test_missing_manifest(tmp_path):
    slot_dir = tmp_path / "slots" / "demo"
    slot_dir.mkdir(parents=True)
    problems = slot_registry.validate_slot(slot_dir)
    assert any("manifest.json" in p for p in problems)


def test_missing_declared_file_rejected(tmp_path):
    slot_dir = make_slot(tmp_path, files=("README.md",))  # 缺 scripts/tool.py 与 hooks/guard.py
    problems = slot_registry.validate_slot(slot_dir)
    assert any("script 不存在" in p for p in problems)
    assert any("hook 不存在" in p for p in problems)


def test_path_escape_rejected(tmp_path):
    bad = dict(VALID_MANIFEST, scripts=["../escape.py"])
    slot_dir = make_slot(tmp_path, manifest=bad)
    assert any("逃逸" in p for p in slot_registry.validate_slot(slot_dir))


def test_unknown_hook_event_rejected(tmp_path):
    bad = dict(VALID_MANIFEST, hooks={"NotAnEvent": ["hooks/guard.py"]})
    slot_dir = make_slot(tmp_path, manifest=bad)
    assert any("未知 hook 事件" in p for p in slot_registry.validate_slot(slot_dir))


def test_name_dir_mismatch_rejected(tmp_path):
    bad = dict(VALID_MANIFEST, name="other-name")
    slot_dir = make_slot(tmp_path, manifest=bad)
    assert any("不一致" in p for p in slot_registry.validate_slot(slot_dir))


def test_missing_required_field_rejected(tmp_path):
    bad = {k: v for k, v in VALID_MANIFEST.items() if k != "summary"}
    slot_dir = make_slot(tmp_path, manifest=bad)
    assert any("summary" in p for p in slot_registry.validate_slot(slot_dir))


def test_discover_ignores_dirs_without_manifest(tmp_path):
    make_slot(tmp_path)
    (tmp_path / "slots" / "not-a-slot").mkdir(parents=True)
    found = slot_registry.discover_slots(tmp_path)
    assert [p.name for p in found] == ["demo"]


def test_team_loop_slot_in_repo_is_valid():
    problems = slot_registry.validate_slot(REPO_ROOT / "slots" / "team-loop")
    assert problems == []


def test_workflow_runner_declares_fanout_script():
    manifest = json.loads((REPO_ROOT / "slots" / "workflow-runner" / "manifest.json").read_text(encoding="utf-8"))
    assert "scripts/workflow_fanout.py" in manifest["scripts"]
    assert slot_registry.validate_slot(REPO_ROOT / "slots" / "workflow-runner") == []


def test_non_string_asset_path_is_rejected(tmp_path):
    bad = dict(VALID_MANIFEST, scripts=["scripts/tool.py", 42])
    slot_dir = make_slot(tmp_path, manifest=bad)
    problems = slot_registry.validate_slot(slot_dir)
    assert any("必须是字符串" in problem for problem in problems)


def test_slot_symlink_is_rejected(tmp_path):
    slot_dir = make_slot(tmp_path)
    link = tmp_path / "slots" / "linked"
    try:
        link.symlink_to(slot_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        return
    problems = slot_registry.validate_slot(link)
    assert any("不能是符号链接" in problem for problem in problems)


def test_external_asset_symlink_is_rejected(tmp_path):
    slot_dir = make_slot(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("# outside\n", encoding="utf-8")
    target = slot_dir / "scripts" / "external.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError):
        return
    manifest = dict(VALID_MANIFEST, scripts=["scripts/external.py"])
    (slot_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    problems = slot_registry.validate_slot(slot_dir)
    assert any("符号链接" in problem or "逃逸" in problem for problem in problems)

#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""spec 插槽 hook 安装器：按插槽 manifest 注册 Claude Code hooks。

- 身份判定用相对标记 `slots/<name>/hooks/<脚本名>`：与安装根无关，
  重装/迁移后旧条目仍可被 `--remove` 识别回收。
- 幂等：重复安装只更新不重复；他人条目（官方组与旧版裸条目）原样保留不动；
  写前备份；损坏 settings 拒绝写。
- 官方 hooks schema：事件值是 matcher 组数组，组内 `hooks` 才是条目数组。
  旧版本安装器曾把本插槽条目直接写在事件数组下（缺 hooks 包装，Claude Code
  会整组忽略、hook 不生效）；重装时这些旧条目按标记识别回收，统一改写为官方组。
- 需要与 slot_registry.py 同目录放置，复用其 manifest 装载与校验。

用法：
  python3 scripts/install_slot_hooks.py --slot team-loop [--scope user|project]
  python3 scripts/install_slot_hooks.py --slot team-loop --remove
  python3 scripts/install_slot_hooks.py --slot team-loop --dry-run
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from slot_registry import REPO_ROOT, SlotError, load_manifest, validate_slot  # noqa: E402

HOOK_TIMEOUT_SECONDS = 20


class InstallError(Exception):
    pass


def slot_dir_for(root: Path, slot_name: str) -> Path:
    target = root / "slots" / slot_name
    if not target.is_dir():
        raise InstallError(f"插槽不存在: {slot_name}（{target}）")
    problems = validate_slot(target)
    if problems:
        raise InstallError(f"插槽 manifest 无效，拒绝安装: {slot_name}: " + "；".join(problems))
    return target


def settings_path(scope: str, project_root: Optional[Path]) -> Path:
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    if scope == "project":
        if project_root is None:
            raise InstallError("--scope project 需要 --project-root")
        target = project_root / ".claude" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    raise InstallError(f"未知 scope: {scope}")


def load_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"settings 损坏（拒绝写入）：{path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InstallError(f"settings 顶层不是对象，拒绝写入：{path}")
    return payload


def relative_markers(slot_name: str, hook_files: list[Path]) -> list[str]:
    """插槽身份标记：slots/<name>/hooks/<file>，与安装根无关。"""
    return [f"slots/{slot_name}/hooks/{f.name}" for f in hook_files]


def _entry_command(entry: Any) -> str:
    if isinstance(entry, dict):
        cmd = entry.get("command", "")
        return cmd if isinstance(cmd, str) else ""
    return ""


def _is_own(entry: dict[str, Any], markers: set[str]) -> bool:
    return any(m in _entry_command(entry) for m in markers)


def _split_items(items: Any, markers: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """事件数组拆成（自己的条目, 他人保留物）。

    官方 schema：事件 -> matcher 组数组，组内 hooks 才是条目：
      "Stop": [{"hooks": [{"type": "command", "command": "..."}]}]
    旧版本曾把条目直接写在事件数组下（缺 hooks 包装，Claude Code 忽略整组）。
    他人条目（组或裸条目）一律原样保留：他方安装器可能按原位置查找，
    迁移他人的位置会令其幂等判定失灵。只有带本插槽标记的条目才回收改写。
    """
    if not isinstance(items, list):
        return [], []
    own_entries: list[dict[str, Any]] = []
    kept_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            kept_items.append(item)
            continue
        inner = item.get("hooks")
        if isinstance(inner, list):
            # matcher 组：组内他人的条目留在原组，自己的拆走
            foreign = [e for e in inner if isinstance(e, dict) and not _is_own(e, markers)]
            own_entries.extend(e for e in inner if isinstance(e, dict) and _is_own(e, markers))
            if foreign:
                kept_items.append({**item, "hooks": foreign})
        elif _is_own(item, markers):
            own_entries.append(item)  # 旧版裸条目：识别回收
        else:
            kept_items.append(item)  # 他人的裸条目：原样保留
    return own_entries, kept_items


def apply_install(
    payload: dict[str, Any], slot_name: str, slot_dir: Path, hook_events: dict[str, list[str]]
) -> tuple[dict[str, Any], int]:
    """manifest 内 hooks 路径相对插槽根；命令写入绝对路径。"""
    hooks = payload.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError("settings.hooks 不是对象，拒绝写入")
    markers = set(relative_markers(slot_name, [slot_dir / rel for rels in hook_events.values() for rel in rels]))
    added = 0
    for event, rel_scripts in hook_events.items():
        own_entries, kept_items = _split_items(hooks.get(event), markers)
        new_group: dict[str, Any] = {"hooks": []}
        for rel in rel_scripts:
            new_group["hooks"].append(
                {"type": "command", "command": f'python3 "{slot_dir / rel}"', "timeout": HOOK_TIMEOUT_SECONDS}
            )
            added += 1
        # 自己的新组放在最前，他人条目（原组序）跟在后面；无条目的空组不写
        rewritten = [new_group, *kept_items] if kept_items or new_group["hooks"] else kept_items
        if rewritten:
            hooks[event] = rewritten
        else:
            hooks.pop(event, None)
    return payload, added


def apply_remove(
    payload: dict[str, Any], slot_name: str, slot_dir: Path, hook_events: dict[str, list[str]]
) -> tuple[dict[str, Any], int]:
    markers = set(relative_markers(slot_name, [slot_dir / rel for rels in hook_events.values() for rel in rels]))
    hooks = payload.get("hooks")
    removed = 0
    if not isinstance(hooks, dict):
        return payload, 0
    for event in list(hooks):
        own_entries, kept_items = _split_items(hooks.get(event), markers)
        removed += len(own_entries)
        if kept_items:
            hooks[event] = kept_items
        else:
            hooks.pop(event, None)
    if not hooks:
        payload.pop("hooks", None)
    return payload, removed


def write_settings(path: Path, payload: dict[str, Any]) -> None:
    backup = None
    if path.is_file():
        backup = path.with_name(path.name + f".bak-{int(time.time())}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_slot_hooks.py", description="按插槽 manifest 注册/移除 Claude Code hooks"
    )
    parser.add_argument("--slot", required=True)
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--scope", choices=["user", "project"], default="user")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--remove", action="store_true", help="移除该插槽注册的 hook 条目")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        root = Path(args.root).resolve()
        slot_dir = slot_dir_for(root, args.slot)
        manifest = load_manifest(slot_dir)
        hook_events = {event: list(rels) for event, rels in manifest["hooks"].items()}

        path = settings_path(args.scope, Path(args.project_root).resolve() if args.project_root else None)
        payload = load_settings(path)
        if args.remove:
            payload, changed = apply_remove(copy.deepcopy(payload), args.slot, slot_dir, hook_events)
            action = "removed"
        else:
            payload, changed = apply_install(payload, args.slot, slot_dir, hook_events)
            action = "installed"
        result = {"ok": True, "action": action, "count": changed, "settings": str(path), "dryRun": bool(args.dry_run)}
        if not args.dry_run:
            write_settings(path, payload)
            result["dryRun"] = False
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (InstallError, SlotError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

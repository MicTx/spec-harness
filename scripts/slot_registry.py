#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""spec 插槽注册器：枚举、展示与校验 `slots/<name>/manifest.json`。

插槽契约（详见 references/slots.md）：
- 每个插槽是 `slots/<name>/` 下的自包含目录，由 manifest.json 声明其
  scripts / hooks / docs / tests 资产。
- 注册器只做静态校验：字段齐全、声明文件存在、路径不得逃逸插槽目录、
  hook 事件必须是已知 Claude Code 事件。不做热加载。

用法：
  python3 scripts/slot_registry.py list [--root .]
  python3 scripts/slot_registry.py show --slot team-loop [--root .]
  python3 scripts/slot_registry.py validate [--root .]   # 逐插槽校验，任一无效 exit 2
"""

from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = (Path(__file__).resolve().parent.parent).resolve()
SLOTS_DIR_NAME = "slots"

REQUIRED_FIELDS = ("name", "version", "summary", "scripts", "hooks")
KNOWN_HOOK_EVENTS = (
    "UserPromptSubmit",
    "Stop",
    "StopFailure",
    "SubagentStop",
    "TeammateIdle",
    "TaskCompleted",
    "TaskCreated",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "PermissionDenied",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "PostCompact",
    "Notification",
    "PreModelSwitch",
    "PostModelSwitch",
)


class SlotError(Exception):
    """插槽 manifest 无效或路径非法。"""


def slots_root(root: Path) -> Path:
    return root / SLOTS_DIR_NAME


def _contained(slot_dir: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(slot_dir.resolve())
    except (ValueError, OSError):
        return False
    return True


def _validate_asset_path(slot_dir: Path, rel: object, kind: str) -> tuple[Path | None, str | None]:
    """Validate a manifest asset path before constructing or opening it.

    Manifest paths are untrusted input.  Keeping this check in one place avoids
    ``Path / rel`` TypeErrors and prevents symlinked assets from quietly
    resolving outside the slot.
    """
    if not isinstance(rel, str):
        return None, f"{kind} 路径必须是字符串: {rel!r}"
    if not rel or "\x00" in rel or any(ord(char) < 32 or ord(char) == 127 for char in rel):
        return None, f"{kind} 路径包含非法控制字符或为空: {rel!r}"
    candidate_rel = Path(rel)
    if candidate_rel.is_absolute() or ".." in candidate_rel.parts:
        return None, f"{kind} 路径逃逸插槽目录: {rel}"
    candidate = slot_dir / candidate_rel
    if not _contained(slot_dir, candidate):
        return None, f"{kind} 路径逃逸插槽目录: {rel}"
    try:
        relative = candidate.relative_to(slot_dir)
        current = slot_dir
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None, f"{kind} 不能通过符号链接访问: {rel}"
    except (OSError, ValueError):
        return None, f"{kind} 路径不可访问: {rel}"
    return candidate, None


def _check_asset(slot_dir: Path, rel: object, kind: str) -> str | None:
    target, problem = _validate_asset_path(slot_dir, rel, kind)
    if problem:
        return problem
    assert target is not None
    if not target.is_file() or not stat.S_ISREG(target.stat().st_mode):
        return f"{kind} 不存在或不是普通文件: {rel}"
    return None


def load_manifest(slot_dir: Path) -> dict[str, Any]:
    manifest_path = slot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SlotError(f"缺少 manifest.json: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SlotError(f"manifest.json 解析失败 {manifest_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SlotError(f"manifest.json 顶层必须是对象: {manifest_path}")
    return payload


def validate_slot(slot_dir: Path) -> list[str]:
    """返回问题清单；空列表即有效。"""
    problems: list[str] = []
    if slot_dir.is_symlink():
        return [f"插槽目录不能是符号链接: {slot_dir}"]
    if not slot_dir.is_dir():
        return [f"插槽目录不存在或不是目录: {slot_dir}"]
    try:
        manifest = load_manifest(slot_dir)
    except SlotError as exc:
        return [str(exc)]

    for field in REQUIRED_FIELDS:
        if field not in manifest:
            problems.append(f"缺少必填字段: {field}")
    if problems:
        return problems

    if not isinstance(manifest.get("name"), str):
        problems.append("name 必须是字符串")
    if not isinstance(manifest.get("version"), str):
        problems.append("version 必须是字符串")
    if not isinstance(manifest.get("summary"), str):
        problems.append("summary 必须是字符串")
    if isinstance(manifest.get("name"), str) and manifest["name"] != slot_dir.name:
        problems.append(f"name 与目录名不一致: {manifest['name']!r} != {slot_dir.name!r}")

    if not isinstance(manifest.get("scripts"), list) or not manifest["scripts"]:
        problems.append("scripts 必须是非空列表")
    else:
        for rel in manifest["scripts"]:
            problem = _check_asset(slot_dir, rel, "script")
            if problem:
                problems.append(problem)

    hooks = manifest.get("hooks")
    if not isinstance(hooks, dict):
        problems.append("hooks 必须是对象（事件 -> 脚本列表）；无 hook 的 slot 声明为空对象")
    else:
        for event, entries in hooks.items():
            if event not in KNOWN_HOOK_EVENTS:
                problems.append(f"未知 hook 事件: {event}")
            if not isinstance(entries, list) or not entries:
                problems.append(f"hooks.{event} 必须是非空列表")
                continue
            for rel in entries:
                problem = _check_asset(slot_dir, rel, "hook")
                if problem:
                    problems.append(problem)

    docs = manifest.get("docs", [])
    if not isinstance(docs, list):
        problems.append("docs 必须是列表")
    else:
        for rel in docs:
            problem = _check_asset(slot_dir, rel, "doc")
            if problem:
                problems.append(problem)

    tests = manifest.get("tests")
    if tests is not None:
        if not isinstance(tests, (str, list)):
            problems.append("tests 必须是目录字符串或列表")
        else:
            test_paths = [tests] if isinstance(tests, str) else tests
            for rel in test_paths:
                if not isinstance(rel, str):
                    problems.append(f"tests 路径必须是字符串: {rel!r}")
                    continue
                target, problem = _validate_asset_path(slot_dir, rel, "tests")
                if problem:
                    problems.append(problem)
                elif target is None or not target.is_dir():
                    problems.append(f"tests 目录不存在: {rel}")

    return problems


def discover_slots(root: Path) -> list[Path]:
    base = slots_root(root)
    if not base.is_dir():
        return []
    return sorted(
        p
        for p in base.iterdir()
        if (p.is_dir() or p.is_symlink()) and ((p / "manifest.json").is_file() or p.is_symlink())
    )


def slot_info(slot_dir: Path, root: Path) -> dict[str, Any]:
    manifest = load_manifest(slot_dir)
    problems = validate_slot(slot_dir)
    return {
        "name": manifest.get("name", slot_dir.name),
        "version": manifest.get("version", "?"),
        "summary": manifest.get("summary", ""),
        "triggers": manifest.get("triggers", []),
        "requires": manifest.get("requires", []),
        "dir": str(slot_dir),
        "valid": not problems,
        "problems": problems,
    }


def _emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slot_registry.py", description="spec 插槽注册器")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("list", "列出全部插槽与有效性"),
        ("validate", "逐插槽校验，任一无效 exit 2"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--root", default=str(REPO_ROOT))
    p = sub.add_parser("show", help="展示单个插槽")
    p.add_argument("--slot", required=True)
    p.add_argument("--root", default=str(REPO_ROOT))
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    slots = discover_slots(root)
    try:
        if args.command == "list":
            _emit([slot_info(p, root) for p in slots])
            return 0
        if args.command == "show":
            target = root / SLOTS_DIR_NAME / args.slot
            if not target.is_dir():
                print(f"error: 插槽不存在: {args.slot}", file=sys.stderr)
                return 2
            _emit(slot_info(target, root))
            return 0
        if args.command == "validate":
            if not slots:
                print("没有已注册插槽", file=sys.stderr)
                return 0
            all_valid = True
            for slot_dir in slots:
                info = slot_info(slot_dir, root)
                state = "OK" if info["valid"] else "INVALID"
                print(f"[{state}] {info['name']} v{info['version']}")
                for problem in info["problems"]:
                    print(f"  - {problem}", file=sys.stderr)
                all_valid = all_valid and info["valid"]
            return 0 if all_valid else 2
    except SlotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

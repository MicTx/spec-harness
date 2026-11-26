"""Idempotently install Spec-owned session hook entries with backups."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
from adapters import HOSTS, capabilities, config_path, hook_command, render_config  # noqa: E402

SHIM_SOURCES = {
    "opencode": HOOKS / "shims" / "opencode.ts",
    "pi": HOOKS / "shims" / "pi.ts",
    "openclaw": HOOKS / "shims" / "openclaw.ts",
    "hermes": HOOKS / "shims" / "hermes" / "__init__.py",
}


def _remove_commands(value, commands: set[str]):
    if isinstance(value, list):
        kept = []
        for item in value:
            if isinstance(item, dict) and item.get("command") in commands:
                continue
            cleaned = _remove_commands(item, commands)
            if isinstance(cleaned, dict) and cleaned.get("hooks") == []:
                continue
            if cleaned is not None:
                kept.append(cleaned)
        return kept
    if isinstance(value, dict):
        cleaned = {key: _remove_commands(item, commands) for key, item in value.items()}
        if "hooks" in cleaned and cleaned["hooks"] == []:
            return None
        return cleaned
    return value


def _uses_nested_hooks(entries: list) -> bool:
    return bool(entries) and isinstance(entries[0].get("hooks"), list)


def _owned_commands(owned: object) -> set[str]:
    if isinstance(owned, list):
        return {command for command in owned if isinstance(command, str)}
    if isinstance(owned, dict) and "commands" in owned:
        return _owned_commands(owned["commands"])
    if not isinstance(owned, dict):
        return set()
    return {command for command in owned.values() if isinstance(command, str)}


OWNERSHIP_KEY = "x-spec-owned"


def _ownership_target(host: str, config_target: Path) -> Path:
    """Strict host schemas require ownership in a sidecar."""
    if HOSTS[host].get("ownership") == "sidecar":
        return config_target.with_name(config_target.name + ".spec-owned.json")
    return config_target


def _read_inline_ownership(config_target: Path) -> dict:
    try:
        data = json.loads(config_target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    owned = data.get(OWNERSHIP_KEY, {}) if isinstance(data, dict) else {}
    return {OWNERSHIP_KEY: owned} if owned else {}


def _load_ownership(host: str, config_target: Path) -> set[str]:
    """Accept both legacy inline and sidecar ownership."""
    commands = _owned_commands(_read_inline_ownership(config_target).get(OWNERSHIP_KEY))
    sidecar = _ownership_target(host, config_target)
    if sidecar != config_target:
        try:
            commands |= _owned_commands(
                json.loads(sidecar.read_text(encoding="utf-8")).get(OWNERSHIP_KEY, {})  # type: ignore[union-attr]
            )
        except (OSError, ValueError):
            pass
    return commands


def _rendered_commands(rendered: dict) -> dict[str, str]:
    return {
        event: entries[0]["hooks"][0]["command"] if _uses_nested_hooks(entries) else entries[0]["command"]
        for event, entries in rendered["hooks"].items()
    }


def _merge_hook_config(host: str, existing: dict, rendered: dict, config_target: Path) -> dict:
    """Replace owned hooks without touching foreign commands."""
    owned = _load_ownership(host, config_target) | _owned_commands(existing.get(OWNERSHIP_KEY, {}))
    data = _remove_commands(existing, owned)
    data.pop(OWNERSHIP_KEY, None)
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("host hooks config must be a JSON object")
    previous = existing.get("hooks", {})
    for event in list(hooks):
        if not hooks[event] and isinstance(previous, dict) and previous.get(event):
            del hooks[event]
    for event, entries in rendered["hooks"].items():
        hooks.setdefault(event, []).extend(entries)
    data["hooks"] = hooks
    if _ownership_target(host, config_target) == config_target:
        data[OWNERSHIP_KEY] = {"commands": list(_rendered_commands(rendered).values())}
    return data


def _install_shim(host: str, target: Path, installed_root: Path) -> None:
    source = SHIM_SOURCES[host]
    if target.exists() and "spec-harness" not in target.read_text(encoding="utf-8"):
        raise ValueError(f"refusing to overwrite non-Spec shim: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    executable, dispatcher = hook_command(installed_root / "hooks" / "spec_hook.py")
    rendered = source.read_text(encoding="utf-8")
    if host == "hermes":
        rendered = rendered.replace('"__SPEC_DISPATCHER__"', json.dumps(str(dispatcher), ensure_ascii=True))
        rendered = rendered.replace('"__SPEC_EXECUTABLE__"', json.dumps(executable))
    rendered = rendered.replace("__SPEC_DISPATCHER__", json.dumps(str(dispatcher), ensure_ascii=True))
    rendered = rendered.replace("__SPEC_EXECUTABLE__", json.dumps(executable))
    target.write_text(rendered, encoding="utf-8")


def install_session_hooks(host: str, home: Path | None = None, installed_root: Path | None = None) -> Path | None:
    if host not in HOSTS or not capabilities(host):
        return None
    target = config_path(host)
    if home is not None:
        target = home / str(HOSTS[host]["config"]).removeprefix("~/")
    hook_root = installed_root.resolve() if installed_root is not None else Path(__file__).resolve().parent.parent
    if host in SHIM_SOURCES:
        _install_shim(host, target, hook_root)
        return target
    rendered = render_config(host, hook_root / "hooks" / "spec_hook.py")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_name(target.name + ".spec-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
        sequence = 1
        while backup.exists():
            backup = target.with_name(
                target.name + ".spec-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{sequence}"
            )
            sequence += 1
        shutil.copy2(target, backup)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"host config must be a JSON object: {target}")
        data = _merge_hook_config(host, data, rendered, target)
    else:
        data = _merge_hook_config(host, {}, rendered, target)
    sidecar = _ownership_target(host, target)
    if sidecar != target:
        record = {OWNERSHIP_KEY: {"commands": list(_rendered_commands(rendered).values())}}
        sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary = target.with_name(target.name + ".spec-tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="append", required=True)
    parser.add_argument("--installed-root", type=Path)
    args = parser.parse_args(argv)
    try:
        for host in args.host:
            target = install_session_hooks(host, installed_root=args.installed_root)
            if target:
                print(f"installed: {target}")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Idempotently install Spec-owned session hook entries with backups."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
from adapters import HOSTS, capabilities, config_path, render_config  # noqa: E402

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


def _merge_hook_config(existing: dict, rendered: dict) -> dict:
    owned = set(existing.get("x-spec-owned", {}).values())
    data = _remove_commands(existing, owned)
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("host hooks config must be a JSON object")
    for event, entries in rendered["hooks"].items():
        hooks.setdefault(event, []).extend(entries)
    data["hooks"] = hooks
    data["x-spec-owned"] = {
        event: entries[0]["hooks"][0]["command"] if _uses_nested_hooks(entries) else entries[0]["command"]
        for event, entries in rendered["hooks"].items()
    }
    return data


def _install_shim(host: str, target: Path) -> None:
    source = SHIM_SOURCES[host]
    if target.exists() and "spec-harness" not in target.read_text(encoding="utf-8"):
        raise ValueError(f"refusing to overwrite non-Spec shim: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    shutil.copy2(HOOKS / "spec_hook.py", target.parent / "spec_hook.py")


def install_session_hooks(host: str, home: Path | None = None) -> Path | None:
    if host not in HOSTS or not capabilities(host):
        return None
    target = config_path(host)
    if home is not None:
        target = home / str(HOSTS[host]["config"]).removeprefix("~/")
    if host in SHIM_SOURCES:
        _install_shim(host, target)
        return target
    rendered = render_config(host, HOOKS / "spec_hook.py")
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
        data = _merge_hook_config(data, rendered)
    else:
        data = rendered
    temporary = target.with_name(target.name + ".spec-tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def install_git_hooks(root: Path, source_dir: Path) -> list[Path]:
    """Install the two Git hooks without replacing project-owned hooks."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel", "--git-common-dir", "--git-path", "hooks"],
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode != 0:
        raise ValueError("not a Git repository")
    repo, common_value, hooks_value = result.stdout.splitlines()
    common = Path(common_value)
    if not common.is_absolute():
        common = (Path(repo) / common).resolve()
    hooks = Path(hooks_value)
    if not hooks.is_absolute():
        hooks = (Path(repo) / hooks).resolve()
    if not any(hooks == allowed or allowed in hooks.parents for allowed in (Path(repo).resolve(), common)):
        raise ValueError(f"refusing hooks path outside repository or Git common dir: {hooks}")
    hooks.mkdir(parents=True, exist_ok=True)
    checker = (source_dir.parent / "scripts" / "check_all.py").resolve()
    python = Path(sys.executable).resolve()
    installed = []
    for name in ("pre-commit", "pre-push"):
        content = (
            (source_dir / name)
            .read_text(encoding="utf-8")
            .replace("__SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER__", str(checker))
            .replace("__SPEC_PYTHON_PLACEHOLDER__", str(python))
        )
        target = hooks / name
        if target.exists() and target.read_text(encoding="utf-8") != content:
            raise ValueError(f"refusing to overwrite project-owned hook: {target}; chain it manually")
        target.write_text(content, encoding="utf-8")
        target.chmod(target.stat().st_mode | 0o111)
        installed.append(target)
    return installed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="append")
    parser.add_argument("--root", help="install Git hooks into this repository instead")
    args = parser.parse_args(argv)
    try:
        if args.root:
            for target in install_git_hooks(Path(args.root).resolve(), HOOKS):
                print(f"installed: {target}")
        else:
            if not args.host:
                parser.error("--host is required without --root")
            for host in args.host:
                target = install_session_hooks(host)
                if target:
                    print(f"installed: {target}")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

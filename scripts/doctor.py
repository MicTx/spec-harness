"""Environment, host session-hook and legacy Git-hook health checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import gitx
import install_hooks


def check(root: Path, hosts: list[str], fix: bool = False) -> dict:
    checks = []
    for host in hosts:
        target = install_hooks.config_path(host)
        wired = target.is_file() and any(
            name in target.read_text(encoding="utf-8") for name in ("spec_hook.py", "spec-hook.sh")
        )
        if fix and not wired:
            install_hooks.install_session_hooks(host)
            wired = True
        checks.append({"host": host, "wired": wired, "config": str(target)})
    active_v2 = [
        path.parent.name
        for path in (root / ".spec" / "specs").glob("*/spec.md")
        if "spec-format: 3" not in path.read_text(encoding="utf-8")
    ]
    retired = gitx.legacy_git_hooks(root, retire=True) if fix else []
    legacy = gitx.legacy_git_hooks(root)
    return {
        "passed": all(item["wired"] for item in checks) and not active_v2 and not legacy,
        "hosts": checks,
        "non_v3_active": active_v2,
        "legacy_git_hooks": legacy,
        "retired_git_hooks": retired,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--host", action="append", default=[])
    parser.add_argument("--fix", action="store_true")
    args = parser.parse_args(argv)
    default_hosts = [host for host in install_hooks.HOSTS if install_hooks.capabilities(host)]
    result = check(Path(args.root).resolve(), args.host or default_hosts, args.fix)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

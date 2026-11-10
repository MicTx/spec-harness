"""Environment and host session-hook health checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import install_hooks


def check(root: Path, hosts: list[str], fix: bool = False) -> dict:
    checks = []
    for host in hosts:
        target = install_hooks.config_path(host)
        wired = target.is_file() and "spec_hook.py" in target.read_text(encoding="utf-8")
        if fix and not wired:
            install_hooks.install_session_hooks(host)
            wired = True
        checks.append({"host": host, "wired": wired, "config": str(target)})
    active_v2 = [
        path.parent.name
        for path in (root / ".spec" / "specs").glob("*/spec.md")
        if "spec-format: 3" not in path.read_text(encoding="utf-8")
    ]
    return {
        "passed": all(item["wired"] for item in checks) and not active_v2,
        "hosts": checks,
        "non_v3_active": active_v2,
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

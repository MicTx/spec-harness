"""Project-level value charter used by planning and autonomous decisions."""

from __future__ import annotations

import re
from pathlib import Path

from package import parse_charter, primary_root

DEFAULTS = {
    "V1": "quality > maintainability > speed > cost",
    "V2": "adjacent-fixes",
    "V3": "package",
    "V4": "commit",
    "V5": "none-new",
    "V6": "keep-public-api",
    "V7": "park-and-continue",
}

ALLOWED = {
    "V2": {"strict", "adjacent-fixes", "refactor-ok"},
    "V3": {"package", "integration", "project"},
    "V4": {"commit", "merge", "push"},
    "V5": {"none-new", "dev-only", "allowed"},
    "V6": {"keep-public-api", "deprecate", "break-ok"},
    "V7": {"park-and-continue", "stop-and-ask"},
}


def load(root: Path) -> dict[str, str]:
    result = dict(DEFAULTS)
    path = primary_root(root) / ".spec" / "charter.md"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"- (V\d+)\s+[^:]+:\s*([^|]+)", line)
            if match and match[1] in DEFAULTS:
                result[match.group(1)] = match.group(2).strip()
    return result


def resolve(root: Path, spec: str) -> dict[str, str]:
    return {**load(root), **parse_charter(spec)}


def summary_line(root: Path, values: dict[str, str] | None = None) -> str:
    values = values or load(root)
    return "; ".join(f"{key}={values[key]}" for key in sorted(values))

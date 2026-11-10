"""Project-level value charter used by planning and autonomous decisions."""

from __future__ import annotations

import re
from pathlib import Path

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
    path = root / ".spec" / "charter.md"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"- (V\d+)\s+[^:]+:\s*([^|]+)", line)
            if match:
                result[match.group(1)] = match.group(2).strip()
    return result


def merge(root: Path, values: dict[str, str]) -> None:
    merged = {**load(root), **values}
    lines = ["# Spec Value Charter", ""]
    labels = ["priority", "scope", "verification", "finish", "dependencies", "compatibility", "blocked"]
    lines.extend(f"- V{index} {label}: {merged[f'V{index}']} | charter.md" for index, label in enumerate(labels, 1))
    (root / ".spec" / "charter.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def summary_line(root: Path) -> str:
    values = load(root)
    return "; ".join(f"{key}={values[key]}" for key in sorted(values))


def pick(root: Path, key: str) -> str:
    return load(root).get(key, "")

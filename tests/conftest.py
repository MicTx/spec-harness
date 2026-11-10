"""Suite-wide fixtures for tests/."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def historical_checkpoint_document(package_name: str, intent: str) -> dict:
    """Build an on-disk-format checkpoint document, checksum computed exactly
    like the retired writer did (locks the historical format contract)."""
    payload = {
        "version": 1,
        "package": package_name,
        "intent": intent,
        "createdAt": "2026-06-12T10:00:00+00:00",
        "snapshot": {
            "spec.md": "# snapshot - 项目范围\n",
            "tasks.md": "# snapshot 任务\n",
            "checklist.md": "# snapshot checklist\n",
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["checksum"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


@pytest.fixture()
def write_historical_checkpoint():
    """Write a valid historical ``update-checkpoint.json`` into a package.

    The checkpoint mechanism is retired; tests use this fixture to leave
    behind exactly the file the retired mechanism produced, so consumer
    surfaces (route/check/report) are exercised against real historical
    state instead of the deleted write path.
    """

    def _write(package_dir: Path, intent: str = "追加数据导出任务") -> Path:
        document = historical_checkpoint_document(package_dir.name, intent)
        path = package_dir / "update-checkpoint.json"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        return path

    return _write

"""Suite-wide fixtures for tests/.

AUD-20 (planning F5): host CLI session markers inherit into pytest runs and
flip environment-sensitive assertions — e.g. inside a Claude Code session the
inherited ``CLAUDECODE`` makes the nearest-ancestor walk in
``autorun_spawn.detect_session_host`` resolve ``claude`` and breaks
``test_session_detection_selects_running_host``. The autouse fixture clears
exactly the four named variables before each test so suite results depend
only on the tests themselves. Tests that deliberately set a marker via
``monkeypatch.setenv`` are unaffected: the fixture ``delenv`` runs first,
the test's ``setenv`` after.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

#: Exactly the host session markers from ``autorun_spawn.HOST_ENV_MARKERS``
#: plus the ``SPEC_AUTORUN_HOST`` override. Keep in sync deliberately; do not
#: widen the list without a failing-test anchor.
CLEARED_HOST_ENV_VARS = (
    "CLAUDECODE",
    "CODEX_SANDBOX",
    "PI_CODING_AGENT",
    "SPEC_AUTORUN_HOST",
)


@pytest.fixture(autouse=True)
def clean_host_session_markers(monkeypatch: pytest.MonkeyPatch):
    """Clear inherited host-session markers for every test in tests/."""
    for var in CLEARED_HOST_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


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

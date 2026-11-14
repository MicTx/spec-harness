"""Pin tests for the machine-readable package inventory (spec.py list)."""

import json
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo

ROOT = Path(__file__).resolve().parents[1]


def _packages(root: Path) -> list[dict]:
    result = cli(root, "list")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["packages"]


def test_list_active_empty_inventory(tmp_path):
    repo(tmp_path)
    (tmp_path / ".spec" / "specs").mkdir(parents=True)
    assert _packages(tmp_path) == []


def test_list_active_package_progress(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)

    (entry,) = _packages(tmp_path)

    assert entry["slug"] == SLUG
    assert entry["archived"] is False
    assert entry["branch"] == f"spec/{SLUG}"
    assert entry["frozen"] is False
    assert entry["tasks"] == {"total": 1, "checked": 0}
    assert entry["criteria"] == {"total": 1, "passed": 0}


def test_list_active_frozen_after_go(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0

    (entry,) = _packages(tmp_path)

    assert entry["frozen"] is True
    assert entry["archived"] is False


def test_list_archived_after_delivery(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "check").returncode == 0

    (entry,) = _packages(tmp_path)

    assert entry["slug"] == SLUG
    assert entry["archived"] is True
    assert entry["frozen"] is True
    assert entry["tasks"] == {"total": 1, "checked": 1}
    assert entry["criteria"] == {"total": 1, "passed": 1}


def test_docs_inventory_projection():
    execution = (ROOT / "references" / "exec.md").read_text(encoding="utf-8")
    assert "spec.py list" in execution

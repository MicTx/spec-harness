"""Pin tests for the machine-readable package inventory (spec.py list)."""

import json
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo

ROOT = Path(__file__).resolve().parents[1]


def _packages(root: Path) -> list[dict]:
    result = cli(root, "list")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["packages"]


def _historical_package(base: Path, name: str, tasks: str) -> None:
    """Write a pre-v3 package: no `spec-format: 3` marker in spec.md."""
    package = base / name
    package.mkdir(parents=True)
    (package / "spec.md").write_text(f"# {name}\n\nHistorical package, no v3 marker.\n", encoding="utf-8")
    (package / "tasks.md").write_text(tasks, encoding="utf-8")


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


def test_list_skips_historical_archive_without_format_marker(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    # A frozen pre-v3 archive: no `spec-format: 3` marker and task blocks
    # without `id:` fields, which parse_tasks rejects as `task_0001`.
    _historical_package(
        tmp_path / ".spec" / "specs" / "archive",
        "2025-12-31_audit-hud",
        "- [x] Audit the HUD\n  - boundary: scripts/hud_support.py\n  - verify: `true`\n",
    )

    (entry,) = _packages(tmp_path)

    # The v3 entry keeps its exact field shape next to the historical archive.
    assert entry == {
        "slug": SLUG,
        "archived": False,
        "branch": f"spec/{SLUG}",
        "frozen": False,
        "tasks": {"total": 1, "checked": 0},
        "criteria": {"total": 1, "passed": 0},
    }


def test_list_skips_parseable_historical_packages_without_format_marker(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    tasks = "- [x] Old task\n  - id: task-old\n  - boundary: app.py\n  - verify: `true`\n  - expect: green\n"
    specs = tmp_path / ".spec" / "specs"
    # One archived and one active package, both parseable but without the
    # marker: skipping is a marker membership test in both inventory loops.
    _historical_package(specs / "archive", "2026-01-01_old-archived", tasks)
    _historical_package(specs, "2026-01-02_old-active", tasks)

    entries = _packages(tmp_path)

    assert [entry["slug"] for entry in entries] == [SLUG]


def test_docs_inventory_projection():
    execution = (ROOT / "references" / "exec.md").read_text(encoding="utf-8")
    assert "spec.py list" in execution

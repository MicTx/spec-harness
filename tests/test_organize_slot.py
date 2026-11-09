"""The organize auditor is a full-only slot (C10).

The structure audit has no relationship to the task-package state machine, so
it left ``scripts/`` and lives at ``slots/organize/`` with a registry
manifest. ``slots/`` is core-excluded, so the slot ships only under the full
profile; no empty ``scripts/`` stub may linger just to satisfy imports.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from payload_contract import CORE_EXCLUDED_DIRS, copy_dirs_for_profile  # noqa: E402

SLOT = ROOT / "slots" / "organize"
ORGANIZE = SLOT / "scripts" / "organize_project_structure.py"
CONTRACTS = SLOT / "scripts" / "structure_contracts.py"
EXPORTER = ROOT / "scripts" / "export_skill_package.py"


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_slot_manifest_is_registered_and_valid():
    manifest = json.loads((SLOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "organize"
    assert manifest["scripts"] == [
        "scripts/organize_project_structure.py",
        "scripts/structure_contracts.py",
    ]
    assert manifest["hooks"] == {}

    validated = run(str(ROOT / "scripts" / "slot_registry.py"), "validate")
    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert "[OK] organize" in validated.stdout

    listed = json.loads(run(str(ROOT / "scripts" / "slot_registry.py"), "list").stdout)
    assert "organize" in {entry["name"] for entry in listed}


def test_no_runtime_script_stub_remains():
    assert ORGANIZE.is_file()
    assert CONTRACTS.is_file()
    # The scripts moved out of the kernel namespace instead of leaving
    # forwarding stubs behind for old imports.
    assert not (ROOT / "scripts" / "organize_project_structure.py").exists()
    assert not (ROOT / "scripts" / "structure_contracts.py").exists()


def test_slot_scripts_run_from_their_slot_location(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = run(str(ORGANIZE), "--root", str(project), "--check", "--format", "json", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "contracts" in payload and "architecture" in payload

    # structure_contracts resolves its support module from the slot location.
    imported = subprocess.run(
        [sys.executable, "-c", "import structure_contracts; print(structure_contracts.__name__)"],
        cwd=str(CONTRACTS.parent),
        capture_output=True,
        text=True,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout.strip() == "structure_contracts"


def test_slots_are_core_excluded_and_full_ships_them():
    assert "slots" in CORE_EXCLUDED_DIRS
    assert "slots" in copy_dirs_for_profile("full")
    assert "slots" not in copy_dirs_for_profile("core")


def test_export_carries_organize_only_in_full(tmp_path):
    for profile, expected in (("core", False), ("full", True)):
        output = tmp_path / profile
        result = run(str(EXPORTER), "--root", str(ROOT), "--output", str(output), "--profile", profile)
        assert result.returncode == 0, result.stderr
        assert (output / "slots" / "organize" / "manifest.json").is_file() is expected
        exported_script = output / ORGANIZE.relative_to(ROOT)
        assert exported_script.is_file() is expected

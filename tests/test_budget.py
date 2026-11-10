"""Size budgets for what the model must read and what the project must maintain.

Ceilings only move down. Raising one needs a PR that names the incident it
serves (references/engineering-philosophy.md, defense admission test).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
KB = 1024

SKILL_MAX = 6 * KB
REFERENCES_TOTAL_MAX = 64 * KB
STAGE_MAX = 2 * KB
STAGES = ("new", "run", "check", "done", "push", "doctor")
SCRIPTS_LINES_MAX = 20_000
MODULE_LINE_BUDGETS = {
    "spec_package_support.py": 1200,
    "spec_verify.py": 500,
    "check_spec_package.py": 450,
    "init_spec_package.py": 400,
    "route_spec_package.py": 350,
    "complete_spec_package.py": 450,
    "push_spec_package.py": 600,
    "check_all_spec_packages.py": 400,
    "doctor_spec_environment.py": 500,
}


@pytest.mark.parametrize("name,maximum", MODULE_LINE_BUDGETS.items())
def test_core_module_line_budget(name, maximum):
    lines = len((ROOT / "scripts" / name).read_text(encoding="utf-8").splitlines())
    assert lines <= maximum, (name, lines, maximum)


def test_public_docs_line_budgets():
    for name, maximum in (("README.md", 150), ("README-en.md", 150), ("docs/getting-started.md", 200)):
        assert len((ROOT / name).read_text(encoding="utf-8").splitlines()) <= maximum, name


def _size(path: Path) -> int:
    return len(path.read_bytes())


def test_skill_entry_fits_budget():
    assert _size(ROOT / "SKILL.md") <= SKILL_MAX


def test_stage_pages_fit_budget():
    stages = ROOT / "references" / "stages"
    for name in STAGES:
        assert _size(stages / f"{name}.md") <= STAGE_MAX, name
    assert {path.stem for path in stages.glob("*.md")} == set(STAGES)


def test_references_total_fits_budget():
    total = sum(_size(path) for path in (ROOT / "references").rglob("*.md"))
    assert total <= REFERENCES_TOTAL_MAX, total


def test_script_lines_fit_budget():
    lines = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in (ROOT / "scripts").glob("*.py"))
    assert lines <= SCRIPTS_LINES_MAX, lines

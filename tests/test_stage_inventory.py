"""Stage inventory contract: one list, every carrier derived from it.

``install.sh`` ``USER_STAGES`` is the engineering source of truth (see the
project engineering facts in ``AGENTS.md``). The root READMEs' host table and
stage bullets, the ``SKILL.md`` command inventory, the stage sections in
``references/commands.md``, and the consulted copy inside
``scripts/doctor_spec_environment.py`` must all agree with it — a stage added
or retired in one carrier only is drift, not a variant.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"
SKILL = ROOT / "SKILL.md"
COMMANDS = ROOT / "references" / "commands.md"
DOCTOR = ROOT / "scripts" / "doctor_spec_environment.py"

STAGE_ARRAY = re.compile(r"^USER_STAGES=\((?P<stages>[^)]*)\)", re.MULTILINE)
BACKTICKED = re.compile(r"`([a-z]+)`")
SKILL_TABLE_ROW = re.compile(r"^\| `/spec:([a-z]+)` \|", re.MULTILINE)
COMMANDS_SECTION = re.compile(r"^## `/spec:([a-z]+)`\s*$", re.MULTILINE)

README_STAGE_MARKERS = {
    "README.md": "**明确的入口**",
    "README-en.md": "**Explicit stages**",
}
README_COUNT_PATTERNS = {
    "README.md": re.compile(r"(\d+)\s*个阶段命令"),
    "README-en.md": re.compile(r"(\d+)\s+stage commands"),
}


def _install_stages() -> list[str]:
    match = STAGE_ARRAY.search(INSTALL.read_text(encoding="utf-8"))
    assert match, "install.sh must declare USER_STAGES"
    return match.group("stages").split()


def _doctor_stages() -> list[str]:
    spec = importlib.util.spec_from_file_location("stage_inventory_doctor", DOCTOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclass processing resolves the defining module through sys.modules.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return list(module.Doctor.USER_STAGES)


def _readme_stages(relative: str) -> list[str]:
    marker = README_STAGE_MARKERS[relative]
    for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
        if marker in line:
            return BACKTICKED.findall(line)
    raise AssertionError(f"{relative}: stage bullet with marker {marker!r} not found")


def test_install_stage_inventory_is_the_single_source():
    stages = _install_stages()
    assert len(stages) == len(set(stages)) >= 12
    assert {"goal", "autoplan", "autorun", "organize"} <= set(stages)
    assert _doctor_stages() == stages, "doctor's consulted stage list must mirror install.sh"


def test_root_readme_stage_bullets_match_the_installer():
    stages = _install_stages()
    for relative in README_STAGE_MARKERS:
        assert _readme_stages(relative) == stages, f"{relative} stage bullet drifted from install.sh"


def test_root_readme_host_table_states_the_derived_count():
    stages = _install_stages()
    for relative, pattern in README_COUNT_PATTERNS.items():
        match = pattern.search((ROOT / relative).read_text(encoding="utf-8"))
        assert match, f"{relative}: host table must state the stage-command count"
        assert int(match.group(1)) == len(stages), f"{relative}: stated count drifted from install.sh"


def test_command_inventories_cover_every_stage():
    stages = set(_install_stages())
    assert set(SKILL_TABLE_ROW.findall(SKILL.read_text(encoding="utf-8"))) == stages
    assert set(COMMANDS_SECTION.findall(COMMANDS.read_text(encoding="utf-8"))) == stages

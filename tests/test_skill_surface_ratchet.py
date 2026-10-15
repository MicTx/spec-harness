"""Skill-surface ratchet (F1, `.spec/specs/2026-10-14_refactor-skill-surface`).

Three frozen guarantees over the four measured surfaces of
``scripts/measure_skill_surface.py``:

- Byte ceilings: each surface stays at or under its ceiling — the measured
  post-implementation value rounded up to the next whole KB. Shrinking further
  is always allowed; regressing past the ceiling fails. Ceilings are hand
  values, not computed ones: tightening or loosening one is a conscious,
  reviewable edit.
- File-set freeze: ``references/`` carries no new files. The dedup law for
  this reduction was "decision contracts stay in SKILL, process detail moves
  to the existing stage pages, registry facts move to scripts" — a new
  reference file would fake the byte savings by moving prose into a fresh
  mandatory-read surface instead of deleting duplication. The scan is the
  filesystem, not ``git ls-files``, so an untracked new file trips it too.
- Entry-list freeze: SKILL.md's reference list stays complete. (Found by the
  F1 negative samples: deleting an entry tripped no guard — reachability is
  satisfied through 00-readme.md's links alone — while the list is part of
  the always-loaded surface.)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Post-implementation measured bytes, rounded up to the KB ceiling (F1, 2026-10-14).
CEILINGS: dict[str, int] = {
    "metadata": 1024,  # measured 291 B — SKILL.md frontmatter
    "skill": 12288,  # measured 11,582 B — SKILL.md whole file (baseline 17,017 B)
    "stages": 53248,  # measured 52,412 B — references/stages/*.md
    # (F1 baseline 53,066 B; F2 complete-verify pointers added, per-page ≤5,120 B holds)
    "templates": 4096,  # measured 3,201 B — init fence instantiation (baseline 3,201 B)
}

#: references/ tree snapshot at ratchet time (2026-10-14, F1).
REFERENCES_BASELINE: frozenset[str] = frozenset(
    {
        "references/00-readme.md",
        "references/changelog-guide.md",
        "references/commands.md",
        "references/engineering-philosophy.md",
        "references/handoff.md",
        "references/naming-and-commits.md",
        "references/orchestration.md",
        "references/output-contracts.md",
        "references/slots.md",
        "references/stages/autoplan.md",
        "references/stages/autorun.md",
        "references/stages/check.md",
        "references/stages/doctor.md",
        "references/stages/done.md",
        "references/stages/goal.md",
        "references/stages/new.md",
        "references/stages/organize.md",
        "references/stages/push.md",
        "references/stages/run.md",
        "references/stages/status.md",
        "references/stages/update.md",
        "references/storage-and-archive.md",
        "references/templates.md",
    }
)

#: SKILL.md reference-list labels at ratchet time (2026-10-14, F1).
SKILL_REFERENCE_BASELINE: frozenset[str] = frozenset(
    {
        "references/commands.md",
        "references/output-contracts.md",
        "references/templates.md",
        "references/orchestration.md",
        "references/slots.md",
        "references/storage-and-archive.md",
        "references/handoff.md",
        "references/naming-and-commits.md",
        "references/changelog-guide.md",
        "references/engineering-philosophy.md",
    }
)


def _measure() -> dict:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "measure_skill_surface.py"), "--json", "--root", str(ROOT)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_every_surface_stays_at_or_under_its_ceiling():
    report = _measure()
    measured = {surface["name"]: surface["bytes"] for surface in report["surfaces"]}
    assert set(measured) == set(CEILINGS), f"surface set drifted: {sorted(measured)}"
    over = {name: (measured[name], ceiling) for name, ceiling in CEILINGS.items() if measured[name] > ceiling}
    assert not over, f"skill surface regressed past its ratchet ceiling: {over}"


def test_references_tree_gains_no_new_files():
    on_disk = {path.relative_to(ROOT).as_posix() for path in (ROOT / "references").rglob("*") if path.is_file()}
    assert on_disk <= REFERENCES_BASELINE, (
        "references/ gained files outside the F1 baseline snapshot — moving prose into a "
        f"new mandatory-read surface fakes the reduction: {sorted(on_disk - REFERENCES_BASELINE)}"
    )


def test_skill_reference_list_stays_complete():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    entries = set(re.findall(r"^- \[`([^`]+)`\]", text, re.MULTILINE))
    assert entries == SKILL_REFERENCE_BASELINE, (
        "SKILL.md reference list drifted from the F1 baseline: "
        f"missing={sorted(SKILL_REFERENCE_BASELINE - entries)} added={sorted(entries - SKILL_REFERENCE_BASELINE)}"
    )

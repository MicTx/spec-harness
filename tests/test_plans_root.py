"""The spec-side plans-root contract: one root under ``.spec``, slug-named
units, linked both ways, archived units in the corresponding archive dir.

``.spec/plans/`` is the only planning root (mirroring ``.spec/specs/``): the
index lives at ``.spec/plans/README.md`` and stays checkbox-free. A planning
unit carries a spec-style dated slug (``YYYY-MM-DD_<verb>-<object>``) — a
single-round plan is one ``<slug>.md`` file kept 1:1 with its Development
Record once archived; a multi-round cluster is a same-named directory holding
``master.md`` (the checkbox index) plus ``NN-<slug>.md`` phase details and
needs no single-record correspondence (its master's checkboxes point at the
records). Delivered or retired units move to ``.spec/plans/archive/`` under
the same name. This module pins that shape so the convention cannot drift
silently.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / ".spec"
PLANS = SPEC / "plans"
INDEX = PLANS / "README.md"
ARCHIVE = PLANS / "archive"
RECORDS = SPEC / "specs" / "archive"

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
PHASE_PLAN_NAME = re.compile(r"^\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
RECORD_PLAN_NAME = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<verb>[a-z]+)-(?P<object>[a-z0-9-]+)\.md$")
RECORD_UNIT_NAME = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<verb>[a-z]+)-(?P<object>[a-z0-9-]+)$")
CHECKBOX = re.compile(r"^\s*[-*]\s+\[[ xX]\]", re.MULTILINE)
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")


def _plan_units(base: Path) -> list[Path]:
    """Top-level planning units (files and cluster directories) under ``base``."""
    return sorted(
        path for path in base.iterdir() if path.name != "README.md" and not (base == PLANS and path.name == "archive")
    )


def _plan_docs() -> list[Path]:
    return sorted(path for path in PLANS.rglob("*.md") if path != INDEX)


def _relative_links(path: Path) -> list[str]:
    return [
        target
        for target in MD_LINK.findall(path.read_text(encoding="utf-8"))
        if not target.startswith(EXTERNAL_PREFIXES)
    ]


def test_plan_index_exists_at_the_spec_side_root():
    assert INDEX.is_file(), "the plans root must carry its index at .spec/plans/README.md"
    assert "规划文档索引" in INDEX.read_text(encoding="utf-8")


def test_index_lists_every_plan_unit():
    text = INDEX.read_text(encoding="utf-8")
    linked = {
        (INDEX.parent / target).resolve()
        for target in MD_LINK.findall(text)
        if not target.startswith(EXTERNAL_PREFIXES)
    }

    def _listed(unit: Path) -> bool:
        if unit.resolve() in linked:
            return True
        # a cluster directory is listed through its master.md link
        return unit.is_dir() and (unit / "master.md").resolve() in linked

    unlisted = [unit.name for unit in _plan_units(PLANS) if not _listed(unit)]
    assert not unlisted, f"plan units missing from the index: {unlisted}"
    archived_unlisted = [unit.name for unit in _plan_units(ARCHIVE) if not _listed(unit)]
    assert not archived_unlisted, f"archived plan units missing from the index: {archived_unlisted}"


def test_plan_unit_names_follow_the_convention():
    for unit in _plan_units(PLANS) + _plan_units(ARCHIVE):
        base = ARCHIVE if unit.parent == ARCHIVE else PLANS
        role = "archived" if base == ARCHIVE else "live"
        if unit.is_file():
            assert RECORD_PLAN_NAME.match(unit.name), (
                f"{role} single-round plans must be named <YYYY-MM-DD>_<verb>-<object>.md, "
                f"got {unit.relative_to(ROOT).as_posix()}"
            )
            if base == ARCHIVE:
                assert (RECORDS / unit.stem).is_dir(), f"archived plan has no Development Record: {unit.stem}"
        else:
            assert RECORD_UNIT_NAME.match(unit.name), (
                f"{role} cluster directories must be named <YYYY-MM-DD>_<verb>-<object>, got {unit.name}"
            )
            assert (unit / "master.md").is_file(), f"cluster directory has no master.md: {unit.name}"
            for member in sorted(unit.glob("*.md")):
                if member.name == "master.md":
                    continue
                assert PHASE_PLAN_NAME.match(member.name), (
                    f"cluster phase details must be named NN-<slug>.md, got {member.relative_to(ROOT).as_posix()}"
                )


def test_archived_units_carry_status_and_backlinks():
    archived = _plan_units(ARCHIVE)
    assert archived, "the archive keeps the delivered/retired plan units"
    for unit in archived:
        if unit.is_file():
            text = unit.read_text(encoding="utf-8")
            assert re.search(r"^> 状态：(已交付|已退役)", text, re.MULTILINE), unit.name
            assert f"../../specs/archive/{unit.stem}/spec.md" in text, f"{unit.name} must link its record"
            assert "](../README.md)" in text, f"{unit.name} must link back to the plans index"
        else:
            text = (unit / "master.md").read_text(encoding="utf-8")
            assert re.search(r"^> 状态：(已交付|已退役)", text, re.MULTILINE), unit.name
            assert "](../../README.md)" in text, f"{unit.name}/master.md must link back to the plans index"


def test_plan_document_links_resolve():
    broken: list[str] = []
    for doc in [INDEX, *_plan_docs()]:
        for target in _relative_links(doc):
            if not (doc.parent / target).resolve().exists():
                broken.append(f"{doc.relative_to(ROOT).as_posix()}: {target}")
    assert not broken, f"plan documents carry unresolved links: {broken}"


def test_index_is_not_a_feature_backlog():
    """The index must not qualify as an autorun planning document by accident."""
    assert not CHECKBOX.search(INDEX.read_text(encoding="utf-8")), (
        ".spec/plans/README.md must stay a checkbox-free index"
    )

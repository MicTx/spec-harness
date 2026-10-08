"""The repository plans-root contract: one root, one naming rule, linked both ways.

``plans/`` is the only planning-document root: the index/master lives at
``plans/README.md``, live phase plans are ``plans/NN-<slug>.md``, delivered or
retired records move to ``plans/archive/<YYYY-MM-DD>_<verb>-<object>.md`` with a
name matching their Development Record. This module pins that shape so the
convention cannot drift silently.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLANS = ROOT / "plans"
INDEX = PLANS / "README.md"
ARCHIVE = PLANS / "archive"
RECORDS = ROOT / ".spec" / "specs" / "archive"

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
PHASE_PLAN_NAME = re.compile(r"^\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
RECORD_PLAN_NAME = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<verb>[a-z]+)-(?P<object>[a-z0-9-]+)\.md$")
CHECKBOX = re.compile(r"^\s*[-*]\s+\[[ xX]\]", re.MULTILINE)
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")


def _plan_docs() -> list[Path]:
    return sorted(path for path in PLANS.rglob("*.md") if path != INDEX)


def _relative_links(path: Path) -> list[str]:
    return [
        target
        for target in MD_LINK.findall(path.read_text(encoding="utf-8"))
        if not target.startswith(EXTERNAL_PREFIXES)
    ]


def test_plan_index_exists_at_the_root():
    assert INDEX.is_file(), "the plans root must carry its index at plans/README.md"
    assert "规划文档索引" in INDEX.read_text(encoding="utf-8")


def test_index_lists_every_plan_document():
    text = INDEX.read_text(encoding="utf-8")
    linked = {
        (INDEX.parent / target).resolve()
        for target in MD_LINK.findall(text)
        if not target.startswith(EXTERNAL_PREFIXES)
    }
    unlisted = [doc.relative_to(ROOT).as_posix() for doc in _plan_docs() if doc.resolve() not in linked]
    assert not unlisted, f"plan documents missing from the index: {unlisted}"


def test_plan_document_names_follow_the_convention():
    for doc in PLANS.glob("*.md"):
        if doc == INDEX:
            continue
        assert PHASE_PLAN_NAME.match(doc.name), (
            f"live plans must be named NN-<slug>.md, got {doc.relative_to(ROOT).as_posix()}"
        )
    for doc in ARCHIVE.glob("*.md"):
        match = RECORD_PLAN_NAME.match(doc.name)
        assert match, f"archived plans must be named <YYYY-MM-DD>_<verb>-<object>.md, got {doc.name}"
        assert (RECORDS / doc.stem).is_dir(), f"archived plan has no Development Record: {doc.stem}"


def test_archived_plans_carry_status_record_and_backlink():
    archived = sorted(ARCHIVE.glob("*.md"))
    assert archived, "the archive keeps the delivered/retired plan records"
    for doc in archived:
        text = doc.read_text(encoding="utf-8")
        assert re.search(r"^> 状态：(已交付|已退役)", text, re.MULTILINE), doc.name
        assert f"../.spec/specs/archive/{doc.stem}/spec.md" in text, f"{doc.name} must link its record"
        assert "](../README.md)" in text, f"{doc.name} must link back to the plans index"


def test_plan_document_links_resolve():
    broken: list[str] = []
    for doc in [INDEX, *_plan_docs()]:
        for target in _relative_links(doc):
            if not (doc.parent / target).resolve().exists():
                broken.append(f"{doc.relative_to(ROOT).as_posix()}: {target}")
    assert not broken, f"plan documents carry unresolved links: {broken}"


def test_index_is_not_a_feature_backlog():
    """The index must not qualify as an autorun planning document by accident."""
    assert not CHECKBOX.search(INDEX.read_text(encoding="utf-8")), "plans/README.md must stay a checkbox-free index"

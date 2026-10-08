"""Reference graph contract over the runtime carriers.

Two guarantees for the runtime documentation graph:

- Reachability: every active ``references/*.md`` page is linked from the entry
  (``SKILL.md``) or from the reading index (``references/00-readme.md``) as a
  relative Markdown link, and the entry's reference list states why the page
  exists. An unlinked page is an orphan; a linked-and-missing page is a broken
  edge.
- Resolution: every relative Markdown link in ``SKILL.md`` and
  ``references/*.md`` resolves inside the repository. Fenced blocks are
  stripped first, so template examples never masquerade as live links.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "SKILL.md"
REFERENCES = ROOT / "references"
INDEX = REFERENCES / "00-readme.md"

FENCED_BLOCK = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,}).*?\n.*?^\1[ \t]*$", re.MULTILINE | re.DOTALL)
MD_LINK = re.compile(r"\[[^\]]*\]\((?P<target>[^)\s]+)\)")
SKILL_REFERENCE_ENTRY = re.compile(
    r"^- \[`references/(?P<name>[^`]+)`\]\((?P<target>references/[^)]+)\)(?P<purpose>.*)$",
    re.MULTILINE,
)
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _prose(text: str) -> str:
    """Drop fenced blocks so code samples stay out of the graph scan."""
    return FENCED_BLOCK.sub("\n", text)


def _relative_targets(path: Path) -> list[str]:
    targets: list[str] = []
    for match in MD_LINK.finditer(_prose(_read(path))):
        target = match.group("target").split("#", 1)[0].strip()
        if not target or target.startswith(EXTERNAL_PREFIXES):
            continue
        targets.append(target)
    return targets


def _resolved(path: Path, target: str) -> Path:
    return (path.parent / target).resolve()


def _referenced_reference_names(path: Path) -> set[str]:
    names: set[str] = set()
    for target in _relative_targets(path):
        if target.startswith("references/"):
            names.add(Path(target).name)
    return names


def test_every_active_reference_is_reachable_from_the_entry():
    active = {path.name for path in REFERENCES.glob("*.md")}
    assert active, "the reference corpus must not collapse to an empty set"
    reachable = _referenced_reference_names(SKILL) | _referenced_reference_names(INDEX)
    orphans = sorted(active - reachable)
    assert not orphans, f"active references unreachable from the entry or reading index: {orphans}"


def test_entry_reference_list_states_a_purpose_for_every_page():
    entries = SKILL_REFERENCE_ENTRY.findall(_read(SKILL))
    assert entries, "SKILL.md must keep its reference list in `- [path](path) — purpose` form"
    for name, target, purpose in entries:
        assert target == f"references/{name}", f"entry link target drifted from its label: {name} -> {target}"
        assert purpose.strip().startswith("—"), f"entry for {name} must state its purpose after an em dash"
        assert len(purpose.strip()) > len("—"), f"entry for {name} carries no purpose text"
        assert (ROOT / target).is_file(), f"entry links a missing reference: {target}"


def test_relative_markdown_links_resolve_in_runtime_docs():
    documents = [SKILL, *sorted(REFERENCES.glob("*.md"))]
    broken: list[str] = []
    for document in documents:
        for target in _relative_targets(document):
            if not _resolved(document, target).exists():
                broken.append(f"{document.relative_to(ROOT).as_posix()}: {target}")
    assert not broken, f"runtime documentation carries unresolved relative links: {broken}"

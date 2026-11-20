"""Relative Markdown links resolve to existing files and headings.

This is the only documentation test: it checks structure, never wording.
History (CHANGELOG, ADRs) is exempt because it describes past layouts.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXEMPT = {"CHANGELOG.md"}
EXEMPT_DIRS = {".git", ".spec", "docs/adr", "tests/fixtures", "release"}
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^(```|~~~)")


def _docs() -> list[Path]:
    found = []
    for path in ROOT.rglob("*.md"):
        relative = path.relative_to(ROOT).as_posix()
        if path.name in EXEMPT or any(relative.startswith(prefix + "/") for prefix in EXEMPT_DIRS):
            continue
        found.append(path)
    return found


def _prose(text: str) -> str:
    kept, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(line)
    return "\n".join(kept)


def _anchors(path: Path) -> set[str]:
    anchors = set()
    for line in _prose(path.read_text(encoding="utf-8")).splitlines():
        if line.startswith("#"):
            title = line.lstrip("#").strip().lower()
            anchors.add(re.sub(r"[^\w\- ]", "", title).replace(" ", "-"))
    return anchors


def test_relative_links_resolve():
    broken = []
    for doc in _docs():
        for target in LINK.findall(_prose(doc.read_text(encoding="utf-8"))):
            if re.match(r"^[a-z]+:", target) or target.startswith("<"):
                continue
            file_part, _, anchor = target.partition("#")
            resolved = (doc.parent / file_part).resolve() if file_part else doc
            if not resolved.exists():
                broken.append(f"{doc.relative_to(ROOT)} -> {target}")
            elif anchor and resolved.suffix == ".md" and anchor.lower() not in _anchors(resolved):
                broken.append(f"{doc.relative_to(ROOT)} -> {target} (missing heading)")
    assert not broken, "\n".join(broken)


INDEXES = ("README.md", "README-en.md", "docs/README.md", "docs/README-en.md")
ROOT_USER_DOCS = ("README.md", "README-en.md", "CONTRIBUTING.md", "RELEASE.md", "SECURITY.md", "SUPPORT.md")


def _reachable() -> set[Path]:
    seen: set[Path] = set()
    stack = [ROOT / name for name in INDEXES if (ROOT / name).exists()]
    while stack:
        page = stack.pop()
        if page in seen:
            continue
        seen.add(page)
        for target in LINK.findall(_prose(page.read_text(encoding="utf-8"))):
            if re.match(r"^[a-z]+:", target) or target.startswith("<"):
                continue
            file_part = target.partition("#")[0]
            if not file_part:
                continue
            resolved = (page.parent / file_part).resolve()
            if resolved.suffix == ".md" and resolved.exists() and ROOT in resolved.parents and resolved not in seen:
                stack.append(resolved)
    return seen


def test_all_docs_are_reachable():
    expected = {path.resolve() for path in (ROOT / "docs").rglob("*.md")}
    expected |= {(ROOT / name).resolve() for name in ROOT_USER_DOCS if (ROOT / name).exists()}
    missing = sorted(path.relative_to(ROOT).as_posix() for path in expected - _reachable())
    assert not missing, "unreachable documentation pages:\n" + "\n".join(missing)

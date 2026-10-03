"""Public reader documentation contract tests."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PUBLIC_DOCS = (
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "RELEASE.md",
    "SECURITY.md",
    "SUPPORT.md",
    "docs/README.md",
    "docs/introduction.md",
    "docs/tutorial.md",
    "docs/git-workflow.md",
    "docs/git-workflow.en.md",
)


def test_public_docs_use_public_repository_links_and_current_identity():
    for relative in PUBLIC_DOCS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "git." + "mxk.dev" not in text
        assert "." + "maintainer/" not in text
        assert ".spec/" + "docs/" not in text
        assert "spec harness" in text.lower() or relative in {"docs/git-workflow.md", "docs/git-workflow.en.md"}


def test_bilingual_git_guides_cover_the_same_sections():
    def headings(relative: str) -> list[str]:
        return [
            line.lstrip("#").strip().lower()
            for line in (ROOT / relative).read_text(encoding="utf-8").splitlines()
            if line.startswith("#")
        ]

    zh = headings("docs/git-workflow.md")
    en = headings("docs/git-workflow.en.md")
    assert len(zh) == len(en)
    assert len(zh) >= 6


def test_public_doc_links_resolve_inside_repository():
    import re

    pattern = re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")
    for relative in PUBLIC_DOCS:
        source = ROOT / relative
        for target in pattern.findall(source.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            assert (source.parent / target).resolve().is_file(), f"{relative}: {target}"

"""Public reader documentation contract tests."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from export_public_repo import PUBLIC_ROOT_FILES  # noqa: E402  # intentional path setup

PUBLIC_DOCS = (
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "RELEASE.md",
    "SECURITY.md",
    "SUPPORT.md",
    "docs/README.md",
    "docs/README-en.md",
    "docs/introduction.md",
    "docs/tutorial.md",
    "docs/git-workflow.md",
    "docs/git-workflow.en.md",
)
PUBLIC_LINK_DOCS = ("README.md", "README-en.md", *PUBLIC_DOCS)

# Public entry documents outside the docs/ tree: they carry deployment or
# packaging entry notes and must resolve the same way, directory links included.
ENTRY_DOCS = ("server/README.md", "agent-plugin/README.md")

RELATIVE_LINK = re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")


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
    pattern = re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")
    for relative in PUBLIC_LINK_DOCS:
        source = ROOT / relative
        for target in pattern.findall(source.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            assert (source.parent / target).resolve().is_file(), f"{relative}: {target}"


def test_root_readmes_form_a_bilingual_public_pair():
    chinese = (ROOT / "README.md").read_text(encoding="utf-8")
    english = (ROOT / "README-en.md").read_text(encoding="utf-8")
    docs_index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    pattern = re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")
    assert "README-en.md" in pattern.findall(chinese)
    assert "README.md" in pattern.findall(english)
    assert "../README.md" in pattern.findall(docs_index)
    assert "../README-en.md" in pattern.findall(docs_index)

    def headings(text: str) -> list[str]:
        return [line for line in text.splitlines() if line.startswith("## ") or line.startswith("### ")]

    chinese_headings = headings(chinese)
    english_headings = headings(english)
    assert [heading.count("#") for heading in chinese_headings] == [heading.count("#") for heading in english_headings]
    assert "## 验证" in chinese_headings
    assert "## Verification" in english_headings
    assert [heading for heading in chinese_headings if heading.startswith("## ")][:5] == [
        "## 你会得到什么",
        "## 目录结构",
        "## 环境要求",
        "## 安装说明",
        "## 快速开始",
    ]
    assert [heading for heading in english_headings if heading.startswith("## ")][:5] == [
        "## What You Get",
        "## Repository Layout",
        "## Requirements",
        "## Installation",
        "## Quick Start",
    ]

    assert "README.md" in PUBLIC_ROOT_FILES
    assert "README-en.md" in PUBLIC_ROOT_FILES


def _controlled_slug_verbs() -> set[str]:
    """The slug verb table in references/naming-and-commits.md is the single source."""
    table = (ROOT / "references" / "naming-and-commits.md").read_text(encoding="utf-8")
    return set(re.findall(r"^\|\s*`([a-z]+)`\s*\|", table, re.MULTILINE))


def test_readme_slug_examples_use_controlled_verbs():
    verbs = _controlled_slug_verbs()
    assert {"add", "fix", "update"} <= verbs, "the controlled verb table must stay parseable"
    pattern = re.compile(r"\b\d{4}-\d{2}-\d{2}_([a-z0-9]+(?:-[a-z0-9]+)*)")
    for relative in ("README.md", "README-en.md"):
        slugs = pattern.findall((ROOT / relative).read_text(encoding="utf-8"))
        assert slugs, f"{relative} must keep at least one runnable slug example"
        for slug in slugs:
            verb = slug.split("-", 1)[0]
            assert verb in verbs, f"{relative}: slug example {slug!r} must start with a controlled verb"


def test_bilingual_docs_indexes_mirror_each_other():
    """The zh and en indexes carry the same structure and the same entry coverage."""
    chinese = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    english = (ROOT / "docs" / "README-en.md").read_text(encoding="utf-8")

    def headings(text: str) -> list[str]:
        return [line for line in text.splitlines() if line.startswith("#")]

    chinese_headings = headings(chinese)
    english_headings = headings(english)
    assert len(chinese_headings) == len(english_headings)
    assert [heading.count("#") for heading in chinese_headings] == [heading.count("#") for heading in english_headings]

    chinese_links = set(RELATIVE_LINK.findall(chinese))
    english_links = set(RELATIVE_LINK.findall(english))
    assert "README-en.md" in chinese_links, "Chinese index must switch to the English index"
    assert "README.md" in english_links, "English index must switch back to the Chinese index"

    required = {
        "introduction.md",
        "tutorial.md",
        "git-workflow.md",
        "git-workflow.en.md",
        "../README.md",
        "../README-en.md",
        "../CONTRIBUTING.md",
        "../RELEASE.md",
        "../SECURITY.md",
        "../SUPPORT.md",
        "../references/00-readme.md",
        "../server/README.md",
        "../agent-plugin/README.md",
    }
    assert required <= chinese_links, f"Chinese index misses entries: {sorted(required - chinese_links)}"
    assert required <= english_links, f"English index misses entries: {sorted(required - english_links)}"


def test_entry_docs_resolve_file_and_directory_links():
    """server/agent-plugin docs link runtime roots; directory entries must stay valid."""
    for relative in ENTRY_DOCS:
        source = ROOT / relative
        targets = [
            target
            for target in RELATIVE_LINK.findall(source.read_text(encoding="utf-8"))
            if not target.startswith(("http://", "https://", "mailto:"))
        ]
        assert targets, f"{relative} must link its entry points"
        for target in targets:
            resolved = (source.parent / target).resolve()
            assert resolved.exists(), f"{relative}: unresolved link {target}"
            if target.endswith("/"):
                assert resolved.is_dir(), f"{relative}: directory link {target} is not a directory"

    server_targets = set(RELATIVE_LINK.findall((ROOT / "server" / "README.md").read_text(encoding="utf-8")))
    assert {"../scripts", "../references"} <= server_targets, (
        "server/README.md must keep its runtime-root directory entries"
    )
    for target in ("../scripts", "../references"):
        assert (ROOT / "server" / target).resolve().is_dir(), f"server/README.md: {target} must resolve to a directory"

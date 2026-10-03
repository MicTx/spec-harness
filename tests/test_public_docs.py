"""Public reader documentation contract tests."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from export_public_repo import PUBLIC_ROOT_FILES

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
PUBLIC_LINK_DOCS = ("README.md", "README-en.md", *PUBLIC_DOCS)


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

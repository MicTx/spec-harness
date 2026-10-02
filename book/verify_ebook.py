#!/usr/bin/env python3
"""Verify the Agent Harness ebook skeleton, chapter contracts, and EPUB build."""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
DIST = ROOT / "dist"
EPUB = DIST / "from-zero-agent-harness.epub"

CHAPTERS: dict[str, tuple[str, str]] = {
    "00": ("00-preface.md", "前言：为什么 Agent 需要 Harness"),
    "01": ("01-agent-out-of-control.md", "第 1 章 智能体为什么会失控"),
    "03": ("03-four-principles.md", "第 3 章 四条原则：显式假设、最小实现、边界清晰、验证优先"),
    "04": ("04-task-package.md", "第 4 章 任务包：范围、任务、验收的同一真源"),
    "05": ("05-single-loop.md", "第 5 章 单线闭环：从一句话目标到可恢复执行"),
    "06": ("06-machine-gates.md", "第 6 章 机器门禁：不要相信「看起来完成」"),
    "07": ("07-git-landing.md", "第 7 章 Git 收尾：干净工作树、独立分支、提交与发布分离"),
    "a": ("a-command-map.md", "附录 A 命令与阶段对照"),
    "b": ("b-artifact-map.md", "附录 B 制品与目录地图"),
    "c": ("c-failure-modes.md", "附录 C 失败模式速查"),
}

REQUIRED_HEADINGS = ("场景", "原则", "最小实现", "反模式")
PLACEHOLDER_PATTERNS = (
    re.compile(r"一句话描述项目要解决什么问题"),
    re.compile(r"\bTODO\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bxxx\b", re.I),
    re.compile(r"占位符"),
    re.compile(r"待撰写"),
    re.compile(r"lorem ipsum", re.I),
)
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
HEADING_RE = re.compile(r"^#{1,3}\s+(\S.*)$", re.M)
MIN_CJK = 28000
MAX_CJK = 80000
MIN_CHAPTER_CJK = 1800
MIN_EPUB_BYTES = 20 * 1024


class CheckError(Exception):
    """Accumulated verification failure."""


def cjk_count(text: str) -> int:
    return len(CJK_RE.findall(text))


def heading_names(text: str) -> list[str]:
    # Headings inside code examples do not satisfy the chapter contract.
    lines = []
    fence = ""
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = ""
            continue
        if not fence:
            lines.append(line)
    return [match.group(1).strip() for match in HEADING_RE.finditer("\n".join(lines))]


def has_heading(names: list[str], needle: str) -> bool:
    return any(needle in name for name in names)


def read(path: Path) -> str:
    if not path.is_file():
        raise CheckError(f"missing file: {path.relative_to(ROOT.parent)}")
    return path.read_text(encoding="utf-8")


def check_placeholders(name: str, text: str) -> list[str]:
    errors: list[str] = []
    for pattern in PLACEHOLDER_PATTERNS:
        if pattern.search(text):
            errors.append(f"{name}: placeholder {pattern.pattern!r}")
    return errors


def check_skeleton() -> list[str]:
    errors: list[str] = []
    for relative in ("README.md", "metadata.yaml", "src/outline.md", "src/glossary.md"):
        path = ROOT / relative if not relative.startswith("src/") else SRC / Path(relative).name
        if relative == "README.md":
            path = ROOT / "README.md"
        elif relative == "metadata.yaml":
            path = ROOT / "metadata.yaml"
        if not path.is_file():
            errors.append(f"missing {relative}")
            continue
        errors.extend(check_placeholders(relative, path.read_text(encoding="utf-8")))
    outline = SRC / "outline.md"
    if outline.is_file():
        text = outline.read_text(encoding="utf-8")
        for filename, title in CHAPTERS.values():
            if filename not in text:
                errors.append(f"outline missing filename {filename}")
            if title not in text:
                errors.append(f"outline missing title {title}")
    glossary = SRC / "glossary.md"
    if glossary.is_file() and cjk_count(glossary.read_text(encoding="utf-8")) < 80:
        errors.append("glossary too short")
    return errors


def check_chapter(key: str) -> list[str]:
    filename, title = CHAPTERS[key]
    path = SRC / filename
    errors: list[str] = []
    try:
        text = read(path)
    except CheckError as exc:
        return [str(exc)]
    names = heading_names(text)
    if not names or title not in names[0]:
        errors.append(f"{filename}: first heading must contain {title!r}")
    for heading in REQUIRED_HEADINGS:
        if not has_heading(names, heading):
            errors.append(f"{filename}: missing heading {heading}")
    if key == "00":
        if not (has_heading(names, "如何读这本书") or has_heading(names, "练习")):
            errors.append(f"{filename}: preface needs 如何读这本书 or 练习")
    elif not has_heading(names, "练习"):
        errors.append(f"{filename}: missing heading 练习")
    count = cjk_count(text)
    if count < MIN_CHAPTER_CJK:
        errors.append(f"{filename}: only {count} CJK chars, need >= {MIN_CHAPTER_CJK}")
    errors.extend(check_placeholders(filename, text))
    return errors


def assemble_book_md(keys: list[str]) -> str:
    parts = [
        read(SRC / "outline.md").strip(),
        "",
        read(SRC / "glossary.md").strip(),
        "",
    ]
    for key in keys:
        parts.append(read(SRC / CHAPTERS[key][0]).strip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def validate_epub(path: Path) -> list[str]:
    """Check ZIP integrity, OPF resources, spine, XHTML links and chapter titles."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if archive.testzip() is not None:
                return ["EPUB CRC failure"]
            if archive.read("mimetype") != b"application/epub+zip":
                return ["EPUB mimetype invalid"]
            first = archive.infolist()[0]
            if first.filename != "mimetype" or first.compress_type != zipfile.ZIP_STORED:
                return ["EPUB mimetype must be first and uncompressed"]
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile")
            if rootfile is None:
                return ["EPUB rootfile missing"]
            opf_name = rootfile.attrib["full-path"]
            opf = ET.fromstring(archive.read(opf_name))
            items = {}
            xml_docs = {}
            for item in opf.findall(".//{*}manifest/{*}item"):
                resource = posixpath.normpath(posixpath.join(posixpath.dirname(opf_name), unquote(item.attrib["href"])))
                if resource not in names:
                    return [f"EPUB missing manifest resource: {resource}"]
                items[item.attrib["id"]] = resource
                if item.attrib.get("media-type") == "application/xhtml+xml":
                    xml_docs[resource] = ET.fromstring(archive.read(resource))
            spine = opf.findall(".//{*}spine/{*}itemref")
            if not spine or any(i.attrib.get("idref") not in items for i in spine):
                return ["EPUB invalid spine"]
            if any(items[i.attrib["idref"]] not in xml_docs for i in spine):
                return ["EPUB spine references non-XHTML resource"]
            ids = {name: {e.attrib["id"] for e in doc.iter() if "id" in e.attrib} for name, doc in xml_docs.items()}
            for name, doc in xml_docs.items():
                for element in doc.iter():
                    for attr in ("href", "src"):
                        href = element.attrib.get(attr)
                        if not href:
                            continue
                        parts = urlsplit(href)
                        if parts.scheme or parts.netloc:
                            continue
                        target = (
                            posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(parts.path)))
                            if parts.path
                            else name
                        )
                        if target not in names:
                            return [f"EPUB broken resource: {name} -> {href}"]
                        if parts.fragment and target in ids and unquote(parts.fragment) not in ids[target]:
                            return [f"EPUB broken fragment: {name} -> {href}"]
            headings = [
                " ".join("".join(e.itertext()).split())
                for doc in xml_docs.values()
                for e in doc.iter()
                if e.tag.rsplit("}", 1)[-1] == "h1"
            ]
            for _, title in CHAPTERS.values():
                if " ".join(title.split()) not in headings:
                    return [f"EPUB missing chapter heading: {title}"]
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        return [f"EPUB structure error: {exc}"]
    return []


def build_epub(book_md: Path) -> list[str]:
    DIST.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=DIST, suffix=".epub", prefix=".build-")
    os.close(fd)
    temporary = Path(name)
    try:
        cmd = [
            "pandoc",
            str(book_md),
            "--metadata-file",
            str(ROOT / "metadata.yaml"),
            "--css",
            str(ROOT / "epub.css"),
            "--toc",
            "--toc-depth=2",
            "-o",
            str(temporary),
        ]
        try:
            completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            return ["pandoc not found; previous EPUB, if present, is not a new build"]
        except subprocess.TimeoutExpired:
            return ["pandoc exceeded 120 seconds"]
        if completed.returncode != 0:
            return [f"pandoc failed: {completed.stderr.strip() or completed.stdout.strip()}"]
        if temporary.stat().st_size < MIN_EPUB_BYTES:
            return [f"epub too small: {temporary.stat().st_size} bytes"]
        errors = validate_epub(temporary)
        if errors:
            return errors
        os.replace(temporary, EPUB)
        return []
    finally:
        temporary.unlink(missing_ok=True)


def check_full() -> list[str]:
    errors = check_skeleton()
    keys = list(CHAPTERS)
    for key in keys:
        errors.extend(check_chapter(key))
    if errors:
        return errors
    book_md = SRC / "book.md"
    assembled = assemble_book_md(keys)
    book_md.write_text(assembled, encoding="utf-8")
    total = cjk_count(assembled)
    if total < MIN_CJK or total > MAX_CJK:
        errors.append(f"CJK count {total} not in {MIN_CJK}-{MAX_CJK}")
    if not errors:
        errors.extend(build_epub(book_md))
    return errors


def parse_chapter_keys(raw: str) -> list[str]:
    keys = [item.strip() for item in raw.split(",") if item.strip()]
    if not keys or len(keys) != len(set(keys)):
        raise CheckError("chapter selection must be nonempty and unique")
    unknown = [key for key in keys if key not in CHAPTERS]
    if unknown:
        raise CheckError(f"unknown chapter keys: {', '.join(unknown)}")
    return keys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--skeleton", action="store_true")
    group.add_argument("--chapters", default=None, help="comma-separated keys such as 00,01,02")
    args = parser.parse_args(argv)
    errors: list[str] = []
    try:
        if args.skeleton:
            errors.extend(check_skeleton())
        elif args.chapters is not None:
            errors.extend(check_skeleton())
            for key in parse_chapter_keys(args.chapters):
                errors.extend(check_chapter(key))
        else:
            errors.extend(check_full())
    except (CheckError, OSError, UnicodeError) as exc:
        errors.append(str(exc))
    if errors:
        print("FAIL")
        for item in errors:
            print(f"- {item}")
        return 1
    mode = "skeleton" if args.skeleton else (f"chapters {args.chapters}" if args.chapters else "full")
    extra = ""
    if not args.skeleton and not args.chapters and (SRC / "book.md").is_file():
        extra = f" cjk={cjk_count((SRC / 'book.md').read_text(encoding='utf-8'))} epub={EPUB.stat().st_size}"
    print(f"PASS {mode}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
First-principles structure audit for a project repository.

Produces the machine-verifiable facts behind the `/spec:organize` command:
a top-level inventory, the external reference graph, deprecated-structure
candidates, and declared-architecture consistency. The script is strictly
read-only; the semantic first-principles judgment stays with the agent,
and any structural change lands through the normal task-package gates.

Sections (all advisory except architecture consistency):

- Inventory: tracked top-level entries with file counts, bytes, and the
  last commit date that touched each entry.
- Reference graph: which tracked text files mention each top-level
  directory outside the directory itself.
- Deprecated candidates: unreferenced top-level directories, stale ones,
  large tracked binaries, zip-plus-extracted-dir duplication, and
  untracked paths not covered by ignore rules.
- Architecture consistency: every `Primary files:` path declared in
  `<specs-dir>/architecture/module-index.md` must exist on disk, and the
  `module-dag.md` mermaid graph must match `module-dag.mmd` (nodes and
  edges) and the module set of the index.
- Finding classes (still advisory): unreferenced files, unreferenced code
  files, and dangling documentation paths grouped by missing target.
  "Unreferenced" is one-hop reference counting, not reachability: any
  other live tracked file that imports or mentions a file keeps it, even
  if that file is itself dead, so a dead chain surfaces its head first.
  Import-shaped text in comments, docstrings, and string literals is not
  an import (a plain path mention there still counts, as in any text
  file). Python absolute imports resolve from the importer's `sys.path`
  root (the parent of its topmost `__init__.py` directory, or a script's
  own directory), else the repo root, else a topmost package of that name
  that is unique in the repo. JS/TS module specifiers (`import`/`export …
  from`, `import()`, `require()`, `jest.mock()`) resolve like tsc:
  relative to the importer, or through the nearest tsconfig/jsconfig
  `paths` (a matched pattern is final) or `baseUrl`. Files test runners
  collect by name and ambient `*.d.ts` declarations are never candidates.
  `build_archive_plan` only describes moves into
  `<specs-dir>/archive/retired/YYYY-MM-DD/`; it never writes or deletes.

Exit codes: 0 = report produced (and `--check` passed when given),
2 = `--check` found declared-architecture inconsistencies,
1 = usage or scan error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from spec_package_support import resolve_specs_root

DEFAULT_SPECS_DIR = ".spec"
DEFAULT_STALE_DAYS = 90
DEFAULT_LARGE_BYTES = 512 * 1024
MAX_REFERENCE_EXAMPLES = 8
MAX_CONTENT_BYTES = 2 * 1024 * 1024
MISSING = "missing"

TEXT_SUFFIXES = {
    ".cfg",
    ".cjs",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".mmd",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
BINARY_SUFFIXES = {
    ".avif",
    ".bin",
    ".epub",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".so",
    ".tar",
    ".webp",
    ".whl",
    ".xlsx",
    ".zip",
}
PATH_TOKEN = re.compile(r"^`([^`]+)`$")
PROTOCOL_ARTIFACT_NAMES = frozenset(
    {
        "plan.json",
        "run.json",
        "runs.md",
        "evidence.json",
        "spec.md",
        "tasks.md",
        "checklist.md",
        "workflow_fanout.py",
    }
)
CLASS_UNREFERENCED_FILE = "unreferenced-file"
CLASS_UNREFERENCED_CODE = "unreferenced-code"
CLASS_DANGLING_DOC = "dangling-doc-path"
CLASS_EXAMPLE_DOC = "example-doc-path"
PLAN_ARCHIVE_PREFIX = "plans/archive/"
ARCHIVE_ACTION = "archive"
CODE_SUFFIXES = {".cjs", ".js", ".jsx", ".mjs", ".py", ".sh", ".ts", ".tsx"}
ENTRY_BASENAMES = {"SKILL.md", "__init__.py", "__main__.py", "install.sh", "pyproject.toml"}
# Test files runners collect by name: pytest's test_*.py / *_test.py / conftest.py and
# jest/vitest's *.test.* / *.spec.* with the marker directly before a JS/TS extension.
RUNNER_DISCOVERED = re.compile(r"(^test_[^/]*\.py$|_test\.py$|^conftest\.py$|\.(?:test|spec)\.[cm]?[jt]sx?$)")
JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
# `.d.ts` resolves last, so a same-name `.js` keeps its import.
JS_RESOLVE_SUFFIXES = JS_SUFFIXES + (".d.ts",)
# For TS importers tsc substitutes the TS source for an emitted-extension specifier (`./x.js` -> `x.ts`).
TS_SOURCE_FOR = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
JS_CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
# Strings stay intact so a `//` inside a URL literal is not taken for a comment.
JS_COMMENT_OR_STRING = re.compile(
    r"""(?P<comment>//[^\n]*|/\*.*?\*/)|(?P<string>'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`)""",
    re.DOTALL,
)
# Specifier patterns run on a skeleton whose comments are blank and whose quote literals are
# `"<index>"` (templates ``` `` ```), so import-shaped text inside a comment or string never matches.
JS_FROM_SPECIFIER = re.compile(r"""\b(?:import|export)\b[^;'"`]*?\bfrom\s*"(\d+)\"""")
JS_BARE_IMPORT = re.compile(r"""\bimport\s*"(\d+)\"""")
JS_CALL_SPECIFIER = re.compile(r"""(?:\bimport|\brequire|\bjest\.mock)\s*\(\s*"(\d+)\"""")
JS_SPECIFIER_PATTERNS = (JS_FROM_SPECIFIER, JS_BARE_IMPORT, JS_CALL_SPECIFIER)
# Top-level shape of a `.d.ts`: ambient (global script or augmentation) or a module that needs an importer.
DTS_TOKEN = re.compile(
    r"""\bdeclare\s+(?:global\b|module\s*")|\bexport\s+as\s+namespace\b|[{}]|\bexport\b|\bimport\b(?!\s*\()"""
)
PY_COMMENT_OR_STRING = re.compile(
    r"#[^\n]*"
    r'|"""(?:\\.|[^\\])*?"""'
    r"|'''(?:\\.|[^\\])*?'''"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'",
    re.DOTALL,
)
JSONC_COMMENT_OR_STRING = re.compile(r"""(?P<comment>//[^\n]*|/\*.*?\*/)|(?P<string>"(?:\\.|[^"\\])*")""", re.DOTALL)
JSONC_TRAILING_COMMA_OR_STRING = re.compile(r"""(?P<comma>,)(?=\s*[}\]])|(?P<string>"(?:\\.|[^"\\])*")""")
PATH_MENTION = re.compile(
    r"(?<![A-Za-z0-9_./-])((?:[.]{1,2}/)?[A-Za-z_][A-Za-z0-9_./-]*\.[A-Za-z][A-Za-z0-9]*)(?![A-Za-z0-9_./-])"
)
FROM_IMPORT = re.compile(r"(?m)^\s*from\s+(\.*[A-Za-z_][A-Za-z0-9_.]*|\.+)\s+import\s+(?:\(([^)]*)\)|([^\n#]+))")
PLAIN_IMPORT = re.compile(r"(?m)^\s*import\s+([A-Za-z_][A-Za-z0-9_., \t]+)")
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCED_BLOCK = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,}).*?\n.*?^\1[ \t]*$", re.MULTILINE | re.DOTALL)
SCRIPT_BLACKLIST_BLOCK = re.compile(r"SCRIPT_BLACKLIST\s*=\s*\{([^}]*)\}", re.DOTALL)
PRIMARY_FILES = re.compile(r"^Primary files:\s*$", re.IGNORECASE)
SECTION_HEADING = re.compile(r"^##\s+(.+?)\s*$")
BULLET = re.compile(r"^\s*-\s+(.*)$")
DAG_EDGE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*-->\s*([A-Za-z0-9_]+)\b")
DAG_NODE = re.compile(r"\b([A-Za-z0-9_]+)\[([^\]]+)\]")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit project structure facts for /spec:organize (read-only).")
    parser.add_argument("--root", default=".", help="Project root to audit (default: current directory)")
    parser.add_argument("--specs-dir", default=DEFAULT_SPECS_DIR, help="Trusted relative specs directory under root")
    parser.add_argument(
        "--stale-days",
        type=int,
        default=DEFAULT_STALE_DAYS,
        help="Unreferenced entries older than this many days count as stale candidates",
    )
    parser.add_argument(
        "--large-bytes",
        type=int,
        default=DEFAULT_LARGE_BYTES,
        help="Tracked binary files above this size count as large-binary candidates",
    )
    parser.add_argument("--check", action="store_true", help="Exit 2 when declared-architecture consistency fails")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser.parse_args(argv)


def run_git(root: Path, *args: str) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def git_repo(root: Path) -> bool:
    if run_git(root, "rev-parse", "--is-inside-work-tree") is None:
        return False
    return True


def list_tracked(root: Path, is_git: bool) -> tuple[list[str], bool]:
    if is_git:
        output = run_git(root, "ls-files", "-z")
        if output is not None:
            return [name for name in output.split("\0") if name], True
    files: list[str] = []
    skip = {".git", "__pycache__", "dist", "build", "tmp", "node_modules", ".pytest_cache", ".ruff_cache"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in skip for part in rel.parts[:-1]) or rel.parts[0] in skip:
            continue
        files.append(str(rel))
    return sorted(files), False


def last_commit_date(root: Path, entry: str) -> Optional[str]:
    output = run_git(root, "log", "-1", "--format=%ad", "--date=short", "--", entry)
    if not output:
        return None
    return output.strip() or None


def is_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return True
    try:
        with open(path, "rb") as handle:
            return b"\0" in handle.read(8192)
    except OSError:
        return True


def read_text_bounded(path: Path) -> str:
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_CONTENT_BYTES + 1)
        return data[:MAX_CONTENT_BYTES].decode("utf-8", errors="replace")
    except OSError:
        return ""


def human_bytes(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}MB"
    if size >= 1024:
        return f"{size / 1024:.0f}KB"
    return f"{size}B"


@dataclass
class InventoryEntry:
    name: str
    kind: str
    files: int
    bytes: int
    last_commit: Optional[str]


@dataclass
class AuditReport:
    root: Path
    is_git: bool
    inventory: list[InventoryEntry] = field(default_factory=list)
    references: dict[str, int] = field(default_factory=dict)
    live_references: dict[str, int] = field(default_factory=dict)
    reference_examples: dict[str, list[str]] = field(default_factory=dict)
    unreferenced_dirs: list[str] = field(default_factory=list)
    stale_dirs: list[tuple[str, Optional[str]]] = field(default_factory=list)
    large_binaries: list[tuple[str, int]] = field(default_factory=list)
    zip_duplications: list[tuple[str, str]] = field(default_factory=list)
    untracked_not_ignored: list[str] = field(default_factory=list)
    unreferenced_files: list[str] = field(default_factory=list)
    unreferenced_code: list[str] = field(default_factory=list)
    dangling_doc_groups: dict[str, list[str]] = field(default_factory=dict)
    example_doc_groups: dict[str, list[str]] = field(default_factory=dict)
    declared_paths: list[str] = field(default_factory=list)
    missing_primary_files: list[str] = field(default_factory=list)
    dag_md_missing: bool = False
    dag_mmd_missing: bool = False
    dag_node_diff: list[str] = field(default_factory=list)
    dag_edge_diff: list[str] = field(default_factory=list)
    index_module_diff: list[str] = field(default_factory=list)

    @property
    def architecture_findings(self) -> int:
        return (
            len(self.missing_primary_files)
            + len(self.dag_node_diff)
            + len(self.dag_edge_diff)
            + len(self.index_module_diff)
        )

    def to_json(self) -> dict:
        return {
            "root": str(self.root),
            "is_git": self.is_git,
            "inventory": [
                {"name": e.name, "kind": e.kind, "files": e.files, "bytes": e.bytes, "last_commit": e.last_commit}
                for e in self.inventory
            ],
            "references": self.references,
            "live_references": self.live_references,
            "reference_examples": self.reference_examples,
            "deprecated_candidates": {
                "unreferenced_dirs": self.unreferenced_dirs,
                "stale_dirs": [{"name": n, "last_commit": d} for n, d in self.stale_dirs],
                "large_binaries": [{"path": p, "bytes": b} for p, b in self.large_binaries],
                "zip_duplications": [{"archive": a, "directory": d} for a, d in self.zip_duplications],
                "untracked_not_ignored": self.untracked_not_ignored,
            },
            "finding_classes": {
                CLASS_UNREFERENCED_FILE: self.unreferenced_files,
                CLASS_UNREFERENCED_CODE: self.unreferenced_code,
                CLASS_DANGLING_DOC: [
                    {"missing": missing, "docs": docs} for missing, docs in sorted(self.dangling_doc_groups.items())
                ],
                CLASS_EXAMPLE_DOC: [
                    {"missing": missing, "docs": docs} for missing, docs in sorted(self.example_doc_groups.items())
                ],
            },
            "architecture": {
                "declared_paths": self.declared_paths,
                "missing_primary_files": self.missing_primary_files,
                "dag_md_missing": self.dag_md_missing,
                "dag_mmd_missing": self.dag_mmd_missing,
                "dag_node_diff": self.dag_node_diff,
                "dag_edge_diff": self.dag_edge_diff,
                "index_module_diff": self.index_module_diff,
            },
        }


def build_inventory(root: Path, tracked: list[str], is_git: bool) -> list[InventoryEntry]:
    stats: dict[str, list[int]] = {}
    for name in tracked:
        top = name.split("/", 1)[0]
        entry = stats.setdefault(top, [0, 0])
        entry[0] += 1
        path = root / name
        try:
            entry[1] += path.stat().st_size
        except OSError:
            entry[1] += 0
    inventory: list[InventoryEntry] = []
    for name in sorted(stats):
        files, size = stats[name]
        kind = "dir" if files > 1 or not (root / name).is_file() else "file"
        commit = last_commit_date(root, name) if is_git else None
        inventory.append(InventoryEntry(name=name, kind=kind, files=files, bytes=size, last_commit=commit))
    return inventory


def is_historical_path(rel: str, specs_dir_name: str) -> bool:
    """Governance records and changelog history are evidence, not live consumers."""
    if rel == "CHANGELOG.md":
        return True
    if rel.startswith(PLAN_ARCHIVE_PREFIX):
        # Delivered/retired plan records under ``plans/archive/`` describe the
        # repository as it was; their paths are history, like ``.spec/`` records.
        return True
    top = rel.split("/", 1)[0]
    if top == specs_dir_name or top == ".spec":
        return True
    return False


def export_script_whitelist(root: Path) -> set[str]:
    """Runtime script entry points declared by an in-repo export blacklist.

    Generic projects have no exporter, so the set is empty. When
    ``scripts/export_skill_package.py`` defines ``SCRIPT_BLACKLIST``, every
    other ``scripts/*.py`` is an export entry point and is not an orphan.
    """
    exporter = root / "scripts" / "export_skill_package.py"
    if not exporter.is_file():
        return set()
    match = SCRIPT_BLACKLIST_BLOCK.search(read_text_bounded(exporter))
    blacklist = set(re.findall(r"""['"]([^'"]+)['"]""", match.group(1))) if match else set()
    names: set[str] = set()
    scripts = root / "scripts"
    if not scripts.is_dir():
        return names
    for path in scripts.glob("*.py"):
        if path.name in blacklist or path.name.startswith("_"):
            continue
        names.add(f"scripts/{path.name}")
    return names


def is_planning_record(rel: str) -> bool:
    """Live planning records under ``plans/`` describe future (or past) state.

    Their prose names deliverables that may not exist yet and existing files
    by basename; those mentions are planning evidence, not live wiring, so
    they never become dangling/example findings. Archived records were
    already historical via ``is_historical_path``; this extends the same
    ruling to live plans. Mentions from these docs still count toward the
    reference graph, so retirement detection keeps its inputs.
    """
    return rel.startswith("plans/")


def is_retired_candidate_excluded(rel: str, declared: set[str], whitelist: set[str]) -> bool:
    path = Path(rel)
    if path.name in ENTRY_BASENAMES:
        return True
    if "tests" in path.parts:
        return True
    if rel in declared or rel in whitelist:
        return True
    return False


def collapse_repo_path(rel: str) -> Optional[str]:
    raw = rel.strip()
    if not raw or raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        return None
    path = Path(raw)
    if path.is_absolute():
        return None
    parts: list[str] = []
    for part in path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        if "/" in part or "\\" in part or ":" in part:
            return None
        parts.append(part)
    if not parts:
        return None
    return "/".join(parts)


def normalize_mentioned_path(doc_rel: str, raw: str, root: Optional[Path] = None) -> Optional[str]:
    target = raw.strip().strip("<>")
    if not target or "://" in target or target.startswith(("#", "mailto:")):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0].strip()
    if not target or target.startswith("/"):
        return None
    # These names are protocol artifact labels in task contracts and examples.
    # Treat a bare label as a path only when it resolves to a real file; otherwise
    # a prose mention must not become a dangling filesystem finding.
    if "/" not in target and target in PROTOCOL_ARTIFACT_NAMES:
        candidate = (root / target).resolve() if root is not None else None
        if candidate is None or not candidate.is_file():
            return None
    looks_like_path = "/" in target or bool(
        re.search(r"\.(py|md|txt|sh|js|ts|toml|json|yml|yaml|mmd)$", target, re.IGNORECASE)
    )
    if not looks_like_path:
        return None
    if target.startswith("./") or target.startswith("../"):
        joined = (Path(doc_rel).parent / target).as_posix()
        return collapse_repo_path(joined)
    # A bare name is relative to the document's directory, then each ancestor
    # up to the repo root — first existing base wins. Kits and slot
    # packages all reference siblings this way; repo-root-only resolution
    # produced mass false "dangling" findings. Without a root (or when no
    # base exists on disk), fall back to the repo-root join so true
    # repo-level gaps still surface.
    bases = [Path(doc_rel).parent]
    bases.extend(bases[0].parents)
    if root is not None:
        for base in bases:
            candidate = base / target
            absolute = (root / candidate).resolve()
            try:
                absolute.relative_to(root)
            except ValueError:
                continue
            if absolute.exists():
                return collapse_repo_path(candidate.as_posix())
    return collapse_repo_path(target)


def package_of(rel: str) -> list[str]:
    parent = Path(rel).parent
    if str(parent) in ("", "."):
        return []
    return [part for part in parent.parts if part not in ("", ".")]


def resolve_from_import(importer: str, module: str, names: str) -> set[str]:
    """Turn a from-import into absolute module ids. Relative imports need the importer path."""
    resolved: set[str] = set()
    if module.startswith("."):
        parts = package_of(importer)
        if not parts:
            return resolved
        dots = len(module) - len(module.lstrip("."))
        remainder = module[dots:]
        climb = dots - 1
        if climb > len(parts):
            return resolved
        base = list(parts[: len(parts) - climb])
        base.extend(part for part in remainder.split(".") if part)
        parent = ".".join(base)
    else:
        parent = module
    if parent:
        resolved.add(parent)
    for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", names):
        if parent:
            resolved.add(f"{parent}.{name}")
        else:
            resolved.add(name)
    return resolved


def strip_py_comments_and_strings(content: str) -> str:
    """Blank comments, docstrings, and string literals; line breaks are kept so imports stay line-anchored."""
    return PY_COMMENT_OR_STRING.sub(lambda m: "\n" * m.group(0).count("\n"), content)


def collect_imported_modules(content: str, importer: str, reroot: Optional[Callable[[str], str]] = None) -> set[str]:
    """Imported module ids; `reroot` maps an absolute import to its repo-root dotted id."""
    code = strip_py_comments_and_strings(content)
    absolute = reroot or (lambda name: name)
    modules: set[str] = set()
    for match in FROM_IMPORT.finditer(code):
        names = match.group(2) if match.group(2) is not None else (match.group(3) or "")
        module = match.group(1)
        if not module.startswith("."):
            module = absolute(module)  # relative imports already resolve to repo-root ids
        modules.update(resolve_from_import(importer, module, names))
    for match in PLAIN_IMPORT.finditer(code):
        for part in match.group(1).split(","):
            token = part.strip().split()[0] if part.strip() else ""
            if token and token != "import":
                modules.add(absolute(token))
    return modules


def module_name_for(rel: str) -> str:
    return str(Path(rel).with_suffix("")).replace("\\", "/").replace("/", ".")


class PythonImportIndex:
    """Resolve an absolute import the way the importer's `sys.path` would.

    The importer's own root comes first: the parent of its topmost `__init__.py`
    directory, or its own directory for a plain script (`backend/app/x.py` has
    root `backend`, so `from app.y import z` is `backend.app.y`). Otherwise the
    repo root, then a topmost package of that name that is unique in the repo.
    Two services that each own a top-level `app` therefore never keep each
    other's modules alive.
    """

    def __init__(self, tracked: list[str]) -> None:
        self.packages = {
            str(PurePosixPath(rel).parent) for rel in tracked if PurePosixPath(rel).name == "__init__.py"
        } - {"."}
        self.provided: set[str] = set()
        for rel in tracked:
            if rel.endswith(".py"):
                parts = module_name_for(rel).split(".")
                self.provided.update(".".join(parts[: depth + 1]) for depth in range(len(parts)))
        tops: dict[str, set[str]] = {}
        for directory in self.packages:
            parts = directory.split("/")
            if not any("/".join(parts[:depth]) in self.packages for depth in range(1, len(parts))):
                tops.setdefault(parts[-1], set()).add("/".join(parts[:-1]))
        self.unique_top = {name: next(iter(roots)) for name, roots in tops.items() if len(roots) == 1}

    def import_root(self, rel: str) -> str:
        parts = PurePosixPath(rel).parent.parts
        for depth in range(1, len(parts) + 1):
            if "/".join(parts[:depth]) in self.packages:
                return "/".join(parts[: depth - 1])
        return "/".join(parts)

    def reroot(self, root: str, name: str) -> str:
        top = name.split(".", 1)[0]
        if root and f"{root.replace('/', '.')}.{top}" in self.provided:
            return f"{root.replace('/', '.')}.{name}"
        if top in self.provided or top not in self.unique_top:
            return name
        base = self.unique_top[top]
        return f"{base.replace('/', '.')}.{name}" if base else name


def join_repo_path(base: str, target: str) -> Optional[str]:
    """Join repo-relative posix paths; `""` is the repo root, None means it escaped the root."""
    parts = [part for part in base.split("/") if part] if base else []
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def resolve_js_candidate(
    base: Optional[str], tracked: set[str], directory_only: bool = False, ts_importer: bool = False
) -> Optional[str]:
    """Bundler order: the path itself, then an added extension, then `<dir>/index.*`.

    A TS importer first tries the TS source behind an emitted extension (`./x.js` -> `x.ts`), as tsc does.
    """
    if base is None:
        return None
    if not directory_only:
        stem, dot, ext = base.rpartition(".")
        if ts_importer and dot and "/" not in ext:
            for source in TS_SOURCE_FOR.get(f".{ext}", ()):
                if stem + source in tracked:
                    return stem + source
        if base in tracked:
            return base
        for suffix in JS_RESOLVE_SUFFIXES:
            if base + suffix in tracked:
                return base + suffix
    prefix = f"{base}/" if base else ""
    for suffix in JS_RESOLVE_SUFFIXES:
        if f"{prefix}index{suffix}" in tracked:
            return f"{prefix}index{suffix}"
    return None


def js_skeleton(content: str) -> tuple[str, list[tuple[int, int, str]], list[tuple[int, int]]]:
    """Code with comments blanked and quote literals replaced by `"<index>"` (templates by ``` `` ```).

    Returns the skeleton, the quote literals as (start, end, value), and the comment spans.
    """
    literals: list[tuple[int, int, str]] = []
    comments: list[tuple[int, int]] = []

    def replace(match: re.Match) -> str:
        if match.group("comment"):
            comments.append(match.span())
            return " "
        text = match.group(0)
        if text.startswith("`"):
            return "``"
        literals.append((match.start(), match.end(), text[1:-1]))
        return f'"{len(literals) - 1}"'

    return JS_COMMENT_OR_STRING.sub(replace, content), literals, comments


def js_specifier_literals(content: str) -> tuple[list[tuple[int, int, str]], list[tuple[int, int]]]:
    """Real module specifiers (`import … from`, `export … from`, `import 'x'`, `import()`, `require()`,
    `jest.mock()`) as (start, end, value), plus comments that hold an import statement."""
    skeleton, literals, comments = js_skeleton(content)
    indexes = {int(m.group(1)) for p in JS_SPECIFIER_PATTERNS for m in p.finditer(skeleton)}
    found = sorted(literals[index] for index in indexes if index < len(literals))
    import_comments = []
    for start, end in comments:
        body = content[start + 2 : end - 2] if content.startswith("/*", start) else content[start + 2 : end]
        # Backticks in a comment are markdown code spans, not template literals.
        inner_found, inner_comments = js_specifier_literals(body.replace("`", " "))
        if inner_found or inner_comments:
            import_comments.append((start, end))
    return found, import_comments


def dts_is_ambient(content: str) -> bool:
    """A `.d.ts` the compiler applies without any import: a global script (no top-level
    import/export) or one that augments (`declare global`, `declare module "x"`, `export as namespace`)."""
    depth, module = 0, False
    for match in DTS_TOKEN.finditer(js_skeleton(content)[0]):
        token = match.group(0)
        if token == "{":
            depth += 1
        elif token == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and token in ("import", "export"):
            module = True
        elif depth == 0:
            return True
    return not module


def parse_jsonc(text: str) -> object:
    """tsconfig/jsconfig JSON with comments and trailing commas; string contents are never touched."""
    text = JSONC_COMMENT_OR_STRING.sub(lambda m: " " if m.group("comment") else m.group(0), text.lstrip("\ufeff"))
    text = JSONC_TRAILING_COMMA_OR_STRING.sub(lambda m: "" if m.group("comma") else m.group(0), text)
    return json.loads(text)


@dataclass
class JsAliasConfig:
    """Effective `baseUrl` / `paths` of one tsconfig/jsconfig after `extends`."""

    base_url: Optional[str] = None
    paths: dict[str, list[str]] = field(default_factory=dict)
    paths_dir: Optional[str] = None


def load_js_config(root: Path, rel: str, chain: tuple[str, ...] = ()) -> JsAliasConfig:
    config = JsAliasConfig()
    if rel in chain:
        return config
    try:
        data = parse_jsonc(read_text_bounded(root / rel))
    except ValueError:
        return config
    if not isinstance(data, dict):
        return config
    config_dir = Path(rel).parent.as_posix()
    config_dir = "" if config_dir == "." else config_dir
    parents = data.get("extends")
    for parent in parents if isinstance(parents, list) else [parents]:
        if not isinstance(parent, str) or not parent.startswith("."):
            continue  # package-name extends live under node_modules: not resolved
        target = join_repo_path(config_dir, parent)
        if target is None:
            continue
        if not (root / target).is_file() and (root / f"{target}.json").is_file():
            target = f"{target}.json"
        if (root / target).is_file():
            inherited = load_js_config(root, target, chain + (rel,))
            if inherited.base_url is not None:
                config.base_url = inherited.base_url
            if inherited.paths:
                config.paths, config.paths_dir = inherited.paths, inherited.paths_dir
    options = data.get("compilerOptions")
    if isinstance(options, dict):
        if isinstance(options.get("baseUrl"), str):
            config.base_url = join_repo_path(config_dir, options["baseUrl"])
        if isinstance(options.get("paths"), dict):
            config.paths = {k: v for k, v in options["paths"].items() if isinstance(v, list)}
            config.paths_dir = config_dir
    return config


def nearest_js_config(
    root: Path,
    importer: str,
    tracked: set[str],
    by_dir: dict[str, Optional[str]],
    loaded: dict[str, JsAliasConfig],
) -> Optional[JsAliasConfig]:
    """The closest tsconfig.json / jsconfig.json at or above the importer's directory."""
    directory = Path(importer).parent.as_posix()
    directory = "" if directory == "." else directory
    walked: list[str] = []
    found: Optional[str] = None
    while True:
        if directory in by_dir:
            found = by_dir[directory]
            break
        walked.append(directory)
        prefix = f"{directory}/" if directory else ""
        found = next((prefix + name for name in JS_CONFIG_NAMES if prefix + name in tracked), None)
        if found is not None or not directory:
            break
        directory = directory.rsplit("/", 1)[0] if "/" in directory else ""
    for name in walked:
        by_dir[name] = found
    if found is None:
        return None
    if found not in loaded:
        loaded[found] = load_js_config(root, found)
    return loaded[found]


def resolve_js_specifier(importer: str, spec: str, tracked: set[str], config: Optional[JsAliasConfig]) -> Optional[str]:
    """Relative specifiers join the importer's directory; bare ones go through `paths`, else `baseUrl`.

    As in tsc, a `paths` pattern that matches is final: when none of its targets exist the
    specifier stays unresolved instead of falling back to `baseUrl`.
    """
    ts_importer = importer.endswith((".ts", ".tsx"))
    if spec.startswith(("./", "../")) or spec in (".", ".."):
        directory = Path(importer).parent.as_posix()
        target = join_repo_path("" if directory == "." else directory, spec)
        # `'.'`, `'..'`, and `'./dir/'` name a directory: only its index resolves (as in tsc).
        directory_only = spec.endswith("/") or spec.rsplit("/", 1)[-1] in (".", "..")
        return resolve_js_candidate(target, tracked, directory_only, ts_importer)
    if config is None or spec.startswith("/"):
        return None
    paths_base = config.base_url if config.base_url is not None else config.paths_dir
    best: Optional[tuple[str, str]] = None
    for pattern in config.paths:
        if "*" not in pattern:
            if pattern == spec:
                best = (pattern, "")
                break
            continue
        head, _, tail = pattern.partition("*")
        if spec.startswith(head) and spec.endswith(tail) and len(spec) >= len(head) + len(tail):
            if best is None or len(head) > len(best[0].partition("*")[0]):
                best = (pattern, spec[len(head) : len(spec) - len(tail)])
    if best is not None and paths_base is not None:
        for substitution in config.paths[best[0]]:
            if not isinstance(substitution, str):
                continue
            target = substitution.replace("*", best[1], 1)
            hit = resolve_js_candidate(join_repo_path(paths_base, target), tracked, ts_importer=ts_importer)
            if hit:
                return hit
        return None
    if config.base_url is not None:
        return resolve_js_candidate(join_repo_path(config.base_url, spec), tracked, ts_importer=ts_importer)
    return None


def blank_spans(content: str, spans: list[tuple[int, int]]) -> str:
    """Replace each (start, end) span with one space."""
    pieces, cursor = [], 0
    for start, end in sorted(spans):
        if start < cursor:
            continue
        pieces.append(content[cursor:start])
        pieces.append(" ")
        cursor = end
    pieces.append(content[cursor:])
    return "".join(pieces)


HTML_WIRED_ATTR = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
JSON_PATH_VALUE = re.compile(
    r'''"([^"\n]+?\.(?:py|md|txt|sh|js|ts|toml|json|yml|yaml|mmd))"''',
    re.IGNORECASE,
)


def mentioned_path_occurrences(doc_rel: str, content: str, root: Optional[Path] = None) -> list[tuple[str, int, int]]:
    """Yield (normalized path, start, end) for every path mention in ``content``.

    Positions stay in the coordinate space of the supplied text so callers can
    classify each mention by its surrounding prose (see
    ``classify_example_mention``).
    """
    found: list[tuple[str, int, int]] = []
    for match in PATH_MENTION.finditer(content):
        if content[max(0, match.start() - 3) : match.start()].endswith("://"):
            continue
        raw = match.group(1)
        if match.start() > 0 and content[match.start() - 1] == "$":
            # `$PWD/tools/x.py`: the leading segment is a shell variable, the path is the rest.
            _, sep, raw = raw.partition("/")
            if not sep:
                continue
        normalized = normalize_mentioned_path(doc_rel, raw, root)
        if normalized and normalized != doc_rel:
            found.append((normalized, match.start(), match.end()))
    for match in MD_LINK.finditer(content):
        normalized = normalize_mentioned_path(doc_rel, match.group(1), root)
        if normalized and normalized != doc_rel:
            found.append((normalized, match.start(), match.end()))
    suffix = Path(doc_rel).suffix.lower()
    if suffix in (".html", ".htm"):
        # <script src> / <link href> / <img src> are live wiring, not prose.
        for match in HTML_WIRED_ATTR.finditer(content):
            normalized = normalize_mentioned_path(doc_rel, match.group(1), root)
            if normalized and normalized != doc_rel:
                found.append((normalized, match.start(), match.end()))
    elif suffix == ".json":
        # Manifest docs arrays, hook event tables, and build configs wire
        # files through string values; treat path-shaped values as live.
        for match in JSON_PATH_VALUE.finditer(content):
            normalized = normalize_mentioned_path(doc_rel, match.group(1), root)
            if normalized and normalized != doc_rel:
                found.append((normalized, match.start(), match.end()))
    return found


def mentioned_paths(doc_rel: str, content: str, root: Optional[Path] = None) -> set[str]:
    return {normalized for normalized, _, _ in mentioned_path_occurrences(doc_rel, content, root)}


# Mentions in clearly illustrative prose are candidates and examples, not broken
# references: a path-with-spaces inside a quoted run (`docs/plan v2.md`), a
# parenthesized enumeration of candidate locations (two or more path-shaped tokens plus 等/etc.), or a
# mention carrying an explicit example marker next to it. Classification is
# reported (``example-doc-path``), never silently dropped, so a misclassification
# stays visible in the audit output.
EXAMPLE_MARKERS = ("示例", "例子", "例如", "候选", "e.g.", "for example", "example", "placeholder")
ENUMERATION_MARKERS = ("等", "etc")
MARKER_WINDOW = 20
PAREN_SPAN = re.compile(r"[（(][^（()）]*[）)]")


def _enclosing_quote_span(line: str, start: int, end: int) -> Optional[tuple[str, int, int]]:
    """Return ``(content, content_start, content_end)`` for the quoted run around a mention."""
    for opener, closer in (("`", "`"), ("'", "'"), ('"', '"')):
        open_index = line.rfind(opener, 0, start)
        close_index = line.find(closer, end)
        if 0 <= open_index < start and close_index >= end:
            return line[open_index + 1 : close_index], open_index + 1, close_index
    return None


def classify_example_mention(text: str, start: int, end: int) -> bool:
    """True when a dangling mention is illustrative prose rather than a broken path."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end]
    local_start, local_end = start - line_start, end - line_start

    # 1. A quoted run that contains whitespace and starts with a path-shaped
    #    token: the mention is part of a "path with spaces" sample, not a repo path.
    quoted = _enclosing_quote_span(line, local_start, local_end)
    if quoted is not None:
        span, span_start, _span_end = quoted
        tokens = span.split()
        first_token = tokens[0] if tokens else ""
        if len(tokens) > 1 and "/" in first_token and local_start - span_start >= len(first_token):
            return True

    # 2. A parenthesized enumeration of two or more path-shaped tokens with an
    #    enumeration marker: a list of candidate locations, not existing files.
    for match in PAREN_SPAN.finditer(line):
        if match.start() <= local_start < match.end():
            body = match.group(0)
            if len(PATH_MENTION.findall(body)) >= 2 and any(marker in body for marker in ENUMERATION_MARKERS):
                return True

    # 3. An explicit example/candidate marker sits next to the mention.
    before = line[max(0, local_start - MARKER_WINDOW) : local_start]
    after = line[local_end : local_end + MARKER_WINDOW]
    return any(marker in before or marker in after for marker in EXAMPLE_MARKERS)


def find_retired_candidates(
    root: Path,
    tracked: list[str],
    specs_dir_name: str,
    declared: set[str],
) -> tuple[list[str], list[str], dict[str, list[str]]]:
    whitelist = export_script_whitelist(root)
    tracked_set = set(tracked)
    python_index = PythonImportIndex([rel for rel in tracked if not is_historical_path(rel, specs_dir_name)])
    js_config_by_dir: dict[str, Optional[str]] = {}
    js_configs: dict[str, JsAliasConfig] = {}
    referenced: set[str] = set()
    imported: set[str] = set()
    dangling: dict[str, list[str]] = {}
    example: dict[str, list[str]] = {}
    for rel in tracked:
        if is_historical_path(rel, specs_dir_name):
            continue
        path = root / rel
        suffix = path.suffix.lower()
        if suffix and suffix not in TEXT_SUFFIXES:
            continue
        content = read_text_bounded(path)
        if not content:
            continue
        mention_text = content
        if suffix == ".py":
            own = module_name_for(rel)
            reroot = partial(python_index.reroot, python_index.import_root(rel))
            # A module's own name (e.g. in its usage docstring) never keeps it alive.
            imported.update(
                name
                for name in collect_imported_modules(content, rel, reroot)
                if name != own and not name.startswith(own + ".")
            )
        elif suffix in JS_SUFFIXES:
            config = nearest_js_config(root, rel, tracked_set, js_config_by_dir, js_configs)
            specifiers, import_comments = js_specifier_literals(content)
            blank = list(import_comments)
            for start, end, spec in specifiers:
                target = resolve_js_specifier(rel, spec, tracked_set, config)
                if target:
                    # The resolver is authoritative for a resolved specifier; unresolved ones
                    # (packages, `?worker` queries) stay visible to path mentions.
                    blank.append((start, end))
                    if target != rel:
                        referenced.add(target)
            # A commented-out import is not a reference, even with an explicit extension.
            mention_text = blank_spans(content, blank)
        referenced.update(mentioned_paths(rel, mention_text, root))
        if suffix != ".md" or is_retired_candidate_excluded(rel, declared, whitelist):
            continue
        if is_planning_record(rel):
            # Planning records name future deliverables and basenames by
            # design; classifying those mentions as drift findings contradicts
            # the document class (see is_planning_record).
            continue
        prose = FENCED_BLOCK.sub("\n", content)
        real_references: set[str] = set()
        illustrative: set[str] = set()
        for missing, start, end in mentioned_path_occurrences(rel, prose, root):
            if (root / missing).exists():
                continue
            if classify_example_mention(prose, start, end):
                illustrative.add(missing)
            else:
                real_references.add(missing)
        # A path mentioned both as a real reference and as an example stays a
        # real reference: classification is fail-open only for pure examples.
        for missing in sorted(real_references):
            dangling.setdefault(missing, []).append(rel)
        for missing in sorted(illustrative - real_references):
            example.setdefault(missing, []).append(rel)
    unreferenced_files: list[str] = []
    unreferenced_code: list[str] = []
    for rel in sorted(tracked):
        if is_historical_path(rel, specs_dir_name):
            continue
        if is_retired_candidate_excluded(rel, declared, whitelist):
            continue
        suffix = Path(rel).suffix.lower()
        if suffix in CODE_SUFFIXES:
            if RUNNER_DISCOVERED.search(Path(rel).name):
                continue
            if rel.endswith(".d.ts"):
                stem = rel[: -len(".d.ts")]
                # Types for a same-name JS file, or ambient declarations the compiler includes:
                # neither needs an importer. A module `.d.ts` must be imported like any module.
                if any(stem + js in tracked_set for js in (".js", ".jsx", ".mjs", ".cjs")):
                    continue
                if dts_is_ambient(read_text_bounded(root / rel)):
                    continue
            module = module_name_for(rel)
            if rel in referenced or any(name == module or name.startswith(module + ".") for name in imported):
                continue
            unreferenced_code.append(rel)
            continue
        if rel in referenced:
            continue
        if suffix and suffix not in TEXT_SUFFIXES and suffix not in BINARY_SUFFIXES:
            continue
        unreferenced_files.append(rel)
    grouped = {missing: sorted(docs) for missing, docs in sorted(dangling.items())}
    example_grouped = {missing: sorted(docs) for missing, docs in sorted(example.items())}
    return unreferenced_files, unreferenced_code, grouped, example_grouped


@dataclass(frozen=True)
class ArchiveMove:
    action: str
    source: str
    destination: str


@dataclass(frozen=True)
class ArchivePlan:
    moves: tuple[ArchiveMove, ...]
    manifest_path: str
    manifest_text: str

    def actions(self) -> tuple[str, ...]:
        return tuple(move.action for move in self.moves)


def build_archive_plan(
    sources: list[str],
    specs_dir: str = ".spec",
    on: Optional[date] = None,
) -> ArchivePlan:
    """Describe archive moves. Does not write, delete, or unlink anything."""
    day = on or date.today()
    prefix = f"{specs_dir}/archive/retired/{day.isoformat()}"
    moves: list[ArchiveMove] = []
    for source in sources:
        rel = collapse_repo_path(source)
        if rel is None:
            raise ValueError(f"unsafe archive source: {source}")
        moves.append(ArchiveMove(action=ARCHIVE_ACTION, source=rel, destination=f"{prefix}/{rel}"))
    manifest_path = f"{prefix}/MANIFEST.md"
    lines = [
        "# Retired manifest",
        "",
        f"Date: {day.isoformat()}",
        "",
        f"Manifest: `{manifest_path}`",
        "",
        "| Source | Destination | Action |",
        "| --- | --- | --- |",
    ]
    for move in moves:
        lines.append(f"| `{move.source}` | `{move.destination}` | {move.action} |")
    return ArchivePlan(moves=tuple(moves), manifest_path=manifest_path, manifest_text="\n".join(lines) + "\n")


def build_reference_graph(
    root: Path,
    tracked: list[str],
    top_dirs: list[str],
) -> dict[str, list[str]]:
    referrers: dict[str, list[str]] = {name: [] for name in top_dirs}
    patterns = {name: re.compile(rf"(?<![A-Za-z0-9_.-]){re.escape(name)}(?![A-Za-z0-9_.-])") for name in top_dirs}
    for rel in tracked:
        path = root / rel
        suffix = path.suffix.lower()
        if suffix and suffix not in TEXT_SUFFIXES:
            continue
        top = rel.split("/", 1)[0]
        content = read_text_bounded(path)
        if not content:
            continue
        for name in top_dirs:
            if name == top:
                continue
            if patterns[name].search(content):
                referrers[name].append(rel)
    return referrers


def find_deprecated(
    root: Path,
    tracked: list[str],
    inventory: list[InventoryEntry],
    live_references: dict[str, int],
    is_git: bool,
    stale_days: int,
    large_bytes: int,
) -> tuple[list[str], list[tuple[str, Optional[str]]], list[tuple[str, int]], list[tuple[str, str]], list[str]]:
    today = date.today()
    unreferenced: list[str] = []
    stale: list[tuple[str, Optional[str]]] = []
    for entry in inventory:
        if entry.kind != "dir":
            continue
        if live_references.get(entry.name, 0):
            continue
        unreferenced.append(entry.name)
        if not is_git or entry.last_commit is None:
            continue
        try:
            committed = datetime.strptime(entry.last_commit, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - committed).days >= stale_days:
            stale.append((entry.name, entry.last_commit))

    large: list[tuple[str, int]] = []
    for rel in tracked:
        path = root / rel
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > large_bytes and is_binary(path):
            large.append((rel, size))

    tracked_dirs: set[str] = set()
    for rel in tracked:
        parts = rel.split("/")
        if len(parts) > 1:
            tracked_dirs.add("/".join(parts[:-1]))
    zips: list[tuple[str, str]] = []
    for rel in tracked:
        path = Path(rel)
        if path.suffix.lower() != ".zip":
            continue
        sibling_dir = path.parent / path.stem
        if str(sibling_dir) in tracked_dirs or sibling_dir.as_posix() in tracked_dirs:
            zips.append((rel, sibling_dir.as_posix()))

    untracked: list[str] = []
    if is_git:
        output = run_git(root, "status", "--porcelain", "--untracked-files=normal")
        if output:
            for line in output.splitlines():
                if line.startswith("?? "):
                    untracked.append(line[3:].strip())

    return unreferenced, stale, large, zips, untracked


def parse_module_index(content: str) -> tuple[list[str], list[str]]:
    """Return (module names, declared primary-file paths) from module-index.md."""
    modules: list[str] = []
    declared: list[str] = []
    lines = content.splitlines()
    index = 0
    while index < len(lines):
        heading = SECTION_HEADING.match(lines[index])
        if not heading:
            index += 1
            continue
        module_name = heading.group(1).strip()
        modules.append(module_name)
        index += 1
        in_primary = False
        saw_primary_marker = False
        section_declared: list[str] = []
        while index < len(lines) and not SECTION_HEADING.match(lines[index]):
            line = lines[index]
            if PRIMARY_FILES.match(line):
                in_primary = True
                saw_primary_marker = True
                index += 1
                continue
            bullet = BULLET.match(line)
            if bullet:
                token = PATH_TOKEN.match(bullet.group(1).strip())
                if token:
                    value = token.group(1).strip()
                    if _is_declared_path(value):
                        section_declared.append(value)
                elif not saw_primary_marker:
                    first = re.match(r"^`([^`]+)`", bullet.group(1).strip())
                    if first and _is_declared_path(first.group(1).strip()):
                        section_declared.append(first.group(1).strip())
                index += 1
                continue
            if in_primary and line.strip():
                in_primary = False
            index += 1
        declared.extend(section_declared)
    return modules, declared


def _is_declared_path(value: str) -> bool:
    if "://" in value or value.startswith("www."):
        return False
    if "/" in value:
        return True
    return bool(re.search(r"\.(py|md|json|sh|yaml|yml|toml|txt|mmd)$", value, re.IGNORECASE))


def parse_mermaid(content: str) -> tuple[dict[str, str], set[tuple[str, str]]]:
    nodes: dict[str, str] = {}
    edges: set[tuple[str, str]] = set()
    for line in content.splitlines():
        match = DAG_EDGE.match(line)
        if match:
            edges.add((match.group(1), match.group(2)))
        for node in DAG_NODE.finditer(line):
            nodes[node.group(1)] = node.group(2)
    return nodes, edges


def extract_mermaid_fence(content: str) -> Optional[str]:
    match = re.search(r"```mermaid\n(.*?)```", content, re.DOTALL)
    return match.group(1) if match else None


def check_architecture(
    root: Path,
    specs_root: Path,
    report: AuditReport,
) -> None:
    arch_dir = specs_root / "architecture"
    module_index_path = arch_dir / "module-index.md"
    module_index_content = module_index_path.read_text(encoding="utf-8") if module_index_path.exists() else ""
    if not module_index_content:
        return

    modules, declared = parse_module_index(module_index_content)
    report.declared_paths = declared
    for rel in declared:
        if not (root / rel).exists():
            report.missing_primary_files.append(rel)

    mmd_path = arch_dir / "module-dag.mmd"
    md_path = arch_dir / "module-dag.md"
    mmd_nodes: dict[str, str] = {}
    mmd_edges: set[tuple[str, str]] = set()
    md_nodes: dict[str, str] = {}
    md_edges: set[tuple[str, str]] = set()
    if mmd_path.exists():
        mmd_nodes, mmd_edges = parse_mermaid(mmd_path.read_text(encoding="utf-8"))
    else:
        report.dag_mmd_missing = True
    if md_path.exists():
        fence = extract_mermaid_fence(md_path.read_text(encoding="utf-8"))
        if fence:
            md_nodes, md_edges = parse_mermaid(fence)
        else:
            report.dag_md_missing = True
    else:
        report.dag_md_missing = True

    if md_nodes or mmd_nodes:
        for node in sorted(set(md_nodes) - set(mmd_nodes)):
            report.dag_node_diff.append(f"module-dag.md only: {node} ({md_nodes.get(node, '?')})")
        for node in sorted(set(mmd_nodes) - set(md_nodes)):
            report.dag_node_diff.append(f"module-dag.mmd only: {node} ({mmd_nodes.get(node, '?')})")
        for edge in sorted(md_edges - mmd_edges):
            report.dag_edge_diff.append(f"module-dag.md only: {edge[0]} --> {edge[1]}")
        for edge in sorted(mmd_edges - md_edges):
            report.dag_edge_diff.append(f"module-dag.mmd only: {edge[0]} --> {edge[1]}")

        mmd_labels = {label for label in mmd_nodes.values()}
        index_set = set(modules)
        for name in sorted(index_set - mmd_labels):
            report.index_module_diff.append(f"module-index.md only: {name}")
        for name in sorted(mmd_labels - index_set):
            report.index_module_diff.append(f"module-dag.mmd only: {name}")


def collect_facts(root: Path, specs_dir_name: str, stale_days: int, large_bytes: int) -> AuditReport:
    is_git = git_repo(root)
    tracked, _ = list_tracked(root, is_git)
    report = AuditReport(root=root, is_git=is_git)
    report.inventory = build_inventory(root, tracked, is_git)

    top_dirs = [entry.name for entry in report.inventory if entry.kind == "dir"]
    referrers = build_reference_graph(root, tracked, top_dirs)
    for name, files in referrers.items():
        live = [rel for rel in files if not is_historical_path(rel, specs_dir_name)]
        report.references[name] = len(files)
        report.live_references[name] = len(live)
        report.reference_examples[name] = (live or files)[:MAX_REFERENCE_EXAMPLES]
    (
        report.unreferenced_dirs,
        report.stale_dirs,
        report.large_binaries,
        report.zip_duplications,
        report.untracked_not_ignored,
    ) = find_deprecated(root, tracked, report.inventory, report.live_references, is_git, stale_days, large_bytes)

    try:
        specs_root = resolve_specs_root(root, specs_dir_name)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
    check_architecture(root, specs_root, report)
    files, code, groups, example_groups = find_retired_candidates(
        root, tracked, specs_dir_name, set(report.declared_paths)
    )
    report.unreferenced_files = files
    report.unreferenced_code = code
    report.dangling_doc_groups = groups
    report.example_doc_groups = example_groups
    return report


def render_markdown(report: AuditReport, check: bool) -> str:
    lines: list[str] = []
    lines.append(f"# Structure Audit: {report.root}")
    lines.append("")
    mode = "git" if report.is_git else "filesystem (not a git repository)"
    lines.append(f"Fact source: {mode}. Advisory report for first-principles judgment; the script changes nothing.")
    lines.append("")
    lines.append("## Inventory")
    lines.append("")
    lines.append("| Entry | Kind | Files | Bytes | Last commit |")
    lines.append("| --- | --- | --- | --- | --- |")
    for entry in report.inventory:
        committed = entry.last_commit or "-"
        lines.append(f"| `{entry.name}` | {entry.kind} | {entry.files} | {human_bytes(entry.bytes)} | {committed} |")
    lines.append("")
    lines.append("## Reference graph (external files per top-level directory)")
    lines.append("")
    for name in sorted(report.references):
        count = report.references[name]
        live = report.live_references.get(name, 0)
        marker = ""
        if live == 0:
            marker = "  <- no live reference (history only)"
        lines.append(f"- `{name}/`: {count} external reference(s), {live} live{marker}")
        for example in report.reference_examples.get(name, [])[:MAX_REFERENCE_EXAMPLES]:
            lines.append(f"  - e.g. `{example}`")
    lines.append("")
    lines.append("## Deprecated candidates (advisory; agent judges, gates decide)")
    lines.append("")
    if report.unreferenced_dirs:
        joined = ", ".join(f"`{n}`" for n in report.unreferenced_dirs)
        lines.append(f"- No live-reference top-level directories: {joined}")
    else:
        lines.append("- No live-reference top-level directories: none")
    if report.stale_dirs:
        joined = ", ".join(f"`{n}` (last commit {d})" for n, d in report.stale_dirs)
        lines.append(f"- Stale (unreferenced and untouched for the configured window): {joined}")
    if report.large_binaries:
        joined = ", ".join(f"`{p}` ({human_bytes(size)})" for p, size in report.large_binaries)
        lines.append(f"- Large tracked binaries: {joined}")
    if report.zip_duplications:
        joined = ", ".join(f"`{a}` duplicates `{d}/`" for a, d in report.zip_duplications)
        lines.append(f"- Archive/directory duplication: {joined}")
    if report.untracked_not_ignored:
        shown = ", ".join(f"`{p}`" for p in report.untracked_not_ignored[:MAX_REFERENCE_EXAMPLES])
        lines.append(f"- Untracked paths not covered by ignore rules: {shown}")
    if not any(
        (
            report.unreferenced_dirs,
            report.stale_dirs,
            report.large_binaries,
            report.zip_duplications,
            report.untracked_not_ignored,
        )
    ):
        lines.append("- none")
    lines.append("")
    lines.append("## Finding classes")
    lines.append("")
    if report.unreferenced_files:
        joined = ", ".join(f"`{path}`" for path in report.unreferenced_files)
        lines.append(f"- {CLASS_UNREFERENCED_FILE}: {joined}")
    else:
        lines.append(f"- {CLASS_UNREFERENCED_FILE}: none")
    if report.unreferenced_code:
        joined = ", ".join(f"`{path}`" for path in report.unreferenced_code)
        lines.append(f"- {CLASS_UNREFERENCED_CODE}: {joined}")
    else:
        lines.append(f"- {CLASS_UNREFERENCED_CODE}: none")
    if report.dangling_doc_groups:
        for missing, docs in report.dangling_doc_groups.items():
            joined = ", ".join(f"`{path}`" for path in docs)
            lines.append(f"- {CLASS_DANGLING_DOC} `{missing}`: {joined}")
    else:
        lines.append(f"- {CLASS_DANGLING_DOC}: none")
    for missing, docs in report.example_doc_groups.items():
        joined = ", ".join(f"`{path}`" for path in docs)
        lines.append(f"- {CLASS_EXAMPLE_DOC} `{missing}`: {joined}")
    lines.append("")
    lines.append("## Architecture consistency")
    lines.append("")
    if report.dag_md_missing:
        lines.append("- module-dag.md mermaid graph missing")
    if report.dag_mmd_missing:
        lines.append("- module-dag.mmd missing")
    for rel in report.missing_primary_files:
        lines.append(f"- Declared primary file missing on disk: `{rel}`")
    for item in report.dag_node_diff:
        lines.append(f"- Node set mismatch: {item}")
    for item in report.dag_edge_diff:
        lines.append(f"- Edge set mismatch: {item}")
    for item in report.index_module_diff:
        lines.append(f"- Module set mismatch: {item}")
    if report.architecture_findings == 0 and not report.dag_md_missing and not report.dag_mmd_missing:
        lines.append("- all declared primary files exist; md/mmd graphs and index modules are consistent")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"- top-level entries: {len(report.inventory)}; "
        f"no-live-reference dirs: {len(report.unreferenced_dirs)}; "
        f"stale: {len(report.stale_dirs)}; large binaries: {len(report.large_binaries)}; "
        f"zip duplications: {len(report.zip_duplications)}; "
        f"untracked-not-ignored: {len(report.untracked_not_ignored)}; "
        f"architecture findings: {report.architecture_findings}"
    )
    verdict = "FAIL" if check and report.architecture_findings else "PASS" if check else "N/A"
    lines.append(f"- --check verdict: {verdict}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: root is not a directory: {root}", file=sys.stderr)
        return 1
    report = collect_facts(root, args.specs_dir, args.stale_days, args.large_bytes)
    if args.format == "json":
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_markdown(report, args.check))
    if args.check and report.architecture_findings:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

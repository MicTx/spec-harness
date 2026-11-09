# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared package parsing, branch ownership and task dependencies."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import gitx

PROJECT_DEFINITION_LABEL_ALIASES = {
    "项目目标": ("goal", "objective"),
    "目标用户": ("target users", "users"),
    "核心价值": ("core value", "value"),
}

PROJECT_DEFINITION_PLACEHOLDERS = {
    "项目目标": ("一句话描述项目要解决什么问题", "one sentence describing the problem this project solves"),
    "目标用户": ("谁会使用这个产品或系统", "who will use this product or system"),
    "核心价值": ("完成后能持续提供什么价值", "the lasting value delivered once complete"),
}

GENERIC_PLACEHOLDERS = {"无", "待补充", "待完善", "n/a", "N/A", "NA", "none", "None"}

DEFAULT_EVIDENCE_PLACEHOLDERS = {"以 task package 状态机为真，fresh package 不得直接进入实现态"}

RULE_IDS = {
    "SPEC-UI-001": "LITE/FULL package must declare exactly one UI surface",
    "SPEC-UI-002": "UI surface changed requires a Browser check command",
    "SPEC-UI-003": "Browser check command must appear verbatim in a machine verify",
    "SPEC-OPT-001": "FULL option ladder requires B1",
    "SPEC-OPT-002": "Option reading must use the fixed role label",
    "SPEC-LOG-001": "Every failed correction round needs new evidence",
    "SPEC-EVID-001": "Machine evidence is missing",
    "SPEC-EVID-002": "Machine evidence is stale or its command changed",
    "SPEC-EVID-003": "Machine evidence is unreadable or malformed",
    "SPEC-SUCCESS-001": "Success criteria must be checked",
    "SPEC-VERIFY-001": "Verification scope field is still a placeholder",
    "SPEC-VERIFY-002": "A machine task must not declare multiple verify fields",
}


def rule_message(rule_id: str, message: str) -> str:
    if rule_id not in RULE_IDS:
        raise ValueError(f"unknown rule id: {rule_id}")
    return f"[{rule_id}] {message}"


SPEC_KEY_ANCHOR_LINE = re.compile("^<!--\\s*spec:([a-z0-9-]+)\\s*-->$")

SPEC_KEY_TRAILING_ANCHOR = re.compile("^(#{1,6}\\s+\\S.*?)?\\s*<!--\\s*spec:([a-z0-9-]+)\\s*-->$")


class SectionKey:
    __slots__ = ("key", "aliases")

    def __init__(self, key: str, *aliases: str) -> None:
        if not aliases:
            raise ValueError("SectionKey requires at least one alias")
        self.key = key
        self.aliases = tuple(aliases)

    @property
    def canonical(self) -> str:
        return self.aliases[0]

    def _identity(self, line: str) -> tuple[str | None, str | None]:
        stripped = line.strip()
        match = SPEC_KEY_TRAILING_ANCHOR.match(stripped)
        if match is None:
            return (None, stripped if stripped.startswith("#") else None)
        (heading_text, anchor_key) = (match.group(1), match.group(2))
        if heading_text is None:
            return (None, None)
        if anchor_key == self.key:
            return (self.key, heading_text.strip())
        if anchor_key in PACKAGE_SECTION_KEYS:
            return ("other", None)
        return (None, heading_text.strip())

    def matches(self, line: str) -> bool:
        (anchor, text) = self._identity(line)
        if anchor == self.key:
            return True
        if text is None:
            return False
        return any((heading_line_matches(text, alias) for alias in self.aliases))

    def matches_prefix(self, line: str) -> bool:
        (anchor, text) = self._identity(line)
        if anchor == self.key:
            return True
        if text is None:
            return False
        return any((text.startswith(alias) for alias in self.aliases))

    def __repr__(self) -> str:
        return f"SectionKey({self.key!r})"


SECTION_CONFIRMED_FACTS = SectionKey("confirmed-facts", "### 2.1 已确认事实", "### 2.1 Confirmed Facts")

SECTION_KEY_ASSUMPTIONS = SectionKey("key-assumptions", "### 2.2 关键假设", "### 2.2 Key Assumptions")

SECTION_EXTERNAL_FACTS = SectionKey("external-facts", "### 2.6 外部事实", "### 2.6 External Facts")

SECTION_OPEN_QUESTIONS = SectionKey("open-questions", "### 2.3 待确认问题", "### 2.3 Open Questions")

SECTION_FALSIFIED_PATHS = SectionKey("falsified-paths", "### 2.5 已证伪路径", "### 2.5 Falsified Paths")

SECTION_OUT_OF_SCOPE = SectionKey("out-of-scope", "### 3.3 不在范围内", "### 3.3 Out of Scope")

SECTION_MINIMAL_PATH = SectionKey("minimal-path", "## 4. 最小实现路径", "## 4. Minimal Implementation Path")

SECTION_OPTION_LADDER = SectionKey("option-ladder", "### 4.1 方案阶梯", "### 4.1 Option Ladder")

SECTION_VERIFICATION_STRATEGY = SectionKey("verification-strategy", "### 5.1 验证策略", "### 5.1 Verification Strategy")

SECTION_ORCHESTRATION = SectionKey("orchestration", "### 5.4 编排策略", "### 5.4 Orchestration Strategy")

SECTION_CORRECTION_LOG = SectionKey("correction-log", "### 5.5 修正日志", "### 5.5 Correction Log")

SECTION_DEVIATION_LOG = SectionKey("deviation-log", "### 5.6 偏离日志", "### 5.6 Deviation Log")

SECTION_ACCEPTANCE_EVIDENCE = SectionKey("acceptance-evidence", "## 验收证据", "## Acceptance Evidence")

SECTION_BEHAVIOR_METRICS = SectionKey("behavior-metrics", "### 6.1 行为成效指标", "### 6.1 Behavior Metrics")

SECTION_CROSS_CARRIER_CONSISTENCY = SectionKey(
    "cross-carrier-consistency", "## 跨载体一致性", "## Cross-Carrier Consistency"
)

SECTION_STRUCTURE_CREDIBILITY = SectionKey(
    "structure-credibility", "## 项目结构与文档可信度", "## Project Structure & Documentation Credibility"
)

SECTION_BOUNDARY_REGRESSION = SectionKey("boundary-regression", "## 边界回归", "## Boundary Regression")

PACKAGE_SECTION_KEYS: dict[str, SectionKey] = {
    section.key: section
    for section in (
        SECTION_CONFIRMED_FACTS,
        SECTION_KEY_ASSUMPTIONS,
        SECTION_EXTERNAL_FACTS,
        SECTION_OPEN_QUESTIONS,
        SECTION_FALSIFIED_PATHS,
        SECTION_OUT_OF_SCOPE,
        SECTION_MINIMAL_PATH,
        SECTION_OPTION_LADDER,
        SECTION_VERIFICATION_STRATEGY,
        SECTION_ORCHESTRATION,
        SECTION_CORRECTION_LOG,
        SECTION_DEVIATION_LOG,
        SECTION_ACCEPTANCE_EVIDENCE,
        SECTION_BEHAVIOR_METRICS,
        SECTION_CROSS_CARRIER_CONSISTENCY,
        SECTION_STRUCTURE_CREDIBILITY,
        SECTION_BOUNDARY_REGRESSION,
    )
}


def fold_section_key_anchors(content: str) -> str:
    out: list[str] = []
    buffered: list[str] = []
    pending_key: str | None = None
    in_fence = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if pending_key is not None:
                out.extend(buffered)
                buffered = []
                pending_key = None
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        anchor = SPEC_KEY_ANCHOR_LINE.fullmatch(stripped)
        if anchor is not None and anchor.group(1) in PACKAGE_SECTION_KEYS:
            buffered.append(line)
            pending_key = anchor.group(1)
            continue
        if pending_key is not None:
            if not stripped:
                out.append(line)
                continue
            if re.match("^#{1,6}\\s", stripped):
                out.append(line.rstrip() + f" <!-- spec:{pending_key} -->")
                buffered = []
                pending_key = None
                continue
            out.extend(buffered)
            out.append(line)
            buffered = []
            pending_key = None
            continue
        out.append(line)
    out.extend(buffered)
    return "\n".join(out)


SECTION_PLACEHOLDER_ITEMS = {
    SECTION_CONFIRMED_FACTS: {"事实 A", "Fact A"},
    SECTION_KEY_ASSUMPTIONS: {"假设 A", "Assumption A"},
    SECTION_OPEN_QUESTIONS: {"问题 A", "Question A"},
    SECTION_OUT_OF_SCOPE: {"明确列出本轮不做的内容", "explicitly list what this round will not do"},
    SECTION_MINIMAL_PATH: {
        "最简单可行方案：xxx",
        "暂不引入：xxx",
        "不做的抽象/配置化：xxx",
        "simplest viable approach: xxx",
        "not introducing yet: xxx",
        "abstractions/configurability not built: xxx",
    },
}

RECOMMENDED_SLUG_VERBS = ("add", "fix", "refactor", "update", "remove", "docs", "test", "chore")

DEVELOPMENT_RECORD_SLUG_PATTERN = re.compile("^(\\d{4}-\\d{2}-\\d{2})_([a-z0-9](?:[a-z0-9_-]*[a-z0-9])?)$")

REMOTE_UNAVAILABLE_MARKERS = (
    "couldn't connect to server",
    "failed to connect",
    "connection refused",
    "connection reset",
    "connection timed out",
    "could not resolve host",
    "name or service not known",
    "network is unreachable",
    "no route to host",
    "operation timed out",
    "does not appear to be a git repository",
    "unable to access",
)

REMOTE_REJECTION_MARKERS = (
    "authentication failed",
    "permission denied",
    "access denied",
    "not authorized",
    "authorization failed",
    "repository not found",
    "protected branch",
    "pre-receive hook declined",
    "remote rejected",
    "fetch first",
    "non-fast-forward",
    "stale info",
    "cannot lock ref",
    "git hooks seem to be broken",
    "repository git hooks seem to be broken",
)

PROTECTED_BRANCHES = {"main", "master", "develop", "development", "staging", "production"}

PROTECTED_PREFIXES = ("release/", "hotfix/")

INTEGRATION_BRANCH_FIELD = re.compile("^\\s*-\\s*Git integration branch[：:]\\s*(.+?)\\s*$", re.MULTILINE)

BACKTICK_VALUE = re.compile("`([^`]+)`")


@dataclass(frozen=True)
class ActivePackageSelection:
    slug: str | None
    mode: str
    current_branch: str
    integration_branch: str
    candidates: tuple[str, ...] = ()
    warning: str = ""

    def as_dict(self) -> dict:
        return {
            "slug": self.slug or "",
            "mode": self.mode,
            "currentBranch": self.current_branch,
            "integrationBranch": self.integration_branch,
            "candidates": list(self.candidates),
            "warning": self.warning,
        }


def combined_process_output(completed: subprocess.CompletedProcess[str]) -> str:
    return "\n".join((part for part in (completed.stdout.strip(), completed.stderr.strip()) if part))


def remote_unavailable_detected(output: str) -> bool:
    normalized = output.lower()
    if any((marker in normalized for marker in REMOTE_REJECTION_MARKERS)):
        return False
    return any((marker in normalized for marker in REMOTE_UNAVAILABLE_MARKERS))


def validate_slug(slug: str) -> str:
    if not re.fullmatch("[a-z0-9][a-z0-9_-]*[a-z0-9]", slug) and (not re.fullmatch("[a-z0-9]", slug)):
        raise ValueError(
            "slug must use lowercase letters, digits, hyphens, and underscores; cannot start/end with hyphen/underscore"
        )
    if slug.startswith(("-", "_")) or slug.endswith(("-", "_")):
        raise ValueError("slug cannot start or end with a hyphen or underscore")
    if "--" in slug or "__" in slug:
        raise ValueError("slug cannot contain consecutive hyphens or underscores")
    return slug


def is_development_record_slug(slug: str) -> bool:
    match = DEVELOPMENT_RECORD_SLUG_PATTERN.fullmatch(slug)
    if not match:
        return False
    try:
        date.fromisoformat(match.group(1))
        validate_slug(match.group(2))
    except ValueError:
        return False
    return True


def slug_verb_advisory(slug: str) -> str | None:
    match = DEVELOPMENT_RECORD_SLUG_PATTERN.fullmatch(slug)
    if not match:
        return None
    suffix = match.group(2)
    first_token = suffix.split("-", 1)[0].split("_", 1)[0]
    if first_token in RECOMMENDED_SLUG_VERBS:
        return None
    return "slug 推荐 verb-object 制式（如 add-/fix-/refactor-/update-），详见 references/naming-and-commits.md"


def extract_integration_branch(spec_content: str) -> str | None:
    match = INTEGRATION_BRANCH_FIELD.search(spec_content)
    if not match:
        return None
    value = match.group(1).strip()
    if "适用外" in value.lower() or value.lower() in {"n/a", "na", "none"}:
        return None
    quoted = BACKTICK_VALUE.findall(value)
    candidate = (quoted[0] if quoted else value.split("；", 1)[0]).strip()
    if not candidate or any((char.isspace() for char in candidate)):
        return None
    if candidate.startswith("-") or candidate.endswith(("/", ".", ".lock")) or ".." in candidate:
        return None
    if any((char in candidate for char in "~^:?*[\\")):
        return None
    return candidate


def select_active_package(
    root: Path,
    specs_root: Path,
    available: list[str],
    *,
    explicit: str | None = None,
    current_branch: str | None = None,
    git_available: bool | None = None,
) -> ActivePackageSelection:
    branch = current_git_branch(root) if current_branch is None else current_branch.strip()
    inside_git = git_worktree_available(root) if git_available is None else git_available
    candidates = tuple(available)
    if explicit:
        slug = validate_slug(explicit.strip())
        if slug not in available:
            raise ValueError(f"active task package not found: {slug}")
        content = read_regular_text(resolve_specs_child(specs_root, "specs", slug, "spec.md"))
        integration = extract_integration_branch(content) or ""
        return ActivePackageSelection(slug, "explicit", branch, integration, candidates)
    bindings: dict[str, str] = {}
    malformed: list[str] = []
    for slug in available:
        try:
            content = read_regular_text(resolve_specs_child(specs_root, "specs", slug, "spec.md"))
        except (OSError, UnicodeError, ValueError):
            malformed.append(slug)
            continue
        integration = extract_integration_branch(content)
        if integration:
            bindings[slug] = integration
    exact = [slug for (slug, integration) in bindings.items() if branch and integration == branch]
    if len(exact) == 1:
        slug = exact[0]
        return ActivePackageSelection(slug, "branch", branch, bindings[slug], candidates)
    if len(exact) > 1:
        return ActivePackageSelection(
            None,
            "ambiguous",
            branch,
            branch,
            candidates,
            "多个活动任务包记录了同一 integration branch，已停止选择：" + "、".join(exact),
        )
    conventional = [slug for slug in available if branch == f"spec/{slug}" and slug not in bindings]
    if len(conventional) == 1:
        slug = conventional[0]
        return ActivePackageSelection(slug, "branch-convention", branch, branch, candidates)
    if len(available) == 1 and (not inside_git):
        slug = available[0]
        return ActivePackageSelection(slug, "single-non-git", branch, bindings.get(slug, ""), candidates)
    if not available:
        return ActivePackageSelection(None, "none", branch, "", candidates)
    detail = f"当前分支 {branch}" if branch else "当前处于 detached HEAD" if inside_git else "当前非 Git 工作区"
    warning = f"{detail} 无法唯一绑定活动任务包；已隐藏跨包数据，请切换到对应 integration branch 或显式传入 --slug"
    if malformed:
        warning += "；无法读取分支元数据：" + "、".join(malformed)
    return ActivePackageSelection(None, "unbound", branch, "", candidates, warning)


def validate_branch_bound_package(
    root: Path, slug: str, spec_content: str, *, allow_non_git: bool = True, allow_legacy_convention: bool = False
) -> tuple[bool, str]:
    expected = extract_integration_branch(spec_content)
    inside_git = git_worktree_available(root)
    current = current_git_branch(root)
    if not inside_git:
        return (True, "non-git") if allow_non_git else (False, "project is not a Git worktree")
    if not expected:
        if allow_legacy_convention and current == f"spec/{slug}":
            return (True, current)
        return (False, f"task package {slug} has no valid Git integration branch metadata")
    if not current:
        return (False, f"detached HEAD cannot operate task package {slug}; expected branch {expected}")
    if current != expected:
        return (False, f"task package {slug} belongs to branch {expected}, current branch is {current}")
    return (True, expected)


def detect_language(text: str) -> str:
    cjk = sum((1 for ch in text if "一" <= ch <= "\u9fff"))
    alpha = sum((1 for ch in text if ch.isalpha()))
    if alpha == 0:
        return "zh"
    return "zh" if cjk / alpha > 0.3 else "en"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    """Replace the package entry, never truncate a linked target inode."""
    descriptor, name = tempfile.mkstemp(dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _heading_level(heading: str | SectionKey) -> int:
    source = heading.aliases[0] if isinstance(heading, SectionKey) else heading
    match = re.match("^(#+)\\s+", source.strip())
    return len(match.group(1)) if match else 0


def _heading_level_source(heading: str | SectionKey) -> str:
    return heading.aliases[0] if isinstance(heading, SectionKey) else heading


def heading_line_matches(line: str, heading: str | SectionKey) -> bool:
    if isinstance(heading, SectionKey):
        return heading.matches(line)
    (actual, expected) = (line.strip(), heading.strip())
    if actual == expected:
        return True
    match = re.fullmatch(re.escape(expected) + "\\s*(?:（([^（）()\\r\\n]+)）|\\(([^（）()\\r\\n]+)\\))", actual)
    return match is not None and bool((match.group(1) or match.group(2)).strip())


def section_exists(content: str, heading: str | SectionKey) -> bool:
    if isinstance(heading, SectionKey):
        content = fold_section_key_anchors(content)
    return any((heading_line_matches(line, heading) for line in content.splitlines()))


def section_prefix_exists(content: str, heading_prefix: str | SectionKey) -> bool:
    if isinstance(heading_prefix, SectionKey):
        content = fold_section_key_anchors(content)
        return any((heading_prefix.matches_prefix(line) for line in content.splitlines()))
    prefix = heading_prefix.strip()
    return any((line.strip().startswith(prefix) for line in content.splitlines()))


def _section_lines(content: str, heading: str | SectionKey, *, prefix: bool = False) -> Iterator[str]:
    if isinstance(heading, SectionKey):
        content = fold_section_key_anchors(content)
    source = _heading_level_source(heading).strip()
    level = _heading_level(heading) or 10
    active = False
    for line in content.splitlines():
        starts = (
            (heading.matches_prefix(line) if isinstance(heading, SectionKey) else line.strip().startswith(source))
            if prefix
            else heading_line_matches(line, heading)
        )
        if starts:
            # Restart on a repeated same-level heading so a later duplicate
            # cannot be hidden by an earlier section's "none" declaration.
            active = True
            continue
        if active and re.match(r"^#{1,%d} " % level, line):
            break
        if active:
            yield line


def section_checkboxes(content: str, heading: str | SectionKey) -> tuple[int, int]:
    return count_tasks("\n".join(_section_lines(content, heading)))


def section_checkboxes_by_prefix(content: str, heading_prefix: str) -> tuple[int, int]:
    return count_tasks("\n".join(_section_lines(content, heading_prefix, prefix=True)))


def extract_heading_title_from_content(content: str, fallback: str, suffix: str | tuple[str, ...]) -> str:
    suffixes = suffix if isinstance(suffix, tuple) else (suffix,)
    for line in content.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            for ending in suffixes:
                if ending and title.endswith(ending):
                    return title[: -len(ending)].strip()
            return title
    return fallback


def extract_heading_title(path: Path, fallback: str, suffix: str) -> str:
    return extract_heading_title_from_content(read_text(path), fallback, suffix)


SPEC_TITLE_SUFFIXES = (" - 项目范围", " - Project Scope")


def extract_title_from_content(spec_content: str, slug: str) -> str:
    return extract_heading_title_from_content(spec_content, slug, SPEC_TITLE_SUFFIXES)


def extract_section_bullets(content: str, heading: str | SectionKey) -> list[str]:
    return [
        match.group(1).strip()
        for line in _section_lines(content, heading)
        if (match := re.match(r"^\s*-\s+(.*)$", line))
    ]


_NO_FALSIFIED_PATHS_DECLARATION = re.compile("(?:无|none)(?:\\s*[（(][^（）()]*[）)])?[。.]?", re.IGNORECASE)


def falsified_path_entries(spec_content: str) -> list[str]:
    return [
        bullet
        for bullet in extract_section_bullets(spec_content, SECTION_FALSIFIED_PATHS)
        if (text := bullet.replace("`", "").strip()) and not _NO_FALSIFIED_PATHS_DECLARATION.fullmatch(text)
    ]


def extract_structured_section_fields(content: str, heading_prefix: str | SectionKey) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in _section_lines(content, heading_prefix, prefix=True):
        match = TOP_LEVEL_BULLET_FIELD.match(line)
        if match:
            fields.setdefault(match.group(1).strip(), match.group(2).strip())
    return fields


def normalize_items(items: list[str], ignored: set[str] | None = None) -> list[str]:
    ignore = set(GENERIC_PLACEHOLDERS)
    if ignored:
        ignore.update(ignored)
    normalized: list[str] = []
    for item in items:
        if not item or item in ignore:
            continue
        if item.replace("`", "").strip() == "xxx":
            continue
        normalized.append(item)
    return normalized


def is_placeholder_value(value: str) -> bool:
    normalized = value.replace("`", "").strip()
    if not normalized or normalized in GENERIC_PLACEHOLDERS:
        return True
    if normalized in DEFAULT_EVIDENCE_PLACEHOLDERS:
        return True
    if "待补充" in normalized or "待完善" in normalized or "xxx" in normalized:
        return True
    return False


SPEC_FORMAT_MARKER = re.compile("<!--\\s*spec-format:\\s*(\\d+)\\s*-->")


def spec_format_version(content: str) -> int:
    match = SPEC_FORMAT_MARKER.search(content)
    return int(match.group(1)) if match else 1


def extract_problem_definition_values(spec_content: str) -> dict[str, str]:
    label_lookup = {
        **{label: label for label in PROJECT_DEFINITION_PLACEHOLDERS},
        **{alias: label for (label, aliases) in PROJECT_DEFINITION_LABEL_ALIASES.items() for alias in aliases},
    }
    values: dict[str, str] = {}
    for match in re.finditer("(?m)^-\\s*\\*\\*([^*：:]+)\\*\\*\\s*[：:]\\s*(.*)$", spec_content):
        label = label_lookup.get(match.group(1).strip().lower(), None)
        if label is not None and label not in values:
            values[label] = match.group(2).strip()
    return values


ORCHESTRATION_ROUTES = ("local", "explore", "build", "review", "external")

SECTION_ORCHESTRATION_HEADING = SECTION_ORCHESTRATION

ORCHESTRATION_HEADING_PREFIX = SECTION_ORCHESTRATION.canonical

TOP_LEVEL_BULLET_FIELD = re.compile("^-\\s*([^：:]+?)\\s*[：:]\\s*(.*)$")

BULLET_LINE = re.compile("^-\\s?")

TASK_MARKER = re.compile(r"(?m)^\s*-\s\[(.)\]\s+(.*)$")


def count_tasks(tasks_content: str) -> tuple[int, int]:
    markers = TASK_MARKER.findall(tasks_content)
    return len(markers), sum(marker.lower() == "x" for marker, _ in markers)


def count_task_detail_gaps(tasks_content: str) -> tuple[int, int]:
    records = parse_task_records(tasks_content)
    return (sum(not record.boundary for record in records), sum(not record.verify for record in records))


def count_unchecked_checkboxes(content: str) -> int:
    return sum(marker.lower() != "x" for marker, _ in TASK_MARKER.findall(content))


TASK_ID_PATTERN = re.compile("^(?:task_\\d{4,}|task-[a-z0-9][a-z0-9-]{0,58})$")

STABLE_TASK_ID_PATTERN = re.compile("^task-[a-z0-9][a-z0-9-]{0,58}$")


class SpecControlError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TaskRecord:
    id: str
    title: str
    checked: bool
    blocked: bool
    boundary: str
    verify: str
    block_text: str

    @property
    def executable(self) -> bool:
        return bool(self.boundary.strip()) and bool(self.verify.strip())


def parse_task_records(tasks_content: str) -> list[TaskRecord]:
    records: list[TaskRecord] = []
    lines = tasks_content.splitlines()
    index = 0
    while index < len(lines):
        match = re.match("^\\s*-\\s\\[(.)\\]\\s+(.*)$", lines[index].rstrip())
        if not match:
            index += 1
            continue
        (marker, title) = (match.group(1), match.group(2).strip())
        block: list[str] = [f"- [{marker}] {title}"]
        index += 1
        own_block = list(block)
        boundary = ""
        verify = ""
        while index < len(lines):
            detail = re.match("^\\s*-\\s+(boundary|verify|depends-on):\\s*(.*)$", lines[index].rstrip())
            if detail:
                block.append(lines[index].strip())
                own_block.append(lines[index].strip())
                if detail.group(1) == "boundary":
                    boundary = detail.group(2).strip()
                elif detail.group(1) == "verify":
                    verify = detail.group(2).strip()
                index += 1
                continue
            next_task = re.match("^\\s*-\\s\\[(.)\\]\\s+", lines[index].rstrip())
            if next_task or lines[index].strip().startswith("#"):
                break
            if lines[index].strip():
                block.append(lines[index].strip())
                if lines[index][0].isspace():
                    own_block.append(lines[index].strip())
            index += 1
        explicit = [line.removeprefix("- id:").strip() for line in block if line.startswith("- id:")]
        if len(explicit) > 1 or (explicit and (not STABLE_TASK_ID_PATTERN.fullmatch(explicit[0]))):
            raise SpecControlError(f"invalid or duplicate explicit task id in {title!r}")
        records.append(
            TaskRecord(
                id=explicit[0] if explicit else f"task_{len(records) + 1:04d}",
                title=title,
                checked=marker.lower() == "x",
                blocked=marker == "!" or (task_title_signals_blocked(title) and marker.lower() != "x"),
                boundary=boundary,
                verify=verify,
                block_text="\n".join(own_block if explicit else block),
            )
        )
    ids = [record.id for record in records]
    if len(ids) != len(set(ids)):
        raise SpecControlError("duplicate task id")
    return records


def task_dependencies(record: TaskRecord) -> tuple[str, ...]:
    refs: list[str] = []
    for line in record.block_text.splitlines():
        match = re.match("^- depends-on:\\s*(.*)$", line)
        if match:
            refs.extend((part.strip() for part in match.group(1).split(",")))
    return tuple(refs)


def dependency_errors(records: list[TaskRecord]) -> list[str]:
    from collections import deque

    known = {item.id for item in records}
    errors: list[str] = []
    indegree = {item.id: 0 for item in records}
    children: dict[str, list[str]] = {item.id: [] for item in records}
    for record in records:
        deps = task_dependencies(record)
        if len(deps) != len(set(deps)):
            errors.append(f"{record.id}: duplicate dependency")
        for ref in dict.fromkeys(deps):
            if not TASK_ID_PATTERN.fullmatch(ref) or ref not in known:
                errors.append(f"{record.id}: unknown or invalid dependency {ref!r}")
            else:
                indegree[record.id] += 1
                children[ref].append(record.id)
    queue = deque((key for (key, degree) in indegree.items() if degree == 0))
    while queue:
        for child in children[queue.popleft()]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    cyclic = [key for (key, degree) in indegree.items() if degree]
    if cyclic:
        errors.append("dependency cycle or dependent on cycle: " + ", ".join(cyclic))
    return errors


def ready_task_ids(records: list[TaskRecord]) -> set[str]:
    if dependency_errors(records):
        return set()
    completed = {item.id for item in records if item.checked}
    return {
        item.id
        for item in records
        if not item.checked
        and (not item.blocked)
        and item.executable
        and all((ref in completed for ref in task_dependencies(item)))
    }


def resolve_active_slug(
    root: Path, specs_dir_name: str | None, explicit: str | None, *, require_branch: bool = True
) -> str:
    specs_root = resolve_specs_root(root, specs_dir_name)
    specs_dir = resolve_specs_child(specs_root, "specs")
    packages = active_package_slugs(specs_dir)
    try:
        selection = select_active_package(root, specs_root, packages, explicit=explicit)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SpecControlError(str(exc)) from exc
    if selection.slug:
        if not require_branch:
            return selection.slug
        try:
            spec_content = read_regular_text(resolve_specs_child(specs_root, "specs", selection.slug, "spec.md"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise SpecControlError(f"cannot read spec.md for branch validation: {exc}") from exc
        (branch_ok, branch_detail) = validate_branch_bound_package(
            root, selection.slug, spec_content, allow_legacy_convention=selection.mode == "branch-convention"
        )
        if not branch_ok:
            raise SpecControlError(branch_detail)
        return selection.slug
    if not packages:
        raise SpecControlError("no active Spec package under the project root")
    raise SpecControlError(
        "multiple active Spec packages; " + (selection.warning or "pass --slug explicitly: " + ", ".join(packages))
    )


def checklist_passed(content: str) -> bool:
    return bool(re.search("(?im)^\\*\\*(?:验收结果|Result)\\*\\*\\s*[：:]\\s*(?:通过|pass)\\s*$", content))


def checklist_complete(content: str) -> bool:
    return checklist_passed(content) and count_unchecked_checkboxes(content) == 0


def acceptance_complete(checklist_content: str | None, spec_content: str) -> bool:
    if checklist_content is not None:
        return checklist_complete(checklist_content)
    (total, checked) = section_checkboxes_by_prefix(spec_content, "## 6.")
    return total > 0 and total == checked


def extract_evidence_fields(checklist_content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    nested: dict[str, list[str]] = {}
    current = None
    for line in _section_lines(checklist_content, SECTION_ACCEPTANCE_EVIDENCE):
        match = re.match(r"^-\s*([^：:]+)[：:]\s*(.*)$", line)
        if match:
            current = match.group(1).strip()
            fields[current], nested[current] = match.group(2).strip(), []
        elif current is not None and line.startswith((" ", "\t")) and line.strip():
            nested[current].append(re.sub(r"^\s*-\s+", "", line).strip())
        elif line.strip():
            current = None
    return {key: value or "\n".join(nested[key]) for key, value in fields.items()}


def has_command_evidence(checklist_content: str) -> bool:
    fields = extract_evidence_fields(checklist_content)
    for key in ("脚本验证", "测试", "构建"):
        value = fields.get(key, "")
        if value and (not is_placeholder_value(value)):
            return True
    return False


EVIDENCE_ANCHOR_KEY = "证据锚点"

EVIDENCE_ANCHOR_VALUE_PATTERN = re.compile("^HEAD\\s+([0-9a-fA-F]{7,40})\\s+@\\s+(\\S.*)$")

EVIDENCE_ANCHOR_FULL_SHA_PATTERN = re.compile("^[0-9a-f]{40}$")

EVIDENCE_ANCHOR_FORMAT_GUIDANCE = "证据锚点须为完整 40 位 SHA"


def evidence_anchor_format_errors(checklist_content: str) -> list[str]:
    value = extract_evidence_fields(checklist_content).get(EVIDENCE_ANCHOR_KEY, "")
    if not value:
        return []
    match = EVIDENCE_ANCHOR_VALUE_PATTERN.match(value)
    if not match:
        return []
    if EVIDENCE_ANCHOR_FULL_SHA_PATTERN.fullmatch(match.group(1).lower()):
        return []
    return [
        "证据锚点格式不合规："
        f"{EVIDENCE_ANCHOR_FORMAT_GUIDANCE}（须匹配 ^[0-9a-f]{{40}}$）；"
        f"当前记录：HEAD {match.group(1)} @ {match.group(2)}；"
        f"请以 git rev-parse HEAD 的完整 40 位输出更新 {EVIDENCE_ANCHOR_KEY}"
    ]


def extract_evidence_anchor(checklist_content: str) -> tuple[str, str] | None:
    value = extract_evidence_fields(checklist_content).get(EVIDENCE_ANCHOR_KEY, "")
    if not value:
        return None
    match = EVIDENCE_ANCHOR_VALUE_PATTERN.match(value)
    if not match:
        return None
    return (match.group(1).lower(), match.group(2).strip())


PACKAGE_WORKFLOWS = ("lite", "full")


def load_reference_templates(root: Path, workflow: str = "lite") -> dict[str, str]:
    if workflow not in PACKAGE_WORKFLOWS:
        raise ValueError(f"unknown workflow {workflow!r}; expected one of {', '.join(PACKAGE_WORKFLOWS)}")
    reference_path = root / "references" / "templates.md"
    content = read_text(reference_path)

    def fence(name: str) -> str:
        match = re.search(f"## `{re.escape(name)}`\\n\\n```markdown\\n(.*?)\\n```", content, re.S)
        if not match:
            raise ValueError(f"template block not found for {name} in {reference_path}")
        return match.group(1)

    return {"spec.md": fence("spec.full.md" if workflow == "full" else "spec.md"), "tasks.md": fence("tasks.md")}


DEFAULT_SPECS_DIR = ".spec"

RESERVED_SPECS_DIR_PARTS = {
    ".claude",
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".svn",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}


def scope_from_changed_paths(paths: list[str], specs_dirs: list[str] | tuple[str, ...] = ()) -> list[str]:
    roots: set[tuple[str, ...]] = {(".spec",), (".trae",)}
    for name in specs_dirs:
        candidate = Path(name)
        if not name.strip() or candidate.is_absolute() or candidate == Path(".") or (".." in candidate.parts):
            raise ValueError("custom specs root must be a trusted relative path")
        if any((part in RESERVED_SPECS_DIR_PARTS for part in candidate.parts)):
            raise ValueError("custom specs root uses a reserved runtime directory")
        roots.add(candidate.parts)
    spec_slugs: set[str] = set()
    for value in paths:
        parts = Path(value.strip()).parts
        for root_parts in sorted(roots, key=lambda item: (len(item), item), reverse=True):
            prefix_length = len(root_parts)
            if parts[:prefix_length] != root_parts or len(parts) <= prefix_length:
                continue
            category = parts[prefix_length]
            if category == "specs" and len(parts) > prefix_length + 1:
                candidate = parts[prefix_length + 1]
                if candidate == "archive" and len(parts) > prefix_length + 2:
                    candidate = parts[prefix_length + 2]
                    if candidate == "retired":
                        candidate = ""
                if candidate and candidate != "archive":
                    if not is_development_record_slug(candidate):
                        raise ValueError(f"invalid Development Record slug in trusted path: {candidate}")
                    spec_slugs.add(candidate)
            break
    return sorted(spec_slugs)


def resolve_specs_root(root: Path, specs_dir_name: str | None = None) -> Path:
    name = DEFAULT_SPECS_DIR if specs_dir_name is None else specs_dir_name
    specs_dir = Path(name)
    if not name.strip() or specs_dir.is_absolute() or specs_dir == Path("."):
        raise ValueError("--specs-dir must be a relative directory under root")
    if ".." in specs_dir.parts:
        raise ValueError("--specs-dir must not contain '..'")
    reserved = next((part for part in specs_dir.parts if part in RESERVED_SPECS_DIR_PARTS), None)
    if reserved:
        raise ValueError(f"--specs-dir must not use reserved runtime directory: {reserved}")
    root_resolved = root.resolve(strict=False)
    candidate = root_resolved / specs_dir
    candidate_resolved = candidate.resolve(strict=False)
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError("--specs-dir must resolve under root")
    return candidate


def resolve_specs_child(specs_root: Path, *parts: str) -> Path:
    candidate = specs_root.joinpath(*parts)
    specs_root_resolved = specs_root.resolve(strict=False)
    candidate_resolved = candidate.resolve(strict=False)
    if candidate_resolved != specs_root_resolved and specs_root_resolved not in candidate_resolved.parents:
        raise ValueError("specs path must resolve under specs root")
    return candidate


def resolve_life_dir(root: Path, specs_dir_name: str | None = None) -> Path:
    return resolve_specs_root(root, specs_dir_name)


def resolve_trae_dir(root: Path, specs_dir_name: str | None = None) -> Path:
    return resolve_specs_root(root, specs_dir_name)


def active_branch_bindings(root: Path, specs_dir_name: str | None = None) -> dict[str, tuple[str, ...]]:
    specs_root = resolve_specs_root(root, specs_dir_name)
    packages = active_package_slugs(resolve_specs_child(specs_root, "specs"))
    bindings: dict[str, list[str]] = {}
    for slug in packages:
        try:
            content = read_regular_text(resolve_specs_child(specs_root, "specs", slug, "spec.md"))
        except (OSError, UnicodeError, ValueError):
            continue
        branch = extract_integration_branch(content)
        if branch:
            bindings.setdefault(branch, []).append(slug)
    return {branch: tuple(slugs) for (branch, slugs) in bindings.items()}


def active_package_slugs(specs_dir: Path) -> list[str]:
    if not specs_dir.exists():
        return []
    packages: list[str] = []
    for path in sorted(specs_dir.iterdir()):
        if path.name == "archive" or path.name.startswith(".") or (not path.is_dir()) or path.is_symlink():
            continue
        if (path / "spec.md").exists() and (path / "tasks.md").exists():
            packages.append(path.name)
    return packages


def add_specs_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--specs-dir",
        default=DEFAULT_SPECS_DIR,
        help="Relative directory under root for specs storage; resolved paths must stay under root (default: .spec)",
    )


def git_worktree_available(root: Path) -> bool:
    return gitx.toplevel(root) is not None


def current_git_branch(root: Path) -> str:
    return gitx.branch(root)


def git_head_sha(root: Path) -> str:
    return gitx.head(root)


def read_regular_text(path: Path) -> str:
    """Package Markdown is project-owned source, read without a second safety layer."""
    return path.read_text(encoding="utf-8")


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json_file(path: Path, **_options) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise SpecControlError(f"cannot read JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SpecControlError(f"file must contain a JSON object: {path}")
    return value


def task_title_signals_blocked(title: str) -> bool:
    """Anchored block declaration in the title: ``blocked:`` / ``阻塞：``.

    Prose mentioning the word ("blocked on X", "Blocker: ...") is not a
    declaration; the ``!`` marker and the standalone ``blocked:`` status line
    remain the authoritative signals (has_blocked_tasks).
    """
    return bool(re.match(r"(?i)^blocked\s*:", title)) or title.startswith("阻塞：")


@dataclass(frozen=True)
class AutonomousQuestion:
    text: str
    kind: str
    closed: bool
    task: str
    fields: dict[str, str]


def _question_rows(spec_content: str) -> list[tuple[bool, str, dict[str, str]]]:
    rows = []
    for item in extract_section_bullets(spec_content, SECTION_OPEN_QUESTIONS):
        if re.fullmatch(r"(?:无|none)[。. ]?", item, re.I):
            continue
        match = re.match(r"\[([ xX!])\]\s*(.*)", item)
        closed, body = (match.group(1).lower() == "x", match.group(2)) if match else (False, item)
        parts = [part.strip() for part in body.split("|")]
        fields = {}
        for part in parts[1:]:
            key, sep, value = part.partition(":")
            if sep:
                fields[key.strip()] = value.strip()
        rows.append((closed, parts[0], fields))
    return rows


def extract_pending_questions(spec_content: str) -> list[str]:
    return [text for closed, text, _fields in _question_rows(spec_content) if not closed]


def autonomous_question_contract(
    spec_content: str, tasks_content: str = ""
) -> tuple[list[AutonomousQuestion], list[str], list[str]]:
    questions, human, errors = [], [], []
    for closed, text, fields in _question_rows(spec_content):
        match = re.match(r"\[(decision|fact|reversible)\]\s*(.*)", text)
        kind, title = match.groups() if match else ("fact", text)
        if closed and is_placeholder_value(fields.get("answer", "")):
            errors.append(f"{title}：关闭需要 answer: <结论>")
        if kind == "decision":
            if not closed:
                human.append(title)
            authority = fields.get("authority", "")
            if closed and (not authority.startswith("user:") or is_placeholder_value(authority[5:])):
                errors.append(f"{title}：decision 关闭需要 authority: user:<引用>")
        else:
            questions.append(AutonomousQuestion(title, kind, closed, fields.get("task", ""), fields))
    return questions, human, errors


def workflow_ledger_errors(spec_content: str, tasks_content: str | None = None) -> list[str]:
    if not re.search(r"(?mi)^-\s*Workflow:\s*`?FULL`?\s*$", spec_content):
        return []
    errors = []
    literal = re.search(r"(?m)^-\s*\*\*Literal ask\*\*\s*[：:]\s*(.+)", spec_content)
    if not literal or is_placeholder_value(literal.group(1)) or literal.group(1).startswith("<"):
        errors.append("FULL 需要非空 Literal ask")
    if not re.search(r"(?m)^-\s*B[1-9]\d*\s*\|\s*\S", spec_content):
        errors.append(rule_message("SPEC-OPT-001", "FULL 至少写一个 B1 备选方案"))
    return errors


def correction_log_errors(spec_content: str) -> list[str]:
    return []  # Optional process notes are advisory, not delivery gates.


def deviation_log_errors(spec_content: str) -> list[str]:
    return []


VERIFICATION_SCOPE_LEVELS = ("package", "integration", "project")
VERIFICATION_SCOPE_HEADING_PREFIX = SECTION_VERIFICATION_STRATEGY.canonical
VERIFICATION_SCOPE_REQUIRED_FIELDS = ("范围级别", "快速检查")
VERIFICATION_SCOPE_FIELD_ALIASES = {
    "范围级别": ("scope", "scope level"),
    "快速检查": ("quick check",),
    "集成检查": ("integration check",),
    "全项目检查": ("full-project check", "full project check"),
}


def verification_scope_section_exists(spec_content: str) -> bool:
    return section_exists(spec_content, SECTION_VERIFICATION_STRATEGY)


def verification_scope_fields(spec_content: str) -> dict[str, str]:
    aliases = {alias: key for key, values in VERIFICATION_SCOPE_FIELD_ALIASES.items() for alias in values}
    fields = extract_structured_section_fields(spec_content, SECTION_VERIFICATION_STRATEGY)
    return {aliases.get(key.lower(), key): value for key, value in fields.items()}


def normalize_verification_scope_value(value: str) -> str:
    return re.split(r"[；;]", value, maxsplit=1)[0].strip().strip("`").lower()


def verification_scope(spec_content: str) -> str | None:
    return normalize_verification_scope_value(verification_scope_fields(spec_content).get("范围级别", "")) or None


def verification_scope_errors(spec_content: str) -> list[str]:
    if not verification_scope_section_exists(spec_content):
        return []
    fields = verification_scope_fields(spec_content)
    errors = []
    if verification_scope(spec_content) not in VERIFICATION_SCOPE_LEVELS:
        errors.append("范围级别必须为 package/integration/project")
    command = fields.get("快速检查", "")
    if is_placeholder_value(command) or command.startswith("<"):
        errors.append(rule_message("SPEC-VERIFY-001", "快速检查需要真实命令"))
    return errors


def clarification_gaps(spec_content: str) -> list[str]:
    errors = []
    values = extract_problem_definition_values(spec_content)
    goal = values.get("项目目标", "")
    if not goal or goal in PROJECT_DEFINITION_PLACEHOLDERS["项目目标"]:
        errors.append("`spec.md` 缺少有效项目目标")
    for section in (SECTION_KEY_ASSUMPTIONS, SECTION_OPEN_QUESTIONS, SECTION_OUT_OF_SCOPE, SECTION_MINIMAL_PATH):
        bullets = extract_section_bullets(spec_content, section)
        if not bullets or any(bullet in SECTION_PLACEHOLDER_ITEMS.get(section, ()) for bullet in bullets):
            errors.append(f"`spec.md` 缺少有效区块：{section.canonical}")
    return errors


def orchestration_strategy_errors(spec_content: str) -> list[str]:
    fields = extract_structured_section_fields(spec_content, SECTION_ORCHESTRATION)
    route = fields.get("route", "local").strip("`")
    return [] if route in ORCHESTRATION_ROUTES else ["route 必须是 local/explore/build/review/external"]

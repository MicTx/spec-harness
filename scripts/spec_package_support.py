# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared task-package semantics.

Branch-bound invariant: ``validate_branch_bound_package`` gates Git-backed
mutations to the exact branch recorded in ``spec.md``. ``select_active_package``
is the shared read selector for route and the Stop guard; it returns at most
one package and fails closed for detached, unmatched, or duplicate bindings.

This module also owns the neutral runtime primitives shared across Spec
scripts: the ``SpecControlError`` operational error type, safe runtime JSON
IO, ``tasks.md`` parsing (``parse_task_records``) with the dependency graph,
and active-slug resolution bound to the integration branch.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from safe_open_support import SafeOpenError, open_regular_read

PROJECT_DEFINITION_PLACEHOLDERS = {
    "项目目标": "一句话描述项目要解决什么问题",
    "目标用户": "谁会使用这个产品或系统",
    "核心价值": "完成后能持续提供什么价值",
}

GENERIC_PLACEHOLDERS = {"无", "待补充", "待完善", "n/a", "N/A", "NA"}
DEFAULT_EVIDENCE_PLACEHOLDERS = {
    "以 task package 状态机为真，fresh package 不得直接进入实现态",
}

MAX_SPEC_FILE_BYTES = 2 * 1024 * 1024

SECTION_PLACEHOLDER_ITEMS = {
    "### 2.1 已确认事实": {"事实 A"},
    "### 2.2 关键假设": {"假设 A"},
    "### 2.3 待确认问题": {"问题 A"},
    "### 3.3 不在范围内": {"明确列出本轮不做的内容"},
    # Template slots for the minimal path embed "xxx" inside prose, so after
    # normalize_items moved to bare-token equality they are enumerated here
    # explicitly (references/templates.md `## 4. 最小实现路径` block). The
    # `标准 A/B -> verify: xxx` lines live under `## 6. 成功标准与验证方式`
    # and never flow through normalize_items, so they need no entry.
    "## 4. 最小实现路径": {"最简单可行方案：xxx", "暂不引入：xxx", "不做的抽象/配置化：xxx"},
}


RECOMMENDED_SLUG_VERBS = ("add", "fix", "refactor", "update", "remove", "docs", "test", "chore")

DEVELOPMENT_RECORD_SLUG_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})_([a-z0-9](?:[a-z0-9_-]*[a-z0-9])?)$")

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

# Branch policy shared by init (integration-branch creation) and push
# (deletion refusal). Kept in one place so the two gates cannot drift.
PROTECTED_BRANCHES = {"main", "master", "develop", "development", "staging", "production"}
PROTECTED_PREFIXES = ("release/", "hotfix/")

INTEGRATION_BRANCH_FIELD = re.compile(r"^\s*-\s*Git integration branch[：:]\s*(.+?)\s*$", re.MULTILINE)
BACKTICK_VALUE = re.compile(r"`([^`]+)`")


@dataclass(frozen=True)
class ActivePackageSelection:
    """Deterministic binding between one working branch and one active package."""

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
    return "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)


def remote_unavailable_detected(output: str) -> bool:
    """Return True only for transport-layer Git remote failures.

    Authentication, permission, missing-repo, and non-fast-forward
    rejections stay fail-closed so callers do not treat them as offline.
    """
    normalized = output.lower()
    if any(marker in normalized for marker in REMOTE_REJECTION_MARKERS):
        return False
    return any(marker in normalized for marker in REMOTE_UNAVAILABLE_MARKERS)


def validate_slug(slug: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*[a-z0-9]", slug) and not re.fullmatch(r"[a-z0-9]", slug):
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
    """Return a verb-object advisory when a Development Record slug does not start with a recommended verb.

    Returns None for non-Development-Record slugs or when the slug already follows the convention.
    """
    match = DEVELOPMENT_RECORD_SLUG_PATTERN.fullmatch(slug)
    if not match:
        return None
    suffix = match.group(2)
    first_token = suffix.split("-", 1)[0].split("_", 1)[0]
    if first_token in RECOMMENDED_SLUG_VERBS:
        return None
    return "slug 推荐 verb-object 制式（如 add-/fix-/refactor-/update-），详见 references/naming-and-commits.md"


def extract_integration_branch(spec_content: str) -> str | None:
    """Extract the exact branch recorded by the task-package template.

    Only the dedicated metadata field is accepted; prose mentioning integration
    branches must never accidentally bind a package. Legacy notes such as
    ``续用 `spec/foo`（...）`` remain readable.
    """
    match = INTEGRATION_BRANCH_FIELD.search(spec_content)
    if not match:
        return None
    value = match.group(1).strip()
    if "适用外" in value.lower() or value.lower() in {"n/a", "na", "none"}:
        return None
    quoted = BACKTICK_VALUE.findall(value)
    candidate = (quoted[0] if quoted else value.split("；", 1)[0]).strip()
    if not candidate or any(char.isspace() for char in candidate):
        return None
    if candidate.startswith("-") or candidate.endswith(("/", ".", ".lock")) or ".." in candidate:
        return None
    if any(char in candidate for char in "~^:?*[\\"):
        return None
    return candidate


def git_worktree_available(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def current_git_branch(root: Path) -> str:
    """Return the symbolic branch, or an empty string outside Git/detached HEAD."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def git_head_sha(root: Path) -> str:
    """Return the lowercase HEAD commit sha of the repo at root, or '' unresolvable.

    Compares the *project* repository (``--root``), never the skill checkout.
    Empty result covers non-Git roots, repos without commits, and Git errors;
    callers must treat '' as "not comparable" instead of a stale mismatch.
    """
    if not git_worktree_available(root):
        return ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip().lower() if result.returncode == 0 else ""


def select_active_package(
    root: Path,
    specs_root: Path,
    available: list[str],
    *,
    explicit: str | None = None,
    current_branch: str | None = None,
    git_available: bool | None = None,
) -> ActivePackageSelection:
    """Select at most one package; never aggregate ambiguous active records.

    Priority is explicit slug, exact recorded branch, conventional ``spec/<slug>``
    branch for legacy metadata, then the sole active package. Multiple unmatched
    packages fail closed so task and helper data cannot bleed across branches.
    """
    branch = current_git_branch(root) if current_branch is None else current_branch.strip()
    inside_git = git_worktree_available(root) if git_available is None else git_available
    candidates = tuple(available)
    # Explicit slug is a diagnostic/read-only selection override for route.
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

    exact = [slug for slug, integration in bindings.items() if branch and integration == branch]
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

    if len(available) == 1 and not inside_git:
        slug = available[0]
        return ActivePackageSelection(slug, "single-non-git", branch, bindings.get(slug, ""), candidates)
    if not available:
        return ActivePackageSelection(None, "none", branch, "", candidates)

    detail = f"当前分支 {branch}" if branch else ("当前处于 detached HEAD" if inside_git else "当前非 Git 工作区")
    warning = f"{detail} 无法唯一绑定活动任务包；已隐藏跨包数据，请切换到对应 integration branch 或显式传入 --slug"
    if malformed:
        warning += "；无法读取分支元数据：" + "、".join(malformed)
    return ActivePackageSelection(None, "unbound", branch, "", candidates, warning)


def validate_branch_bound_package(
    root: Path,
    slug: str,
    spec_content: str,
    *,
    allow_non_git: bool = True,
    allow_legacy_convention: bool = False,
) -> tuple[bool, str]:
    """Validate that a package is being operated on from its own branch."""
    expected = extract_integration_branch(spec_content)
    inside_git = git_worktree_available(root)
    current = current_git_branch(root)
    if not inside_git:
        return (True, "non-git") if allow_non_git else (False, "project is not a Git worktree")
    if not expected:
        if allow_legacy_convention and current == f"spec/{slug}":
            return True, current
        return False, f"task package {slug} has no valid Git integration branch metadata"
    if not current:
        return False, f"detached HEAD cannot operate task package {slug}; expected branch {expected}"
    if current != expected:
        return False, f"task package {slug} belongs to branch {expected}, current branch is {current}"
    return True, expected


def detect_language(text: str) -> str:
    """Detect 'zh' or 'en' from text based on CJK character ratio.

    Returns 'zh' when CJK characters exceed 30% of alphabetic content, else 'en'.
    Falls back to 'zh' when there is nothing to compare.
    """
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    alpha = sum(1 for ch in text if ch.isalpha())
    if alpha == 0:
        return "zh"
    return "zh" if cjk / alpha > 0.3 else "en"


def read_regular_text(path: Path) -> str:
    try:
        with open_regular_read(path, max_bytes=MAX_SPEC_FILE_BYTES) as descriptor:
            with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as handle:
                data = handle.read(MAX_SPEC_FILE_BYTES + 1)
    except SafeOpenError as exc:
        raise ValueError(str(exc)) from exc
    if len(data) > MAX_SPEC_FILE_BYTES:
        raise ValueError(f"required file exceeds {MAX_SPEC_FILE_BYTES} bytes: {path}")
    return data


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _heading_level(heading: str) -> int:
    match = re.match(r"^(#+)\s+", heading.strip())
    return len(match.group(1)) if match else 0


def heading_line_matches(line: str, heading: str) -> bool:
    """Match a complete heading, optionally followed by one parenthetical note.

    Require paired, nonempty, non-nested parentheses at the end; arbitrary
    prefixes must not make ``## 行为成效回填`` match ``## 行为成效``.
    """
    actual, expected = line.strip(), heading.strip()
    if actual == expected:
        return True
    match = re.fullmatch(re.escape(expected) + r"\s*(?:（([^（）()\r\n]+)）|\(([^（）()\r\n]+)\))", actual)
    return match is not None and bool((match.group(1) or match.group(2)).strip())


def section_exists(content: str, heading: str) -> bool:
    return any(heading_line_matches(line, heading) for line in content.splitlines())


def section_prefix_exists(content: str, heading_prefix: str) -> bool:
    """Prefix match for optional titled sections such as orchestration briefs."""
    prefix = heading_prefix.strip()
    return any(line.strip().startswith(prefix) for line in content.splitlines())


def section_checkboxes(content: str, heading: str) -> tuple[int, int]:
    lines = content.splitlines()
    in_section = False
    target_level = _heading_level(heading) or len(heading.split(" ")[0])
    total = 0
    checked = 0

    for line in lines:
        if heading_line_matches(line, heading):
            in_section = True
            continue
        if in_section and re.match(r"^#{1,%d} " % target_level, line):
            break
        if in_section:
            match = re.match(r"^\s*-\s\[(.)\]\s+.*$", line)
            if match:
                total += 1
                if match.group(1).lower() == "x":
                    checked += 1
    return total, checked


def extract_heading_title_from_content(content: str, fallback: str, suffix: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            if title.endswith(suffix):
                return title[: -len(suffix)].strip()
            return title
    return fallback


def extract_heading_title(path: Path, fallback: str, suffix: str) -> str:
    return extract_heading_title_from_content(read_text(path), fallback, suffix)


def extract_title_from_content(spec_content: str, slug: str) -> str:
    return extract_heading_title_from_content(spec_content, slug, " - 项目范围")


def extract_section_bullets(content: str, heading: str) -> list[str]:
    lines = content.splitlines()
    in_section = False
    target_level = _heading_level(heading) or len(heading.split(" ")[0])
    bullets: list[str] = []

    for line in lines:
        if heading_line_matches(line, heading):
            in_section = True
            continue
        if in_section and re.match(r"^#{1,%d} " % target_level, line):
            break
        if in_section:
            match = re.match(r"^\s*-\s+(.*)$", line)
            if match:
                bullets.append(match.group(1).strip())
    return bullets


def extract_structured_section_fields(content: str, heading_prefix: str) -> dict[str, str]:
    """Top-level ``- key: value`` fields of a titled section.

    Only bullets at the section's own indentation level count: indented
    (nested) bullets belong to their parent field and never override it.
    The first top-level occurrence of a key wins; callers that need
    fail-closed duplicate handling (e.g. the 5.4 route gate) count the
    top-level lines themselves instead of trusting the merged view.
    """
    fields: dict[str, str] = {}
    lines = content.splitlines()
    in_section = False
    prefix = heading_prefix.strip()

    for line in lines:
        if line.strip().startswith(prefix):
            in_section = True
            continue
        if in_section and re.match(r"^#{1,3} ", line):
            break
        if not in_section:
            continue
        if line[:1] in (" ", "\t"):
            continue
        match = TOP_LEVEL_BULLET_FIELD.match(line)
        if match:
            key = match.group(1).strip()
            if key not in fields:
                fields[key] = match.group(2).strip()

    return fields


VERIFICATION_SCOPE_HEADING_PREFIX = "### 5.1 验证策略"
VERIFICATION_SCOPE_LEVELS = ("package", "integration", "project")
VERIFICATION_SCOPE_REQUIRED_FIELDS = (
    "范围级别",
    "变更对象",
    "快速检查",
    "集成检查",
    "全项目检查",
    "升级触发",
)


def _verification_scope_section_lines(spec_content: str) -> tuple[list[str], int]:
    """Return the first real 5.1 section and count real headings outside fences."""
    in_fence = False
    in_section = False
    headings = 0
    section_lines: list[str] = []
    for line in spec_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if heading_line_matches(line, VERIFICATION_SCOPE_HEADING_PREFIX):
            headings += 1
            if not in_section:
                in_section = True
                continue
            break
        if in_section and re.match(r"^#{1,3} ", line):
            break
        if in_section:
            section_lines.append(line)
    return section_lines, headings


def verification_scope_section_exists(spec_content: str) -> bool:
    """Return whether a real (non-code-fenced) 5.1 section is present."""
    _lines, headings = _verification_scope_section_lines(spec_content)
    return headings > 0


def verification_scope_fields(spec_content: str) -> dict[str, str]:
    """Return the declared verification strategy for a package.

    The section is intentionally optional for historical records. New
    packages get it from the template, while old archives remain readable
    without being regraded against a contract that did not exist at creation.
    """
    section_lines, _headings = _verification_scope_section_lines(spec_content)
    if not section_lines and not verification_scope_section_exists(spec_content):
        return {}
    fields: dict[str, str] = {}
    for line in section_lines:
        if line[:1] in (" ", "\t"):
            continue
        match = re.match(r"^-\s*([^：:]+?)\s*[：:]\s*(.*)$", line)
        if match:
            fields.setdefault(match.group(1).strip(), match.group(2).strip())
    return fields


def verification_scope_errors(spec_content: str) -> list[str]:
    """Validate the opt-in verification-scope contract fail-closed."""
    section_lines, heading_count = _verification_scope_section_lines(spec_content)
    if heading_count == 0:
        return []
    fields = verification_scope_fields(spec_content)
    errors = [
        f"`{VERIFICATION_SCOPE_HEADING_PREFIX}` 缺少字段：{field}"
        for field in VERIFICATION_SCOPE_REQUIRED_FIELDS
        if not fields.get(field)
    ]
    if heading_count > 1:
        errors.append(f"`{VERIFICATION_SCOPE_HEADING_PREFIX}` 出现 {heading_count} 次，必须唯一")
    duplicate_counts: dict[str, int] = {field: 0 for field in VERIFICATION_SCOPE_REQUIRED_FIELDS}
    for line in section_lines:
        if line[:1] in (" ", "\t"):
            continue
        match = re.match(r"^-\s*([^：:]+?)\s*[：:]\s*(.*)$", line)
        if match and match.group(1).strip() in duplicate_counts:
            duplicate_counts[match.group(1).strip()] += 1
    errors.extend(
        f"`{VERIFICATION_SCOPE_HEADING_PREFIX}` 字段 {field} 出现 {count} 次，必须唯一"
        for field, count in duplicate_counts.items()
        if count > 1
    )
    for field in VERIFICATION_SCOPE_REQUIRED_FIELDS:
        value = fields.get(field, "").strip()
        if value and is_placeholder_value(value):
            errors.append(f"`{VERIFICATION_SCOPE_HEADING_PREFIX}` 字段 {field} 仍是占位内容")
        if value.startswith("<") and value.endswith(">"):
            errors.append(f"`{VERIFICATION_SCOPE_HEADING_PREFIX}` 字段 {field} 必须填写真实内容")
    level = normalize_verification_scope_value(fields.get("范围级别", ""))
    if level and level not in VERIFICATION_SCOPE_LEVELS:
        errors.append(
            f"`{VERIFICATION_SCOPE_HEADING_PREFIX}` 范围级别必须是单 token "
            f"（{' / '.join(VERIFICATION_SCOPE_LEVELS)}），当前为 {level!r}"
        )
    if level in {"package", "integration"}:
        full_project = fields.get("全项目检查", "").strip()
        if full_project and not (full_project.startswith("适用外") or "仅在" in full_project or "仅当" in full_project):
            errors.append(
                f"`{VERIFICATION_SCOPE_HEADING_PREFIX}` package/integration 范围必须说明"
                "全项目检查的升级条件或适用外理由"
            )
    required_real_fields = {
        "package": ("快速检查",),
        "integration": ("快速检查", "集成检查"),
        "project": ("快速检查", "集成检查", "全项目检查"),
    }.get(level, ())
    for field in required_real_fields:
        value = fields.get(field, "").strip()
        if value.startswith(
            (
                "适用外",
                "不适用",
                "仅在",
                "仅当",
                "条件：",
                "条件:",
                "由全项目检查",
                "未执行",
                "未运行",
                "未验证",
                "未检查",
                "未跑",
                "跳过",
                "不执行",
            )
        ):
            errors.append(f"`{VERIFICATION_SCOPE_HEADING_PREFIX}` {level} 范围必须填写真实的{field}，不能标记为适用外")
    return errors


def verification_scope(spec_content: str) -> str | None:
    """Return the normalized declared scope, or ``None`` for legacy records."""
    value = normalize_verification_scope_value(verification_scope_fields(spec_content).get("范围级别", ""))
    return value or None


def normalize_verification_scope_value(value: str) -> str:
    """Normalize a scope token and ignore the template's explanatory suffix."""
    token = value.strip().strip("`")
    token = re.split(r"[；;]", token, maxsplit=1)[0]
    return token.strip().strip("`").lower()


def normalize_items(items: list[str], ignored: set[str] | None = None) -> list[str]:
    ignore = set(GENERIC_PLACEHOLDERS)
    if ignored:
        ignore.update(ignored)
    normalized: list[str] = []
    for item in items:
        if not item or item in ignore:
            continue
        # Placeholder slots are matched as whole tokens only: content that
        # merely mentions "xxx" (e.g. a pending question about xxx.py) is
        # real. Template bullets that embed "xxx" in prose are enumerated
        # per heading in SECTION_PLACEHOLDER_ITEMS instead.
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


def _has_pending_question_state(text: str) -> bool:
    """Detect current blockers, independently of narrative/question grammar."""
    return bool(
        re.search(
            r"[？?]|无权限|无数据"
            r"|未(?:确认|解决|授权|澄清|决定|决策|确定|明确)|尚未"
            # A bounded subject may sit between 待 and the pending verb
            # (待用户授权 / 待用户确认范围), so limit the gap to a short
            # clause without its own punctuation or question mark.
            r"|待[^，,。；;？?]{0,6}?(?:授权|确认|解决|澄清|决定|决策|提供|部署)"
            r"|(?:仍|还|尚)(?:需|要|待)|(?<![无不])需(?:要)?[^，,；;。]*授权",
            text,
        )
    )


def _has_unresolved_question(text: str) -> bool:
    """Residual questions override a closure, even without a question mark.

    Inspect only live prose for struck-out items. In particular, 未配置 is a
    deployment condition, not itself an unresolved decision.
    """
    return _has_pending_question_state(text) or bool(
        re.search(r"是否|能否|可否|如何|怎么|何时|谁|哪|但|然而|不过", text)
    )


def _is_closed_pending_question(item: str) -> bool:
    """Accept a whole struck-out question plus an explicit closure record.

    A strike alone, an incidental 已确认, and unrecognized live prose all
    stay pending. Deliberately recognize a small grammar rather than infer
    resolution from arbitrary narrative or strip the historical question.
    """
    match = re.fullmatch(r"~~([^~\n]+)~~\s*(?:→|->)\s*(.+)", item.strip())
    if not match or not match.group(1).strip():
        return False
    resolution = match.group(2).replace("`", "").strip()
    if _has_unresolved_question(resolution):
        return False
    reference = r"(?:（[^（）()]+）|\([^（）()]+\))?"
    chapter = r"见第\s*[0-9]+\s*节"
    authorization_reference = rf"(?:（{chapter}）|\({chapter}\))?"
    closure_patterns = (
        # Completion, optionally with a paired explanatory note.
        rf"已(?:执行完成|完成|解决|关闭)\s*{reference}[。.]?",
        # Authorization is not completion unless completion is also recorded.
        rf"已(?:于\s*\d{{4}}-\d{{2}}-\d{{2}}\s*)?用户授权\s*{reference}"
        r"\s*[，,；;]\s*已执行完成[。.]?",
        # An authorized scope decision can close a blocker while retaining
        # an explicit deployment configuration and honest failure behavior.
        # Keep dated map-scope authorization and chapter references explicit;
        # neither arbitrary authorization prose nor arbitrary footnotes close it.
        r"(?:[0-9]{4}-[0-9]{2}-[0-9]{2}\s*)?用户追加(?:地图多坐标系)?授权\s*"
        rf"{authorization_reference}\s*[：:]\s*不再阻塞[^，,；;。.!！]+"
        r"(?:[；;]\s*[A-Za-z_][A-Za-z0-9_]*\s*仍为部署侧配置[，,]\s*"
        r"未配置时[^，,；;。.!！]+报\s*(?:503(?:/错误)?|错误)"
        r"(?:[，,]\s*内置底图与运行时模板不受影响)?)?[。.]?",
    )
    return any(re.fullmatch(pattern, resolution) for pattern in closure_patterns)


def _is_no_pending_declaration(item: str) -> bool:
    """Return True only for an explicit "no pending questions" declaration."""
    normalized = item.strip().replace("`", "")
    no_pending_patterns = (
        r"无[。.]?",
        r"无(?:（[^（）()]*）|\([^（）()]*\))(?:。|[：:][^？?]+)?",
        # The explicit 无： token distinguishes this from 无权限/无数据.
        r"无[：:][^？?]*",
        r"(?:none|no pending|nothing pending)",
        # Scope is a single declarative phrase, not preceding/trailing prose.
        r"(?:本轮[^，,；;。！？?：:（）()~]*?)?无待(?:决策项|确认问题)[。.]?",
    )
    if not any(re.fullmatch(pattern, normalized, re.I) for pattern in no_pending_patterns):
        return False

    # Declarations can explain past decisions and ordinary contrasts (但).
    # Unlike strike-closure footnotes, those are not themselves new blockers.
    # Mask only explicit negated pending vocabulary; all current-state markers
    # and real question marks are checked before considering historical prose.
    # 而非/不是/已非 directly negate the pending word they precede (e.g.
    # 「…已记入第 7 节风险而非待确认项」 states the item is NOT a pending
    # question), so the pair is stripped before live-state detection.
    live_prose = re.sub(
        r"(?:而[非不]|不是|已非)无?(?:阻塞性)?待(?:决策项|确认问题|确认项|授权|解决|澄清|决定)",
        "",
        normalized,
    )
    live_prose = re.sub(r"无(?:阻塞性)?待(?:决策项|确认问题|确认项)", "", live_prose)
    if _has_pending_question_state(live_prose):
        return False

    # A historical 是否 must have an explicit disposition in the same phrase:
    # decided variables, or research work requiring no user decision. Merely
    # saying 已确认 is insufficient. Do not consume the disposition or any later
    # prose, and never span another interrogative or a sentence/parenthesis.
    interrogative = r"是否|能否|可否|如何|怎么|何时|谁|哪"
    subject = rf"(?:(?!{interrogative})[^；;。！？?（）()\n])"
    value = r"[^\s，,；;。！？?（）()]+"
    live_prose = re.sub(
        rf"是否{subject}+?(?=均已决策为{value}|已内化为{subject}+?无需用户决策)",
        "",
        live_prose,
    )
    return not re.search(interrogative, live_prose)


def extract_pending_questions(spec_content: str) -> list[str]:
    items = normalize_items(
        extract_section_bullets(spec_content, "### 2.3 待确认问题"),
        ignored=SECTION_PLACEHOLDER_ITEMS["### 2.3 待确认问题"],
    )
    return [item for item in items if not _is_no_pending_declaration(item) and not _is_closed_pending_question(item)]


def extract_problem_definition_values(spec_content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for label in PROJECT_DEFINITION_PLACEHOLDERS:
        match = re.search(rf"- \*\*{re.escape(label)}\*\*：(.*)", spec_content)
        if match:
            values[label] = match.group(1).strip()
    return values


def clarification_gaps(spec_content: str) -> list[str]:
    gaps: list[str] = []
    definition_values = extract_problem_definition_values(spec_content)
    for label, placeholder in PROJECT_DEFINITION_PLACEHOLDERS.items():
        value = definition_values.get(label, "")
        if not value or value == placeholder:
            gaps.append(f"`spec.md` 缺少有效{label}")

    required_sections = (
        "### 2.1 已确认事实",
        "### 2.2 关键假设",
        "### 3.3 不在范围内",
        "## 4. 最小实现路径",
    )
    for heading in required_sections:
        ignored = SECTION_PLACEHOLDER_ITEMS.get(heading, set())
        items = normalize_items(extract_section_bullets(spec_content, heading), ignored=ignored)
        if not items:
            gaps.append(f"`spec.md` 缺少有效区块：{heading}")

    pending_heading = "### 2.3 待确认问题"
    pending_bullets = extract_section_bullets(spec_content, pending_heading)
    pending_real = normalize_items(
        pending_bullets,
        ignored=SECTION_PLACEHOLDER_ITEMS[pending_heading],
    )
    has_decision = any(_is_no_pending_declaration(bullet) for bullet in pending_bullets)
    if not pending_real and not has_decision:
        gaps.append(f"`spec.md` 缺少有效区块：{pending_heading}")

    return gaps


ORCHESTRATION_ROUTES = ("local", "explore", "build", "review", "external")
ORCHESTRATION_HEADING_PREFIX = "### 5.4 编排策略"
# Historical packages wrote explicit opt-outs instead of a route token;
# these are equivalent to `route: local` and must not fail the gate.
_ORCHESTRATION_OPT_OUTS = ("未启用", "适用外", "不启用", "单线", "单线程")
# One tokenizer for every consumer of the 5.4 top level: the generic field
# parser and the route gate must see exactly the same bullets, so a route the
# parser recognizes (including `- route : value` spacing) can never be
# invisible to the gate.
TOP_LEVEL_BULLET_FIELD = re.compile(r"^-\s*([^：:]+?)\s*[：:]\s*(.*)$")
BULLET_LINE = re.compile(r"^-\s?")


def _orchestration_top_level_lines(spec_content: str) -> list[str]:
    """Unindented bullet lines of the 5.4 section (nested bullets excluded)."""
    collected: list[str] = []
    in_section = False
    for line in spec_content.splitlines():
        if line.strip().startswith(ORCHESTRATION_HEADING_PREFIX):
            in_section = True
            continue
        if in_section and re.match(r"^#{1,3} ", line):
            break
        if not in_section:
            continue
        if line[:1] in (" ", "\t"):
            continue
        if BULLET_LINE.match(line):
            collected.append(line)
    return collected


def _is_orchestration_opt_out_declaration(bullet: str) -> bool:
    """An opt-out is the bullet's own subject, never a sibling mask.

    ``- 未启用（本包 route=local…）`` and ``- 适用外：单线执行…`` declare the
    package stays local; ``- ownership: 主线程单线程合流`` merely mentions the
    vocabulary inside a sibling field and must not bypass route validation.
    """
    text = BULLET_LINE.sub("", bullet, count=1).strip()
    key_match = TOP_LEVEL_BULLET_FIELD.match(f"- {text}")
    subject = key_match.group(1).strip() if key_match else text
    return subject.startswith(_ORCHESTRATION_OPT_OUTS)


def _top_level_field(line: str) -> re.Match | None:
    return TOP_LEVEL_BULLET_FIELD.match(line)


def orchestration_strategy_errors(spec_content: str) -> list[str]:
    """Structural errors in the optional `### 5.4 编排策略` section.

    Absence of the section is fine (local by default). When present, an
    explicit top-level `route` field always validates first: it must be a
    single token from ORCHESTRATION_ROUTES (backticks tolerated, multi-token
    values like ``build（主线程）+ review`` rejected) and appear at most once.
    Only when no route field exists may an anchored opt-out declaration
    (未启用 / 适用外 / 不启用 / 单线 / 单线程 as the bullet's own subject)
    count as `route: local`. Nested (indented) bullets never participate.
    """
    if not section_exists(spec_content, ORCHESTRATION_HEADING_PREFIX) and not section_prefix_exists(
        spec_content, ORCHESTRATION_HEADING_PREFIX
    ):
        return []
    bullets = _orchestration_top_level_lines(spec_content)
    route_bullets = [
        line for line in bullets if (match := _top_level_field(line)) is not None and match.group(1).strip() == "route"
    ]
    if len(route_bullets) > 1:
        return [f"`{ORCHESTRATION_HEADING_PREFIX}` 顶层 route 字段出现 {len(route_bullets)} 次，必须唯一"]
    if route_bullets:
        match = _top_level_field(route_bullets[0])
        if match is None:
            return [f"`{ORCHESTRATION_HEADING_PREFIX}` 缺少 route 字段"]
        route_value = match.group(2).strip()
        token = route_value.strip("`").strip()
        if token not in ORCHESTRATION_ROUTES:
            allowed = " / ".join(ORCHESTRATION_ROUTES)
            return [f"`{ORCHESTRATION_HEADING_PREFIX}` route 必须是单 token（{allowed}），当前为 {route_value!r}"]
        return []
    if any(_is_orchestration_opt_out_declaration(line) for line in bullets):
        return []
    return [f"`{ORCHESTRATION_HEADING_PREFIX}` 缺少 route 字段"]


def count_tasks(tasks_content: str) -> tuple[int, int]:
    total = 0
    completed = 0
    for line in tasks_content.splitlines():
        match = re.match(r"^\s*-\s\[(.)\]\s+(.*)$", line)
        if not match:
            continue
        total += 1
        if match.group(1).lower() == "x":
            completed += 1
    return total, completed


def count_unfinished_tasks(tasks_content: str) -> int:
    return sum(
        1
        for line in tasks_content.splitlines()
        if (match := re.match(r"^\s*-\s\[(.)\]\s+(.*)$", line)) and match.group(1).lower() != "x"
    )


def count_task_detail_gaps(tasks_content: str) -> tuple[int, int]:
    missing_boundary = 0
    missing_verify = 0
    has_boundary = False
    has_verify = False
    seen_task = False

    for raw_line in tasks_content.splitlines():
        task_match = re.match(r"^\s*-\s\[(.)\]\s+(.*)$", raw_line.rstrip())
        if task_match:
            if seen_task:
                if not has_boundary:
                    missing_boundary += 1
                if not has_verify:
                    missing_verify += 1
            has_boundary = False
            has_verify = False
            seen_task = True
            continue

        detail_match = re.match(r"^\s*-\s+(boundary|verify):\s*(.*)$", raw_line.rstrip())
        if detail_match and seen_task:
            value = detail_match.group(2).strip()
            if detail_match.group(1) == "boundary" and value:
                has_boundary = True
            if detail_match.group(1) == "verify" and value:
                has_verify = True

    if seen_task:
        if not has_boundary:
            missing_boundary += 1
        if not has_verify:
            missing_verify += 1

    return missing_boundary, missing_verify


def count_unchecked_checkboxes(content: str) -> int:
    """Count every checkbox whose marker is not a completed ``x``/``X``."""
    return sum(1 for marker in re.findall(r"(?m)^\s*-\s\[(.)\]\s+", content) if marker.lower() != "x")


# --------------------------------------------------------------------------- #
# Shared control primitives and task-record graph
# --------------------------------------------------------------------------- #

MAX_RUNTIME_JSON_BYTES = 256 * 1024
TASK_ID_PATTERN = re.compile(r"^(?:task_\d{4,}|task-[a-z0-9][a-z0-9-]{0,58})$")
STABLE_TASK_ID_PATTERN = re.compile(r"^task-[a-z0-9][a-z0-9-]{0,58}$")


class SpecControlError(Exception):
    """Operational or validation failure with a user-facing message."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise SpecControlError(f"refusing symlink path: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise SpecControlError(f"refusing symlink path: {path}")
    os.chmod(path, 0o700)
    return path


def read_json_file(path: Path, *, max_bytes: int = MAX_RUNTIME_JSON_BYTES) -> dict:
    try:
        with open_regular_read(path, max_bytes=max_bytes) as descriptor:
            with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as handle:
                data = handle.read(max_bytes + 1)
    except SafeOpenError as exc:
        raise SpecControlError(str(exc)) from exc
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise SpecControlError(f"file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SpecControlError(f"file must contain a JSON object: {path}")
    return payload


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
        """A task is independently executable only with boundary and verify."""
        return bool(self.boundary.strip()) and bool(self.verify.strip())


def parse_task_records(tasks_content: str) -> list[TaskRecord]:
    """Parse ``tasks.md`` checkbox tasks into position-stable records.

    Position ids (``task_0001`` ...) and explicit ``- id:`` names share one
    namespace, so dependency references survive task reordering.
    """
    records: list[TaskRecord] = []
    lines = tasks_content.splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r"^\s*-\s\[(.)\]\s+(.*)$", lines[index].rstrip())
        if not match:
            index += 1
            continue
        marker, title = match.group(1), match.group(2).strip()
        block: list[str] = [f"- [{marker}] {title}"]
        index += 1
        own_block = list(block)
        boundary = ""
        verify = ""
        while index < len(lines):
            # ``depends-on`` is a task detail exactly like boundary/verify so the
            # line reaches both ``block`` and ``own_block``; otherwise a
            # non-indented ``- depends-on:`` under an explicit-id task would be
            # dropped from ``block_text`` and silently vanish from the graph.
            detail = re.match(r"^\s*-\s+(boundary|verify|depends-on):\s*(.*)$", lines[index].rstrip())
            if detail:
                block.append(lines[index].strip())
                own_block.append(lines[index].strip())
                if detail.group(1) == "boundary":
                    boundary = detail.group(2).strip()
                elif detail.group(1) == "verify":
                    verify = detail.group(2).strip()
                index += 1
                continue
            next_task = re.match(r"^\s*-\s\[(.)\]\s+", lines[index].rstrip())
            if next_task or lines[index].strip().startswith("#"):
                break
            # Continuation prose of the same task block.
            if lines[index].strip():
                block.append(lines[index].strip())
                if lines[index][0].isspace():
                    own_block.append(lines[index].strip())
            index += 1
        explicit = [line.removeprefix("- id:").strip() for line in block if line.startswith("- id:")]
        if len(explicit) > 1 or (explicit and not STABLE_TASK_ID_PATTERN.fullmatch(explicit[0])):
            raise SpecControlError(f"invalid or duplicate explicit task id in {title!r}")
        records.append(
            TaskRecord(
                id=explicit[0] if explicit else f"task_{len(records) + 1:04d}",
                title=title,
                checked=marker.lower() == "x",
                # A "!" marker or an unchecked task titled 阻塞 is a block
                # signal; completed tasks describing resolved blocking are not.
                blocked=marker == "!" or ("阻塞" in title and marker.lower() != "x"),
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
        match = re.match(r"^- depends-on:\s*(.*)$", line)
        if match:
            refs.extend(part.strip() for part in match.group(1).split(","))
    return tuple(refs)


def dependency_errors(records: list[TaskRecord]) -> list[str]:
    """Validate the whole graph in O(tasks + edges), including completed cycles."""
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
    queue = deque(key for key, degree in indegree.items() if degree == 0)
    while queue:
        for child in children[queue.popleft()]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    cyclic = [key for key, degree in indegree.items() if degree]
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
        and not item.blocked
        and item.executable
        and all(ref in completed for ref in task_dependencies(item))
    }


def resolve_active_slug(
    root: Path,
    specs_dir_name: str | None,
    explicit: str | None,
    *,
    require_branch: bool = True,
) -> str:
    """Resolve one active package from its branch, with optional read-only override."""
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
        branch_ok, branch_detail = validate_branch_bound_package(
            root,
            selection.slug,
            spec_content,
            allow_legacy_convention=selection.mode == "branch-convention",
        )
        if not branch_ok:
            raise SpecControlError(branch_detail)
        return selection.slug
    if not packages:
        raise SpecControlError("no active Spec package under the project root")
    raise SpecControlError(
        "multiple active Spec packages; " + (selection.warning or ("pass --slug explicitly: " + ", ".join(packages)))
    )


def checklist_passed(content: str) -> bool:
    return bool(re.search(r"(?m)^\*\*验收结果\*\*：通过\s*$", content))


def checklist_complete(content: str) -> bool:
    return checklist_passed(content) and count_unchecked_checkboxes(content) == 0


def extract_evidence_fields(checklist_content: str) -> dict[str, str]:
    """Parse ``## 验收证据`` fields, including nested bullet blocks.

    Top-level entries must be unindented ``- key：value`` lines. When the value
    is empty, subsequent indented ``- ...`` lines are joined into that field so
    common nested evidence blocks remain machine-readable. Indented
    ``旧：`` / ``新：`` style lines no longer leak as top-level keys.
    """
    fields: dict[str, str] = {}
    lines = checklist_content.splitlines()
    in_section = False
    current_key: str | None = None
    nested_parts: list[str] = []

    def flush_current() -> None:
        nonlocal current_key, nested_parts
        if current_key is None:
            return
        existing = fields.get(current_key, "").strip()
        if not existing and nested_parts:
            fields[current_key] = "\n".join(nested_parts).strip()
        current_key = None
        nested_parts = []

    for line in lines:
        if heading_line_matches(line, "## 验收证据"):
            in_section = True
            continue
        if in_section and re.match(r"^##\s+", line) and not heading_line_matches(line, "## 验收证据"):
            flush_current()
            break
        if not in_section:
            continue

        top_level = re.match(r"^-\s*([^：:]+)[：:]\s*(.*)$", line)
        if top_level:
            flush_current()
            current_key = top_level.group(1).strip()
            value = top_level.group(2).strip()
            fields[current_key] = value
            nested_parts = []
            continue

        if current_key is not None:
            nested = re.match(r"^\s+-\s+(.*)$", line)
            if nested:
                nested_parts.append(nested.group(1).strip())
                continue
            if line.strip() == "":
                continue
            # Non-bullet indented prose still belongs to the open field.
            if line.startswith((" ", "\t")) and line.strip():
                nested_parts.append(line.strip())
                continue
            flush_current()

    flush_current()
    return fields


def has_command_evidence(checklist_content: str) -> bool:
    """Return True when checklist evidence includes a non-placeholder command/script proof."""
    fields = extract_evidence_fields(checklist_content)
    for key in ("脚本验证", "测试", "构建"):
        value = fields.get(key, "")
        if value and not is_placeholder_value(value):
            return True
    return False


EVIDENCE_ANCHOR_KEY = "证据锚点"
EVIDENCE_ANCHOR_VALUE_PATTERN = re.compile(r"^HEAD\s+([0-9a-fA-F]{7,40})\s+@\s+(\S.*)$")
EVIDENCE_ANCHOR_FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_ANCHOR_FORMAT_GUIDANCE = "证据锚点须为完整 40 位 SHA"


def evidence_anchor_format_errors(checklist_content: str) -> list[str]:
    """Fail-closed SHA-format check for a *present* evidence anchor line.

    A well-shaped ``HEAD <sha> @ <time>`` anchor whose SHA is not a full
    40-character hex string (``^[0-9a-f]{40}$`` on the lowercased token) is a
    format failure with explicit guidance: the gate must neither crash nor
    misreport it as HEAD drift. An absent anchor, or a value without the
    ``HEAD <sha> @`` shape, stays not-applicable (exact legacy behavior).
    """
    value = extract_evidence_fields(checklist_content).get(EVIDENCE_ANCHOR_KEY, "")
    if not value:
        return []
    match = EVIDENCE_ANCHOR_VALUE_PATTERN.match(value)
    if not match:
        return []
    if EVIDENCE_ANCHOR_FULL_SHA_PATTERN.fullmatch(match.group(1).lower()):
        return []
    return [
        f"证据锚点格式不合规：{EVIDENCE_ANCHOR_FORMAT_GUIDANCE}"
        f"（须匹配 ^[0-9a-f]{{40}}$）；当前记录：HEAD {match.group(1)} @ {match.group(2)}；"
        f"请以 git rev-parse HEAD 的完整 40 位输出更新 {EVIDENCE_ANCHOR_KEY}"
    ]


def extract_evidence_anchor(checklist_content: str) -> tuple[str, str] | None:
    """Parse the optional evidence freshness anchor from ``## 验收证据``.

    Accepted form (top-level bullet, per references/commands.md /spec:check):

        - 证据锚点：HEAD <sha> @ <ISO-8601 timestamp>

    Returns ``(sha, timestamp)`` — sha lowercased — when a well-formed anchor
    exists, else ``None``. An absent *or malformed* anchor never activates the
    freshness gate: legacy packages keep their exact current behavior.
    """
    value = extract_evidence_fields(checklist_content).get(EVIDENCE_ANCHOR_KEY, "")
    if not value:
        return None
    match = EVIDENCE_ANCHOR_VALUE_PATTERN.match(value)
    if not match:
        return None
    return match.group(1).lower(), match.group(2).strip()


def load_reference_templates(root: Path) -> dict[str, str]:
    reference_path = root / "references" / "templates.md"
    content = read_text(reference_path)
    filenames = ("spec.md", "tasks.md", "checklist.md")
    templates: dict[str, str] = {}

    for filename in filenames:
        pattern = rf"## `{re.escape(filename)}`\n\n```markdown\n(.*?)\n```"
        match = re.search(pattern, content, re.S)
        if not match:
            raise ValueError(f"template block not found for {filename} in {reference_path}")
        templates[filename] = match.group(1)

    return templates


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


def scope_from_changed_paths(
    paths: list[str],
    specs_dirs: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Extract trusted Spec Development Record slugs from repository-relative paths."""
    roots: set[tuple[str, ...]] = {(".spec",), (".trae",)}
    for name in specs_dirs:
        candidate = Path(name)
        if not name.strip() or candidate.is_absolute() or candidate == Path(".") or ".." in candidate.parts:
            raise ValueError("custom specs root must be a trusted relative path")
        if any(part in RESERVED_SPECS_DIR_PARTS for part in candidate.parts):
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
                        # organize's dated retired container, never a package slug
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
    """Map recorded integration branches to active package slugs."""
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
    return {branch: tuple(slugs) for branch, slugs in bindings.items()}


def active_package_slugs(specs_dir: Path) -> list[str]:
    """Slugs of active packages: triad present, not archive/hidden/symlink.

    Single enumeration contract for route/status views; skips the same
    non-package entries as check_all_spec_packages.package_dirs.
    """
    if not specs_dir.exists():
        return []
    packages: list[str] = []
    for path in sorted(specs_dir.iterdir()):
        if path.name == "archive" or path.name.startswith(".") or not path.is_dir() or path.is_symlink():
            continue
        if (path / "spec.md").exists() and (path / "tasks.md").exists() and (path / "checklist.md").exists():
            packages.append(path.name)
    return packages


def add_specs_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--specs-dir",
        default=DEFAULT_SPECS_DIR,
        help=("Relative directory under root for specs storage; resolved paths must stay under root (default: .spec)"),
    )

#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared validation for closing findings without leaving user-owned work.

The Development Record triad remains the task source of truth. This module only
validates how a finding was disposed before another carrier may enter a closed
state.
"""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

CLOSURE_VERSION = 1
CLOSURE_HEADING = "## 问题处置"
DISPOSITION_TYPES = {
    "resolved_current",
    "resolved_followup",
    "accepted_risk",
    "external_blocked",
    "non_actionable",
}
ISSUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
SLUG_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")
PLACEHOLDER_VALUES = {"", "无", "none", "n/a", "na", "待补充", "待完善", "xxx"}
SUMMARY_PLACEHOLDERS = ("待补充", "待完善", "xxx")
REQUIRED_SUMMARY_HEADINGS = (
    "## 交付结论",
    "## 假设回顾",
    "## 交付范围",
    "## 简化决策",
    "## 变更边界",
    "## 验证证据",
    "## 门禁证据",
    CLOSURE_HEADING,
)
MAX_CLOSURE_FILE_BYTES = 2 * 1024 * 1024
MAX_FOLLOWUP_NODES = 128
MAX_FOLLOWUP_DEPTH = 32


class IssueClosureError(ValueError):
    """A finding disposition or closure artifact is not machine-verifiable."""


def _real_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.replace("`", "").strip()
    return normalized.lower() not in PLACEHOLDER_VALUES and not any(
        token in normalized.lower() for token in ("待补充", "待完善", "xxx")
    )


def _regular_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _read_regular_text(path: Path) -> str:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise IssueClosureError(f"required closure file missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise IssueClosureError(f"required closure file must be regular: {path}")
    if metadata.st_size > MAX_CLOSURE_FILE_BYTES:
        raise IssueClosureError(f"required closure file exceeds {MAX_CLOSURE_FILE_BYTES} bytes: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise IssueClosureError(f"required closure file is unreadable: {path}: {exc}") from exc


def parse_task_records(tasks_content: str) -> dict[str, dict[str, Any]]:
    """Return stable order-based task records used by closure links."""
    from spec_package_support import parse_task_records as parse_task_blocks

    return {
        task.id: {"id": task.id, "subject": task.title, "completed": task.checked}
        for task in parse_task_blocks(tasks_content)
    }


def closure_payload(issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": CLOSURE_VERSION, "issues": issues}


def render_issue_closure(issues: list[dict[str, Any]]) -> str:
    payload = json.dumps(closure_payload(issues), ensure_ascii=False, indent=2, sort_keys=True)
    return f"{CLOSURE_HEADING}\n\n```json\n{payload}\n```"


def extract_issue_closure(summary_content: str) -> dict[str, Any] | None:
    heading_index = summary_content.find(CLOSURE_HEADING)
    if heading_index < 0:
        return None
    tail = summary_content[heading_index + len(CLOSURE_HEADING) :]
    next_heading = re.search(r"(?m)^##\s+", tail)
    section = tail[: next_heading.start()] if next_heading else tail
    fence = section.find("```json")
    if fence < 0:
        raise IssueClosureError("问题处置 section must contain one JSON object")
    payload_text = section[fence + len("```json") :].lstrip()
    try:
        payload, end = json.JSONDecoder().raw_decode(payload_text)
    except json.JSONDecodeError as exc:
        raise IssueClosureError(f"问题处置 JSON is invalid: {exc}") from exc
    if not payload_text[end:].lstrip().startswith("```"):
        raise IssueClosureError("问题处置 section must contain one JSON object")
    if not isinstance(payload, dict):
        raise IssueClosureError("问题处置 JSON must be an object")
    return payload


def validate_completion_summary(
    summary_content: str,
    *,
    specs_root: Path | None = None,
    current_slug: str | None = None,
    current_tasks_content: str | None = None,
    require_v1: bool = True,
    verify_targets: bool = True,
    verify_followups: bool = True,
    baseline_equivalent: Callable[[str], bool] | None = None,
) -> list[str]:
    """Validate a generated completion summary and its structured closeout."""
    failures: list[str] = []
    for heading in REQUIRED_SUMMARY_HEADINGS:
        if heading not in summary_content:
            failures.append(f"completion summary missing section: {heading}")
    for placeholder in SUMMARY_PLACEHOLDERS:
        if placeholder in summary_content:
            failures.append(f"completion summary contains placeholder: {placeholder}")
    try:
        payload = extract_issue_closure(summary_content)
    except IssueClosureError as exc:
        failures.append(str(exc))
        return failures
    if payload is None:
        if require_v1:
            failures.append("completion summary missing v1 issue closure block")
        return failures
    if payload.get("version") != CLOSURE_VERSION:
        failures.append(f"unsupported issue closure version: {payload.get('version')!r}")
        return failures
    failures.extend(
        validate_issue_dispositions(
            payload.get("issues"),
            specs_root=specs_root,
            current_slug=current_slug,
            current_tasks_content=current_tasks_content,
            verify_targets=verify_targets,
            verify_followups=verify_followups,
            baseline_equivalent=baseline_equivalent,
        )
    )
    return failures


def _load_archive_node(
    specs_root: Path,
    slug: str,
    baseline_equivalent: Callable[[str], bool] | None = None,
) -> tuple[list[str], tuple[str, ...]]:
    failures: list[str] = []
    if not SLUG_PATTERN.fullmatch(slug):
        return [f"follow-up slug is not a Development Record slug: {slug}"], ()
    package = specs_root / "specs" / "archive" / slug
    if package.is_symlink() or not package.is_dir():
        return [f"follow-up Development Record is not archived: {slug}"], ()

    contents: dict[str, str] = {}
    for filename in ("spec.md", "tasks.md", "checklist.md", "completion-summary.md"):
        try:
            contents[filename] = _read_regular_text(package / filename)
        except IssueClosureError as exc:
            failures.append(str(exc))
    if not all(filename in contents for filename in ("spec.md", "tasks.md", "checklist.md", "completion-summary.md")):
        return failures, ()

    from check_spec_package import overall_check_passed
    from spec_package_support import checklist_passed, count_unchecked_checkboxes, count_unfinished_tasks

    unfinished = count_unfinished_tasks(contents["tasks.md"])
    if unfinished:
        failures.append(f"follow-up archive has unfinished tasks: {slug}")
    if count_unchecked_checkboxes(contents["checklist.md"]) or not checklist_passed(contents["checklist.md"]):
        failures.append(f"follow-up archive checklist is not passed: {slug}")
    # Same legacy-baseline doctrine as check_all_spec_packages: an archive
    # byte-identical to the authoritative baseline was graded under the gates
    # of its day and is never retrospectively re-graded by *current* gates
    # here. Structural checks above and the closure validation below stay in
    # force regardless.
    gate_exempt = baseline_equivalent is not None and baseline_equivalent(slug)
    if not gate_exempt and not overall_check_passed(
        contents["spec.md"],
        contents["tasks.md"],
        contents["checklist.md"],
        slug=slug,
    ):
        failures.append(f"follow-up archive Spec gate is not passed: {slug}")
    summary_failures = validate_completion_summary(
        contents["completion-summary.md"],
        specs_root=specs_root,
        current_slug=slug,
        current_tasks_content=contents["tasks.md"],
        require_v1=True,
        verify_targets=True,
        verify_followups=False,
    )
    failures.extend(f"follow-up archive closure invalid: {failure}" for failure in summary_failures)

    outgoing: list[str] = []
    try:
        payload = extract_issue_closure(contents["completion-summary.md"])
    except IssueClosureError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("issues"), list):
        for issue in payload["issues"]:
            if isinstance(issue, dict) and issue.get("disposition") == "resolved_followup":
                target = issue.get("followUpSpecSlug")
                if isinstance(target, str) and target:
                    outgoing.append(target)
    return failures, tuple(outgoing)


def validate_followup_archives(
    specs_root: Path,
    targets: list[str] | tuple[str, ...],
    *,
    origin_slug: str | None = None,
    baseline_equivalent: Callable[[str], bool] | None = None,
) -> list[str]:
    """Validate a bounded, memoized follow-up graph without recursive calls."""
    failures: list[str] = []
    graph: dict[str, tuple[str, ...]] = {}
    queue = list(dict.fromkeys(targets))

    while queue:
        slug = queue.pop()
        if slug in graph:
            continue
        if len(graph) >= MAX_FOLLOWUP_NODES:
            failures.append(f"follow-up graph exceeds {MAX_FOLLOWUP_NODES} archives")
            break
        node_failures, outgoing = _load_archive_node(specs_root, slug, baseline_equivalent)
        graph[slug] = outgoing
        failures.extend(node_failures)
        for target in outgoing:
            if target not in graph:
                queue.append(target)

    indegree = {slug: 0 for slug in graph}
    for outgoing in graph.values():
        for target in outgoing:
            if target in indegree:
                indegree[target] += 1
    ready = [slug for slug, degree in indegree.items() if degree == 0]
    order: list[str] = []
    while ready:
        slug = ready.pop()
        order.append(slug)
        for target in graph.get(slug, ()):
            if target not in indegree:
                continue
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if len(order) != len(graph):
        cycle_nodes = sorted(slug for slug, degree in indegree.items() if degree > 0)
        failures.append(f"follow-up Development Record cycle detected: {', '.join(cycle_nodes)}")
        return failures

    depth = {slug: 1 for slug in graph}
    if origin_slug:
        depth.update({slug: 2 for slug in targets if slug in graph})
    for slug in order:
        for target in graph.get(slug, ()):
            if target in depth:
                depth[target] = max(depth[target], depth[slug] + 1)
    too_deep = [(slug, value) for slug, value in depth.items() if value > MAX_FOLLOWUP_DEPTH]
    if too_deep:
        slug, value = max(too_deep, key=lambda item: item[1])
        failures.append(f"follow-up graph exceeds depth {MAX_FOLLOWUP_DEPTH}: {slug} at depth {value}")
    return failures


def validate_issue_dispositions(
    issues: Any,
    *,
    specs_root: Path | None = None,
    current_slug: str | None = None,
    current_tasks_content: str | None = None,
    verify_targets: bool = True,
    verify_followups: bool = True,
    baseline_equivalent: Callable[[str], bool] | None = None,
) -> list[str]:
    """Validate every finding has one complete, machine-checkable disposition."""
    if not isinstance(issues, list):
        return ["issue dispositions must be an array"]

    failures: list[str] = []
    task_records = parse_task_records(current_tasks_content or "")
    seen_ids: set[str] = set()
    followup_targets: list[str] = []
    for index, issue in enumerate(issues):
        label = f"issueDispositions[{index}]"
        if not isinstance(issue, dict):
            failures.append(f"{label} must be an object")
            continue
        issue_id = issue.get("id")
        if not isinstance(issue_id, str) or not ISSUE_ID_PATTERN.fullmatch(issue_id):
            failures.append(f"{label}.id must be a stable identifier")
        elif issue_id in seen_ids:
            failures.append(f"duplicate issue id: {issue_id}")
        else:
            seen_ids.add(issue_id)
        if not _real_text(issue.get("summary")):
            failures.append(f"{label}.summary must be non-placeholder text")

        disposition = issue.get("disposition")
        if disposition not in DISPOSITION_TYPES:
            failures.append(f"{label}.disposition is invalid: {disposition!r}")
            continue
        actionable = issue.get("actionable")
        if not isinstance(actionable, bool):
            failures.append(f"{label}.actionable must be boolean and is required")
            actionable = True
        if disposition == "non_actionable" and actionable is not False:
            failures.append(f"{label} non_actionable requires actionable=false")

        required_fields: tuple[str, ...]
        if disposition == "resolved_current":
            required_fields = ("taskId", "evidence")
        elif disposition == "resolved_followup":
            required_fields = ("followUpSpecSlug", "evidence")
        elif disposition == "accepted_risk":
            required_fields = ("owner", "rationale", "reviewTrigger")
        elif disposition == "external_blocked":
            required_fields = ("dependency", "owner", "retryTrigger")
        else:
            required_fields = ("rationale",)
        for field in required_fields:
            if not _real_text(issue.get(field)):
                failures.append(f"{label}.{field} must be non-placeholder text")

        if disposition == "resolved_current" and _real_text(issue.get("taskId")) and verify_targets:
            task_id = str(issue["taskId"])
            task = task_records.get(task_id)
            if task is None:
                failures.append(f"{label}.taskId does not exist in current tasks: {task_id}")
            elif not task["completed"]:
                failures.append(f"{label}.taskId is not completed: {task_id}")
        if disposition == "resolved_followup" and _real_text(issue.get("followUpSpecSlug")) and verify_followups:
            target = str(issue["followUpSpecSlug"])
            if current_slug and target == current_slug:
                failures.append(f"{label}.followUpSpecSlug cannot reference the current spec")
            elif specs_root is None:
                failures.append(f"{label}.followUpSpecSlug cannot be verified without specs_root")
            else:
                followup_targets.append(target)

    if verify_followups and specs_root is not None and followup_targets:
        failures.extend(
            f"follow-up graph: {failure}"
            for failure in validate_followup_archives(
                specs_root,
                followup_targets,
                origin_slug=current_slug,
                baseline_equivalent=baseline_equivalent,
            )
        )
    return failures


def parse_issue_disposition_arg(value: str) -> dict[str, Any]:
    """Parse one inline JSON object or ``@path`` object supplied by a CLI."""
    raw = value.strip()
    if raw.startswith("@"):
        path = Path(raw[1:]).expanduser()
        if not _regular_file(path):
            raise IssueClosureError(f"issue disposition file must be a regular file: {path}")
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise IssueClosureError(f"cannot read issue disposition file: {path}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IssueClosureError(f"issue disposition must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise IssueClosureError("one --issue-disposition value must contain a JSON object")
    return parsed

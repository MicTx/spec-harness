#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Render a /spec:check-style validation summary from a Spec package.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dashboard_support import (
    HEALTH_BLOCKED,
    HEALTH_OK,
    HEALTH_RISK,
    Dashboard,
    task_strip_from_records,
)
from handoff_support import HANDOFF_FILENAME, read_handoff, validate_handoff
from spec_package_support import (
    EVIDENCE_ANCHOR_KEY,
    ORCHESTRATION_HEADING_PREFIX,
    SpecControlError,
    add_specs_dir_arg,
    checklist_passed,
    clarification_gaps,
    count_task_detail_gaps,
    count_tasks,
    count_unchecked_checkboxes,
    dependency_errors,
    evidence_anchor_format_errors,
    extract_evidence_anchor,
    extract_evidence_fields,
    extract_pending_questions,
    extract_section_bullets,
    extract_title_from_content,
    git_head_sha,
    has_command_evidence,
    is_development_record_slug,
    is_placeholder_value,
    normalize_items,
    normalize_verification_scope_value,
    orchestration_strategy_errors,
    parse_task_records,
    read_regular_text,
    resolve_specs_child,
    resolve_specs_root,
    section_checkboxes,
    section_exists,
    section_prefix_exists,
    validate_branch_bound_package,
    validate_slug,
    verification_scope,
    verification_scope_errors,
    verification_scope_fields,
    verification_scope_section_exists,
)
from update_checkpoint_support import detect_update_checkpoint


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a /spec:check-style validation summary from a Spec package.")
    parser.add_argument("--slug", required=True, help="Task package slug, e.g. 2026-06-12_spec-self-hosting")
    parser.add_argument(
        "--root",
        default=".",
        help="Project root where the .spec directory exists (default: current directory)",
    )
    add_specs_dir_arg(parser)
    parser.add_argument("--ascii", action="store_true", help="Render dashboard glyphs as ASCII fallback")
    parser.add_argument("--compact", action="store_true", help="Render metrics as a single line")
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help=(
            "Output format: markdown dashboard (default) or json with top-level "
            "converged/gaps fields for machine consumers (e.g. Stop hooks)"
        ),
    )
    return parser.parse_args()


def parse_tasks(tasks_content: str) -> tuple[int, int, int, int]:
    total, completed = count_tasks(tasks_content)
    missing_boundary, missing_verify = count_task_detail_gaps(tasks_content)
    return total, completed, missing_boundary, missing_verify


_TEMPLATE_TASK_PATTERNS = (
    "固化问题定义、关键假设与非目标",
    "初始化最小项目骨架",
    "实现核心功能",
    "打通关键链路",
    "最小化复核点",
    "完成验收检查并补齐文档",
)


def is_likely_template(tasks_content: str) -> bool:
    lines = tasks_content.splitlines()
    unchecked_count = 0
    template_hit_count = 0
    for line in lines:
        match = re.match(r"^\s*-\s\[\s\]\s+(.*)$", line)
        if match:
            unchecked_count += 1
            text = match.group(1).strip()
            if any(pattern in text for pattern in _TEMPLATE_TASK_PATTERNS):
                template_hit_count += 1
    return unchecked_count > 0 and template_hit_count == unchecked_count


BLOCKED_TASK_MARKER = "!"


def has_blocked_tasks(tasks_content: str) -> bool:
    """Return True when a task is explicitly blocked.

    Only the ``!`` marker, an *unchecked* task title containing 阻塞, or
    a standalone 阻塞： status line is authoritative. A completed task
    whose title mentions 阻塞 (解除依赖阻塞并修复) describes resolved
    blocking and must not keep the package unconverged.
    """
    for line in tasks_content.splitlines():
        match = re.match(r"^\s*-\s\[(.)\]\s+(.*)$", line)
        if match:
            marker, text_line = match.group(1), match.group(2)
            if marker == BLOCKED_TASK_MARKER or ("阻塞" in text_line and marker.lower() != "x"):
                return True
            continue
        if re.match(r"^\s*(?:-\s*)?阻塞[：:]", line):
            return True
    return False


EVIDENCE_KEYS = ("外部对标", "脚本验证", "旧新对比", "差异边界", "行为成效")


def result_label(passed: bool, partial: bool = False) -> str:
    if passed:
        return "通过"
    if partial:
        return "部分通过"
    return "待修复"


# --------------------------------------------------------------------------- #
# GateResults: single computation of all gate values
# --------------------------------------------------------------------------- #


@dataclass
class GateResults:
    """All intermediate gate-computation results, computed exactly once.

    Both ``overall_check_passed`` and ``render`` consume this dataclass
    instead of recomputing the same 12+ functions independently.
    """

    # Assumptions & scope
    pending_questions: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    minimal_path: list[str] = field(default_factory=list)
    task_contract_errors: list[str] = field(default_factory=list)
    spec_clarification_gaps: list[str] = field(default_factory=list)

    # Orchestration strategy (optional 5.4 section)
    orchestration_section_exists: bool = False
    orchestration_errors: list[str] = field(default_factory=list)

    # Verification strategy (optional for historical packages; required for
    # newly initialized packages whose template carries 5.1).
    verification_scope: str | None = None
    verification_scope_fields: dict[str, str] = field(default_factory=dict)
    verification_scope_errors: list[str] = field(default_factory=list)
    verification_scope_section_exists: bool = False

    @property
    def orchestration_ok(self) -> bool:
        return not self.orchestration_errors

    # Tasks
    total_tasks: int = 0
    completed_tasks: int = 0
    missing_boundary: int = 0
    missing_verify: int = 0
    unchecked_items: int = 0
    # spec.md 功能框（§3 核心功能/扩展功能复选框）：声明交付的功能框必须
    # 在交付验证后勾选；零复选框保持 vacuous 通过（无框 spec 合法）。
    unchecked_spec_functions: int = 0
    blocked_tasks: bool = False
    all_tasks_done: bool = False
    passed_checklist: bool = False
    template_warning: bool = False

    # Checklist sections
    consistency_section_exists: bool = False
    consistency_total: int = 0
    consistency_checked: int = 0
    consistency_ok: bool = False

    structure_section_exists: bool = False
    structure_total: int = 0
    structure_checked: int = 0
    structure_ok: bool = False

    boundary_regression_section_exists: bool = False
    boundary_regression_total: int = 0
    boundary_regression_checked: int = 0
    boundary_regression_ok: bool = False

    # Evidence
    evidence_fields: dict[str, str] = field(default_factory=dict)
    verification_scope_evidence: str = ""
    evidence_present: bool = False
    evidence_filled_count: int = 0
    command_evidence_ok: bool = False
    evidence_refill_ok: bool = False

    # Evidence freshness anchor (optional 证据锚点：HEAD <sha> @ <iso> marker).
    # ok: True fresh / False stale or format-invalid (SHA not 40-char hex) /
    # None not applicable (absent anchor, archived review, or
    # non-Git/unresolvable root — the *comparison* never fails there).
    evidence_anchor: tuple[str, str] | None = None
    evidence_anchor_ok: bool | None = None
    evidence_anchor_detail: str = ""
    evidence_anchor_evaluated: bool = False

    # Derived booleans
    development_record_ok: bool = False
    assumptions_ok: bool = False
    simplicity_ok: bool = False
    boundary_ok: bool = False
    function_ok: bool = False
    has_new_gate_markers: bool = False
    base_ok: bool = False
    overall_ok: bool = False


def compute_gate_results(
    spec_content: str,
    tasks_content: str,
    checklist_content: str,
    slug: str | None = None,
    root: Path | None = None,
) -> GateResults:
    """Compute all gate values exactly once.

    This is the single source of truth for all intermediate computation.
    ``overall_check_passed``, ``render``, and ``route_for_package`` all
    consume this instead of recomputing independently.

    ``root`` is the *project* repository root used to resolve the current
    HEAD for the evidence freshness anchor. ``None`` (routing, reports,
    text-level helpers, archived read-only review) leaves the anchor gate
    unevaluated; it never fails in those contexts.
    """
    r = GateResults()

    # Assumptions & scope
    r.pending_questions = extract_pending_questions(spec_content)
    r.out_of_scope = normalize_items(
        extract_section_bullets(spec_content, "### 3.3 不在范围内"),
        ignored={"明确列出本轮不做的内容"},
    )
    r.minimal_path = normalize_items(extract_section_bullets(spec_content, "## 4. 最小实现路径"))
    r.spec_clarification_gaps = clarification_gaps(spec_content)
    r.orchestration_errors = orchestration_strategy_errors(spec_content)
    orchestration_seen = section_exists(spec_content, ORCHESTRATION_HEADING_PREFIX)
    r.orchestration_section_exists = orchestration_seen or section_prefix_exists(
        spec_content, ORCHESTRATION_HEADING_PREFIX
    )
    r.verification_scope = verification_scope(spec_content)
    r.verification_scope_fields = verification_scope_fields(spec_content)
    r.verification_scope_errors = verification_scope_errors(spec_content)
    r.verification_scope_section_exists = verification_scope_section_exists(spec_content)
    try:
        r.task_contract_errors = dependency_errors(parse_task_records(tasks_content))
    except SpecControlError as exc:
        r.task_contract_errors = [str(exc)]
    r.spec_clarification_gaps.extend(r.task_contract_errors)

    # Tasks
    r.total_tasks, r.completed_tasks, r.missing_boundary, r.missing_verify = parse_tasks(tasks_content)
    r.unchecked_items = count_unchecked_checkboxes(checklist_content)
    # Same fail-closed count extended to spec.md: :267 previously only counted
    # the checklist, so spec.md functional boxes could stay unchecked forever
    # while the checker still said 通过.
    r.unchecked_spec_functions = count_unchecked_checkboxes(spec_content)
    r.blocked_tasks = has_blocked_tasks(tasks_content)
    r.all_tasks_done = r.total_tasks > 0 and r.completed_tasks == r.total_tasks
    r.passed_checklist = checklist_passed(checklist_content)
    r.template_warning = is_likely_template(tasks_content)

    # Checklist sections
    r.consistency_section_exists = section_exists(checklist_content, "## 跨载体一致性")
    r.consistency_total, r.consistency_checked = section_checkboxes(checklist_content, "## 跨载体一致性")
    r.consistency_ok = (
        r.consistency_section_exists and r.consistency_total > 0 and r.consistency_total == r.consistency_checked
    )

    r.structure_section_exists = section_exists(checklist_content, "## 项目结构与文档可信度")
    r.structure_total, r.structure_checked = section_checkboxes(checklist_content, "## 项目结构与文档可信度")
    r.structure_ok = r.structure_section_exists and r.structure_total > 0 and r.structure_total == r.structure_checked

    # Boundary regression (optional gate, appears-in-force like the two above):
    # negative-sample regression rows (out-of-boundary edits, task/rule order
    # swap, verify bypass entrances). A row that does not apply to this round
    # is still checked off with an inline `N/A：<reason>` annotation; the gate
    # itself stays purely total == checked.
    r.boundary_regression_section_exists = section_exists(checklist_content, "## 边界回归")
    r.boundary_regression_total, r.boundary_regression_checked = section_checkboxes(checklist_content, "## 边界回归")
    r.boundary_regression_ok = (
        r.boundary_regression_section_exists
        and r.boundary_regression_total > 0
        and r.boundary_regression_total == r.boundary_regression_checked
    )

    # Evidence
    r.evidence_fields = extract_evidence_fields(checklist_content)
    if r.verification_scope_section_exists:
        r.verification_scope_evidence = r.evidence_fields.get("验证范围", "").strip()
        normalized_evidence_scope = normalize_verification_scope_value(r.verification_scope_evidence)
        if is_placeholder_value(r.verification_scope_evidence):
            r.verification_scope_errors.append(
                "`## 验收证据` 缺少与 spec.md 一致的验证范围：需填写 package/integration/project"
            )
        elif normalized_evidence_scope != (r.verification_scope or ""):
            r.verification_scope_errors.append(
                "`## 验收证据` 验证范围与 spec.md 不一致："
                f"声明 {r.verification_scope or '未声明'}，记录 {r.verification_scope_evidence}"
            )
    r.evidence_present = any(key in r.evidence_fields for key in EVIDENCE_KEYS)
    r.evidence_filled_count = sum(
        1 for key in EVIDENCE_KEYS if key in r.evidence_fields and not is_placeholder_value(r.evidence_fields[key])
    )
    r.command_evidence_ok = has_command_evidence(checklist_content)
    r.evidence_refill_ok = r.evidence_present and r.evidence_filled_count > 0 and r.command_evidence_ok

    # Evidence freshness anchor: appears-in-force like the optional gates
    # (check_spec_package optional-gate precedent). An absent anchor keeps the
    # exact legacy behavior; a well-formed anchor must match the project repo's
    # current HEAD or the stale evidence fails the gate. A present anchor whose
    # SHA is not a full 40-char hex string fails closed with format guidance
    # instead of being misreported as HEAD drift (fail-closed UX).
    r.evidence_anchor = extract_evidence_anchor(checklist_content)
    r.evidence_anchor_evaluated = root is not None
    anchor_format_errors = evidence_anchor_format_errors(checklist_content) if r.evidence_anchor_evaluated else []
    if r.evidence_anchor is None:
        r.evidence_anchor_ok = None
        r.evidence_anchor_detail = ""
    elif anchor_format_errors:
        r.evidence_anchor_ok = False
        r.evidence_anchor_detail = "；".join(anchor_format_errors)
    else:
        anchor_sha, anchor_time = r.evidence_anchor
        head_sha = git_head_sha(root) if root is not None else ""
        if not head_sha:
            r.evidence_anchor_ok = None
            r.evidence_anchor_detail = (
                f"适用外：非 Git 工作区或 HEAD 不可解析，跳过锚点比对（锚点 HEAD {anchor_sha[:12]} @ {anchor_time}）"
            )
        elif head_sha == anchor_sha:
            r.evidence_anchor_ok = True
            r.evidence_anchor_detail = f"锚点 HEAD {anchor_sha[:12]} @ {anchor_time} 与当前 HEAD 一致"
        else:
            r.evidence_anchor_ok = False
            r.evidence_anchor_detail = (
                f"证据过期需重跑取证：锚点 HEAD {anchor_sha[:12]} @ {anchor_time}，"
                f"当前 HEAD 已前移至 {head_sha[:12]}；重跑取证后把 {EVIDENCE_ANCHOR_KEY} 更新为当前 HEAD"
            )

    # Derived booleans
    r.development_record_ok = slug is None or is_development_record_slug(slug)
    r.assumptions_ok = not r.spec_clarification_gaps and not r.pending_questions and bool(r.out_of_scope)
    r.simplicity_ok = len(r.minimal_path) >= 3
    r.boundary_ok = r.missing_boundary == 0 and r.missing_verify == 0
    r.function_ok = (
        r.all_tasks_done
        and r.passed_checklist
        and r.unchecked_items == 0
        and r.unchecked_spec_functions == 0
        and not r.blocked_tasks
    )

    r.has_new_gate_markers = (
        r.consistency_section_exists
        or r.structure_section_exists
        or r.boundary_regression_section_exists
        or r.verification_scope_section_exists
    )
    verification_scope_ok = not r.verification_scope_errors
    r.base_ok = (
        r.development_record_ok
        and r.assumptions_ok
        and r.simplicity_ok
        and r.boundary_ok
        and r.function_ok
        and r.command_evidence_ok
        and r.orchestration_ok
        and verification_scope_ok
        # Anchor-in-force: only a stale (present, resolvable, mismatched)
        # anchor blocks; None (absent/skip) keeps legacy behavior.
        and r.evidence_anchor_ok is not False
    )
    if r.has_new_gate_markers:
        # Every optional gate is conditional on its own section (absent =
        # not applicable, never blocks) — including 跨载体一致性. The
        # unconditional consistency_ok here previously contradicted the
        # documented optional-gate contract ("optional gates that are not
        # enabled neither participate in the verdict nor produce failures")
        # and blocked packages whose only new-gate marker was 边界回归
        # (shipped by the default template).
        r.overall_ok = (
            r.base_ok
            and (not r.consistency_section_exists or r.consistency_ok)
            and (not r.structure_section_exists or r.structure_ok)
            and (not r.boundary_regression_section_exists or r.boundary_regression_ok)
            and r.evidence_refill_ok
        )
    else:
        r.overall_ok = r.base_ok

    return r


def package_physical_state_errors(package_dir: Path) -> list[str]:
    """Fail closed on disk state that triad text cannot represent."""
    status = detect_update_checkpoint(package_dir)
    if status.state == "active":
        return ["存在未完成 update checkpoint：rollback 回滚更新，或 complete 确认保留"]
    if status.state == "corrupted":
        return ["存在未完成 update checkpoint：检查点记录损坏，需人工恢复"]
    return []


def overall_check_passed(
    spec_content: str,
    tasks_content: str,
    checklist_content: str,
    slug: str | None = None,
    root: Path | None = None,
) -> bool:
    """Return True when all required gates pass.

    Delegates to ``compute_gate_results`` so the computation happens exactly
    once per invocation.  Callers that also need the intermediate values
    (e.g. ``render``, ``route_for_package``) should call
    ``compute_gate_results`` directly and read ``r.overall_ok``.
    ``root`` enables the evidence freshness anchor gate; see
    ``compute_gate_results``.
    """
    return compute_gate_results(spec_content, tasks_content, checklist_content, slug=slug, root=root).overall_ok


def convergence_state(results: GateResults) -> tuple[bool, list[str]]:
    """Derive convergence semantics from already-computed gate fields.

    Converged = all tasks checked + every checklist checkbox checked +
    acceptance passed + no blocked task + evidence satisfied. Gaps reuse
    the failure details GateResults already computed; no second detector.
    """
    r = results
    gaps: list[str] = list(r.task_contract_errors)
    if r.total_tasks == 0:
        gaps.append("任务清单为空")
    elif not r.all_tasks_done:
        gaps.append(f"未勾任务 {r.total_tasks - r.completed_tasks} 项")
    if r.unchecked_items > 0:
        gaps.append(f"未勾检查清单 {r.unchecked_items} 项")
    if r.unchecked_spec_functions > 0:
        gaps.append(f"spec.md 未勾功能框 {r.unchecked_spec_functions} 项")
    if not r.passed_checklist:
        gaps.append("验收未通过")
    if r.blocked_tasks:
        gaps.append("存在阻塞任务")
    if r.verification_scope_errors:
        gaps.extend(r.verification_scope_errors)
    evidence_satisfied = r.evidence_refill_ok if r.has_new_gate_markers else r.command_evidence_ok
    if not evidence_satisfied:
        gaps.append(
            f"证据缺口：证据字段 {r.evidence_filled_count}/{len(EVIDENCE_KEYS)}；"
            f"命令证据{'有' if r.command_evidence_ok else '无'}"
        )
    return not gaps, gaps


def convergence_lines(converged: bool, gaps: list[str], *, compact: bool = False) -> list[str]:
    """Markdown lines for the convergence verdict (new in v0.7; additive output)."""
    status = "已收敛" if converged else "未收敛"
    lines = [f"- 收敛状态：{status}"] if compact else ["## 收敛状态", "", f"- 收敛状态：{status}"]
    lines.extend(f"- 差距：{gap}" for gap in gaps)
    return lines


def gate_rows(r: GateResults, slug: str | None) -> list[tuple[str, bool | None, str]]:
    """(label, passed | None(适用外), evidence) rows shared by render and JSON.

    Single source of truth: the markdown alerts and the machine-facing
    ``overallGaps`` JSON field must always describe the same failures.
    """
    return [
        (
            "假设与范围",
            r.assumptions_ok,
            f"待确认 {len(r.pending_questions)}；范围外 {len(r.out_of_scope)}；"
            f"澄清缺口 {len(r.spec_clarification_gaps)}",
        ),
        ("简洁性", r.simplicity_ok, f"最小实现路径 {len(r.minimal_path)} 条"),
        (
            "编排策略",
            r.orchestration_ok if r.orchestration_section_exists else None,
            "; ".join(r.orchestration_errors) or "route 合法",
        ),
        (
            "验证范围",
            (not r.verification_scope_errors) if r.verification_scope_section_exists else None,
            "; ".join(r.verification_scope_errors)
            or (
                f"范围级别 {r.verification_scope}；证据记录 {r.verification_scope_evidence}"
                if r.verification_scope
                else "历史包未声明"
            ),
        ),
        ("开发记录规范", r.development_record_ok, f"slug `{slug}`"),
        (
            "变更边界",
            r.boundary_ok,
            f"任务 {r.total_tasks}；缺边界说明 {r.missing_boundary}；缺验证说明 {r.missing_verify}",
        ),
        (
            "功能与验证",
            r.function_ok,
            f"任务完成 {r.completed_tasks}/{r.total_tasks}；检查清单未勾选 {r.unchecked_items}；"
            f"spec.md 未勾功能框 {r.unchecked_spec_functions}；"
            f"验收{'通过' if r.passed_checklist else '未通过'}",
        ),
        (
            "跨载体一致性",
            r.consistency_ok if r.consistency_section_exists else None,
            f"勾选 {r.consistency_checked}/{r.consistency_total}",
        ),
        (
            "项目结构与文档可信度",
            r.structure_ok if r.structure_section_exists else None,
            f"勾选 {r.structure_checked}/{r.structure_total}",
        ),
        (
            "边界回归",
            r.boundary_regression_ok if r.boundary_regression_section_exists else None,
            f"勾选 {r.boundary_regression_checked}/{r.boundary_regression_total}",
        ),
        (
            "证据与回填",
            r.evidence_refill_ok if r.has_new_gate_markers else r.command_evidence_ok,
            f"证据字段 {r.evidence_filled_count}/{len(EVIDENCE_KEYS)}；"
            f"命令证据{'有' if r.command_evidence_ok else '无'}",
        ),
        (
            "证据新鲜度",
            r.evidence_anchor_ok,
            r.evidence_anchor_detail
            or "适用外：未记录证据锚点（可选；记录 `证据锚点：HEAD <sha> @ <时间>` 后须与当前 HEAD 一致）",
        ),
    ]


def gate_failure_details(r: GateResults, slug: str | None) -> dict[str, str]:
    """Failed applicable gates as {label: evidence} for alerts and JSON."""
    return {label: evidence for label, passed, evidence in gate_rows(r, slug) if passed is not None and not passed}


def render(
    slug: str,
    title: str,
    spec_content: str,
    tasks_content: str,
    checklist_content: str,
    results: GateResults | None = None,
    *,
    ascii_mode: bool = False,
    compact: bool = False,
    archived: bool = False,
) -> str:
    if results is None:
        results = compute_gate_results(spec_content, tasks_content, checklist_content, slug=slug)

    r = results
    from report_spec_package import parse_tasks as parse_task_records_for_strip

    _sections, tasks = parse_task_records_for_strip(tasks_content)
    strip, strip_labels = task_strip_from_records(tasks)

    # (label, passed | None(适用外), evidence)
    gates: list[tuple[str, bool | None, str]] = gate_rows(r, slug)

    applicable = [(label, passed, evidence) for label, passed, evidence in gates if passed is not None]

    failed_labels = [label for label, passed, _evidence in applicable if not passed]
    failure_details = {label: evidence for label, passed, evidence in applicable if not passed}
    alerts: list[str] = []
    if "假设与范围" in failure_details:
        alerts.append(f"需求范围尚未就绪：{failure_details['假设与范围']}")
    if "简洁性" in failure_details:
        alerts.append("最小实现方案尚未锁定")
    if "开发记录规范" in failure_details:
        alerts.append(f"项目记录命名需要修正：{failure_details['开发记录规范']}")
    if "变更边界" in failure_details:
        alerts.append(f"任务执行说明不完整：{failure_details['变更边界']}")
    if "功能与验证" in failure_details:
        alerts.append(f"项目尚未完成验收：{failure_details['功能与验证']}")
    if "编排策略" in failure_details:
        alerts.append(f"编排策略尚未满足：{failure_details['编排策略']}")
    if "验证范围" in failure_details:
        alerts.append(f"验证范围尚未满足：{failure_details['验证范围']}")
    for label in (
        "跨载体一致性",
        "项目结构与文档可信度",
        "边界回归",
        "证据与回填",
        "证据新鲜度",
    ):
        if label in failure_details:
            alerts.append(f"{label}尚未满足：{failure_details[label]}")
    if r.template_warning:
        alerts.append("任务仍是初始化模板，尚未按本项目需求细化")

    current_task = next((task.text for task in tasks if not task.is_completed), None)
    if r.overall_ok:
        next_step = "整理交付结果并完成归档"
    elif current_task:
        next_step = current_task
    else:
        next_step = "修复未通过的验收项"

    health = HEALTH_BLOCKED if r.blocked_tasks else (HEALTH_OK if r.overall_ok else HEALTH_RISK)
    detail = ["验收结果：通过", f"有效证据：{r.evidence_filled_count} 项"] if r.overall_ok else []
    # Advisory only: an unevaluated-but-possible anchor absence never blocks;
    # it just means HEAD drift after evidence capture would go unnoticed.
    if detail and r.evidence_anchor_evaluated and r.evidence_anchor is None:
        detail.append("提示：未记录证据锚点（可选），建议在验收证据中记录 `证据锚点：HEAD <sha> @ <时间>`")
    if archived:
        detail = ["包状态：已归档（只读复查）", *detail]

    dash = Dashboard(
        slug=slug,
        stage="check",
        title=title,
        done=r.completed_tasks,
        total=r.total_tasks,
        health=health,
        strip=strip,
        strip_labels=strip_labels,
        metrics={
            "阻塞": "1" if r.blocked_tasks else "0",
            "待确认": str(len(r.pending_questions)),
            "门禁": "通过" if r.overall_ok else f"未过 {len(failed_labels)}",
            "证据": str(r.evidence_filled_count),
            "范围": r.verification_scope or "",
        },
        current=current_task,
        alerts=alerts,
        next_step=next_step,
        detail=detail,
        ascii_mode=ascii_mode,
        compact=compact,
    )
    base = dash.render()
    converged, gaps = convergence_state(r)
    convergence = convergence_lines(converged, gaps, compact=compact)
    if not convergence:
        return base
    if not base:
        return "\n".join(convergence)
    return base + ("\n" if compact else "\n\n") + "\n".join(convergence)


def main() -> int:
    args = parse_args()
    try:
        slug = validate_slug(args.slug.strip())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    root = Path(args.root).resolve()
    try:
        specs_root = resolve_specs_root(root, args.specs_dir)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        active_spec = resolve_specs_child(specs_root, "specs", slug, "spec.md")
        archived_spec = resolve_specs_child(specs_root, "specs", "archive", slug, "spec.md")
        archived = not active_spec.is_file() and archived_spec.is_file()
        prefix = ("specs", "archive", slug) if archived else ("specs", slug)
        spec_path = resolve_specs_child(specs_root, *prefix, "spec.md")
        tasks_path = resolve_specs_child(specs_root, *prefix, "tasks.md")
        checklist_path = resolve_specs_child(specs_root, *prefix, "checklist.md")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for path in (spec_path, tasks_path, checklist_path):
        if not path.exists():
            print(f"error: required file missing: {path}", file=sys.stderr)
            return 1

    # Optional fifth member: a handoff document is validated only when it
    # exists; packages without one (all legacy packages) are unaffected.
    # An unreadable document (oversize, non-UTF-8, symlink/irregular) fails
    # closed with a clean diagnostic instead of a traceback.
    package_dir = resolve_specs_child(specs_root, *prefix)
    handoff_path = package_dir / HANDOFF_FILENAME
    if handoff_path.is_file():
        try:
            handoff_content = read_handoff(package_dir)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"error: unreadable handoff document: {exc}", file=sys.stderr)
            return 1
        if handoff_content is not None:
            handoff_errors = validate_handoff(handoff_content)
            if handoff_errors:
                print("error: invalid handoff document:", file=sys.stderr)
                for error in handoff_errors:
                    print(f"  - {error}", file=sys.stderr)
                return 1

    try:
        spec_content = read_regular_text(spec_path)
        tasks_content = read_regular_text(tasks_path)
        checklist_content = read_regular_text(checklist_path)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: invalid task package file: {exc}", file=sys.stderr)
        return 1
    if archived:
        # Archived packages completed the done/push chain already; their
        # integration branch is merged and deleted, so branch binding cannot
        # be revalidated and must not block a read-only review.
        branch_detail = "archived (branch binding already closed at done/push)"
    else:
        branch_ok, branch_detail = validate_branch_bound_package(root, slug, spec_content)
        if not branch_ok:
            print(f"error: {branch_detail}", file=sys.stderr)
            return 1
    title = extract_title_from_content(spec_content, slug)
    # Archived read-only review never runs the HEAD comparison: the archived
    # package's integration branch is merged and deleted, so the anchor is
    # historical by construction and must not fail the review.
    results = compute_gate_results(
        spec_content,
        tasks_content,
        checklist_content,
        slug=slug,
        root=None if archived else root,
    )
    if args.format == "json":
        converged, gaps = convergence_state(results)
        # overallGaps mirrors the markdown alerts' failure details so machine
        # consumers can explain a non-zero exit even when converged=true;
        # exit code and converged semantics are unchanged.
        overall_gaps = [f"{label}：{evidence}" for label, evidence in gate_failure_details(results, slug).items()]
        print(
            json.dumps(
                {
                    "slug": slug,
                    "overall": results.overall_ok,
                    "converged": converged,
                    "gaps": gaps,
                    "overallGaps": overall_gaps,
                    "verificationScope": results.verification_scope,
                    "archived": archived,
                },
                ensure_ascii=False,
            )
        )
        return 0 if results.overall_ok else 1
    print(
        render(
            slug,
            title,
            spec_content,
            tasks_content,
            checklist_content,
            results,
            ascii_mode=args.ascii,
            compact=args.compact,
            archived=archived,
        )
    )
    return 0 if results.overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())

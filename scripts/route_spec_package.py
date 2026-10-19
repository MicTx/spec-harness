#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""
Render the internal spec stage decision that is compatible with Claude Code and Codex-style skill invocation.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import sys
from pathlib import Path

from check_spec_package import compute_gate_results
from dashboard_support import (
    HEALTH_BLOCKED,
    HEALTH_OK,
    HEALTH_RISK,
    Dashboard,
    default_pipeline,
    project_gap_message,
    task_strip_from_records,
    worst_health,
)
from handoff_support import freshness, parse_handoff, read_handoff, validate_handoff
from report_spec_package import parse_tasks
from spec_package_support import (
    SpecControlError,
    active_package_slugs,
    add_specs_dir_arg,
    extract_title_from_content,
    parse_task_records,
    read_regular_text,
    ready_task_ids,
    resolve_specs_child,
    resolve_specs_root,
    select_active_package,
    task_dependencies,
    utc_now,
    validate_branch_bound_package,
    validate_slug,
)
from update_checkpoint_support import CheckpointStatus, detect_update_checkpoint


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render an internal spec stage decision from current Spec package state."
    )
    parser.add_argument("--slug", help="Specific task package slug to inspect")
    parser.add_argument(
        "--root",
        default=".",
        help="Project root where the .spec directory exists (default: current directory)",
    )
    add_specs_dir_arg(parser)
    parser.add_argument("--ascii", action="store_true", help="Render dashboard glyphs as ASCII fallback")
    parser.add_argument("--compact", action="store_true", help="Render metrics as a single line")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument(
        "--brief",
        action="store_true",
        help="Bounded aggregated read: route decision + per-task contracts in one envelope (implies JSON)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=DEFAULT_MAX_ITEMS,
        help=f"Task-slice item budget for --brief (default {DEFAULT_MAX_ITEMS}, hard cap {HARD_MAX_ITEMS})",
    )
    parser.add_argument(
        "--page-token",
        default=None,
        help="Opaque continuation token from a prior --brief envelope's `next` field",
    )
    return parser.parse_args()


# Shared enumeration contract; alias kept for existing importers.
active_packages = active_package_slugs


# --------------------------------------------------------------------------- #
# bounded-read envelope (doc 03 §2.1/§2.2): route's host implementation.
# Host scripts implement the envelope themselves — the tests pin the shared
# contract per host; there is deliberately no cross-capability module.
# --------------------------------------------------------------------------- #

BRIEF_CAPABILITY = "brief"
DEFAULT_MAX_ITEMS = 100
HARD_MAX_ITEMS = 1000
TRUNCATION_NONE = "none"
TRUNCATION_ITEM_BUDGET = "item_budget"


class EnvelopeError(Exception):
    """Classified bounded-read failure; kind must stay inside the shared table
    (invalid_query / missing / unreadable / version_changed / unsupported)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _encode_page_token(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")


def _decode_page_token(token: str) -> dict:
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8"))
    except (binascii.Error, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise EnvelopeError("invalid_query", f"page token 不可解码：{exc}") from exc
    if not isinstance(payload, dict):
        raise EnvelopeError("invalid_query", "page token 必须编码 JSON 对象")
    return payload


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _brief_task_state(record, ready: set[str]) -> str:
    if record.checked:
        return "done"
    if record.blocked:
        return "blocked"
    if record.block_text.startswith("- [>"):
        return "manual-in-progress"
    if record.id in ready:
        return "ready"
    return "pending"


def _brief_task_projection(record, ready: set[str]) -> dict:
    return {
        "id": record.id,
        "title": record.title,
        "state": _brief_task_state(record, ready),
        "boundary": record.boundary,
        "verify": record.verify,
        "depends_on": list(task_dependencies(record)),
    }


def _handoff_view(package_dir: Path, root: Path, tasks_content: str, panel_line: str | None) -> dict:
    """Objectify the same handoff conclusion the markdown panel renders."""
    if panel_line is None:
        return {"line": None, "fresh": "absent"}
    if "不可读" in panel_line:
        return {"line": panel_line, "fresh": "unreadable"}
    if "结构非法" in panel_line or "无条目" in panel_line:
        return {"line": panel_line, "fresh": "unknown"}
    content = read_handoff(package_dir)
    doc = parse_handoff(content) if content is not None else None
    if doc is None or doc.latest is None:
        return {"line": panel_line, "fresh": "unknown"}
    label, detail = freshness(doc, root, tasks_content)
    view = {"line": panel_line, "fresh": label}
    if label != "fresh":
        view["stale_reason"] = detail
    return view


def _brief_envelope(
    *,
    slug: str | None,
    payload: dict,
    records: list,
    tasks_content: str,
    max_items: int,
    page_token: str | None,
) -> dict:
    """Item-budget paging over the task slice; version pinned to tasks.md."""
    version = _digest_text(tasks_content) if slug is not None else None
    item_offset = 0
    if page_token:
        token = _decode_page_token(page_token)
        if token.get("capability") != BRIEF_CAPABILITY:
            raise EnvelopeError("invalid_query", "page token 不属于 brief 能力")
        if slug is None:
            raise EnvelopeError("invalid_query", "无活动包分支没有可续页的任务切片")
        if token.get("version") != version:
            raise EnvelopeError(
                "version_changed",
                f"tasks.md 摘要已变化（token {str(token.get('version'))[:12]} ≠ 当前 {version[:12]}），请从头重取",
            )
        try:
            item_offset = int(token["item_offset"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EnvelopeError("invalid_query", f"page token 缺少合法 item_offset：{exc}") from exc
        if item_offset < 0 or item_offset > len(records):
            raise EnvelopeError("invalid_query", f"item_offset 越界：{item_offset}")
    tasks = payload.get("tasks", [])
    window = tasks[item_offset : item_offset + max_items]
    complete = item_offset + len(window) >= len(tasks)
    payload.update(
        {
            "capability": BRIEF_CAPABILITY,
            "query": {"slug": slug, "max_items": max_items, "item_offset": item_offset},
            "version": {"source_sha256": version, "records": len(records)},
            "counts": {"returned": len(window), "total_matching": len(tasks)},
            "complete": complete,
            "truncated_reason": TRUNCATION_NONE if complete else TRUNCATION_ITEM_BUDGET,
            "partial_line": False,
            "next": (
                _encode_page_token(
                    {"capability": BRIEF_CAPABILITY, "item_offset": item_offset + len(window), "version": version}
                )
                if not complete
                else None
            ),
        }
    )
    payload["tasks"] = window
    return payload


def checkpoint_alert(status: CheckpointStatus) -> str:
    """Prominent advisory line for a historical unresolved checkpoint.

    The checkpoint mechanism is retired: Git WIP history is the recovery
    point, so this is an advisory to resolve via Git and delete the file,
    not a hard block on routing.
    """
    if status.state == "active" and status.checkpoint is not None:
        return (
            f"存在未完成 update checkpoint（历史遗留，机制已退役）：更新「{status.checkpoint.intent}」"
            "未确认完成；按 Git 历史确认/恢复三件套后删除 update-checkpoint.json，再继续 check/done"
        )
    return (
        "存在未完成 update checkpoint（历史遗留，机制已退役）：记录已损坏；"
        "按 Git 历史恢复三件套后删除 update-checkpoint.json，再重跑 check"
    )


def _route_unreadable_checkpoint(slug: str, status: CheckpointStatus, output_format: str) -> str:
    """Render recovery without inventing progress from an unreadable triad."""
    alert = checkpoint_alert(status)
    next_step = (
        "处理未完成 update checkpoint（历史遗留）：按 Git 历史确认/恢复三件套后"
        "删除 update-checkpoint.json，再重跑 status"
        if status.state == "active"
        else "处理未完成 update checkpoint（历史遗留）：记录已损坏；"
        "按 Git 历史恢复三件套后删除 update-checkpoint.json，再重跑 status"
    )
    if output_format == "json":
        return json.dumps(
            {
                "slug": slug,
                "stage": "status",
                "health": HEALTH_BLOCKED,
                "completed": None,
                "total": None,
                "currentTask": None,
                "dependencyErrors": [],
                "updateCheckpoint": status.state,
                "alerts": [alert, "任务记录缺失或不可读，任务进度未知"],
                "nextStep": next_step,
            },
            ensure_ascii=False,
        )
    # Recovery has no trustworthy task strip or counters. Keep the normal
    # project/attention/next-step headings without rendering a fictitious 0/0.
    # No glyphs or metric grid are needed, so this also serves --ascii/--compact.
    return (
        f"# {slug}\n\n"
        f"状态：{HEALTH_BLOCKED}（status）\n\n"
        "已完成：未知（任务记录缺失或不可读）\n\n"
        f"## 需要关注\n\n- {alert}\n\n"
        f"## 接下来\n\n{next_step}\n"
    )


def route_for_package(
    root: Path,
    slug: str,
    specs_dir_name: str | None = None,
    *,
    ascii_mode: bool = False,
    compact: bool = False,
    output_format: str = "markdown",
    brief: bool = False,
    max_items: int = DEFAULT_MAX_ITEMS,
    page_token: str | None = None,
) -> str:
    if brief:
        output_format = "json"
    slug = validate_slug(slug)
    specs_root = resolve_specs_root(root, specs_dir_name)
    package_dir = resolve_specs_child(specs_root, "specs", slug)
    if package_dir.is_symlink():
        raise ValueError(f"task package directory must not be a symlink: {package_dir}")
    if not package_dir.is_dir():
        raise ValueError(f"task package not found: {package_dir}")

    # Detect before reading the possibly half-applied triad. Path validation
    # stays outside the recovery fallback: a checkpoint never permits escape
    # from the trusted specs root, even through an individual triad member.
    checkpoint_status = detect_update_checkpoint(package_dir)
    checkpoint_pending = checkpoint_status.interrupted
    triad_paths = [
        resolve_specs_child(specs_root, "specs", slug, name) for name in ("spec.md", "tasks.md", "checklist.md")
    ]
    try:
        spec_content, tasks_content = (read_regular_text(path) for path in triad_paths[:2])
        # checklist.md is optional since v2 packages (F11): absent file → the
        # success-criteria acceptance surface instead of the checklist gates.
        checklist_content: str | None = read_regular_text(triad_paths[2]) if triad_paths[2].exists() else None
    except (OSError, UnicodeError, ValueError):
        if checkpoint_pending:
            return _route_unreadable_checkpoint(slug, checkpoint_status, output_format)
        raise

    # Single computation: compute_gate_results replaces 8 separate function
    # calls that overall_check_passed would recompute internally.
    r = compute_gate_results(spec_content, tasks_content, checklist_content, slug=slug)
    _, tasks = parse_tasks(tasks_content)
    strip, strip_labels = task_strip_from_records(tasks)
    title = extract_title_from_content(spec_content, slug)

    blocked = r.blocked_tasks
    blocked_tasks = [task.text for task in tasks if task.is_blocked]
    all_done = r.all_tasks_done
    check_passed = r.overall_ok

    # An unresolved historical checkpoint is a prominent advisory, not a
    # hard block: Git WIP history is the recovery point, and the check/done
    # gates still refuse while the file exists. Route only detects it.

    alerts: list[str] = []
    if checkpoint_pending:
        alerts.append(checkpoint_alert(checkpoint_status))
    if blocked_tasks:
        alerts.extend(f"任务受阻：{task}" for task in blocked_tasks)
    elif blocked:
        alerts.append("项目中存在受阻任务，具体原因已记录在任务清单")
    alerts.extend(f"需要确认需求：{question}" for question in r.pending_questions)
    alerts.extend(f"项目定义不完整：{project_gap_message(gap)}" for gap in r.spec_clarification_gaps)
    # 编排策略结构错误在执行前拦截：非法 5.4 route 不得继续 stage=run。
    alerts.extend(f"编排策略尚未满足：{error}" for error in r.orchestration_errors)
    alerts.extend(f"验证范围尚未满足：{error}" for error in r.verification_scope_errors)

    orchestration_blocked = bool(r.orchestration_errors)
    if blocked or r.pending_questions or r.spec_clarification_gaps or orchestration_blocked:
        next_stage = "status"
        health = HEALTH_BLOCKED if blocked else HEALTH_RISK
        # Still scoping when clarification is missing; otherwise stalled mid-run.
        pipeline = default_pipeline(
            "new" if (r.pending_questions or r.spec_clarification_gaps or orchestration_blocked) else "run"
        )
    elif all_done and check_passed:
        next_stage = "done"
        health = HEALTH_OK
        pipeline = default_pipeline("done")
    elif all_done:
        next_stage = "check"
        health = HEALTH_RISK
        pipeline = default_pipeline("check")
        if r.missing_boundary or r.missing_verify:
            alerts.append(f"任务执行说明不完整：缺影响范围 {r.missing_boundary} 项，缺验证方式 {r.missing_verify} 项")
        if r.unchecked_items:
            alerts.append(f"项目尚有 {r.unchecked_items} 项验收未完成")
        if not r.checklist_present and not r.success_criteria_ok:
            alerts.append(f"项目尚有 {r.success_criteria_total - r.success_criteria_checked} 项成功标准未达成")
        if not check_passed and len(alerts) == 0:
            alerts.append("项目尚未满足最终验收要求")
    else:
        next_stage = "run"
        health = HEALTH_OK
        pipeline = default_pipeline("run")

    try:
        records = parse_task_records(tasks_content)
        ready = ready_task_ids(records)
        current_task = next((record.title for record in records if record.id in ready), None)
    except SpecControlError:
        current_task = None
    if checkpoint_pending:
        # Advisory-level attention: gates refuse until resolved, but routing
        # itself proceeds; the advisory line carries the recovery guidance.
        health = worst_health(health, HEALTH_RISK)
    if blocked:
        next_action = next((f"解除阻塞：{task.text}" for task in tasks if task.is_blocked), "处理任务阻塞")
    elif r.pending_questions:
        next_action = f"确认需求：{r.pending_questions[0]}"
    elif r.spec_clarification_gaps:
        next_action = f"补齐项目定义：{project_gap_message(r.spec_clarification_gaps[0])}"
    elif orchestration_blocked:
        next_action = f"修正编排策略：{r.orchestration_errors[0]}"
    elif all_done and check_passed:
        if checkpoint_pending:
            # Converged except for the historical checkpoint: resolving it is
            # the only step left before done (check/done refuse while it lives).
            next_action = (
                "处理历史遗留 update checkpoint：按 Git 历史确认/恢复三件套后删除 update-checkpoint.json，再继续归档"
            )
        else:
            next_action = "整理交付结果并完成归档"
    elif all_done:
        next_action = "修复验收未通过项"
    else:
        next_action = current_task or "继续实现项目任务"
    gate_gap_total = len(r.spec_clarification_gaps) + r.missing_boundary + r.missing_verify + r.unchecked_items
    handoff_line = None
    try:
        handoff_content = read_handoff(package_dir)
    except (OSError, UnicodeError, ValueError):
        handoff_content = None
        handoff_line = "handoff：不可读（超大/非 UTF-8/非常规文件），运行 spec_handoff.py validate 诊断"
    if handoff_content is not None and handoff_line is None:
        if validate_handoff(handoff_content):
            handoff_line = "handoff：结构非法，运行 spec_handoff.py validate 诊断"
        else:
            doc = parse_handoff(handoff_content)
            if doc.latest is not None:
                label, _detail = freshness(doc, root, tasks_content)
                handoff_line = f"handoff：最新 {doc.latest.timestamp}（{label}，{doc.latest.collab}）"
    dash = Dashboard(
        slug=slug,
        stage=next_stage,
        title=title,
        done=r.completed_tasks,
        total=r.total_tasks,
        health=health,
        pipeline=pipeline,
        strip=strip,
        strip_labels=strip_labels,
        metrics={
            "阻塞": "1" if blocked else "0",
            "待确认": str(len(r.pending_questions)),
            "门禁": "通过" if check_passed else f"缺口 {gate_gap_total}",
            "证据": str(r.evidence_filled_count),
            "范围": r.verification_scope or "",
        },
        alerts=alerts,
        next_step=next_action,
        detail=[handoff_line] if handoff_line else [],
        detail_title="交接" if handoff_line else "交付信息",
        ascii_mode=ascii_mode,
        compact=compact,
    )
    if output_format == "json":
        payload = {
            "slug": slug,
            "stage": next_stage,
            "health": health,
            "completed": r.completed_tasks,
            "total": r.total_tasks,
            "currentTask": current_task,
            "dependencyErrors": r.task_contract_errors,
            "orchestrationErrors": r.orchestration_errors,
            "verificationScope": r.verification_scope,
            "updateCheckpoint": checkpoint_status.state,
            **({"handoff": handoff_line} if handoff_line else {}),
        }
        if brief:
            records_for_slice: list = []
            ready: set[str] = set()
            try:
                records_for_slice = parse_task_records(tasks_content)
                ready = ready_task_ids(records_for_slice)
            except SpecControlError:
                # The gate's dependencyErrors already carries the parse reason;
                # an empty slice plus that error is the honest projection.
                records_for_slice = []
                ready = set()
            payload["title"] = title
            payload["branch_binding"] = validate_branch_bound_package(
                root, slug, spec_content, allow_legacy_convention=True
            )[1]
            payload["ready"] = sorted(ready)
            payload["tasks"] = [_brief_task_projection(record, ready) for record in records_for_slice]
            payload["alerts"] = alerts
            payload["handoff"] = _handoff_view(package_dir, root, tasks_content, handoff_line)
            payload["source"] = {
                "spec_sha256": _digest_text(spec_content),
                "tasks_sha256": _digest_text(tasks_content),
                "checklist_sha256": _digest_text(checklist_content) if checklist_content is not None else None,
                "generated_at": utc_now(),
            }
            payload = _brief_envelope(
                slug=slug,
                payload=payload,
                records=records_for_slice,
                tasks_content=tasks_content,
                max_items=max_items,
                page_token=page_token,
            )
        return json.dumps(payload, ensure_ascii=False)
    return dash.render()


def _validate_envelope_flags(args: argparse.Namespace) -> str | None:
    """Fail-closed flag validation before any output; None when acceptable."""
    if args.brief and args.format == "markdown":
        return "--brief 隐含 JSON 输出，不能与 --format markdown 同时使用"
    if not args.brief and (args.max_items != DEFAULT_MAX_ITEMS or args.page_token):
        return "--max-items/--page-token 仅在 --brief 下有效"
    if not 1 <= args.max_items <= HARD_MAX_ITEMS:
        return f"--max-items 必须在 1..{HARD_MAX_ITEMS} 内（硬上限，超限是错误不是截断）"
    return None


def _print_envelope_error(kind: str, message: str) -> int:
    print(f"error: {kind}: {message}", file=sys.stderr)
    return 1


def _decorate_branch_payload(payload: dict, args: argparse.Namespace) -> dict:
    """Attach the shared brief envelope to the slug-less main branches."""
    if not args.brief:
        return payload
    return _brief_envelope(
        slug=None,
        payload=payload,
        records=[],
        tasks_content="",
        max_items=args.max_items,
        page_token=args.page_token,
    )


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    invalid = _validate_envelope_flags(args)
    if invalid:
        return _print_envelope_error("invalid_query", invalid)
    try:
        specs_root = resolve_specs_root(root, args.specs_dir)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        specs_dir = resolve_specs_child(specs_root, "specs")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.slug:
        try:
            slug = validate_slug(args.slug.strip())
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        package_dir = specs_dir / slug
        if not package_dir.exists():
            message = f"task package not found: {package_dir}"
            if args.brief:
                return _print_envelope_error("missing", message)
            print(f"error: {message}", file=sys.stderr)
            return 1
        try:
            print(
                route_for_package(
                    root,
                    slug,
                    args.specs_dir,
                    ascii_mode=args.ascii,
                    compact=args.compact,
                    output_format=args.format,
                    brief=args.brief,
                    max_items=args.max_items,
                    page_token=args.page_token,
                )
            )
        except EnvelopeError as exc:
            return _print_envelope_error(exc.kind, exc.message)
        except (OSError, UnicodeError, ValueError) as exc:
            if args.brief:
                return _print_envelope_error("unreadable", str(exc))
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    packages = active_packages(specs_dir)
    if not packages:
        if args.brief or args.format == "json":
            payload = _decorate_branch_payload(
                {
                    "slug": None,
                    "stage": "new",
                    "health": HEALTH_OK,
                    "alerts": [],
                    "nextStep": "描述要完成的目标，skill 会直接建立任务并开始推进",
                },
                args,
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        dash = Dashboard(
            slug="spec",
            stage="new",
            title="当前没有进行中的项目",
            health=HEALTH_OK,
            next_step="描述要完成的目标，skill 会直接建立任务并开始推进",
            ascii_mode=args.ascii,
            compact=args.compact,
        )
        print(dash.render())
        return 0

    try:
        selection = select_active_package(root, specs_root, packages)
    except (OSError, UnicodeError, ValueError) as exc:
        message = f"cannot bind active task package: {exc}"
        if args.brief:
            return _print_envelope_error("unreadable", message)
        print(f"error: {message}", file=sys.stderr)
        return 1
    if selection.slug:
        try:
            print(
                route_for_package(
                    root,
                    selection.slug,
                    args.specs_dir,
                    ascii_mode=args.ascii,
                    compact=args.compact,
                    output_format=args.format,
                    brief=args.brief,
                    max_items=args.max_items,
                    page_token=args.page_token,
                )
            )
        except EnvelopeError as exc:
            return _print_envelope_error(exc.kind, exc.message)
        except (OSError, UnicodeError, ValueError) as exc:
            if args.brief:
                return _print_envelope_error("unreadable", str(exc))
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    warning = selection.warning or f"无法唯一选择活动任务包：{'、'.join(packages)}"
    if args.brief or args.format == "json":
        payload = _decorate_branch_payload(
            {
                "slug": None,
                "stage": "status",
                "health": HEALTH_RISK,
                "branch": selection.current_branch,
                "candidates": list(packages),
                "alerts": [warning],
                "nextStep": "切换到任务包记录的 integration branch，或显式指定 --slug 只读查看",
            },
            args,
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    dash = Dashboard(
        slug="spec",
        stage="status",
        title="任务包分支未绑定",
        health=HEALTH_RISK,
        alerts=[warning],
        metrics={"门禁": "分支绑定"},
        next_step="切换到任务包记录的 integration branch，或显式指定 --slug 只读查看",
        ascii_mode=args.ascii,
        compact=args.compact,
    )
    print(dash.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())

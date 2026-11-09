#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Generate a completion summary and optionally archive a checked package."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import gitx
from check_spec_package import compute_gate_results, gate_failure_details, overall_check_passed
from dashboard_support import HEALTH_OK, HEALTH_RISK, Dashboard, task_strip_from_records
from handoff_support import HandoffError, append_close_entry
from issue_closure_support import (
    IssueClosureError,
    parse_issue_disposition_arg,
    parse_task_records,
    render_issue_closure,
    validate_completion_summary,
    validate_issue_dispositions,
)
from report_spec_package import parse_tasks
from spec_package_support import (
    SECTION_BEHAVIOR_METRICS,
    SECTION_KEY_ASSUMPTIONS,
    SECTION_MINIMAL_PATH,
    SECTION_OUT_OF_SCOPE,
    SpecControlError,
    add_specs_dir_arg,
    extract_section_bullets,
    extract_title_from_content,
    is_development_record_slug,
    normalize_items,
    read_regular_text,
    resolve_specs_child,
    resolve_specs_root,
    verification_scope,
    write_text,
)
from spec_verify import fresh_machine_evidence, machine_verification_summary, runs_protocol_enabled


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True, help="Development Record slug")
    parser.add_argument("--root", default=".", help="Project root (default: current directory)")
    add_specs_dir_arg(parser)
    parser.add_argument("--archive", action="store_true", help="Move the checked package into archive")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing completion summary, not an archive")
    parser.add_argument("--main-branch", default="main", help="Main branch for the recorded Git commit interval")
    parser.add_argument("--agents-proposal", action="append", default=[], help="Incremental project-fact diff line")
    parser.add_argument(
        "--issue-disposition", action="append", default=[], help="Issue disposition JSON object or @path"
    )
    return parser.parse_args()


def bullet_lines(items: list[str], fallback: str) -> str:
    values = [item.strip() for item in items if item.strip()]
    return "\n".join(f"- {item}" for item in (values or [fallback]))


def section_values(content: str, heading, fallback: str) -> list[str]:
    return normalize_items(extract_section_bullets(content, heading)) or [fallback]


def archive_commit_range(root: Path, main_branch: str) -> str:
    head = gitx.head(root)
    if not head:
        return "适用外（非 Git 工作区或无提交）"
    result = gitx.git(root, "merge-base", f"refs/heads/{main_branch}", "HEAD", check=False)
    if result.returncode != 0:
        return f"适用外（无法解析 refs/heads/{main_branch} 的 merge-base）"
    return f"{result.stdout.strip()}..{head}"


def build_summary(
    title: str,
    spec: str,
    tasks: str,
    package: Path,
    root: Path,
    issues: list[dict],
    proposals: list[str],
    *,
    main_branch: str,
) -> str:
    assumptions = section_values(spec, SECTION_KEY_ASSUMPTIONS, "已按任务包验收")
    choices = section_values(spec, SECTION_MINIMAL_PATH, "按任务包最小路径交付")
    excluded = section_values(spec, SECTION_OUT_OF_SCOPE, "未扩张到任务边界外")
    metrics = section_values(spec, SECTION_BEHAVIOR_METRICS, "以任务包验收标准为准")
    delivered = [str(item["subject"]) for item in parse_task_records(tasks).values() if item["completed"]]
    proposal_lines = [line.strip() for line in proposals if line.strip()]
    proposal = "```diff\n" + "\n".join(proposal_lines) + "\n```" if proposal_lines else "无"
    machine_rate = machine_verification_summary(tasks, package) if runs_protocol_enabled(spec) else None
    machine = fresh_machine_evidence(spec, tasks, package, root) if machine_rate else []
    evidence = [f"脚本验证：{item}" for item in machine]
    if not evidence:
        evidence = ["manual：人工验证说明见 tasks.md" if runs_protocol_enabled(spec) else "验收证据见任务包"]
    rate = f"- 机器验证率：{machine_rate}\n" if machine_rate else ""
    scope = verification_scope(spec) or "历史包未声明"
    completed = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""# {title} - 完成总结

## 交付结论
- 结果：完成
- 完成时间：{completed}

## 假设回顾
{bullet_lines(assumptions, "已按任务包验收")}

## 交付范围
{bullet_lines(delivered, "尚无已完成任务")}

## 简化决策
{bullet_lines(choices, "按任务包最小路径交付")}

## 变更边界
- 具体边界见 tasks.md
{bullet_lines(excluded, "未扩张到任务边界外")}

## 验证证据
{bullet_lines(evidence, "验收证据见任务包")}

## 验证范围
- 级别：{scope}
{rate}
## 哲学生效证据
{bullet_lines(metrics, "以任务包验收标准为准")}

## Git 记录
- 日期时间：{completed}
- 范围：{title}
- 提交区间：{archive_commit_range(root, main_branch)}
- 提交：pending local commit
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
{proposal}

## 门禁证据
- check gates 已通过

{render_issue_closure(issues)}
"""


def move_package(root: Path, package: Path, destination: Path) -> None:
    """Use git mv for tracked packages; wholly untracked packages need a rename."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(destination):
        raise ValueError(f"archive destination already exists: {destination}")
    if gitx.toplevel(root) is not None:
        source = package.relative_to(root).as_posix()
        target = destination.relative_to(root).as_posix()
        tracked = gitx.git(root, "ls-files", "--", f":(literal){source}")
        if tracked.stdout:
            gitx.git(root, "mv", "--", source, target)
            return
    package.rename(destination)


def display_done(slug: str, title: str, tasks: str, destination: Path, *, archived: bool, close: int | None) -> None:
    _sections, records = parse_tasks(tasks)
    strip, labels = task_strip_from_records(records)
    detail = [f"交付记录：{destination}" if archived else f"交付总结草稿：{destination}"]
    if archived:
        detail.append(f"完成总结：{destination / 'completion-summary.md'}")
        if close is not None:
            detail.append(f"交接记录：{destination / 'handoff.md'}（entry {close} 关闭）")
    dashboard = Dashboard(
        slug=slug,
        stage="done",
        title=title,
        done=sum(task.is_completed for task in records),
        total=len(records),
        health=HEALTH_OK if archived else HEALTH_RISK,
        strip=strip,
        strip_labels=labels,
        metrics={"门禁": "通过" if archived else "草稿"},
        next_step="完成代码提交与发布收尾" if archived else "完成验收后归档交付记录",
        detail=detail,
    )
    print(dashboard.render())


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    slug = args.slug.strip()
    try:
        if not is_development_record_slug(slug):
            raise ValueError("task package slug must follow YYYY-MM-DD_slug Development Record format")
        specs_root = resolve_specs_root(root, args.specs_dir)
        package = resolve_specs_child(specs_root, "specs", slug)
        destination = resolve_specs_child(specs_root, "specs", "archive", slug)
        if package.resolve() != package:
            raise ValueError(f"task package path must not contain symlinks: {package}")
        if package.is_symlink() or not package.is_dir():
            raise ValueError(f"task package must be a non-symlink directory: {package}")
        spec = read_regular_text(resolve_specs_child(specs_root, "specs", slug, "spec.md"))
        tasks = read_regular_text(resolve_specs_child(specs_root, "specs", slug, "tasks.md"))
        checklist_path = resolve_specs_child(specs_root, "specs", slug, "checklist.md")
        checklist = read_regular_text(checklist_path) if checklist_path.exists() else None
        results = compute_gate_results(spec, tasks, checklist, slug=slug, root=root, package_dir=package)
        if not results.overall_ok:
            details = "; ".join(gate_failure_details(results, slug).values())
            raise ValueError(f"spec package has not passed check gates: {details}")
        summary_path = resolve_specs_child(specs_root, "specs", slug, "completion-summary.md")
        if os.path.lexists(summary_path) and (
            not args.force or summary_path.is_symlink() or not summary_path.is_file()
        ):
            raise ValueError(f"completion summary already exists: {summary_path}; use --force for a regular file")
        if args.archive and os.path.lexists(destination):
            raise ValueError(f"archive destination already exists: {destination}")
        issues = [parse_issue_disposition_arg(value) for value in args.issue_disposition]
        title = extract_title_from_content(spec, slug)
        summary = build_summary(
            title, spec, tasks, package, root, issues, args.agents_proposal, main_branch=args.main_branch
        )
        if args.archive:
            failures = validate_completion_summary(
                summary,
                specs_root=specs_root,
                current_slug=slug,
                current_tasks_content=tasks,
                spec_gate=overall_check_passed,
            )
        else:
            failures = validate_issue_dispositions(
                issues,
                specs_root=specs_root,
                current_slug=slug,
                current_tasks_content=tasks,
                spec_gate=overall_check_passed,
            )
        if failures:
            raise ValueError("completion summary closure gate failed: " + "; ".join(failures))
        close = append_close_entry(package, root, slug, tasks, spec) if args.archive else None
        write_text(summary_path, summary)
        if args.archive:
            try:
                move_package(root, package, destination)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                detail = (
                    exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
                )
                raise ValueError(
                    f"archive failed: {detail}; retry with --force to regenerate the retained summary"
                ) from exc
        display_done(
            slug, title, tasks, destination if args.archive else summary_path, archived=args.archive, close=close
        )
        return 0
    except (
        OSError,
        UnicodeError,
        ValueError,
        SpecControlError,
        HandoffError,
        IssueClosureError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

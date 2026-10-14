#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""
Generate completion-summary.md for a Spec package and optionally archive it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from check_spec_package import overall_check_passed, package_physical_state_errors
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
from safe_open_support import SafeOpenError, open_regular_read
from spec_package_support import (
    MAX_SPEC_FILE_BYTES,
    SECTION_BEHAVIOR_METRICS,
    SECTION_KEY_ASSUMPTIONS,
    SECTION_MINIMAL_PATH,
    SECTION_OUT_OF_SCOPE,
    SPEC_TITLE_SUFFIXES,
    SpecControlError,
    add_specs_dir_arg,
    detect_language,
    extract_evidence_fields,
    extract_heading_title_from_content,
    extract_section_bullets,
    is_development_record_slug,
    is_placeholder_value,
    normalize_items,
    read_regular_text,
    resolve_specs_child,
    resolve_specs_root,
    validate_branch_bound_package,
    verification_scope,
)
from spec_verify import machine_verification_summary, runs_protocol_enabled


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate completion-summary.md for a Spec package and optionally archive it."
    )
    parser.add_argument("--slug", required=True, help="Task package slug, e.g. 2026-06-12_payment-recovery")
    parser.add_argument(
        "--root",
        default=".",
        help="Project root where the .spec directory exists (default: current directory)",
    )
    add_specs_dir_arg(parser)
    parser.add_argument("--result", default="完成", help="Delivery result label")
    parser.add_argument("--completed-at", help="Completion time override, e.g. 2026-04-22 21:15")
    parser.add_argument("--verified-assumption", action="append", default=[])
    parser.add_argument("--open-risk", action="append", default=[])
    parser.add_argument("--delivered", action="append", default=[])
    parser.add_argument("--not-delivered", action="append", default=[])
    parser.add_argument("--deviation", action="append", default=[])
    parser.add_argument("--simplification", action="append", default=[])
    parser.add_argument("--out-of-scope", action="append", default=[])
    parser.add_argument("--touched-area", action="append", default=[])
    parser.add_argument("--untouched-area", action="append", default=[])
    parser.add_argument("--build-evidence", action="append", default=[])
    parser.add_argument("--test-evidence", action="append", default=[])
    parser.add_argument("--manual-evidence", action="append", default=[])
    parser.add_argument("--effect-metric", action="append", default=[])
    parser.add_argument("--consistency-evidence", action="append", default=[])
    parser.add_argument("--commit", default="pending local commit")
    parser.add_argument("--push", default="not-run (push stage follows commit)")
    parser.add_argument(
        "--git-record-language",
        choices=("auto", "zh", "en"),
        default="auto",
        help="Language for Git record labels in completion-summary.md (auto detects from spec title)",
    )
    parser.add_argument(
        "--main-branch",
        default="main",
        help="Branch the integration branch merges into; used only for the recorded 提交区间 anchor",
    )
    parser.add_argument(
        "--agents-proposal",
        action="append",
        default=[],
        help=(
            "One line of the AGENTS.md incremental diff proposal for this round's "
            "distillation; repeat for multiple lines. Rendered as a single diff "
            "fence in the summary; omit to record 无 (no reusable fact this round). "
            "The script only carries session-authored proposal text and never "
            "writes into AGENTS.md or any host instruction file."
        ),
    )
    parser.add_argument(
        "--follow-up",
        action="append",
        default=[],
        help=(
            "Legacy draft-only note. Closed archives reject this free-text channel; "
            "use --issue-disposition with a typed JSON object instead."
        ),
    )
    parser.add_argument(
        "--issue-disposition",
        action="append",
        default=[],
        help=(
            "One v1 issue-disposition JSON object, inline or @path. Repeat for multiple findings. "
            "Executable follow-up work must target an already archived Development Record."
        ),
    )
    parser.add_argument("--rejected-extension", action="append", default=[])
    parser.add_argument("--gate-evidence", action="append", default=[])
    parser.add_argument(
        "--process-metric",
        action="append",
        default=[],
        help=(
            "Optional process metric of the check loop, e.g. check rounds, rework "
            "count, or a human-escalation mark. Repeat for multiple entries; "
            "omitting it keeps the summary without the 过程指标 section."
        ),
    )
    parser.add_argument("--archive", action="store_true", help="Move the package into .spec/specs/archive/")
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Bypass task/checklist completion gates",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing completion-summary.md or archive destination if needed",
    )
    return parser.parse_args()


def has_any_real_value(values: list[str]) -> bool:
    return any(value.strip() and not is_placeholder_value(value) for value in values)


def bullet_lines(items: list[str], default: str) -> str:
    values = [item.strip() for item in items if item and item.strip()]
    if not values:
        values = [default]
    return "\n".join(f"- {value}" for value in values)


def bullet_inline(items: list[str], default: str) -> str:
    values = [item.strip() for item in items if item and item.strip()]
    if not values:
        return default
    return "；".join(values)


def completed_task_subjects(tasks_content: str) -> list[str]:
    return [str(record["subject"]) for record in parse_task_records(tasks_content).values() if record["completed"]]


def archive_summary_values(
    spec_content: str,
    tasks_content: str,
    checklist_content: str | None,
    args: argparse.Namespace,
) -> dict[str, list[str]]:
    """Fill archive summary sections from the already-passed package.

    Evidence fields collapse to machine evidence (runs.jsonl for
    ``evidence: runs`` packages) plus free-text notes (F11): the legacy
    ``--test-evidence`` / ``--build-evidence`` / ``--manual-evidence``
    arguments aggregate into the notes list and never gate the archive.
    """
    evidence = extract_evidence_fields(checklist_content or "")
    assumptions = normalize_items(extract_section_bullets(spec_content, SECTION_KEY_ASSUMPTIONS))
    minimal_path = normalize_items(extract_section_bullets(spec_content, SECTION_MINIMAL_PATH))
    out_of_scope = normalize_items(extract_section_bullets(spec_content, SECTION_OUT_OF_SCOPE))
    behavior = normalize_items(extract_section_bullets(spec_content, SECTION_BEHAVIOR_METRICS))
    command_evidence = [
        f"{key}：{value}"
        for key in ("脚本验证", "测试", "构建")
        if (value := evidence.get(key, "")) and not is_placeholder_value(value)
    ]
    delivered = completed_task_subjects(tasks_content)
    evidence_notes = (
        [
            *(f"测试：{value}" for value in args.test_evidence),
            *(f"构建：{value}" for value in args.build_evidence),
            *(f"手工：{value}" for value in args.manual_evidence),
        ]
        or command_evidence
        or [
            (
                "机器证据：runs.jsonl（见验证范围的机器验证率）"
                if runs_protocol_enabled(spec_content)
                else "命令证据：spec.md §6 成功标准 verify span"
            )
        ]
    )
    return {
        "verified_assumptions": args.verified_assumption or assumptions or ["Development Record 假设与范围门禁通过"],
        "delivered": args.delivered or delivered or ["Development Record 内全部任务已完成"],
        "simplifications": args.simplification or minimal_path or ["按 Development Record 最小实现路径交付"],
        "out_of_scope": args.out_of_scope or out_of_scope or ["未扩张到任务边界外"],
        "touched_areas": args.touched_area or ["见 tasks.md 的 boundary 记录"],
        "untouched_areas": args.untouched_area or out_of_scope or ["任务边界外模块未触碰"],
        "evidence_notes": evidence_notes,
        "effect_metrics": args.effect_metric or behavior or ["Development Record 行为门禁通过"],
        "consistency_evidence": args.consistency_evidence or ["验收面（checklist 或成功标准）已通过"],
        "gate_evidences": args.gate_evidence or ["check_spec_package.py exit 0"],
    }


MAX_ARCHIVE_FILES = 512
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def extract_title_from_content(spec_content: str, slug: str) -> str:
    # Shared bilingual suffix pair (项目范围 / Project Scope): the H1 is
    # display text and never gates, so both localizations strip here.
    return extract_heading_title_from_content(spec_content, slug, SPEC_TITLE_SUFFIXES)


def write_regular_text(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(content)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def validate_package_tree(package_dir: Path) -> None:
    for path in package_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"package contains symlink: {path}")
        if not path.is_dir() and not path.is_file():
            raise ValueError(f"package contains non-regular entry: {path}")


def copy_validated_tree(
    package_dir: Path,
    staging: Path,
    required_snapshots: dict[str, str],
) -> None:
    file_count = 0
    total_bytes = 0
    for root, directories, files in os.walk(package_dir, topdown=True, followlinks=False):
        root_path = Path(root)
        relative_root = root_path.relative_to(package_dir)
        for name in directories:
            source = root_path / name
            if source.is_symlink():
                raise ValueError(f"package contains symlink: {source}")
            (staging / relative_root / name).mkdir(parents=True, exist_ok=True)
        for name in files:
            source = root_path / name
            relative = relative_root / name
            target = staging / relative
            if relative_root == Path(".") and name in required_snapshots:
                write_regular_text(target, required_snapshots[name])
                continue
            if relative_root == Path(".") and name == "completion-summary.md":
                continue
            try:
                with open_regular_read(source, max_bytes=MAX_SPEC_FILE_BYTES) as descriptor:
                    metadata = os.fstat(descriptor)
                    file_count += 1
                    total_bytes += metadata.st_size
                    if file_count > MAX_ARCHIVE_FILES or total_bytes > MAX_ARCHIVE_BYTES:
                        raise ValueError("package exceeds archive file or byte limit")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    try:
                        while chunk := os.read(descriptor, 1024 * 1024):
                            view = memoryview(chunk)
                            while view:
                                written = os.write(output, view)
                                view = view[written:]
                    finally:
                        os.close(output)
            except SafeOpenError as exc:
                raise ValueError(f"package contains unsafe entry: {source}: {exc}") from exc


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def archive_validated_package(
    package_dir: Path,
    archive_destination: Path,
    summary: str,
    required_snapshots: dict[str, str],
    *,
    force: bool,
) -> None:
    staging = archive_destination.with_name(f".{archive_destination.name}.staging-{os.getpid()}")
    backup = archive_destination.with_name(f".{archive_destination.name}.backup-{os.getpid()}")
    quarantine = package_dir.with_name(f".{package_dir.name}.archiving-{os.getpid()}")
    archived_tombstone = archive_destination.parent / quarantine.name

    # A prior invocation may have published the archive and then failed while
    # deleting its tombstone/backup.  Treat that state as recoverable: clean
    # the residue and return without rebuilding or resurrecting the active
    # package.  This keeps archive, quarantine and active paths consistent and
    # makes retrying `--archive` safe after an interrupted cleanup.
    if (
        not os.path.lexists(package_dir)
        and os.path.lexists(archive_destination)
        and (os.path.lexists(archived_tombstone) or os.path.lexists(backup))
    ):
        try:
            if os.path.lexists(archived_tombstone):
                remove_path(archived_tombstone)
            if os.path.lexists(backup):
                remove_path(backup)
        except OSError as exc:
            raise ValueError(f"archive cleanup pending; retry after fixing residue: {exc}") from exc
        return

    validate_package_tree(package_dir)
    if any(os.path.lexists(path) for path in (staging, backup, quarantine, archived_tombstone)):
        raise ValueError("archive staging, backup, or quarantine path already exists")
    source_dir = package_dir
    published = False
    try:
        os.replace(package_dir, quarantine)
        source_dir = quarantine
        current_snapshots = {filename: read_regular_text(source_dir / filename) for filename in required_snapshots}
        if current_snapshots != required_snapshots:
            raise ValueError("required package files changed after validation")
        validate_package_tree(source_dir)
        staging.mkdir(parents=False)
        copy_validated_tree(source_dir, staging, required_snapshots)
        write_regular_text(staging / "completion-summary.md", summary)

        had_destination = os.path.lexists(archive_destination)
        if had_destination:
            if not force:
                raise ValueError(f"archive destination already exists: {archive_destination}")
            os.replace(archive_destination, backup)
        try:
            os.replace(staging, archive_destination)
            published = True
        except Exception:
            if had_destination and os.path.lexists(backup):
                os.replace(backup, archive_destination)
            raise
        if os.path.lexists(backup):
            try:
                remove_path(backup)
            except OSError as exc:
                print(
                    f"warning: archive published but backup cleanup failed; retained recoverable backup "
                    f"{backup}: {exc}",
                    file=sys.stderr,
                )
        os.replace(source_dir, archived_tombstone)
        try:
            remove_path(archived_tombstone)
        except OSError as exc:
            print(
                f"warning: archive published but tombstone cleanup failed: {archived_tombstone}: {exc}",
                file=sys.stderr,
            )
    except Exception:
        remove_path(staging)
        if os.path.lexists(backup) and not os.path.lexists(archive_destination):
            os.replace(backup, archive_destination)
        if not published and os.path.lexists(quarantine) and not os.path.lexists(package_dir):
            os.replace(quarantine, package_dir)
        raise


def archive_commit_range(root: Path, main_branch: str) -> str:
    """AUD-06: the delivery window this archive anchors to, frozen at archive time.

    Returns ``<merge-base(main, HEAD)>..<HEAD>`` — the interval covering every
    commit this package delivered on its integration branch, computed once
    here and never recomputed by gates (the archive commit that lands right
    after this summary is head's direct child and carries the same Spec
    footer, closing the window by construction). Non-Git roots, a missing
    main branch, or an unresolvable merge-base return an explicit 适用外 note
    so archival is never blocked by the interval itself.
    """

    def run_git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    head = run_git("rev-parse", "HEAD")
    if head is None:
        return "适用外（非 Git 工作区或无提交）"
    base = run_git("merge-base", f"refs/heads/{main_branch}", "HEAD")
    if base is None:
        return f"适用外（无法解析 refs/heads/{main_branch} 的 merge-base）"
    return f"{base}..{head}"


def git_record_labels(language: str) -> dict[str, str]:
    if language == "en":
        return {
            "section": "Git Records",
            "datetime": "Date Time",
            "scope": "Scope",
            "feature": "Feature",
            "action": "Action",
            "effect": "Effect",
            "commit": "Commit",
            "push": "Push",
        }
    return {
        "section": "Git 记录",
        "datetime": "日期时间",
        "scope": "范围",
        "feature": "功能",
        "action": "操作",
        "effect": "成效",
        "commit": "提交",
        "push": "推送",
    }


def build_summary(
    title: str,
    completed_at: str,
    result: str,
    verified_assumptions: list[str],
    open_risks: list[str],
    delivered: list[str],
    not_delivered: list[str],
    deviations: list[str],
    simplifications: list[str],
    out_of_scope: list[str],
    touched_areas: list[str],
    untouched_areas: list[str],
    evidence_notes: list[str],
    effect_metrics: list[str],
    consistency_evidence: list[str],
    commit_ref: str,
    push_ref: str,
    git_record_language: str,
    agents_proposals: list[str],
    follow_ups: list[str],
    rejected_extensions: list[str],
    gate_evidences: list[str],
    issue_dispositions: list[dict[str, object]] | None = None,
    process_metrics: list[str] | None = None,
    verification_scope_level: str | None = None,
    machine_verification_rate: str | None = None,
    commit_range: str | None = None,
) -> str:
    git_labels = git_record_labels(git_record_language)
    closure = render_issue_closure(issue_dispositions or [])
    # Optional machine-verification line: rendered only for `evidence: runs`
    # packages, so legacy summaries keep their exact field order and content.
    machine_rate_section = f"- 机器验证率：{machine_verification_rate}\n" if machine_verification_rate else ""
    # AUD-06: the commit-interval anchor. The label stays fixed Chinese (not
    # localized) because it is the machine-readable anchor the footer
    # interval judge parses; None keeps legacy callers' output unchanged.
    commit_range_section = f"- 提交区间：{commit_range}\n" if commit_range else ""
    # Optional process-metrics section: rendered only when real entries are
    # passed, so legacy summaries keep their exact field order.
    process_metrics_section = ""
    metric_entries = [value.strip() for value in (process_metrics or []) if value and value.strip()]
    if metric_entries:
        rendered_metrics = "\n".join(f"- {value}" for value in metric_entries)
        process_metrics_section = f"\n## 过程指标\n{rendered_metrics}\n"
    # AUD-13 (F15): distillation is an incremental AGENTS.md diff proposal the
    # done session authors; the script only renders it (diff fence, one line
    # per repeated flag value) and records 无 explicitly when there is nothing
    # reusable. It never writes into AGENTS.md or any host instruction file —
    # landing a proposal stays a human decision.
    proposal_lines = [line.strip() for line in agents_proposals if line and line.strip()]
    if proposal_lines:
        agents_proposal_section = "```diff\n" + "\n".join(proposal_lines) + "\n```"
    else:
        agents_proposal_section = "无"
    return f"""# {title} - 完成总结

## 交付结论
- 结果：{result}
- 完成时间：{completed_at}

## 假设回顾
### 已验证假设
{bullet_lines(verified_assumptions, "待补充")}

### 仍未完全验证的假设
{bullet_lines(open_risks, "无")}

## 交付范围
### 已交付
{bullet_lines(delivered, "待补充")}

### 未交付
{bullet_lines(not_delivered, "无")}

### 偏差说明
{bullet_lines(deviations, "无")}

## 简化决策
### 保持简单的关键选择
{bullet_lines(simplifications, "待补充")}

### 本轮明确不做的内容
{bullet_lines(out_of_scope, "待补充")}

## 变更边界
### 本轮主要改动模块
{bullet_lines(touched_areas, "待补充")}

### 明确未触碰的区域
{bullet_lines(untouched_areas, "待补充")}

## 验证证据
### 备注
{bullet_lines(evidence_notes, "待补充")}

## 验证范围
- 级别：{verification_scope_level or "历史包未声明"}
{machine_rate_section}- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
{bullet_lines(effect_metrics, "待补充")}

### 一致性门禁回顾
{bullet_lines(consistency_evidence, "待补充")}

## {git_labels["section"]}
- {git_labels["datetime"]}：{completed_at}
- {git_labels["scope"]}：{title}
- {git_labels["feature"]}：{bullet_inline(delivered, "待补充")}
- {git_labels["action"]}：{git_labels["commit"]} / {git_labels["push"]}
- {git_labels["effect"]}：{bullet_inline(effect_metrics, "待补充")}
- {git_labels["commit"]}：{commit_ref}
{commit_range_section}- {git_labels["push"]}：{push_ref}

## AGENTS.md 增量提议
{agents_proposal_section}

## 被拒绝的扩展提议
{bullet_lines(rejected_extensions, "无")}

## 门禁证据
{bullet_lines(gate_evidences, "无")}
{process_metrics_section}
## 遗留事项
{bullet_lines(follow_ups, "无；问题处置见下方结构化区块")}

{closure}
"""


def _archive_baseline_equivalence_for_done(root: Path, specs_root: Path) -> Callable[[str], bool] | None:
    """Follow-up Spec-gate exemption for done-time closure validation.

    Reuses check_all's single baseline-equivalence mechanism (same hash
    pipeline, same default baseline resolution) so a follow-up archive that is
    byte-identical to the authoritative baseline keeps its day-grade verdict
    instead of being re-graded by current gates. The baseline scan loads
    lazily on first queried slug: done runs whose closures visit no follow-up
    nodes never pay for it. ``None`` (specs root outside the project root)
    keeps the always-regrade default.
    """
    try:
        specs_identity = specs_root.relative_to(root).as_posix()
    except ValueError:
        return None
    builder: dict[str, Callable[[str], bool]] = {}

    def equivalent(slug: str) -> bool:
        if not builder:
            from check_all_spec_packages import archive_baseline_equivalence, legacy_archive_hashes

            builder["inner"] = archive_baseline_equivalence(specs_root, specs_identity, legacy_archive_hashes(root))
        return builder["inner"](slug)

    return equivalent


def main() -> int:
    args = parse_args()

    # Reject incompatible options before reading package state or Git metadata.
    if args.archive and args.allow_incomplete:
        print(
            "error: --allow-incomplete cannot be combined with --archive; incomplete packages must remain active",
            file=sys.stderr,
        )
        return 1

    slug = args.slug.strip()

    root = Path(args.root).resolve()
    try:
        specs_root = resolve_specs_root(root, args.specs_dir)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        package_dir = resolve_specs_child(specs_root, "specs", slug)
        archive_dir = resolve_specs_child(specs_root, "specs", "archive")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not package_dir.exists() or package_dir.is_symlink() or not package_dir.is_dir():
        print(f"error: task package must be a non-symlink directory: {package_dir}", file=sys.stderr)
        return 1

    try:
        spec_path = resolve_specs_child(specs_root, "specs", slug, "spec.md")
        tasks_path = resolve_specs_child(specs_root, "specs", slug, "tasks.md")
        checklist_path = resolve_specs_child(specs_root, "specs", slug, "checklist.md")
        summary_path = resolve_specs_child(specs_root, "specs", slug, "completion-summary.md")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for path in (spec_path, tasks_path):
        if not path.exists():
            print(f"error: required file missing: {path}", file=sys.stderr)
            return 1
    # checklist.md is optional since v2 packages (F11).
    checklist_content: str | None = read_regular_text(checklist_path) if checklist_path.exists() else None

    try:
        spec_content = read_regular_text(spec_path)
        tasks_content = read_regular_text(tasks_path)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: invalid task package file: {exc}", file=sys.stderr)
        return 1
    required_snapshots = {
        "spec.md": spec_content,
        "tasks.md": tasks_content,
        **({"checklist.md": checklist_content} if checklist_content is not None else {}),
    }
    branch_ok, branch_detail = validate_branch_bound_package(root, slug, spec_content)
    if not branch_ok:
        print(f"error: {branch_detail}", file=sys.stderr)
        return 1

    if not is_development_record_slug(slug):
        print(
            "error: task package slug must follow YYYY-MM-DD_slug Development Record format",
            file=sys.stderr,
        )
        return 1

    if not args.allow_incomplete:
        physical_errors = package_physical_state_errors(package_dir)
        if physical_errors:
            print(f"error: {physical_errors[0]}", file=sys.stderr)
            return 1
        # root enables the evidence freshness anchor: a stale 证据锚点 at done
        # time (HEAD moved after evidence capture) blocks the archive, exactly
        # like the check stage it reuses. package_dir additionally enables the
        # executable-verify gate: an `evidence: runs` package archives only
        # when every machine task's latest runs.jsonl record is exit==0 and
        # fingerprint-fresh against the current worktree.
        if not overall_check_passed(
            spec_content, tasks_content, checklist_content, slug=slug, root=root, package_dir=package_dir
        ):
            print(
                "error: spec package has not passed check gates; "
                "run check_spec_package.py or use --allow-incomplete to bypass",
                file=sys.stderr,
            )
            return 1

    if os.path.lexists(summary_path) and not args.force:
        print(
            f"error: completion summary already exists: {summary_path}; use --force to overwrite",
            file=sys.stderr,
        )
        return 1

    try:
        archive_destination = resolve_specs_child(specs_root, "specs", "archive", slug)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.archive and os.path.lexists(archive_destination) and not args.force:
        print(
            f"error: archive destination already exists: {archive_destination}; use --force to overwrite",
            file=sys.stderr,
        )
        return 1

    title = extract_title_from_content(spec_content, slug)
    completed_at = args.completed_at or datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        issue_dispositions = [parse_issue_disposition_arg(value) for value in args.issue_disposition]
    except IssueClosureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    disposition_failures = validate_issue_dispositions(
        issue_dispositions,
        specs_root=specs_root,
        current_slug=slug,
        current_tasks_content=tasks_content,
    )
    if disposition_failures:
        print("error: issue closure gate failed:", file=sys.stderr)
        for failure in disposition_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    if args.archive:
        legacy_open_items = {
            "--open-risk": args.open_risk,
            "--not-delivered": args.not_delivered,
            "--deviation": args.deviation,
            "--follow-up": args.follow_up,
        }
        used_legacy = [name for name, values in legacy_open_items.items() if has_any_real_value(values)]
        if used_legacy:
            print(
                "error: closed archives reject free-text unresolved fields: "
                + ", ".join(used_legacy)
                + "; resolve current work or use --issue-disposition",
                file=sys.stderr,
            )
            return 1
        summary_values = archive_summary_values(spec_content, tasks_content, checklist_content, args)
    else:
        summary_values = {
            "verified_assumptions": args.verified_assumption,
            "delivered": args.delivered,
            "simplifications": args.simplification,
            "out_of_scope": args.out_of_scope,
            "touched_areas": args.touched_area,
            "untouched_areas": args.untouched_area,
            "evidence_notes": [
                *(f"测试：{value}" for value in args.test_evidence),
                *(f"构建：{value}" for value in args.build_evidence),
                *(f"手工：{value}" for value in args.manual_evidence),
            ],
            "effect_metrics": args.effect_metric,
            "consistency_evidence": args.consistency_evidence,
            "gate_evidences": args.gate_evidence,
        }

    git_record_language = args.git_record_language
    if git_record_language == "auto":
        git_record_language = detect_language(title)

    # Executable-verify packages carry their machine-verification rate into
    # the archived summary (manual tasks are honest human work, excluded
    # from the denominator). Computed from the same runs.jsonl the gate
    # above already accepted, so the rate never disagrees with the gate.
    machine_rate = None
    if runs_protocol_enabled(spec_content):
        try:
            machine_rate = machine_verification_summary(tasks_content, package_dir)
        except (OSError, UnicodeError, ValueError, SpecControlError) as exc:
            print(f"error: runs 证据不可读：{exc}", file=sys.stderr)
            return 1

    summary = build_summary(
        title=title,
        completed_at=completed_at,
        result=args.result.strip() or "完成",
        verified_assumptions=summary_values["verified_assumptions"],
        open_risks=args.open_risk,
        delivered=summary_values["delivered"],
        not_delivered=args.not_delivered,
        deviations=args.deviation,
        simplifications=summary_values["simplifications"],
        out_of_scope=summary_values["out_of_scope"],
        touched_areas=summary_values["touched_areas"],
        untouched_areas=summary_values["untouched_areas"],
        evidence_notes=summary_values["evidence_notes"],
        effect_metrics=summary_values["effect_metrics"],
        consistency_evidence=summary_values["consistency_evidence"],
        commit_ref=args.commit.strip() or "pending local commit",
        push_ref=args.push.strip() or "not-run (push stage follows commit)",
        git_record_language=git_record_language,
        agents_proposals=args.agents_proposal,
        follow_ups=args.follow_up,
        rejected_extensions=args.rejected_extension,
        gate_evidences=summary_values["gate_evidences"],
        issue_dispositions=issue_dispositions,
        # Process metrics stay opt-in in both draft and archive modes: no
        # package-derived default, an unpassed flag renders no section.
        process_metrics=args.process_metric,
        verification_scope_level=verification_scope(spec_content),
        machine_verification_rate=machine_rate,
        commit_range=archive_commit_range(root, args.main_branch),
    )

    if args.archive:
        summary_failures = validate_completion_summary(
            summary,
            specs_root=specs_root,
            current_slug=slug,
            current_tasks_content=tasks_content,
            require_v1=True,
            baseline_equivalent=_archive_baseline_equivalence_for_done(root, specs_root),
        )
        if summary_failures:
            print("error: completion summary closure gate failed:", file=sys.stderr)
            for failure in summary_failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1

    archive_dir.mkdir(parents=True, exist_ok=True)
    # Dashboard 证据 metric: legacy packages count filled checklist evidence
    # fields; v2 packages count checked §6 success criteria.
    if checklist_content is not None:
        evidence_values = extract_evidence_fields(checklist_content)
        evidence_filled = sum(1 for value in evidence_values.values() if value and not is_placeholder_value(value))
    else:
        from spec_package_support import section_checkboxes_by_prefix

        _total, evidence_filled = section_checkboxes_by_prefix(spec_content, "## 6.")
    from report_spec_package import parse_tasks as parse_task_records_for_strip

    _sections, package_tasks = parse_task_records_for_strip(tasks_content)
    strip, strip_labels = task_strip_from_records(package_tasks)
    if args.archive:
        # Terminal handoff entry: the archive freezes the entry log with a
        # close record; a package without a handoff document is unaffected.
        try:
            close_number = append_close_entry(package_dir, root, slug, tasks_content, spec_content)
        except (OSError, UnicodeError, ValueError, HandoffError) as exc:
            print(f"error: handoff close entry failed: {exc}", file=sys.stderr)
            return 1
        try:
            archive_validated_package(
                package_dir,
                archive_destination,
                summary,
                required_snapshots,
                force=args.force,
            )
        except (OSError, ValueError, SpecControlError) as exc:
            print(f"error: archive failed: {exc}", file=sys.stderr)
            return 1
        dash = Dashboard(
            slug=slug,
            stage="done",
            title=title,
            done=len(package_tasks),
            total=len(package_tasks),
            health=HEALTH_OK,
            strip=strip,
            strip_labels=strip_labels,
            metrics={"阻塞": "0", "待确认": "0", "门禁": "通过", "证据": str(evidence_filled)},
            delta=["交付内容已通过验收", "交付记录和完成总结已归档"],
            next_step="完成代码提交与发布收尾",
            detail=[
                f"交付记录：{archive_destination}",
                f"完成总结：{archive_destination / 'completion-summary.md'}",
                *(
                    [f"交接记录：{archive_destination / 'handoff.md'}（entry {close_number} 关闭）"]
                    if close_number
                    else []
                ),
            ],
        )
        print(dash.render())
    else:
        try:
            if args.force and os.path.lexists(summary_path):
                summary_path.unlink()
            write_regular_text(summary_path, summary)
        except (OSError, ValueError) as exc:
            print(f"error: completion summary write failed: {exc}", file=sys.stderr)
            return 1
        dash = Dashboard(
            slug=slug,
            stage="done",
            title=title,
            done=len([task for task in package_tasks if task.is_completed]),
            total=len(package_tasks),
            health=HEALTH_RISK,
            strip=strip,
            strip_labels=strip_labels,
            metrics={"阻塞": "0", "待确认": "0", "门禁": "草稿", "证据": str(evidence_filled)},
            alerts=["交付总结已生成，但项目尚未通过最终验收并归档"],
            next_step="完成剩余验收项后归档交付记录",
            detail=[f"交付总结草稿：{summary_path}"],
        )
        print(dash.render())

    return 0


if __name__ == "__main__":
    sys.exit(main())

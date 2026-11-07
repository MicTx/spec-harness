#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Machine writeback hook for /spec:check findings.

plan: read a check JSON report and emit the proposed package update (no writes).
apply: atomically append checkfix tasks to tasks.md and one correction-log row
to spec.md §5.5, then print the handoff for /spec:run.

The check gates stay read-only; this script is the only writer in the chain.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from issue_closure_support import DISPOSITION_TYPES
from spec_package_support import (
    SECTION_CORRECTION_LOG,
    STABLE_TASK_ID_PATTERN,
    add_specs_dir_arg,
    correction_log_errors,
    parse_task_records,
    read_regular_text,
    resolve_specs_child,
    resolve_specs_root,
)

PLAN_VERSION = 1
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_WRITEBACK_ROUNDS = 3
WEAK_ID_PREFIX = "task-checkfix-t-"
ROW_MARKER = "check 回写钩子"
SECTION_HEADING_PREFIX = "## check 回写修正"
MANUAL_VERIFY_PREFIX = "manual:"
ROUND_SUFFIX_PATTERN = re.compile(r"^(?P<base>" + re.escape(WEAK_ID_PREFIX) + r"[0-9a-f]{8})(?:-r(?P<round>\d+))?$")
NONE_DECLARATION = re.compile(r"^(?:-\s*)?(?:none|无)\s*[。.]?\s*$", re.IGNORECASE)
VOLATILE_NUMBER = re.compile(r"(?<![A-Za-z0-9_-])\d+(?![A-Za-z0-9_-])")
EXIT_ESCALATE = 3
EXIT_ERROR = 2

PLACEHOLDER_VERIFY = f"{MANUAL_VERIFY_PREFIX} 由主会话按 gap 生成真实修复与裁决命令；收敛由下一轮 check 复跑证明"


class WritebackError(ValueError):
    """Plan or package state cannot be turned into a safe writeback."""


def _iso_date() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def _read_json_file(path: Path) -> object:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise WritebackError(f"input must be a regular file: {path}")
    if metadata.st_size > MAX_FILE_BYTES:
        raise WritebackError(f"input exceeds {MAX_FILE_BYTES} bytes: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WritebackError(f"input JSON is unreadable or invalid: {path}: {exc}") from exc


def normalize_gap(gap: str) -> str:
    """Collapse volatile counts while keeping identity tokens (task-3, task_0001) intact."""
    return re.sub(r"\s+", " ", VOLATILE_NUMBER.sub("", gap)).strip()


def weak_task_id(gap: str) -> str:
    digest = hashlib.sha256(normalize_gap(gap).encode("utf-8")).hexdigest()[:8]
    return f"{WEAK_ID_PREFIX}{digest}"


def _round_suffix(task_id: str) -> tuple[str, int]:
    match = ROUND_SUFFIX_PATTERN.fullmatch(task_id)
    if not match:
        raise WritebackError(f"unexpected checkfix id shape: {task_id}")
    return match.group("base"), int(match.group("round") or 1)


def _load_gaps(payload: object) -> list[str]:
    if isinstance(payload, dict):
        payload = payload.get("gaps")
    if not isinstance(payload, list) or not all(isinstance(gap, str) for gap in payload):
        raise WritebackError("gaps input must be a JSON array of strings or {\"gaps\": [...]}")
    gaps: list[str] = []
    for gap in payload:
        text = gap.strip()
        if not text:
            raise WritebackError("gaps input contains an empty gap string")
        if text not in gaps:
            gaps.append(text)
    return gaps


def _correction_section_span(spec_lines: list[str]) -> tuple[int, int] | None:
    start = next((index for index, line in enumerate(spec_lines) if SECTION_CORRECTION_LOG.matches(line)), None)
    if start is None:
        return None
    end = len(spec_lines)
    for index in range(start + 1, len(spec_lines)):
        if re.match(r"^#{1,3} ", spec_lines[index]):
            end = index
            break
    return start, end


def _correction_table_rows(spec_lines: list[str], span: tuple[int, int]) -> list[int]:
    start, end = span
    return [index for index in range(start, end) if spec_lines[index].startswith("|")]


def _count_writeback_rows(spec_lines: list[str]) -> tuple[int, int]:
    span = _correction_section_span(spec_lines)
    if span is None:
        raise WritebackError("spec.md is missing the correction log section (### 5.5 修正日志)")
    rows = _correction_table_rows(spec_lines, span)
    data_rows = [index for index in rows if not re.fullmatch(r"\|[-\s|]+\|", spec_lines[index])]
    data_rows = [index for index in data_rows if "round" not in spec_lines[index].lower()]
    writeback_rows = [index for index in data_rows if ROW_MARKER in spec_lines[index]]
    return len(data_rows), len(writeback_rows)


def _existing_checkfix_state(tasks_lines: list[str]) -> dict[str, dict[str, object]]:
    state: dict[str, dict[str, object]] = {}
    for record in parse_task_records("\n".join(tasks_lines) + "\n"):
        if record.id.startswith(WEAK_ID_PREFIX):
            base, round_number = _round_suffix(record.id)
            known = state.get(base)
            if known is None:
                state[base] = {"checked": record.checked, "round": round_number}
            else:
                known["round"] = max(int(known["round"]), round_number)
                if not record.checked:
                    known["checked"] = False
    return state


def _plan_tasks(gaps: list[str], tasks_lines: list[str]) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    state = _existing_checkfix_state(tasks_lines)
    planned: list[dict[str, str]] = []
    used_bases: set[str] = set()
    for gap in gaps:
        base = weak_task_id(gap)
        if base in used_bases:
            for task in planned:
                if weak_task_id(task["gap"]) == base:
                    task.setdefault("extra_gaps", []).append(gap)
                    break
            continue
        used_bases.add(base)
        known = state.get(base)
        if known is not None and not known["checked"]:
            continue
        round_number = 1 if known is None else int(known["round"]) + 1
        task_id = base if round_number == 1 else f"{base}-r{round_number}"
        planned.append(
            {
                "id": task_id,
                "gap": gap,
                "disposition": "resolved_current",
                "boundary": "只修复该门禁差距指向的包内状态或文档；不改与本 gap 无关的交付物",
                "verify": PLACEHOLDER_VERIFY,
            }
        )
    merged_gaps = [
        {"base": weak_task_id(task["gap"]), "gaps": list(task.get("extra_gaps", []))}
        for task in planned
        if task.get("extra_gaps")
    ]
    return planned, merged_gaps


def _task_block(task: dict[str, str]) -> str:
    title = re.sub(r"\s+", " ", task["gap"])[:80]
    block = (
        f"- [ ] check 回写修正：{title}\n"
        f"  - id: {task['id']}\n"
        f"  - boundary: {task['boundary']}\n"
        f"  - verify: {task['verify']}\n"
        f"  - 处置枚举: {task['disposition']}\n"
        f"  - 说明: 由 check 回写钩子生成；待 run 修复，未验证；收敛由下一轮 check 复跑证明\n"
    )
    for gap in [task["gap"], *task.get("extra_gaps", [])]:
        block += f"  - 未通过项: {gap}\n"
    return block


def _append_tasks_section(tasks_content: str, tasks: list[dict[str, str]]) -> str:
    if not tasks_content.endswith("\n"):
        tasks_content += "\n"
    section = f"\n{SECTION_HEADING_PREFIX}（{_iso_date()}）\n\n"
    section += "\n".join(_task_block(task) for task in tasks)
    return tasks_content + section


def _correction_row(round_number: int, task_count: int, gaps_source: str) -> str:
    return (
        f"| {round_number} | {ROW_MARKER} plan/apply | FAIL: {task_count} gaps 转为 checkfix 任务 "
        f"| new output: check_spec_package --format json gaps @ {gaps_source} ({_iso_date()}) |"
    )


def _upsert_correction_row(spec_content: str, row: str) -> str:
    lines = spec_content.splitlines()
    span = _correction_section_span(lines)
    if span is None:
        raise WritebackError("spec.md is missing the correction log section (### 5.5 修正日志)")
    start, end = span
    row_indexes = _correction_table_rows(lines, span)
    if row_indexes:
        insert_at = row_indexes[-1] + 1
        lines.insert(insert_at, row)
    else:
        none_index = next(
            (
                index
                for index in range(start, end)
                if NONE_DECLARATION.fullmatch(lines[index].strip().lstrip("-").strip())
            ),
            None,
        )
        if none_index is None:
            raise WritebackError("correction log has neither a table nor a none declaration")
        lines[none_index : none_index + 1] = [
            "| round | approach | result | new information |",
            "| --- | --- | --- | --- |",
            row,
        ]
    return "\n".join(lines) + "\n"


def _git_paths_dirty(root: Path, paths: list[Path]) -> list[str]:
    if not (root / ".git").exists():
        return []
    relative = [str(path.relative_to(root)) for path in paths]
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--", *relative],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WritebackError(f"git status failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _backup_and_write(root: Path, updates: dict[Path, str]) -> Path:
    backup_dir = Path(tempfile.mkdtemp(prefix="spec-writeback-"))
    originals: dict[Path, bytes] = {}
    try:
        for target, new_text in updates.items():
            original = target.read_bytes()
            originals[target] = original
            shutil.copy2(target, backup_dir / target.name)
        for target, new_text in updates.items():
            temp_path = target.with_name(target.name + ".writeback-tmp")
            temp_path.write_text(new_text, encoding="utf-8")
            os.replace(temp_path, target)
    except Exception:
        for target, payload in originals.items():
            target.write_bytes(payload)
        raise
    return backup_dir


def _validate_package_tasks(tasks_content: str, expected_ids: list[str]) -> None:
    records = {record.id: record for record in parse_task_records(tasks_content)}
    for task_id in expected_ids:
        if task_id not in records:
            raise WritebackError(f"appended task failed to parse: {task_id}")


def cmd_plan(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    specs_root = resolve_specs_root(root, args.specs_dir)
    package_dir = resolve_specs_child(specs_root, "specs", args.slug)
    tasks_lines = read_regular_text(resolve_specs_child(package_dir, "tasks.md")).splitlines()
    spec_lines = read_regular_text(resolve_specs_child(package_dir, "spec.md")).splitlines()
    gaps = _load_gaps(_read_json_file(Path(args.gaps)))
    total_rows, writeback_rows = _count_writeback_rows(spec_lines)
    if writeback_rows >= MAX_WRITEBACK_ROUNDS:
        print(
            json.dumps(
                {"version": PLAN_VERSION, "slug": args.slug, "next": "escalate", "writeback_rounds": writeback_rows},
                ensure_ascii=False,
            )
        )
        return EXIT_ESCALATE
    tasks, merged_gaps = _plan_tasks(gaps, tasks_lines)
    payload = {
        "version": PLAN_VERSION,
        "slug": args.slug,
        "gaps_source": Path(args.gaps).name,
        "next": "apply" if tasks else "check-green",
        "correction_log_round": total_rows + 1,
        "correction_log_row": _correction_row(total_rows + 1, len(tasks), Path(args.gaps).name) if tasks else None,
        "tasks": tasks,
        "merged_gaps": merged_gaps,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


def _validate_plan(payload: object, slug: str) -> tuple[list[dict[str, str]], str]:
    if not isinstance(payload, dict) or payload.get("version") != PLAN_VERSION:
        raise WritebackError("plan must be a version-1 JSON object")
    if payload.get("slug") != slug:
        raise WritebackError("plan slug does not match the target package")
    gaps_source = payload.get("gaps_source")
    if not isinstance(gaps_source, str) or not gaps_source.strip():
        raise WritebackError("plan is missing gaps_source provenance")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise WritebackError("plan has no tasks to apply (check-green plans are not appliable)")
    for task in tasks:
        if not isinstance(task, dict):
            raise WritebackError("plan tasks must be objects")
        task_id = task.get("id", "")
        if not isinstance(task_id, str) or not STABLE_TASK_ID_PATTERN.fullmatch(task_id):
            raise WritebackError(f"plan task id is not a stable task id: {task_id!r}")
        if not task_id.startswith(WEAK_ID_PREFIX):
            raise WritebackError(f"plan task id must use the checkfix weak identity: {task_id!r}")
        if task.get("disposition") not in DISPOSITION_TYPES:
            raise WritebackError(f"plan task {task_id} disposition must be one of {sorted(DISPOSITION_TYPES)}")
        for field in ("gap", "boundary", "verify"):
            if not isinstance(task.get(field), str) or not task[field].strip():
                raise WritebackError(f"plan task {task_id} is missing a real {field}")
        if task["verify"].strip() == PLACEHOLDER_VERIFY:
            raise WritebackError(
                f"plan task {task_id} still carries the untouched plan placeholder verify; "
                "review and replace it (real command or a justified manual reason) before apply"
            )
        _round_suffix(task_id)
    return tasks, gaps_source


def _gate_converged(root: Path, slug: str) -> bool:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().with_name("check_spec_package.py")),
            "--root",
            str(root),
            "--slug",
            slug,
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise WritebackError(
            f"gate output is not valid JSON (rc={result.returncode}); cannot verify the anti-fake-success invariant"
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("converged"), bool):
        raise WritebackError("gate output is malformed; cannot verify the anti-fake-success invariant")
    return payload["converged"] is True


def cmd_apply(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    specs_root = resolve_specs_root(root, args.specs_dir)
    package_dir = resolve_specs_child(specs_root, "specs", args.slug)
    tasks_path = resolve_specs_child(package_dir, "tasks.md")
    spec_path = resolve_specs_child(package_dir, "spec.md")
    tasks_content = read_regular_text(tasks_path)
    spec_content = read_regular_text(spec_path)
    tasks, gaps_source = _validate_plan(_read_json_file(Path(args.plan)), args.slug)
    dirty = _git_paths_dirty(root, [tasks_path, spec_path])
    if dirty:
        print("error: target paths have uncommitted changes; resolve exclusive access first:", file=sys.stderr)
        for line in dirty:
            print(f"  {line}", file=sys.stderr)
        return EXIT_ERROR
    spec_lines = spec_content.splitlines()
    total_rows, writeback_rows = _count_writeback_rows(spec_lines)
    if writeback_rows >= MAX_WRITEBACK_ROUNDS:
        print(
            json.dumps(
                {"version": PLAN_VERSION, "slug": args.slug, "next": "escalate", "writeback_rounds": writeback_rows},
                ensure_ascii=False,
            )
        )
        return EXIT_ESCALATE
    existing = parse_task_records(tasks_content)
    existing_ids = {record.id: record.checked for record in existing}
    applied: list[dict[str, str]] = []
    skipped: list[str] = []
    for task in tasks:
        checked = existing_ids.get(task["id"])
        if checked is None:
            applied.append(task)
        elif not checked:
            skipped.append({"id": task["id"], "gap": task["gap"]})
        else:
            raise WritebackError(f"plan task id already exists as a checked task: {task['id']}")
    if not applied:
        print(
            json.dumps(
                {"version": PLAN_VERSION, "slug": args.slug, "next": "run", "tasks": [], "skipped": skipped},
                ensure_ascii=False,
            )
        )
        return 0
    new_tasks_content = _append_tasks_section(tasks_content, applied)
    row = _correction_row(total_rows + 1, len(applied), gaps_source)
    new_spec_content = _upsert_correction_row(spec_content, row)
    expected_ids = [task["id"] for task in applied]
    _validate_package_tasks(new_tasks_content, expected_ids)
    spec_errors = correction_log_errors(new_spec_content)
    if spec_errors:
        raise WritebackError(f"generated correction row failed the log contract: {spec_errors}")
    if not new_tasks_content.startswith(tasks_content):
        raise WritebackError("append-only invariant violated: existing tasks.md bytes would change")
    backup_dir = _backup_and_write(root, {tasks_path: new_tasks_content, spec_path: new_spec_content})
    try:
        _validate_package_tasks(read_regular_text(tasks_path), expected_ids)
        rerun_errors = correction_log_errors(read_regular_text(spec_path))
        if rerun_errors:
            raise WritebackError(f"post-write correction log validation failed: {rerun_errors}")
        if _gate_converged(root, args.slug):
            raise WritebackError(
                "post-write gate unexpectedly converged with unchecked checkfix tasks; "
                "rolled back to prevent a fake-success writeback"
            )
    except Exception:
        tasks_path.write_bytes((backup_dir / tasks_path.name).read_bytes())
        spec_path.write_bytes((backup_dir / spec_path.name).read_bytes())
        raise
    print(
        json.dumps(
            {
                "version": PLAN_VERSION,
                "slug": args.slug,
                "next": "run",
                "tasks": expected_ids,
                "skipped": skipped,
                "correction_log_round": total_rows + 1,
                "backups": str(backup_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--slug", required=True, help="Task package slug, e.g. 2026-06-12_spec-self-hosting")
        sub.add_argument("--root", default=".", help="Project root where the .spec directory exists")
        add_specs_dir_arg(sub)
        if name == "plan":
            sub.add_argument("--gaps", required=True, help="Path to check_spec_package --format json output")
            sub.add_argument("--out", help="Write the plan JSON here instead of stdout")
        else:
            sub.add_argument("--plan", required=True, help="Path to the plan JSON produced by `plan`")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            return cmd_plan(args)
        return cmd_apply(args)
    except WritebackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())

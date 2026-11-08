#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Machine writeback hook for /spec:check findings.

plan: read a check JSON report and emit the proposed package update (no writes).
apply: snapshot-bound, locked update of tasks.md and one correction-log row
to spec.md §5.5, then print the handoff for /spec:run.

The check gates stay read-only; this script is the only writer in the chain.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

try:
    import fcntl
except ImportError:  # No cross-platform lock substitute with weaker semantics.
    fcntl = None

from issue_closure_support import DISPOSITION_TYPES
from safe_open_support import SafeOpenError, open_regular_read
from spec_package_support import (
    SECTION_CORRECTION_LOG,
    STABLE_TASK_ID_PATTERN,
    add_specs_dir_arg,
    correction_log_errors,
    parse_task_records,
    resolve_specs_child,
    resolve_specs_root,
)

PLAN_VERSION = 2
LOCK_NAME = ".writeback.lock"
TRANSACTION_NAME = ".writeback-transaction.json"
DOCUMENT_NAMES = ("tasks.md", "spec.md")
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
    try:
        with open_regular_read(path, max_bytes=MAX_FILE_BYTES) as descriptor:
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise WritebackError(f"input exceeds {MAX_FILE_BYTES} bytes: {path}")
        return json.loads(data.decode("utf-8"))
    except (OSError, SafeOpenError, UnicodeError, json.JSONDecodeError) as exc:
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
        raise WritebackError('gaps input must be a JSON array of strings or {"gaps": [...]}')
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


def _file_state(info: os.stat_result, data: bytes) -> dict:
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


@contextmanager
def _package_handle(root: Path, package_dir: Path):
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise WritebackError("platform lacks no-follow directory access; writeback unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        # Open every child against an already-open directory, not a path that
        # may redirect through a symlink between validation and use.
        for part in package_dir.relative_to(root).parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        _assert_package_binding(root, package_dir, descriptor)
        yield descriptor
    finally:
        os.close(descriptor)


def _assert_package_binding(root: Path, package_dir: Path, descriptor: int) -> None:
    if package_dir.resolve(strict=True) != package_dir.absolute():
        raise WritebackError("package path contains a symlink")
    current, opened = package_dir.stat(), os.fstat(descriptor)
    if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
        raise WritebackError("package directory changed during writeback")


def _read_document(descriptor: int, name: str) -> tuple[bytes, dict]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    handle = os.open(name, flags, dir_fd=descriptor)
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
            raise WritebackError(f"{name} must be a bounded, single-link regular file")
        with os.fdopen(handle, "rb", closefd=False) as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise WritebackError(f"{name} exceeds {MAX_FILE_BYTES} bytes")
        state = _file_state(info, data)
        if state != _file_state(os.fstat(handle), data) or state != _file_state(
            os.stat(name, dir_fd=descriptor, follow_symlinks=False), data
        ):
            raise WritebackError(f"{name} changed while reading")
        return data, state
    finally:
        os.close(handle)


def _snapshot(root: Path, package_dir: Path, descriptor: int) -> tuple[dict, dict[str, bytes]]:
    _assert_package_binding(root, package_dir, descriptor)
    root_info, package_info = root.stat(), os.fstat(descriptor)
    documents, states = {}, {}
    for name in DOCUMENT_NAMES:
        documents[name], states[name] = _read_document(descriptor, name)
    snapshot = {
        "root": str(root),
        "root_id": [root_info.st_dev, root_info.st_ino],
        "package": package_dir.relative_to(root).as_posix(),
        "package_id": [package_info.st_dev, package_info.st_ino],
        "files": states,
    }
    return snapshot, documents


def _refuse_transaction(descriptor: int) -> None:
    try:
        os.stat(TRANSACTION_NAME, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise WritebackError(
        f"unfinished writeback transaction: inspect {TRANSACTION_NAME} and its private backups; "
        "verify/restore BOTH documents before manually removing the marker; no automatic recovery"
    )


@contextmanager
def _exclusive_lock(descriptor: int):
    if fcntl is None:
        raise WritebackError("platform lacks flock; writeback unavailable")
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    lock = os.open(LOCK_NAME, flags, 0o600, dir_fd=descriptor)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise WritebackError("writeback lock must be a single-link regular file")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WritebackError("writeback is locked by another writer; do not remove a live lock") from exc
        current = os.stat(LOCK_NAME, dir_fd=descriptor, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise WritebackError("writeback lock changed during acquisition")
        yield
    finally:
        # Keep the inode: unlinking a flock file permits two independent locks.
        os.close(lock)


def _write_private(descriptor: int, name: str, data: bytes, mode: int = 0o600) -> None:
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
    with os.fdopen(handle, "wb") as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _replace_document(descriptor: int, name: str, data: bytes, expected: dict) -> dict:
    if len(data) > MAX_FILE_BYTES:
        raise WritebackError(f"{name} update exceeds {MAX_FILE_BYTES} bytes")
    temporary = f".writeback-{uuid4().hex}.tmp"
    try:
        _write_private(descriptor, temporary, data, expected["mode"])
        prepared = _read_document(descriptor, temporary)[1]
        if _read_document(descriptor, name)[1] != expected:
            raise WritebackError(f"{name} changed before replacement; refusing to overwrite conflict")
        os.replace(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
        observed, state = _read_document(descriptor, name)
        # A post-rename read must not claim another writer's bytes/inode as
        # ours: rollback would otherwise overwrite that conflict later.
        identity = ("device", "inode", "mode", "mtime_ns", "sha256")
        if observed != data or any(state[key] != prepared[key] for key in identity):
            raise WritebackError(f"{name} changed after replacement; refusing ownership of conflict")
        return state
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass


def _transactional_update(root, package_dir, descriptor, snapshot, originals, updates, validate) -> Path:
    """Two renames are not atomic: a durable marker fences interrupted pairs.

    Backups survive both success and failure, outside the worktree. Only files
    still matching our own writes may be rolled back; conflicts retain the
    marker for human recovery. BaseException intentionally simulates a crash.
    """
    backup_dir = Path(tempfile.mkdtemp(prefix="spec-writeback-"))
    backup_fd = os.open(backup_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name, data in originals.items():
            _write_private(backup_fd, name, data)
        os.fsync(backup_fd)
    finally:
        os.close(backup_fd)
    marker = {
        "version": 1,
        "transaction_id": uuid4().hex,
        "backups": str(backup_dir),
        "snapshot": snapshot,
        "after": {name: hashlib.sha256(data).hexdigest() for name, data in updates.items()},
    }
    _write_private(descriptor, TRANSACTION_NAME, (json.dumps(marker, ensure_ascii=False) + "\n").encode("utf-8"))
    os.fsync(descriptor)
    written = {}
    try:
        if _snapshot(root, package_dir, descriptor)[0] != snapshot:
            raise WritebackError("package snapshot changed before transaction")
        for name, data in updates.items():
            _assert_package_binding(root, package_dir, descriptor)
            written[name] = _replace_document(descriptor, name, data, snapshot["files"][name])
        validate()
        final, observed = _snapshot(root, package_dir, descriptor)
        if observed != updates or any(final["files"][name] != state for name, state in written.items()):
            raise WritebackError("package changed during post-write validation")
    except Exception as exc:
        try:
            for name, state in written.items():
                _assert_package_binding(root, package_dir, descriptor)
                _replace_document(descriptor, name, originals[name], state)
            if _snapshot(root, package_dir, descriptor)[1] != originals:
                raise WritebackError("documents no longer match the original pair")
        except Exception as rollback_error:
            raise WritebackError(
                f"{exc}; rollback conflict: {rollback_error}; transaction marker retained; inspect {backup_dir}"
            ) from exc
        os.unlink(TRANSACTION_NAME, dir_fd=descriptor)
        os.fsync(descriptor)
        raise WritebackError(f"{exc}; rolled back; backups: {backup_dir}") from exc
    os.unlink(TRANSACTION_NAME, dir_fd=descriptor)
    os.fsync(descriptor)
    return backup_dir


def _validate_package_tasks(tasks_content: str, expected_ids: list[str]) -> None:
    records = {record.id: record for record in parse_task_records(tasks_content)}
    for task_id in expected_ids:
        if task_id not in records:
            raise WritebackError(f"appended task failed to parse: {task_id}")


def cmd_plan(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    package_dir = resolve_specs_child(resolve_specs_root(root, args.specs_dir), "specs", args.slug)
    with _package_handle(root, package_dir) as descriptor:
        _refuse_transaction(descriptor)
        snapshot, documents = _snapshot(root, package_dir, descriptor)
        if _snapshot(root, package_dir, descriptor)[0] != snapshot:
            raise WritebackError("package changed while planning; retry from a stable snapshot")
    tasks_lines = documents["tasks.md"].decode("utf-8").splitlines()
    spec_lines = documents["spec.md"].decode("utf-8").splitlines()
    gaps = _load_gaps(_read_json_file(Path(args.gaps)))
    tasks, merged_gaps = _plan_tasks(gaps, tasks_lines)
    total_rows, writeback_rows = _count_writeback_rows(spec_lines)
    # Empty reports and existing pending fixes consume no new writeback round.
    next_action = "check-green" if not gaps else ("apply" if tasks else "run")
    if tasks and writeback_rows >= MAX_WRITEBACK_ROUNDS:
        next_action = "escalate"
    payload = {
        "version": PLAN_VERSION,
        "slug": args.slug,
        "snapshot": snapshot,
        "gaps_source": Path(args.gaps).name,
        "next": next_action,
        "writeback_rounds": writeback_rows,
        "correction_log_round": total_rows + 1,
        "correction_log_row": _correction_row(total_rows + 1, len(tasks), Path(args.gaps).name) if tasks else None,
        "tasks": tasks,
        "merged_gaps": merged_gaps,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        output = Path(args.out)
        reserved = [package_dir / name for name in (*DOCUMENT_NAMES, LOCK_NAME, TRANSACTION_NAME)]
        if output.is_symlink() or output.resolve() in reserved:
            raise WritebackError("plan output must not be a symlink or a package control file")
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return EXIT_ESCALATE if next_action == "escalate" else 0


def _validate_plan(payload: object, slug: str) -> tuple[list[dict[str, str]], str]:
    if not isinstance(payload, dict) or payload.get("version") != PLAN_VERSION:
        raise WritebackError("plan must be a version-2 JSON object; regenerate old plans")
    if payload.get("slug") != slug:
        raise WritebackError("plan slug does not match the target package")
    gaps_source = payload.get("gaps_source")
    if not isinstance(gaps_source, str) or not gaps_source.strip():
        raise WritebackError("plan is missing gaps_source provenance")
    if not isinstance(payload.get("snapshot"), dict):
        raise WritebackError("plan is missing its package snapshot; regenerate it")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise WritebackError("plan has no tasks to apply (check-green plans are not appliable)")
    seen_ids = set()
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
        if task_id in seen_ids:
            raise WritebackError(f"duplicate plan task id: {task_id}")
        seen_ids.add(task_id)
        extra = task.get("extra_gaps", [])
        if not isinstance(extra, list) or not all(isinstance(gap, str) and gap.strip() for gap in extra):
            raise WritebackError(f"plan task {task_id} has invalid extra_gaps")
        if any("\n" in value or "\r" in value for value in [task["gap"], task["boundary"], task["verify"], *extra]):
            raise WritebackError(f"plan task {task_id} fields must be single-line values")
        _round_suffix(task_id)
    return tasks, gaps_source


def _gate_converged(root: Path, slug: str, specs_dir: str | None = None) -> bool:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().with_name("check_spec_package.py")),
            "--root",
            str(root),
            "--slug",
            slug,
            *(["--specs-dir", specs_dir] if specs_dir is not None else []),
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
    package_dir = resolve_specs_child(resolve_specs_root(root, args.specs_dir), "specs", args.slug)
    payload = _read_json_file(Path(args.plan))
    _validate_plan(payload, args.slug)
    with _package_handle(root, package_dir) as descriptor, _exclusive_lock(descriptor):
        _refuse_transaction(descriptor)
        snapshot, originals = _snapshot(root, package_dir, descriptor)
        if payload["snapshot"] != snapshot:
            raise WritebackError("stale or foreign package snapshot; regenerate and review the plan")
        return _apply_locked(args, root, package_dir, descriptor, snapshot, originals, payload)


def _apply_locked(args, root, package_dir, descriptor, snapshot, originals, payload) -> int:
    tasks, gaps_source = _validate_plan(payload, args.slug)
    tasks_content, spec_content = (originals[name].decode("utf-8") for name in DOCUMENT_NAMES)
    total_rows, writeback_rows = _count_writeback_rows(spec_content.splitlines())
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
    if writeback_rows >= MAX_WRITEBACK_ROUNDS:
        print(json.dumps({"version": PLAN_VERSION, "slug": args.slug, "next": "escalate"}, ensure_ascii=False))
        return EXIT_ESCALATE
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
    updates = {"tasks.md": new_tasks_content.encode("utf-8"), "spec.md": new_spec_content.encode("utf-8")}
    if any(len(data) > MAX_FILE_BYTES for data in updates.values()):
        raise WritebackError("generated documents exceed the package size limit")

    def validate():
        _validate_package_tasks(_read_document(descriptor, "tasks.md")[0].decode("utf-8"), expected_ids)
        rerun_errors = correction_log_errors(_read_document(descriptor, "spec.md")[0].decode("utf-8"))
        if rerun_errors:
            raise WritebackError(f"post-write correction log validation failed: {rerun_errors}")
        if _gate_converged(root, args.slug, args.specs_dir):
            raise WritebackError("post-write gate unexpectedly converged with unchecked checkfix tasks")

    backup_dir = _transactional_update(root, package_dir, descriptor, snapshot, originals, updates, validate)
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
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())

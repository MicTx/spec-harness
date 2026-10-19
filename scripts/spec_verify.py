#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""
Executable verify recorder for Spec packages (F7 / AUD-02, AUD-05).

Turns a task's ``verify`` line from prose into executed evidence: the first
backtick span of a machine verify is run via ``subprocess`` (cwd = project
root, bounded timeout) and appended to the package's ``runs.jsonl`` together
with a worktree fingerprint binding the record to the exact tree it ran
against. ``check``/``done`` read those records and refuse packages whose
machine tasks lack a fresh ``exit == 0`` record — 「通过」再次意味着「真的
跑过」。

Protocol data shape (one JSON object per line, append-only):

    {task, cmd, exit, duration_s, stdout_tail_sha256, tree_fp, at}

``tree_fp`` covers HEAD + tracked diff (``git diff --binary HEAD``) + content
of untracked-but-not-ignored files, with every ``.spec/`` path excluded: the
package's own documents (checkboxes, evidence, this very file) legitimately
change after evidence capture and must not invalidate it. ``exit == -1`` means
the command did not exit normally (timeout kill or fatal spawn error).

Hooks never execute verify commands; they only read the conclusions this
recorder produces.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from spec_package_support import (
    SpecControlError,
    add_specs_dir_arg,
    parse_task_records,
    read_regular_text,
    resolve_specs_child,
    resolve_specs_root,
)

RUNS_FILENAME = "runs.jsonl"
RUNS_EVIDENCE_MARKER = "evidence: runs"
MANUAL_VERIFY_PREFIX = "manual:"
BACKTICK_SPAN = re.compile(r"`([^`]+)`")
DEFAULT_TIMEOUT_S = 600
STDOUT_TAIL_BYTES = 4096
FINGERPRINT_EXCLUDED_PREFIX = ".spec/"
#: exit code recorded when the command was killed (timeout) or never ran.
ABNORMAL_EXIT = -1


# --------------------------------------------------------------------------- #
# verify classification
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VerifyClassification:
    """Two-way verify semantics: machine command or manual justification."""

    kind: str  # "machine" | "manual"
    command: str = ""  # machine only: first backtick span
    reason: str = ""  # manual only: text after ``manual:``


def classify_verify(verify: str) -> VerifyClassification | None:
    """Classify a task ``verify`` value.

    ``manual:`` prefix wins even when its justification contains backticks
    (a read-only rationale may cite ``file:line`` spans). Otherwise the first
    backtick span is the machine command; later spans are prose and never
    execute. ``None`` means the contract is violated — no command and no
    ``manual:`` marker — which the ``evidence: runs`` gate reports as an
    error instead of guessing intent.
    """
    text = verify.strip()
    if text.startswith(MANUAL_VERIFY_PREFIX):
        return VerifyClassification("manual", reason=text[len(MANUAL_VERIFY_PREFIX) :].strip())
    match = BACKTICK_SPAN.search(text)
    if match:
        return VerifyClassification("machine", command=match.group(1).strip())
    return None


def runs_protocol_enabled(spec_content: str) -> bool:
    """True when spec.md declares the executable-evidence protocol.

    The marker is a case-sensitive substring anywhere in spec.md; packages
    without it keep the exact legacy gate conclusions.
    """
    return RUNS_EVIDENCE_MARKER in spec_content


_HOLLOW_EXACT = {"true", ":", "exit 0"}
_HOLLOW_COMPOSITION_MARKERS = (">", "<", "|", "&&", "||", ";")


def is_hollow_command(command: str) -> bool:
    """Reject commands that only *look* like verification.

    ``true``, ``:``, ``exit 0`` and a bare ``echo`` (no redirection, pipe, or
    composition) always succeed without checking anything, so executing them
    would manufacture fake evidence. They are refused before execution.
    """
    text = command.strip()
    if not text:
        return True
    if text in _HOLLOW_EXACT:
        return True
    parts = text.split()
    if parts and parts[0] == "echo":
        if not any(marker in text for marker in _HOLLOW_COMPOSITION_MARKERS):
            return True
    return False


# --------------------------------------------------------------------------- #
# worktree fingerprint
# --------------------------------------------------------------------------- #


def _git(root: Path, *args: str, text: bool) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=text)


def compute_tree_fingerprint(root: Path) -> str | None:
    """Bind evidence to the exact worktree it ran against.

    sha256 over: ``HEAD`` sha + NUL + ``git diff --binary HEAD`` (pathspec
    excluding ``.spec``) + NUL + per untracked-unignored file
    ``path + NUL + content + NUL`` (sorted by path, ``.spec`` excluded).
    Binary diff output is hashed as raw bytes. Returns ``None`` when the root
    is not a usable Git worktree; callers treat that as 不适用 (never crash,
    never silently pass).
    """
    head = _git(root, "rev-parse", "HEAD", text=True)
    if head.returncode == 0:
        head_text = head.stdout.strip()
        diff = _git(root, "diff", "--binary", "HEAD", "--", ".", ":(exclude).spec", text=False)
    else:
        # No commits yet (fresh ``git init``): fingerprint the worktree
        # against the index so an initial tree still hashes deterministically.
        head_text = ""
        diff = _git(root, "diff", "--binary", "--", ".", ":(exclude).spec", text=False)
    if diff.returncode != 0:
        return None
    hasher = hashlib.sha256()
    hasher.update(head_text.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(diff.stdout)
    hasher.update(b"\0")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z", text=False)
    if untracked.returncode != 0:
        return None
    for raw_path in sorted(part for part in untracked.stdout.split(b"\0") if part):
        path = raw_path.decode("utf-8", "surrogateescape")
        if path == ".spec" or path.startswith(FINGERPRINT_EXCLUDED_PREFIX):
            continue
        if path == "runs.jsonl" or path.endswith("/runs.jsonl"):
            # Engine-written evidence self-exclusion (F11): runs.jsonl
            # certifies the tree, so it can never be part of the tree it
            # certifies — without this, recording evidence for a package in
            # a non-default specs dir would stale itself by construction.
            continue
        file_path = root / Path(path)
        if not file_path.is_file():
            continue
        try:
            content = file_path.read_bytes()
        except OSError:
            return None
        hasher.update(path.encode("utf-8", "surrogateescape"))
        hasher.update(b"\0")
        hasher.update(content)
        hasher.update(b"\0")
    return hasher.hexdigest()


# --------------------------------------------------------------------------- #
# runs.jsonl reading and gate evaluation (shared by check / complete / check_all)
# --------------------------------------------------------------------------- #


def read_runs_records(package_dir: Path) -> list[dict] | None:
    """Parse ``runs.jsonl`` into records; ``None`` when the file is absent.

    A present-but-malformed file fails closed with the offending line number:
    the gate must neither crash nor silently drop evidence it cannot read.
    """
    entries = read_runs_entries(package_dir)
    if entries is None:
        return None
    return [entry.record for entry in entries]


class RunEntry:
    """One ``runs.jsonl`` line: audit position plus exact source bytes."""

    __slots__ = ("line", "raw", "record", "start_offset")

    def __init__(self, line: int, raw: str, record: dict, start_offset: int):
        self.line = line
        self.raw = raw
        self.record = record
        self.start_offset = start_offset


def read_runs_entries(package_dir: Path) -> list[RunEntry] | None:
    """Line-annotated parse of ``runs.jsonl``; ``None`` when the file is absent.

    Single parser behind both the gate (``read_runs_records``) and the
    read-only ``show`` view: the same fail-closed-by-line-number contract,
    with the raw line kept so bounded views stay byte-reassemblable.
    """
    path = package_dir / RUNS_FILENAME
    if not path.is_file():
        return None
    entries: list[RunEntry] = []
    content = read_regular_text(path)
    offset = 0
    for number, line in enumerate(content.splitlines(), 1):
        line_bytes = len(line.encode("utf-8")) + 1  # +1 for the consumed newline
        if not line.strip():
            offset += line_bytes
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{RUNS_FILENAME} 第 {number} 行不是合法 JSON：{exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{RUNS_FILENAME} 第 {number} 行不是 JSON 对象")
        entries.append(RunEntry(number, line, record, offset))
        offset += line_bytes
    return entries


def latest_record_by_task(records: list[dict]) -> dict[str, dict]:
    """Last record per task id — the newest fact wins, history stays for audit."""
    latest: dict[str, dict] = {}
    for record in records:
        task = record.get("task")
        if isinstance(task, str) and task:
            latest[task] = record
    return latest


def _machine_tasks(tasks_content: str) -> tuple[list[tuple[str, str, str]], list[str], int]:
    """(machine (id, command, verify) triples, errors, manual count)."""
    machine: list[tuple[str, str, str]] = []
    errors: list[str] = []
    manual_count = 0
    records = parse_task_records(tasks_content)
    for record in records:
        classification = classify_verify(record.verify)
        if classification is None:
            errors.append(f"{record.id}：verify 无反引号命令且非 manual:（evidence: runs 包要求二者其一）")
            continue
        if classification.kind == "manual":
            manual_count += 1
            continue
        machine.append((record.id, classification.command, record.verify))
    return machine, errors, manual_count


def _runs_gate_states(
    spec_content: str,
    tasks_content: str,
    package_dir: Path | None,
    root: Path | None,
) -> tuple[bool | None, list[str], str, dict[str, str]]:
    """Single-pass runs-gate evaluation with per-task freshness states.

    The state comparison lives ONLY here: ``evaluate_runs_gate`` (gate
    callers) and ``show`` (bounded read view) consume the same computation —
    there is no second fingerprint-comparison truth.
    """
    if not runs_protocol_enabled(spec_content):
        return None, [], "", {}
    if package_dir is None or root is None:
        return None, [], "适用外：缺少 package_dir/root（路由与报告上下文不评估运行证据）", {}
    try:
        machine, errors, manual_count = _machine_tasks(tasks_content)
    except SpecControlError as exc:
        return False, [f"任务清单解析失败：{exc}"], "任务清单解析失败", {}
    manual_ids = {
        record.id
        for record in parse_task_records(tasks_content)
        if (classification := classify_verify(record.verify)) is not None and classification.kind == "manual"
    }
    try:
        records = read_runs_records(package_dir)
    except (OSError, UnicodeError, ValueError) as exc:
        return False, [f"runs 证据不可读：{exc}"], "runs 证据不可读", {task_id: "unverified" for task_id, _c, _v in machine}
    fingerprint = compute_tree_fingerprint(root)
    if fingerprint is None:
        return None, [], "适用外：非 Git 工作区或指纹不可计算，跳过 runs 比对", {}
    latest = latest_record_by_task(records or [])
    passed = 0
    states: dict[str, str] = {task_id: "manual" for task_id in manual_ids}
    for task_id, _command, _verify in machine:
        record = latest.get(task_id)
        if record is None:
            errors.append(f"{task_id}：未验证（{RUNS_FILENAME} 无该任务记录）")
            states[task_id] = "unverified"
            continue
        exit_code = record.get("exit")
        if exit_code != 0:
            errors.append(f"{task_id}：未验证（最近记录 exit={exit_code}）")
            states[task_id] = "unverified"
            continue
        recorded_fp = record.get("tree_fp")
        if not isinstance(recorded_fp, str) or recorded_fp != fingerprint:
            shown = recorded_fp if isinstance(recorded_fp, str) else str(recorded_fp)
            errors.append(
                f"{task_id}：证据过期（记录指纹 {shown[:12]} ≠ 当前工作树指纹 {fingerprint[:12]}；"
                "工作树变更后需重跑 spec_verify.py）"
            )
            states[task_id] = "stale"
            continue
        passed += 1
        states[task_id] = "fresh"
    detail = f"机器任务 {passed}/{len(machine)} 最近记录 exit==0 且指纹匹配；manual 任务 {manual_count} 项"
    return not errors, errors, detail, states


def evaluate_runs_gate(
    spec_content: str,
    tasks_content: str,
    package_dir: Path | None,
    root: Path | None,
) -> tuple[bool | None, list[str], str]:
    """Evaluate the executable-evidence gate for an ``evidence: runs`` package.

    Returns ``(ok, errors, detail)``. ``ok is None`` means 不适用 — package
    not opted in, or routing/report context without ``root``/``package_dir``,
    or a non-Git root; the gate never blocks there (same contract as the
    evidence anchor). Machine tasks need a latest record with ``exit == 0``
    and ``tree_fp`` equal to the current fingerprint; anything else is named
    precisely: 未验证 (no usable record) or 证据过期 (tree moved since).
    """
    ok, errors, detail, _states = _runs_gate_states(spec_content, tasks_content, package_dir, root)
    return ok, errors, detail


def machine_verification_summary(tasks_content: str, package_dir: Path) -> str | None:
    """``通过/总数`` machine-verification rate for completion-summary.md.

    Manual tasks are excluded from the denominator (they are honest human
    work, not machine evidence). ``None`` when the package has no machine
    tasks at all — nothing to report.
    """
    machine, _errors, manual_count = _machine_tasks(tasks_content)
    if not machine:
        return None
    records = read_runs_records(package_dir) or []
    latest = latest_record_by_task(records)
    passed = sum(1 for task_id, _command, _verify in machine if latest.get(task_id, {}).get("exit") == 0)
    suffix = f"；manual 任务 {manual_count} 项不计入分母" if manual_count else ""
    return f"{passed}/{len(machine)}{suffix}"


# --------------------------------------------------------------------------- #
# run subcommand: execute and record
# --------------------------------------------------------------------------- #


def _record_run(
    package_dir: Path,
    task_id: str,
    command: str,
    exit_code: int,
    duration_s: float,
    stdout_bytes: bytes,
    tree_fp: str,
) -> dict:
    record = {
        "task": task_id,
        "cmd": command,
        "exit": exit_code,
        "duration_s": round(duration_s, 3),
        "stdout_tail_sha256": hashlib.sha256(stdout_bytes[-STDOUT_TAIL_BYTES:]).hexdigest(),
        "tree_fp": tree_fp,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with (package_dir / RUNS_FILENAME).open("a", encoding="utf-8") as runs_file:
        runs_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def run_selected_tasks(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        specs_root = resolve_specs_root(root, args.specs_dir)
        package_dir = resolve_specs_child(specs_root, "specs", args.slug)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    tasks_path = package_dir / "tasks.md"
    if not tasks_path.is_file():
        print(f"error: required file missing: {tasks_path}", file=sys.stderr)
        return 1
    try:
        tasks_content = read_regular_text(tasks_path)
        records = parse_task_records(tasks_content)
    except (OSError, UnicodeError, ValueError, SpecControlError) as exc:
        print(f"error: cannot parse tasks: {exc}", file=sys.stderr)
        return 1

    by_id = {record.id: record for record in records}
    if args.all:
        selected = list(records)
    elif args.task:
        unknown = [task_id for task_id in args.task if task_id not in by_id]
        if unknown:
            print(f"error: unknown task id(s): {', '.join(unknown)}", file=sys.stderr)
            return 1
        selected = [by_id[task_id] for task_id in args.task]
    else:
        print("error: pass --task <id> (repeatable) or --all to select tasks", file=sys.stderr)
        return 1

    package_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for record in selected:
        classification = classify_verify(record.verify)
        if classification is None:
            print(f"{record.id}: 契约错误（无反引号命令且非 manual:）")
            failures += 1
            continue
        if classification.kind == "manual":
            print(f"{record.id}: 跳过（manual：{classification.reason[:60]}）")
            continue
        command = classification.command
        if is_hollow_command(command):
            print(f"{record.id}: 拒绝执行空洞命令：{command!r}（true/:/exit 0/纯 echo 不构成验证）")
            failures += 1
            continue
        if "check_spec_package.py" in command and args.slug in command:
            print(
                f"{record.id}: 拒绝自指命令：verify 调用本包 check 门禁"
                "（执行中读到自身缺失/失败记录，最近一条语义下永不满足）；"
                "包级 check 通过由归档门禁自证，见 references/templates.md 自指禁令"
            )
            failures += 1
            continue
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(root),
                capture_output=True,
                timeout=args.timeout,
            )
            exit_code = completed.returncode
            stdout_bytes = completed.stdout or b""
            stderr_text = (completed.stderr or b"").decode("utf-8", "replace").strip()
        except subprocess.TimeoutExpired as exc:
            exit_code = ABNORMAL_EXIT
            stdout_bytes = exc.stdout if isinstance(exc.stdout, bytes) else b""
            stderr_text = f"timeout after {args.timeout}s"
        duration = time.monotonic() - started
        # Fingerprint after execution: the record binds to the tree the
        # verification leaves behind, so a command that legitimately formats
        # files keeps its own evidence fresh.
        tree_fp = compute_tree_fingerprint(root)
        if tree_fp is None:
            print(f"{record.id}: 指纹不可计算（非 Git 工作区？），拒绝记录", file=sys.stderr)
            failures += 1
            continue
        _record_run(package_dir, record.id, command, exit_code, duration, stdout_bytes, tree_fp)
        verdict = "OK" if exit_code == 0 else "FAIL"
        tail = f" exit={exit_code}"
        if exit_code == ABNORMAL_EXIT:
            tail = " exit=timeout"
        if stderr_text and exit_code != 0:
            tail += f" stderr={stderr_text[:200]!r}"
        print(f"{record.id}: {verdict}{tail} ({duration:.2f}s) fp={tree_fp[:12]} -> {RUNS_FILENAME}")
        if exit_code != 0:
            failures += 1

    print(f"记录 {RUNS_FILENAME}：{package_dir / RUNS_FILENAME}")
    if failures:
        print(f"{failures} 个任务未通过（失败命令/契约错误/空洞命令均已计入）", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------- #
# show subcommand: bounded read-only evidence view (F3 / doc 03 §2.3)
# --------------------------------------------------------------------------- #

SHOW_CAPABILITY = "evidence"
DEFAULT_MAX_BYTES = 16384
DEFAULT_MAX_LINES = 200
HARD_MAX_BYTES = 1024 * 1024
HARD_MAX_LINES = 5000


class ShowError(Exception):
    """Classified show failure; kind stays in the shared envelope table."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _encode_show_token(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")


def _decode_show_token(token: str) -> dict:
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8"))
    except (binascii.Error, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ShowError("invalid_query", f"page token 不可解码：{exc}") from exc
    if not isinstance(payload, dict):
        raise ShowError("invalid_query", "page token 必须编码 JSON 对象")
    return payload


def _show_freshness(spec_content: str, tasks_content: str, package_dir: Path, root: Path) -> dict:
    """Freshness block strictly from the ``_runs_gate_states`` computation."""
    ok, errors, detail, states = _runs_gate_states(spec_content, tasks_content, package_dir, root)
    if ok is None:
        per_task = {}
        try:
            per_task = {record.id: "n_a" for record in parse_task_records(tasks_content)}
        except SpecControlError:
            per_task = {}
        reason = detail or "适用外：包未启用 evidence: runs 协议，无 runs 新鲜度可言"
        return {"status": "n_a", "ok": None, "reason": reason, "errors": errors, "per_task": per_task}
    return {"status": "evaluated", "ok": ok, "detail": detail, "errors": errors, "per_task": states}


def show_evidence(args: argparse.Namespace) -> int:
    """Bounded, read-only, version-pinned view over ``runs.jsonl``.

    Never executes verify commands and never writes files; the only
    subprocesses are the read-only git calls inside the tree fingerprint.
    A missing ``runs.jsonl`` is the *unverified* fact it is — a ``missing``
    error, never an empty success.
    """
    if not 1 <= args.max_bytes <= HARD_MAX_BYTES:
        raise ShowError("invalid_query", f"--max-bytes 必须在 1..{HARD_MAX_BYTES} 内（硬上限，超限是错误不是截断）")
    if not 1 <= args.max_lines <= HARD_MAX_LINES:
        raise ShowError("invalid_query", f"--max-lines 必须在 1..{HARD_MAX_LINES} 内（硬上限，超限是错误不是截断）")
    if args.failures_only and args.no_freshness:
        raise ShowError("invalid_query", "--failures-only 的 stale 判定依赖 freshness，不能与 --no-freshness 同时使用")

    root = Path(args.root).resolve()
    try:
        specs_root = resolve_specs_root(root, args.specs_dir)
        package_dir = resolve_specs_child(specs_root, "specs", args.slug)
    except ValueError as exc:
        raise ShowError("invalid_query", str(exc)) from exc
    spec_path = package_dir / "spec.md"
    tasks_path = package_dir / "tasks.md"
    if not spec_path.is_file() or not tasks_path.is_file():
        raise ShowError("missing", f"task package files missing: {spec_path} / {tasks_path}")
    try:
        spec_content = read_regular_text(spec_path)
        tasks_content = read_regular_text(tasks_path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ShowError("unreadable", f"包文档不可读：{exc}") from exc
    try:
        known_tasks = {record.id for record in parse_task_records(tasks_content)}
    except SpecControlError as exc:
        raise ShowError("unreadable", f"任务清单不可解析：{exc}") from exc
    unknown = [task_id for task_id in args.task if task_id not in known_tasks]
    if unknown:
        raise ShowError("invalid_query", f"未知任务 id：{'、'.join(unknown)}")
    task_filter = set(args.task) if args.task else None

    try:
        entries = read_runs_entries(package_dir)
    except FileNotFoundError as exc:
        raise ShowError("missing", f"{RUNS_FILENAME} 不存在：未验证事实，不是空列表") from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise ShowError("unreadable", str(exc)) from exc
    if entries is None:
        raise ShowError("missing", f"{RUNS_FILENAME} 不存在：未验证事实，不是空列表")
    content_bytes = (package_dir / RUNS_FILENAME).read_bytes()
    version = hashlib.sha256(content_bytes).hexdigest()

    # View construction: deterministic ordering derived only from file content.
    view = list(entries)  # history view: file order
    if not args.history:
        latest: dict[str, RunEntry] = {}
        for entry in entries:
            task = entry.record.get("task")
            if isinstance(task, str) and task:
                latest[task] = entry
        view = sorted(latest.values(), key=lambda entry: entry.line)
        if args.failures_only:
            freshness = _show_freshness(spec_content, tasks_content, package_dir, root)
            view = [
                entry
                for entry in view
                if freshness["per_task"].get(entry.record.get("task", "")) in {"stale", "unverified"}
            ]
    elif args.failures_only:
        freshness = _show_freshness(spec_content, tasks_content, package_dir, root)
        failing = {
            task for task, state in freshness["per_task"].items() if state in {"stale", "unverified"}
        }
        view = [entry for entry in view if entry.record.get("task") in failing]
    if task_filter is not None:
        view = [entry for entry in view if entry.record.get("task") in task_filter]

    # Resume point: token pins the exact next line (number + byte offset).
    start_index = 0
    if args.page_token:
        token = _decode_show_token(args.page_token)
        if token.get("capability") != SHOW_CAPABILITY:
            raise ShowError("invalid_query", "page token 不属于 evidence 能力")
        if token.get("version") != version:
            raise ShowError(
                "version_changed",
                f"runs.jsonl 摘要已变化（token {str(token.get('version'))[:12]} ≠ 当前 {version[:12]}），"
                "请从头重取，不得跨版本拼接",
            )
        line_index = token.get("line_index")
        offset_bytes = token.get("offset_bytes")
        start_index = next((i for i, entry in enumerate(view) if entry.line == line_index), None)
        if start_index is None or view[start_index].start_offset != offset_bytes:
            raise ShowError("invalid_query", f"page token 锚点不匹配：line={line_index}, offset={offset_bytes}")

    items: list[dict] = []
    raw_bytes = 0
    index = start_index
    truncated_reason = "none"
    next_token = None
    while index < len(view):
        entry = view[index]
        entry_bytes = len(entry.raw.encode("utf-8"))
        if len(items) >= args.max_lines:
            truncated_reason = "line_budget"
            break
        if entry_bytes > args.max_bytes:
            # Oversized single line: an explicit marker, never a silent drop
            # or a hard truncation pretending to be the record.
            items.append(
                {
                    "line": entry.line,
                    "oversized": True,
                    "length_bytes": entry_bytes,
                    "sha256": hashlib.sha256(entry.raw.encode("utf-8")).hexdigest(),
                }
            )
            truncated_reason = "oversized_line"
            index += 1
            break
        if raw_bytes + entry_bytes > args.max_bytes:
            truncated_reason = "byte_budget"
            break
        items.append({"line": entry.line, "record": entry.record, "raw": entry.raw})
        raw_bytes += entry_bytes
        index += 1

    elided = truncated_reason == "oversized_line"
    complete = index >= len(view) and not elided
    if complete:
        truncated_reason = "none"
    if index < len(view):
        anchor = view[index]
        next_token = _encode_show_token(
            {
                "capability": SHOW_CAPABILITY,
                "offset_bytes": anchor.start_offset,
                "line_index": anchor.line,
                "version": version,
            }
        )

    query = {
        "slug": args.slug,
        "task": list(args.task) if args.task else None,
        "failures_only": args.failures_only,
        "history": args.history,
        "freshness": not args.no_freshness,
        "max_bytes": args.max_bytes,
        "max_lines": args.max_lines,
    }
    payload: dict = {
        "capability": SHOW_CAPABILITY,
        "query": query,
        "version": {"source_sha256": version, "records": len(entries)},
        "items": items,
        "counts": {"returned": len(items), "total_matching": len(view)},
        "complete": complete,
        "truncated_reason": truncated_reason,
        # Line-granular slicing: cuts only ever fall on line (codepoint-safe)
        # boundaries, so a partial line is never returned.
        "partial_line": False,
        "next": next_token,
        "stdout_note": "stdout_tail_sha256 仅是尾部 4096 B 摘要；正文不可从 runs.jsonl 恢复，完整证据=重跑 run",
    }
    if not args.no_freshness:
        payload["freshness"] = _show_freshness(spec_content, tasks_content, package_dir, root)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executable verify recorder for Spec packages.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Execute task verify commands and append runs.jsonl")
    run_parser.add_argument("--root", default=".", help="Project root (default: current directory)")
    run_parser.add_argument("--slug", required=True, help="Task package slug")
    add_specs_dir_arg(run_parser)
    run_parser.add_argument("--task", action="append", default=[], help="Task id to run; repeat for multiple")
    run_parser.add_argument("--all", action="store_true", help="Run every task's machine verify")
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"Per-command timeout in seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    show_parser = subparsers.add_parser(
        "show",
        help="Bounded read-only view over runs.jsonl (never executes verify, never writes)",
    )
    show_parser.add_argument("--root", default=".", help="Project root (default: current directory)")
    show_parser.add_argument("--slug", required=True, help="Task package slug")
    add_specs_dir_arg(show_parser)
    show_parser.add_argument("--task", action="append", default=[], help="Restrict to task id; repeat for multiple")
    show_parser.add_argument(
        "--failures-only", action="store_true", help="Latest view filtered to failing or stale tasks"
    )
    show_parser.add_argument("--history", action="store_true", help="All records in file order instead of latest-per-task")
    show_parser.add_argument("--page-token", default=None, help="Opaque continuation token from a prior envelope's `next`")
    show_parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"Raw-line byte budget per page (default {DEFAULT_MAX_BYTES}, hard cap {HARD_MAX_BYTES})",
    )
    show_parser.add_argument(
        "--max-lines",
        type=int,
        default=DEFAULT_MAX_LINES,
        help=f"Item line budget per page (default {DEFAULT_MAX_LINES}, hard cap {HARD_MAX_LINES})",
    )
    show_parser.add_argument(
        "--no-freshness",
        action="store_true",
        help="Omit the freshness block (gate conclusion stays with check)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "show":
        try:
            return show_evidence(args)
        except ShowError as exc:
            print(f"error: {exc.kind}: {exc.message}", file=sys.stderr)
            return 1
    return run_selected_tasks(args)


if __name__ == "__main__":
    sys.exit(main())

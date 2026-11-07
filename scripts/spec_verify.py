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

    {task, cmd, exit, duration_s, stdout_tail_sha256, tree_fp, package_fp, package_id, at}

``tree_fp`` covers HEAD + tracked diff (``git diff --binary HEAD``) + content
of untracked-but-not-ignored files, excluding ``.spec/`` and a custom specs
package subtree. ``package_fp`` binds auxiliary files in the current package.
Both fingerprints describe observed states, not atomic snapshots.
``exit == -1`` means execution failed abnormally or changed package inputs.

Hooks never execute verify commands; they only read the conclusions this
recorder produces.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from safe_open_support import SafeOpenError, open_regular_read
from spec_package_support import (
    MAX_SPEC_FILE_BYTES,
    SpecControlError,
    add_specs_dir_arg,
    autonomous_question_fingerprints,
    decision_question_fingerprints,
    parse_task_records,
    read_regular_text,
    resolve_specs_child,
    resolve_specs_root,
    rule_message,
)

RUNS_FILENAME = "runs.jsonl"
RUNS_EVIDENCE_MARKER = "evidence: runs"
MANUAL_VERIFY_PREFIX = "manual:"
BACKTICK_SPAN = re.compile(r"`([^`]+)`")
DEFAULT_TIMEOUT_S = 600
STDOUT_TAIL_BYTES = 4096
FINGERPRINT_EXCLUDED_PREFIX = ".spec/"
# Archive container between the specs root and the slug; archiving only moves the
# directory, so records keep the identity they were written under.
ARCHIVE_DIRNAME = "archive"
SPECS_CONTAINER = "specs"
PACKAGE_STATE_FILES = frozenset(
    {
        "spec.md",
        "tasks.md",
        "checklist.md",
        "handoff.md",
        "completion-summary.md",
        "runs.jsonl",
        "runs.jsonl.corrupt",
        "adoption.json",
        "update-checkpoint.json",
    }
)
#: Exit code for timeouts, spawn errors, or auxiliary inputs changed during verification.
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


def compute_tree_fingerprint(root: Path, package_dir: Path | None = None) -> str | None:
    """Bind evidence to the exact worktree it ran against.

    sha256 over: ``HEAD`` sha + NUL + ``git diff --binary HEAD`` (pathspec
    excluding ``.spec``) + NUL + per untracked-unignored file
    ``path + NUL + content + NUL`` (sorted by path, ``.spec`` excluded).
    A custom specs package subtree is also excluded: the package fingerprint
    separately binds the current package's auxiliary files. Binary diff output
    is hashed as raw bytes. Returns ``None`` for an unusable Git worktree.
    """
    excluded_package_root = ""
    if package_dir is not None:
        try:
            relative_package = package_dir.relative_to(root)
        except ValueError:
            return None
        if relative_package.parts and relative_package.parts[0] != ".spec":
            excluded_package_root = relative_package.parent.as_posix()
    exclusions = [":(exclude).spec"]
    if excluded_package_root:
        exclusions.append(f":(exclude,literal){excluded_package_root}")
    head = _git(root, "rev-parse", "HEAD", text=True)
    if head.returncode == 0:
        head_text = head.stdout.strip()
        diff = _git(root, "diff", "--binary", "HEAD", "--", ".", *exclusions, text=False)
    else:
        # No commits yet (fresh ``git init``): fingerprint the worktree
        # against the index so an initial tree still hashes deterministically.
        head_text = ""
        diff = _git(root, "diff", "--binary", "--", ".", *exclusions, text=False)
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
        if excluded_package_root and (path == excluded_package_root or path.startswith(excluded_package_root + "/")):
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


def package_input_snapshot(package_dir: Path) -> tuple[str, dict[str, tuple[int, bytes]]]:
    """Hash auxiliary files and retain ephemeral per-file digests for diagnostics."""
    if package_dir.resolve(strict=True) != package_dir.absolute():
        raise ValueError("package path contains a symlink")
    digest = hashlib.sha256()
    entries: dict[str, tuple[int, bytes]] = {}
    for path in sorted(package_dir.rglob("*")):
        relative = path.relative_to(package_dir)
        if len(relative.parts) == 1 and relative.name in PACKAGE_STATE_FILES:
            continue
        if path.is_symlink():
            raise ValueError(f"package auxiliary path is a symlink: {relative}")
        if path.is_dir():
            continue
        try:
            with open_regular_read(path, max_bytes=MAX_SPEC_FILE_BYTES) as descriptor:
                content = hashlib.sha256()
                while chunk := os.read(descriptor, 65536):
                    content.update(chunk)
                mode = os.fstat(descriptor).st_mode & 0o111
        except SafeOpenError as exc:
            raise ValueError(str(exc)) from exc
        name = relative.as_posix()
        file_digest = content.digest()
        entries[name] = (mode, file_digest)
        digest.update(os.fsencode(name))
        digest.update(b"\0")
        digest.update(str(mode).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_digest)
    return digest.hexdigest(), entries


def compute_package_fingerprint(package_dir: Path) -> str:
    """Bind auxiliary package files while leaving harness state mutable."""
    return package_input_snapshot(package_dir)[0]


def package_identity(root: Path, package_dir: Path) -> str:
    """Identify a package relative to its project, including a custom specs root.

    This is the identity a run record is *written* under, and the one an
    active package is judged by.
    """
    return package_dir.relative_to(root).as_posix()


def package_identities(root: Path, package_dir: Path) -> frozenset[str]:
    """Identities a run record may carry and still describe this package.

    Archiving only moves the directory (``<specs>/<slug>`` ->
    ``<specs>/archive/<slug>``, possibly through a dated retired container),
    so an archived package keeps the identity its records earned while it was
    active — historical archives are not regraded by a path rename. An active
    package accepts exactly one identity, so a copied archived record can
    never satisfy a new active package.

    The relaxation is anchored to the layout the archive step creates: an
    ``archive`` segment directly below ``specs``. Any other directory that
    happens to be called ``archive`` stays strict.
    """
    relative = package_identity(root, package_dir)
    identities = {relative}
    parts = relative.split("/")
    for index in range(len(parts) - 2, 0, -1):
        if parts[index] == ARCHIVE_DIRNAME and parts[index - 1] == SPECS_CONTAINER:
            identities.add("/".join(parts[:index] + [parts[-1]]))
            break
    return frozenset(identities)


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


def duplicate_verify_error(task_id: str, block_text: str) -> str | None:
    if len(re.findall(r"(?m)^-\s+verify:", block_text)) > 1:
        return rule_message("SPEC-VERIFY-002", f"{task_id}：verify 必须唯一；合并命令或拆分任务")
    return None


def stale_record_reason(
    record: dict, command: str, tree_fp: str, package_fp: str, package_ids: frozenset[str]
) -> str | None:
    """Use the same freshness contract for the runs gate and explicit claims."""
    if record.get("cmd") != command:
        return "记录命令与当前 verify 不一致；需重跑 spec_verify.py"
    if record.get("package_fp") != package_fp:
        return "包内辅助文件变化或旧记录缺少 package_fp；需重跑 spec_verify.py"
    recorded_id = record.get("package_id")
    if not isinstance(recorded_id, str) or recorded_id not in package_ids:
        return "任务包身份不匹配或旧记录缺少身份；需重跑 spec_verify.py"
    recorded_fp = record.get("tree_fp")
    if not isinstance(recorded_fp, str) or recorded_fp != tree_fp:
        shown = recorded_fp if isinstance(recorded_fp, str) else str(recorded_fp)
        return f"记录指纹 {shown[:12]} ≠ 当前工作树指纹 {tree_fp[:12]}；工作树变更后需重跑 spec_verify.py"
    return None


def _machine_tasks(tasks_content: str) -> tuple[list[tuple[str, str, str]], list[str], int]:
    """(machine (id, command, verify) triples, errors, manual count)."""
    machine: list[tuple[str, str, str]] = []
    errors: list[str] = []
    manual_count = 0
    records = parse_task_records(tasks_content)
    for record in records:
        duplicate = duplicate_verify_error(record.id, record.block_text)
        if duplicate:
            errors.append(duplicate)
            continue
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
        unverified = {task_id: "unverified" for task_id, _c, _v in machine}
        return False, [rule_message("SPEC-EVID-003", f"runs 证据不可读：{exc}")], "runs 证据不可读", unverified
    fingerprint = compute_tree_fingerprint(root, package_dir)
    if fingerprint is None:
        return None, [], "适用外：非 Git 工作区或指纹不可计算，跳过 runs 比对", {}
    try:
        package_fingerprint = compute_package_fingerprint(package_dir)
        current_package_ids = package_identities(root, package_dir)
    except (OSError, UnicodeError, ValueError) as exc:
        unverified = {task_id: "unverified" for task_id, _c, _v in machine}
        return False, [rule_message("SPEC-EVID-003", f"包内辅助文件不可读：{exc}")], "包内辅助文件不可读", unverified
    latest = latest_record_by_task(records or [])
    passed = 0
    states: dict[str, str] = {task_id: "manual" for task_id in manual_ids}
    for task_id, command, _verify in machine:
        record = latest.get(task_id)
        if record is None:
            errors.append(rule_message("SPEC-EVID-001", f"{task_id}：未验证（{RUNS_FILENAME} 无该任务记录）"))
            states[task_id] = "unverified"
            continue
        exit_code = record.get("exit")
        if exit_code != 0:
            errors.append(rule_message("SPEC-EVID-001", f"{task_id}：未验证（最近记录 exit={exit_code}）"))
            states[task_id] = "unverified"
            continue
        stale_reason = stale_record_reason(record, command, fingerprint, package_fingerprint, current_package_ids)
        if stale_reason:
            errors.append(rule_message("SPEC-EVID-002", f"{task_id}：证据过期（{stale_reason}）"))
            states[task_id] = "stale"
            continue
        passed += 1
        states[task_id] = "fresh"
    detail = f"机器任务 {passed}/{len(machine)} 最近记录 exit==0 且命令、双指纹匹配；manual 任务 {manual_count} 项"
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
    and ``cmd`` equal to the current verify and ``tree_fp`` equal to the current fingerprint; anything else is named
    precisely: 未验证 (no usable record) or 证据过期 (tree moved since).
    """
    ok, errors, detail, _states = _runs_gate_states(spec_content, tasks_content, package_dir, root)
    return ok, errors, detail


def evaluate_question_evidence(
    spec_content: str, tasks_content: str, package_dir: Path | None, root: Path | None
) -> list[str]:
    from spec_package_support import autonomous_question_contract

    questions, _human, _errors = autonomous_question_contract(spec_content, tasks_content)
    tasks = {record.id: record for record in parse_task_records(tasks_content)} if questions else {}
    contract_errors = []
    for question in questions:
        task = tasks.get(question.task)
        verification = classify_verify(task.verify) if task else None
        if verification is None or verification.kind != "machine" or is_hollow_command(verification.command):
            contract_errors.append(f"{question.text}：自主调查 task 必须提供非空洞机器 verify，不能以 manual 替代")
    closed = [question for question in questions if question.closed]
    if not closed:
        return contract_errors
    if package_dir is None or root is None:
        return [*contract_errors, "自主问题关闭证据未评估；需在项目根目录运行 check"]
    _ok, _gate_errors, _detail, states = _runs_gate_states(spec_content, tasks_content, package_dir, root)
    latest = latest_record_by_task(read_runs_records(package_dir) or [])
    errors: list[str] = list(contract_errors)
    for question in closed:
        record = latest.get(question.task, {})
        if states.get(question.task) != "fresh" or type(record.get("exit")) is not int:
            errors.append(f"{question.text}：{question.task} 无新鲜、匹配且 exit=0 的机器运行证据")
        elif not isinstance(record.get("question_fps"), list) or question.fingerprint not in record["question_fps"]:
            errors.append(f"{question.text}：运行证据未绑定当前问题与结论；填结论后重跑 verify，再勾选")
    return errors


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


def fresh_machine_evidence(
    spec_content: str,
    tasks_content: str,
    package_dir: Path,
    root: Path,
) -> list[str]:
    """Closed-syntax evidence lines for fresh passing machine tasks.

    Used by ``done --archive`` so users never have to hand-copy a command and
    task id into the completion summary. The helper is read-only: it never
    appends to runs.jsonl and never relaxes the verifier's freshness rules.
    """
    if not runs_protocol_enabled(spec_content):
        return []
    machine, _errors, _manual_count = _machine_tasks(tasks_content)
    records = read_runs_records(package_dir) or []
    latest = latest_record_by_task(records)
    fingerprint = compute_tree_fingerprint(root, package_dir)
    package_fingerprint = compute_package_fingerprint(package_dir)
    current_package_ids = package_identities(root, package_dir)
    lines: list[str] = []
    for task_id, command, _verify in machine:
        record = latest.get(task_id)
        if record is None or record.get("exit") != 0 or fingerprint is None:
            continue
        if stale_record_reason(record, command, fingerprint, package_fingerprint, current_package_ids):
            continue
        lines.append(f"`{command}` -> exit 0 (runs:{task_id})")
    return lines


def _record_run(
    package_dir: Path,
    task_id: str,
    command: str,
    exit_code: int,
    duration_s: float,
    stdout_bytes: bytes,
    tree_fp: str,
    package_fp: str,
    package_id: str,
    question_fps: list[str] | None = None,
) -> dict:
    record = {
        "task": task_id,
        "cmd": command,
        "exit": exit_code,
        "duration_s": round(duration_s, 3),
        "stdout_tail_sha256": hashlib.sha256(stdout_bytes[-STDOUT_TAIL_BYTES:]).hexdigest(),
        "tree_fp": tree_fp,
        "package_fp": package_fp,
        "package_id": package_id,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if question_fps:
        record["question_fps"] = question_fps
    if package_dir.resolve(strict=True) != package_dir.absolute():
        raise ValueError("package path contains a symlink")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("platform lacks O_NOFOLLOW support for runs.jsonl")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | nofollow | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(package_dir / RUNS_FILENAME, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("runs.jsonl must be a regular, non-hardlinked file")
        pending = memoryview((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
        while pending:
            written = os.write(descriptor, pending)
            if written == 0:
                raise OSError("cannot append runs.jsonl")
            pending = pending[written:]
    finally:
        os.close(descriptor)
    return record


def _record_abnormal_attempt(
    package_dir: Path,
    task_id: str,
    command: str,
    duration_s: float,
    stdout_bytes: bytes,
    tree_fp: str,
    package_fp: str,
    package_id: str,
) -> None:
    """Do not leave a previous pass as the latest result after an attempted run."""
    try:
        _record_run(
            package_dir, task_id, command, ABNORMAL_EXIT, duration_s, stdout_bytes, tree_fp, package_fp, package_id
        )
    except (OSError, ValueError) as exc:
        print(rule_message("SPEC-EVID-003", f"无法记录失败的验证尝试：{exc}"), file=sys.stderr)


def run_selected_tasks(args: argparse.Namespace) -> int:
    from worker_decision_guard import DecisionGuard, decision_guard_errors

    root = Path(args.root).resolve()
    decision_errors = decision_guard_errors(root)
    if decision_errors:
        print("; ".join(decision_errors), file=sys.stderr)
        return 1
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
        duplicate = duplicate_verify_error(record.id, record.block_text)
        if duplicate:
            print(duplicate, file=sys.stderr)
            failures += 1
            continue
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
        try:
            package_fp_before, package_inputs_before = package_input_snapshot(package_dir)
            spec_path = package_dir / "spec.md"
            spec_before = read_regular_text(spec_path) if spec_path.exists() else ""
            question_fps_before = autonomous_question_fingerprints(spec_before, tasks_content, record.id)
            decision_fps_before = decision_question_fingerprints(spec_before)
        except (OSError, UnicodeError, ValueError, SpecControlError) as exc:
            print(rule_message("SPEC-EVID-003", f"包内辅助文件不可读：{exc}"), file=sys.stderr)
            failures += 1
            continue
        started = time.monotonic()
        timed_out = False
        try:
            guard = DecisionGuard(root, args.specs_dir or ".spec")
        except (OSError, UnicodeError, ValueError, SpecControlError) as exc:
            print(f"{record.id}：decision 运行保护不可验证：{exc}", file=sys.stderr)
            failures += 1
            continue
        decision_error = None
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
            timed_out = True
            exit_code = ABNORMAL_EXIT
            stdout_bytes = exc.stdout if isinstance(exc.stdout, bytes) else b""
            stderr_text = f"timeout after {args.timeout}s"
        finally:
            try:
                guard.finish()
            except (OSError, UnicodeError, ValueError, SpecControlError) as exc:
                decision_error = str(exc)
        duration = time.monotonic() - started
        # Fingerprint after execution: the record binds to the tree the
        # verification leaves behind, so a command that legitimately formats
        # files keeps its own evidence fresh.
        tree_fp = compute_tree_fingerprint(root, package_dir)
        if tree_fp is None:
            _record_abnormal_attempt(
                package_dir,
                record.id,
                command,
                duration,
                stdout_bytes,
                "",
                package_fp_before,
                package_identity(root, package_dir),
            )
            print(f"{record.id}: 指纹不可计算（非 Git 工作区？），拒绝成功证据", file=sys.stderr)
            failures += 1
            continue
        try:
            package_fp, package_inputs_after = package_input_snapshot(package_dir)
            spec_after = read_regular_text(spec_path) if spec_path.exists() else ""
            question_fps_after = autonomous_question_fingerprints(spec_after, read_regular_text(tasks_path), record.id)
            decision_fps_after = decision_question_fingerprints(spec_after)
        except (OSError, UnicodeError, ValueError, SpecControlError) as exc:
            _record_abnormal_attempt(
                package_dir,
                record.id,
                command,
                duration,
                stdout_bytes,
                tree_fp,
                package_fp_before,
                package_identity(root, package_dir),
            )
            print(rule_message("SPEC-EVID-003", f"包内辅助文件不可读：{exc}"), file=sys.stderr)
            failures += 1
            continue
        if package_fp != package_fp_before:
            _record_abnormal_attempt(
                package_dir,
                record.id,
                command,
                duration,
                stdout_bytes,
                tree_fp,
                package_fp,
                package_identity(root, package_dir),
            )
            changed = sorted(
                name
                for name in package_inputs_before.keys() | package_inputs_after.keys()
                if package_inputs_before.get(name) != package_inputs_after.get(name)
            )
            paths = ", ".join(repr(name) for name in changed[:5])
            if len(changed) > 5:
                paths += f"（另有 {len(changed) - 5} 项）"
            print(
                rule_message(
                    "SPEC-EVID-002",
                    f"{record.id}：验证期间包内辅助文件发生变化：{paths}；请将输出写到包外并重跑",
                ),
                file=sys.stderr,
            )
            failures += 1
            continue
        if decision_error or decision_fps_before != decision_fps_after:
            _record_abnormal_attempt(
                package_dir,
                record.id,
                command,
                duration,
                stdout_bytes,
                tree_fp,
                package_fp,
                package_identity(root, package_dir),
            )
            print(f"{record.id}：验证期间 decision 问题发生变化；授权决策必须在受控验证之外确认", file=sys.stderr)
            failures += 1
            continue
        if question_fps_before != question_fps_after:
            _record_abnormal_attempt(
                package_dir,
                record.id,
                command,
                duration,
                stdout_bytes,
                tree_fp,
                package_fp,
                package_identity(root, package_dir),
            )
            print(f"{record.id}：验证期间问题或结论发生变化；填结论后重跑 verify", file=sys.stderr)
            failures += 1
            continue
        try:
            _record_run(
                package_dir,
                record.id,
                command,
                exit_code,
                duration,
                stdout_bytes,
                tree_fp,
                package_fp,
                package_identity(root, package_dir),
                question_fps_before,
            )
        except (OSError, ValueError) as exc:
            print(rule_message("SPEC-EVID-003", f"无法写入 runs.jsonl：{exc}"), file=sys.stderr)
            failures += 1
            continue
        verdict = "OK" if exit_code == 0 else "FAIL"
        tail = f" exit={exit_code}"
        if timed_out:
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
        failing = {task for task, state in freshness["per_task"].items() if state in {"stale", "unverified"}}
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
    show_parser.add_argument(
        "--history", action="store_true", help="All records in file order instead of latest-per-task"
    )
    show_parser.add_argument(
        "--page-token", default=None, help="Opaque continuation token from a prior envelope's `next`"
    )
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

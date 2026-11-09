#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Execute task verifies and bind their exit codes to one final working tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import gitx
from spec_package_support import (
    SpecControlError,
    add_specs_dir_arg,
    parse_task_records,
    resolve_specs_child,
    resolve_specs_root,
    rule_message,
    utc_now,
    validate_branch_bound_package,
    validate_slug,
)

RUNS_FILENAME = "runs.jsonl"
RUNS_EVIDENCE_MARKER = "evidence: runs"
STDOUT_TAIL_BYTES = 4096
DIAGNOSTIC_TAIL_BYTES = 4096
FINGERPRINT_CHUNK_BYTES = 64 * 1024
ABNORMAL_EXIT = -1
DEFAULT_TIMEOUT_S = 600


@dataclass(frozen=True)
class VerifyClassification:
    kind: str
    command: str = ""
    reason: str = ""


def classify_verify(verify: str) -> VerifyClassification | None:
    value = verify.strip()
    if value.startswith("manual:"):
        reason = value[len("manual:") :].strip()
        return VerifyClassification("manual", reason=reason) if reason else None
    match = re.search(r"`([^`]+)`", value)
    return VerifyClassification("machine", command=match.group(1).strip()) if match else None


def runs_protocol_enabled(spec_content: str) -> bool:
    return RUNS_EVIDENCE_MARKER in spec_content


def compute_tree_fingerprint(root: Path, package_dir: Path | None = None) -> str | None:
    if gitx.toplevel(root) is None:
        return None
    excluded = [".spec"]
    if package_dir is not None:
        try:
            excluded.append(package_dir.relative_to(root).parent.as_posix())
        except ValueError:
            return None
    exclusions = [f":(exclude,literal){name}" for name in excluded if name not in (".", "")]
    head = gitx.head(root)
    hasher = hashlib.sha256(head.encode() + b"\0")
    args = ["git", "-C", str(root), "diff", "--binary", *(["HEAD"] if head else []), "--", ".", *exclusions]
    try:
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=gitx.clean_env()) as process:
            while chunk := process.stdout.read(FINGERPRINT_CHUNK_BYTES):
                hasher.update(chunk)
            if process.wait() != 0:
                return None
        hasher.update(b"\0")
        files = gitx.git(root, "ls-files", "--others", "--exclude-standard", "-z", text=False).stdout
        for raw in sorted(part for part in files.split(b"\0") if part):
            name = os.fsdecode(raw)
            if any(name == item or name.startswith(item + "/") for item in excluded):
                continue
            if Path(name).name == RUNS_FILENAME:
                continue
            path = root / name
            hasher.update(raw + b"\0")
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                hasher.update(os.fsencode(os.readlink(path)))
            elif stat.S_ISREG(mode):
                with path.open("rb") as source:
                    while chunk := source.read(FINGERPRINT_CHUNK_BYTES):
                        hasher.update(chunk)
            else:
                return None
            hasher.update(b"\0")
    except (OSError, subprocess.SubprocessError):
        return None
    return hasher.hexdigest()


def read_runs_records(package_dir: Path) -> list[dict] | None:
    path = package_dir / RUNS_FILENAME
    if not path.exists():
        return None
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{RUNS_FILENAME} 第 {number} 行不是合法 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{RUNS_FILENAME} 第 {number} 行不是 JSON 对象")
        records.append(value)
    return records


def latest_record_by_task(records: list[dict]) -> dict[str, dict]:
    return {record["task"]: record for record in records if isinstance(record.get("task"), str)}


def duplicate_verify_error(task_id: str, block_text: str) -> str | None:
    if len(re.findall(r"(?m)^-\s+verify:", block_text)) > 1:
        return rule_message("SPEC-VERIFY-002", f"{task_id}：verify 必须唯一；合并命令或拆分任务")
    return None


def _machine_tasks(tasks_content: str) -> tuple[list[tuple[str, str]], list[str], int]:
    machine, errors, manual = [], [], 0
    for task in parse_task_records(tasks_content):
        error = duplicate_verify_error(task.id, task.block_text)
        classification = classify_verify(task.verify)
        if error:
            errors.append(error)
        elif classification is None:
            errors.append(f"{task.id}：verify 需要反引号命令或 manual: 理由")
        elif classification.kind == "manual":
            manual += 1
        else:
            machine.append((task.id, classification.command))
    return machine, errors, manual


def evaluate_runs_gate(
    spec_content: str,
    tasks_content: str,
    package_dir: Path | None,
    root: Path | None,
) -> tuple[bool | None, list[str], str]:
    if not runs_protocol_enabled(spec_content) or package_dir is None or root is None:
        return None, [], "未评估运行证据"
    if package_dir.relative_to(root).parts[-3:-1] == ("specs", "archive"):
        return None, [], "已归档（冻结）"
    try:
        machine, errors, manual = _machine_tasks(tasks_content)
        latest = latest_record_by_task(read_runs_records(package_dir) or [])
        fingerprint = compute_tree_fingerprint(root, package_dir)
        passed = 0
        for task_id, command in machine:
            record = latest.get(task_id, {})
            if type(record.get("exit")) is not int or record.get("exit") != 0:
                errors.append(rule_message("SPEC-EVID-001", f"{task_id}：未验证（最近记录 exit={record.get('exit')}）"))
            elif fingerprint is None or record.get("cmd") != command or record.get("tree_fp") != fingerprint:
                errors.append(rule_message("SPEC-EVID-002", f"{task_id}：证据过期，命令或工作树变化，需重跑 --all"))
            else:
                passed += 1
        detail = f"机器任务 {passed}/{len(machine)} exit=0，命令与最终工作树匹配；manual {manual} 项"
        return not errors, errors, detail
    except (OSError, ValueError, SpecControlError) as exc:
        return False, [rule_message("SPEC-EVID-003", str(exc))], "运行证据不可读"


def machine_verification_summary(tasks_content: str, package_dir: Path) -> str | None:
    machine, _errors, manual = _machine_tasks(tasks_content)
    latest = latest_record_by_task(read_runs_records(package_dir) or [])
    passed = sum(latest.get(task, {}).get("exit") == 0 for task, _command in machine)
    return f"{passed}/{len(machine)}；manual 任务 {manual} 项不计入分母" if machine else None


def fresh_machine_evidence(spec_content: str, tasks_content: str, package_dir: Path, root: Path) -> list[str]:
    ok, _errors, _detail = evaluate_runs_gate(spec_content, tasks_content, package_dir, root)
    if ok is not True:
        return []
    machine, _errors, _manual = _machine_tasks(tasks_content)
    return [f"`{command}` -> exit 0 (runs:{task})" for task, command in machine]


def _show_failure_output(task_id: str, stdout: bytes, stderr: bytes) -> None:
    for name, data in (("stdout", stdout), ("stderr", stderr)):
        if not data:
            continue
        tail = data[-DIAGNOSTIC_TAIL_BYTES:]
        safe = "".join(
            char if char in "\n\t" or char.isprintable() else char.encode("unicode_escape").decode("ascii")
            for char in tail.decode("utf-8", "replace")
        )
        truncated = "; truncated" if len(data) > len(tail) else ""
        print(
            f"--- {task_id} {name}: untrusted output, tail {len(tail)}/{len(data)} bytes{truncated} ---",
            file=sys.stderr,
        )
        print(safe, file=sys.stderr)
        print(f"--- end {name} ---", file=sys.stderr)


def _load_package(args: argparse.Namespace) -> tuple[Path, Path, str, str]:
    root = Path(args.root).resolve()
    slug = validate_slug(args.slug)
    package = resolve_specs_child(resolve_specs_root(root, args.specs_dir), "specs", slug)
    spec = (package / "spec.md").read_text(encoding="utf-8")
    tasks = (package / "tasks.md").read_text(encoding="utf-8")
    return root, package, spec, tasks


def run_selected_tasks(args: argparse.Namespace) -> int:
    try:
        root, package, spec, tasks = _load_package(args)
        bound, detail = validate_branch_bound_package(root, args.slug, spec, allow_legacy_convention=True)
        if not bound:
            raise ValueError(detail)
        machine, errors, _manual = _machine_tasks(tasks)
        if not args.all and not args.task:
            raise ValueError("选择 --all 或 --task")
        selected = machine if args.all else [(task, command) for task, command in machine if task in args.task]
        records = parse_task_records(tasks)
        known = {record.id for record in records}
        if set(args.task or ()) - known:
            raise ValueError("未知 task id")
        manual_ids = {
            record.id
            for record in records
            if (classification := classify_verify(record.verify)) is not None and classification.kind == "manual"
        }
        for task_id in args.task or ():
            if task_id in manual_ids:
                print(f"{task_id}: 跳过（manual：由会话按理由人工复核，不写运行记录）")
        fingerprint = compute_tree_fingerprint(root, package)
        if selected and fingerprint is None:
            errors.append("机器运行证据需要可计算的 Git 工作树指纹；未写 runs.jsonl")
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        results = []
        for task, command in selected:
            started = time.monotonic()
            try:
                completed = subprocess.run(command, shell=True, cwd=root, capture_output=True, timeout=args.timeout)
                code, out, err = completed.returncode, completed.stdout, completed.stderr
            except (subprocess.TimeoutExpired, OSError) as exc:
                code = ABNORMAL_EXIT
                out, err = getattr(exc, "stdout", b"") or b"", getattr(exc, "stderr", b"") or b""
            duration = time.monotonic() - started
            if code:
                _show_failure_output(task, out, err)
            results.append(
                {
                    "task": task,
                    "cmd": command,
                    "exit": code,
                    "duration_s": round(duration, 3),
                    "stdout_tail_sha256": hashlib.sha256(out[-STDOUT_TAIL_BYTES:]).hexdigest(),
                    "tree_fp": fingerprint,
                    "package_id": package.relative_to(root).as_posix(),
                    "at": utc_now(),
                }
            )
            print(f"{task}: {'OK' if code == 0 else 'FAIL'} exit={'timeout' if code == -1 else code} ({duration:.2f}s)")
        final = compute_tree_fingerprint(root, package)
        if final != fingerprint:
            errors.append("verify 改变工作树；请在最终树上重跑 --all")
            results = [{**record, "exit": ABNORMAL_EXIT} for record in results]
        with (package / RUNS_FILENAME).open("a", encoding="utf-8") as stream:
            for record in results:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        for error in errors:
            print(error, file=sys.stderr)
        print(f"记录 {RUNS_FILENAME}：{package / RUNS_FILENAME}")
        return int(bool(errors) or any(record["exit"] != 0 for record in results))
    except (OSError, ValueError, SpecControlError) as exc:
        print(rule_message("SPEC-EVID-003", str(exc)), file=sys.stderr)
        return 1


def show_evidence(args: argparse.Namespace) -> int:
    try:
        _root, package, _spec, _tasks = _load_package(args)
        records = read_runs_records(package) or []
        filtered = [record for record in records if not args.task or record.get("task") in args.task]
        for record in filtered[-args.tail :]:
            print(json.dumps(record, ensure_ascii=False))
        return 0
    except (OSError, ValueError, SpecControlError) as exc:
        print(rule_message("SPEC-EVID-003", str(exc)), file=sys.stderr)
        return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for name in ("run", "show"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--root", default=".")
        subparser.add_argument("--slug", required=True)
        add_specs_dir_arg(subparser)
        subparser.add_argument("--task", action="append")
        if name == "run":
            subparser.add_argument("--all", action="store_true")
            subparser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
        else:
            subparser.add_argument("--tail", type=int, default=10)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.operation == "show" and args.tail < 1:
        print("--tail must be positive", file=sys.stderr)
        return 1
    return run_selected_tasks(args) if args.operation == "run" else show_evidence(args)


if __name__ == "__main__":
    sys.exit(main())

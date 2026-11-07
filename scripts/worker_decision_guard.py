"""Invocation-scoped decision integrity, not an actor-authentication boundary."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from safe_open_support import SafeOpenError, create_exclusive, open_regular_read, replace_preserving_mode
from spec_package_support import (
    MAX_RUNTIME_JSON_BYTES,
    SpecControlError,
    decision_question_fingerprints,
    read_json_file,
    read_regular_text,
    resolve_specs_child,
    resolve_specs_root,
    utc_now,
)

try:
    import fcntl
except ImportError:
    fcntl = None

MAX_GUARDS = 128
MAX_PACKAGES = 128
GUARD_DIRECTORY = ".worker-decision-guards"
TERMINATION_GRACE_SECONDS = 2.0


class DecisionGuardError(ValueError):
    pass


def _fallback_guard_directory(root: Path) -> Path:
    directory = root / ".spec" / GUARD_DIRECTORY
    if directory.resolve(strict=False) != directory:
        raise DecisionGuardError("decision guard path must not traverse a symlink")
    return directory


def _guard_directory(root: Path) -> Path:
    git_dir = root / ".git"
    if git_dir.is_symlink():
        raise DecisionGuardError("Git metadata must not be a symlink")
    if git_dir.is_file():
        content = read_regular_text(git_dir).strip()
        if not content.startswith("gitdir: ") or "\n" in content:
            raise DecisionGuardError("invalid Git worktree metadata")
        git_dir = (root / content.removeprefix("gitdir: ")).resolve()
    directory = git_dir / "spec-worker-decisions" if git_dir.is_dir() else _fallback_guard_directory(root)
    if directory.resolve(strict=False) != directory:
        raise DecisionGuardError("decision guard path must not traverse a symlink")
    return directory


def _snapshot(root: Path, specs_dir: str, before: dict | None = None) -> dict[str, dict[str, list[str]]]:
    specs_root = resolve_specs_root(root, specs_dir)
    directory = resolve_specs_child(specs_root, "specs")
    result: dict[str, dict[str, list[str]]] = {}

    def add(path: Path, relative: str) -> None:
        content = read_regular_text(path)
        fingerprints = decision_question_fingerprints(content)
        if fingerprints:
            result[relative] = {
                "all": fingerprints,
                "closed": decision_question_fingerprints(content, closed_only=True),
            }

    packages = (
        [path for path in directory.iterdir() if path.name != "archive" and not path.name.startswith(".")]
        if directory.exists()
        else []
    )
    if len(packages) > MAX_PACKAGES:
        raise DecisionGuardError("too many active packages for decision integrity snapshot")
    for package in sorted(packages):
        path = resolve_specs_child(specs_root, "specs", package.name, "spec.md")
        if not path.exists() and not path.is_symlink():
            continue
        if path.resolve(strict=False) != path:
            raise DecisionGuardError("decision source must not traverse a symlink")
        add(path, path.relative_to(root).as_posix())
    for relative in before or {}:
        path = root / relative
        if path.parent.parent != directory or path.name != "spec.md":
            raise DecisionGuardError("invalid decision snapshot path")
        if path.exists() or path.is_symlink():
            continue
        archived = resolve_specs_child(specs_root, "specs", "archive", path.parent.name, "spec.md")
        if archived.exists() or archived.is_symlink():
            if archived.resolve(strict=False) != archived:
                raise DecisionGuardError("archived decision source must not traverse a symlink")
            add(archived, relative)
    return result


def _changed(root: Path, record: dict) -> bool:
    if record.get("version") != 2 or record.get("status") not in ("running", "abnormal"):
        raise DecisionGuardError("invalid decision guard record")
    before = record.get("before")
    specs_dir = record.get("specs_dir")
    if not isinstance(before, dict) or not isinstance(specs_dir, str):
        raise DecisionGuardError("invalid decision guard snapshot")
    for fingerprint in before.values():
        if not isinstance(fingerprint, dict) or set(fingerprint) != {"all", "closed"}:
            raise DecisionGuardError("invalid decision guard fingerprints")
        for values in fingerprint.values():
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise DecisionGuardError("invalid decision guard fingerprint list")
    after = _snapshot(root, specs_dir, before)
    for relative in before.keys() | after.keys():
        previous = Counter(before.get(relative, {}).get("all", []))
        current = after.get(relative, {})
        if previous - Counter(current.get("all", [])) or Counter(current.get("closed", [])) - previous:
            return True
    return False


def _running(path: Path) -> bool:
    if fcntl is None:
        raise DecisionGuardError("decision guard requires process-held file locks")
    with open_regular_read(path.with_suffix(".lock"), max_bytes=0) as descriptor:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    return False


def decision_guard_errors(root: Path | None) -> list[str]:
    if root is None:
        return []
    root = root.resolve()
    try:
        directories = {_guard_directory(root), _fallback_guard_directory(root)}
        paths = sorted(path for directory in directories if directory.exists() for path in directory.glob("*.json"))
        if len(paths) > MAX_GUARDS:
            raise DecisionGuardError("too many outstanding decision guards")
        errors = []
        for path in paths:
            try:
                record = read_json_file(path)
                if _changed(root, record) or record["status"] == "abnormal":
                    reason = "受控 worker 运行期间 decision 问题发生变化"
                elif not _running(path):
                    reason = "受控 worker 已中断且未完成后置核验"
                else:
                    continue
            except (OSError, UnicodeError, ValueError, SpecControlError, SafeOpenError):
                if not path.exists() and not path.is_symlink():
                    continue
                raise
            errors.append(
                f"{reason}；运行异常，路由阻断：{path.name}；停止相关 worker 后由用户在运行之外核对决策并处理该记录"
            )
        return errors
    except (OSError, UnicodeError, ValueError, SpecControlError, SafeOpenError) as exc:
        return [f"decision 运行保护不可验证：{exc}"]


class DecisionGuard:
    def __init__(self, root: Path, specs_dir: str = ".spec"):
        self.root = root.resolve()
        errors = decision_guard_errors(self.root)
        if errors:
            raise DecisionGuardError("; ".join(errors))
        if fcntl is None:
            raise DecisionGuardError("decision guard requires process-held file locks")
        self.record = {
            "version": 2,
            "status": "running",
            "specs_dir": specs_dir,
            "before": _snapshot(self.root, specs_dir),
            "at": utc_now(),
        }
        self.path = _guard_directory(self.root) / f"{uuid.uuid4().hex}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if _guard_directory(self.root) != self.path.parent:
            raise DecisionGuardError("decision guard directory changed")
        data = (json.dumps(self.record, sort_keys=True) + "\n").encode("utf-8")
        if len(data) > MAX_RUNTIME_JSON_BYTES:
            raise DecisionGuardError("decision snapshot exceeds runtime size limit")
        self.lock_path = self.path.with_suffix(".lock")
        self.lock_fd = create_exclusive(self.lock_path)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            replace_preserving_mode(self.path, data)
        except BaseException:
            self._release_lock()
            raise

    def _release_lock(self) -> None:
        descriptor = self.lock_fd
        if descriptor is not None:
            self.lock_fd = None
            try:
                self.lock_path.unlink(missing_ok=True)
            finally:
                os.close(descriptor)

    def finish(self) -> None:
        try:
            try:
                changed = _changed(self.root, self.record)
            except (OSError, UnicodeError, ValueError, SpecControlError):
                changed = True
            if changed:
                self.record.update(status="abnormal", exit=-1, finished_at=utc_now())
                replace_preserving_mode(self.path, (json.dumps(self.record, sort_keys=True) + "\n").encode("utf-8"))
                raise DecisionGuardError("受控 worker 运行期间 decision 问题发生变化；已记录异常并阻断路由")
            self.path.unlink()
        finally:
            self._release_lock()


@contextmanager
def guarded_decisions(root: Path, specs_dir: str = ".spec") -> Iterator[DecisionGuard]:
    guard = DecisionGuard(root, specs_dir)
    try:
        yield guard
    except BaseException:
        try:
            guard.finish()
        except (OSError, UnicodeError, ValueError, SpecControlError):
            pass
        raise
    else:
        guard.finish()


def worker_argv(root: Path, command: list[str]) -> list[str]:
    return [sys.executable, "-B", str(Path(__file__).resolve()), "--root", str(root), "--", *command]


def run_worker(root: Path, command: list[str]) -> int:
    guard = DecisionGuard(root)
    process = None
    handlers = {}
    termination_deadline = None

    def forward(signum, _frame):
        nonlocal termination_deadline
        if signum == signal.SIGINT:
            return
        if termination_deadline is None:
            termination_deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
        if process is not None and process.poll() is None:
            process.send_signal(signum)

    try:
        process = subprocess.Popen(command, cwd=root)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[signum] = signal.signal(signum, forward)
        while True:
            try:
                returncode = process.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if termination_deadline is not None and time.monotonic() >= termination_deadline:
                    process.kill()
                    returncode = process.wait(timeout=5)
                    break
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        guard.finish()
    return returncode if returncode >= 0 else 128 - returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("worker command is required")
    try:
        return run_worker(Path(args.root).resolve(), command)
    except (OSError, UnicodeError, ValueError, SpecControlError) as exc:
        print(f"decision guard: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

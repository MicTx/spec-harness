#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Deterministic subprocess fan-out driver with pi and codex worker backends.

This repository-owned driver dispatches one-shot workers concurrently from a JSON
plan and collects results in JSONL. The main session owns task decomposition,
assignment contracts, routing, acceptance, and done/push gates. Each worker timeout
or failure affects only its item; output is collected as text, not fabricated structure.

Usage:
  python3 workflow_fanout.py --backend pi --plan plan.json --out results.jsonl
  python3 workflow_fanout.py --backend codex --plan plan.json --out results.jsonl \
      --timeout 20 --concurrency 2
  python3 workflow_fanout.py --backend pi --plan plan.json --out results.jsonl --no-cache

Cache reuse is off by default. A non-secret --cache-context enables successful-item
caching only when the entire Git workspace is verifiably clean. The workspace is
rechecked after each item and at batch end; artifact SHA-256 is checked on each hit.
The caller must attest external inputs; omit the identifier if they cannot be verified.
--no-cache always bypasses cache reads and writes and redispatches every item.

plan.json structure:
  {
    "items": [
      {"id": "rev-1", "prompt": "<complete assignment>", "requiresWrite": false}
    ]
  }
  requiresWrite is optional; true is rejected by the read-only codex backend without escalation.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

BACKENDS = ("pi", "codex")
STDOUT_SUMMARY_CAP = 2000  # JSONL stdout summary limit; full output is stored separately.
MAX_PLAN_ITEMS = 1000
MAX_ID_LENGTH = 128
MAX_PROMPT_LENGTH = 128 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_CONCURRENCY = 64
MAX_TIMEOUT_SECONDS = 24 * 60 * 60


class BatchStopError(OSError):
    """Stop the batch if process-group cleanup or verification fails.

    OSError inheritance preserves existing handlers; this distinct type lets
    run_item and main stop queued dispatch and wait for concurrent items.
    """


def _platform_support_error() -> Optional[str]:
    """Return an execution capability error, or None when supported."""
    required = ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "killpg")
    if not sys.platform.startswith("linux") or not all(hasattr(os, name) for name in required):
        return "verified process-group cleanup requires Linux /proc and waitid(WNOWAIT)"
    if not Path("/proc/self/stat").is_file():
        return "verified process-group cleanup requires readable Linux /proc"
    return None


def _validate_item(item: Any, seen: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("each plan item must be an object")
    item_id = item.get("id")
    prompt = item.get("prompt")
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("item id must be a non-empty string")
    if len(item_id) > MAX_ID_LENGTH or item_id in {".", ".."}:
        raise ValueError("item id is too long or reserved")
    if any(ord(char) < 32 or ord(char) == 127 for char in item_id) or "/" in item_id or "\\" in item_id:
        raise ValueError("item id must be a portable path segment")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"item {item_id!r} has an empty prompt")
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ValueError(f"item {item_id!r} prompt is too long")
    if "requiresWrite" in item and not isinstance(item["requiresWrite"], bool):
        raise ValueError(f"item {item_id!r} requiresWrite must be a boolean")
    if seen is not None:
        if item_id in seen:
            raise ValueError(f"duplicate item id: {item_id}")
        seen.add(item_id)
    return item


def _safe_child(root: Path, name: str) -> Path:
    """Return a generated file path rooted in *root*, rejecting links."""
    if Path(name).name != name or name in {".", ".."}:
        raise ValueError("generated path is not a single path segment")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("generated path contains control characters")
    if root.is_symlink():
        raise ValueError("output directory must not be a symlink")
    root_real = root.resolve()
    candidate = root / name
    if candidate.is_symlink():
        raise ValueError(f"generated path is a symlink: {candidate}")
    if not candidate.resolve(strict=False).is_relative_to(root_real):
        raise ValueError("generated path escapes output directory")
    return candidate


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Signal the known process group while its leader PID is still reserved."""
    try:
        # start_new_session=True makes the leader PID the process-group ID.
        # Never discover the group after wait()/poll() has reaped the leader:
        # its PID could have been reused by an unrelated process group.
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError as exc:
        try:
            process.kill()
        except OSError:
            pass
        raise BatchStopError(f"cannot signal worker process group: {exc}") from exc


def _live_group_members(pgid: int) -> Optional[bool]:
    """Check Linux /proc for non-zombie members; None means not verifiable."""
    try:
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry.name}/stat", "rb") as handle:
                        tail = handle.read().rsplit(b")", 1)[-1].split()
                except (FileNotFoundError, ProcessLookupError):
                    # A proc entry can disappear before open (ENOENT), or its
                    # already-open stat fd can report ESRCH after reaping.
                    # Both mean that particular process no longer exists.
                    continue
                except OSError:
                    return None
                if len(tail) < 3:
                    return None
                try:
                    group = int(tail[2])
                except ValueError:
                    return None
                if group == pgid and tail[0] not in (b"Z", b"X"):
                    return True
    except OSError:
        return None
    return False


def _cleanup_group(process: subprocess.Popen) -> None:
    """Kill and verify same-group workers before reaping the leader."""
    _kill_process_tree(process)
    deadline = time.monotonic() + 2.0
    while True:
        live = _live_group_members(process.pid)
        if live is False:
            return
        if live is None or time.monotonic() >= deadline:
            raise BatchStopError("cannot verify worker process-group cleanup; manual inspection required")
        time.sleep(0.02)


def _run_bounded(cmd: list[str], timeout: float) -> tuple[subprocess.CompletedProcess[str], bool, bool]:
    """Run one worker with bounded output and verified same-group cleanup."""
    # Keep monkeypatched subprocess.run compatibility for isolated unit tests.
    if getattr(subprocess.run, "__module__", "subprocess") != "subprocess":
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return completed, False, False
    support_error = _platform_support_error()
    if support_error is not None:
        raise OSError(support_error)
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    selector.register(process.stdout, selectors.EVENT_READ, stdout)
    selector.register(process.stderr, selectors.EVENT_READ, stderr)
    deadline = time.monotonic() + timeout
    timed_out = False
    output_limited = False
    leader_exited = False
    leader_lost = False
    cleaned = False
    try:
        while True:
            if not leader_exited:
                try:
                    # WNOWAIT observes exit without reaping: the PID remains
                    # reserved until after its process group has been cleaned.
                    event = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                except ChildProcessError as exc:
                    leader_lost = True
                    raise OSError("worker leader was reaped externally; group identity is unknown") from exc
                if event is not None:
                    leader_exited = True
                    _cleanup_group(process)
                    cleaned = True
            if leader_exited and not selector.get_map():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                if not cleaned:
                    _cleanup_group(process)
                    cleaned = True
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = key.data
                room = MAX_OUTPUT_BYTES - len(target)
                if len(chunk) > room:
                    target.extend(chunk[: max(room, 0)])
                    output_limited = True
                    if not cleaned:
                        _cleanup_group(process)
                        cleaned = True
                    break
                target.extend(chunk)
            if output_limited:
                break
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired as exc:
            raise OSError("worker leader did not exit after group cleanup") from exc
    finally:
        if not cleaned and not leader_lost:
            try:
                _kill_process_tree(process)
            except OSError:
                pass
        if not leader_lost and process.returncode is None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        for key in list(selector.get_map().values()):
            selector.unregister(key.fileobj)
        process.stdout.close()
        process.stderr.close()
        selector.close()
    return (
        subprocess.CompletedProcess(
            cmd,
            process.returncode,
            bytes(stdout).decode("utf-8", "replace"),
            bytes(stderr).decode("utf-8", "replace"),
        ),
        timed_out,
        output_limited,
    )


def _open_directory_nofollow(path: Path) -> int:
    """Pin each directory component without following a symlink."""
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise OSError("safe directory-relative file access is unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(path.anchor if path.is_absolute() else ".", flags)
    try:
        parts = path.parts[1:] if path.is_absolute() else path.parts
        for part in parts:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        return directory
    except BaseException:
        os.close(directory)
        raise


def _write_results(path: Path, records: list[dict[str, Any]]) -> None:
    """Atomically replace one directory entry without truncating hardlinks."""
    directory = _open_directory_nofollow(path.parent)
    temporary = f".fanout-{uuid.uuid4().hex}.tmp"
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600, dir_fd=directory)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            existing = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise OSError("result output must be a regular non-symlink file")
        # Replacing the name is atomic and never truncates another hardlink to
        # the old inode; concurrent writers each publish a complete JSONL.
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        created = False
        os.fsync(directory)
    finally:
        if created:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)


def build_command(backend: str, prompt: str, output_file: Optional[Path], model: Optional[str] = None) -> list[str]:
    """Build a one-shot worker command; codex writes via --output-last-message.

    Terminate options with ``--`` so an external prompt beginning with ``-`` is
    not interpreted as an option by either worker CLI.
    """
    flags = ["--model", model] if model is not None else []
    if backend == "pi":
        return ["pi", "-p", "--no-session", "--mode", "text", *flags, "--", prompt]
    if backend == "codex":
        cmd = ["codex", "exec", "--sandbox", "read-only", *flags]
        if output_file is not None:
            cmd.extend(["--output-last-message", str(output_file)])
        return [*cmd, "--", prompt]
    raise ValueError(f"unknown backend: {backend!r}")


def resolve_binary(backend: str) -> Optional[str]:
    """Return the backend executable, or None if it is unavailable."""
    return shutil.which(backend)


def _tracked_fingerprint(root: Path, tracked: str) -> Optional[str]:
    """Hash actual tracked bytes and permissions, without following links.

    Git status/index stat heuristics can miss same-size edits or permission
    changes. Bound the scan; unreadable or unstable inputs disable caching.
    """
    digest = hashlib.sha256()
    total = 0
    try:
        root_fd = _open_directory_nofollow(root)
    except OSError:
        return None
    try:
        for entry in tracked.split("\0"):
            if not entry:
                continue
            fields = entry.split(" ", 3)
            if len(fields) != 4 or fields[0] != "H" or fields[1] not in ("100644", "100755"):
                return None
            stage_and_path = fields[3].split("\t", 1)
            if len(stage_and_path) != 2 or stage_and_path[0] != "0":
                return None
            relative = stage_and_path[1]
            parts = relative.split("/")
            if not relative or relative.startswith("/") or any(part in ("", ".", "..") for part in parts):
                return None
            directory = os.dup(root_fd)
            try:
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    os.close(directory)
                    directory = child
                flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
                descriptor = os.open(parts[-1], flags, dir_fd=directory)
                with os.fdopen(descriptor, "rb") as handle:
                    before = os.fstat(handle.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_size > 128 * 1024 * 1024:
                        return None
                    total += before.st_size
                    if total > 512 * 1024 * 1024:
                        return None
                    header = json.dumps([relative, stat.S_IMODE(before.st_mode), before.st_size], ensure_ascii=True)
                    digest.update(header.encode("ascii") + b"\0")
                    count = 0
                    while chunk := handle.read(1024 * 1024):
                        count += len(chunk)
                        if count > before.st_size:
                            return None
                        digest.update(chunk)
                    after = os.fstat(handle.fileno())
                    before_id = (
                        before.st_dev,
                        before.st_ino,
                        before.st_mode,
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                    )
                    after_id = (
                        after.st_dev,
                        after.st_ino,
                        after.st_mode,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    )
                    if count != before.st_size or before_id != after_id:
                        return None
            finally:
                os.close(directory)
    except (OSError, UnicodeError, ValueError):
        return None
    finally:
        os.close(root_fd)
    return digest.hexdigest()


def _workspace_context(attestation: Optional[str], binary: Optional[str]) -> Optional[str]:
    """Identify a clean Git workspace only with an explicit external-context attestation.

    A caller must account for the worker/model/configuration, external files and
    services; we never inspect credentials or user Git configuration. Unknown,
    dirty, ignored/untracked, sparse, symlinked or submodule state is a miss.
    """
    if not isinstance(attestation, str) or not attestation.strip() or len(attestation) > 512 or not binary:
        return None
    try:
        cwd = Path.cwd().resolve()
        executable = Path(binary).resolve(strict=True)
        if not executable.is_file() or executable.stat().st_size > 128 * 1024 * 1024:
            return None
        with executable.open("rb") as handle:
            worker_sha = hashlib.sha256()
            while chunk := handle.read(1024 * 1024):
                worker_sha.update(chunk)
        driver_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return None
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.devnull,
        "XDG_CONFIG_HOME": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    prefix = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"]

    def git(*args: str) -> Optional[str]:
        try:
            result = subprocess.run(prefix + list(args), cwd=cwd, env=env, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError, UnicodeError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout

    root = git("rev-parse", "--show-toplevel")
    head = git("rev-parse", "--verify", "HEAD")
    status = git("status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching", "--ignore-submodules=none")
    tracked = git("ls-files", "-v", "--stage", "-z")
    if not root or not head or status is None or status or tracked is None:
        return None
    try:
        root_path = Path(root.strip()).resolve(strict=True)
        if not cwd.is_relative_to(root_path):
            return None
    except OSError:
        return None
    actual_files = _tracked_fingerprint(root_path, tracked)
    if actual_files is None:
        return None
    # Reject index/HEAD or untracked-state changes during the scan as well.
    if git("rev-parse", "--verify", "HEAD") != head or git("ls-files", "-v", "--stage", "-z") != tracked:
        return None
    if git("status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching", "--ignore-submodules=none") != "":
        return None
    source = json.dumps(
        [
            str(root_path),
            str(cwd),
            head.strip(),
            actual_files,
            str(executable),
            worker_sha.hexdigest(),
            driver_sha,
            attestation,
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _cache_key(
    backend: str,
    item_id: str,
    prompt: str,
    context: str = "",
    requires_write: bool = False,
    model: Optional[str] = None,
) -> str:
    """Key by attested context and the complete execution-relevant item."""
    source = json.dumps(
        ["v4", backend, item_id, prompt, context, requires_write, model],
        ensure_ascii=False,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _read_bounded_regular(path: Path) -> bytes:
    """Read at most the limit from a pinned, non-symlink regular file.

    Reject special entries before opening them, and open nonblocking so a
    regular-file-to-FIFO replacement between stat and open cannot hang us.
    The descriptor is checked again because the directory entry can change.
    """
    if not hasattr(os, "O_NONBLOCK"):
        raise OSError("nonblocking file access is unavailable")
    directory = _open_directory_nofollow(path.parent)
    try:
        info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_OUTPUT_BYTES:
            raise OSError("file is not a bounded regular file")
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
        fd = os.open(path.name, flags, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_OUTPUT_BYTES:
                raise OSError("file is not a bounded regular file")
            with os.fdopen(fd, "rb") as handle:
                fd = -1  # owned by handle from here
                data = handle.read(MAX_OUTPUT_BYTES + 1)
        finally:
            if fd != -1:
                os.close(fd)
    finally:
        os.close(directory)
    if len(data) > MAX_OUTPUT_BYTES:
        raise OSError("file exceeds the size limit")
    return data


def _read_artifact(out_dir: Path, path: Path) -> bytes:
    """Read one bounded, regular, non-symlink output directly under out_dir."""
    if path.parent.resolve() != out_dir.resolve():
        raise OSError("output artifact is outside the output directory")
    return _read_bounded_regular(path)


def cache_path(out_dir: Path, key: str) -> Path:
    return out_dir / "cache" / f"{key}.json"


def read_cache(out_dir: Path, key: str) -> Optional[dict[str, Any]]:
    """Return a validated successful cache record or None for a miss.

    The associated artifact must be readable and its digest must match; missing,
    corrupt, incomplete, and legacy records are dispatched again.
    """
    path = cache_path(out_dir, key)
    try:
        data = json.loads(_read_bounded_regular(path).decode("utf-8"))
        if not isinstance(data, dict) or data.get("ok") is not True:
            return None
        if not isinstance(data.get("id"), str) or not isinstance(data.get("backend"), str):
            return None
        output = data.get("outputFile")
        digest = data.get("outputSha256")
        if not isinstance(output, str) or not isinstance(digest, str):
            return None
        artifact = _read_artifact(out_dir, Path(output))
        if hashlib.sha256(artifact).hexdigest() != digest:
            return None
    except (OSError, ValueError, UnicodeError):
        return None
    return data


def write_cache(out_dir: Path, key: str, record: dict[str, Any]) -> None:
    """Atomically write a cache record using a unique temporary file.

    Concurrent writers do not share temporary files; the last rename wins and
    each record still refers to its own artifact.
    """
    cache_dir = out_dir / "cache"
    if cache_dir.is_symlink():
        raise OSError("cache directory is a symlink")
    cache_dir.mkdir(parents=True, exist_ok=True)
    final = cache_path(out_dir, key)
    if final.is_symlink():
        raise OSError("cache file is a symlink")
    tmp = final.with_suffix(final.suffix + f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(final)


def run_item(
    backend: str,
    item: dict[str, Any],
    out_dir: Path,
    timeout: float,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Dispatch one item; main checkpoints progress and revalidates before caching."""
    item_id = item.get("id") if isinstance(item, dict) else "item"
    item_id = item_id if isinstance(item_id, str) else "item"
    prompt = item.get("prompt") if isinstance(item, dict) else ""
    prompt = prompt.strip() if isinstance(prompt, str) else ""
    started = time.time()
    if not prompt:
        return {
            "id": item_id,
            "backend": backend,
            "ok": False,
            "error": "empty prompt",
            "durationSec": 0.0,
            "outputFile": "",
            "stdoutSummary": "",
        }
    try:
        _validate_item(item)
        if backend == "codex" and item.get("requiresWrite") is True:
            raise ValueError("codex backend is read-only; item requires workspace writes")
        # Names do not include user ids: Unicode ids can exceed filesystem byte
        # limits even when they satisfy the character-count validation.
        invocation = uuid.uuid4().hex
        full_output = _safe_child(out_dir, f"item-{invocation}.out")
        dump_output = _safe_child(out_dir, f"item-{invocation}.stdout")
    except (OSError, TypeError, ValueError) as exc:
        return {
            "id": item_id,
            "backend": backend,
            "ok": False,
            "error": str(exc),
            "durationSec": 0.0,
            "outputFile": "",
            "stdoutSummary": "",
        }
    # Keep stdout/stderr separate from the codex --output-last-message artifact.
    # The pi backend does not use output_file.

    record: dict[str, Any] = {
        "id": item_id,
        "backend": backend,
        "ok": False,
        "error": None,
        "durationSec": 0.0,
        "outputFile": str(full_output),
        "stdoutSummary": "",
        "model": model,
    }

    try:
        cmd = build_command(backend, prompt, full_output, model)
        completed, timed_out, output_limited = _run_bounded(cmd, timeout)
    except subprocess.TimeoutExpired:
        record["error"] = f"timeout after {timeout}s"
        record["durationSec"] = round(time.time() - started, 2)
        return record
    except BatchStopError:
        # Cleanup signaling or verification failure stops the batch, not just one item.
        raise
    except (OSError, UnicodeError) as exc:
        record["error"] = f"spawn failed: {exc}"
        record["durationSec"] = round(time.time() - started, 2)
        return record

    record["durationSec"] = round(time.time() - started, 2)
    if timed_out:
        record["error"] = f"timeout after {timeout}s"
        return record
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        record["error"] = "subprocess output is not valid text"
        return record
    combined = stdout + ("\n---stderr---\n" + stderr if stderr else "")
    if output_limited or len(combined.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES:
        record["error"] = f"output exceeds {MAX_OUTPUT_BYTES} bytes"
        record["stdoutSummary"] = stdout[:STDOUT_SUMMARY_CAP]
        return record
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(dump_output, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(combined)
    except (OSError, UnicodeError) as exc:
        record["error"] = f"output write failed: {exc}"
        return record
    if backend == "pi":
        # Pi has no --output-last-message file; point to its actual stdout/stderr artifact.
        record["outputFile"] = str(dump_output)
    record["stdoutSummary"] = stdout[:STDOUT_SUMMARY_CAP]
    record["returncode"] = completed.returncode

    if completed.returncode != 0:
        record["error"] = f"exit {completed.returncode}: {stderr[:300]}"
        return record

    try:
        # Codex must create this invocation's final-message file; noisy stdout
        # is not a final answer and a prior invocation's file is never reused.
        artifact = _read_artifact(out_dir, Path(record["outputFile"]))
        text_target = stdout if backend == "pi" else artifact.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        record["error"] = f"output read failed: {exc}"
        return record
    if not text_target.strip():
        record["error"] = "empty output"
        return record

    record["outputSha256"] = hashlib.sha256(artifact).hexdigest()
    record["ok"] = True
    return record


def load_plan(plan_path: Path) -> list[dict[str, Any]]:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid plan: {exc}") from exc
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("plan must contain a non-empty 'items' array")
    if len(items) > MAX_PLAN_ITEMS:
        raise ValueError(f"plan contains more than {MAX_PLAN_ITEMS} items")
    seen: set[str] = set()
    for item in items:
        _validate_item(item, seen)
    return items


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="workflow_fanout.py", description="deterministic subprocess fan-out driver")
    parser.add_argument("--backend", required=True, choices=BACKENDS)
    parser.add_argument(
        "--model", default=None, help="explicit model ID; Codex requires a top-tier local catalog model"
    )
    parser.add_argument("--plan", required=True, help="JSON task plan (items + prompt)")
    parser.add_argument("--out", required=True, help="JSONL result output path")
    parser.add_argument("--out-dir", default=None, help="full output directory (default: <out>.d/)")
    parser.add_argument("--concurrency", type=int, default=3, help="concurrency limit (default: 3)")
    parser.add_argument("--timeout", type=float, default=300.0, help="hard timeout per item in seconds (default: 300)")
    parser.add_argument(
        "--cache-context",
        default=None,
        help="non-secret external context identifier; caching also requires a verifiably clean Git workspace",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="bypass cache reads and writes and dispatch every item",
    )
    args = parser.parse_args(argv)

    if args.concurrency < 1 or args.concurrency > MAX_CONCURRENCY:
        print(f"[fanout] invalid concurrency: 1..{MAX_CONCURRENCY}", file=sys.stderr)
        return 2
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeout > MAX_TIMEOUT_SECONDS:
        print(f"[fanout] invalid timeout: 0..{MAX_TIMEOUT_SECONDS}s", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_dir = Path(args.out_dir) if args.out_dir else out_path.with_suffix(out_path.suffix + ".d")
    try:
        if _has_symlink_component(out_path) or _has_symlink_component(out_dir):
            raise ValueError("output path must not be a symlink")
        out_dir.mkdir(parents=True, exist_ok=True)
        if not out_dir.is_dir():
            raise ValueError("output directory is not a directory")
        cache_dir = out_dir / "cache"
        if cache_dir.is_symlink():
            raise ValueError("cache directory must not be a symlink")
        if out_path.exists() and not out_path.is_file():
            raise ValueError("result output must be a regular file")
        if args.out_dir is not None and not out_path.resolve(strict=False).is_relative_to(out_dir.resolve()):
            raise ValueError("result output must be inside --out-dir")
        items = load_plan(Path(args.plan))
    except (OSError, ValueError) as exc:
        print(f"[fanout] invalid input: {exc}", file=sys.stderr)
        return 2

    # Unknown external context, worker identity or workspace is never cacheable.
    binary = resolve_binary(args.backend)
    context = None if args.no_cache else _workspace_context(args.cache_context, binary)
    if args.cache_context and not args.no_cache and context is None:
        print("[fanout] cache disabled: workspace/context cannot be verified", file=sys.stderr)
    records: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for item in items:
        item_id = item["id"]
        prompt = item["prompt"].strip()
        requires_write = item.get("requiresWrite", False)
        cached = None
        if context is not None and not (args.backend == "codex" and requires_write):
            cached = read_cache(out_dir, _cache_key(args.backend, item_id, prompt, context, requires_write, args.model))
        if cached is not None and cached.get("id") == item_id and cached.get("backend") == args.backend:
            record = dict(cached)
            record["cached"] = True
            records.append(record)
        else:
            pending.append(item)

    attempt_id = uuid.uuid4().hex
    by_id = {item["id"]: item for item in items}
    order = {item["id"]: index for index, item in enumerate(items)}

    def publish(*, complete=False):
        # A checkpoint is an observation of THIS attempt, never a restart
        # authorization. Only read_cache + its strict context can skip work.
        for record in records:
            item = by_id[record["id"]]
            if record.get("cached") and record.get("attemptId") != attempt_id:
                record["sourceAttemptId"] = record.get("attemptId")
            record["attemptId"] = attempt_id
            record["batchComplete"] = complete
            record["status"] = (
                "cached" if record.get("cached") and record["ok"] else ("succeeded" if record["ok"] else "failed")
            )
            record["inputKey"] = hashlib.sha256(
                json.dumps(
                    [
                        args.backend,
                        item["id"],
                        item["prompt"].strip(),
                        item.get("requiresWrite", False),
                        args.model,
                        context,
                    ],
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            record.setdefault("outputSha256", None)
        records.sort(key=lambda record: order[record["id"]])
        _write_results(out_path, records)

    def cache_completed(record):
        if context is None or not record["ok"] or record.get("cached"):
            return
        if _workspace_context(args.cache_context, resolve_binary(args.backend)) != context:
            return
        item = by_id[record["id"]]
        key = _cache_key(
            args.backend, item["id"], item["prompt"].strip(), context, item.get("requiresWrite", False), args.model
        )
        try:
            write_cache(out_dir, key, record)
        except OSError:
            record["cacheWriteError"] = "cache write failed"

    # Clear a previous attempt before any new workers start. This is not a
    # lease and must never be used to take over an unknown live worker.
    try:
        publish()
    except (OSError, UnicodeError) as exc:
        print(f"[fanout] checkpoint write failed: {exc}", file=sys.stderr)
        return 2
    checkpoint_error = False
    backend_missing = binary is None and bool(pending)
    batch_error: Optional[BatchStopError] = None
    if backend_missing:
        records.extend(
            {
                "id": it["id"],
                "backend": args.backend,
                "ok": False,
                "error": f"backend binary not found: {args.backend}",
                "durationSec": 0.0,
                "outputFile": "",
                "stdoutSummary": "",
            }
            for it in pending
        )
        print(f"[fanout] backend missing: {args.backend}; {len(pending)} items recorded as failed")
    elif pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {
                pool.submit(run_item, args.backend, item, out_dir, args.timeout, args.model): item for item in pending
            }
            cancelled: set[concurrent.futures.Future[dict[str, Any]]] = set()
            for future in concurrent.futures.as_completed(futures):
                if future in cancelled:
                    continue  # Already recorded as unstarted.
                item = futures[future]
                try:
                    result = future.result()
                except BatchStopError as exc:
                    # Stop unstarted items on batch-level failure; the pool waits for active items.
                    result = {
                        "id": item["id"],
                        "backend": args.backend,
                        "ok": False,
                        "error": f"batch stopped: {exc}",
                        "durationSec": 0.0,
                        "outputFile": "",
                        "stdoutSummary": "",
                    }
                    if batch_error is None:
                        batch_error = exc
                        for other, other_item in futures.items():
                            if other is not future and other.cancel():
                                cancelled.add(other)
                                records.append(
                                    {
                                        "id": other_item["id"],
                                        "backend": args.backend,
                                        "ok": False,
                                        "error": f"not started: batch stopped ({exc})",
                                        "durationSec": 0.0,
                                        "outputFile": "",
                                        "stdoutSummary": "",
                                    }
                                )
                records.append(result)
                try:
                    publish()
                    cache_completed(result)
                except (OSError, UnicodeError) as exc:
                    checkpoint_error = True
                    print(f"[fanout] checkpoint write failed: {exc}", file=sys.stderr)
                    # Cancel only queued work owned by this pool, never recover
                    # or kill workers left over from a different invocation.
                    for other, other_item in futures.items():
                        if other not in cancelled and other.cancel():
                            cancelled.add(other)
                            records.append(
                                {
                                    "id": other_item["id"],
                                    "backend": args.backend,
                                    "ok": False,
                                    "error": "not started: checkpoint write failed",
                                    "durationSec": 0.0,
                                    "outputFile": "",
                                    "stdoutSummary": "",
                                }
                            )
    if batch_error is not None:
        print(f"[fanout] batch stopped: {batch_error}", file=sys.stderr)

    # Work can change while a batch is running. Never return an old cached
    # success or save a new cache entry when the original snapshot is gone.
    if context is not None:
        stable = _workspace_context(args.cache_context, resolve_binary(args.backend)) == context
        if not stable:
            for record in records:
                if record.get("cached"):
                    record["ok"] = False
                    record["error"] = "workspace context changed during execution; rerun"
            print("[fanout] workspace context changed; cached results are invalid", file=sys.stderr)

    try:
        publish(complete=True)
    except (OSError, UnicodeError) as exc:
        print(f"[fanout] result write failed: {exc}", file=sys.stderr)
        return 2

    ok_count = sum(1 for r in records if r["ok"])
    cached_count = sum(1 for r in records if r.get("cached"))
    print(
        f"[fanout] backend={args.backend} items={len(records)} "
        f"ok={ok_count} fail={len(records) - ok_count} cached={cached_count}"
    )
    print(f"[fanout] results: {out_path}")
    return 1 if backend_missing else (0 if ok_count == len(records) and not checkpoint_error else 2)


if __name__ == "__main__":
    sys.exit(main())

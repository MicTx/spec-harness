# scripts/safe_open_support.py
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""Shared safe-open primitives for runtime and archive file reads.

Single definition point for the O_NOFOLLOW/O_NONBLOCK open + fstat
regular-file verification contract used across spec scripts. Read
primitives fail closed when the platform lacks O_NOFOLLOW; create/append
primitives rely on O_EXCL/O_APPEND as their primary contract and treat
O_NOFOLLOW as a defensive enhancement (see per-function docs).
"""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SafeOpenError(Exception):
    """Raised when a path cannot be opened under the safe-open contract.

    ``reason`` is one of:
      - ``"missing"``: the path does not exist;
      - ``"unsupported"``: the platform lacks O_NOFOLLOW for safe reads;
      - ``"untrusted"``: symlink, non-regular file, or size over limit.
    """

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


def _read_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise SafeOpenError("platform lacks O_NOFOLLOW support for safe reads", reason="unsupported")
    return os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0)


@contextmanager
def open_regular_read(path: Path, *, max_bytes: int | None) -> Iterator[int]:
    """Yield an fd for a regular file opened no-follow, non-blocking.

    Raises SafeOpenError for symlinks (ELOOP), non-regular files, missing
    files, and files larger than max_bytes (when given). The context owns the fd
    throughout; callers borrow it and must not close it.
    Caller owns nothing on error.
    """
    try:
        descriptor = os.open(path, _read_flags())
    except FileNotFoundError as exc:
        raise SafeOpenError(f"required file missing: {path}", reason="missing") from exc
    except OSError as exc:
        # ELOOP (symlink refusal), EINVAL and friends surface as open errors.
        raise SafeOpenError(f"cannot safely open {path}: {exc}", reason="untrusted") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SafeOpenError(f"path is not a regular file: {path}", reason="untrusted")
        if max_bytes is not None and metadata.st_size > max_bytes:
            raise SafeOpenError(f"file exceeds {max_bytes} bytes: {path}", reason="untrusted")
        # The context owns this descriptor. fdopen users must pass closefd=False;
        # probing a possibly reused fd after a caller closes it is not ownership.
        yield descriptor
    finally:
        os.close(descriptor)


def safe_mtime(path: Path) -> float | None:
    """Trusted mtime of a regular file, or None when trust is impossible.

    Fail-closed: platforms without O_NOFOLLOW yield None rather than
    following a symlink; symlinks, directories, FIFOs and other
    non-regular paths yield None; open never blocks (O_NONBLOCK).
    """
    try:
        with open_regular_read(path, max_bytes=None) as descriptor:
            return os.fstat(descriptor).st_mtime
    except SafeOpenError:
        return None


def create_exclusive(path: Path, *, mode: int = 0o600) -> int:
    """Create a new file owned by this call; never follow an existing path.

    O_CREAT | O_EXCL is the primary protection: an existing path (symlink
    included) fails with FileExistsError instead of being followed or
    truncated. O_NOFOLLOW is a defensive enhancement that degrades
    silently on platforms lacking the flag; the exclusive-create contract
    itself holds everywhere.

    Raises the underlying FileExistsError when the target already exists
    so callers keep their idempotency/retry semantics.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags, mode)


def open_append_private(path: Path, *, mode: int = 0o600) -> int:
    """Append-only descriptor for a regular log file, created 0o600.

    O_APPEND | O_CREAT cannot truncate, so an existing file keeps its
    bytes. On POSIX an existing symlink is refused by O_NOFOLLOW; where
    the flag is unavailable this degrades to opening the link target —
    acceptable for append-only logs whose directory the process already
    owns (0o700 private dirs), and documented here so callers know the
    boundary. A pre-existing regular file keeps its current permissions.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags, mode)

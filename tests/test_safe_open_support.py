# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""Contract tests for the shared safe-open primitives."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from safe_open_support import (  # noqa: E402
    SafeOpenError,
    create_exclusive,
    open_append_private,
    open_regular_read,
    safe_mtime,
)


def test_open_regular_read_yields_readable_fd(tmp_path):
    target = tmp_path / "data.json"
    target.write_text("{}", encoding="utf-8")
    with open_regular_read(target, max_bytes=1024) as descriptor:
        assert os.read(descriptor, 16) == b"{}"


def test_open_regular_read_rejects_symlink(tmp_path):
    target = tmp_path / "real.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(SafeOpenError):
        with open_regular_read(link, max_bytes=1024):
            pass


def test_open_regular_read_rejects_directory(tmp_path):
    directory = tmp_path / "dir"
    directory.mkdir()
    with pytest.raises(SafeOpenError):
        with open_regular_read(directory, max_bytes=1024):
            pass


def test_open_regular_read_rejects_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "pipe"
    try:
        os.mkfifo(fifo)
    except (OSError, AttributeError, NotImplementedError):
        pytest.skip("FIFOs unavailable")
    result: dict = {}

    def probe():
        try:
            with open_regular_read(fifo, max_bytes=1024) as descriptor:
                result["fd"] = descriptor
        except SafeOpenError as exc:
            result["error"] = str(exc)

    worker = threading.Thread(target=probe, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "open_regular_read blocked on FIFO"
    assert "error" in result and "fd" not in result


def test_open_regular_read_rejects_oversize_file(tmp_path):
    target = tmp_path / "big.bin"
    target.write_bytes(b"x" * 16)
    with pytest.raises(SafeOpenError):
        with open_regular_read(target, max_bytes=8):
            pass


def test_open_regular_read_rejects_missing_file(tmp_path):
    with pytest.raises(SafeOpenError):
        with open_regular_read(tmp_path / "absent.json", max_bytes=8):
            pass


def test_open_regular_read_fails_closed_without_nofollow(monkeypatch, tmp_path):
    target = tmp_path / "data.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.delattr(os, "O_NOFOLLOW")
    with pytest.raises(SafeOpenError) as excinfo:
        with open_regular_read(target, max_bytes=8):
            pass
    assert excinfo.value.reason == "unsupported"


def test_safe_open_error_reasons_are_structured(tmp_path):
    missing = tmp_path / "absent.json"
    with pytest.raises(SafeOpenError) as excinfo:
        with open_regular_read(missing, max_bytes=8):
            pass
    assert excinfo.value.reason == "missing"

    directory = tmp_path / "dir"
    directory.mkdir()
    with pytest.raises(SafeOpenError) as excinfo:
        with open_regular_read(directory, max_bytes=8):
            pass
    assert excinfo.value.reason == "untrusted"

    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 16)
    with pytest.raises(SafeOpenError) as excinfo:
        with open_regular_read(big, max_bytes=8):
            pass
    assert excinfo.value.reason == "untrusted"


def test_safe_mtime_returns_regular_file_mtime(tmp_path):
    target = tmp_path / "regular.json"
    target.write_text("{}", encoding="utf-8")
    assert safe_mtime(target) == target.stat().st_mtime


def test_safe_mtime_returns_none_for_symlink_directory_fifo_and_capacity(tmp_path, monkeypatch):
    directory = tmp_path / "dir"
    directory.mkdir()
    assert safe_mtime(directory) is None

    fifo = tmp_path / "pipe"
    try:
        os.mkfifo(fifo)
    except (OSError, AttributeError, NotImplementedError):
        pytest.skip("FIFOs unavailable")
    assert safe_mtime(fifo) is None

    target = tmp_path / "real.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert safe_mtime(link) is None

    monkeypatch.delattr(os, "O_NOFOLLOW")
    assert safe_mtime(target) is None


def test_create_exclusive_creates_once_and_refuses_symlink(tmp_path):
    target = tmp_path / "card.json"
    first = create_exclusive(target)
    os.close(first)
    with pytest.raises(FileExistsError):
        create_exclusive(target)

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(OSError):
        create_exclusive(link)


def test_open_append_private_appends_and_creates_private(tmp_path):
    log = tmp_path / "run.log"
    descriptor = open_append_private(log)
    os.write(descriptor, b"first\n")
    os.close(descriptor)
    assert (log.stat().st_mode & 0o777) == 0o600

    second = open_append_private(log)
    os.write(second, b"second\n")
    os.close(second)
    assert log.read_text(encoding="utf-8") == "first\nsecond\n"

    outside = tmp_path / "outside.log"
    outside.write_text("", encoding="utf-8")
    link = tmp_path / "link.log"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(OSError):
        open_append_private(link)


def test_borrowed_fd_stays_owned_until_context_exit(tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("data")
    with open_regular_read(target, max_bytes=1024) as descriptor:
        with os.fdopen(descriptor, "r", closefd=False) as stream:
            assert stream.read() == "data"
        # No gap in fd ownership: another open cannot reuse this live number.
        other = os.open(target, os.O_RDONLY)
        try:
            assert other != descriptor
            assert os.fstat(descriptor).st_size == 4
        finally:
            os.close(other)
    with pytest.raises(OSError):
        os.fstat(descriptor)


@pytest.mark.parametrize("reader", ["json", "text", "archive"])
def test_production_readers_never_close_a_borrowed_descriptor(tmp_path, monkeypatch, reader):
    from contextlib import contextmanager

    from check_all_spec_packages import _read_archive_file_bytes
    from spec_package_support import read_json_file, read_regular_text

    target = tmp_path / "data.json"
    target.write_text('{"ok":true}')
    original = os.fdopen
    checked = []

    @contextmanager
    def borrowed(fd, *args, **kwargs):
        assert kwargs.get("closefd") is False
        with original(fd, *args, **kwargs) as handle:
            yield handle
        # The original defect closed here; any parallel opener could then
        # acquire this number and be accidentally closed by safe-open finally.
        os.fstat(fd)
        checked.append(fd)

    monkeypatch.setattr(os, "fdopen", borrowed)
    {"json": read_json_file, "text": read_regular_text, "archive": _read_archive_file_bytes}[reader](target)
    assert len(checked) == 1

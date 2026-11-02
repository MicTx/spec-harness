"""Offline regressions for races in Linux /proc group verification."""

from __future__ import annotations

import builtins
import errno
import io
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
import workflow_fanout as wf

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not os.path.isfile("/proc/self/stat"),
    reason="requires Linux /proc process stat entries",
)


@pytest.mark.parametrize("later_live", [False, True])
def test_reaped_proc_stat_fd_reports_esrch_and_scan_continues(monkeypatch, later_live):
    # Open a real /proc stat fd, then deterministically reap just our own child
    # before the scanner reads it. Linux reports ESRCH, not ENOENT, on that fd.
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    target = f"/proc/{child.pid}/stat"
    actual_errors = []
    real_open = builtins.open

    class ReapedReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def read(self):
            child.terminate()
            child.wait(timeout=3)
            try:
                return self.handle.read()
            except OSError as exc:
                actual_errors.append((type(exc), exc.errno))
                raise

    def racing_open(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        return ReapedReader(handle) if str(path) == target else handle

    class OrderedProcEntries:
        def __enter__(self):
            # Force the vanished entry before this still-live process so the
            # test also proves scanning does not stop on ESRCH.
            return iter([SimpleNamespace(name=str(child.pid)), SimpleNamespace(name=str(os.getpid()))])

        def __exit__(self, *args):
            return None

    real_scandir = os.scandir
    monkeypatch.setattr(wf, "open", racing_open, raising=False)
    monkeypatch.setattr(wf.os, "scandir", lambda path: OrderedProcEntries() if path == "/proc" else real_scandir(path))
    try:
        # -1 is not a real group; the second case is our own live group.
        pgid = os.getpgrp() if later_live else -1
        assert wf._live_group_members(pgid) is later_live
        assert actual_errors == [(ProcessLookupError, errno.ESRCH)]
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)


@pytest.mark.parametrize("damage", ["permission", "io", "malformed"])
def test_unverifiable_proc_data_still_fails_closed(monkeypatch, damage):
    target = f"/proc/{os.getpid()}/stat"
    real_open = builtins.open
    seen = []
    signalled = []

    def broken_open(path, *args, **kwargs):
        if str(path) != target:
            return real_open(path, *args, **kwargs)
        seen.append(damage)
        if damage == "permission":
            raise PermissionError(errno.EACCES, "permission denied", target)
        if damage == "io":
            raise OSError(errno.EIO, "I/O error", target)
        return io.BytesIO(b"malformed stat")

    monkeypatch.setattr(wf, "open", broken_open, raising=False)
    monkeypatch.setattr(wf, "_kill_process_tree", lambda process: signalled.append(process.pid))
    with pytest.raises(OSError, match="cannot verify worker process-group cleanup"):
        wf._cleanup_group(SimpleNamespace(pid=-1))
    assert seen == [damage]
    assert signalled == [-1]


def test_live_group_member_is_still_detected():
    assert wf._live_group_members(os.getpgrp()) is True


def test_cleanup_still_fails_if_group_remains_live(monkeypatch):
    ticks = iter([0.0, 0.1, 2.1])
    monkeypatch.setattr(wf, "_kill_process_tree", lambda process: None)
    monkeypatch.setattr(wf, "_live_group_members", lambda pgid: True)
    monkeypatch.setattr(wf, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda delay: None))
    with pytest.raises(OSError, match="cannot verify worker process-group cleanup"):
        wf._cleanup_group(SimpleNamespace(pid=-1))

"""Offline regressions for the continuation-workflow-fix assignment.

Covers four independent defects fixed in workflow_fanout.py:

1. stdin leak: workers must be spawned with stdin=subprocess.DEVNULL so they
   never inherit the caller's stdin.
2. killpg/verify failure must be a batch-level safe stop (BatchStopError),
   not a per-item spawn failure that lets the thread pool keep dispatching.
3. Honest boundary: a start_new_session worker survives an abnormal driver exit.
4. Linux-only execution surface: non-Linux / missing waitid(WNOWAIT) /
   unreadable /proc must fail before any worker spawns.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import workflow_fanout as wf


def _process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            tail = handle.read().rsplit(b")", 1)[-1].split()
    except OSError:
        return True
    return bool(tail) and tail[0] != b"Z"


# ---------------------------------------------------------------------------
# 1. stdin leak
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="verified cleanup requires Linux")
def test_worker_stdin_is_devnull_not_inherited(tmp_path):
    marker = tmp_path / "stdin_eof"
    worker = f"import sys;data = sys.stdin.buffer.read();open(r'{marker}', 'w').write(str(len(data)))"
    completed, timed_out, limited = wf._run_bounded([sys.executable, "-c", worker], 10)
    assert not timed_out and not limited
    assert completed.returncode == 0, completed.stderr
    assert marker.read_text().strip() == "0", "worker stdin must be DEVNULL (immediate EOF)"


# ---------------------------------------------------------------------------
# 2. killpg permission failure -> batch-level safe stop
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="verified cleanup requires Linux")
def test_killpg_permission_failure_stops_batch_and_blocks_unstarted(tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"items": [{"id": f"i{n}", "prompt": f"task {n}"} for n in range(5)]}))
    out = tmp_path / "result.jsonl"

    started_ids = []
    max_concurrent = {"value": 0}
    current = {"value": 0}

    def fail_on_first(cmd, timeout):
        started_ids.append(cmd[-1])
        current["value"] += 1
        max_concurrent["value"] = max(max_concurrent["value"], current["value"])
        try:
            raise wf.BatchStopError("cannot signal worker process group: [Errno 1] Operation not permitted")
        finally:
            current["value"] -= 1

    monkeypatch.setattr(wf, "_run_bounded", fail_on_first)
    monkeypatch.setattr(wf, "resolve_binary", lambda backend: sys.executable)
    monkeypatch.setattr(
        wf,
        "build_command",
        lambda backend, prompt, output, model=None: [sys.executable, "-c", "", prompt],
    )

    code = wf.main(["--backend", "pi", "--plan", str(plan), "--out", str(out), "--concurrency", "2"])

    assert code == 2
    records = {r["id"]: r for r in (json.loads(line) for line in out.read_text().splitlines())}
    assert len(records) == 5
    not_started = {rid for rid, r in records.items() if r["error"].startswith("not started: batch stopped")}
    batch_stopped = {rid for rid, r in records.items() if r["error"].startswith("batch stopped:")}
    assert batch_stopped, "at least one item must be marked batch-stopped"
    assert len(not_started) + len(batch_stopped) == 5
    assert all(not r["ok"] for r in records.values())
    # Physical concurrency must never exceed the cap.
    assert max_concurrent["value"] <= 2, max_concurrent["value"]
    # The core invariant: an item recorded "not started" never actually dispatched
    # a worker (its _run_bounded was never called); a "batch stopped" item did.
    # started_ids holds prompts ("task N"); map back to record ids ("iN").
    started_record_ids = {f"i{p.split()[-1]}" for p in started_ids}
    assert not_started.isdisjoint(started_record_ids), "a 'not started' item must never run _run_bounded"
    assert batch_stopped <= started_record_ids, "a 'batch stopped' item must have actually started"


# ---------------------------------------------------------------------------
# 3. driver exit (SIGTERM) leaves start_new_session worker alive
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="session semantics require Linux")
def test_start_new_session_worker_survives_driver_sigterm(tmp_path):
    worker_pid_file = tmp_path / "worker.pid"
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import subprocess, sys, time\n"
        "worker = [sys.executable, '-c', \"import time; time.sleep(60)\"]\n"
        "p = subprocess.Popen(worker, stdin=subprocess.DEVNULL,\n"
        "                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "                     start_new_session=True)\n"
        f"open(r'{worker_pid_file}', 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen([sys.executable, str(driver)])
    worker_pid = None
    try:
        deadline = time.monotonic() + 5
        while not worker_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert worker_pid_file.exists(), "driver did not spawn and record the worker"
        worker_pid = int(worker_pid_file.read_text().strip())

        assert os.getsid(worker_pid) != os.getsid(proc.pid), "worker must be in its own session"

        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        time.sleep(0.2)
        assert _process_alive(worker_pid), "start_new_session worker must survive driver SIGTERM (documented boundary)"
    finally:
        if worker_pid is not None and _process_alive(worker_pid):
            try:
                os.kill(worker_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# 4. platform capability missing -> fail before worker spawn
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mutate", ["non_linux", "missing_waitid", "unreadable_proc"])
def test_platform_capability_missing_fails_before_spawn(monkeypatch, mutate):
    """Non-Linux / missing waitid(WNOWAIT) / unreadable /proc fail before Popen."""
    monkeypatch.setattr(
        wf.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("must not launch a worker without Linux cleanup support"),
    )
    if mutate == "non_linux":
        monkeypatch.setattr(wf.sys, "platform", "win32")
    elif mutate == "missing_waitid":
        # Remove a real capability so _platform_support_error's hasattr check fails.
        monkeypatch.delattr(wf.os, "waitid", raising=False)
    elif mutate == "unreadable_proc":
        # Make the /proc self-stat probe report missing without touching the real fs.
        real_is_file = Path.is_file

        def unreadable_proc(self):
            if str(self) == "/proc/self/stat":
                return False
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", unreadable_proc)
    with pytest.raises(OSError, match="verified process-group cleanup"):
        wf._run_bounded(["fake"], 1)

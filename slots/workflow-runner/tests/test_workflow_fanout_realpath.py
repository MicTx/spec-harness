#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""生产 selector 循环的真实路径测试。

不 monkeypatch ``subprocess.run``：这些用例驱动真实子进程，走
``_run_bounded`` 的生产有界读取分支（兼容 shim 之外的代码路径），
覆盖输出上限判定、进程组清理、超时与 ``--`` 分隔符。
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest
import workflow_fanout as wf

# This file drives the real driver, not the subprocess.run compatibility shim.
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="real driver execution requires Linux /proc and waitid(WNOWAIT)",
)


def _emitter(tmp_path: Path, name: str, body: str) -> Path:
    stub = tmp_path / name
    stub.write_text("#!/bin/sh\n" + body + "\n")
    stub.chmod(0o755)
    return stub


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # The signal probe also answers for unreaped zombies: when the whole
    # process group is SIGKILLed together, the grandchild's parent is gone
    # and a container PID 1 that never reaps leaves the Z entry in place,
    # so CI would report the kill as failed. The contract under test is
    # "the grandchild is terminated", which a zombie already satisfies.
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            tail = handle.read().rsplit(b")", 1)[-1].split()
    except OSError:
        return True  # non-Linux (e.g. macOS): keep the plain signal probe
    return bool(tail) and tail[0] != b"Z"


def test_real_path_output_under_cap_near_limit_not_flagged(tmp_path, monkeypatch):
    """回归（issue-1）：总输出落在 (cap-64K, cap] 的对齐窗口不得误判超限。"""
    monkeypatch.setattr(wf, "MAX_OUTPUT_BYTES", 200_000)
    total = 200_000 - 30_000  # cap-30K，处于旧实现的假阳性窗口
    stub = _emitter(tmp_path, "near_cap.sh", f"python3 -c \"import sys; sys.stdout.write('x'*{total})\"")
    completed, timed_out, output_limited = wf._run_bounded([str(stub)], 30)
    assert len(completed.stdout) == total
    assert total <= wf.MAX_OUTPUT_BYTES
    assert output_limited is False, "未超上限的输出被误判超限（假阳性回归）"
    assert timed_out is False
    assert completed.returncode == 0


def test_real_path_output_over_cap_is_limited_and_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "MAX_OUTPUT_BYTES", 100_000)
    stub = _emitter(tmp_path, "over_cap.sh", "python3 -c \"import sys; sys.stdout.write('x'*400000)\"")
    completed, timed_out, output_limited = wf._run_bounded([str(stub)], 30)
    assert output_limited is True
    assert timed_out is False
    assert len(completed.stdout.encode()) <= wf.MAX_OUTPUT_BYTES
    assert completed.returncode != 0


def test_real_path_timeout_kills_process_group_including_grandchild(tmp_path):
    """issue-11：超时必须终止整个进程组，孙进程不得存活。"""
    grandchild_pid_file = tmp_path / "grandchild.pid"
    marker = tmp_path / "still_alive"
    stub = tmp_path / "spawner.sh"
    stub.write_text(
        "#!/bin/sh\n"
        "python3 -c \"import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); "
        '[time.sleep(0.2) for _ in iter(int,1)]" '
        f'"{grandchild_pid_file}" &\n'
        "echo spawned\n"
        "while true; do sleep 0.2; done\n"
    )
    stub.chmod(0o755)
    start = time.monotonic()
    completed, timed_out, output_limited = wf._run_bounded([str(stub)], 1.5)
    assert timed_out is True
    assert time.monotonic() - start < 10
    assert "spawned" in completed.stdout
    grandchild_pid = int(grandchild_pid_file.read_text().strip())
    deadline = time.monotonic() + 5
    while _process_alive(grandchild_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _process_alive(grandchild_pid), "孙进程在超时清理后仍存活（进程组 kill 失效）"
    assert not marker.exists()


def test_real_path_normal_completion_captures_output(tmp_path):
    stub = _emitter(tmp_path, "hello.sh", 'echo "hello-from-stub"')
    completed, timed_out, output_limited = wf._run_bounded([str(stub)], 10)
    assert completed.returncode == 0
    assert "hello-from-stub" in completed.stdout
    assert timed_out is False and output_limited is False


def test_build_command_dash_prompt_is_guarded_by_separator():
    """issue-12：以 '-' 开头的 prompt 必须位于 '--' 之后。"""
    for backend in ("pi", "codex"):
        cmd = wf.build_command(backend, "--version", None, "test-model")
        assert "--" in cmd
        assert cmd[cmd.index("--") + 1] == "--version"
        assert cmd[-1] == "--version"
    cmd = wf.build_command("codex", "--version", Path("/tmp/x.out"), "test-model")
    assert cmd[cmd.index("--") + 1] == "--version"


def test_run_item_pi_outputfile_points_to_real_dump(tmp_path, monkeypatch):
    """issue-10：pi 成功项的 outputFile 必须指向真实存在的产物。"""
    stub = _emitter(tmp_path, "fakepi.sh", 'echo "STUB_ANSWER"')
    monkeypatch.setattr(wf, "build_command", lambda backend, prompt, output_file, model=None: [str(stub)])
    record = wf.run_item("pi", {"id": "p-dash", "prompt": "hi"}, tmp_path, 10)
    assert record["ok"] is True, record
    assert record["outputFile"], "outputFile 不得为空"
    assert Path(record["outputFile"]).exists(), f"outputFile 悬空: {record['outputFile']}"
    assert Path(record["outputFile"]).read_text(encoding="utf-8").strip() == "STUB_ANSWER"


def test_run_item_pi_dash_prompt_end_to_end(tmp_path, monkeypatch):
    """端到端：'- 开头 prompt 经 '--' 送达 stub 的位置参数而非被吞成选项。"""
    received = tmp_path / "argv.json"
    stub = tmp_path / "argvrecorder.sh"
    stub.write_text(
        "#!/bin/sh\n"
        f"python3 -c \"import json,sys; open('{received}','w').write(json.dumps(sys.argv[1:]))\" \"$@\"\n"
        "echo STUB_OK\n"
    )
    stub.chmod(0o755)
    monkeypatch.setattr(wf, "build_command", lambda backend, prompt, output_file, model=None: [str(stub), "--", prompt])
    record = wf.run_item("pi", {"id": "p-argv", "prompt": "--dangerous"}, tmp_path, 10)
    assert record["ok"] is True, record
    argv = __import__("json").loads(received.read_text())
    assert "--dangerous" in argv


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="verified group cleanup requires Linux /proc")
def test_normal_exit_kills_detached_stdio_children_before_next_item(tmp_path, monkeypatch):
    """A successful parent may leave same-group workers with DEVNULL stdio."""
    children = tmp_path / "child-pids.txt"
    plan = tmp_path / "plan.json"
    result = tmp_path / "result.jsonl"
    plan.write_text(json.dumps({"items": [{"id": "a", "prompt": "first"}, {"id": "b", "prompt": "second"}]}))
    worker = r"""
import os, subprocess, sys
from pathlib import Path
log, label = Path(sys.argv[1]), sys.argv[2]
previous_alive = False
if log.exists():
    previous_pid = int(log.read_text().splitlines()[-1].split(":")[0])
    try:
        tail = Path(f"/proc/{previous_pid}/stat").read_bytes().rsplit(b")", 1)[-1].split()
        previous_alive = bool(tail) and tail[0] != b"Z"
    except FileNotFoundError:
        pass
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
with log.open("a") as handle:
    handle.write(f"{child.pid}:{os.getpgrp()}\n")
print(f"previous_alive={previous_alive} child={child.pid}", flush=True)
"""
    monkeypatch.setattr(wf, "resolve_binary", lambda backend: sys.executable)
    monkeypatch.setattr(
        wf,
        "build_command",
        lambda backend, prompt, output, model=None: [sys.executable, "-c", worker, str(children), prompt],
    )
    try:
        code = wf.main(["--backend", "pi", "--plan", str(plan), "--out", str(result), "--concurrency", "1"])
        records = [json.loads(line) for line in result.read_text().splitlines()]
        assert code == 0 and len(records) == 2 and all(record["ok"] for record in records)
        assert all("previous_alive=False" in record["stdoutSummary"] for record in records)
        pairs = [tuple(map(int, line.split(":"))) for line in children.read_text().splitlines()]
        assert len(pairs) == 2
        assert all(not _process_alive(pid) for pid, _ in pairs)
    finally:
        # The test owns only these exact recorded children and verifies the
        # original process-group identity before any emergency cleanup.
        if children.exists():
            for line in children.read_text().splitlines():
                pid, group = map(int, line.split(":"))
                try:
                    if _process_alive(pid) and os.getpgid(pid) == group:
                        os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="verified group cleanup requires Linux /proc")
def test_parent_exit_kills_child_holding_output_pipe():
    worker = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print('done',flush=True)"
    )
    started = time.monotonic()
    completed, timed_out, limited = wf._run_bounded([sys.executable, "-c", worker], 6)
    assert time.monotonic() - started < 4
    assert completed.returncode == 0 and not timed_out and not limited
    assert "done" in completed.stdout

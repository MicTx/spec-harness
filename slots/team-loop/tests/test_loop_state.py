"""loop_state 状态机测试：生命周期、检查点原子性、事件日志、恢复一致性。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from loop_state import (
    EVENTS_FILENAME,
    LOCK_FILENAME,
    MODES,
    RUN_FILENAME,
    STOP_FILENAME,
    LoopStateError,
    LoopStore,
    default_run_dir,
    new_run_id,
    write_stop_sentinel,
)


def make_store(tmp_path, clock, goal="批量抽取元数据直至校验通过", mode="until-converged"):
    run_id = new_run_id(goal, clock())
    run_dir = default_run_dir(tmp_path, run_id)
    return LoopStore.init(run_dir, goal, mode, config={"retry": {"baseSec": 0, "maxSec": 0}}, now=clock)


def test_init_creates_atomic_state(tmp_path, clock):
    store = make_store(tmp_path, clock)
    assert (store.run_dir / RUN_FILENAME).is_file()
    state = store.state
    assert state["status"] == "active"
    assert state["goal"] == "批量抽取元数据直至校验通过"
    assert state["config"]["concurrency"] == 3
    # 事件日志有 run_init
    events = store.events()
    assert events[0]["type"] == "run_init"


def test_init_rejects_nonempty_dir(tmp_path, clock):
    run_dir = tmp_path / "run-x"
    run_dir.mkdir()
    (run_dir / "stray.txt").write_text("x", encoding="utf-8")
    with pytest.raises(LoopStateError):
        LoopStore.init(run_dir, "g", "until-converged", now=clock)


def test_init_rejects_bad_config(tmp_path, clock):
    with pytest.raises(LoopStateError):
        LoopStore.init(tmp_path / "r1", "g", "until-converged", config={"concurrency": 0}, now=clock)
    with pytest.raises(LoopStateError):
        LoopStore.init(
            tmp_path / "r2",
            "g",
            "until-converged",
            config={"retry": {"maxSec": 1, "baseSec": 5}},
            now=clock,
        )


def test_round_lifecycle(tmp_path, clock):
    store = make_store(tmp_path, clock)
    r1 = store.round_start("第一轮：解析")
    assert r1 == 1
    with pytest.raises(LoopStateError):
        store.round_start("重复开轮")
    store.task_add(1, "t1", "worker", "解析章节 1-6")
    store.task_spawn(1, "t1", "agent-a", "sub-1")
    store.task_result(1, "t1", "ok", result={"chapters": 6}, confirmed_stopped=True)
    store.round_end("pass")
    with pytest.raises(LoopStateError):
        store.round_end("pass")  # 没有 open round


def test_task_retry_flow_and_exhaustion(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    store.task_spawn(1, "t1", "a", "s")
    assert store.task_result(1, "t1", "fail", error="超时", confirmed_stopped=True) == "retry"
    assert store.state["rounds"][0]["tasks"]["t1"]["status"] == "pending"
    assert store.state["rounds"][0]["tasks"]["t1"]["attempts"] == 1
    store.task_spawn(1, "t1", "a2", "s2")  # attempts=2
    assert store.task_result(1, "t1", "fail", error="又超时", submission="s2", confirmed_stopped=True) == "retry"
    store.task_spawn(1, "t1", "a3", "s3")  # attempts=3 = maxAttempts
    assert store.task_result(1, "t1", "fail", error="第三次", submission="s3", confirmed_stopped=True) == "exhausted"
    task = store.state["rounds"][0]["tasks"]["t1"]
    assert task["status"] == "failed"
    assert task["lastError"] == "第三次"


def test_task_result_requires_in_flight(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    with pytest.raises(LoopStateError):
        store.task_result(1, "t1", "ok", confirmed_stopped=True)


def test_heartbeat_updates(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    store.task_spawn(1, "t1", "a", "s")
    hb0 = store.state["rounds"][0]["tasks"]["t1"]["lastHeartbeat"]
    clock.advance(60)
    store.task_heartbeat(1, "t1", source="worker", submission="s")
    assert store.state["rounds"][0]["tasks"]["t1"]["lastHeartbeat"] > hb0


def test_finish_states_and_guards(tmp_path, clock):
    store = make_store(tmp_path, clock)
    with pytest.raises(LoopStateError, match="convergence conditions are not yet met"):
        store.run_converged("空 run 不能收敛")
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    store.task_spawn(1, "t1", "a", "s")
    store.task_result(1, "t1", "ok", confirmed_stopped=True)
    store.round_end("pass")
    store.run_converged("完成")
    with pytest.raises(LoopStateError):
        store.round_start("再开")  # 终态拒绝变更


def test_terminal_run_rejects_all_task_mutations(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    store.task_spawn(1, "t1", "a", "s")
    with pytest.raises(LoopStateError, match="convergence conditions are not yet met"):
        store.run_converged("在途不能收敛")
    store.run_failed("abort", confirmed_stopped=True)
    for operation in (
        lambda: store.task_heartbeat(1, "t1"),
        lambda: store.task_backoff_until(1, "t1", clock()),
        lambda: store.task_cancel(1, "t1", "stop"),
    ):
        with pytest.raises(LoopStateError):
            operation()


def test_interrupted_resume_retains_in_flight(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务一")
    store.task_add(1, "t2", "worker", "任务二")
    store.task_spawn(1, "t1", "a", "s")
    store.task_spawn(1, "t2", "a2", "s2")
    store.task_result(1, "t1", "ok", confirmed_stopped=True)
    affected = store.run_interrupted("用户暂停")
    assert affected == ["r1/t2"]
    assert store.state["status"] == "interrupted"
    info = store.resume()
    assert store.state["status"] == "active"
    tasks = store.state["rounds"][0]["tasks"]
    assert tasks["t1"]["status"] == "done"
    assert tasks["t2"]["status"] == "in_flight"
    assert "r1/t2" in info["inFlight"]
    assert info["nextActions"]  # 有可执行下一步


def test_resume_rejects_terminal(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.run_failed("不行")
    with pytest.raises(LoopStateError):
        store.resume()


def test_reload_detects_corruption(tmp_path, clock):
    store = make_store(tmp_path, clock)
    path = store.run_dir / RUN_FILENAME
    path.write_text("{broken json", encoding="utf-8")
    with pytest.raises(LoopStateError):
        LoopStore(store.run_dir).load()


def test_reload_rejects_malformed_nested_schema(tmp_path, clock):
    store = make_store(tmp_path, clock)
    path = store.run_dir / RUN_FILENAME
    payload = store.state.copy()
    payload["config"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LoopStateError):
        LoopStore(store.run_dir).load()
    payload = store.state.copy()
    payload["config"] = store.state["config"]
    payload["rounds"] = {"bad": True}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LoopStateError):
        LoopStore(store.run_dir).load()
    path.write_text(json.dumps({"nonsense": True}), encoding="utf-8")
    with pytest.raises(LoopStateError):
        LoopStore(store.run_dir).load()


def test_events_log_sequence(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    store.task_spawn(1, "t1", "a", "s")
    store.task_result(1, "t1", "fail", error="x", confirmed_stopped=True)
    store.task_spawn(1, "t1", "a2", "s2")
    store.task_result(1, "t1", "ok", submission="s2", confirmed_stopped=True)
    store.round_end("pass")
    store.run_converged("完成")
    types = [e["type"] for e in store.events()]
    assert types == [
        "run_init",
        "round_start",
        "task_add",
        "task_spawn",
        "task_retry_scheduled",
        "task_spawn",
        "task_result",
        "round_end",
        "run_converged",
    ]


def test_no_tmp_left_behind(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    leftovers = [p.name for p in store.run_dir.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_corrupt_event_shape_and_symlink_are_rejected(tmp_path, clock):
    store = make_store(tmp_path, clock)
    events = store.run_dir / EVENTS_FILENAME
    events.write_text("[]\n", encoding="utf-8")
    with pytest.raises(LoopStateError):
        store.events()
    events.unlink()
    target = tmp_path / "outside-events.jsonl"
    target.write_text('{"type": "fake"}\n', encoding="utf-8")
    if hasattr(os, "symlink"):
        events.symlink_to(target)
        with pytest.raises(LoopStateError):
            store.events()


def test_init_rejects_unknown_mode(tmp_path, clock):
    with pytest.raises(LoopStateError, match="mode"):
        LoopStore.init(tmp_path / "run-mode", "g", "bogus", now=clock)


@pytest.mark.parametrize("mode", MODES)
def test_each_mode_drives_the_same_lifecycle(tmp_path, clock, mode):
    run_dir = default_run_dir(tmp_path, new_run_id("模式", clock()))
    store = LoopStore.init(run_dir, "g", mode, now=clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "d")
    token = store.task_reserve(1, "t1")
    store.task_spawn(1, "t1", "a", "s", reservation=token)
    store.task_result(1, "t1", "ok", reservation=token, confirmed_stopped=True)
    store.round_end("pass")
    store.run_converged()
    assert store.state["mode"] == mode
    assert store.state["status"] == "converged"


def test_resume_rejects_event_for_unknown_task(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    store.task_spawn(1, "t1", "a", "s")
    assert store.run_interrupted("暂停") == ["r1/t1"]
    ghost = {"ts": clock(), "type": "task_spawn", "round": 1, "task": "ghost"}
    with (store.run_dir / EVENTS_FILENAME).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(ghost) + "\n")
    with pytest.raises(LoopStateError, match="inconsistent"):
        store.resume()
    # 一致性检查必须先于 interrupted -> active 迁移
    assert json.loads((store.run_dir / RUN_FILENAME).read_text(encoding="utf-8"))["status"] == "interrupted"


@pytest.mark.parametrize(
    "damage",
    [
        lambda data: data.__setitem__("rounds", [1]),
        lambda data: data["rounds"][0].__setitem__("tasks", []),
        lambda data: data["rounds"][0]["tasks"].__setitem__("t1", 1),
        lambda data: data["rounds"][0]["tasks"]["t1"].__setitem__("status", "zombie"),
        lambda data: data["rounds"][0]["tasks"]["t1"].__setitem__("workerReport", []),
        lambda data: data["config"].__setitem__("retry", []),
    ],
)
def test_reload_rejects_deeply_malformed_state(tmp_path, clock, damage):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    store.task_spawn(1, "t1", "a", "s")
    payload = json.loads((store.run_dir / RUN_FILENAME).read_text(encoding="utf-8"))
    damage(payload)
    (store.run_dir / RUN_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LoopStateError):
        LoopStore(store.run_dir).load()


def test_state_mutations_require_flock(tmp_path, clock, monkeypatch):
    import loop_state

    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    monkeypatch.setattr(loop_state, "fcntl", None)
    with pytest.raises(LoopStateError, match="flock"):
        store.task_reserve(1, "t1")
    with pytest.raises(LoopStateError, match="flock"):
        write_stop_sentinel(store.run_dir, {"reason": "stop"})
    assert not (store.run_dir / STOP_FILENAME).exists()
    assert store.state["rounds"][0]["tasks"]["t1"]["status"] == "pending"


def test_protocol_files_reject_hardlinks(tmp_path, clock):
    store = make_store(tmp_path, clock)
    outside_events = tmp_path / "outside-events.jsonl"
    outside_events.write_text("", encoding="utf-8")
    events = store.run_dir / EVENTS_FILENAME
    events.unlink()
    os.link(outside_events, events)
    with pytest.raises(LoopStateError, match="hard link"):
        store.round_start("r")
    assert outside_events.read_text(encoding="utf-8") == ""

    outside_lock = tmp_path / "outside.lock"
    outside_lock.write_text("sentinel", encoding="utf-8")
    lock = store.run_dir / LOCK_FILENAME
    lock.unlink()
    os.link(outside_lock, lock)
    with pytest.raises(LoopStateError, match="hard link"):
        store._persist()
    assert outside_lock.read_text(encoding="utf-8") == "sentinel"


def test_reservation_rechecks_stop_after_lock_acquisition(tmp_path, clock):
    import loop_state

    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    results: list[str] = []

    def reserve() -> None:
        try:
            store.task_reserve(1, "t1")
        except LoopStateError as exc:
            results.append(str(exc))

    with loop_state._protocol_file(store.run_dir / LOCK_FILENAME, "a+") as lock:
        loop_state.fcntl.flock(lock.fileno(), loop_state.fcntl.LOCK_EX)
        thread = threading.Thread(target=reserve)
        thread.start()
        time.sleep(0.2)
        assert thread.is_alive(), "reserve 必须在 run 锁上等待"
        loop_state._atomic_write_json(store.run_dir / STOP_FILENAME, {"reason": "stop"})
    thread.join(10)
    assert results and "STOP" in results[0]
    assert LoopStore(store.run_dir, now=clock).load().state["counters"]["tasksSpawned"] == 0


def test_stop_sentinel_write_waits_for_run_lock(tmp_path, clock):
    import loop_state

    store = make_store(tmp_path, clock)
    done = threading.Event()

    def write_stop() -> None:
        write_stop_sentinel(store.run_dir, {"reason": "stop"})
        done.set()

    with loop_state._protocol_file(store.run_dir / LOCK_FILENAME, "a+") as lock:
        loop_state.fcntl.flock(lock.fileno(), loop_state.fcntl.LOCK_EX)
        thread = threading.Thread(target=write_stop)
        thread.start()
        time.sleep(0.2)
        assert not done.is_set(), "STOP 写入必须等待 run 锁"
    thread.join(10)
    assert done.is_set()
    assert json.loads((store.run_dir / STOP_FILENAME).read_text(encoding="utf-8"))["reason"] == "stop"


def test_cli_result_requires_outcome(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    store.task_spawn(1, "t1", "a", "s")
    before = (store.run_dir / RUN_FILENAME).read_bytes()
    script = Path(__file__).resolve().parent.parent / "scripts" / "loop_state.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "task",
            "result",
            "--run-dir",
            str(store.run_dir),
            "--round",
            "1",
            "--id",
            "t1",
            "--confirmed-stopped",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2 and "outcome" in result.stderr
    assert (store.run_dir / RUN_FILENAME).read_bytes() == before


def test_summary_keeps_public_fields(tmp_path, clock):
    store = make_store(tmp_path, clock)
    summary = store.summary()
    assert summary["active"] is True
    assert summary["rounds"] == 0
    assert {"runId", "goal", "mode", "status", "converged", "currentRound", "inFlight", "runDir"} <= summary.keys()
    store.round_start("r")
    assert store.summary()["rounds"] == 1

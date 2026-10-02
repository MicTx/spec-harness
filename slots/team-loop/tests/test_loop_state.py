"""loop_state 状态机测试：生命周期、检查点原子性、事件日志、恢复一致性。"""

from __future__ import annotations

import json
import os

import pytest
from loop_state import (
    EVENTS_FILENAME,
    RUN_FILENAME,
    LoopStateError,
    LoopStore,
    default_run_dir,
    new_run_id,
)


def make_store(tmp_path, clock, goal="批量抽取元数据直至校验通过", mode="until-converged"):
    run_id = new_run_id(goal, clock())
    run_dir = default_run_dir(tmp_path, run_id)
    return LoopStore.init(run_dir, goal, mode, now=clock)


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
    store.task_result(1, "t1", "ok", result={"chapters": 6})
    store.round_end("pass")
    with pytest.raises(LoopStateError):
        store.round_end("pass")  # 没有 open round


def test_task_retry_flow_and_exhaustion(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    store.task_spawn(1, "t1", "a", "s")
    assert store.task_result(1, "t1", "fail", error="超时") == "retry"
    assert store.state["rounds"][0]["tasks"]["t1"]["status"] == "pending"
    assert store.state["rounds"][0]["tasks"]["t1"]["attempts"] == 1
    store.task_spawn(1, "t1", "a2", "s2")  # attempts=2
    assert store.task_result(1, "t1", "fail", error="又超时") == "retry"
    store.task_spawn(1, "t1", "a3", "s3")  # attempts=3 = maxAttempts
    assert store.task_result(1, "t1", "fail", error="第三次") == "exhausted"
    task = store.state["rounds"][0]["tasks"]["t1"]
    assert task["status"] == "failed"
    assert task["lastError"] == "第三次"


def test_task_result_requires_in_flight(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    with pytest.raises(LoopStateError):
        store.task_result(1, "t1", "ok")


def test_heartbeat_updates(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    store.task_spawn(1, "t1", "a", "s")
    hb0 = store.state["rounds"][0]["tasks"]["t1"]["lastHeartbeat"]
    clock.advance(60)
    store.task_heartbeat(1, "t1")
    assert store.state["rounds"][0]["tasks"]["t1"]["lastHeartbeat"] > hb0


def test_finish_states_and_guards(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.run_converged("完成")
    with pytest.raises(LoopStateError):
        store.round_start("再开")  # 终态拒绝变更


def test_terminal_run_rejects_all_task_mutations(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务")
    store.task_spawn(1, "t1", "a", "s")
    store.run_converged("完成")
    for operation in (
        lambda: store.task_heartbeat(1, "t1"),
        lambda: store.task_backoff_until(1, "t1", clock()),
        lambda: store.task_cancel(1, "t1", "stop"),
    ):
        with pytest.raises(LoopStateError):
            operation()


def test_interrupted_resume_reverts_in_flight(tmp_path, clock):
    store = make_store(tmp_path, clock)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务一")
    store.task_add(1, "t2", "worker", "任务二")
    store.task_spawn(1, "t1", "a", "s")
    store.task_spawn(1, "t2", "a2", "s2")
    store.task_result(1, "t1", "ok")
    affected = store.run_interrupted("用户暂停")
    assert affected == ["r1/t2"]
    assert store.state["status"] == "interrupted"
    info = store.resume()
    assert store.state["status"] == "active"
    tasks = store.state["rounds"][0]["tasks"]
    assert tasks["t1"]["status"] == "done"
    assert tasks["t2"]["status"] == "pending"
    assert "r1/t2" in info["pending"]
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
    store.task_result(1, "t1", "fail", error="x")
    store.task_spawn(1, "t1", "a2", "s2")
    store.task_result(1, "t1", "ok")
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


def test_stale_store_cannot_overwrite_newer_revision(tmp_path, clock):
    store = make_store(tmp_path, clock)
    first = LoopStore(store.run_dir, now=clock).load()
    second = LoopStore(store.run_dir, now=clock).load()
    first.round_start("first")
    with pytest.raises(LoopStateError, match="并发冲突"):
        second.round_start("stale")

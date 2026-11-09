"""Managed dispatch boundaries: no host or model is launched by these tests."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from loop_control import admission, reap_stale_tasks, stale_tasks, termination_check
from loop_state import LoopStateError, LoopStore, write_stop_sentinel

STATE = Path(__file__).resolve().parents[1] / "scripts" / "loop_state.py"


def make_store(tmp_path, clock, **config):
    defaults = {
        "maxRounds": 2,
        "concurrency": 2,
        "budgetTasks": 8,
        "stalenessSec": 10,
        "retry": {"maxAttempts": 3, "baseSec": 5, "maxSec": 20, "jitter": 0},
    }
    defaults.update(config)
    store = LoopStore.init(tmp_path / "run", "g", "until-converged", config=defaults, now=clock)
    store.round_start("r1")
    return store


def add(store, task_id="t1"):
    store.task_add(1, task_id, "worker", "verify")


def attach(store, token, task_id="t1", submission="s1"):
    store.task_spawn(1, task_id, "agent", submission, reservation=token)


def test_last_round_and_budget_allow_active_work_and_close(tmp_path, clock):
    store = make_store(tmp_path, clock, maxRounds=1, budgetTasks=1)
    add(store)
    token = store.task_reserve(1, "t1")
    assert termination_check(store, clock())["verdict"] == "continue"
    attach(store, token)
    assert admission(store, clock())["admit"] is False
    assert termination_check(store, clock())["verdict"] == "continue"
    with pytest.raises(LoopStateError, match="尚未满足"):
        store.run_converged()
    store.task_result(1, "t1", "ok", reservation=token, confirmed_stopped=True)
    assert termination_check(store, clock())["verdict"] == "continue"
    store.round_end("pass")
    assert termination_check(store, clock())["verdict"] == "converged"
    store.run_converged()


def test_failed_review_is_superseded_by_later_pass(tmp_path, clock):
    store = make_store(tmp_path, clock)
    add(store)
    token = store.task_reserve(1, "t1")
    attach(store, token)
    store.task_result(1, "t1", "ok", reservation=token, confirmed_stopped=True)
    store.round_end("fail")
    assert termination_check(store, clock())["verdict"] == "continue"
    store.round_start("repair review")
    store.task_add(2, "t2", "worker", "verify correction")
    token2 = store.task_reserve(2, "t2")
    store.task_spawn(2, "t2", "agent2", "s2", reservation=token2)
    store.task_result(2, "t2", "ok", reservation=token2, confirmed_stopped=True)
    store.round_end("pass")
    assert termination_check(store, clock())["verdict"] == "converged"
    store.run_converged()


def test_last_budget_inflight_with_pending_waits_then_stops(tmp_path, clock):
    store = make_store(tmp_path, clock, budgetTasks=1)
    add(store, "a")
    add(store, "b")
    token = store.task_reserve(1, "a")
    attach(store, token, "a")
    with pytest.raises(LoopStateError, match="budgetTasks"):
        store.task_reserve(1, "b")
    assert termination_check(store, clock())["verdict"] == "continue"
    store.task_result(1, "a", "ok", reservation=token, confirmed_stopped=True)
    verdict = termination_check(store, clock())
    assert verdict["verdict"] == "stopped" and "budgetTasks" in " ".join(verdict["reasons"])


def test_backoff_is_atomic_and_cannot_be_shortened(tmp_path, clock):
    store = make_store(tmp_path, clock)
    add(store)
    token = store.task_reserve(1, "t1")
    attach(store, token)
    store.task_result(1, "t1", "fail", reservation=token, confirmed_stopped=True)
    task = store.state["rounds"][0]["tasks"]["t1"]
    assert task["backoffUntil"] == clock() + 5
    store.task_backoff_until(1, "t1", clock() - 100)
    assert task["backoffUntil"] == clock() + 5
    with pytest.raises(LoopStateError, match="backoff"):
        store.task_reserve(1, "t1")
    clock.advance(5)
    token2 = store.task_reserve(1, "t1")
    attach(store, token2, submission="s2")
    with pytest.raises(LoopStateError, match="过期"):
        store.task_result(1, "t1", "ok", reservation=token, submission="s1", confirmed_stopped=True)
    with pytest.raises(LoopStateError, match="submission"):
        store.task_result(1, "t1", "ok", reservation=token2, submission="s1", confirmed_stopped=True)
    store.task_result(1, "t1", "ok", reservation=token2, submission="s2", confirmed_stopped=True)


def test_reservation_survives_crash_and_attach_is_idempotent(tmp_path, clock):
    store = make_store(tmp_path, clock, concurrency=1)
    add(store, "a")
    add(store, "b")
    token = store.task_reserve(1, "a")
    # The host may or may not have launched after this point. Never expire the reservation on age.
    clock.advance(100)
    recovered = LoopStore(store.run_dir, now=clock).load()
    assert recovered.resume()["reserved"] == ["r1/a"]
    assert stale_tasks(recovered, clock()) == []
    with pytest.raises(LoopStateError, match="并发"):
        recovered.task_reserve(1, "b")
    with pytest.raises(LoopStateError, match="核实"):
        recovered.task_abandon(1, "a", token, confirmed_stopped=False)
    recovered.task_spawn(1, "a", "host-worker", "submission", reservation=token)
    LoopStore(store.run_dir, now=clock).load().task_spawn(1, "a", "host-worker", "submission", reservation=token)
    latest = LoopStore(store.run_dir, now=clock).load()
    assert latest.state["counters"]["tasksSpawned"] == 1
    with pytest.raises(LoopStateError):
        latest.task_spawn(1, "a", "other-worker", "submission", reservation=token)


def test_confirmed_reservation_abandon_consumes_attempt_and_budget(tmp_path, clock):
    store = make_store(tmp_path, clock, budgetTasks=1)
    add(store)
    token = store.task_reserve(1, "t1")
    assert store.task_abandon(1, "t1", token, confirmed_stopped=True) == "retry"
    clock.advance(10)
    with pytest.raises(LoopStateError, match="budgetTasks"):
        store.task_reserve(1, "t1")
    assert termination_check(store, clock())["verdict"] == "stopped"


def test_slow_worker_is_not_reaped_on_coordinator_heartbeat_or_age_alone(tmp_path, clock):
    store = make_store(tmp_path, clock)
    add(store)
    token = store.task_reserve(1, "t1")
    attach(store, token)
    clock.advance(11)
    store.task_heartbeat(1, "t1")  # only the coordinator is alive
    assert stale_tasks(store, clock()) == ["r1/t1"]
    with pytest.raises(LoopStateError, match="确认"):
        reap_stale_tasks(store, clock(), task_key="r1/t1")
    with pytest.raises(LoopStateError, match="submission"):
        store.task_heartbeat(1, "t1", source="host", submission="old", reservation=token)
    store.task_heartbeat(1, "t1", source="host", submission="s1", reservation=token)
    assert stale_tasks(store, clock()) == []
    clock.advance(11)
    outcome = reap_stale_tasks(store, clock(), task_key="r1/t1", confirmed_stopped=True)
    assert outcome["reaped"][0]["outcome"] == "retry"
    assert store.state["rounds"][0]["tasks"]["t1"]["backoffUntil"] == clock() + 5


def test_interrupt_resume_does_not_free_worker_or_reset_attempts(tmp_path, clock):
    store = make_store(tmp_path, clock, retry={"maxAttempts": 1, "baseSec": 0, "maxSec": 0, "jitter": 0})
    add(store)
    token = store.task_reserve(1, "t1")
    attach(store, token)
    store.run_interrupted("pause")
    info = store.resume()
    assert info["inFlight"] == ["r1/t1"] and not info["pending"]
    with pytest.raises(LoopStateError):
        store.task_reserve(1, "t1")
    assert store.task_result(1, "t1", "fail", reservation=token, confirmed_stopped=True) == "exhausted"
    with pytest.raises(LoopStateError):
        store.task_reserve(1, "t1")


def test_legacy_retry_requires_current_submission_and_unique_ids(tmp_path, clock):
    store = make_store(tmp_path, clock, retry={"maxAttempts": 3, "baseSec": 0, "maxSec": 0, "jitter": 0})
    add(store)
    store.task_spawn(1, "t1", "a", "s1")
    store.task_result(1, "t1", "fail", confirmed_stopped=True)  # legacy first attempt remains compatible
    with pytest.raises(LoopStateError, match="submission"):
        store.task_spawn(1, "t1", "b", "s1")
    store.task_spawn(1, "t1", "b", "s2")
    with pytest.raises(LoopStateError, match="submission"):
        store.task_result(1, "t1", "ok", confirmed_stopped=True)
    with pytest.raises(LoopStateError, match="过期"):
        store.task_result(1, "t1", "ok", submission="s1", confirmed_stopped=True)
    store.task_result(1, "t1", "ok", submission="s2", confirmed_stopped=True)


def test_stop_sentinel_blocks_stale_snapshot_reservation(tmp_path, clock):
    store = make_store(tmp_path, clock)
    add(store)
    stale = LoopStore(store.run_dir, now=clock).load()
    write_stop_sentinel(store.run_dir, {"reason": "stop"})
    with pytest.raises(LoopStateError, match="STOP"):
        stale.task_reserve(1, "t1")
    loaded = LoopStore(store.run_dir, now=clock).load()
    assert loaded.state["counters"]["tasksSpawned"] == 0


def test_legacy_pending_failure_auto_schedules_backoff(tmp_path, clock):
    """历史遗留的 pending+lastError+无 backoffUntil 不再死锁：dispatch 当场补退避。"""
    store = make_store(tmp_path, clock)
    add(store)
    store.task_spawn(1, "t1", "a", "s")
    store.task_result(1, "t1", "fail", confirmed_stopped=True)
    # 模拟自动退避存在之前写入的历史状态
    store.state["rounds"][0]["tasks"]["t1"]["backoffUntil"] = None
    store._persist()
    with pytest.raises(LoopStateError, match="backoff"):
        store.task_reserve(1, "t1")
    assert store.state["rounds"][0]["tasks"]["t1"]["backoffUntil"] == clock() + 5
    clock.advance(5)
    assert store.task_reserve(1, "t1")


def test_historical_exhausted_task_does_not_mask_later_pass(tmp_path, clock):
    store = make_store(tmp_path, clock, retry={"maxAttempts": 1, "baseSec": 0, "maxSec": 0, "jitter": 0})
    add(store)
    token = store.task_reserve(1, "t1")
    attach(store, token)
    assert store.task_result(1, "t1", "fail", reservation=token, confirmed_stopped=True) == "exhausted"
    store.round_end("fail")
    store.round_start("later repair")
    store.task_add(2, "t2", "worker", "verify")
    second = store.task_reserve(2, "t2")
    store.task_spawn(2, "t2", "b", "s2", reservation=second)
    store.task_result(2, "t2", "ok", reservation=second, confirmed_stopped=True)
    store.round_end("pass")
    assert termination_check(store, clock())["verdict"] == "converged"


def test_worker_report_keeps_attempt_live_and_capacity_occupied(tmp_path, clock):
    store = make_store(tmp_path, clock, concurrency=1)
    add(store)
    token = store.task_reserve(1, "t1")
    attach(store, token)
    with pytest.raises(LoopStateError, match="非空"):
        store.task_report(1, "t1", {}, submission="s1")
    with pytest.raises(LoopStateError, match="submission"):
        store.task_report(1, "t1", {"evidence": "x"}, submission="old")
    store.task_report(1, "t1", {"evidence": "x"}, submission="s1")
    task = store.state["rounds"][0]["tasks"]["t1"]
    assert task["status"] == "in_flight" and task["workerReport"] == {"evidence": "x"}
    add(store, "t2")
    with pytest.raises(LoopStateError, match="并发"):
        store.task_reserve(1, "t2")
    store.task_result(1, "t1", "ok", reservation=token, confirmed_stopped=True)
    assert store.task_reserve(1, "t2")


def test_resume_after_interrupt_keeps_pending_work(tmp_path, clock):
    store = make_store(tmp_path, clock)
    add(store, "done")
    add(store, "pending")
    token = store.task_reserve(1, "done")
    attach(store, token, "done")
    store.task_result(1, "done", "ok", reservation=token, confirmed_stopped=True)
    store.run_interrupted("pause")
    info = store.resume()
    assert info["pending"] == ["r1/pending"] and info["inFlight"] == []
    assert any(action.startswith("reserve") for action in info["nextActions"])


def test_stale_snapshot_cannot_over_reserve(tmp_path, clock):
    """两个协调者各自加载快照后顺序 reserve：后写者必须被拒，不得双双拿到 token。"""
    store = make_store(tmp_path, clock, concurrency=1)
    add(store, "a")
    add(store, "b")
    stale = LoopStore(store.run_dir, now=clock).load()
    store.task_reserve(1, "a")
    with pytest.raises(LoopStateError, match="重新 load"):
        stale.task_reserve(1, "b")
    # 被拒后同一实例已回到磁盘真源：看不到虚假的第二个 in_flight/预算占用
    assert stale.state["rounds"][0]["tasks"]["b"]["status"] == "pending"
    assert stale.state["counters"]["tasksSpawned"] == 1
    loaded = LoopStore(store.run_dir, now=clock).load()
    assert loaded.state["rounds"][0]["tasks"]["a"]["status"] == "in_flight"
    assert loaded.state["counters"]["tasksSpawned"] == 1
    with pytest.raises(LoopStateError, match="并发"):
        loaded.task_reserve(1, "b")


def test_concurrent_reservers_never_both_win(tmp_path, clock):
    """cap=1 下两个并发写入者只能有一个落盘预约，另一个拿到 stale 错误。"""
    import threading

    store = make_store(tmp_path, clock, concurrency=1)
    add(store, "a")
    add(store, "b")
    stores = [LoopStore(store.run_dir, now=clock).load() for _ in range(2)]
    barrier = threading.Barrier(2)
    results = []

    def reserve(candidate, task_id):
        barrier.wait()
        try:
            results.append(("ok", candidate.task_reserve(1, task_id)))
        except LoopStateError as exc:
            results.append(("rejected", str(exc)))

    threads = [
        threading.Thread(target=reserve, args=(stores[index], task_id)) for index, task_id in enumerate(("a", "b"))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for status, _ in results if status == "ok") == 1
    loaded = LoopStore(store.run_dir, now=clock).load()
    assert loaded.state["counters"]["tasksSpawned"] == 1
    assert sum(task["status"] == "in_flight" for task in loaded.state["rounds"][0]["tasks"].values()) == 1


def test_failed_persist_keeps_instance_on_disk_truth(tmp_path, clock, monkeypatch):
    import loop_state

    store = make_store(tmp_path, clock, concurrency=1)
    add(store)
    real_write = loop_state._atomic_write_json

    def boom(path, payload):
        raise OSError("disk full")

    monkeypatch.setattr(loop_state, "_atomic_write_json", boom)
    with pytest.raises(LoopStateError, match="无法写入"):
        store.task_reserve(1, "t1")
    assert store.state["rounds"][0]["tasks"]["t1"]["status"] == "pending"
    assert store.state["counters"]["tasksSpawned"] == 0
    monkeypatch.setattr(loop_state, "_atomic_write_json", real_write)
    token = store.task_reserve(1, "t1")
    assert token and store.state["counters"]["tasksSpawned"] == 1


def test_legacy_backoff_deadline_persists_across_reloads(tmp_path, clock):
    """历史 pending+lastError 的补算期限必须先落盘：否则每次重载都重算出更晚期限，永不重试。"""
    store = make_store(tmp_path, clock, retry={"maxAttempts": 3, "baseSec": 5, "maxSec": 20, "jitter": 0})
    add(store)
    store.task_spawn(1, "t1", "a", "s")
    store.task_result(1, "t1", "fail", confirmed_stopped=True)
    legacy = LoopStore(store.run_dir, now=clock).load()
    legacy.state["rounds"][0]["tasks"]["t1"]["backoffUntil"] = None
    legacy._persist()
    first = LoopStore(store.run_dir, now=clock).load()
    with pytest.raises(LoopStateError, match="backoff"):
        first.task_reserve(1, "t1")
    disk = json.loads((store.run_dir / "run.json").read_text(encoding="utf-8"))
    deadline = disk["rounds"][0]["tasks"]["t1"]["backoffUntil"]
    assert deadline == clock() + 5
    clock.advance(3)
    second = LoopStore(store.run_dir, now=clock).load()
    with pytest.raises(LoopStateError, match="backoff"):
        second.task_reserve(1, "t1")
    # 重新加载不得把期限往后推
    assert second.state["rounds"][0]["tasks"]["t1"]["backoffUntil"] == deadline
    clock.advance(2)
    assert LoopStore(store.run_dir, now=clock).load().task_reserve(1, "t1")


def test_retried_attempt_identity_required_for_report_and_heartbeat(tmp_path, clock):
    store = make_store(tmp_path, clock, retry={"maxAttempts": 3, "baseSec": 0, "maxSec": 0, "jitter": 0})
    add(store)
    first = store.task_reserve(1, "t1")
    attach(store, first)
    store.task_result(1, "t1", "fail", reservation=first, confirmed_stopped=True)
    second = store.task_reserve(1, "t1")
    attach(store, second, submission="s2")
    with pytest.raises(LoopStateError, match="预约"):
        store.task_report(1, "t1", {"evidence": "old"}, reservation=first)
    with pytest.raises(LoopStateError, match="预约"):
        store.task_heartbeat(1, "t1", source="worker", reservation=first)
    with pytest.raises(LoopStateError, match="submission"):
        store.task_report(1, "t1", {"evidence": "old"}, submission="s1")
    with pytest.raises(LoopStateError, match="重试报告"):
        store.task_report(1, "t1", {"evidence": "anonymous"})
    with pytest.raises(LoopStateError, match="重试心跳"):
        store.task_heartbeat(1, "t1", source="worker")
    store.task_report(1, "t1", {"evidence": "current"}, reservation=second)
    task = store.state["rounds"][0]["tasks"]["t1"]
    assert task["workerReport"] == {"evidence": "current"}


def test_cli_report_passes_reservation_identity(tmp_path, clock):
    store = make_store(tmp_path, clock, retry={"maxAttempts": 3, "baseSec": 0, "maxSec": 0, "jitter": 0})
    add(store)
    first = store.task_reserve(1, "t1")
    attach(store, first)
    store.task_result(1, "t1", "fail", reservation=first, confirmed_stopped=True)
    second = store.task_reserve(1, "t1")
    attach(store, second, submission="s2")
    report = tmp_path / "worker-report.json"
    report.write_text(json.dumps({"evidence": "cli"}), encoding="utf-8")
    base = [
        sys.executable,
        str(STATE),
        "task",
        "report",
        "--run-dir",
        str(store.run_dir),
        "--round",
        "1",
        "--id",
        "t1",
        "--result",
        "@" + str(report),
    ]
    stale = subprocess.run([*base, "--reservation", first], capture_output=True, text=True, timeout=30)
    assert stale.returncode == 2 and "预约" in stale.stderr
    current = subprocess.run([*base, "--reservation", second], capture_output=True, text=True, timeout=30)
    assert current.returncode == 0, current.stderr
    task = LoopStore(store.run_dir).load().state["rounds"][0]["tasks"]["t1"]
    assert task["workerReport"] == {"evidence": "cli"}

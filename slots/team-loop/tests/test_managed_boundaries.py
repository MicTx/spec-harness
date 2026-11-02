"""Managed dispatch boundaries: no host or model is launched by these tests."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from loop_control import admission, reap_stale_tasks, stale_tasks, termination_check
from loop_state import LoopStateError, LoopStore, write_stop_sentinel


def make_store(tmp_path, clock, **config):
    defaults = {"maxRounds": 2, "concurrency": 2, "budgetTasks": 8,
                "stalenessSec": 10, "retry": {"maxAttempts": 3, "baseSec": 5, "maxSec": 20, "jitter": 0}}
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
        store.task_result(1, "t1", "ok", reservation=token, confirmed_stopped=True)
    with pytest.raises(LoopStateError, match="submission"):
        store.task_result(1, "t1", "ok", reservation=token2, submission="s1", confirmed_stopped=True)
    store.task_result(1, "t1", "ok", reservation=token2, submission="s2", confirmed_stopped=True)


@pytest.mark.parametrize("config", [{"concurrency": 1, "budgetTasks": 5},
                                    {"concurrency": 5, "budgetTasks": 1}])
def test_competing_pre_spawn_reservations_have_one_winner(tmp_path, clock, config):
    store = make_store(tmp_path, clock, **config)
    add(store, "a")
    add(store, "b")
    contenders = [LoopStore(store.run_dir, now=clock).load() for _ in range(2)]
    barrier = threading.Barrier(2)
    dispatched = []  # fake host is called only after successful reservation

    def compete(index):
        barrier.wait(timeout=5)
        try:
            token = contenders[index].task_reserve(1, ("a", "b")[index])
        except LoopStateError:
            return None
        dispatched.append(index)
        return token

    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(compete, range(2)))
    assert sum(token is not None for token in tokens) == 1
    assert len(dispatched) == 1
    loaded = LoopStore(store.run_dir, now=clock).load()
    assert loaded.state["counters"]["tasksSpawned"] == 1
    assert sum(t["status"] == "in_flight" for t in loaded.state["rounds"][0]["tasks"].values()) == 1
    with pytest.raises(LoopStateError):
        loaded.task_reserve(1, ("b", "a")[dispatched[0]])


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


def test_two_cli_processes_compete_before_fake_host_spawn(tmp_path):
    store = LoopStore.init(tmp_path / "run", "g", "until-converged",
                           config={"concurrency": 2, "budgetTasks": 1})
    store.round_start("r")
    store.task_add(1, "a", "worker", "verify")
    store.task_add(1, "b", "worker", "verify")
    script = Path(__file__).resolve().parents[1] / "scripts" / "loop_state.py"
    processes = [subprocess.Popen([sys.executable, str(script), "task", "reserve", "--run-dir", str(store.run_dir),
                                  "--round", "1", "--id", task], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for task in ("a", "b")]
    results = [(p.communicate(timeout=20), p.returncode) for p in processes]
    winners = [json.loads(out) for ((out, err), rc) in results if rc == 0]
    assert len(winners) == 1 and winners[0]["reservation"]
    assert all(rc in (0, 2) for (_, rc) in results)
    loaded = LoopStore(store.run_dir).load()
    assert loaded.state["counters"]["tasksSpawned"] == 1


def test_legacy_pending_failure_requires_recorded_backoff(tmp_path, clock):
    store = make_store(tmp_path, clock)
    add(store)
    store.task_spawn(1, "t1", "a", "s")
    store.task_result(1, "t1", "fail", confirmed_stopped=True)
    # Simulate a historical state written before automatic backoff existed.
    store.state["rounds"][0]["tasks"]["t1"]["backoffUntil"] = None
    store._persist()
    with pytest.raises(LoopStateError, match="旧任务缺少失败退避"):
        store.task_reserve(1, "t1")
    store.task_backoff_until(1, "t1", clock() + 5)
    with pytest.raises(LoopStateError, match="backoff"):
        store.task_reserve(1, "t1")
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


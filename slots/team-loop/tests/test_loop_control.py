"""loop_control 原语测试：退避数学、并发准入、staleness、终止组合、外部停止。"""

from __future__ import annotations

import os
import random

import pytest
from loop_control import (
    admission,
    backoff_delay,
    external_stop_requested,
    interrupt_run,
    retry_ready,
    should_retry,
    stale_tasks,
    termination_check,
)
from loop_state import STOP_FILENAME, LoopStateError, default_run_dir, new_run_id


def make_store(tmp_path, clock, concurrency=2, max_attempts=2):
    run_id = new_run_id("测试", clock())
    run_dir = default_run_dir(tmp_path, run_id)
    cfg = {"concurrency": concurrency, "retry": {"maxAttempts": max_attempts}}
    from loop_state import LoopStore

    return LoopStore.init(run_dir, "测试", "until-converged", config=cfg, now=clock)


def seeded_round(store, n_tasks=1):
    store.round_start("r")
    for i in range(1, n_tasks + 1):
        store.task_add(1, f"t{i}", "worker", f"任务{i}")


class TestBackoff:
    def test_exponential_ceiling(self):
        rng = random.Random(42)
        cfg = {"baseSec": 1.0, "maxSec": 60.0, "jitter": 0.0}
        # jitter=0 时退避应精确等于指数值
        assert backoff_delay(1, cfg, rng) == 1.0
        assert backoff_delay(2, cfg, rng) == 2.0
        assert backoff_delay(3, cfg, rng) == 4.0
        assert backoff_delay(4, cfg, rng) == 8.0

    def test_cap(self):
        rng = random.Random(42)
        cfg = {"baseSec": 10.0, "maxSec": 60.0, "jitter": 0.0}
        assert backoff_delay(10, cfg, rng) == 60.0  # 10*2^9 远超 cap

    def test_jitter_bounds_and_zero_at_base_zero(self):
        rng = random.Random(7)
        cfg = {"baseSec": 0.0, "maxSec": 60.0, "jitter": 0.5}
        for _ in range(20):
            assert backoff_delay(3, cfg, rng) == 0.0
        cfg2 = {"baseSec": 4.0, "maxSec": 60.0, "jitter": 0.3}
        for _ in range(50):
            d = backoff_delay(2, cfg2, rng)
            assert 0.0 <= d <= 8.0

    def test_deterministic_with_seed(self):
        a = backoff_delay(3, {"baseSec": 5.0, "maxSec": 60.0, "jitter": 0.3}, random.Random(1))
        b = backoff_delay(3, {"baseSec": 5.0, "maxSec": 60.0, "jitter": 0.3}, random.Random(1))
        assert a == b


class TestShouldRetry:
    def test_pending_under_max(self):
        assert should_retry({"status": "pending", "attempts": 1}, {"maxAttempts": 3})
        assert not should_retry({"status": "pending", "attempts": 3}, {"maxAttempts": 3})
        assert not should_retry({"status": "done", "attempts": 1}, {"maxAttempts": 3})

    def test_backoff_gate(self):
        task = {"backoffUntil": 2000.0}
        assert not retry_ready(task, now=1999.9)
        assert retry_ready(task, now=2000.0)


class TestAdmission:
    def test_admit_then_reject_at_cap(self, tmp_path, clock):
        store = make_store(tmp_path, clock, concurrency=2)
        seeded_round(store, n_tasks=3)
        store.task_spawn(1, "t1", "a1", "s1")
        assert admission(store, clock())["admit"] is True
        store.task_spawn(1, "t2", "a2", "s2")
        verdict = admission(store, clock())
        assert verdict["admit"] is False
        assert verdict["inFlight"] == 2
        assert "wait" in verdict["reason"]

    def test_done_frees_slot(self, tmp_path, clock):
        store = make_store(tmp_path, clock, concurrency=1)
        seeded_round(store, n_tasks=2)
        store.task_spawn(1, "t1", "a1", "s1")
        assert admission(store, clock())["admit"] is False
        store.task_result(1, "t1", "ok")
        assert admission(store, clock())["admit"] is True

    def test_spawn_enforces_cap_even_without_separate_admission(self, tmp_path, clock):
        store = make_store(tmp_path, clock, concurrency=1)
        seeded_round(store, n_tasks=2)
        store.task_spawn(1, "t1", "a1", "s1")
        with pytest.raises(LoopStateError, match="并发已满"):
            store.task_spawn(1, "t2", "a2", "s2")


class TestStaleness:
    def test_stale_detection(self, tmp_path, clock):
        store = make_store(tmp_path, clock)
        store.state["config"]["stalenessSec"] = 100.0
        seeded_round(store)
        store.task_spawn(1, "t1", "a", "s")
        assert stale_tasks(store, clock()) == []
        clock.advance(101)
        assert stale_tasks(store, clock()) == ["r1/t1"]
        store.task_heartbeat(1, "t1")
        assert stale_tasks(store, clock()) == []


class TestTermination:
    def test_continue_while_working(self, tmp_path, clock):
        store = make_store(tmp_path, clock)
        seeded_round(store)
        store.task_spawn(1, "t1", "a", "s")
        v = termination_check(store, clock())
        assert v == {"stop": False, "verdict": "continue", "reasons": []}

    def test_converged_when_all_done_rounds_closed(self, tmp_path, clock):
        store = make_store(tmp_path, clock)
        seeded_round(store)
        store.task_spawn(1, "t1", "a", "s")
        store.task_result(1, "t1", "ok")
        store.round_end("pass")
        v = termination_check(store, clock())
        assert v["stop"] is True and v["verdict"] == "converged"

    def test_not_converged_with_open_round(self, tmp_path, clock):
        store = make_store(tmp_path, clock)
        seeded_round(store)
        store.task_spawn(1, "t1", "a", "s")
        store.task_result(1, "t1", "ok")
        # round 仍 open -> 不算收敛
        v = termination_check(store, clock())
        assert v["verdict"] == "continue"

    def test_failed_task_triggers_stop(self, tmp_path, clock):
        store = make_store(tmp_path, clock, max_attempts=1)
        seeded_round(store)
        store.task_spawn(1, "t1", "a", "s")
        store.task_result(1, "t1", "fail", error="挂了")
        v = termination_check(store, clock())
        assert v["stop"] is True and v["verdict"] == "stopped"
        assert any("failed" in r for r in v["reasons"])

    def test_max_rounds(self, tmp_path, clock):
        store = make_store(tmp_path, clock)
        store.state["config"]["maxRounds"] = 1
        seeded_round(store)
        store.task_spawn(1, "t1", "a", "s")
        store.task_result(1, "t1", "ok")
        store.round_end("pass")
        # 已用 1 轮且无 open round -> converged 优先
        v = termination_check(store, clock())
        assert v["verdict"] == "converged"
        # 有 open round 且轮数用尽 -> stopped
        store.state["rounds"][0]["status"] = "open"
        v = termination_check(store, clock())
        assert v["verdict"] == "stopped" and any("maxRounds" in r for r in v["reasons"])

    def test_timeout(self, tmp_path, clock):
        store = make_store(tmp_path, clock)
        store.state["config"]["timeoutSec"] = 10.0
        seeded_round(store)
        clock.advance(11)
        v = termination_check(store, clock())
        assert v["verdict"] == "stopped" and any("runTimeout" in r for r in v["reasons"])

    def test_budget(self, tmp_path, clock):
        store = make_store(tmp_path, clock)
        store.state["config"]["budgetTasks"] = 1
        seeded_round(store, n_tasks=2)
        store.task_spawn(1, "t1", "a", "s")  # spawned=1 == budget，t2 仍 pending
        v = termination_check(store, clock())
        assert v["stop"] is True and any("budgetTasks" in r for r in v["reasons"])

    def test_external_stop_sentinel(self, tmp_path, clock):
        store = make_store(tmp_path, clock)
        seeded_round(store)
        (store.run_dir / STOP_FILENAME).write_text("{}", encoding="utf-8")
        assert external_stop_requested(store.run_dir)
        v = termination_check(store, clock())
        assert v["verdict"] == "stopped" and any("外部停止" in r for r in v["reasons"])

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
    def test_stop_symlink_is_rejected(self, tmp_path, clock):
        store = make_store(tmp_path, clock)
        target = tmp_path / "outside-stop"
        target.write_text("{}", encoding="utf-8")
        (store.run_dir / STOP_FILENAME).symlink_to(target)
        with pytest.raises(LoopStateError, match="symlink"):
            external_stop_requested(store.run_dir)

    def test_combined_reasons_and(self, tmp_path, clock):
        store = make_store(tmp_path, clock)
        store.state["config"]["timeoutSec"] = 5.0
        seeded_round(store)
        clock.advance(100)
        (store.run_dir / STOP_FILENAME).write_text("{}", encoding="utf-8")
        v = termination_check(store, clock())
        assert v["verdict"] == "stopped"
        assert len(v["reasons"]) >= 2  # AND 组合同时命中


class TestInterrupt:
    def test_interrupt_returns_inflight(self, tmp_path, clock):
        store = make_store(tmp_path, clock)
        seeded_round(store, n_tasks=2)
        store.task_spawn(1, "t1", "a1", "s1")
        store.task_spawn(1, "t2", "a2", "s2")
        out = interrupt_run(store, "暂停")
        assert out["inFlight"] == ["r1/t1", "r1/t2"]
        assert store.state["status"] == "interrupted"

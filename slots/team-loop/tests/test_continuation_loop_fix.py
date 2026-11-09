"""assignment continuation-loop-fix 隔离回归测试。

覆盖 6 项独立复现缺陷，全部不启动真实模型/宿主 worker：
1. task_cancel 不得把耗尽 failed 任务改写为 cancelled（否则 pass 掩盖失败、误报 converged）。
2. task_reserve 锁外检查 timeout，持锁跨期限后仍落盘 in_flight 并消耗预算。
3. run_converged/_finish 锁外 termination_check，等锁跨期限仍落 converged。
4. list_run_dirs/find_active_runs 对缺 run.json 但有 events.jsonl/.lock 痕迹的目录 fail-closed。
5. --run-id 直接传 default_run_dir 未校验单一路径段，../escape 或绝对路径落在 loop/ 外。
6. README 结果登记示例（成功、失败）缺 --confirmed-stopped，与强制要求不符。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loop_control import find_active_runs, termination_check
from loop_state import (
    EVENTS_FILENAME,
    LOCK_FILENAME,
    LoopStateError,
    LoopStore,
    default_run_dir,
    list_orphan_run_dirs,
    list_run_dirs,
)


def make_store(tmp_path, clock, **config):
    settings = {
        "maxRounds": 3,
        "concurrency": 3,
        "budgetTasks": 16,
        "timeoutSec": 100.0,
        "retry": {"maxAttempts": 2, "baseSec": 0, "maxSec": 0, "jitter": 0},
    }
    settings.update(config)
    store = LoopStore.init(tmp_path / "run", "goal", "until-converged", config=settings, now=clock)
    store.round_start("r")
    return store


def reserve_and_spawn(store, task_id="t1", agent="a", submission="s1"):
    store.task_add(1, task_id, "worker", "d")
    token = store.task_reserve(1, task_id)
    store.task_spawn(1, task_id, agent, submission, reservation=token)
    return token


# ---------------------------------------------------------------------------
# 缺陷 1：failed 任务不得被 cancel 改写
# ---------------------------------------------------------------------------


def test_cancel_rejects_failed_exhausted_task(tmp_path, clock):
    store = make_store(tmp_path, clock, retry={"maxAttempts": 1, "baseSec": 0, "maxSec": 0, "jitter": 0})
    store.task_add(1, "t1", "worker", "d")
    store.task_spawn(1, "t1", "a", "s")
    assert store.task_result(1, "t1", "fail", "boom", confirmed_stopped=True) == "exhausted"
    assert store.state["rounds"][0]["tasks"]["t1"]["status"] == "failed"
    with pytest.raises(LoopStateError, match="cannot be cancelled"):
        store.task_cancel(1, "t1", "give up", confirmed_stopped=True)
    assert store.state["rounds"][0]["tasks"]["t1"]["status"] == "failed"


def test_failed_task_cannot_be_masked_by_pass_after_cancel_attempt(tmp_path, clock):
    """取消失败被拒后，round_end pass 不得掩盖真实 failed，converged 不得误报。"""
    store = make_store(tmp_path, clock, retry={"maxAttempts": 1, "baseSec": 0, "maxSec": 0, "jitter": 0})
    store.task_add(1, "t1", "worker", "d")
    store.task_spawn(1, "t1", "a", "s")
    store.task_result(1, "t1", "fail", "boom", confirmed_stopped=True)
    with pytest.raises(LoopStateError, match="cannot be cancelled"):
        store.task_cancel(1, "t1", "give up", confirmed_stopped=True)
    # round_end 拒绝非终态？failed 是终态，可以关轮；但 pass 决策不得掩盖 failed。
    store.round_end("pass")
    verdict = termination_check(store, clock())
    assert verdict["verdict"] != "converged"
    with pytest.raises(LoopStateError, match="convergence conditions are not yet met"):
        store.run_converged()


# ---------------------------------------------------------------------------
# 缺陷 2：reserve 锁内未重验 timeout
# ---------------------------------------------------------------------------


def test_reserve_rechecks_timeout_inside_lock(tmp_path, clock, monkeypatch):
    store = make_store(
        tmp_path, clock, timeoutSec=10.0, retry={"maxAttempts": 2, "baseSec": 0, "maxSec": 0, "jitter": 0}
    )
    store.task_add(1, "t1", "worker", "d")
    # 锁外 _check_dispatch 时尚未超时；进入锁内前越过期限。

    real_persist = LoopStore._persist
    crossed = False

    def persist(self, **kwargs):
        nonlocal crossed
        # 在真正进入锁、写盘前推进时钟跨过 timeout 期限
        if not crossed:
            crossed = True
            clock.advance(11)
        return real_persist(self, **kwargs)

    monkeypatch.setattr(LoopStore, "_persist", persist)
    with pytest.raises(LoopStateError, match="runTimeout"):
        store.task_reserve(1, "t1")
    # 同一实例也必须回到磁盘真源：不得继续显示虚假 in_flight/预算占用
    assert store.state["rounds"][0]["tasks"]["t1"]["status"] == "pending"
    assert store.state["counters"]["tasksSpawned"] == 0
    # 落盘后不得残留 in_flight，预算不得消耗
    loaded = LoopStore(store.run_dir, now=clock).load()
    task = loaded.state["rounds"][0]["tasks"]["t1"]
    assert task["status"] == "pending"
    assert loaded.state["counters"]["tasksSpawned"] == 0


# ---------------------------------------------------------------------------
# 缺陷 3：run_converged 锁内未重验 timeout
# ---------------------------------------------------------------------------


def test_run_converged_rechecks_timeout_inside_lock(tmp_path, clock, monkeypatch):
    store = make_store(
        tmp_path, clock, timeoutSec=10.0, retry={"maxAttempts": 2, "baseSec": 0, "maxSec": 0, "jitter": 0}
    )
    token = reserve_and_spawn(store)
    store.task_result(1, "t1", "ok", reservation=token, confirmed_stopped=True)
    store.round_end("pass")
    assert termination_check(store, clock())["verdict"] == "converged"

    real_persist = LoopStore._persist
    crossed = False

    def persist(self, **kwargs):
        nonlocal crossed
        if not crossed:
            crossed = True
            clock.advance(11)
        return real_persist(self, **kwargs)

    monkeypatch.setattr(LoopStore, "_persist", persist)
    with pytest.raises(LoopStateError, match="runTimeout"):
        store.run_converged()
    # 同一实例不得声称已收敛
    assert store.state["status"] == "active"
    loaded = LoopStore(store.run_dir, now=clock).load()
    assert loaded.state["status"] == "active"


# ---------------------------------------------------------------------------
# 缺陷 4：缺 run.json 但有痕迹的目录 fail-closed
# ---------------------------------------------------------------------------


def _orphan_dir(tmp_path, name="orphan"):
    base = tmp_path / ".agents" / "runtime" / "loop"
    base.mkdir(parents=True, exist_ok=True)
    d = base / name
    d.mkdir()
    return d


def test_list_orphan_run_dirs_detects_traces(tmp_path):
    d = _orphan_dir(tmp_path)
    (d / EVENTS_FILENAME).write_text('{"type": "run_init"}\n', encoding="utf-8")
    (d / LOCK_FILENAME).write_text("", encoding="utf-8")
    assert list_orphan_run_dirs(tmp_path) == [d]


def test_list_run_dirs_ignores_orphan_but_find_active_runs_fails_closed(tmp_path):
    d = _orphan_dir(tmp_path)
    (d / EVENTS_FILENAME).write_text('{"type": "run_init"}\n', encoding="utf-8")
    assert list_run_dirs(tmp_path) == []
    active, corrupt = find_active_runs(tmp_path)
    assert active == []
    assert corrupt == [d]


def test_empty_new_dir_is_not_an_orphan(tmp_path):
    _orphan_dir(tmp_path, "empty")
    assert list_orphan_run_dirs(tmp_path) == []
    active, corrupt = find_active_runs(tmp_path)
    assert active == [] and corrupt == []


def test_dir_with_only_unrelated_file_is_not_orphan(tmp_path):
    d = _orphan_dir(tmp_path, "stray")
    (d / "notes.txt").write_text("x", encoding="utf-8")
    assert list_orphan_run_dirs(tmp_path) == []
    active, corrupt = find_active_runs(tmp_path)
    assert active == [] and corrupt == []


def test_lock_trace_alone_fails_closed(tmp_path):
    d = _orphan_dir(tmp_path, "lock-only")
    (d / LOCK_FILENAME).write_text("", encoding="utf-8")
    assert list_orphan_run_dirs(tmp_path) == [d]
    active, corrupt = find_active_runs(tmp_path)
    assert active == [] and corrupt == [d]


# ---------------------------------------------------------------------------
# 缺陷 5：--run-id 单一路径段校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", ".", "/abs/path", "a\\b"])
def test_default_run_dir_rejects_unsafe_run_id(tmp_path, bad):
    with pytest.raises(LoopStateError, match="run-id"):
        default_run_dir(tmp_path, bad)


@pytest.mark.parametrize("good", ["run-1", "20260101-run", "valid_id"])
def test_default_run_dir_accepts_single_segment(tmp_path, good):
    d = default_run_dir(tmp_path, good)
    assert d == tmp_path / ".agents" / "runtime" / "loop" / good
    assert d.parent == tmp_path / ".agents" / "runtime" / "loop"


# ---------------------------------------------------------------------------
# 缺陷 6：README 结果登记示例必须含 --confirmed-stopped
# ---------------------------------------------------------------------------


def test_readme_result_examples_require_confirmed_stopped():
    readme = Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text(encoding="utf-8")
    # 成功与失败两个登记示例都必须携带 --confirmed-stopped
    assert "task result ... --outcome ok --result" in text
    assert "--reservation <token> --confirmed-stopped" in text
    ok_line = next(line for line in text.splitlines() if "--outcome ok --result" in line)
    fail_line = next(line for line in text.splitlines() if "--outcome fail --error" in line)
    assert "--confirmed-stopped" in ok_line
    assert "--confirmed-stopped" in fail_line

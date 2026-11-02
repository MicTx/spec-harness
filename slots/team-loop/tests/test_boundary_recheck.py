"""Isolated regressions for loop boundaries; no host workers or models are invoked."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import loop_state
from loop_control import termination_check
from loop_state import LoopStateError, LoopStore, default_run_dir, write_stop_sentinel


HOOKS = Path(__file__).resolve().parents[1] / "hooks"


def make_store(tmp_path, clock, **config):
    settings = {"maxRounds": 2, "concurrency": 3, "budgetTasks": 4,
                "retry": {"maxAttempts": 1, "baseSec": 0, "maxSec": 0, "jitter": 0}}
    settings.update(config)
    store = LoopStore.init(tmp_path / "run", "goal", "until-converged", config=settings, now=clock)
    store.round_start("first")
    return store


def reserve(store, task_id, agent=None):
    token = store.task_reserve(1, task_id)
    if agent:
        store.task_spawn(1, task_id, agent, "sub-" + task_id, reservation=token)
    return token


def test_exhaustion_waits_for_reserved_and_attached_work_and_allows_next_round(tmp_path, clock):
    store = make_store(tmp_path, clock)
    for task_id in ("failed", "reserved", "pending"):
        store.task_add(1, task_id, "worker", "do work")
    failed = reserve(store, "failed", "a")
    reserved = reserve(store, "reserved")
    assert store.task_result(1, "failed", "fail", reservation=failed, confirmed_stopped=True) == "exhausted"
    assert termination_check(store, clock())["verdict"] == "continue"
    assert store.state["rounds"][0]["tasks"]["reserved"]["lastHeartbeat"] is None
    store.task_spawn(1, "reserved", "b", "sub-reserved", reservation=reserved)
    assert termination_check(store, clock())["verdict"] == "continue"
    with pytest.raises(LoopStateError, match="确认"):
        store.task_result(1, "reserved", "ok", reservation=reserved)
    pending = reserve(store, "pending", "c")
    store.task_result(1, "reserved", "ok", reservation=reserved, confirmed_stopped=True)
    store.task_result(1, "pending", "ok", reservation=pending, confirmed_stopped=True)
    assert termination_check(store, clock())["verdict"] == "continue"
    store.round_end("fail")
    assert termination_check(store, clock())["verdict"] == "continue"
    store.round_start("repair")
    store.task_add(2, "repair", "worker", "verify")
    token = store.task_reserve(2, "repair")
    store.task_spawn(2, "repair", "d", "sub-repair", reservation=token)
    store.task_result(2, "repair", "ok", reservation=token, confirmed_stopped=True)
    store.round_end("pass")
    assert termination_check(store, clock())["verdict"] == "converged"
    store.run_converged()


@pytest.mark.parametrize("max_rounds, expected", [(1, "stopped"), (2, "continue")])
def test_exhausted_task_cannot_make_a_passing_round_converge(tmp_path, clock, max_rounds, expected):
    store = make_store(tmp_path, clock, maxRounds=max_rounds)
    store.task_add(1, "failed", "worker", "work")
    token = reserve(store, "failed", "a")
    store.task_result(1, "failed", "fail", reservation=token, confirmed_stopped=True)
    store.round_end("pass")  # A recorded decision must not erase the actual failed task.
    assert termination_check(store, clock())["verdict"] == expected
    with pytest.raises(LoopStateError, match="尚未满足"):
        store.run_converged()


@pytest.mark.parametrize("hard_stop", ["STOP", "timeout"])
def test_real_hard_stop_wins_even_with_exhausted_and_live_work(tmp_path, clock, hard_stop):
    store = make_store(tmp_path, clock, timeoutSec=10)
    for task_id in ("failed", "live"):
        store.task_add(1, task_id, "worker", "work")
    failed = reserve(store, "failed", "a")
    reserve(store, "live", "b")
    store.task_result(1, "failed", "fail", reservation=failed, confirmed_stopped=True)
    if hard_stop == "STOP":
        write_stop_sentinel(store.run_dir, {"reason": "test"})
    else:
        clock.advance(11)
    verdict = termination_check(store, clock())
    assert verdict["verdict"] == "stopped"
    assert any(("STOP" if hard_stop == "STOP" else "runTimeout") in reason for reason in verdict["reasons"])
    with pytest.raises(LoopStateError, match="核实"):
        store.run_failed("stop")
    assert store.state["rounds"][0]["tasks"]["live"]["status"] == "in_flight"


def call_hook(name, event, root):
    return subprocess.run([sys.executable, str(HOOKS / (name + ".py"))],
                          input=json.dumps({"hook_event_name": event, "cwd": str(root),
                                            "teammate_name": "agent"}),
                          text=True, capture_output=True, timeout=20)


@pytest.mark.parametrize("attached", [False, True])
def test_stop_hook_waits_for_budgeted_work_after_exhaustion(tmp_path, attached):
    run_dir = default_run_dir(tmp_path, "run")
    store = LoopStore.init(run_dir, "goal", "until-converged",
                           config={"maxRounds": 2, "budgetTasks": 3,
                                   "retry": {"maxAttempts": 1}})
    store.round_start("first")
    for task in ("failed", "live"):
        store.task_add(1, task, "worker", "work")
    failed = reserve(store, "failed", "a")
    live = reserve(store, "live", "b" if attached else None)
    store.task_result(1, "failed", "fail", reservation=failed, confirmed_stopped=True)
    before = (run_dir / "run.json").read_bytes()
    guard = call_hook("loop_stop_guard", "Stop", tmp_path)
    assert guard.returncode == 0
    reason = json.loads(guard.stdout)["reason"]
    assert "r1/live" in reason and ("wait" if attached else "核对未登记") in reason
    assert "finish failed" not in reason and (run_dir / "run.json").read_bytes() == before
    if not attached:
        store.task_spawn(1, "live", "b", "sub-live", reservation=live)
    store.task_result(1, "live", "ok", reservation=live, confirmed_stopped=True)
    assert "round end --decision fail" in json.loads(call_hook("loop_stop_guard", "Stop", tmp_path).stdout)["reason"]
    store.round_end("fail")
    assert "round start" in json.loads(call_hook("loop_stop_guard", "Stop", tmp_path).stdout)["reason"]


@pytest.mark.parametrize("damage", ["state_utf8", "event_utf8", "counters", "tasksSpawned",
                                    "taskResults", "attempts", "currentRound"])
def test_damaged_state_fails_closed_in_both_hooks(tmp_path, damage):
    run_dir = default_run_dir(tmp_path, "run")
    store = LoopStore.init(run_dir, "goal", "until-converged")
    store.round_start("r")
    store.task_add(1, "t", "worker", "work")
    store.task_spawn(1, "t", "agent", "submission")
    path = run_dir / "run.json"
    if damage == "state_utf8":
        path.write_bytes(b"\xff")
    elif damage == "event_utf8":
        (run_dir / "events.jsonl").write_bytes(b"\xff")
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        if damage in ("counters", "currentRound"):
            del data[damage]
        elif damage == "attempts":
            del data["rounds"][0]["tasks"]["t"]["attempts"]
        else:
            del data["counters"][damage]
        path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LoopStateError):
        LoopStore(run_dir).load()
    stop = call_hook("loop_stop_guard", "Stop", tmp_path)
    assert stop.returncode == 0 and json.loads(stop.stdout)["decision"] == "block"
    assert "损坏" in json.loads(stop.stdout)["reason"] and "Traceback" not in stop.stderr
    teammate = call_hook("loop_teammate_gate", "TeammateIdle", tmp_path)
    assert teammate.returncode == 2 and "损坏" in teammate.stderr and "Traceback" not in teammate.stderr


@pytest.mark.skipif(not hasattr(os, "link"), reason="hard links unavailable")
@pytest.mark.parametrize("swap_at_replace", [False, True])
def test_stop_write_does_not_truncate_hardlinked_external_file(tmp_path, clock, monkeypatch, swap_at_replace):
    store = make_store(tmp_path, clock)
    outside = tmp_path / "outside-stop"
    outside.write_bytes(b"do not change")
    target = store.run_dir / "STOP"
    if swap_at_replace:
        target.write_text("{}", encoding="utf-8")
        real_replace = os.replace

        def swap(source, destination):
            if Path(destination) == target:
                target.unlink()
                os.link(outside, target)
            return real_replace(source, destination)

        monkeypatch.setattr(loop_state.os, "replace", swap)
    else:
        os.link(outside, target)
    write_stop_sentinel(store.run_dir, {"reason": "safe stop"})
    assert outside.read_bytes() == b"do not change"
    assert json.loads(target.read_text(encoding="utf-8"))["reason"] == "safe stop"


@pytest.mark.skipif(not hasattr(os, "link"), reason="hard links unavailable")
def test_stop_cli_does_not_truncate_hardlinked_external_file(tmp_path, clock):
    store = make_store(tmp_path, clock)
    outside = tmp_path / "outside-stop"
    outside.write_bytes(b"external bytes")
    os.link(outside, store.run_dir / "STOP")
    command = HOOKS.parent / "scripts" / "loop_control.py"
    result = subprocess.run([sys.executable, str(command), "stop", "--run-dir", str(store.run_dir)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and json.loads(result.stdout)["ok"] is True
    assert outside.read_bytes() == b"external bytes"
    assert json.loads((store.run_dir / "STOP").read_text(encoding="utf-8"))["source"] == "loop_control stop"


@pytest.mark.skipif(not hasattr(os, "link"), reason="hard links unavailable")
@pytest.mark.parametrize("swap_at_open", [False, True])
def test_event_append_rejects_hardlink_even_if_swapped_at_open(tmp_path, clock, monkeypatch, swap_at_open):
    store = make_store(tmp_path, clock)
    outside = tmp_path / "outside-events"
    outside.write_bytes(b'{"type":"outside"}\n')
    event_path = store.events_path
    if swap_at_open:
        real_open = os.open
        swapped = False

        def swap(path, flags, *args, **kwargs):
            nonlocal swapped
            if Path(path) == event_path and not swapped:
                swapped = True
                event_path.unlink()
                os.link(outside, event_path)
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(loop_state.os, "open", swap)
    else:
        event_path.unlink()
        os.link(outside, event_path)
    with pytest.raises(LoopStateError):
        store._emit("probe")
    assert outside.read_bytes() == b'{"type":"outside"}\n'


@pytest.mark.skipif(not hasattr(os, "link"), reason="hard links unavailable")
def test_event_link_created_after_validation_cannot_receive_new_event(tmp_path, clock, monkeypatch):
    store = make_store(tmp_path, clock)
    outside = tmp_path / "linked-after-validation"
    previous = store.events_path.read_bytes()
    real_copy = loop_state.shutil.copyfileobj

    def link_during_copy(source, destination):
        os.link(store.events_path, outside)
        return real_copy(source, destination)

    monkeypatch.setattr(loop_state.shutil, "copyfileobj", link_during_copy)
    store._emit("probe")
    assert outside.read_bytes() == previous
    assert store.events_path.read_bytes() == previous + b'{"ts": 1000000.0, "type": "probe"}\n'

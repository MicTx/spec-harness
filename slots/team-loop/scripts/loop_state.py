#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""team-loop run/round/task state machine and its command-line entrypoint."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager, suppress
from functools import partialmethod
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms fail closed on write
    fcntl = None

RUN_FILENAME, EVENTS_FILENAME, STOP_FILENAME, LOCK_FILENAME = "run.json", "events.jsonl", "STOP", ".lock"
ACTIVE_STATUSES = ("active",)
TERMINAL_STATUSES = ("converged", "failed", "interrupted", "cancelled")
TASK_STATUSES = ("pending", "in_flight", "done", "failed", "cancelled")
ROUND_DECISIONS = ("pass", "fail")
TASK_ACTIONS = ("add", "reserve", "spawn", "heartbeat", "report", "result", "abandon", "backoff", "cancel")
MODES = ("until-converged", "fixed-rounds", "batch-fanout")
# fmt: off
CONFIG = {"maxRounds": 8, "concurrency": 3, "stalenessSec": 300.0, "timeoutSec": 7200.0, "budgetTasks": 64,
          "retry": {"maxAttempts": 3, "baseSec": 5.0, "maxSec": 60.0, "jitter": 0.3}}
# fmt: on
CLEARED_FIELDS = ("agentId", "submissionId", "lastError", "backoffUntil", "workerReport", "lastHeartbeat")


class LoopStateError(Exception):
    """Invalid input or inconsistent persisted state."""


def _need(condition: Any, message: str) -> None:
    if not condition:
        raise LoopStateError(message)


utc_now = time.time


@contextmanager
def _protocol_file(path: Path, mode: str) -> Iterator[TextIO]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    except OSError as exc:
        raise LoopStateError(f"无法检查协议文件 {path}: {exc}") from exc
    _need(info is None or stat.S_ISREG(info.st_mode), f"协议文件必须是普通文件: {path}")
    _need(info is None or info.st_nlink == 1, f"协议文件不得是硬链接: {path}")
    try:
        with open(path, mode, encoding="utf-8") as stream:
            yield stream
    except UnicodeError as exc:
        raise LoopStateError(f"协议文件文本无法编码 {path}: {exc}") from exc


def _atomic_write_json(path: Path, payload: Any) -> None:
    _need(not path.is_symlink(), f"协议文件不得是 symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise


def new_run_id(goal: str, now: float) -> str:
    slug = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", goal).strip("-")[:24] or "run"
    return f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{slug}-{uuid.uuid4().hex[:6]}"


def default_run_dir(root: Path, run_id: str) -> Path:
    ok = isinstance(run_id, str) and run_id not in ("", ".", "..")
    _need(ok and not any(c in run_id for c in ("/", "\\", "\x00")), "run-id 非法（单一路径段）")
    return root / ".agents" / "runtime" / "loop" / run_id


def find_run_root(start: Path) -> Path | None:
    current = start.resolve(strict=False)
    for candidate in (current, *current.parents):
        if candidate.name == ".agents":
            return candidate.parent
        if (candidate / ".agents").is_dir() or (candidate / ".git").exists():
            return candidate
    return None


def _run_dirs(root: Path) -> list[Path]:
    return [p for p in (root / ".agents" / "runtime" / "loop").glob("*") if p.is_dir() and not p.is_symlink()]


def list_run_dirs(root: Path) -> list[Path]:
    return sorted(p for p in _run_dirs(root) if (p / RUN_FILENAME).exists())


def list_orphan_run_dirs(root: Path) -> list[Path]:
    traces = (EVENTS_FILENAME, LOCK_FILENAME)
    missing = (p for p in _run_dirs(root) if not (p / RUN_FILENAME).exists())
    return sorted(p for p in missing if any((p / name).exists() for name in traces))


def _validate_config(config: dict[str, Any]) -> None:
    retry = config.get("retry")
    _need(isinstance(retry, dict), "配置必须含 retry 对象")
    ints = [config.get(n) for n in ("maxRounds", "concurrency", "budgetTasks")] + [retry.get("maxAttempts")]
    _need(all(type(v) is int and v >= 1 for v in ints), "配置整数项必须为正")
    _need(retry["maxSec"] >= retry["baseSec"], "retry.maxSec 不能小于 retry.baseSec")


def backoff_delay(attempt: int, retry_cfg: dict[str, Any], rng: random.Random) -> float:
    ceiling = min(float(retry_cfg.get("maxSec", 60)), float(retry_cfg.get("baseSec", 5)) * 2 ** max(0, attempt - 1))
    jitter = float(retry_cfg.get("jitter", 0.3))
    raw = rng.uniform(0, ceiling) if jitter > 0 else ceiling
    return round(rng.uniform(raw * (1 - jitter), raw), 3) if jitter > 0 and raw > 0 else raw


def _validate_state(state: Any) -> None:
    ok = isinstance(state, dict) and isinstance(state.get("counters"), dict) and isinstance(state.get("rounds"), list)
    ok = ok and isinstance(state.get("config"), dict) and type(state.get("currentRound")) is int
    ok = ok and state.get("mode") in MODES and state.get("status") in ACTIVE_STATUSES + TERMINAL_STATUSES
    counters = state.get("counters") if isinstance(state, dict) else None
    ok = ok and type(counters) is dict and all(type(counters.get(n)) is int for n in ("tasksSpawned", "taskResults"))
    _need(ok, "run 状态结构非法")
    _validate_config(state["config"])
    for rnd in state["rounds"]:
        ok = isinstance(rnd, dict) and isinstance(rnd.get("tasks"), dict) and isinstance(rnd.get("status"), str)
        _need(ok and type(rnd.get("index")) is int, "run 状态 round 结构非法")
        for task in rnd["tasks"].values():
            ok = isinstance(task, dict) and task.get("status") in TASK_STATUSES
            ok = ok and type(task.get("attempts")) is int
            _need(ok and isinstance(task.get("workerReport", {}), (dict, type(None))), "任务结构非法")


class LoopStore:
    def __init__(self, run_dir: Path, now: Callable[[], float] = utc_now) -> None:
        self.run_dir, self._now = Path(run_dir), now
        self.state_path, self.events_path = self.run_dir / RUN_FILENAME, self.run_dir / EVENTS_FILENAME
        self.state: dict[str, Any] | None = None

    def _read_state(self, missing: bool = False) -> dict[str, Any] | None:
        if missing and not os.path.lexists(self.state_path):
            return None
        _need(not self.run_dir.is_symlink() and self.run_dir.is_dir(), "run 目录非法")
        _need(not self.state_path.is_symlink() and self.state_path.is_file(), "run 状态文件不存在或非法")
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            raise LoopStateError(f"run 状态损坏: {exc}") from exc
        _validate_state(payload)
        return payload

    def load(self) -> LoopStore:
        self.state = self._read_state()
        self.events()
        return self

    # fmt: off
    @classmethod
    def init(cls, run_dir: Path, goal: str, mode: str, config: dict[str, Any] | None = None,
             now: Callable[[], float] = utc_now) -> LoopStore:
        # fmt: on
        store = cls(run_dir, now)
        _need(mode in MODES, "mode 非法")
        _need(not store.run_dir.is_symlink(), "run 目录不得是 symlink")
        _need(not store.run_dir.exists() or store.run_dir.is_dir() and not any(store.run_dir.iterdir()), "run 目录非空")
        _need(config is None or isinstance(config, dict) and isinstance(config.get("retry", {}), dict), "配置非法")
        config, defaults = config or {}, json.loads(json.dumps(CONFIG))
        merged = {**defaults, **config, "retry": {**defaults["retry"], **config.get("retry", {})}}
        _validate_config(merged)
        created = now()
        # fmt: off
        store.state = dict(
            runId=store.run_dir.name, goal=goal, mode=mode, status="active", createdAt=created, updatedAt=created,
            currentRound=0, config=merged, rounds=[], counters=dict(tasksSpawned=0, taskResults=0),
        )
        # fmt: on
        store.run_dir.mkdir(parents=True, exist_ok=True)
        store._persist(event="run_init", goal=goal, mode=mode)
        return store

    def _emit(self, event_type: str, **fields: Any) -> None:
        with _protocol_file(self.events_path, "a") as stream:
            stream.write(json.dumps({"ts": self._now(), "type": event_type, **fields}, ensure_ascii=False) + "\n")

    def events(self) -> list[dict[str, Any]]:
        if not os.path.lexists(self.events_path):
            return []
        with _protocol_file(self.events_path, "r") as stream:
            try:
                events = [json.loads(line) for line in stream if line.strip()]
            except ValueError as exc:
                raise LoopStateError(f"事件日志损坏: {exc}") from exc
        _need(all(isinstance(e, dict) and isinstance(e.get("type"), str) for e in events), "事件日志结构非法")
        return events

    def _persist(self, *, stop: bool = False, timeout: bool = False, event: str | None = None, **extra: Any) -> None:
        # lock -> admission recheck -> CAS -> atomic replace -> event; restore on failure
        try:
            _need(fcntl is not None, "状态写入需要跨进程 flock；当前平台不可用")
            self.run_dir.mkdir(parents=True, exist_ok=True)
            with _protocol_file(self.run_dir / LOCK_FILENAME, "a+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                _need(not stop or not os.path.lexists(self.run_dir / STOP_FILENAME), "STOP 哨兵存在，拒绝变更")
                timed_out = self._now() - self.state["createdAt"] > self.state["config"]["timeoutSec"]
                _need(not timeout or not timed_out, "runTimeout 已超时，拒绝变更")
                disk = self._read_state(missing=True)
                revision = disk.get("_revision", 0) if disk is not None else None
                _need(revision == self.state.get("_revision"), "run 已被其他写入者更新，请重新 load 后再试")
                self.state.update(updatedAt=self._now(), _revision=(revision or 0) + 1)
                _atomic_write_json(self.state_path, self.state)
                if event:
                    self._emit(event, **extra)
        except BaseException as exc:
            self.state = None
            with suppress(LoopStateError):
                self.state = self._read_state(missing=True)
            if isinstance(exc, OSError):
                raise LoopStateError(f"无法写入 run 状态: {exc}") from exc
            raise

    def _round(self, index: int, open_only: bool = False) -> dict[str, Any]:
        _need(self.state["status"] == "active", "run 已处于终态，拒绝变更")
        rnd = next((r for r in self.state["rounds"] if r["index"] == index), None)
        _need(rnd is not None, f"round {index} 不存在")
        _need(not open_only or rnd["status"] == "open", f"round {index} 已关闭")
        return rnd

    def _task(self, index: int, task_id: str, open_only: bool = False) -> dict[str, Any]:
        task = self._round(index, open_only).get("tasks", {}).get(task_id)
        _need(task is not None, f"task {task_id} 不存在于 round {index}")
        return task

    def _tasks(self, *statuses: str) -> dict[str, Any]:
        # fmt: off
        return {
            f"r{rnd['index']}/{tid}": task for rnd in self.state["rounds"]
            for tid, task in rnd.get("tasks", {}).items() if task["status"] in statuses
        }
        # fmt: on

    def round_start(self, goal: str) -> int:
        _need(self.state["status"] == "active", "run 已处于终态，拒绝变更")
        _need(not any(r["status"] == "open" for r in self.state["rounds"]), "已有 open round，先 close 再开新轮")
        index = len(self.state["rounds"]) + 1
        _need(index <= self.state["config"]["maxRounds"], "超过 maxRounds")
        self.state["rounds"].append({"index": index, "goal": goal, "status": "open", "tasks": {}, "decision": None})
        self.state["currentRound"] = index
        self._persist(event="round_start", round=index, goal=goal)
        return index

    def round_end(self, decision: str, note: str = "") -> None:
        _need(self.state["status"] == "active", "run 已处于终态，拒绝变更")
        _need(decision in ROUND_DECISIONS, "round decision 非法")
        rnd = next((r for r in reversed(self.state["rounds"]) if r["status"] == "open"), None)
        _need(rnd is not None, "没有 open round 可结束")
        _need(all(t["status"] in ("done", "failed", "cancelled") for t in rnd["tasks"].values()), "仍有非终态任务")
        rnd.update(status="closed", decision=decision)
        self._persist(event="round_end", round=rnd["index"], decision=decision, note=note)

    def _dispatch(self, index: int, task_id: str, task: dict[str, Any], token: str | None) -> None:
        cfg = self.state["config"]
        self._round(index, True)
        _need(task["status"] == "pending", f"task {task_id} 状态不可 spawn")
        _need(task["attempts"] < cfg["retry"]["maxAttempts"], "task 已耗尽 maxAttempts")
        if task["attempts"] and task.get("lastError") and task.get("backoffUntil") is None:
            task["backoffUntil"] = self._now() + backoff_delay(task["attempts"], cfg["retry"], random.Random())
            self._persist(stop=True, timeout=True)  # Persist legacy repair before rejecting dispatch.
        _need(task.get("backoffUntil") is None or self._now() >= task["backoffUntil"], "task 仍在 backoff")
        _need(len(self._tasks("in_flight")) < cfg["concurrency"], "并发已满，拒绝 spawn")
        _need(self.state["counters"]["tasksSpawned"] < cfg["budgetTasks"], "budgetTasks 已用尽")
        used = [*task.get("usedSubmissionIds", [])]
        if task.get("submissionId") not in (None, *used):
            used.append(task["submissionId"])
        # fmt: off
        task.update(
            **dict.fromkeys(CLEARED_FIELDS), usedSubmissionIds=used, status="in_flight",
            attempts=task["attempts"] + 1, reservedAt=self._now(),
            reservationToken=token, dispatchPhase="reserved" if token else "attached",
        )
        # fmt: on
        self.state["counters"]["tasksSpawned"] += 1

    def _identity(self, task: dict[str, Any], submission: Any, reservation: Any, label: str) -> None:
        _need(task["status"] == "in_flight" and task.get("dispatchPhase") != "reserved", f"task 状态不可{label}")
        _need(submission is None or submission == task.get("submissionId"), f"submission 不匹配，拒绝过期{label}")
        _need(reservation is None or reservation == task.get("reservationToken"), f"预约 token 不匹配，拒绝过期{label}")
        current = submission is not None or reservation is not None
        _need(current or task["attempts"] <= 1, f"重试{label}需要当前 --submission 或 --reservation")

    @staticmethod
    def _attached(task: dict[str, Any], token: str, agent: str, submission: str) -> bool:
        keys = ("status", "dispatchPhase", "reservationToken", "agentId", "submissionId")
        return tuple(task.get(key) for key in keys) == ("in_flight", "attached", token, agent, submission)

    def task_add(self, round_index: int, task_id: str, agent_type: str, description: str) -> None:
        rnd = self._round(round_index, True)
        _need(task_id not in rnd["tasks"], f"task {task_id} 已存在")
        # fmt: off
        rnd["tasks"][task_id] = {
            **dict.fromkeys(("submissionId", "agentId", "lastError", "backoffUntil", "lastHeartbeat", "result")),
            "id": task_id, "agentType": agent_type, "description": description, "status": "pending", "attempts": 0,
        }
        # fmt: on
        self._persist(event="task_add", round=round_index, task=task_id, agentType=agent_type)

    def task_reserve(self, round_index: int, task_id: str) -> str:
        task = self._task(round_index, task_id)
        token = uuid.uuid4().hex
        self._dispatch(round_index, task_id, task, token)
        self._persist(stop=True, timeout=True, event="task_reserved", round=round_index,
                      task=task_id, attempt=task["attempts"])
        return token

    # fmt: off
    def task_spawn(self, round_index: int, task_id: str, agent_id: str, submission_id: str,
                   reservation: str | None = None) -> None:
        task = self._task(round_index, task_id)
        _need(all(isinstance(x, str) and x for x in (agent_id, submission_id)), "spawn 需要非空 agent 与 submission")
        reserved = task["status"] == "in_flight" and task.get("dispatchPhase") == "reserved"
        if reservation and self._attached(task, reservation, agent_id, submission_id):
            self.load()
            _need(self._attached(self._task(round_index, task_id), reservation, agent_id, submission_id),
                  "预约已变更，拒绝过期挂载")
            return None
        _need((reservation == task.get("reservationToken")) if reserved else not reservation, "预约 token 不匹配")
        _need(submission_id not in [task.get("submissionId"), *task.get("usedSubmissionIds", [])],
              "submission 已被之前登记使用")
        if not reserved:
            self._dispatch(round_index, task_id, task, None)
        task.update(
            dispatchPhase="attached", agentId=agent_id, submissionId=submission_id, lastHeartbeat=self._now(),
            usedSubmissionIds=[*task.get("usedSubmissionIds", []), submission_id],
        )
        self._persist(event="task_spawn", stop=not reserved, timeout=not reserved, round=round_index,
                      task=task_id, attempt=task["attempts"], agent=agent_id,
                      submission=submission_id, reserved=reserved)
    # fmt: on

    # fmt: off
    def task_heartbeat(self, round_index: int, task_id: str, *, source: str | None = None,
                       submission: str | None = None, reservation: str | None = None) -> None:
        task = self._task(round_index, task_id)
        if source is None:
            _need(task["status"] == "in_flight" and task.get("dispatchPhase") != "reserved", "task 状态不可心跳")
            return None  # Coordinator activity is not worker liveness.
        self._identity(task, submission, reservation, "心跳")
        task.update(lastHeartbeat=self._now(), heartbeatSource=source)
        self._persist(round=round_index, task=task_id)
    # fmt: on

    # fmt: off
    def task_report(self, round_index: int, task_id: str, report: dict[str, Any], *,
                    submission: str | None = None, reservation: str | None = None) -> None:
        task = self._task(round_index, task_id)
        self._identity(task, submission, reservation, "报告")
        _need(isinstance(report, dict) and report, "task report 需要非空 JSON 对象")
        task["workerReport"] = report
        self._persist(event="task_report", round=round_index, task=task_id, attempt=task["attempts"])
    # fmt: on

    # fmt: off
    def task_result(self, round_index: int, task_id: str, outcome: str, error: str = "", result: Any = None, *,
                    submission: str | None = None, reservation: str | None = None,
                    confirmed_stopped: bool = False) -> str:
        task = self._task(round_index, task_id)
        self._identity(task, submission, reservation, "结果")
        _need(outcome in ("ok", "fail"), f"outcome 非法: {outcome!r}")
        return self._settle(task, outcome == "ok", error, result, confirmed_stopped, round_index, task_id)
    # fmt: on

    def task_abandon(self, round_index: int, task_id: str, reservation: str, *, confirmed_stopped: bool) -> str:
        task = self._task(round_index, task_id)
        _need(task["status"] == "in_flight" and task.get("dispatchPhase") == "reserved", "仅可放弃未登记的预约")
        _need(reservation and reservation == task.get("reservationToken"), "预约 token 不匹配")
        return self._settle(task, False, "", None, confirmed_stopped, round_index, task_id)

    # fmt: off
    def _settle(self, task: dict[str, Any], ok: bool, error: str, result: Any, confirmed_stopped: bool,
                round_index: int, task_id: str) -> str:
        _need(confirmed_stopped, "须确认并核实宿主 worker 已停止，传入 --confirmed-stopped")
        retry = self.state["config"]["retry"]
        outcome = "done" if ok else "retry" if task["attempts"] < retry["maxAttempts"] else "exhausted"
        task["status"] = {"done": "done", "retry": "pending", "exhausted": "failed"}[outcome]
        self.state["counters"]["taskResults"] += 1
        fields: dict[str, Any] = dict(round=round_index, task=task_id)
        if ok:
            task["result"] = result
        else:
            task["lastError"] = error or "预约放弃/unspecified failure"
            fields["error"] = task["lastError"]
        if outcome == "retry":
            task["backoffUntil"] = self._now() + backoff_delay(task["attempts"], retry, random.Random())
            self._persist(event="task_retry_scheduled", attempt=task["attempts"], **fields)
        else:
            self._persist(event="task_result", outcome="ok" if ok else "exhausted", **fields)
        return outcome
    # fmt: on

    def task_backoff_until(self, round_index: int, task_id: str, until: float) -> None:
        task = self._task(round_index, task_id)
        _need(task["status"] == "pending" and math.isfinite(until), "backoff 需要 pending 任务与有限时间")
        task["backoffUntil"] = max(task.get("backoffUntil") or 0, until)
        self._persist(round=round_index, task=task_id)

    def task_cancel(self, round_index: int, task_id: str, reason: str, *, confirmed_stopped: bool = False) -> None:
        task = self._task(round_index, task_id)
        _need(task["status"] in ("pending", "in_flight"), "task 状态不可取消")
        _need(confirmed_stopped or task["status"] != "in_flight", "须确认宿主已停止（--confirmed-stopped）")
        task.update(status="cancelled", lastError=reason)
        self._persist(event="task_cancelled", round=round_index, task=task_id, reason=reason)

    # fmt: off
    def _finish(self, status: str, text: str = "", *, confirmed_stopped: bool = False) -> Any:
        from loop_control import termination_check

        _need(self.state["status"] == "active", "run 已处于终态，拒绝变更")
        affected = list(self._tasks("in_flight"))
        if status == "converged":
            _need(termination_check(self, self._now())["verdict"] == "converged", "尚未满足收敛条件")
        elif status in ("failed", "cancelled"):
            _need(confirmed_stopped or not affected, "须确认并核实宿主 worker 已停止，传入 --confirmed-stopped")
        fields = {"inFlight": affected} if status == "interrupted" else {}
        fields |= {"summary": text} if status == "converged" else {"reason": text}
        self.state["status"] = status
        self._persist(event=f"run_{status}", stop=status == "converged", timeout=status == "converged", **fields)
        return affected if status == "interrupted" else None

    for _status in ("converged", "failed", "cancelled", "interrupted"):
        locals()["run_" + _status] = partialmethod(_finish, _status)
    # fmt: on

    def resume(self) -> dict[str, Any]:
        _need(self.state["status"] in ("active", "interrupted"), "run 终态不可恢复")
        known = {f"r{rnd['index']}/{tid}" for rnd in self.state["rounds"] for tid in rnd["tasks"]}
        for event in self.events():
            key = f"r{event.get('round')}/{event.get('task')}"
            _need(event.get("type") != "task_spawn" or key in known, f"事件日志引用了不存在的任务 {key}，状态不一致")
        if self.state["status"] == "interrupted":
            self.state["status"] = "active"
            self._persist(event="run_resumed", fromStatus="interrupted")
        in_flight = self._tasks("in_flight")
        groups: dict[str, list[str]] = {"pending": list(self._tasks("pending")), "reserved": [], "inFlight": []}
        for key, item in in_flight.items():
            groups["reserved" if item.get("dispatchPhase") == "reserved" else "inFlight"].append(key)
        open_rounds = [r["index"] for r in self.state["rounds"] if r["status"] == "open"]
        # fmt: off
        hints = {"pending": "reserve：先预约 pending 任务 ", "reserved": "核对宿主预约并登记或确认停止后 abandon：",
                 "inFlight": "wait 在途任务并记录 result；确认停止后 stale --reap --task rN/id --confirmed-stopped："}
        # fmt: on
        actions = ["round_start：开启下一轮"] if not open_rounds else []
        actions.extend(hints[name] + ", ".join(keys) for name, keys in groups.items() if keys)
        return {"status": self.state["status"], "openRounds": open_rounds, **groups, "nextActions": actions}

    def summary(self) -> dict[str, Any]:
        st = self.state["status"]
        # fmt: off
        return {**{k: self.state[k] for k in ("runId", "goal", "mode", "status", "currentRound")},
                "active": st in ACTIVE_STATUSES, "converged": st == "converged", "rounds": len(self.state["rounds"]),
                "inFlight": [*self._tasks("in_flight")], "runDir": str(self.run_dir)}
        # fmt: on


def write_stop_sentinel(run_dir: Path, payload: dict[str, Any]) -> None:
    run_dir = Path(run_dir)
    _need(not run_dir.is_symlink() and run_dir.is_dir(), "run 目录必须是非 symlink 目录")
    _need(fcntl is not None, "STOP 写入需要跨进程 flock；当前平台不可用")
    try:
        with _protocol_file(run_dir / LOCK_FILENAME, "a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            _atomic_write_json(run_dir / STOP_FILENAME, payload)
    except OSError as exc:
        raise LoopStateError(f"无法写入 STOP 哨兵: {exc}") from exc


def _json_arg(raw: str | None) -> Any:
    try:
        return json.loads(Path(raw[1:]).read_text(encoding="utf-8") if raw.startswith("@") else raw) if raw else None
    except (OSError, UnicodeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"无效 JSON: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loop_state.py", description=__doc__)
    parser.add_argument("command", choices=("init", "status", "resume", "events", "round", "task", "finish"))
    parser.add_argument("action", nargs="?")
    # fmt: off
    for flag in ("--run-dir", "--goal", "--run-id", "--agent-type", "--description", "--submission",
                 "--reservation", "--error", "--reason", "--summary"):
        parser.add_argument(flag)
    # fmt: on
    parser.add_argument("--mode", choices=MODES, default=MODES[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", type=_json_arg)
    parser.add_argument("--round", dest="round_index", type=int)
    parser.add_argument("--id", dest="task_id")
    parser.add_argument("--agent", dest="agent_id")
    parser.add_argument("--source", choices=("worker", "host"))
    parser.add_argument("--outcome", choices=("ok", "fail"))
    parser.add_argument("--until", type=float)
    parser.add_argument("--result", type=_json_arg)
    parser.add_argument("--decision", choices=ROUND_DECISIONS)
    parser.add_argument("--confirmed-stopped", action="store_true")
    return parser


def _task_cli(store: LoopStore, args: argparse.Namespace) -> dict[str, Any]:
    a, i, t = args.action, args.round_index, args.task_id
    _need(a in TASK_ACTIONS, f"未知 task action: {a!r}")
    _need(i is not None and t, "task 需要 --round 与 --id")
    sub, res, stopped = args.submission, args.reservation, args.confirmed_stopped
    if a == "add":
        store.task_add(i, t, args.agent_type or "", args.description or "")
    elif a == "reserve":
        return {"reservation": store.task_reserve(i, t)}
    elif a == "spawn":
        store.task_spawn(i, t, args.agent_id or "", sub or "", reservation=res)
        return {"ok": True, "warning": None if res else "旧式事后登记无法约束宿主启动竞态；请先 reserve"}
    elif a == "heartbeat":
        store.task_heartbeat(i, t, source=args.source, submission=sub, reservation=res)
        return {"ok": True, "workerLivenessUpdated": bool(args.source)}
    elif a == "report":
        store.task_report(i, t, args.result, submission=sub, reservation=res)
        return {"ok": True, "status": "in_flight"}
    elif a == "result":
        # fmt: off
        outcome = store.task_result(i, t, args.outcome or "", error=args.error or "", result=args.result,
                                    submission=sub, reservation=res, confirmed_stopped=stopped)
        # fmt: on
        return {"outcome": outcome}
    elif a == "abandon":
        _need(res, "task abandon 需要 --reservation")
        return {"outcome": store.task_abandon(i, t, res, confirmed_stopped=stopped)}
    elif a == "backoff":
        _need(args.until is not None, "task backoff 需要 --until")
        store.task_backoff_until(i, t, args.until)
    else:
        store.task_cancel(i, t, args.reason or "", confirmed_stopped=stopped)
    return {"ok": True}


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "init":
            _need(args.goal, "--goal is required（必填）")
            root = find_run_root(Path(args.root)) or Path(args.root).resolve()
            run_id = args.run_id or new_run_id(args.goal, utc_now())
            directory = Path(args.run_dir) if args.run_dir else default_run_dir(root, run_id)
            output = LoopStore.init(directory, args.goal, args.mode, args.config).summary()
        else:
            _need(args.run_dir, "--run-dir is required（必填）")
            store = LoopStore(Path(args.run_dir)).load()
            if args.command == "task":
                output = _task_cli(store, args)
            elif args.command == "round" and args.action == "start":
                output = {"round": store.round_start(args.goal or "round auto")}
            elif args.command == "round":
                store.round_end(args.decision)
                output = {"ok": True}
            elif args.command == "finish":
                _need(args.action in TERMINAL_STATUSES, f"未知 finish status: {args.action!r}")
                text = (args.summary or "") if args.action == "converged" else (args.reason or "unspecified")
                value = getattr(store, "run_" + args.action)(text, confirmed_stopped=args.confirmed_stopped)
                output = {"affected": value} if args.action == "interrupted" else {"ok": True}
            else:
                output = getattr(store, "summary" if args.command == "status" else args.command)()
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except LoopStateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

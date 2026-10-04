#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""agents-team-loop 状态机：run/round/task 生命周期、检查点、事件日志与恢复。

设计对齐（见 references/prior-art.md）：
- LangGraph checkpointer：每次事件后原子落盘 run.json，中断后可从检查点 resume。
- Claude Code agent teams 共享任务列表：任务状态持久化，恢复会话保留任务。
- AutoGen ExternalTermination：run 目录下的 STOP 哨兵文件提供外部程序化停止。

状态模型：
  run    : active -> converged | failed | interrupted | cancelled
  round  : open -> closed(pass|fail)
  task   : pending -> in_flight -> done | failed
           failed --(attempts < max, 退避到期)--> pending
           任何态 --interrupt--> 记录在案，resume 后回 pending/in_flight 语义由 agent 重新 spawn

磁盘布局（run 目录 = <root>/.agents/runtime/loop/<run-id>/）：
  run.json       配置 + 状态（唯一真源，原子写）
  events.jsonl   追加式事件日志（审计 + 恢复校验）
  STOP           外部停止哨兵（存在即触发终止）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the single-process fallback
    fcntl = None

RUN_FILENAME = "run.json"
EVENTS_FILENAME = "events.jsonl"
STOP_FILENAME = "STOP"

ACTIVE_STATUSES = ("active",)
TERMINAL_STATUSES = ("converged", "failed", "interrupted", "cancelled")
TASK_STATUSES = ("pending", "in_flight", "done", "failed", "cancelled")
ROUND_DECISIONS = ("pass", "fail")


class LoopStateError(Exception):
    """状态机被非法使用或磁盘状态不一致。"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _assert_regular_nosymlink(path: Path, *, allow_missing: bool = True) -> None:
    """Reject symlinks and non-regular files at a protocol boundary."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        raise LoopStateError(f"协议文件不存在: {path}")
    except OSError as exc:
        raise LoopStateError(f"无法检查协议文件 {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise LoopStateError(f"协议文件不得是 symlink: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise LoopStateError(f"协议文件必须是普通文件: {path}")


def _assert_directory_nosymlink(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise LoopStateError(f"无法检查 run 目录 {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise LoopStateError(f"run 目录必须是非 symlink 目录: {path}")


@contextmanager
def _exclusive_file_lock(path: Path):
    """Serialize state/event writes across processes where flock is available."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_regular_nosymlink(path)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise LoopStateError(f"无法打开 run 写锁 {path}: {exc}") from exc
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    """Persist directory entry updates on platforms that support directory fsync."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def utc_now() -> float:
    return time.time()


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_regular_nosymlink(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _slugify_run_id(goal: str) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", goal).strip("-")
    return text[:24] if text else "run"


def new_run_id(goal: str, now: float) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    return f"{stamp}-{_slugify_run_id(goal)}-{uuid.uuid4().hex[:6]}"


def default_run_dir(root: Path, run_id: str) -> Path:
    return root / ".agents" / "runtime" / "loop" / run_id


def find_run_root(start: Path) -> Optional[Path]:
    """从 start 向上找最近的可作为 loop root 的目录（含 .agents 或 .git 标记）。

    只认 .agents/.git：接受 .claude 会让非 git 的 home 子目录把 root 解析到
    $HOME，run 泄漏到 ~/.agents 且 Stop 守卫跨项目误拦截。
    """
    current = start.resolve(strict=False)
    for candidate in (current, *current.parents):
        if candidate.name == ".agents":
            return candidate.parent
        if (candidate / ".agents").is_dir() or (candidate / ".git").exists():
            return candidate
    return None


def list_run_dirs(root: Path) -> list[Path]:
    base = root / ".agents" / "runtime" / "loop"
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if not p.is_symlink() and p.is_dir() and os.path.lexists(p / RUN_FILENAME))


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def default_config() -> dict[str, Any]:
    return {
        "maxRounds": 8,
        "concurrency": 3,
        "retry": {"maxAttempts": 3, "baseSec": 5.0, "maxSec": 60.0, "jitter": 0.3},
        "stalenessSec": 300.0,
        "timeoutSec": 7200.0,
        "budgetTasks": 64,
    }


def _validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise LoopStateError("配置必须是对象")
    retry = config.get("retry")
    if not isinstance(retry, dict):
        raise LoopStateError("配置项 retry 必须是对象")
    checks = [
        ("maxRounds", config.get("maxRounds"), 1, 1000),
        ("concurrency", config.get("concurrency"), 1, 256),
        ("stalenessSec", config.get("stalenessSec"), 1.0, 86400.0 * 7),
        ("timeoutSec", config.get("timeoutSec"), 1.0, 86400.0 * 30),
        ("budgetTasks", config.get("budgetTasks"), 1, 100000),
        ("retry.maxAttempts", retry.get("maxAttempts"), 1, 100),
        ("retry.baseSec", retry.get("baseSec"), 0.0, 3600.0),
        ("retry.maxSec", retry.get("maxSec"), 0.0, 86400.0),
        ("retry.jitter", retry.get("jitter"), 0.0, 1.0),
    ]
    for name, value, low, high in checks:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise LoopStateError(f"配置项 {name} 缺失或类型错误: {value!r}")
        if not (low <= value <= high):
            raise LoopStateError(f"配置项 {name} 越界: {value} 不在 [{low}, {high}]")
    if retry["maxSec"] < retry["baseSec"]:
        raise LoopStateError("retry.maxSec 不能小于 retry.baseSec")


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


class LoopStore:
    """单个 run 的磁盘真源。所有变更方法完成后状态已落盘。"""

    def __init__(self, run_dir: Path, now: Callable[[], float] = utc_now) -> None:
        self.run_dir = Path(run_dir)
        self._now = now
        self._state: Optional[dict[str, Any]] = None
        self._revision: int | None = None

    # -- 装载 -------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.run_dir / RUN_FILENAME

    @property
    def events_path(self) -> Path:
        return self.run_dir / EVENTS_FILENAME

    @property
    def lock_path(self) -> Path:
        return self.run_dir / ".lock"

    @property
    def state(self) -> dict[str, Any]:
        if self._state is None:
            raise LoopStateError("状态未装载：先调用 init 或 load")
        return self._state

    def exists(self) -> bool:
        return self.state_path.is_file()

    def load(self) -> "LoopStore":
        _assert_directory_nosymlink(self.run_dir)
        _assert_regular_nosymlink(self.state_path, allow_missing=False)
        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            raise LoopStateError(f"无法读取 run 状态 {self.state_path}: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LoopStateError(f"run 状态损坏（非 JSON）: {self.state_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise LoopStateError(f"run 状态损坏（顶层不是对象）: {self.state_path}")
        self._validate(payload)
        self._state = payload
        self._revision = int(payload.get("_revision", 0))
        if os.path.lexists(self.events_path):
            # Loading a run is a trust boundary: corrupt or symlinked event
            # logs must fail closed instead of allowing hooks to act on a
            # partially auditable state.
            self.events()
        return self

    @staticmethod
    def _validate(payload: dict[str, Any]) -> None:
        for key in ("runId", "goal", "mode", "status", "createdAt", "updatedAt", "config", "rounds"):
            if key not in payload:
                raise LoopStateError(f"run 状态缺少字段 {key}")
        if not isinstance(payload["runId"], str) or not payload["runId"]:
            raise LoopStateError("run 状态 runId 类型错误")
        if not isinstance(payload["goal"], str) or not isinstance(payload["mode"], str):
            raise LoopStateError("run 状态 goal/mode 类型错误")
        if payload["status"] not in ACTIVE_STATUSES + TERMINAL_STATUSES:
            raise LoopStateError(f"未知 run status: {payload['status']!r}")
        if not isinstance(payload["createdAt"], (int, float)) or isinstance(payload["createdAt"], bool):
            raise LoopStateError("run 状态 createdAt 类型错误")
        if not isinstance(payload["updatedAt"], (int, float)) or isinstance(payload["updatedAt"], bool):
            raise LoopStateError("run 状态 updatedAt 类型错误")
        revision = payload.get("_revision", 0)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise LoopStateError("run 状态 _revision 类型错误")
        if not isinstance(payload["config"], dict):
            raise LoopStateError("run 状态 config 必须是对象")
        _validate_config(payload["config"])
        if not isinstance(payload["rounds"], list):
            raise LoopStateError("run 状态 rounds 必须是数组")
        current_round = payload.get("currentRound", 0)
        if not isinstance(current_round, int) or isinstance(current_round, bool) or current_round < 0:
            raise LoopStateError("run 状态 currentRound 类型错误")
        counters = payload.get("counters", {})
        if not isinstance(counters, dict):
            raise LoopStateError("run 状态 counters 必须是对象")
        for counter_name in ("tasksSpawned", "taskResults"):
            counter = counters.get(counter_name, 0)
            if not isinstance(counter, int) or isinstance(counter, bool) or counter < 0:
                raise LoopStateError(f"run 状态 {counter_name} 类型错误")
        for rnd in payload["rounds"]:
            if not isinstance(rnd, dict):
                raise LoopStateError("run 状态 round 必须是对象")
            if not isinstance(rnd.get("index"), int) or isinstance(rnd.get("index"), bool):
                raise LoopStateError("run 状态 round index 类型错误")
            if not isinstance(rnd.get("goal", ""), str):
                raise LoopStateError("run 状态 round goal 类型错误")
            if rnd.get("status") not in ("open", "closed"):
                raise LoopStateError(f"round {rnd.get('index')} status 非法: {rnd.get('status')!r}")
            tasks = rnd.get("tasks", {})
            if not isinstance(tasks, dict):
                raise LoopStateError(f"round {rnd.get('index')} tasks 必须是对象")
            for task in tasks.values():
                if not isinstance(task, dict):
                    raise LoopStateError(f"round {rnd.get('index')} task 必须是对象")
                if task.get("status") not in TASK_STATUSES:
                    raise LoopStateError(f"task {task.get('id')} status 非法: {task.get('status')!r}")

    # -- 初始化 -----------------------------------------------------------

    @classmethod
    def init(
        cls,
        run_dir: Path,
        goal: str,
        mode: str,
        config: Optional[dict[str, Any]] = None,
        now: Callable[[], float] = utc_now,
    ) -> "LoopStore":
        run_dir = Path(run_dir)
        if run_dir.is_symlink() or (run_dir.exists() and not run_dir.is_dir()):
            raise LoopStateError(f"run 目录必须是非 symlink 目录: {run_dir}")
        if run_dir.exists() and any(run_dir.iterdir()):
            raise LoopStateError(f"run 目录非空，拒绝覆盖: {run_dir}")
        merged = default_config()
        if config:
            if not isinstance(config, dict):
                raise LoopStateError("--config 必须是 JSON 对象")
            for key, value in config.items():
                if key == "retry":
                    if not isinstance(value, dict):
                        raise LoopStateError("配置项 retry 必须是对象")
                    merged["retry"].update(value)
                else:
                    merged[key] = value
        _validate_config(merged)
        ts = now()
        state = {
            "runId": run_dir.name,
            "goal": goal,
            "mode": mode,
            "status": "active",
            "createdAt": ts,
            "updatedAt": ts,
            "config": merged,
            "currentRound": 0,
            "rounds": [],
            "counters": {"tasksSpawned": 0, "taskResults": 0},
        }
        store = cls(run_dir, now=now)
        store._state = state
        store._revision = -1
        store._persist()
        store._emit("run_init", goal=goal, mode=mode)
        return store

    # -- 事件日志 ---------------------------------------------------------

    def _emit(self, event_type: str, **fields: Any) -> None:
        entry = {"ts": self._now(), "type": event_type}
        entry.update(fields)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _assert_directory_nosymlink(self.run_dir)
        encoded = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        with _exclusive_file_lock(self.lock_path):
            _assert_regular_nosymlink(self.events_path)
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                fd = os.open(self.events_path, flags, 0o600)
            except OSError as exc:
                raise LoopStateError(f"无法打开事件日志 {self.events_path}: {exc}") from exc
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            _fsync_directory(self.run_dir)

    def events(self) -> list[dict[str, Any]]:
        if not os.path.lexists(self.events_path):
            return []
        _assert_regular_nosymlink(self.events_path)
        events: list[dict[str, Any]] = []
        try:
            with self.events_path.open("r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except (OSError, UnicodeError) as exc:
            raise LoopStateError(f"无法读取事件日志 {self.events_path}: {exc}") from exc
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LoopStateError(f"事件日志第 {line_no} 行损坏: {exc}") from exc
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise LoopStateError(f"事件日志第 {line_no} 行结构非法")
            events.append(event)
        return events

    def _persist(self) -> None:
        self.state["updatedAt"] = self._now()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _assert_directory_nosymlink(self.run_dir)
        with _exclusive_file_lock(self.lock_path):
            current_revision = -1
            if os.path.lexists(self.state_path):
                _assert_regular_nosymlink(self.state_path)
                try:
                    with self.state_path.open("r", encoding="utf-8") as handle:
                        current = json.load(handle)
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise LoopStateError(f"run 状态损坏，无法并发写入: {self.state_path}: {exc}") from exc
                if not isinstance(current, dict):
                    raise LoopStateError(f"run 状态损坏（顶层不是对象）: {self.state_path}")
                current_revision = current.get("_revision", 0)
                if not isinstance(current_revision, int) or isinstance(current_revision, bool):
                    raise LoopStateError(f"run 状态 _revision 类型错误: {self.state_path}")
                expected = 0 if self._revision is None else self._revision
                if current_revision != expected:
                    raise LoopStateError(f"run 状态并发冲突: expected revision {expected}, found {current_revision}")
            elif self._revision not in (None, -1):
                raise LoopStateError("run 状态在写入期间被删除")
            next_revision = current_revision + 1
            self.state["_revision"] = next_revision
            _atomic_write_json(self.state_path, self.state)
            self._revision = next_revision

    # -- 内部定位 ---------------------------------------------------------

    def _require_active(self) -> None:
        if self.state["status"] != "active":
            raise LoopStateError(f"run 已处于终态 {self.state['status']}，拒绝变更")

    def _round(self, index: int) -> dict[str, Any]:
        for rnd in self.state["rounds"]:
            if rnd["index"] == index:
                return rnd
        raise LoopStateError(f"round {index} 不存在")

    def _task(self, round_index: int, task_id: str) -> dict[str, Any]:
        task = self._round(round_index).get("tasks", {}).get(task_id)
        if task is None:
            raise LoopStateError(f"task {task_id} 不存在于 round {round_index}")
        return task

    def _latest_open_round(self) -> Optional[dict[str, Any]]:
        for rnd in reversed(self.state["rounds"]):
            if rnd["status"] == "open":
                return rnd
        return None

    # -- round ------------------------------------------------------------

    def round_start(self, goal: str) -> int:
        self._require_active()
        if self._latest_open_round() is not None:
            raise LoopStateError("已有 open round，先 close 再开新轮")
        index = len(self.state["rounds"]) + 1
        if index > self.state["config"]["maxRounds"]:
            raise LoopStateError(f"超过 maxRounds={self.state['config']['maxRounds']}")
        self.state["rounds"].append({"index": index, "goal": goal, "status": "open", "tasks": {}, "decision": None})
        self.state["currentRound"] = index
        self._persist()
        self._emit("round_start", round=index, goal=goal)
        return index

    def round_end(self, decision: str, note: str = "") -> None:
        self._require_active()
        if decision not in ROUND_DECISIONS:
            raise LoopStateError(f"round decision 非法: {decision!r}")
        rnd = self._latest_open_round()
        if rnd is None:
            raise LoopStateError("没有 open round 可结束")
        # 关轮前任务必须全部终态：孤儿 in_flight/pending 会让 termination_check
        # 的 all_tasks_terminal 永远为假，run 楔死到超时，且永久占用并发额度。
        # 需要放弃存活任务时先 task_cancel，再关轮。
        live = [
            tid for tid, task in rnd.get("tasks", {}).items() if task["status"] not in ("done", "failed", "cancelled")
        ]
        if live:
            raise LoopStateError(
                f"round {rnd['index']} 仍有非终态任务 {', '.join(live)}：先记录 result 或 cancel 再关轮"
            )
        rnd["status"] = "closed"
        rnd["decision"] = decision
        self._persist()
        self._emit("round_end", round=rnd["index"], decision=decision, note=note)

    # -- task -------------------------------------------------------------

    def task_add(self, round_index: int, task_id: str, agent_type: str, description: str) -> None:
        self._require_active()
        rnd = self._round(round_index)
        if rnd["status"] != "open":
            raise LoopStateError(f"round {round_index} 已关闭，不能加任务")
        if task_id in rnd.setdefault("tasks", {}):
            raise LoopStateError(f"task {task_id} 已存在于 round {round_index}")
        rnd["tasks"][task_id] = {
            "id": task_id,
            "agentType": agent_type,
            "description": description,
            "status": "pending",
            "attempts": 0,
            "submissionId": None,
            "agentId": None,
            "lastError": None,
            "backoffUntil": None,
            "lastHeartbeat": None,
            "result": None,
        }
        self._persist()
        self._emit("task_add", round=round_index, task=task_id, agentType=agent_type)

    def task_spawn(self, round_index: int, task_id: str, agent_id: str, submission_id: str) -> None:
        self._require_active()
        rnd = self._round(round_index)
        if rnd["status"] != "open":
            raise LoopStateError(f"round {round_index} 已关闭，不能 spawn 任务")
        task = self._task(round_index, task_id)
        # 只允许 pending：failed = 重试耗尽（attempts 已达 maxAttempts），再 spawn
        # 会绕过重试预算无限白送尝试；中断恢复走 resume 的 in_flight -> pending。
        if task["status"] != "pending":
            raise LoopStateError(f"task {task_id} 状态 {task['status']} 不可 spawn")
        in_flight = sum(
            1
            for rnd in self.state["rounds"]
            for candidate in rnd.get("tasks", {}).values()
            if candidate.get("status") == "in_flight"
        )
        cap = int(self.state["config"]["concurrency"])
        if in_flight >= cap:
            raise LoopStateError(f"并发已满 inFlight={in_flight} cap={cap}，拒绝 spawn")
        task["status"] = "in_flight"
        task["attempts"] += 1
        task["agentId"] = agent_id
        task["submissionId"] = submission_id
        task["lastError"] = None
        task["backoffUntil"] = None
        task["lastHeartbeat"] = self._now()
        self.state["counters"]["tasksSpawned"] += 1
        self._persist()
        self._emit(
            "task_spawn",
            round=round_index,
            task=task_id,
            agent=agent_id,
            submission=submission_id,
            attempt=task["attempts"],
        )

    def task_heartbeat(self, round_index: int, task_id: str) -> None:
        self._require_active()
        task = self._task(round_index, task_id)
        if task["status"] != "in_flight":
            raise LoopStateError(f"task {task_id} 状态 {task['status']} 不可心跳")
        task["lastHeartbeat"] = self._now()
        self._persist()

    def task_result(
        self,
        round_index: int,
        task_id: str,
        outcome: str,
        error: str = "",
        result: Any = None,
    ) -> str:
        """记录任务结果；返回 done | retry | exhausted。"""
        self._require_active()
        if outcome not in ("ok", "fail"):
            raise LoopStateError(f"outcome 非法: {outcome!r}")
        task = self._task(round_index, task_id)
        if task["status"] != "in_flight":
            raise LoopStateError(f"task {task_id} 状态 {task['status']} 不可记录结果")
        self.state["counters"]["taskResults"] += 1
        if outcome == "ok":
            task["status"] = "done"
            task["result"] = result
            self._persist()
            self._emit("task_result", round=round_index, task=task_id, outcome="ok")
            return "done"
        task["lastError"] = error or "unspecified failure"
        retry_cfg = self.state["config"]["retry"]
        if task["attempts"] >= retry_cfg["maxAttempts"]:
            task["status"] = "failed"
            self._persist()
            self._emit(
                "task_result",
                round=round_index,
                task=task_id,
                outcome="exhausted",
                error=task["lastError"],
            )
            return "exhausted"
        task["status"] = "pending"
        self._persist()
        self._emit(
            "task_retry_scheduled",
            round=round_index,
            task=task_id,
            attempt=task["attempts"],
            error=task["lastError"],
        )
        return "retry"

    def task_backoff_until(self, round_index: int, task_id: str, until: float) -> None:
        self._require_active()
        task = self._task(round_index, task_id)
        if task["status"] != "pending":
            raise LoopStateError(f"task {task_id} 状态 {task['status']} 不可设置退避")
        task["backoffUntil"] = until
        self._persist()

    def task_cancel(self, round_index: int, task_id: str, reason: str) -> None:
        self._require_active()
        task = self._task(round_index, task_id)
        if task["status"] in ("done", "cancelled"):
            raise LoopStateError(f"task {task_id} 状态 {task['status']} 不可取消")
        task["status"] = "cancelled"
        task["lastError"] = reason
        self._persist()
        self._emit("task_cancelled", round=round_index, task=task_id, reason=reason)

    # -- 终态 -------------------------------------------------------------
    # converged/failed/interrupted/cancelled 与终止判定见 loop_control.py；
    # 这里只负责落状态与事件，保证单一写入口。

    def _finish(self, status: str, **fields: Any) -> None:
        self._require_active()
        self.state["status"] = status
        self._persist()
        self._emit(f"run_{status}", **fields)

    def run_converged(self, summary: str = "") -> None:
        self._finish("converged", summary=summary)

    def run_failed(self, reason: str) -> None:
        self._finish("failed", reason=reason)

    def run_interrupted(self, reason: str) -> list[str]:
        """协作中断：记录受影响 in_flight 任务，状态转 interrupted。"""
        self._require_active()
        affected = []
        for rnd in self.state["rounds"]:
            for task in rnd.get("tasks", {}).values():
                if task["status"] == "in_flight":
                    affected.append(f"r{rnd['index']}/{task['id']}")
        self._finish("interrupted", reason=reason, inFlight=affected)
        return affected

    def run_cancelled(self, reason: str) -> None:
        self._finish("cancelled", reason=reason)

    # -- 恢复 -------------------------------------------------------------

    def resume(self) -> dict[str, Any]:
        """校验状态一致性并给出续跑动作。

        interrupted 恢复：in_flight 语义任务（事件日志中 spawn 过但无结果）回 pending，
        attempts 保留，等待 agent 重新 spawn。
        """
        if self.state["status"] == "active":
            pass  # 正常续跑
        elif self.state["status"] == "interrupted":
            reverted = []
            for rnd in self.state["rounds"]:
                for task in rnd.get("tasks", {}).values():
                    if task["status"] == "in_flight":
                        task["status"] = "pending"
                        reverted.append(f"r{rnd['index']}/{task['id']}")
            self.state["status"] = "active"
            self._persist()
            self._emit("run_resumed", fromStatus="interrupted", reverted=reverted)
        else:
            raise LoopStateError(f"run 终态 {self.state['status']} 不可恢复")
        # 一致性：事件日志里的 spawn 必须有对应任务记录
        known = {f"r{rnd['index']}/{tid}" for rnd in self.state["rounds"] for tid in rnd.get("tasks", {})}
        for event in self.events():
            if event.get("type") == "task_spawn":
                key = f"r{event.get('round')}/{event.get('task')}"
                if key not in known:
                    raise LoopStateError(f"事件日志引用了不存在的任务 {key}，状态不一致")
        open_rounds = [rnd["index"] for rnd in self.state["rounds"] if rnd["status"] == "open"]
        pending = [
            f"r{rnd['index']}/{tid}"
            for rnd in self.state["rounds"]
            for tid, task in rnd.get("tasks", {}).items()
            if task["status"] == "pending"
        ]
        in_flight = [
            f"r{rnd['index']}/{tid}"
            for rnd in self.state["rounds"]
            for tid, task in rnd.get("tasks", {}).items()
            if task["status"] == "in_flight"
        ]
        actions = []
        if not open_rounds:
            actions.append("round_start：开启下一轮")
        if pending:
            actions.append("spawn：重新派发 pending 任务 " + ", ".join(pending))
        if in_flight:
            actions.append(
                "wait 在途任务 " + ", ".join(in_flight) + " 并记录 task result；"
                "worker 已死（心跳超时）时用 loop_control.py stale --reap --run-dir <run目录> 回收"
            )
        return {
            "status": self.state["status"],
            "openRounds": open_rounds,
            "pending": pending,
            "inFlight": in_flight,
            "nextActions": actions,
        }

    # -- 摘要 -------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        st = self.state
        in_flight = [
            f"r{rnd['index']}/{tid}"
            for rnd in st["rounds"]
            for tid, task in rnd.get("tasks", {}).items()
            if task["status"] == "in_flight"
        ]
        return {
            "runId": st["runId"],
            "goal": st["goal"],
            "mode": st["mode"],
            "status": st["status"],
            "active": st["status"] in ACTIVE_STATUSES,
            "converged": st["status"] == "converged",
            "currentRound": st["currentRound"],
            "rounds": len(st["rounds"]),
            "inFlight": in_flight,
            "runDir": str(self.run_dir),
        }


def write_stop_sentinel(run_dir: Path, payload: dict[str, Any]) -> None:
    """Create STOP without following a pre-existing symlink."""
    run_dir = Path(run_dir)
    _assert_directory_nosymlink(run_dir)
    target = run_dir / STOP_FILENAME
    with _exclusive_file_lock(run_dir / ".lock"):
        _assert_regular_nosymlink(target)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(target, flags, 0o600)
        except OSError as exc:
            raise LoopStateError(f"无法写入 STOP 哨兵 {target}: {exc}") from exc
        try:
            encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            view = memoryview(encoded)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_directory(run_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _emit_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _store_from(args: argparse.Namespace) -> LoopStore:
    run_dir = Path(args.run_dir)
    store = LoopStore(run_dir)
    if not store.exists():
        raise LoopStateError(f"run 不存在: {run_dir}")
    return store.load()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loop_state.py", description="agents-team-loop 状态机 CLI（磁盘真源）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="初始化一个 run")
    p.add_argument("--goal", required=True)
    p.add_argument("--mode", default="until-converged", choices=["until-converged", "fixed-rounds", "batch-fanout"])
    p.add_argument("--root", default=".", help="run root（默认当前项目根）")
    p.add_argument("--run-id", default=None)
    p.add_argument("--config", default=None, help="JSON 文件或内联 JSON，覆盖默认配置")
    p.add_argument("--run-dir", default=None, help="显式 run 目录（优先于 root/run-id）")

    for name in ("status", "resume"):
        p = sub.add_parser(name, help={"status": "输出 run 摘要", "resume": "校验并恢复"}[name])
        p.add_argument("--run-dir", required=True)

    p = sub.add_parser("round", help="round 生命周期")
    p.add_argument("--run-dir", required=True)
    p.add_argument("action", choices=["start", "end"])
    p.add_argument("--goal", default="")
    p.add_argument("--decision", choices=ROUND_DECISIONS)

    p = sub.add_parser("task", help="task 生命周期")
    p.add_argument("--run-dir", required=True)
    p.add_argument(
        "action",
        choices=["add", "spawn", "heartbeat", "result", "backoff", "cancel"],
    )
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--agent-type")
    p.add_argument("--description")
    p.add_argument("--agent")
    p.add_argument("--submission")
    p.add_argument("--outcome", choices=["ok", "fail"])
    p.add_argument("--error", default="")
    p.add_argument("--until", type=float)
    p.add_argument("--reason", default="")
    p.add_argument("--result", default=None, help="结果内联 JSON 或 @file")

    p = sub.add_parser("finish", help="run 终态")
    p.add_argument("--run-dir", required=True)
    p.add_argument("status", choices=list(TERMINAL_STATUSES))
    p.add_argument("--reason", default="")
    p.add_argument("--summary", default="")

    p = sub.add_parser("events", help="输出事件日志")
    p.add_argument("--run-dir", required=True)

    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "init":
            config = None
            if args.config:
                raw = args.config
                try:
                    if raw.startswith("@"):
                        config = json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
                    else:
                        config = json.loads(raw)
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise LoopStateError(f"无效 --config: {exc}") from exc
            if args.run_dir:
                run_dir = Path(args.run_dir)
            else:
                root = find_run_root(Path(args.root)) or Path(args.root).resolve()
                run_id = args.run_id or new_run_id(args.goal, utc_now())
                run_dir = default_run_dir(root, run_id)
            store = LoopStore.init(run_dir, args.goal, args.mode, config=config)
            _emit_json({"runDir": str(run_dir), "runId": store.state["runId"], **store.summary()})
        elif args.command == "status":
            _emit_json(_store_from(args).summary())
        elif args.command == "resume":
            _emit_json(_store_from(args).resume())
        elif args.command == "round":
            store = _store_from(args)
            if args.action == "start":
                _emit_json({"round": store.round_start(args.goal or "round auto")})
            else:
                if not args.decision:
                    raise LoopStateError("round end 需要 --decision pass|fail")
                store.round_end(args.decision)
                _emit_json({"ok": True})
        elif args.command == "task":
            store = _store_from(args)
            if args.action == "add":
                if not args.agent_type or args.description is None:
                    raise LoopStateError("task add 需要 --agent-type 与 --description")
                store.task_add(args.round, args.id, args.agent_type, args.description)
                _emit_json({"ok": True})
            elif args.action == "spawn":
                if not args.agent or not args.submission:
                    raise LoopStateError("task spawn 需要 --agent 与 --submission")
                store.task_spawn(args.round, args.id, args.agent, args.submission)
                _emit_json({"ok": True})
            elif args.action == "heartbeat":
                store.task_heartbeat(args.round, args.id)
                _emit_json({"ok": True})
            elif args.action == "result":
                if not args.outcome:
                    raise LoopStateError("task result 需要 --outcome ok|fail")
                result = None
                if args.result:
                    raw = args.result
                    try:
                        result = (
                            json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
                            if raw.startswith("@")
                            else json.loads(raw)
                        )
                    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                        raise LoopStateError(f"无效 --result: {exc}") from exc
                outcome_state = store.task_result(args.round, args.id, args.outcome, args.error, result)
                _emit_json({"outcome": outcome_state})
            elif args.action == "backoff":
                if args.until is None:
                    raise LoopStateError("task backoff 需要 --until <epoch秒>")
                store.task_backoff_until(args.round, args.id, args.until)
                _emit_json({"ok": True})
            else:
                store.task_cancel(args.round, args.id, args.reason or "unspecified")
                _emit_json({"ok": True})
        elif args.command == "finish":
            store = _store_from(args)
            if args.status == "converged":
                store.run_converged(args.summary)
            elif args.status == "failed":
                store.run_failed(args.reason or "unspecified")
            elif args.status == "interrupted":
                affected = store.run_interrupted(args.reason or "unspecified")
                _emit_json({"affected": affected})
                return 0
            else:
                store.run_cancelled(args.reason or "unspecified")
            _emit_json({"ok": True})
        elif args.command == "events":
            _emit_json(_store_from(args).events())
        return 0
    except LoopStateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

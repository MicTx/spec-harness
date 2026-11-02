#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""agents-team-loop 状态机：run/round/task 生命周期、检查点、事件日志与恢复。

设计对齐（见 references/prior-art.md）：
- LangGraph checkpointer：每次事件后原子落盘 run.json，中断后可从检查点 resume。
- Claude Code agent teams 共享任务列表：任务状态持久化，恢复会话保留任务。
- AutoGen ExternalTermination：run 目录下的 STOP 哨兵文件提供外部程序化停止。

状态模型：
  run    : active -> converged | failed | interrupted | cancelled
  round  : open -> closed(pass|fail)
  task   : pending -> in_flight(reserved -> attached) -> done | failed
           失败且 attempts < max 时转 pending 并记录退避；中断恢复保留在途与预约
           旧式直接登记仍兼容，但无法限制登记前的宿主启动竞态。

磁盘布局（run 目录 = <root>/.agents/runtime/loop/<run-id>/）：
  run.json       配置 + 状态（唯一真源，原子写）
  events.jsonl   追加式事件日志（审计 + 恢复校验）
  STOP           外部停止哨兵（存在即触发终止）
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
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
LOCK_FILENAME = ".lock"

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


def _validate_run_id(run_id: str) -> None:
    """run-id 必须是单一路径段，不得包含路径分隔符、父目录或为绝对路径。

    否则 run 会落到 loop/ 之外，Stop 守卫与并发/预算约束静默失去作用。
    """
    if not isinstance(run_id, str) or not run_id:
        raise LoopStateError("run-id 必须是非空字符串")
    if run_id in (".", ".."):
        raise LoopStateError(f"run-id 非法（单一路径段）: {run_id!r}")
    if "/" in run_id or "\\" in run_id or "\x00" in run_id:
        raise LoopStateError(f"run-id 非法（含路径分隔符）: {run_id!r}")
    if Path(run_id).is_absolute():
        raise LoopStateError(f"run-id 非法（绝对路径）: {run_id!r}")
    if Path(run_id).name != run_id:
        raise LoopStateError(f"run-id 非法（非单一路径段）: {run_id!r}")


def default_run_dir(root: Path, run_id: str) -> Path:
    _validate_run_id(run_id)
    return loop_base(root) / run_id


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


def loop_base(root: Path) -> Path:
    return root / ".agents" / "runtime" / "loop"


def list_run_dirs(root: Path) -> list[Path]:
    base = loop_base(root)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if not p.is_symlink() and p.is_dir() and os.path.lexists(p / RUN_FILENAME))


def list_orphan_run_dirs(root: Path) -> list[Path]:
    """列出 loop base 下有活动痕迹却缺 run.json 的目录（fail-closed 候选）。

    一个真实 run 可能因 run.json 尚未写、被删除或写入中断而只剩 events.jsonl/.lock
    等痕迹；这类目录不是「无 run」，不能被守卫静默当不存在放行，而应 fail-closed。
    空目录或只有无关文件的目录仍视为无 run。
    """
    base = loop_base(root)
    if not base.is_dir():
        return []
    out: list[Path] = []
    for p in base.iterdir():
        if p.is_symlink() or not p.is_dir():
            continue
        if os.path.lexists(p / RUN_FILENAME):
            continue  # 已由 list_run_dirs 处理
        if os.path.lexists(p / EVENTS_FILENAME) or os.path.lexists(p / LOCK_FILENAME):
            out.append(p)
    return sorted(out)


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


def backoff_delay(attempt: int, retry_cfg: dict[str, Any], rng: random.Random) -> float:
    """Compute the retry delay once, when the failure is persisted."""
    base = float(retry_cfg.get("baseSec", 5.0))
    cap = float(retry_cfg.get("maxSec", 60.0))
    jitter = float(retry_cfg.get("jitter", 0.3))
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    if jitter <= 0.0:
        return round(ceiling, 3)
    raw = rng.uniform(0.0, ceiling)
    return round(rng.uniform(raw * (1.0 - jitter), raw), 3) if raw > 0 else 0.0


def _validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise LoopStateError("配置必须是对象")
    retry = config.get("retry")
    if not isinstance(retry, dict):
        raise LoopStateError("配置项 retry 必须是对象")
    # These budgets and limits count indivisible units; never truncate floats.
    discrete = {
        "maxRounds": config.get("maxRounds"),
        "concurrency": config.get("concurrency"),
        "budgetTasks": config.get("budgetTasks"),
        "retry.maxAttempts": retry.get("maxAttempts"),
    }
    for name, value in discrete.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise LoopStateError(f"配置项 {name} 必须是正整数: {value!r}")
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
        return self.run_dir / LOCK_FILENAME

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
        except (OSError, UnicodeError) as exc:
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
        if (
            not isinstance(payload["createdAt"], (int, float))
            or isinstance(payload["createdAt"], bool)
            or not math.isfinite(payload["createdAt"])
        ):
            raise LoopStateError("run 状态 createdAt 类型错误")
        if (
            not isinstance(payload["updatedAt"], (int, float))
            or isinstance(payload["updatedAt"], bool)
            or not math.isfinite(payload["updatedAt"])
        ):
            raise LoopStateError("run 状态 updatedAt 类型错误")
        revision = payload.get("_revision", 0)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise LoopStateError("run 状态 _revision 类型错误")
        if not isinstance(payload["config"], dict):
            raise LoopStateError("run 状态 config 必须是对象")
        _validate_config(payload["config"])
        if not isinstance(payload["rounds"], list):
            raise LoopStateError("run 状态 rounds 必须是数组")
        current_round = payload.get("currentRound")
        if not isinstance(current_round, int) or isinstance(current_round, bool) or current_round < 0:
            raise LoopStateError("run 状态 currentRound 类型错误")
        counters = payload.get("counters")
        if not isinstance(counters, dict):
            raise LoopStateError("run 状态 counters 必须是对象")
        for counter_name in ("tasksSpawned", "taskResults"):
            counter = counters.get(counter_name)
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
                attempts = task.get("attempts")
                if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
                    raise LoopStateError("task attempts 类型错误")
                for field in ("backoffUntil", "lastHeartbeat", "reservedAt"):
                    value = task.get(field)
                    if value is not None and (
                        not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
                    ):
                        raise LoopStateError(f"task {field} 类型错误")
                phase = task.get("dispatchPhase")
                if phase is not None and phase not in ("reserved", "attached"):
                    raise LoopStateError("task dispatchPhase 类型错误")
                token = task.get("reservationToken")
                if token is not None and (not isinstance(token, str) or not token):
                    raise LoopStateError("task reservationToken 类型错误")
                if phase == "reserved" and not token:
                    raise LoopStateError("task 预约缺少 token")
                used = task.get("usedSubmissionIds", [])
                if (
                    not isinstance(used, list)
                    or any(not isinstance(item, str) or not item for item in used)
                    or len(set(used)) != len(used)
                ):
                    raise LoopStateError("task usedSubmissionIds 类型错误")

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
            # Copy-on-write prevents an existing inode (including a hard link
            # installed after checking it) from ever receiving appended bytes.
            existed = os.path.lexists(self.events_path)
            _assert_regular_nosymlink(self.events_path)
            source_fd = None
            tmp_name = None
            try:
                if existed:
                    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                    source_fd = os.open(self.events_path, flags)
                    info = os.fstat(source_fd)
                    path_info = self.events_path.lstat()
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or stat.S_ISLNK(path_info.st_mode)
                        or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
                    ):
                        raise LoopStateError(f"事件日志必须是独立的普通文件: {self.events_path}")
                fd, tmp_name = tempfile.mkstemp(dir=str(self.run_dir), prefix=".tmp-", suffix=".events")
                with os.fdopen(fd, "wb") as handle:
                    if source_fd is not None:
                        source = os.fdopen(source_fd, "rb")
                        source_fd = None
                        with source:
                            shutil.copyfileobj(source, handle)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, self.events_path)
                tmp_name = None
                _fsync_directory(self.run_dir)
            except OSError as exc:
                raise LoopStateError(f"无法安全写入事件日志 {self.events_path}: {exc}") from exc
            finally:
                if source_fd is not None:
                    os.close(source_fd)
                if tmp_name is not None:
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass

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

    def _persist(self, *, reject_stop: bool = False, reject_timeout: bool = False) -> None:
        self.state["updatedAt"] = self._now()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _assert_directory_nosymlink(self.run_dir)
        with _exclusive_file_lock(self.lock_path):
            if reject_stop and os.path.lexists(self.run_dir / STOP_FILENAME):
                _assert_regular_nosymlink(self.run_dir / STOP_FILENAME)
                raise LoopStateError("STOP 哨兵存在，拒绝变更")
            if reject_timeout and self._run_timed_out():
                # 锁外检查超时后、持锁跨期限：必须在锁内重验，拒绝落盘
                # in_flight/预算消耗或误报 converged。
                raise LoopStateError("runTimeout 已超时，拒绝变更")
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

    def _run_timed_out(self) -> bool:
        cfg = self.state["config"]
        return self._now() - float(self.state["createdAt"]) > float(cfg["timeoutSec"])

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

    def _check_dispatch(self, round_index: int, task_id: str) -> dict[str, Any]:
        self._require_active()
        if self._round(round_index)["status"] != "open":
            raise LoopStateError(f"round {round_index} 已关闭，不能 spawn 任务")
        task = self._task(round_index, task_id)
        if task["status"] != "pending":
            raise LoopStateError(f"task {task_id} 状态 {task['status']} 不可 spawn")
        cfg = self.state["config"]
        if task["attempts"] >= int(cfg["retry"]["maxAttempts"]):
            raise LoopStateError(f"task {task_id} 已耗尽 maxAttempts")
        until = task.get("backoffUntil")
        if task["attempts"] > 0 and task.get("lastError") and until is None:
            raise LoopStateError("旧任务缺少失败退避；先用 task backoff 安全设置截止时间")
        if until is not None and self._now() < float(until):
            raise LoopStateError(f"task {task_id} 仍在 backoff 至 {until}")
        if self._now() - float(self.state["createdAt"]) > float(cfg["timeoutSec"]):
            raise LoopStateError("runTimeout 已超时，拒绝 spawn")
        stop = self.run_dir / STOP_FILENAME
        if os.path.lexists(stop):
            _assert_regular_nosymlink(stop)
            raise LoopStateError("STOP 哨兵存在，拒绝 spawn")
        in_flight = sum(
            1
            for rnd in self.state["rounds"]
            for candidate in rnd.get("tasks", {}).values()
            if candidate.get("status") == "in_flight"
        )
        cap = int(cfg["concurrency"])
        if in_flight >= cap:
            raise LoopStateError(f"并发已满 inFlight={in_flight} cap={cap}，拒绝 spawn")
        if int(self.state["counters"]["tasksSpawned"]) >= int(cfg["budgetTasks"]):
            raise LoopStateError(f"budgetTasks={cfg['budgetTasks']} 已用尽，拒绝 spawn")
        return task

    def _start_attempt(self, task: dict[str, Any], reservation: Optional[str]) -> None:
        used = list(task.get("usedSubmissionIds", []))
        previous = task.get("submissionId")
        if previous and previous not in used:
            used.append(previous)
        task["usedSubmissionIds"] = used
        task["status"] = "in_flight"
        task["attempts"] += 1
        task["agentId"] = None
        task["submissionId"] = None
        task["lastError"] = None
        task["backoffUntil"] = None
        task["workerReport"] = None
        task["lastHeartbeat"] = None
        task["reservedAt"] = self._now()
        task["reservationToken"] = reservation
        task["dispatchPhase"] = "reserved" if reservation else "attached"
        self.state["counters"]["tasksSpawned"] += 1

    def task_reserve(self, round_index: int, task_id: str) -> str:
        """Reserve capacity *before* calling the host. CAS rejects concurrent snapshots."""
        if fcntl is None:
            raise LoopStateError("预约需要跨进程 flock；当前平台不可用")
        task = self._check_dispatch(round_index, task_id)
        token = uuid.uuid4().hex
        self._start_attempt(task, token)
        self._persist(reject_stop=True, reject_timeout=True)
        self._emit("task_reserved", round=round_index, task=task_id, attempt=task["attempts"])
        return token

    def task_spawn(
        self,
        round_index: int,
        task_id: str,
        agent_id: str,
        submission_id: str,
        reservation: Optional[str] = None,
    ) -> None:
        """Attach a host worker to a reservation; legacy direct registration is unsafe."""
        self._require_active()
        if not agent_id or not submission_id:
            raise LoopStateError("task spawn 需要非空 agent 与 submission")
        task = self._task(round_index, task_id)
        reserved = task.get("status") == "in_flight" and task.get("dispatchPhase") == "reserved"
        if (
            reservation
            and task.get("status") == "in_flight"
            and task.get("dispatchPhase") == "attached"
            and reservation == task.get("reservationToken")
            and agent_id == task.get("agentId")
            and submission_id == task.get("submissionId")
        ):
            return  # Idempotent recovery after an ambiguous attach response.
        if reserved:
            if not reservation or reservation != task.get("reservationToken"):
                raise LoopStateError("预约 token 不匹配，拒绝登记")
        else:
            if reservation:
                raise LoopStateError("没有匹配的待登记预约")
            task = self._check_dispatch(round_index, task_id)
        used = list(task.get("usedSubmissionIds", []))
        previous = task.get("submissionId")
        if previous and previous not in used:
            used.append(previous)
        if submission_id in used:
            raise LoopStateError("submission 已被之前的尝试使用，无法 fencing")
        if not reserved:
            self._start_attempt(task, None)
        used.append(submission_id)
        task["usedSubmissionIds"] = used
        task["dispatchPhase"] = "attached"
        task["agentId"] = agent_id
        task["submissionId"] = submission_id
        # Grace-period origin, not evidence of ongoing worker health.
        task["lastHeartbeat"] = self._now()
        self._persist(reject_stop=not reserved)
        self._emit(
            "task_spawn",
            round=round_index,
            task=task_id,
            agent=agent_id,
            submission=submission_id,
            attempt=task["attempts"],
            reserved=reserved,
        )

    def _check_attempt(self, task: dict[str, Any], submission: Optional[str], reservation: Optional[str]) -> None:
        token = task.get("reservationToken")
        if token and reservation != token:
            raise LoopStateError("预约 token 不匹配，拒绝过期尝试")
        if submission is not None and submission != task.get("submissionId"):
            raise LoopStateError("submission 不匹配，拒绝过期尝试")
        # Preserve the old first-attempt CLI; retries must identify the attempt.
        if not token and task.get("attempts", 0) > 1 and not submission:
            raise LoopStateError("重试结果需要当前 --submission")

    def task_heartbeat(
        self,
        round_index: int,
        task_id: str,
        *,
        source: Optional[str] = None,
        submission: Optional[str] = None,
        reservation: Optional[str] = None,
    ) -> None:
        self._require_active()
        task = self._task(round_index, task_id)
        if task["status"] != "in_flight" or task.get("dispatchPhase") == "reserved":
            raise LoopStateError(f"task {task_id} 状态不可心跳")
        if source is None:
            # A coordinator's wait loop says nothing about worker liveness.
            task["lastCoordinatorHeartbeat"] = self._now()
        else:
            if source not in ("worker", "host") or not submission:
                raise LoopStateError("心跳需要 worker/host 来源及当前 submission")
            self._check_attempt(task, submission, reservation)
            task["lastHeartbeat"] = self._now()
            task["heartbeatSource"] = source
        self._persist()

    def _record_failure(self, round_index: int, task_id: str, task: dict[str, Any], error: str) -> str:
        task["lastError"] = error or "unspecified failure"
        retry_cfg = self.state["config"]["retry"]
        if task["attempts"] >= retry_cfg["maxAttempts"]:
            task["status"] = "failed"
            self._persist()
            self._emit("task_result", round=round_index, task=task_id, outcome="exhausted", error=task["lastError"])
            return "exhausted"
        task["status"] = "pending"
        task["backoffUntil"] = self._now() + backoff_delay(task["attempts"], retry_cfg, random.Random())
        self._persist()
        self._emit(
            "task_retry_scheduled", round=round_index, task=task_id, attempt=task["attempts"], error=task["lastError"]
        )
        return "retry"

    def task_report(
        self,
        round_index: int,
        task_id: str,
        report: Any,
        *,
        submission: Optional[str] = None,
        reservation: Optional[str] = None,
    ) -> None:
        """Persist a nonterminal worker handoff without asserting worker exit."""
        self._require_active()
        task = self._task(round_index, task_id)
        if task["status"] != "in_flight" or task.get("dispatchPhase") == "reserved":
            raise LoopStateError("仅可对已登记的 in_flight 尝试提交报告")
        self._check_attempt(task, submission, reservation)
        if not isinstance(report, dict) or not report:
            raise LoopStateError("task report 需要非空 JSON 对象作为实际工作交接")
        task["workerReport"] = report
        self._persist()
        self._emit("task_report", round=round_index, task=task_id, attempt=task["attempts"])

    def task_result(
        self,
        round_index: int,
        task_id: str,
        outcome: str,
        error: str = "",
        result: Any = None,
        *,
        submission: Optional[str] = None,
        reservation: Optional[str] = None,
        confirmed_stopped: bool = False,
    ) -> str:
        """Record a result only for the current attached attempt."""
        self._require_active()
        if outcome not in ("ok", "fail"):
            raise LoopStateError(f"outcome 非法: {outcome!r}")
        task = self._task(round_index, task_id)
        if task["status"] != "in_flight" or task.get("dispatchPhase") == "reserved":
            raise LoopStateError(f"task {task_id} 状态 {task['status']} 不可记录结果")
        self._check_attempt(task, submission, reservation)
        if not confirmed_stopped:
            raise LoopStateError("主会话确认宿主 worker 已停止后才能记录终态，传入 --confirmed-stopped")
        self.state["counters"]["taskResults"] += 1
        if outcome == "ok":
            task["status"] = "done"
            task["result"] = result
            self._persist()
            self._emit("task_result", round=round_index, task=task_id, outcome="ok")
            return "done"
        return self._record_failure(round_index, task_id, task, error)

    def task_abandon(self, round_index: int, task_id: str, reservation: str, *, confirmed_stopped: bool) -> str:
        """Resolve an ambiguous reservation only after host reconciliation by the caller."""
        self._require_active()
        task = self._task(round_index, task_id)
        if task["status"] != "in_flight" or task.get("dispatchPhase") != "reserved":
            raise LoopStateError("仅可放弃未登记的预约")
        if not reservation or reservation != task.get("reservationToken"):
            raise LoopStateError("预约 token 不匹配")
        if not confirmed_stopped:
            raise LoopStateError("须核实宿主未启动或已停止 worker，传入 --confirmed-stopped")
        self.state["counters"]["taskResults"] += 1
        return self._record_failure(round_index, task_id, task, "预约已由调用者确认无运行中的宿主 worker")

    def task_backoff_until(self, round_index: int, task_id: str, until: float) -> None:
        self._require_active()
        task = self._task(round_index, task_id)
        if task["status"] != "pending":
            raise LoopStateError(f"task {task_id} 状态 {task['status']} 不可设置退避")
        if not math.isfinite(until):
            raise LoopStateError("backoffUntil 必须是有限时间")
        # Old CLI may extend, but cannot shorten the automatically scheduled delay.
        task["backoffUntil"] = max(float(task.get("backoffUntil") or 0), until)
        self._persist()

    def task_cancel(self, round_index: int, task_id: str, reason: str, *, confirmed_stopped: bool = False) -> None:
        self._require_active()
        task = self._task(round_index, task_id)
        # failed 是重试耗尽的终态：取消不得改写它，否则 termination_check 会把它
        # 从 failed 排除而按 cancelled 接受，让同轮 pass 掩盖真实失败并误报 converged。
        if task["status"] in ("done", "cancelled", "failed"):
            raise LoopStateError(f"task {task_id} 状态 {task['status']} 不可取消")
        if task["status"] == "in_flight" and not confirmed_stopped:
            raise LoopStateError("取消在途/预约任务前须确认宿主 worker 已停止 --confirmed-stopped")
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
        # converged 是真正的成功收敛：锁外 termination_check 后等锁跨期限不得落 converged；
        # failed/cancelled/interrupted 是终态逃生口，即使已超时也必须允许落盘。
        self._persist(reject_stop=status == "converged", reject_timeout=status == "converged")
        self._emit(f"run_{status}", **fields)

    def run_converged(self, summary: str = "") -> None:
        self._require_active()
        from loop_control import LoopStateError as ControlError
        from loop_control import termination_check

        try:
            verdict = termination_check(self, self._now())
        except ControlError as exc:
            raise LoopStateError(str(exc)) from exc
        if verdict["verdict"] != "converged":
            raise LoopStateError("尚未满足收敛条件，拒绝 finish converged")
        self._finish("converged", summary=summary)

    def _require_workers_stopped(self, confirmed_stopped: bool) -> None:
        live = any(
            task.get("status") == "in_flight" for rnd in self.state["rounds"] for task in rnd.get("tasks", {}).values()
        )
        if live and not confirmed_stopped:
            raise LoopStateError("结束有在途/预约的 run 前须核实宿主 worker 已停止 --confirmed-stopped")

    def run_failed(self, reason: str, *, confirmed_stopped: bool = False) -> None:
        self._require_workers_stopped(confirmed_stopped)
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

    def run_cancelled(self, reason: str, *, confirmed_stopped: bool = False) -> None:
        self._require_workers_stopped(confirmed_stopped)
        self._finish("cancelled", reason=reason)

    # -- 恢复 -------------------------------------------------------------

    def resume(self) -> dict[str, Any]:
        """校验状态一致性并给出续跑动作。

        interrupted 恢复不假定宿主 worker 已停止：保留 in_flight/预约，
        由调用方核实后登记结果、继续等待或安全回收。
        """
        if self.state["status"] == "active":
            pass  # 正常续跑
        elif self.state["status"] == "interrupted":
            # Interruption does not prove that a host worker stopped. Keep every
            # in-flight/reserved attempt occupied until result or reconciliation.
            self.state["status"] = "active"
            self._persist()
            self._emit("run_resumed", fromStatus="interrupted", reverted=[])
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
            if task["status"] == "in_flight" and task.get("dispatchPhase") != "reserved"
        ]
        reserved = [
            f"r{rnd['index']}/{tid}"
            for rnd in self.state["rounds"]
            for tid, task in rnd.get("tasks", {}).items()
            if task["status"] == "in_flight" and task.get("dispatchPhase") == "reserved"
        ]
        actions = []
        if not open_rounds:
            actions.append("round_start：开启下一轮")
        if pending:
            actions.append("reserve：先预约 pending 任务 " + ", ".join(pending))
        if reserved:
            actions.append("核对宿主预约并登记或确认停止后 abandon：" + ", ".join(reserved))
        if in_flight:
            actions.append(
                "wait 在途任务 " + ", ".join(in_flight) + " 并记录 task result；"
                "心跳超时仅为嫌疑；宿主确认该 worker 已停止后用 stale --reap --task rN/id --confirmed-stopped 回收"
            )
        return {
            "status": self.state["status"],
            "openRounds": open_rounds,
            "pending": pending,
            "inFlight": in_flight,
            "reserved": reserved,
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
    """Atomically replace STOP without truncating an existing hard-link target."""
    run_dir = Path(run_dir)
    _assert_directory_nosymlink(run_dir)
    target = run_dir / STOP_FILENAME
    with _exclusive_file_lock(run_dir / ".lock"):
        try:
            _atomic_write_json(target, payload)
        except OSError as exc:
            raise LoopStateError(f"无法写入 STOP 哨兵 {target}: {exc}") from exc


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
        choices=["add", "reserve", "spawn", "abandon", "heartbeat", "report", "result", "backoff", "cancel"],
    )
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--agent-type")
    p.add_argument("--description")
    p.add_argument("--agent")
    p.add_argument("--submission")
    p.add_argument("--reservation", help="预约及尝试 fencing token")
    p.add_argument("--source", choices=["worker", "host"], help="worker 心跳或宿主验证过的存活观察")
    p.add_argument("--confirmed-stopped", action="store_true", help="调用者已向宿主核实相关 worker 未运行")
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
    p.add_argument("--confirmed-stopped", action="store_true", help="调用者已核实所有在途宿主 worker 停止")

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
            elif args.action == "reserve":
                token = store.task_reserve(args.round, args.id)
                _emit_json({"ok": True, "reservation": token})
            elif args.action == "spawn":
                if not args.agent or not args.submission:
                    raise LoopStateError("task spawn 需要 --agent 与 --submission")
                store.task_spawn(args.round, args.id, args.agent, args.submission, reservation=args.reservation)
                _emit_json(
                    {
                        "ok": True,
                        "warning": None if args.reservation else "旧式事后登记无法约束宿主启动竞态；请先 reserve",
                    }
                )
            elif args.action == "abandon":
                outcome = store.task_abandon(
                    args.round, args.id, args.reservation or "", confirmed_stopped=args.confirmed_stopped
                )
                _emit_json({"outcome": outcome})
            elif args.action == "heartbeat":
                store.task_heartbeat(
                    args.round, args.id, source=args.source, submission=args.submission, reservation=args.reservation
                )
                _emit_json({"ok": True, "workerLivenessUpdated": bool(args.source)})
            elif args.action in ("report", "result"):
                if args.action == "result" and not args.outcome:
                    raise LoopStateError("task result 需要 --outcome ok|fail")
                if args.action == "report" and not args.result:
                    raise LoopStateError("task report 需要 --result，提供真实交接的 JSON 对象或 @文件")
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
                if args.action == "report":
                    store.task_report(
                        args.round, args.id, result, submission=args.submission, reservation=args.reservation
                    )
                    _emit_json({"ok": True, "status": "in_flight"})
                else:
                    outcome_state = store.task_result(
                        args.round,
                        args.id,
                        args.outcome,
                        args.error,
                        result,
                        submission=args.submission,
                        reservation=args.reservation,
                        confirmed_stopped=args.confirmed_stopped,
                    )
                    _emit_json({"outcome": outcome_state})
            elif args.action == "backoff":
                if args.until is None:
                    raise LoopStateError("task backoff 需要 --until <epoch秒>")
                store.task_backoff_until(args.round, args.id, args.until)
                _emit_json({"ok": True})
            else:
                store.task_cancel(
                    args.round, args.id, args.reason or "unspecified", confirmed_stopped=args.confirmed_stopped
                )
                _emit_json({"ok": True})
        elif args.command == "finish":
            store = _store_from(args)
            if args.status == "converged":
                store.run_converged(args.summary)
            elif args.status == "failed":
                store.run_failed(args.reason or "unspecified", confirmed_stopped=args.confirmed_stopped)
            elif args.status == "interrupted":
                affected = store.run_interrupted(args.reason or "unspecified")
                _emit_json({"affected": affected})
                return 0
            else:
                store.run_cancelled(args.reason or "unspecified", confirmed_stopped=args.confirmed_stopped)
            _emit_json({"ok": True})
        elif args.command == "events":
            _emit_json(_store_from(args).events())
        return 0
    except LoopStateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

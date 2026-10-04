#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""2026-10-04 chain/loop 审计回归测试。

覆盖问题台账（见 `.spec/specs/2026-10-04_fix-chain-loop-mechanisms/`）中
team-loop 侧修复：#4/#5/#6/#7/#8/#13/#14/#15/#17/#18/#19。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
HOOKS = SKILL_DIR / "hooks"

sys.path.insert(0, str(SCRIPTS))

from loop_control import (  # noqa: E402
    admission,
    reap_stale_tasks,
    stale_tasks,
    termination_check,
)
from loop_state import (  # noqa: E402
    LoopStateError,
    LoopStore,
    default_run_dir,
    find_run_root,
    utc_now,
)


def make_store(tmp_path, name="run-a", config=None):
    run_dir = default_run_dir(tmp_path, name)
    store = LoopStore.init(run_dir, "审计回归", "until-converged", config=config)
    return store


def run_hook(module_name: str, stdin_payload) -> tuple[int, str, str]:
    script = HOOKS / f"{module_name}.py"
    completed = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(stdin_payload) if isinstance(stdin_payload, dict) else (stdin_payload or ""),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.returncode, completed.stdout, completed.stderr


def run_cli(script: Path, *args) -> tuple[int, str, str]:
    completed = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.returncode, completed.stdout, completed.stderr


class TestRoundEndLiveTasks:
    def test_round_end_rejects_live_in_flight(self, tmp_path):
        store = make_store(tmp_path)
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "a", "s")
        with pytest.raises(LoopStateError, match="非终态任务"):
            store.round_end("pass")
        assert store.state["rounds"][0]["status"] == "open"

    def test_round_end_rejects_pending(self, tmp_path):
        store = make_store(tmp_path)
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        with pytest.raises(LoopStateError, match="非终态任务"):
            store.round_end("pass")

    def test_round_end_allows_after_cancel_then_converges(self, tmp_path):
        store = make_store(tmp_path)
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "a", "s")
        store.task_cancel(1, "t1", "放弃")
        store.round_end("pass")
        verdict = termination_check(store, utc_now())
        assert verdict["stop"] is True and verdict["verdict"] == "converged"

    def test_spawn_on_closed_round_rejected_result_still_allowed(self, tmp_path):
        """#19：closed round 拒绝新 spawn；result/heartbeat 保留作旧状态恢复通道。"""
        store = make_store(tmp_path)
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_add(1, "t2", "worker", "d2")
        store.task_spawn(1, "t2", "a", "s")
        store.task_result(1, "t2", "ok")
        store.task_cancel(1, "t1", "放弃")
        store.round_end("pass")
        # 注入修复前的遗留状态：closed round 内挂着 pending 任务
        legacy = LoopStore(store.run_dir).load()
        legacy.state["rounds"][0]["tasks"]["t2"]["status"] = "pending"
        legacy._persist()
        with pytest.raises(LoopStateError, match="已关闭"):
            legacy.task_spawn(1, "t2", "a3", "s3")
        legacy.state["rounds"][0]["tasks"]["t2"]["status"] = "in_flight"
        legacy._persist()
        legacy.task_result(1, "t2", "fail", "遗留状态恢复")  # result 在 closed round 仍可用
        assert legacy.state["rounds"][0]["tasks"]["t2"]["status"] in ("pending", "failed")


class TestSpawnFromFailed:
    def test_exhausted_task_cannot_be_respawned(self, tmp_path):
        store = make_store(tmp_path, config={"retry": {"maxAttempts": 1, "baseSec": 0.1, "maxSec": 0.1, "jitter": 0.0}})
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "a", "s")
        assert store.task_result(1, "t1", "fail", "boom") == "exhausted"
        with pytest.raises(LoopStateError, match="不可 spawn"):
            store.task_spawn(1, "t1", "a2", "s2")
        task = store.state["rounds"][0]["tasks"]["t1"]
        assert task["status"] == "failed" and task["attempts"] == 1

    def test_retry_pending_respawn_still_works(self, tmp_path):
        store = make_store(tmp_path)
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "a", "s")
        assert store.task_result(1, "t1", "fail", "boom") == "retry"
        store.task_spawn(1, "t1", "a2", "s2")  # pending -> in_flight 正常
        assert store.state["rounds"][0]["tasks"]["t1"]["status"] == "in_flight"


class TestTerminationFailRounds:
    def test_closed_fail_round_with_all_done_is_not_converged(self, tmp_path):
        """#5：失败轮 + 全任务 done 不得判 converged。"""
        store = make_store(tmp_path)
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "a", "s")
        store.task_result(1, "t1", "ok")
        store.round_end("fail")
        verdict = termination_check(store, utc_now())
        assert verdict["verdict"] != "converged"
        assert verdict["stop"] is False  # 还有轮数预算 -> 开新轮

    def test_closed_fail_round_at_maxrounds_stops_with_reason(self, tmp_path):
        store = make_store(tmp_path, config={"maxRounds": 1})
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "a", "s")
        store.task_result(1, "t1", "ok")
        store.round_end("fail")
        verdict = termination_check(store, utc_now())
        assert verdict["stop"] is True and verdict["verdict"] == "stopped"
        assert any("maxRounds" in r for r in verdict["reasons"])

    def test_closed_pass_rounds_still_converge(self, tmp_path):
        store = make_store(tmp_path)
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "a", "s")
        store.task_result(1, "t1", "ok")
        store.round_end("pass")
        verdict = termination_check(store, utc_now())
        assert verdict["stop"] is True and verdict["verdict"] == "converged"


class TestStaleReap:
    def test_reap_moves_stale_in_flight_to_retry_with_backoff(self, tmp_path):
        """#14：stale in_flight 按失败进重试路径并释放并发额度。"""
        store = make_store(
            tmp_path, config={"stalenessSec": 1.0, "retry": {"baseSec": 0.2, "maxSec": 0.2, "jitter": 0.0}}
        )
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "a", "s")
        now = utc_now() + 10  # 心跳已超过 stalenessSec=1.0
        assert stale_tasks(store, now) == ["r1/t1"]
        result = reap_stale_tasks(store, now)
        assert result["reaped"] == [{"task": "r1/t1", "outcome": "retry"}]
        task = store.state["rounds"][0]["tasks"]["t1"]
        assert task["status"] == "pending"
        assert task["backoffUntil"] is not None
        assert admission(store, now)["admit"] is True  # 并发槽已释放

    def test_reap_exhausted_task_becomes_failed(self, tmp_path):
        store = make_store(
            tmp_path,
            config={"stalenessSec": 1.0, "retry": {"maxAttempts": 1, "baseSec": 0.1, "maxSec": 0.1, "jitter": 0.0}},
        )
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "a", "s")
        now = utc_now() + 10
        result = reap_stale_tasks(store, now)
        assert result["reaped"][0]["outcome"] == "exhausted"
        assert store.state["rounds"][0]["tasks"]["t1"]["status"] == "failed"
        verdict = termination_check(store, now)
        assert any("failed" in r for r in verdict["reasons"])

    def test_resume_lists_in_flight_with_reap_hint(self, tmp_path):
        store = make_store(tmp_path)
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "a", "s")
        info = store.resume()
        assert info["inFlight"] == ["r1/t1"]
        assert any("reap" in a for a in info["nextActions"])


class TestBackoffRunConfig:
    def test_backoff_cli_uses_run_config(self, tmp_path):
        """#17：backoff --run-dir 用 run 自身 retry 配置。"""
        store = make_store(tmp_path, config={"retry": {"baseSec": 60.0, "maxSec": 60.0, "jitter": 0.0}})
        store.round_start("r1")
        code, out, err = run_cli(
            SCRIPTS / "loop_control.py", "backoff", "--attempt", "1", "--run-dir", str(store.run_dir)
        )
        assert code == 0, err
        data = json.loads(out)
        assert data["delaySec"] == 60.0
        assert data["retryConfigSource"] == str(store.run_dir)

    def test_backoff_cli_default_without_run_dir(self, tmp_path):
        code, out, err = run_cli(SCRIPTS / "loop_control.py", "backoff", "--attempt", "1", "--seed", "7")
        assert code == 0, err
        assert json.loads(out)["retryConfigSource"] == "default"


class TestFindRunRoot:
    def test_claude_marker_no_longer_selects_root(self, tmp_path):
        """#15：.claude 不再作为 root 标记（防 run 泄漏到 $HOME）。"""
        fake_home = tmp_path / "home-with-claude"
        (fake_home / ".claude").mkdir(parents=True)
        project = fake_home / "notes"
        project.mkdir()
        # tmp_path 之上不存在 .agents/.git 时才返回 None；pytest tmp 树满足
        assert find_run_root(project) is None or find_run_root(project) == project, find_run_root(project)

    def test_git_and_agents_markers_still_select_root(self, tmp_path):
        git_root = tmp_path / "gitroot"
        (git_root / ".git").mkdir(parents=True)
        (git_root / "proj").mkdir()
        assert find_run_root(git_root / "proj") == git_root
        agents_root = tmp_path / "agentsroot"
        (agents_root / ".agents").mkdir(parents=True)
        (agents_root / "proj").mkdir()
        assert find_run_root(agents_root / "proj") == agents_root


class TestCliInputErrors:
    def test_init_bad_config_json_exits_2(self, tmp_path):
        code, out, err = run_cli(
            SCRIPTS / "loop_state.py", "init", "--goal", "g", "--config", "{bad json", "--run-dir", str(tmp_path / "r")
        )
        assert code == 2 and "error" in err and "Traceback" not in err

    def test_init_retry_scalar_exits_2(self, tmp_path):
        code, out, err = run_cli(
            SCRIPTS / "loop_state.py",
            "init",
            "--goal",
            "g",
            "--config",
            '{"retry": 5}',
            "--run-dir",
            str(tmp_path / "r2"),
        )
        assert code == 2 and "error" in err and "Traceback" not in err

    def test_result_bad_json_exits_2(self, tmp_path):
        store = make_store(tmp_path, name="r3")
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "a", "s")
        code, out, err = run_cli(
            SCRIPTS / "loop_state.py",
            "task",
            "result",
            "--run-dir",
            str(store.run_dir),
            "--round",
            "1",
            "--id",
            "t1",
            "--outcome",
            "ok",
            "--result",
            "{bad",
        )
        assert code == 2 and "error" in err and "Traceback" not in err

    def test_route_file_missing_exits_2(self):
        for script in (SCRIPTS / "loop_route.py",):
            code, out, err = run_cli(script, "--file", "/nonexistent-audit.md")
            assert code == 2 and "无法读取文件" in err and "Traceback" not in err


class TestStopGuardSymlinkStop:
    def test_symlink_stop_blocks_instead_of_crashing(self, tmp_path):
        """#13：STOP symlink 时守卫必须 fail-closed 输出 block，而不是崩栈放行。"""
        store = make_store(tmp_path, name="guard-run")
        store.round_start("r1")
        store.task_add(1, "t1", "worker", "d")
        store.task_spawn(1, "t1", "agent-x", "s")
        (store.run_dir / "STOP").symlink_to("/etc/hostname")
        code, out, err = run_hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(tmp_path)})
        assert code == 0, err
        data = json.loads(out)
        assert data["decision"] == "block"
        assert "无法安全评估" in data["reason"]
        assert "Traceback" not in err


class TestTeammateGateMatching:
    def _seed(self, tmp_path):
        (tmp_path / ".agents").mkdir(exist_ok=True)
        store = LoopStore.init(default_run_dir(tmp_path, "gate-run"), "gate", "until-converged")
        store.round_start("r1")
        store.task_add(1, "a", "worker", "d")
        store.task_spawn(1, "a", "review-worker", "s1")
        return store

    def test_unanchored_substring_no_longer_matches(self, tmp_path):
        self._seed(tmp_path)
        code, out, err = run_hook(
            "loop_teammate_gate", {"hook_event_name": "TeammateIdle", "teammate_name": "worker", "cwd": str(tmp_path)}
        )
        assert code == 0, err

    def test_exact_agent_id_matches_and_blocks(self, tmp_path):
        self._seed(tmp_path)
        code, out, err = run_hook(
            "loop_teammate_gate",
            {"hook_event_name": "TeammateIdle", "teammate_name": "review-worker", "cwd": str(tmp_path)},
        )
        assert code == 2 and "r1/a" in err, err

    def test_separator_suffix_still_matches(self, tmp_path):
        store = self._seed(tmp_path)
        task = store.state["rounds"][0]["tasks"]["a"]
        task["agentId"] = "worker-2"
        store._persist()
        code, out, err = run_hook(
            "loop_teammate_gate", {"hook_event_name": "TeammateIdle", "teammate_name": "worker", "cwd": str(tmp_path)}
        )
        assert code == 2 and "r1/a" in err, err

    def test_corrupt_run_fails_closed(self, tmp_path):
        self._seed(tmp_path)
        bad_dir = default_run_dir(tmp_path, "corrupt-run")
        bad_dir.mkdir(parents=True)
        (bad_dir / "run.json").write_text("{not json", encoding="utf-8")
        code, out, err = run_hook(
            "loop_teammate_gate",
            {"hook_event_name": "TeammateIdle", "teammate_name": "review-worker", "cwd": str(tmp_path)},
        )
        assert code == 2 and "损坏" in err, err

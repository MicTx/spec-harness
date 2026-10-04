"""hook 协议测试：stdin JSON -> decision，对齐 Claude Code hooks 官方契约。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
HOOKS = SKILL_DIR / "hooks"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HOOKS))


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


def make_run(tmp_path, status="active", with_inflight=True, name="run-1"):
    from loop_state import LoopStore, default_run_dir, utc_now

    (tmp_path / ".agents").mkdir(exist_ok=True)
    run_dir = default_run_dir(tmp_path, name)
    # 用真实时钟基准：Stop 守卫会用 utc_now() 做超时判定
    store = LoopStore.init(run_dir, "循环任务直至通过", "until-converged", now=utc_now)
    store.round_start("r")
    store.task_add(1, "t1", "worker", "任务一")
    if with_inflight:
        store.task_spawn(1, "t1", "agent-x", "sub-1")
    if status != "active":
        if status == "interrupted":
            store.run_interrupted("测试")
        elif status == "converged":
            store.run_converged("done")
    return store


class TestStopGuard:
    def test_no_run_allows(self, tmp_path):
        code, out, _ = run_hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(tmp_path)})
        assert code == 0 and out == ""

    def test_unconverged_run_blocks_with_next_action(self, tmp_path):
        make_run(tmp_path)
        code, out, _ = run_hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(tmp_path)})
        assert code == 0
        data = json.loads(out)
        assert data["decision"] == "block"
        assert "wait" in data["reason"] and "t1" in data["reason"]

    def test_stop_hook_active_silent(self, tmp_path):
        make_run(tmp_path)
        code, out, _ = run_hook(
            "loop_stop_guard",
            {"hook_event_name": "Stop", "cwd": str(tmp_path), "stop_hook_active": True},
        )
        assert code == 0 and out == ""

    def test_interrupted_run_allows(self, tmp_path):
        make_run(tmp_path, status="interrupted")
        code, out, _ = run_hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(tmp_path)})
        assert code == 0 and out == ""

    def test_converged_pending_finish_blocks_then_terminal_allows(self, tmp_path):
        make_run(tmp_path, status="active", with_inflight=False)
        code, out, _ = run_hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(tmp_path)})
        assert code == 0
        assert json.loads(out)["decision"] == "block"  # 未记录 finish -> 提示落终态
        # 落终态后放行
        from loop_state import LoopStore

        store = LoopStore(tmp_path / ".agents" / "runtime" / "loop" / "run-1").load()
        if store.state["rounds"][0]["tasks"]["t1"]["status"] == "in_flight":
            store.task_result(1, "t1", "ok")
        store.run_converged("done")
        code, out, _ = run_hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(tmp_path)})
        assert code == 0 and out == ""

    def test_multiple_active_runs_fail_closed(self, tmp_path):
        make_run(tmp_path)
        make_run(tmp_path, name="run-2")
        code, out, _ = run_hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(tmp_path)})
        data = json.loads(out)
        assert data["decision"] == "block"
        assert "多个活跃 run" in data["reason"]

    def test_external_stop_blocks_with_finish_instruction(self, tmp_path):
        store = make_run(tmp_path)
        (store.run_dir / "STOP").write_text("{}", encoding="utf-8")
        code, out, _ = run_hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(tmp_path)})
        data = json.loads(out)
        assert data["decision"] == "block"
        assert "外部停止" in data["reason"]

    def test_corrupt_run_fail_closed_then_recovers(self, tmp_path):
        # check 轮回归：损坏 run 不得静默放行（fail-closed），排查后放行
        store = make_run(tmp_path)
        (store.run_dir / "run.json").write_text("{oops", encoding="utf-8")
        code, out, _ = run_hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(tmp_path)})
        data = json.loads(out)
        assert data["decision"] == "block"
        assert "损坏" in data["reason"]
        # 修复（这里用删除废弃目录模拟处置）后放行
        import shutil

        shutil.rmtree(store.run_dir)
        code, out, _ = run_hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(tmp_path)})
        assert code == 0 and out == ""


class TestTeammateGate:
    def test_inflight_teammate_exit2(self, tmp_path):
        make_run(tmp_path)
        payload = {
            "hook_event_name": "TeammateIdle",
            "teammate_name": "agent-x",
            "cwd": str(tmp_path),
        }
        code, _, err = run_hook("loop_teammate_gate", payload)
        assert code == 2
        assert "in_flight" in err and "task result" in err

    def test_no_run_allows(self, tmp_path):
        code, out, _ = run_hook("loop_teammate_gate", {"hook_event_name": "TeammateIdle", "cwd": str(tmp_path)})
        assert code == 0 and out == ""

    def test_unknown_teammate_allows(self, tmp_path):
        make_run(tmp_path)
        code, out, _ = run_hook(
            "loop_teammate_gate",
            {"hook_event_name": "TeammateIdle", "teammate_name": "someone-else", "cwd": str(tmp_path)},
        )
        assert code == 0 and out == ""

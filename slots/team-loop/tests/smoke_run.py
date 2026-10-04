#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""端到端 smoke：用真实 CLI 子进程走通 init -> fail -> backoff -> retry -> converge。

验证对象：loop_state.py / loop_control.py 的 CLI 面（README 协议里 agent 实际敲的命令）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
STATE = SKILL_DIR / "scripts" / "loop_state.py"
CONTROL = SKILL_DIR / "scripts" / "loop_control.py"


def run(script: Path, *args: str, stdin: str | None = None) -> dict:
    completed = subprocess.run(
        [sys.executable, str(script), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise SystemExit(f"FAIL {' '.join(args)} -> exit {completed.returncode}: {completed.stderr.strip()}")
    return json.loads(completed.stdout) if completed.stdout.strip() else {}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loop-smoke-") as td:
        root = Path(td)
        # 项目根放 .agents 标记，模拟真实项目
        (root / ".agents").mkdir()

        # 1. init
        info = run(STATE, "init", "--goal", "循环抽取直至校验通过", "--mode", "until-converged", "--root", str(root))
        run_dir = Path(info["runDir"])
        assert info["status"] == "active", info

        # 2. round + task
        assert run(STATE, "round", "start", "--goal", "第一轮", "--run-dir", str(run_dir))["round"] == 1
        run(
            STATE,
            "task",
            "add",
            "--round",
            "1",
            "--id",
            "t1",
            "--agent-type",
            "worker",
            "--description",
            "解析并输出验证证据",
            "--run-dir",
            str(run_dir),
        )
        run(
            STATE,
            "task",
            "add",
            "--round",
            "1",
            "--id",
            "t2",
            "--agent-type",
            "worker",
            "--description",
            "解析其余部分",
            "--run-dir",
            str(run_dir),
        )

        # 3. 并发准入 + spawn
        admit = run(CONTROL, "admit", "--run-dir", str(run_dir))
        assert admit["admit"] is True
        run(
            STATE,
            "task",
            "spawn",
            "--round",
            "1",
            "--id",
            "t1",
            "--agent",
            "agent-1",
            "--submission",
            "sub-1",
            "--run-dir",
            str(run_dir),
        )
        run(STATE, "task", "heartbeat", "--round", "1", "--id", "t1", "--run-dir", str(run_dir))

        # 4. 失败 -> 重试路径 + 退避
        outcome = run(
            STATE,
            "task",
            "result",
            "--round",
            "1",
            "--id",
            "t1",
            "--outcome",
            "fail",
            "--error",
            "模拟超时",
            "--run-dir",
            str(run_dir),
        )
        assert outcome["outcome"] == "retry", outcome
        delay = run(CONTROL, "backoff", "--attempt", "1", "--seed", "1")["delaySec"]
        assert 0.0 <= delay <= 5.0
        run(
            STATE,
            "task",
            "backoff",
            "--round",
            "1",
            "--id",
            "t1",
            "--until",
            str(time.time() + delay),
            "--run-dir",
            str(run_dir),
        )

        # 5. 退避到期重试 -> 成功
        time.sleep(delay + 0.05)
        admit = run(CONTROL, "admit", "--run-dir", str(run_dir))
        assert admit["admit"] is True
        run(
            STATE,
            "task",
            "spawn",
            "--round",
            "1",
            "--id",
            "t1",
            "--agent",
            "agent-1b",
            "--submission",
            "sub-1b",
            "--run-dir",
            str(run_dir),
        )
        outcome = run(
            STATE,
            "task",
            "result",
            "--round",
            "1",
            "--id",
            "t1",
            "--outcome",
            "ok",
            "--result",
            '{"evidence": "tests-pass"}',
            "--run-dir",
            str(run_dir),
        )
        assert outcome["outcome"] == "done"

        # t2 完成后全绿
        run(
            STATE,
            "task",
            "spawn",
            "--round",
            "1",
            "--id",
            "t2",
            "--agent",
            "agent-2",
            "--submission",
            "sub-2",
            "--run-dir",
            str(run_dir),
        )
        run(STATE, "task", "result", "--round", "1", "--id", "t2", "--outcome", "ok", "--run-dir", str(run_dir))

        # 6. 终止判定（round 还 open -> continue）
        verdict = run(CONTROL, "terminate", "--run-dir", str(run_dir))
        assert verdict["verdict"] == "continue", verdict
        run(STATE, "round", "end", "--decision", "pass", "--run-dir", str(run_dir))
        verdict = run(CONTROL, "terminate", "--run-dir", str(run_dir))
        assert verdict["verdict"] == "converged", verdict

        # 7. finish + 状态断言
        run(STATE, "finish", "converged", "--summary", "全部完成", "--run-dir", str(run_dir))
        final = run(STATE, "status", "--run-dir", str(run_dir))
        assert final["status"] == "converged" and final["converged"] is True

        # 8. 事件日志完整序列
        events = [e["type"] for e in run(STATE, "events", "--run-dir", str(run_dir))]
        expected = [
            "run_init",
            "round_start",
            "task_add",
            "task_add",
            "task_spawn",
            "task_retry_scheduled",
            "task_spawn",
            "task_result",
            "task_spawn",
            "task_result",
            "round_end",
            "run_converged",
        ]
        assert events == expected, (events, expected)

        # 9. 中断/恢复路径（第二个 run）
        info2 = run(STATE, "init", "--goal", "第二轮任务", "--mode", "until-converged", "--root", str(root))
        run_dir2 = Path(info2["runDir"])
        run(STATE, "round", "start", "--goal", "r", "--run-dir", str(run_dir2))
        run(
            STATE,
            "task",
            "add",
            "--round",
            "1",
            "--id",
            "a1",
            "--agent-type",
            "worker",
            "--description",
            "x",
            "--run-dir",
            str(run_dir2),
        )
        run(
            STATE,
            "task",
            "spawn",
            "--round",
            "1",
            "--id",
            "a1",
            "--agent",
            "ag",
            "--submission",
            "sb",
            "--run-dir",
            str(run_dir2),
        )
        interrupted = run(CONTROL, "interrupt", "--reason", "smoke", "--run-dir", str(run_dir2))
        assert interrupted["inFlight"] == ["r1/a1"]
        resumed = run(STATE, "resume", "--run-dir", str(run_dir2))
        assert resumed["status"] == "active" and "r1/a1" in resumed["pending"]
        run(STATE, "finish", "cancelled", "--reason", "smoke 收尾", "--run-dir", str(run_dir2))

    print("smoke: OK（init->retry->converge + interrupt->resume 全链路通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

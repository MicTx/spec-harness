#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Exercise the CLI protocol with simulated worker ids, never actual model calls."""
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


def run(script: Path, *args: str) -> dict:
    completed = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, timeout=60)
    if completed.returncode:
        raise SystemExit(f"FAIL {' '.join(args)} -> exit {completed.returncode}: {completed.stderr.strip()}")
    return json.loads(completed.stdout) if completed.stdout.strip() else {}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loop-smoke-") as td:
        root = Path(td)
        (root / ".agents").mkdir()
        config = json.dumps({"maxRounds": 1, "budgetTasks": 2,
                             "retry": {"maxAttempts": 2, "baseSec": 0.01, "maxSec": 0.01, "jitter": 0}})
        info = run(STATE, "init", "--goal", "循环直至通过", "--root", str(root), "--config", config)
        rd = info["runDir"]
        run(STATE, "round", "start", "--goal", "验证", "--run-dir", rd)
        base = ("--run-dir", rd, "--round", "1", "--id", "t1")
        run(STATE, "task", "add", *base, "--agent-type", "worker", "--description", "验证")
        token1 = run(STATE, "task", "reserve", *base)["reservation"]
        run(STATE, "task", "spawn", *base, "--agent", "mock-a", "--submission", "mock-s1", "--reservation", token1)
        assert run(CONTROL, "terminate", "--run-dir", rd)["verdict"] == "continue"
        run(STATE, "task", "heartbeat", *base, "--source", "worker", "--submission", "mock-s1", "--reservation", token1)
        assert run(STATE, "task", "result", *base, "--outcome", "fail", "--error", "模拟失败",
                   "--reservation", token1, "--confirmed-stopped")["outcome"] == "retry"
        time.sleep(0.03)  # wait for the deterministic 10ms configured backoff
        token2 = run(STATE, "task", "reserve", *base)["reservation"]
        run(STATE, "task", "spawn", *base, "--agent", "mock-b", "--submission", "mock-s2", "--reservation", token2)
        assert run(CONTROL, "terminate", "--run-dir", rd)["verdict"] == "continue"
        run(STATE, "task", "result", *base, "--outcome", "ok", "--reservation", token2, "--confirmed-stopped",
            "--result", '{"evidence":"simulated-tests-pass"}')
        run(STATE, "round", "end", "--decision", "pass", "--run-dir", rd)
        assert run(CONTROL, "terminate", "--run-dir", rd)["verdict"] == "converged"
        run(STATE, "finish", "converged", "--summary", "完成", "--run-dir", rd)
        assert run(STATE, "status", "--run-dir", rd)["converged"] is True
        events = [event["type"] for event in run(STATE, "events", "--run-dir", rd)]
        assert events == ["run_init", "round_start", "task_add", "task_reserved", "task_spawn",
                          "task_retry_scheduled", "task_reserved", "task_spawn", "task_result",
                          "round_end", "run_converged"]

        info2 = run(STATE, "init", "--goal", "中断", "--root", str(root))
        rd2 = info2["runDir"]
        run(STATE, "round", "start", "--goal", "r", "--run-dir", rd2)
        base2 = ("--run-dir", rd2, "--round", "1", "--id", "t1")
        run(STATE, "task", "add", *base2, "--agent-type", "worker", "--description", "模拟")
        token = run(STATE, "task", "reserve", *base2)["reservation"]
        run(STATE, "task", "spawn", *base2, "--agent", "mock", "--submission", "mock-s", "--reservation", token)
        assert run(CONTROL, "interrupt", "--run-dir", rd2)["inFlight"] == ["r1/t1"]
        resumed = run(STATE, "resume", "--run-dir", rd2)
        assert resumed["inFlight"] == ["r1/t1"] and not resumed["pending"]
        run(STATE, "task", "result", *base2, "--outcome", "ok", "--reservation", token, "--confirmed-stopped")
        run(STATE, "round", "end", "--decision", "pass", "--run-dir", rd2)
        run(STATE, "finish", "converged", "--run-dir", rd2)
    print("smoke: OK（模拟预约->失败->退避->收敛 + 中断->恢复）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

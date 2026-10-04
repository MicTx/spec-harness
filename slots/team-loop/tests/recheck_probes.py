#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""check 轮独立复核探针：不信任自报，用未调参过的输入重新取证。

场景均为上一轮测试/清单未覆盖的边界。任何 FAIL 都回写任务包处理。
"""

# CLI probe vectors intentionally preserve complete command records for audit readability.
# ruff: noqa: E501

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
STATE = SKILL / "scripts" / "loop_state.py"
CONTROL = SKILL / "scripts" / "loop_control.py"
ROUTE = SKILL / "scripts" / "loop_route.py"
HOOKS = SKILL / "hooks"

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -> {detail}" if detail and not cond else ""))


def cli(script: Path, *args: str, stdin: str | None = None, cwd: str | None = None):
    p = subprocess.run(
        [sys.executable, str(script), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=cwd,
    )
    return p.returncode, p.stdout, p.stderr


def hook(name: str, payload: dict | str, cwd: str | None = None):
    data = json.dumps(payload) if isinstance(payload, dict) else payload
    p = subprocess.run(
        [sys.executable, str(HOOKS / f"{name}.py")],
        input=data,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=cwd,
    )
    return p.returncode, p.stdout, p.stderr


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loop-check-") as td:
        root = Path(td)
        (root / ".agents").mkdir()

        # --- A. 路由：未调参输入 -------------------------------------------
        cases = [
            ("把这份报告改到领导满意为止", True),  # 收敛语义（直至…满意）
            ("keep iterating on the API until the flaky test passes", True),
            ("run 3 rounds of parallel subagent reviews on the PR", True),
            ("汇总今天的会议记录", False),
            ("What is the capital of France?", False),
            ("重构这个函数", False),
        ]
        for text, expected in cases:
            rc, out, _ = cli(ROUTE, "--json", "--text", text)
            d = json.loads(out)
            check(f"route: {text[:24]!r} -> {d['loopRecommended']}", d["loopRecommended"] == expected, out)

        rc, out, _ = cli(ROUTE, "--json", stdin="第一行循环目标\n第二行：直至全部通过")
        d = json.loads(out)
        check("route: 多行 stdin", rc == 0 and d["loopRecommended"] is True, out)

        # --- B. 状态机：新边界 ----------------------------------------------
        info = json.loads(cli(STATE, "init", "--goal", "复核", "--root", str(root))[1])
        rd = Path(info["runDir"])

        # B1: 空 run 直接 converge（无任务无轮次）
        v = json.loads(cli(CONTROL, "terminate", "--run-dir", str(rd))[1])
        check("terminate: 空 run 判 continue（不假收敛）", v["verdict"] == "continue", str(v))

        # B2: 缺 run-dir 的 status 必须显式失败（argparse 缺参 = 标准显式契约）
        rc, _, err = cli(STATE, "status")
        check(
            "status: 缺 --run-dir 非零退出且显式报错",
            rc != 0 and ("required" in err or "run 不存在" in err),
            f"rc={rc} {err}",
        )

        # B3: 事件日志损坏 -> resume 必须显式报错，不静默
        (rd / "events.jsonl").write_text('{"ts":1,"type":"task_spawn","round":1,"task":"ghost"}\n', encoding="utf-8")
        rc, out, err = cli(STATE, "resume", "--run-dir", str(rd))
        check("resume: 引用不存在任务的日志 -> 显式报错", rc != 0 and "不一致" in err, f"rc={rc} {out} {err}")

        # B4: run.json 损坏（隔离场景：唯一 run）-> stop guard fail-closed block
        probe_root = root / "iso"
        (probe_root / ".agents").mkdir(parents=True)
        info_iso = json.loads(cli(STATE, "init", "--goal", "损坏隔离", "--root", str(probe_root))[1])
        (Path(info_iso["runDir"]) / "run.json").write_text("{oops", encoding="utf-8")
        rc, out, _ = hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(probe_root)})
        d = json.loads(out) if out.strip() else {}
        check(
            "stop guard: 唯一 run 状态损坏 -> block + 修复指引",
            d.get("decision") == "block" and "损坏" in d.get("reason", ""),
            out,
        )

        # B5: 损坏 run 排查后删除目录 -> 守卫放行（修复路径可走通）
        import shutil

        shutil.rmtree(Path(info_iso["runDir"]))
        rc, out, _ = hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(probe_root)})
        check("stop guard: 删除废弃损坏 run 后放行", rc == 0 and out == "", out)

        # --- C. Stop guard：未调参场景 ---------------------------------------
        # C1: cwd 缺失时回退 Path.cwd()（不崩）
        rc, out, _ = hook("loop_stop_guard", {"hook_event_name": "Stop"})
        check("stop guard: 无 cwd 字段不崩（当前目录无 run 放行）", rc == 0 and out == "", out)

        # C2: 非本 run 目录的 cwd -> 放行，不跨项目误伤
        other = root / "unrelated" / "deep"
        other.mkdir(parents=True)
        rc, out, _ = hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(other)})
        check("stop guard: 子目录向上找得到根 -> 按根状态处理", rc == 0, out)  # root 下无 active run

        info3 = json.loads(cli(STATE, "init", "--goal", "复核3 循环直至通过", "--root", str(root))[1])
        rd3 = Path(info3["runDir"])
        cli(STATE, "round", "start", "--goal", "r", "--run-dir", str(rd3))
        cli(
            STATE,
            "task",
            "add",
            "--round",
            "1",
            "--id",
            "a",
            "--agent-type",
            "worker",
            "--description",
            "d",
            "--run-dir",
            str(rd3),
        )
        cli(
            STATE,
            "task",
            "spawn",
            "--round",
            "1",
            "--id",
            "a",
            "--agent",
            "ag-1",
            "--submission",
            "sb",
            "--run-dir",
            str(rd3),
        )
        rc, out, _ = hook("loop_stop_guard", {"hook_event_name": "Stop", "cwd": str(other)})
        d = json.loads(out)
        check("stop guard: 子目录内 run 未收敛 -> block", d.get("decision") == "block", out)

        # C3: unknown hook event -> 全部静默
        rc, out, _ = hook("loop_stop_guard", {"hook_event_name": "PreCompact", "cwd": str(root)})
        check("stop guard: 非 Stop 事件静默", rc == 0 and out == "", out)

        # --- D. 并发准入：真实 CLI 链路 ---------------------------------------
        cfg = json.dumps({"concurrency": 1, "retry": {"maxAttempts": 2}})
        info4 = json.loads(cli(STATE, "init", "--goal", "并发复核", "--root", str(root), "--config", cfg)[1])
        rd4 = Path(info4["runDir"])
        cli(STATE, "round", "start", "--goal", "r", "--run-dir", str(rd4))
        for t in ("a", "b"):
            cli(
                STATE,
                "task",
                "add",
                "--round",
                "1",
                "--id",
                t,
                "--agent-type",
                "worker",
                "--description",
                "d",
                "--run-dir",
                str(rd4),
            )
        cli(
            STATE,
            "task",
            "spawn",
            "--round",
            "1",
            "--id",
            "a",
            "--agent",
            "ag",
            "--submission",
            "s",
            "--run-dir",
            str(rd4),
        )
        admit = json.loads(cli(CONTROL, "admit", "--run-dir", str(rd4))[1])
        check("admit: cap=1 时第二个任务被拒", admit["admit"] is False and admit["cap"] == 1, str(admit))
        # 重试路径不改并发语义：fail -> pending 仍占不在飞，admit 恢复
        cli(
            STATE,
            "task",
            "result",
            "--round",
            "1",
            "--id",
            "a",
            "--outcome",
            "fail",
            "--error",
            "x",
            "--run-dir",
            str(rd4),
        )
        admit = json.loads(cli(CONTROL, "admit", "--run-dir", str(rd4))[1])
        check("admit: fail 释放并发槽", admit["admit"] is True, str(admit))

        # --- E. Teammate gate： teammate_name 与 agentId 子串歧义 ------------
        # agentId="review-worker"，teammate_name="worker"（子串双向），不应误伤其它 run
        rc, _, err = hook(
            "loop_teammate_gate", {"hook_event_name": "TeammateIdle", "teammate_name": "worker", "cwd": str(root)}
        )
        check("teammate gate: 子串匹配到 run4 的 pending 任务不 exit2（无 in_flight）", rc == 0, err)
        cli(
            STATE,
            "task",
            "spawn",
            "--round",
            "1",
            "--id",
            "a",
            "--agent",
            "review-worker",
            "--submission",
            "s2",
            "--run-dir",
            str(rd4),
        )
        rc, _, err = hook(
            "loop_teammate_gate", {"hook_event_name": "TeammateIdle", "teammate_name": "worker", "cwd": str(root)}
        )
        # 无锚点子串匹配已收紧为等值/前缀+分隔符：worker 不得误配 review-worker 的任务
        check("teammate gate: 无锚点子串不再误配他人任务", rc == 0, err)
        rc, _, err = hook(
            "loop_teammate_gate",
            {"hook_event_name": "TeammateIdle", "teammate_name": "review-worker", "cwd": str(root)},
        )
        check("teammate gate: 精确 agentId 命中 in_flight -> exit 2", rc == 2 and "r1/a" in err, err)

        # --- F. 安装器：真实写入/回滚（临时项目，不碰 ~/.claude）-------------
        proj = root / "proj"
        (proj / ".claude").mkdir(parents=True)
        # 插槽形态：manifest 驱动的 install_slot_hooks.py（spec 仓库 scripts/）
        repo_root = SKILL.parent.parent
        install = repo_root / "scripts" / "install_slot_hooks.py"
        rc, out, _ = cli(
            install, "--slot", "team-loop", "--root", str(repo_root), "--scope", "project", "--project-root", str(proj)
        )
        check("install: 项目级写入成功", rc == 0, out)
        settings = json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))
        events = ("Stop", "TeammateIdle", "TaskCompleted")
        check(
            "install: 3 个事件注册（UserPromptSubmit 注入 hook 已随 897a42f 删除）",
            sum(len(settings["hooks"].get(e, [])) for e in events) == 3,
            str(settings),
        )
        # 官方 matcher 组 schema：组内必须有 hooks 数组，否则 Claude Code 整组忽略
        check(
            "install: 官方 matcher 组 schema（组内含 hooks 数组）",
            all(
                isinstance(g, dict) and isinstance(g.get("hooks"), list) and g["hooks"]
                for e in events
                for g in settings["hooks"].get(e, [])
            ),
            str(settings),
        )
        # 注入他人条目再装一次 -> 幂等且保留
        settings["hooks"]["Stop"].append({"type": "command", "command": "echo foreign"})
        (proj / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        cli(install, "--slot", "team-loop", "--root", str(repo_root), "--scope", "project", "--project-root", str(proj))
        settings = json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))

        def cmds_of(items):
            """收集事件数组里的全部 command，兼容官方 matcher 组与旧版裸条目。"""
            out = []
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("hooks"), list):
                    out.extend(e.get("command", "") for e in it["hooks"] if isinstance(e, dict))
                elif isinstance(it, dict) and "command" in it:
                    out.append(it["command"])
            return out

        stop_cmds = cmds_of(settings["hooks"]["Stop"])
        own = [c for c in stop_cmds if "slots/team-loop/hooks" in c]
        foreign = [c for c in stop_cmds if c == "echo foreign"]
        check("install: 幂等 + 保留他人条目", len(own) == 1 and len(foreign) == 1, str(stop_cmds))
        # 旧版裸条目（缺 hooks 包装）重装时必须被迁移为官方组；foreign 同场证明只迁自己的
        settings["hooks"]["Stop"] = [
            {"type": "command", "command": "echo foreign"},
            {
                "type": "command",
                "command": 'python3 "/old/root/slots/team-loop/hooks/loop_stop_guard.py"',
                "timeout": 20,
            },
        ]
        (proj / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        cli(install, "--slot", "team-loop", "--root", str(repo_root), "--scope", "project", "--project-root", str(proj))
        settings = json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))
        groups = settings["hooks"]["Stop"]
        check(
            "install: 旧版裸条目迁移为官方组",
            len(groups) == 2
            and any(isinstance(g, dict) and isinstance(g.get("hooks"), list) and len(g["hooks"]) == 1 for g in groups)
            and any(g.get("command") == "echo foreign" for g in groups),
            str(groups),
        )
        cli(
            install,
            "--slot",
            "team-loop",
            "--root",
            str(repo_root),
            "--scope",
            "project",
            "--project-root",
            str(proj),
            "--remove",
        )
        settings = json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))
        cmds = cmds_of(settings.get("hooks", {}).get("Stop", []))
        check(
            "install: --remove 只删自己的",
            cmds == ["echo foreign"] and "TeammateIdle" not in settings.get("hooks", {}),
            str(settings.get("hooks")),
        )
        check(
            "install: 未写真实 ~/.claude/settings.json 的备份残留",
            not list(Path.home().joinpath(".claude").glob("settings.json.bak-loop-check*")),
        )

    print(f"\n== 复核探针：{len(PASS)} passed, {len(FAIL)} failed ==")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""workflow_fanout 驱动测试：命令构造、并发收敛、超时 fail-per-item、二进制缺失、结果缓存。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from workflow_fanout import build_command, load_plan, main, run_item


def _make_stub(tmp_path: Path, name: str = "fakebin") -> Path:
    """既有端到端风格的可执行 stub：echo 一个可断言前缀。"""
    stub = tmp_path / name
    stub.write_text('#!/bin/sh\n echo "STUB_OUT:$1"\n')
    stub.chmod(0o755)
    return stub


def test_build_command_pi():
    cmd = build_command("pi", "do work", None)
    assert cmd[:5] == ["pi", "-p", "--no-session", "--mode", "text"]
    assert cmd[-1] == "do work"


def test_build_command_codex_with_output_file(tmp_path):
    out = tmp_path / "item.out"
    cmd = build_command("codex", "do work", out)
    assert cmd[0] == "codex"
    assert "--output-last-message" in cmd
    assert str(out) in cmd
    assert cmd[-1] == "do work"


def test_build_command_unknown_backend():
    with pytest.raises(ValueError):
        build_command("nope", "x", None)


def test_run_item_empty_prompt_fails(tmp_path):
    record = run_item("pi", {"id": "a", "prompt": "  "}, tmp_path, 5)
    assert record["ok"] is False
    assert record["error"] == "empty prompt"


def test_run_item_timeout_isolated(tmp_path, monkeypatch):
    """子进程超时只记该 item 失败，永不抛错。"""
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        raise sp.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr("workflow_fanout.subprocess.run", fake_run)
    record = run_item("pi", {"id": "slow", "prompt": "x"}, tmp_path, 0.1)
    assert record["ok"] is False
    assert record["error"].startswith("timeout after")


def test_run_item_nonzero_exit(tmp_path, monkeypatch):
    import subprocess as sp

    monkeypatch.setattr(
        "workflow_fanout.subprocess.run",
        lambda cmd, **k: sp.CompletedProcess(cmd, 3, stdout="", stderr="boom"),
    )
    record = run_item("pi", {"id": "bad", "prompt": "x"}, tmp_path, 5)
    assert record["ok"] is False
    assert "exit 3" in record["error"]


def test_run_item_pi_success(tmp_path, monkeypatch):
    import subprocess as sp

    monkeypatch.setattr(
        "workflow_fanout.subprocess.run",
        lambda cmd, **k: sp.CompletedProcess(cmd, 0, stdout="RESULT_TEXT", stderr=""),
    )
    record = run_item("pi", {"id": "ok1", "prompt": "x"}, tmp_path, 5)
    assert record["ok"] is True
    assert record["stdoutSummary"] == "RESULT_TEXT"
    assert (tmp_path / "ok1.stdout").exists()


def test_run_item_large_output_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "workflow_fanout.build_command",
        lambda backend, prompt, output_file: [
            sys.executable,
            "-c",
            "print('x' * (9 * 1024 * 1024))",
        ],
    )
    record = run_item("pi", {"id": "large", "prompt": "x"}, tmp_path, 5)
    assert record["ok"] is False
    assert "output exceeds" in record["error"]


def test_main_backend_missing_fail_fast(tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"items": [{"id": "a", "prompt": "x"}, {"id": "b", "prompt": "y"}]}))
    out = tmp_path / "res.jsonl"
    monkeypatch.setattr("workflow_fanout.resolve_binary", lambda backend: None)
    code = main(["--backend", "pi", "--plan", str(plan), "--out", str(out)])
    assert code == 1
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(records) == 2
    assert all(r["ok"] is False and "not found" in r["error"] for r in records)


def test_main_stub_binary_end_to_end(tmp_path, monkeypatch):
    """用假二进制 stub 走完整链路：plan -> 并发派发 -> JSONL 收敛顺序稳定。"""
    stub = tmp_path / "fakebin"
    stub.write_text('#!/bin/sh\nsleep 0.05\n echo "STUB_OUT:$1"\n')
    stub.chmod(0o755)
    monkeypatch.setattr("workflow_fanout.resolve_binary", lambda backend: str(stub))
    monkeypatch.setattr(
        "workflow_fanout.build_command",
        lambda backend, prompt, output_file: [str(stub), prompt],
    )

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "items": [
                    {"id": "i1", "prompt": "task one"},
                    {"id": "i2", "prompt": "task two"},
                    {"id": "i3", "prompt": "task three"},
                ]
            }
        )
    )
    out = tmp_path / "res.jsonl"
    code = main(["--backend", "pi", "--plan", str(plan), "--out", str(out), "--concurrency", "2"])
    assert code == 0
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["id"] for r in records] == ["i1", "i2", "i3"]
    assert all(r["ok"] for r in records)
    assert all(r["stdoutSummary"].startswith("STUB_OUT:") for r in records)


def test_run_item_codex_success_preserves_output_file(tmp_path, monkeypatch):
    """回归：codex 的 --output-last-message 落盘文件不得被 stdout 倾倒覆盖。"""
    import subprocess as sp

    message_file = tmp_path / "c1.out"

    # codex 子进程先"写"最终消息（模拟 --output-last-message），再返回 noisy stdout
    def fake_run(cmd, **kwargs):
        message_file.write_text("FINAL_ANSWER_FROM_CODEX", encoding="utf-8")
        return sp.CompletedProcess(cmd, 0, stdout="lots of progress noise", stderr="")

    monkeypatch.setattr("workflow_fanout.subprocess.run", fake_run)
    record = run_item("codex", {"id": "c1", "prompt": "x"}, tmp_path, 5)
    assert record["ok"] is True
    # outputFile 仍持有 codex 落盘的最终消息，而不是被 stdout 噪音覆盖
    assert message_file.read_text(encoding="utf-8") == "FINAL_ANSWER_FROM_CODEX"
    assert Path(record["outputFile"]).read_text(encoding="utf-8") == "FINAL_ANSWER_FROM_CODEX"
    # stdout 噪音进了独立倾倒文件
    assert (tmp_path / "c1.stdout").read_text(encoding="utf-8").startswith("lots of progress noise")
    assert record["stdoutSummary"].startswith("lots of progress noise")


def test_load_plan_rejects_empty(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"items": []}))
    with pytest.raises(ValueError):
        load_plan(plan)


@pytest.mark.parametrize(
    "item",
    [
        {"id": "../escape", "prompt": "x"},
        {"id": "a/b", "prompt": "x"},
        {"id": "a\x00b", "prompt": "x"},
        {"id": "a", "prompt": "x" * (128 * 1024 + 1)},
    ],
)
def test_load_plan_rejects_unsafe_item_paths_and_limits(tmp_path, item):
    plan = tmp_path / "unsafe.json"
    plan.write_text(json.dumps({"items": [item]}))
    with pytest.raises(ValueError):
        load_plan(plan)


def test_main_rejects_symlinked_output_directory(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "out-link"
    link.symlink_to(target, target_is_directory=True)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"items": [{"id": "ok", "prompt": "x"}]}))
    assert (
        main(
            [
                "--backend",
                "pi",
                "--plan",
                str(plan),
                "--out",
                str(tmp_path / "result.jsonl"),
                "--out-dir",
                str(link),
            ]
        )
        == 2
    )


# ---------------------------------------------------------------------------
# 结果缓存（断点续传）：命中回填 / 失败不缓存 / 全命中免二进制 / --no-cache /
# 并发不同 key 不冲突 / 幂等重跑
# ---------------------------------------------------------------------------


def test_cache_hit_backfills_record_with_cached_flag(tmp_path, monkeypatch, capsys):
    """命中回填：第二跑同键 item 直接回填缓存记录并置 cached=true，不进线程池。"""
    stub = _make_stub(tmp_path)
    monkeypatch.setattr("workflow_fanout.resolve_binary", lambda backend: str(stub))
    monkeypatch.setattr(
        "workflow_fanout.build_command",
        lambda backend, prompt, output_file: [str(stub), prompt],
    )

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"items": [{"id": "i1", "prompt": "task one"}]}))
    out = tmp_path / "res.jsonl"

    assert main(["--backend", "pi", "--plan", str(plan), "--out", str(out)]) == 0
    first = json.loads(out.read_text().splitlines()[0])
    assert first["ok"] is True
    assert first.get("cached") is not True  # 首跑是真派发，不带 cached 标志
    capsys.readouterr()  # 清空首跑输出，只断言第二跑摘要

    # 派发计数器：第二跑若走线程池必会再调 build_command
    dispatches = []
    monkeypatch.setattr(
        "workflow_fanout.build_command",
        lambda backend, prompt, output_file: dispatches.append(prompt) or [str(stub), prompt],
    )
    assert main(["--backend", "pi", "--plan", str(plan), "--out", str(out)]) == 0
    second = json.loads(out.read_text().splitlines()[0])
    assert second["ok"] is True
    assert second["cached"] is True
    assert second["stdoutSummary"].startswith("STUB_OUT:")  # 回填的是缓存里的完整记录
    assert dispatches == []  # 未派发

    summary = capsys.readouterr().out
    assert "cached=1" in summary


def test_failed_item_not_cached_and_redispatched(tmp_path, monkeypatch):
    """失败不缓存：exit!=0 的 item 不落缓存，重跑时重新派发。"""
    stub = _make_stub(tmp_path)
    bad = tmp_path / "badbin"
    bad.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    bad.chmod(0o755)
    monkeypatch.setattr("workflow_fanout.resolve_binary", lambda backend: str(stub))
    calls = []

    def flaky_build(backend, prompt, output_file):
        calls.append(prompt)
        # 首次派发走坏 stub（exit 3），之后返回好 stub
        if len(calls) == 1:
            return [str(bad), prompt]
        return [str(stub), prompt]

    monkeypatch.setattr("workflow_fanout.build_command", flaky_build)

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"items": [{"id": "f1", "prompt": "task one"}]}))
    out = tmp_path / "res.jsonl"

    code = main(["--backend", "pi", "--plan", str(plan), "--out", str(out)])
    assert code == 2  # fail-per-item 收敛，不是崩溃
    first = json.loads(out.read_text().splitlines()[0])
    assert first["ok"] is False
    assert "exit 3" in first["error"]
    cache_dir = out.with_suffix(".jsonl.d") / "cache"
    assert not cache_dir.exists() or not any(cache_dir.iterdir())  # 失败项零缓存

    code = main(["--backend", "pi", "--plan", str(plan), "--out", str(out)])
    assert code == 0  # 重新派发后成功
    second = json.loads(out.read_text().splitlines()[0])
    assert second["ok"] is True
    assert second.get("cached") is not True
    assert len(calls) == 2  # 第二跑确实重新派发了


def test_all_cached_skips_binary_fail_fast(tmp_path, monkeypatch, capsys):
    """全命中免二进制：全部 item 命中缓存时二进制缺失也收敛退出 0。"""
    stub = _make_stub(tmp_path)
    monkeypatch.setattr("workflow_fanout.resolve_binary", lambda backend: str(stub))
    monkeypatch.setattr(
        "workflow_fanout.build_command",
        lambda backend, prompt, output_file: [str(stub), prompt],
    )

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"items": [{"id": "i1", "prompt": "task one"}]}))
    out = tmp_path / "res.jsonl"
    assert main(["--backend", "pi", "--plan", str(plan), "--out", str(out)]) == 0
    capsys.readouterr()  # 清空首跑输出

    # 第二跑前把二进制换成不存在：全命中时 fail-fast 不生效
    monkeypatch.setattr("workflow_fanout.resolve_binary", lambda backend: None)
    assert main(["--backend", "pi", "--plan", str(plan), "--out", str(out)]) == 0
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert all(r["ok"] is True and r["cached"] is True for r in records)
    assert "cached=1" in capsys.readouterr().out


def test_no_cache_flag_bypasses_cache(tmp_path, monkeypatch):
    """--no-cache：跳过读与写——既不命中也不落盘，强制全量派发。"""
    stub = _make_stub(tmp_path)
    monkeypatch.setattr("workflow_fanout.resolve_binary", lambda backend: str(stub))
    monkeypatch.setattr(
        "workflow_fanout.build_command",
        lambda backend, prompt, output_file: [str(stub), prompt],
    )

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"items": [{"id": "i1", "prompt": "task one"}]}))
    out = tmp_path / "res.jsonl"

    assert main(["--backend", "pi", "--plan", str(plan), "--out", str(out), "--no-cache"]) == 0
    out_dir = out.with_suffix(".jsonl.d")
    cache_dir = out_dir / "cache"
    assert not cache_dir.exists()  # 写被跳过，连目录都不建
    record = json.loads(out.read_text().splitlines()[0])
    assert record["ok"] is True
    assert "cached" not in record

    # 再跑一次仍 --no-cache：依旧全量派发
    dispatches = []
    monkeypatch.setattr(
        "workflow_fanout.build_command",
        lambda backend, prompt, output_file: dispatches.append(prompt) or [str(stub), prompt],
    )
    assert main(["--backend", "pi", "--plan", str(plan), "--out", str(out), "--no-cache"]) == 0
    assert len(dispatches) == 1
    record = json.loads(out.read_text().splitlines()[0])
    assert record["ok"] is True and "cached" not in record


def test_concurrent_items_distinct_cache_keys(tmp_path, monkeypatch):
    """并发写不冲突：两 item 同后端不同 id/prompt -> 不同 key 各自落盘互不覆盖。"""
    from workflow_fanout import _cache_key, cache_path

    k1 = _cache_key("pi", "a", "prompt one")
    k2 = _cache_key("pi", "b", "prompt two")
    k3 = _cache_key("pi", "a", "prompt one")  # 同输入同键（幂等基础）
    assert k1 != k2 and k1 == k3
    assert len({k1, _cache_key("codex", "a", "prompt one")}) == 2  # backend 入键
    # id 入键防同 prompt 换位误命中
    assert _cache_key("pi", "a", "p") != _cache_key("pi", "b", "p")
    assert cache_path(tmp_path, k1) != cache_path(tmp_path, k2)

    stub = _make_stub(tmp_path)
    monkeypatch.setattr("workflow_fanout.resolve_binary", lambda backend: str(stub))
    monkeypatch.setattr(
        "workflow_fanout.build_command",
        lambda backend, prompt, output_file: [str(stub), prompt],
    )
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "items": [
                    {"id": "a", "prompt": "prompt one"},
                    {"id": "b", "prompt": "prompt two"},
                ]
            }
        )
    )
    out = tmp_path / "res.jsonl"
    assert main(["--backend", "pi", "--plan", str(plan), "--out", str(out), "--concurrency", "2"]) == 0

    cache_dir = out.with_suffix(".jsonl.d") / "cache"
    cached_files = sorted(p.name for p in cache_dir.iterdir())
    # 期望列表同样按文件名排序，与上面 sorted 对齐（key 是十六进制摘要，序与 plan 无关）
    assert cached_files == sorted([f"{k1}.json", f"{k2}.json"])
    # 各自记录完整且 id 对得上
    for key, item_id in ((k1, "a"), (k2, "b")):
        data = json.loads((cache_dir / f"{key}.json").read_text())
        assert data["id"] == item_id and data["ok"] is True


def test_idempotent_second_run_all_cached(tmp_path, monkeypatch, capsys):
    """幂等重跑：stub 端到端两跑，第二跑全 cached、零派发、退出码 0。"""
    stub = _make_stub(tmp_path)
    monkeypatch.setattr("workflow_fanout.resolve_binary", lambda backend: str(stub))
    monkeypatch.setattr(
        "workflow_fanout.build_command",
        lambda backend, prompt, output_file: [str(stub), prompt],
    )
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "items": [
                    {"id": "i1", "prompt": "task one"},
                    {"id": "i2", "prompt": "task two"},
                    {"id": "i3", "prompt": "task three"},
                ]
            }
        )
    )
    out = tmp_path / "res.jsonl"

    assert main(["--backend", "pi", "--plan", str(plan), "--out", str(out)]) == 0
    capsys.readouterr()
    first = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["id"] for r in first] == ["i1", "i2", "i3"]

    dispatches = []
    monkeypatch.setattr(
        "workflow_fanout.build_command",
        lambda backend, prompt, output_file: dispatches.append(prompt) or [str(stub), prompt],
    )
    assert main(["--backend", "pi", "--plan", str(plan), "--out", str(out)]) == 0
    second = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["id"] for r in second] == ["i1", "i2", "i3"]  # 收敛顺序仍按 plan
    assert all(r["ok"] is True and r["cached"] is True for r in second)
    assert dispatches == []  # 零派发
    assert "cached=3" in capsys.readouterr().out


def test_duplicate_items_same_key_no_crash(tmp_path, monkeypatch):
    """重复 id 在派发前拒绝，避免结果与缓存互相覆盖。"""
    stub = tmp_path / "fakebin"
    stub.write_text('#!/bin/sh\nsleep 0.05\necho "STUB_OUT:$1"\n')
    stub.chmod(0o755)
    monkeypatch.setattr("workflow_fanout.resolve_binary", lambda backend: str(stub))
    monkeypatch.setattr(
        "workflow_fanout.build_command",
        lambda backend, prompt, output_file: [str(stub), prompt],
    )

    plan = tmp_path / "plan_dup.json"
    plan.write_text(
        json.dumps(
            {
                "items": [
                    {"id": "a", "prompt": "p"},
                    {"id": "a", "prompt": "p"},
                ]
            }
        )
    )
    out = tmp_path / "res.jsonl"
    assert main(["--backend", "pi", "--plan", str(plan), "--out", str(out)]) == 2
    assert not out.exists()


def test_write_cache_failure_does_not_fail_item(tmp_path, monkeypatch):
    """缓存写失败吞错：write_cache 抛 OSError 时 item 仍 ok=True 记
    cacheWriteError，绝不击穿 run_item「永不抛错」承诺。"""
    import subprocess as sp

    monkeypatch.setattr(
        "workflow_fanout.subprocess.run",
        lambda cmd, **k: sp.CompletedProcess(cmd, 0, stdout="RESULT_TEXT", stderr=""),
    )

    def broken_write_cache(out_dir, key, record):
        raise OSError("disk full")

    monkeypatch.setattr("workflow_fanout.write_cache", broken_write_cache)
    record = run_item("pi", {"id": "c1", "prompt": "x"}, tmp_path, 5)
    assert record["ok"] is True  # 派发成功不受缓存故障影响
    assert record["cacheWriteError"] == "cache write failed"

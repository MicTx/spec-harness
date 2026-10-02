"""workflow_route_hook 行为测试：推荐注入、不推荐放行、失败永不 block、argv 不带用户文本。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import workflow_route_hook as hook_module


def _run_hook(payload) -> tuple[int, str]:
    script = Path(hook_module.__file__)
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    result = subprocess.run(
        [sys.executable, str(script)],
        input=raw,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode, result.stdout


def test_recommended_prompt_injects_context():
    code, stdout = _run_hook({"prompt": "批量处理这 40 个条目，每个都独立并行完成"})
    assert code == 0
    output = json.loads(stdout)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "workflow-runner" in context
    assert "仓库自有" in context
    assert "执行面固定为仓库驱动" in context
    assert len(context) <= 9000


def test_non_recommended_prompt_is_silent():
    code, stdout = _run_hook({"prompt": "帮我写一个 hello world"})
    assert code == 0
    assert stdout == ""


def test_unparseable_stdin_never_blocks():
    code, stdout = _run_hook("not-json")
    assert code == 0
    assert stdout == ""


def test_non_dict_payload_is_silent():
    code, stdout = _run_hook(["a", "list"])
    assert code == 0
    assert stdout == ""


def test_non_string_prompt_is_silent():
    """评审回补：truthy 非字符串 prompt（dict/list/number）不得进入路由。"""
    for bad in ({"deep": "object"}, [1, 2], 42, 3.14, True):
        code, stdout = _run_hook({"prompt": bad})
        assert code == 0
        assert stdout == "", f"non-string prompt must be silent: {bad!r}"


def test_empty_prompt_is_silent():
    code, stdout = _run_hook({"prompt": ""})
    assert code == 0
    assert stdout == ""


def test_prompt_travels_via_stdin_not_argv(monkeypatch):
    """评审回补：用户 prompt 不进 argv（防进程列表泄露/截断）。"""
    seen_argv = []
    seen_stdin = []

    def fake_run(argv, input=None, **kwargs):
        seen_argv.append(argv)
        seen_stdin.append(input)
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=json.dumps(
                {
                    "workflowRecommended": True,
                    "mode": "batch-fanout",
                    "score": 2,
                    "reason": "批量独立目标 x1",
                    "suggestedConfig": {"mode": "batch-fanout"},
                }
            ),
        )

    monkeypatch.setattr(hook_module.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(json.dumps({"prompt": "批量处理这些条目"})))
    code = hook_module.main()
    assert code == 0
    assert seen_stdin == ["批量处理这些条目"], "prompt must travel via stdin"
    assert all("--text" not in arg for arg in seen_argv[0]), "prompt must not appear in argv"
    assert "批量处理这些条目" not in json.dumps(seen_argv[0], ensure_ascii=False)


def test_route_script_crash_fails_open(monkeypatch, capsys):
    """路由子进程崩溃（无效 JSON stdout）时 hook 静默放行（fail-open，永不 block）。"""
    monkeypatch.setattr(
        hook_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=1, stdout="boom"),
    )
    monkeypatch.setattr(sys, "stdin", _FakeStdin(json.dumps({"prompt": "批量处理 40 个条目 fan-out"})))
    code = hook_module.main()
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""


def test_route_timeout_fails_open(monkeypatch, capsys):
    monkeypatch.setattr(
        hook_module.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd="x", timeout=1)),
    )
    monkeypatch.setattr(sys, "stdin", _FakeStdin(json.dumps({"prompt": "批量处理 40 个条目 fan-out"})))
    code = hook_module.main()
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""


class _FakeStdin:
    """json.load(sys.stdin) 兼容的最小 stdin 替身。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text

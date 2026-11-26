"""The launcher filters safely without starting Python outside Spec projects."""

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from tests.v3_support import cli, repo, started_package

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "spec_hook.py"
LAUNCHER = ROOT / "hooks" / "spec-hook.sh"


def _invoke(program, cwd, payload, event="pre_tool", env=None):
    return subprocess.run(
        [*program, "--host", "claude", event],
        input=payload,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_host_matrix_keeps_all_hosts_with_four_canonical_events():
    sys.path.insert(0, str(ROOT / "hooks"))
    from adapters import EVENTS, HOSTS, capabilities

    assert EVENTS == ("start", "prompt", "pre_tool", "stop")
    assert len([host for host in HOSTS if capabilities(host)]) == 12
    assert all(set(capabilities(host)) <= set(EVENTS) for host in HOSTS)
    assert set(capabilities("pi")) == set(EVENTS)
    assert set(capabilities("dsh")) == {"pre_tool", "stop"}


def test_launcher_never_invokes_python_outside_spec(tmp_path):
    repo(tmp_path)
    fake = tmp_path / "bin"
    fake.mkdir()
    marker = tmp_path / "python-ran"
    python = fake / "python3"
    python.write_text(f"#!/bin/sh\ntouch {marker}\n")
    python.chmod(0o755)
    env = {**os.environ, "PATH": str(fake) + os.pathsep + os.environ["PATH"]}
    payload = json.dumps({"cwd": str(tmp_path), "tool_input": {"command": "echo can't"}})
    before = []
    for _ in range(15):
        started = time.perf_counter()
        result = _invoke(["sh", str(LAUNCHER)], tmp_path, payload, env=env)
        before.append((time.perf_counter() - started) * 1000)
        assert result.returncode == 0 and not result.stdout
    assert not marker.exists()
    assert statistics.median(before) < 50


def test_launcher_uses_payload_cwd_not_process_cwd(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    repo(project)
    started_package(project)
    payload = json.dumps({"cwd": str(project), "tool_input": {"command": "git commit -m example"}})
    original = _invoke([sys.executable, str(HOOK)], tmp_path, payload)
    filtered = _invoke(["sh", str(LAUNCHER)], tmp_path, payload)
    assert original.returncode == filtered.returncode == 0
    assert json.loads(original.stdout)["decision"] == json.loads(filtered.stdout)["decision"] == "block"


def test_escaped_and_duplicate_cwd_fall_through_without_bypass(tmp_path):
    project = tmp_path / "中文-路径"
    project.mkdir()
    repo(project)
    started_package(project)
    for payload in (
        json.dumps({"cwd": str(project), "command": "git push"}),
        '{"cwd":"/", "cwd":' + json.dumps(str(project)) + ', "command":"git push"}',
        json.dumps({"cwd": None, "workspaceRoot": str(project), "command": "git push"}),
    ):
        direct = _invoke([sys.executable, str(HOOK)], tmp_path, payload)
        launcher = _invoke(["sh", str(LAUNCHER)], tmp_path, payload)
        assert json.loads(direct.stdout)["decision"] == json.loads(launcher.stdout)["decision"] == "block"


def test_escaped_key_and_quoted_directory_cannot_bypass_dispatch(tmp_path):
    project = tmp_path / 'project "quoted"'
    project.mkdir()
    repo(project)
    started_package(project)
    payload = '{"c\\u0077d":' + json.dumps(str(project)) + ',"command":"git push"}'
    direct = _invoke([sys.executable, str(HOOK)], tmp_path, payload)
    launcher = _invoke(["sh", str(LAUNCHER)], tmp_path, payload)
    assert json.loads(direct.stdout)["decision"] == json.loads(launcher.stdout)["decision"] == "block"


def test_launcher_never_trusts_nested_or_invalid_json_cwd(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    repo(project)
    started_package(project)
    cases = (
        json.dumps({"metadata": {"cwd": "/"}, "cwd": str(project), "command": "git push"}),
        '{"cwd":"/", "extra":, "command":"git push"}',
    )
    for payload in cases:
        direct = _invoke([sys.executable, str(HOOK)], project, payload)
        launcher = _invoke(["sh", str(LAUNCHER)], project, payload)
        assert direct.stdout == launcher.stdout


def test_nested_repository_without_spec_is_a_noop(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=nested, check=True)
    payload = json.dumps({"cwd": str(nested), "command": "git push"})
    result = _invoke(["sh", str(LAUNCHER)], tmp_path, payload)
    assert result.returncode == 0 and not result.stdout


def test_host_event_after_compaction_recovers_from_the_ledger(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    payload = json.dumps({"cwd": str(tmp_path), "source": "compact"})
    result = _invoke(["sh", str(LAUNCHER)], tmp_path, payload, event="start")
    assert "spec.py check" in result.stdout

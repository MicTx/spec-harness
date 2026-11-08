"""Zero discovery candidates are not proof of absence after a bounded scan."""

import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import route_spec_package as route  # noqa: E402
import session_context_support as context  # noqa: E402
from session_adapters import AdapterError  # noqa: E402


def render(root, monkeypatch, capsys, *, markdown=False, brief=False):
    argv = ["route_spec_package.py", "--root", str(root)]
    if not markdown:
        argv += ["--format", "json"]
    if brief:
        argv.append("--brief")
    monkeypatch.setattr(sys, "argv", argv)
    assert route.main() == 0
    output = capsys.readouterr().out
    return output if markdown else json.loads(output)


@pytest.mark.parametrize(
    "reason,status",
    [
        ("entry-budget", "partial"),
        ("byte-budget", "partial"),
        ("permission-denied", "unreadable"),
        ("layout-unverified", "unsupported"),
    ],
)
@pytest.mark.parametrize("brief", [False, True])
def test_empty_incomplete_sources_stay_visible_in_route(tmp_path, monkeypatch, capsys, reason, status, brief):
    found = {
        "status": "none",
        "complete": False,
        "candidates": [],
        "sources": [
            {"agent": "pi", "status": status, "reason_code": reason, "complete": False, "path": "PRIVATE-PATH"},
            {"agent": "claude", "status": "absent", "reason_code": "no-store", "complete": True},
        ],
    }
    calls = []

    def discover(root, **kwargs):
        calls.append(kwargs)
        return found

    monkeypatch.setattr(route, "discover_native_sessions", discover)
    state = render(tmp_path, monkeypatch, capsys, brief=brief)
    assert calls == [{"client": "unknown", "limit": 4}]
    assert state["stage"] == "status" and state["health"] == "风险"
    native = state["nativeContext"]
    assert native["complete"] is False and native["status"] == "partial"
    assert native["authority"] == "context-only" and native["candidates"] == []
    assert native["sources"] == [{"agent": "pi", "status": status, "reason_code": reason, "complete": False}]
    assert reason in " ".join(state["alerts"])
    assert "零候选不等于没有历史" in " ".join(state["alerts"])
    assert "显式指定已知 session" in state["nextStep"]
    assert "PRIVATE-PATH" not in json.dumps(state)
    markdown = render(tmp_path, monkeypatch, capsys, markdown=True)
    assert "原生会话发现不完整" in markdown and reason in markdown
    assert "不提高扫描上限" in markdown


@pytest.mark.parametrize("failure", ["bounded", "adapter-error", "permission"])
def test_real_discovery_source_failures_reach_route(tmp_path, monkeypatch, capsys, failure):
    home = tmp_path / "home"
    home.mkdir()
    for name in context.adapters.ADAPTERS:
        monkeypatch.setitem(
            context.adapters.ADAPTERS,
            name,
            SimpleNamespace(
                discover=lambda _ctx: {
                    "status": "absent",
                    "reason_code": "no-store",
                    "complete": True,
                    "candidates": [],
                }
            ),
        )

    def failed(_ctx):
        if failure == "adapter-error":
            raise RuntimeError("PRIVATE-EXCEPTION")
        if failure == "permission":
            raise AdapterError("unreadable", "permission-denied")
        return {"status": "partial", "reason_code": "entry-budget", "complete": False, "candidates": []}

    monkeypatch.setitem(context.adapters.ADAPTERS, "pi", SimpleNamespace(discover=failed))
    monkeypatch.setattr(
        route, "discover_native_sessions", lambda root, **kwargs: context.discover(root, home=home, **kwargs)
    )
    state = render(tmp_path, monkeypatch, capsys)
    assert state["stage"] == "status" and state["nativeContext"]["complete"] is False
    expected = {"bounded": "entry-budget", "adapter-error": "adapter-error", "permission": "permission-denied"}[failure]
    assert state["nativeContext"]["sources"][0]["reason_code"] == expected
    assert "PRIVATE-EXCEPTION" not in json.dumps(state)


def test_unverified_present_provider_does_not_become_no_history(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".cursor/projects").mkdir(parents=True)
    for name in context.adapters.ADAPTERS:
        monkeypatch.setitem(
            context.adapters.ADAPTERS,
            name,
            SimpleNamespace(
                discover=lambda _ctx: {
                    "status": "absent",
                    "reason_code": "no-store",
                    "complete": True,
                    "candidates": [],
                }
            ),
        )
    monkeypatch.setattr(
        route, "discover_native_sessions", lambda root, **kwargs: context.discover(root, home=home, **kwargs)
    )
    state = render(tmp_path, monkeypatch, capsys)
    native = state["nativeContext"]
    assert state["stage"] == "status" and native["complete"] is False
    assert native["sources"] == [
        {
            "agent": "cursor",
            "status": "unsupported",
            "reason_code": context.adapters.UNVERIFIED_REASON,
            "complete": False,
        }
    ]


def test_complete_empty_scan_keeps_new_stage(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        route,
        "discover_native_sessions",
        lambda *_args, **_kwargs: {"status": "none", "complete": True, "candidates": [], "sources": []},
    )
    state = render(tmp_path, monkeypatch, capsys)
    assert state["stage"] == "new" and "nativeContext" not in state


def test_unavailable_scan_reports_reason(tmp_path, monkeypatch, capsys):
    def unavailable(*_args, **_kwargs):
        raise context.SessionContextError("workspace-unavailable")

    monkeypatch.setattr(route, "discover_native_sessions", unavailable)
    state = render(tmp_path, monkeypatch, capsys)
    assert state["stage"] == "status"
    assert state["nativeContext"]["status"] == "unavailable"
    assert state["nativeContext"]["reason_code"] == "workspace-unavailable"


def test_candidate_scoping_privacy_and_shell_safe_read_command(tmp_path, monkeypatch):
    candidate = {
        "agent": "pi",
        "session_id": "id;echo not-command",
        "title": "metadata",
        "relation": "root",
        "association_confidence": "verified",
        "complete": True,
        "body": "PRIVATE-BODY",
    }
    found = {
        "status": "partial",
        "complete": False,
        "candidates": [
            candidate,
            dict(candidate, relation="foreign"),
            dict(candidate, association_confidence="unverified"),
        ],
        "sources": [],
    }
    monkeypatch.setattr(route, "discover_native_sessions", lambda *_args, **_kwargs: found)
    native = route._native_context_hint(tmp_path)
    assert len(native["candidates"]) == 1 and native["complete"] is False
    assert "PRIVATE-BODY" not in json.dumps(native)
    command = shlex.split(native["candidates"][0]["read_command"])
    assert f"--session={candidate['session_id']}" in command

"""Workspace scoping, budgeting and result-schema tests for session context recovery.

All inputs are synthetic sessions written into a tmp HOME; no real native
store is ever touched. Native input literals are fixed synthetic text
('export alpha', 'alpha next step', 'unrelated beta', 'discarded branch',
'active branch', 'compacted alpha', 'SYNTHETIC_SECRET_SENTINEL').
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import session_adapters  # noqa: E402
import session_context_support as scs  # noqa: E402

_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(_GIT_ENV)
    out = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env)
    return out.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    (path / "f.txt").write_text("x", encoding="utf-8")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-qm", "init")
    return path


def _claude_encode(path: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "-" for c in path)


def _claude_write(home: Path, bucket_cwd: Path, session_id: str, records: list[dict]) -> Path:
    bucket = home / ".claude" / "projects" / _claude_encode(str(bucket_cwd))
    bucket.mkdir(parents=True, exist_ok=True)
    target = bucket / f"{session_id}.jsonl"
    with open(target, "w", encoding="utf-8", newline="") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return target


def _cl_user(cwd: str, sid: str, uuid: str, parent, ts: str, text: str, branch: str = "main"):
    return {
        "type": "user",
        "cwd": cwd,
        "sessionId": sid,
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": ts,
        "gitBranch": branch,
        "isSidechain": False,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _cl_assistant(cwd: str, sid: str, uuid: str, parent, ts: str, text: str, branch: str = "main"):
    return {
        "type": "assistant",
        "cwd": cwd,
        "sessionId": sid,
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": ts,
        "gitBranch": branch,
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "SYNTHETIC_SECRET_SENTINEL"},
                {"type": "text", "text": text},
                {"type": "tool_use", "name": "Bash", "input": {"cmd": "SYNTHETIC_SECRET_SENTINEL"}},
            ],
        },
    }


def _claude_chain(root: Path, home: Path, sid: str = "sid-1", branch: str = "main") -> Path:
    cwd = str(root)
    return _claude_write(
        home,
        root,
        sid,
        [
            _cl_user(cwd, sid, "u1", None, "2026-11-02T10:00:00Z", "export alpha", branch),
            _cl_assistant(cwd, sid, "u2", "u1", "2026-11-02T10:01:00Z", "alpha next step", branch),
        ],
    )


def test_workspace_anchor_git_and_candidate(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _claude_chain(root, home)
    result = scs.discover(root, home=home)
    assert result["schema_version"] == 1
    assert result["workspace"]["git"] is True
    assert result["workspace"]["root"] == str(root.resolve())
    assert result["workspace"]["branch"] == _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    assert result["workspace"]["head"] == _git(root, "rev-parse", "HEAD")
    assert result["status"] == "ok"
    assert result["complete"] is True
    assert len(result["candidates"]) == 1
    cand = result["candidates"][0]
    assert cand["agent"] == "claude"
    assert cand["session_id"] == "sid-1"
    assert cand["has_user"] is True
    assert cand["relation"] == "root"
    assert str(home) not in json.dumps(result)


def test_sibling_prefix_directory_not_in_scope(tmp_path):
    root = _init_repo(tmp_path / "proj")
    sibling = _init_repo(tmp_path / "proj-extra")
    home = tmp_path / "home"
    _claude_chain(sibling, home, sid="sid-sib")
    result = scs.discover(root, home=home)
    assert result["candidates"] == []
    assert result["status"] == "none"


def test_canonical_alias_matches(tmp_path):
    root = _init_repo(tmp_path / "proj")
    alias = tmp_path / "alias"
    alias.symlink_to(root)
    home = tmp_path / "home"
    _claude_chain(root, home)
    by_alias = scs.discover(alias, home=home)
    assert [c["session_id"] for c in by_alias["candidates"]] == ["sid-1"]

    home2 = tmp_path / "home2"
    cwd_alias = str(alias)
    _claude_write(
        home2,
        root,
        "sid-2",
        [_cl_user(cwd_alias, "sid-2", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    by_root = scs.discover(root, home=home2)
    assert [c["session_id"] for c in by_root["candidates"]] == ["sid-2"]


def test_subdirectory_same_worktree_in_scope(tmp_path):
    root = _init_repo(tmp_path / "proj")
    sub = root / "pkg" / "sub"
    sub.mkdir(parents=True)
    home = tmp_path / "home"
    _claude_write(
        home,
        root,
        "sid-sub",
        [_cl_user(str(sub), "sid-sub", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    result = scs.discover(root, home=home)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["relation"] == "descendant"


def test_nested_repo_excluded(tmp_path):
    root = _init_repo(tmp_path / "proj")
    nested = _init_repo(root / "nested")
    home = tmp_path / "home"
    _claude_write(
        home,
        root,
        "sid-nested",
        [_cl_user(str(nested), "sid-nested", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    result = scs.discover(root, home=home)
    assert result["candidates"] == []


def test_linked_worktree_excluded(tmp_path):
    root = _init_repo(tmp_path / "proj")
    worktree = tmp_path / "proj-wt"
    _git(root, "worktree", "add", "-q", str(worktree))
    home = tmp_path / "home"
    _claude_write(
        home,
        root,
        "sid-wt",
        [_cl_user(str(worktree), "sid-wt", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    result = scs.discover(root, home=home)
    assert result["candidates"] == []


def test_nongit_requires_exact_match(tmp_path):
    root = tmp_path / "plain"
    sub = root / "sub"
    sub.mkdir(parents=True)
    home = tmp_path / "home"
    _claude_write(
        home,
        root,
        "sid-sub",
        [_cl_user(str(sub), "sid-sub", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    _claude_write(
        home,
        root,
        "sid-exact",
        [_cl_user(str(root), "sid-exact", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    result = scs.discover(root, home=home)
    assert result["workspace"]["git"] is False
    assert result["workspace"]["branch"] is None
    assert result["workspace"]["head"] is None
    assert [c["session_id"] for c in result["candidates"]] == ["sid-exact"]


def test_current_session_excluded(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _claude_chain(root, home, sid="sid-live")
    result = scs.discover(root, home=home, client="claude", current_session="sid-live")
    assert result["candidates"] == []
    unknown = scs.discover(root, home=home, current_session="sid-live")
    assert [c["session_id"] for c in unknown["candidates"]] == ["sid-live"]


def test_candidate_limit_reports_partial(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    for index in range(3):
        _claude_chain(root, home, sid=f"sid-{index}")
    result = scs.discover(root, home=home, limit=2)
    assert len(result["candidates"]) == 2
    assert result["complete"] is False
    assert result["status"] == "partial"


def test_empty_home_reports_none_with_full_registry(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    home.mkdir()
    result = scs.discover(root, home=home)
    assert result["status"] == "none"
    assert result["candidates"] == []
    names = {s["agent"] for s in result["sources"]}
    assert names == {"claude", "codex", "devin", "pi", "cursor", "opencode", "gemini", "copilot"}
    by_name = {s["agent"]: s for s in result["sources"]}
    assert by_name["claude"]["status"] == "absent"
    for agent in ("cursor", "opencode", "gemini", "copilot"):
        assert by_name[agent]["status"] == "absent"
        assert by_name[agent]["reason_code"]


def test_read_returns_bounded_messages_and_advisories(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _claude_chain(root, home)
    result = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert result["status"] == "ok"
    assert result["authority"] == "context-only"
    assert "untrusted-history" in result["advisories"]
    assert "not-completion-evidence" in result["advisories"]
    assert result["candidate"]["session_id"] == "sid-1"
    roles = [m["role"] for m in result["messages"]]
    assert roles == ["user", "assistant"]
    assert result["messages"][0]["text"] == "export alpha"
    assert result["messages"][1]["text"] == "alpha next step"
    assert "SYNTHETIC_SECRET_SENTINEL" not in json.dumps(result)
    assert result["freshness"] in {"fresh", "stale", "unknown"}


def test_read_unknown_session_id(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _claude_chain(root, home)
    result = scs.read(root, agent="claude", session_id="sid-missing", home=home)
    assert result["status"] == "unknown_id"
    assert result["error"]["reason_code"] == "unknown-session"


def test_read_refuses_path_like_session_id(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    for bad in ("../x", "a/b", "a\\b", "..", "x.jsonl/../../../etc/passwd", ""):
        result = scs.read(root, agent="claude", session_id=bad, home=home)
        assert result["status"] == "refused", bad
        assert result["error"]["reason_code"] == "invalid-session-id"


def test_read_refuses_unsupported_agent(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    result = scs.read(root, agent="cursor", session_id="abc", home=home)
    assert result["status"] == "unavailable"
    assert result["error"]["reason_code"]


def test_message_cap_truncates_with_reason(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    records = []
    for index in range(30):
        uuid = f"u{index}"
        parent = f"u{index - 1}" if index else None
        if index % 2 == 0:
            records.append(_cl_user(cwd, "sid-1", uuid, parent, f"2026-11-02T10:{index:02d}:00Z", "export alpha"))
        else:
            records.append(
                _cl_assistant(cwd, "sid-1", uuid, parent, f"2026-11-02T10:{index:02d}:00Z", "alpha next step")
            )
    _claude_write(home, root, "sid-1", records)
    result = scs.read(root, agent="claude", session_id="sid-1", home=home, max_messages=4)
    assert result["status"] == "ok"
    assert len(result["messages"]) == 4
    assert result["complete"] is False
    assert result["truncation"] == "message-limit"
    assert result["messages"][-1]["record_id"] == "u29"


def test_freshness_branch_comparison(tmp_path):
    root = _init_repo(tmp_path / "proj")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    home = tmp_path / "home"
    _claude_chain(root, home, sid="sid-fresh", branch=branch)
    _claude_chain(root, home, sid="sid-stale", branch="other-branch")
    fresh = scs.read(root, agent="claude", session_id="sid-fresh", home=home)
    stale = scs.read(root, agent="claude", session_id="sid-stale", home=home)
    assert fresh["freshness"] == "fresh"
    assert stale["freshness"] == "stale"


def test_total_decode_budget_marks_partial(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _claude_chain(root, home, sid="sid-1")
    monkeypatch.setattr(session_adapters, "TOTAL_DECODE_CAP", 64)
    result = scs.discover(root, home=home)
    assert result["complete"] is False
    assert result["status"] == "partial"


def test_symlink_session_file_never_followed(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    outside = tmp_path / "outside.jsonl"
    outside.write_text(
        json.dumps(_cl_user(str(root), "sid-evil", "u1", None, "2026-11-02T10:00:00Z", "unrelated beta")) + "\n",
        encoding="utf-8",
    )
    _claude_chain(root, home, sid="sid-1")
    bucket = home / ".claude" / "projects" / _claude_encode(str(root))
    (bucket / "sid-evil.jsonl").symlink_to(outside)
    result = scs.discover(root, home=home)
    assert [c["session_id"] for c in result["candidates"]] == ["sid-1"]
    assert "unrelated beta" not in json.dumps(result)


def test_secret_like_title_redacted_and_truncated(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-1",
        [
            _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha"),
            {
                "type": "title",
                "cwd": cwd,
                "sessionId": "sid-1",
                "timestamp": "2026-11-02T10:02:00Z",
                "title": "token=SYNTHETIC_SECRET_SENTINEL " + "x" * 300,
            },
        ],
    )
    result = scs.discover(root, home=home)
    title = result["candidates"][0]["title"]
    assert title is not None
    assert len(title) <= 160
    assert "SYNTHETIC_SECRET_SENTINEL" not in title


def test_discovery_sorted_by_recency_then_client(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-old",
        [_cl_user(cwd, "sid-old", "u1", None, "2026-11-01T10:00:00Z", "export alpha")],
    )
    _claude_write(
        home,
        root,
        "sid-new",
        [_cl_user(cwd, "sid-new", "u1", None, "2026-11-03T10:00:00Z", "export alpha")],
    )
    result = scs.discover(root, home=home, client="claude")
    assert [c["session_id"] for c in result["candidates"]] == ["sid-new", "sid-old"]


def test_client_agent_scanned_and_sorted_first(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-old",
        [_cl_user(cwd, "sid-old", "u1", None, "2026-11-01T10:00:00Z", "export alpha")],
    )
    enc = "".join(c if c.isalnum() or c in "_-" else "-" for c in str(root))
    bucket = home / ".pi" / "agent" / "sessions" / f"--{enc}--"
    bucket.mkdir(parents=True)
    records = [
        {"type": "session", "version": 3, "id": "p-new", "timestamp": "2026-11-03T09:00:00Z", "cwd": cwd},
        {
            "type": "message",
            "id": "e1",
            "parentId": None,
            "timestamp": "2026-11-03T10:00:00Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "export alpha"}]},
        },
    ]
    with open(bucket / "p-new.jsonl", "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    result = scs.discover(root, home=home, client="claude")
    assert result["sources"][0]["agent"] == "claude"
    assert result["candidates"][0]["session_id"] == "sid-old"
    assert result["candidates"][0]["agent"] == "claude"


def test_current_session_excluded_per_client_agent_only(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "shared",
        [_cl_user(cwd, "shared", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    enc = "".join(c if c.isalnum() or c in "_-" else "-" for c in str(root))
    bucket = home / ".pi" / "agent" / "sessions" / f"--{enc}--"
    bucket.mkdir(parents=True)
    records = [
        {"type": "session", "version": 3, "id": "shared", "timestamp": "2026-11-02T09:00:00Z", "cwd": cwd},
        {
            "type": "message",
            "id": "e1",
            "parentId": None,
            "timestamp": "2026-11-02T10:00:00Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "export alpha"}]},
        },
    ]
    with open(bucket / "shared.jsonl", "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    result = scs.discover(root, home=home, client="claude", current_session="shared")
    assert [c["agent"] for c in result["candidates"]] == ["pi"]
    assert result["candidates"][0]["session_id"] == "shared"


def test_deleted_cwd_scope_resolution(tmp_path):
    root = _init_repo(tmp_path / "proj")
    nested = _init_repo(root / "nested")
    home = tmp_path / "home"
    _claude_write(
        home,
        root,
        "sid-del-ok",
        [
            _cl_user(
                str(root / "deleted-sub"),
                "sid-del-ok",
                "u1",
                None,
                "2026-11-02T10:00:00Z",
                "export alpha",
            )
        ],
    )
    _claude_write(
        home,
        root,
        "sid-del-bad",
        [
            _cl_user(
                str(nested / "deleted-sub"),
                "sid-del-bad",
                "u1",
                None,
                "2026-11-02T10:01:00Z",
                "unrelated beta",
            )
        ],
    )
    result = scs.discover(root, home=home)
    assert [c["session_id"] for c in result["candidates"]] == ["sid-del-ok"]


def test_missing_root_raises(tmp_path):
    home = tmp_path / "home"
    with pytest.raises(scs.SessionContextError):
        scs.discover(tmp_path / "does-not-exist", home=home)


def test_git_env_cannot_redirect_toplevel(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    foreign = _init_repo(tmp_path / "foreign")
    home = tmp_path / "home"
    _claude_chain(root, home)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(foreign))
    result = scs.discover(root, home=home)
    assert result["workspace"]["root"] == str(root.resolve())
    assert result["workspace"]["git"] is True
    assert [c["session_id"] for c in result["candidates"]] == ["sid-1"]

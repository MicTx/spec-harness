"""Per-provider adapter tests with independent synthetic native input.

Fixtures write the documented native shapes (Claude JSONL buckets, Codex
state_5.sqlite + rollout JSONL, Devin sessions.db message_nodes, Pi session
trees) into a tmp HOME; expectations are written by hand, never derived
from the implementation under test.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

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


def _cl_assistant(cwd: str, sid: str, uuid: str, parent, ts: str, text: str):
    return {
        "type": "assistant",
        "cwd": cwd,
        "sessionId": sid,
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": ts,
        "gitBranch": "main",
        "isSidechain": False,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


# --------------------------------------------------------------------------- #
# Claude Code                                                                 #
# --------------------------------------------------------------------------- #


def test_claude_bucket_name_collision_uses_record_cwd(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _claude_write(
        home,
        root,
        "sid-foreign",
        [_cl_user(str(foreign), "sid-foreign", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    result = scs.discover(root, home=home)
    assert result["candidates"] == []


def test_claude_tool_result_only_is_not_user_turn(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-1",
        [
            {
                "type": "user",
                "cwd": cwd,
                "sessionId": "sid-1",
                "uuid": "u1",
                "parentUuid": None,
                "timestamp": "2026-11-02T10:00:00Z",
                "isSidechain": False,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": "unrelated beta"}],
                },
            },
            _cl_assistant(cwd, "sid-1", "u2", "u1", "2026-11-02T10:01:00Z", "alpha next step"),
        ],
    )
    result = scs.discover(root, home=home)
    assert result["candidates"][0]["has_user"] is False
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert [m["role"] for m in read["messages"]] == ["assistant"]


def test_claude_is_meta_and_sidechain_not_user_turns(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    meta = _cl_user(cwd, "sid-1", "m1", None, "2026-11-02T09:59:00Z", "export alpha")
    meta["isMeta"] = True
    side = _cl_user(cwd, "sid-1", "s1", None, "2026-11-02T09:58:00Z", "unrelated beta")
    side["isSidechain"] = True
    _claude_write(
        home,
        root,
        "sid-1",
        [meta, side, _cl_assistant(cwd, "sid-1", "u2", None, "2026-11-02T10:01:00Z", "alpha next step")],
    )
    result = scs.discover(root, home=home)
    assert result["candidates"][0]["has_user"] is False
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert json.dumps(read).count("unrelated beta") == 0


def test_claude_sidechain_only_session_excluded(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    side = _cl_user(cwd, "sid-w", "s1", None, "2026-11-02T10:00:00Z", "export alpha")
    side["isSidechain"] = True
    _claude_write(home, root, "sid-w", [side])
    assert scs.discover(root, home=home)["candidates"] == []


def test_claude_mode_only_session_excluded(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    records = [
        {"type": "mode", "cwd": cwd, "sessionId": "sid-m", "timestamp": "2026-11-02T10:00:00Z"},
        {"type": "permission", "cwd": cwd, "sessionId": "sid-m", "timestamp": "2026-11-02T10:00:01Z"},
        {"type": "system", "cwd": cwd, "sessionId": "sid-m", "timestamp": "2026-11-02T10:00:02Z"},
        {"type": "progress", "cwd": cwd, "sessionId": "sid-m", "timestamp": "2026-11-02T10:00:03Z"},
    ]
    _claude_write(home, root, "sid-m", records)
    assert scs.discover(root, home=home)["candidates"] == []


def test_claude_leaf_pointer_prefers_active_chain(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-1",
        [
            _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha"),
            _cl_assistant(cwd, "sid-1", "u2", "u1", "2026-11-02T10:01:00Z", "alpha next step"),
            _cl_user(cwd, "sid-1", "u9", None, "2026-11-02T10:05:00Z", "discarded branch"),
            {"type": "last-prompt", "leafUuid": "u2", "sessionId": "sid-1", "cwd": cwd},
        ],
    )
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert [m["text"] for m in read["messages"]] == ["export alpha", "alpha next step"]


def test_claude_forked_branch_not_concatenated(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-1",
        [
            _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha"),
            _cl_user(cwd, "sid-1", "u4", "u1", "2026-11-02T10:02:00Z", "discarded branch"),
            _cl_user(cwd, "sid-1", "u2", "u1", "2026-11-02T10:01:00Z", "alpha next step"),
            _cl_assistant(cwd, "sid-1", "u3", "u2", "2026-11-02T10:03:00Z", "active branch"),
        ],
    )
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert [m["text"] for m in read["messages"]] == [
        "export alpha",
        "alpha next step",
        "active branch",
    ]


def test_claude_missing_parent_and_cycle_are_partial(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-gap",
        [
            _cl_user(cwd, "sid-gap", "u1", None, "2026-11-02T10:00:00Z", "export alpha"),
            _cl_user(cwd, "sid-gap", "u2", "ghost", "2026-11-02T10:01:00Z", "alpha next step"),
        ],
    )
    _claude_write(
        home,
        root,
        "sid-cycle",
        [
            _cl_user(cwd, "sid-cycle", "u1", "u2", "2026-11-02T10:00:00Z", "export alpha"),
            _cl_user(cwd, "sid-cycle", "u2", "u1", "2026-11-02T10:01:00Z", "alpha next step"),
        ],
    )
    gap = scs.read(root, agent="claude", session_id="sid-gap", home=home)
    assert gap["complete"] is False
    cycle = scs.read(root, agent="claude", session_id="sid-cycle", home=home)
    assert cycle["complete"] is False


def test_claude_truncated_tail_and_oversized_record_are_partial(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    truncated = _claude_write(
        home,
        root,
        "sid-trunc",
        [_cl_user(cwd, "sid-trunc", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    with open(truncated, "a", encoding="utf-8") as handle:
        handle.write('{"type":"user","cwd":"' + cwd + '","sessionId":"sid-trunc","uuid":"u2"')
    read = scs.read(root, agent="claude", session_id="sid-trunc", home=home)
    assert read["complete"] is False

    huge_text = "y" * (300 * 1024)
    _claude_write(
        home,
        root,
        "sid-huge",
        [
            _cl_user(cwd, "sid-huge", "u1", None, "2026-11-02T10:00:00Z", "export alpha"),
            _cl_user(cwd, "sid-huge", "u2", "u1", "2026-11-02T10:01:00Z", huge_text),
        ],
    )
    read = scs.read(root, agent="claude", session_id="sid-huge", home=home)
    assert read["complete"] is False


def test_claude_compact_summary_on_chain_and_system_never_leaks(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    compacted = _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "compacted alpha")
    compacted["isCompactSummary"] = True
    _claude_write(
        home,
        root,
        "sid-1",
        [
            {
                "type": "system",
                "subtype": "init",
                "cwd": cwd,
                "sessionId": "sid-1",
                "uuid": "s0",
                "timestamp": "2026-11-02T09:59:00Z",
                "content": "SYNTHETIC_SECRET_SENTINEL",
            },
            compacted,
            _cl_user(cwd, "sid-1", "u2", "u1", "2026-11-02T10:01:00Z", "export alpha"),
        ],
    )
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert [m["role"] for m in read["messages"]] == ["summary", "user"]
    assert read["messages"][0]["text"] == "compacted alpha"
    assert "SYNTHETIC_SECRET_SENTINEL" not in json.dumps(read)


def test_claude_detached_summary_needs_verified_leaf_association(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-ver",
        [
            {
                "type": "summary",
                "summary": "compacted alpha",
                "sessionId": "sid-ver",
                "cwd": cwd,
                "leafUuid": "u1",
                "timestamp": "2026-11-02T09:59:00Z",
            },
            _cl_user(cwd, "sid-ver", "u1", None, "2026-11-02T10:00:00Z", "export alpha"),
        ],
    )
    verified = scs.read(root, agent="claude", session_id="sid-ver", home=home)
    assert [m["text"] for m in verified["messages"]] == ["compacted alpha", "export alpha"]

    _claude_write(
        home,
        root,
        "sid-free",
        [
            {
                "type": "summary",
                "summary": "unrelated beta",
                "sessionId": "sid-free",
                "cwd": cwd,
                "leafUuid": "other",
                "timestamp": "2026-11-02T09:59:00Z",
            },
            _cl_user(cwd, "sid-free", "u1", None, "2026-11-02T10:00:00Z", "export alpha"),
        ],
    )
    stray = scs.read(root, agent="claude", session_id="sid-free", home=home)
    assert stray["complete"] is False
    assert "unrelated beta" not in json.dumps(stray)


def test_claude_leaf_pointer_gap_is_partial_not_branch_swap(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-1",
        [
            _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha"),
            _cl_user(cwd, "sid-1", "u9", None, "2026-11-02T10:05:00Z", "discarded branch"),
            {"type": "last-prompt", "leafUuid": "ghost", "sessionId": "sid-1", "cwd": cwd},
        ],
    )
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert read["complete"] is False
    assert "discarded branch" not in json.dumps(read)


def test_claude_conflicting_session_id_rejected(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    record = _cl_user(cwd, "other-id", "u1", None, "2026-11-02T10:00:00Z", "export alpha")
    _claude_write(home, root, "sid-1", [record])
    result = scs.discover(root, home=home)
    assert result["candidates"] == []
    assert result["complete"] is False


def test_claude_chain_order_is_causal_not_timestamp(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-1",
        [
            _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:05:00Z", "export alpha"),
            _cl_assistant(cwd, "sid-1", "u2", "u1", "2026-11-02T10:00:00Z", "alpha next step"),
        ],
    )
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert [m["text"] for m in read["messages"]] == ["export alpha", "alpha next step"]


def test_claude_ai_title_is_metadata_not_summary(tmp_path):
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
                "type": "ai-title",
                "cwd": cwd,
                "sessionId": "sid-1",
                "timestamp": "2026-11-02T10:02:00Z",
                "title": "alpha work",
            },
        ],
    )
    result = scs.discover(root, home=home)
    assert result["candidates"][0]["title"] == "alpha work"
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert [m["role"] for m in read["messages"]] == ["user"]


def test_claude_subdirectory_and_unrecognized_bucket_discovery(tmp_path):
    root = _init_repo(tmp_path / "proj")
    sub = root / "pkg" / "sub"
    sub.mkdir(parents=True)
    home = tmp_path / "home"
    _claude_write(
        home,
        sub,
        "sid-sub",
        [_cl_user(str(sub), "sid-sub", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    odd = home / ".claude" / "projects" / "not-an-encoded-name"
    odd.mkdir(parents=True)
    with open(odd / "sid-odd.jsonl", "w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(_cl_user(str(root), "sid-odd", "u1", None, "2026-11-02T10:01:00Z", "export alpha")) + "\n"
        )
    result = scs.discover(root, home=home)
    assert {c["session_id"] for c in result["candidates"]} == {"sid-sub", "sid-odd"}
    assert {c["session_id"]: c["relation"] for c in result["candidates"]}["sid-sub"] == "descendant"


def test_claude_discovery_never_full_reads(monkeypatch, tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-1",
        [_cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    import session_adapters

    real = session_adapters._jsonl_records
    calls = []

    def guard(path, ctx, **kwargs):
        calls.append(kwargs.get("whole_max"))
        assert kwargs.get("whole_max") != session_adapters.JSONL_WHOLE_MAX
        return real(path, ctx, **kwargs)

    monkeypatch.setattr(session_adapters, "_jsonl_records", guard)
    result = scs.discover(root, home=home)
    assert [c["session_id"] for c in result["candidates"]] == ["sid-1"]
    assert calls


def test_claude_assistant_blocks_only_export_text(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    record = _cl_assistant(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "alpha next step")
    record["message"]["content"] = [
        {"type": "thinking", "thinking": "SYNTHETIC_SECRET_SENTINEL"},
        {"type": "text", "text": "alpha next step"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "SYNTHETIC_SECRET_SENTINEL"}},
    ]
    _claude_write(
        home,
        root,
        "sid-1",
        [_cl_user(cwd, "sid-1", "u0", None, "2026-11-02T09:59:00Z", "export alpha"), record],
    )
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert read["messages"][-1]["text"] == "alpha next step"
    assert "SYNTHETIC_SECRET_SENTINEL" not in json.dumps(read)


def test_claude_text_secret_redacted(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-1",
        [
            _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha"),
            _cl_assistant(
                cwd,
                "sid-1",
                "u2",
                "u1",
                "2026-11-02T10:01:00Z",
                "use token=SYNTHETIC_SECRET_SENTINEL here",
            ),
        ],
    )
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert "SYNTHETIC_SECRET_SENTINEL" not in json.dumps(read)
    assert "export alpha" in json.dumps(read)


def test_claude_unreadable_file_is_distinct_from_absent(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    target = _claude_write(
        home,
        root,
        "sid-1",
        [_cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    os.chmod(target, 0o000)
    try:
        result = scs.discover(root, home=home)
    finally:
        os.chmod(target, 0o600)
    source = {s["agent"]: s for s in result["sources"]}["claude"]
    assert source["status"] in {"unreadable", "partial"}
    assert source["reason_code"] != "store-missing"


# --------------------------------------------------------------------------- #
# Codex                                                                       #
# --------------------------------------------------------------------------- #

CODEX_COLUMNS = "id,rollout_path,cwd,title,git_branch,git_sha,source,archived,updated_at"


def _codex_db(home: Path, rows: list[tuple], *, extra_schema: str = "") -> Path:
    codex = home / ".codex"
    codex.mkdir(parents=True, exist_ok=True)
    db = codex / "state_5.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE threads(id TEXT, rollout_path TEXT, cwd TEXT, title TEXT, "
        "git_branch TEXT, git_sha TEXT, source TEXT, archived INTEGER, updated_at TEXT)"
    )
    if extra_schema:
        conn.executescript(extra_schema)
    conn.executemany(f"INSERT INTO threads({CODEX_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def _codex_rollout(home: Path, thread_id: str, cwd: str, records: list[dict], *, archived=False) -> Path:
    base = home / ".codex" / ("archived_sessions" if archived else "sessions") / "2026" / "11" / "02"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"rollout-2026-11-02T10-00-00-{thread_id}.jsonl"
    meta = {
        "type": "session_meta",
        "payload": {
            "id": thread_id,
            "cwd": cwd,
            "runtime_workspace_roots": [cwd],
            "source": "vscode",
            "git": {"branch": "main", "sha": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"},
        },
    }
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(meta) + "\n")
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def _cx_user(text: str):
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def _cx_assistant(text: str):
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def test_codex_in_scope_thread_with_rollout(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    rollout = _codex_rollout(home, "tid-1", str(root), [_cx_user("export alpha"), _cx_assistant("alpha next step")])
    _codex_db(
        home,
        [
            (
                "tid-1",
                str(rollout),
                str(root),
                "alpha work",
                "main",
                "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                "vscode",
                0,
                "2026-11-02T10:00:00Z",
            )
        ],
    )
    result = scs.discover(root, home=home)
    assert len(result["candidates"]) == 1
    cand = result["candidates"][0]
    assert cand["session_id"] == "tid-1"
    assert cand["title"] == "alpha work"
    assert cand["branch"] == "main"
    assert cand["head"] == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    assert cand["has_user"] is True
    read = scs.read(root, agent="codex", session_id="tid-1", home=home)
    assert [m["text"] for m in read["messages"]] == ["export alpha", "alpha next step"]
    assert read["freshness"] in {"fresh", "stale"}


def test_codex_scope_revalidates_sibling_and_descendant(tmp_path):
    root = _init_repo(tmp_path / "proj")
    sibling = _init_repo(tmp_path / "proj-extra")
    sub = root / "sub"
    sub.mkdir()
    home = tmp_path / "home"
    good = _codex_rollout(home, "tid-sub", str(sub), [_cx_user("export alpha")])
    bad = _codex_rollout(home, "tid-sib", str(sibling), [_cx_user("unrelated beta")])
    _codex_db(
        home,
        [
            ("tid-sub", str(good), str(sub), "in", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z"),
            ("tid-sib", str(bad), str(sibling), "out", "main", "s", "vscode", 0, "2026-11-02T10:01:00Z"),
        ],
    )
    result = scs.discover(root, home=home)
    assert [c["session_id"] for c in result["candidates"]] == ["tid-sub"]


def test_codex_hostile_rollout_path_never_read(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    hostile = tmp_path / "hostile.jsonl"
    hostile.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "unrelated beta"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _codex_db(home, [("tid-evil", str(hostile), str(root), "evil", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    result = scs.discover(root, home=home)
    assert result["candidates"] == []
    assert "unrelated beta" not in json.dumps(result)


def test_codex_archived_rollout_only_when_flagged(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    rollout = _codex_rollout(home, "tid-a", str(root), [_cx_user("export alpha")], archived=True)
    _codex_db(home, [("tid-a", str(rollout), str(root), "old", "main", "s", "vscode", 1, "2026-11-02T10:00:00Z")])
    result = scs.discover(root, home=home)
    assert [c["session_id"] for c in result["candidates"]] == ["tid-a"]

    home2 = tmp_path / "home2"
    rollout2 = _codex_rollout(home2, "tid-b", str(root), [_cx_user("export alpha")], archived=True)
    _codex_db(home2, [("tid-b", str(rollout2), str(root), "sneaky", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    assert scs.discover(root, home=home2)["candidates"] == []


def test_codex_worker_thread_excluded(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    rollout = _codex_rollout(home, "tid-w", str(root), [_cx_user("export alpha")])
    _codex_db(
        home,
        [
            (
                "tid-w",
                str(rollout),
                str(root),
                "w",
                "main",
                "s",
                '{"subagent": {"parent": "p"}}',
                0,
                "2026-11-02T10:00:00Z",
            )
        ],
    )
    assert scs.discover(root, home=home)["candidates"] == []


def test_codex_meta_mismatch_blocks_body(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    other = tmp_path / "other"
    other.mkdir()
    rollout = _codex_rollout(home, "tid-x", str(other), [_cx_user("unrelated beta")])
    _codex_db(home, [("tid-x", str(rollout), str(root), "lie", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    result = scs.discover(root, home=home)
    assert result["candidates"] == []
    assert "unrelated beta" not in json.dumps(result)


def test_codex_fallback_scan_when_index_missing(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _codex_rollout(home, "tid-f", str(root), [_cx_user("export alpha")])
    result = scs.discover(root, home=home)
    source = {s["agent"]: s for s in result["sources"]}["codex"]
    assert source["status"] == "partial"
    assert source["reason_code"] == "index-missing"
    assert [c["session_id"] for c in result["candidates"]] == ["tid-f"]


def test_codex_absent_and_unknown_schema(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    result = scs.discover(root, home=home)
    assert {s["agent"]: s for s in result["sources"]}["codex"]["status"] == "absent"

    codex = home / ".codex"
    codex.mkdir(parents=True)
    conn = sqlite3.connect(codex / "state_5.sqlite")
    conn.execute("CREATE TABLE threads(id TEXT, weird TEXT)")
    conn.execute("INSERT INTO threads VALUES('x','y')")
    conn.commit()
    conn.close()
    result = scs.discover(root, home=home)
    assert {s["agent"]: s for s in result["sources"]}["codex"]["status"] == "unsupported"


def test_codex_live_wal_and_no_new_files(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    rollout = _codex_rollout(home, "tid-1", str(root), [_cx_user("export alpha")])
    rollout_wal = _codex_rollout(home, "wal-thread", str(root), [_cx_user("active branch")])
    db = _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])

    writer = sqlite3.connect(db)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        f"INSERT INTO threads({CODEX_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?)",
        ("wal-thread", str(rollout_wal), str(root), "open", "main", "s", "vscode", 0, "2026-11-02T10:02:00Z"),
    )
    writer.commit()
    import urllib.parse

    control = sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?immutable=1", uri=True)
    try:
        seen = control.execute("SELECT count(*) FROM threads WHERE id = 'wal-thread'").fetchone()[0]
    except sqlite3.Error:
        seen = 0
    control.close()
    assert seen == 0

    before = {p.name for p in db.parent.iterdir()}
    db_bytes = db.read_bytes()
    result = scs.discover(root, home=home)
    assert {c["session_id"] for c in result["candidates"]} == {"tid-1", "wal-thread"}
    assert {p.name for p in db.parent.iterdir()} == before
    assert db.read_bytes() == db_bytes
    writer.close()


def test_codex_closed_wal_remains_usable_without_new_files(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    rollout = _codex_rollout(home, "tid-1", str(root), [_cx_user("export alpha")])
    db = _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    writer = sqlite3.connect(db)
    writer.execute("PRAGMA journal_mode=WAL")
    rollout_wal = _codex_rollout(home, "tid-wal", str(root), [_cx_user("active branch")])
    writer.execute(
        f"INSERT INTO threads({CODEX_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?)",
        ("tid-wal", str(rollout_wal), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:02:00Z"),
    )
    writer.commit()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.close()
    (db.parent / "state_5.sqlite-wal").unlink(missing_ok=True)
    (db.parent / "state_5.sqlite-shm").unlink(missing_ok=True)
    before = {p.name for p in db.parent.iterdir()}
    result = scs.discover(root, home=home)
    assert {c["session_id"] for c in result["candidates"]} == {"tid-1", "tid-wal"}
    assert {p.name for p in db.parent.iterdir()} == before
    read = scs.read(root, agent="codex", session_id="tid-wal", home=home)
    assert read["status"] == "ok"
    assert {p.name for p in db.parent.iterdir()} == before


def test_codex_symlinked_and_fifo_store_paths_unreadable(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    rollout = _codex_rollout(home, "tid-1", str(root), [_cx_user("export alpha")])
    db = _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    real_db = db.read_bytes()
    db.unlink()
    outside = tmp_path / "outside.sqlite"
    outside.write_bytes(real_db)
    db.symlink_to(outside)
    result = scs.discover(root, home=home)
    assert {s["agent"]: s for s in result["sources"]}["codex"]["status"] == "unreadable"
    db.unlink()
    db.write_bytes(real_db)
    result = scs.discover(root, home=home)
    assert {s["agent"]: s for s in result["sources"]}["codex"]["status"] != "unreadable"

    home2 = tmp_path / "home2"
    _codex_rollout(home2, "tid-1", str(root), [_cx_user("export alpha")])
    db2 = _codex_db(home2, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    shm = db2.parent / "state_5.sqlite-shm"
    shm.symlink_to(outside)
    result = scs.discover(root, home=home2)
    assert {s["agent"]: s for s in result["sources"]}["codex"]["status"] == "unreadable"

    home3 = tmp_path / "home3"
    _codex_rollout(home3, "tid-1", str(root), [_cx_user("export alpha")])
    (home3 / ".codex" / "state_5.sqlite").unlink(missing_ok=True)
    os.mkfifo(home3 / ".codex" / "state_5.sqlite")
    result = scs.discover(root, home=home3)
    assert {s["agent"]: s for s in result["sources"]}["codex"]["status"] == "unreadable"


def test_codex_nonempty_wal_without_shm_is_unreadable(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    rollout = _codex_rollout(home, "tid-1", str(root), [_cx_user("export alpha")])
    db = _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    (db.parent / "state_5.sqlite-wal").write_bytes(b"x" * 128)
    shm = db.parent / "state_5.sqlite-shm"
    assert not shm.exists()
    result = scs.discover(root, home=home)
    assert {s["agent"]: s for s in result["sources"]}["codex"]["status"] == "unreadable"


def test_codex_event_msg_not_double_counted(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    records = [
        _cx_user("export alpha"),
        {"type": "event_msg", "payload": {"type": "user_message", "message": "export alpha"}},
        _cx_assistant("alpha next step"),
    ]
    rollout = _codex_rollout(home, "tid-1", str(root), records)
    _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    read = scs.read(root, agent="codex", session_id="tid-1", home=home)
    texts = [m["text"] for m in read["messages"]]
    assert texts.count("export alpha") == 1


def test_codex_native_compacted_replaces_history(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    records = [
        _cx_user("unrelated beta"),
        {
            "type": "compacted",
            "payload": {
                "message": "compacted alpha",
                "replacement_history": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "alpha next step"}],
                    },
                    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "export alpha"}]},
                ],
            },
        },
        _cx_user("active branch"),
    ]
    rollout = _codex_rollout(home, "tid-1", str(root), records)
    _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    read = scs.read(root, agent="codex", session_id="tid-1", home=home)
    texts = [m["text"] for m in read["messages"]]
    assert "unrelated beta" not in texts
    assert "alpha next step" in texts
    assert "export alpha" in texts
    assert "active branch" in texts
    assert "compacted alpha" in texts


def test_codex_compacted_unknown_shape_drops_prefix_partial(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    records = [
        _cx_user("unrelated beta"),
        {"type": "compacted", "payload": {"message": "compacted alpha"}},
        _cx_user("active branch"),
    ]
    rollout = _codex_rollout(home, "tid-1", str(root), records)
    _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    read = scs.read(root, agent="codex", session_id="tid-1", home=home)
    assert read["complete"] is False
    texts = [m["text"] for m in read["messages"]]
    assert "unrelated beta" not in texts
    assert "compacted alpha" in texts
    assert "active branch" in texts


def test_codex_metadata_and_noncontext_types_never_exposed(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    records = [
        {"type": "turn_context", "payload": {"cwd": str(root), "text": "unrelated beta"}},
        {"type": "token_usage_record", "payload": {"info": "x"}},
        {"type": "world_state", "payload": {"text": "unrelated beta"}},
        {"type": "inter_agent_communication_metadata", "payload": {"text": "unrelated beta"}},
        _cx_user("export alpha"),
        {"type": "response_item", "payload": {"type": "additional_tools", "tools": ["unrelated beta"]}},
        {"type": "response_item", "payload": {"type": "configuration_update", "text": "unrelated beta"}},
        {"type": "response_item", "payload": {"type": "agent_message", "text": "unrelated beta"}},
        {"type": "response_item", "payload": "not-a-dict"},
        _cx_assistant("alpha next step"),
    ]
    rollout = _codex_rollout(home, "tid-1", str(root), records)
    _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    read = scs.read(root, agent="codex", session_id="tid-1", home=home)
    assert [m["text"] for m in read["messages"]] == ["alpha next step"]
    assert "unrelated beta" not in json.dumps(read)
    assert "export alpha" not in json.dumps(read)
    assert read["complete"] is False


def test_codex_known_metadata_preserves_context(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    records = [
        {"type": "turn_context", "payload": {"cwd": str(root), "text": "unrelated beta"}},
        {"type": "token_usage_record", "payload": {"info": "x"}},
        {"type": "world_state", "payload": {"text": "unrelated beta"}},
        {"type": "inter_agent_communication_metadata", "payload": {"text": "unrelated beta"}},
        _cx_user("export alpha"),
        {"type": "response_item", "payload": {"type": "additional_tools", "tools": ["unrelated beta"]}},
        {"type": "response_item", "payload": {"type": "configuration_update", "text": "unrelated beta"}},
        {"type": "response_item", "payload": {"type": "agent_message", "text": "unrelated beta"}},
        _cx_assistant("alpha next step"),
    ]
    rollout = _codex_rollout(home, "tid-1", str(root), records)
    _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    read = scs.read(root, agent="codex", session_id="tid-1", home=home)
    assert [m["text"] for m in read["messages"]] == ["export alpha", "alpha next step"]
    assert "unrelated beta" not in json.dumps(read)


# --------------------------------------------------------------------------- #
# Devin                                                                       #
# --------------------------------------------------------------------------- #


def _devin_db(home: Path, sessions: list[tuple], nodes: list[tuple], *, schema_ok: bool = True) -> Path:
    base = home / ".local" / "share" / "devin" / "cli"
    base.mkdir(parents=True, exist_ok=True)
    db = base / "sessions.db"
    conn = sqlite3.connect(db)
    if schema_ok:
        conn.execute(
            "CREATE TABLE sessions(id TEXT, working_directory TEXT, created_at INTEGER, "
            "last_activity_at INTEGER, title TEXT, main_chain_id INTEGER, hidden INTEGER)"
        )
        conn.execute(
            "CREATE TABLE message_nodes(session_id TEXT, node_id INTEGER, "
            "parent_node_id INTEGER, chat_message TEXT, metadata TEXT)"
        )
        conn.executemany("INSERT INTO sessions VALUES(?,?,?,?,?,?,?)", sessions)
        conn.executemany("INSERT INTO message_nodes VALUES(?,?,?,?,?)", nodes)
    else:
        conn.execute("CREATE TABLE sessions(id TEXT)")
    conn.commit()
    conn.close()
    return db


def _dv_msg(role: str, text: str, mid: str = "m") -> str:
    return json.dumps({"message_id": mid, "role": role, "content": [{"type": "text", "text": text}]})


def test_devin_main_chain_walk_not_row_order(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s1", str(root), "2026-11-02T09:00:00Z", "2026-11-02T10:00:00Z", "alpha", 740, 0)]
    nodes = [
        ("s1", 735, None, _dv_msg("user", "export alpha", "m1"), "{}"),
        ("s1", 736, 735, _dv_msg("assistant", "alpha next step", "m2"), "{}"),
        ("s1", 738, 736, _dv_msg("user", "discarded branch", "m3"), "{}"),
        ("s1", 739, 738, _dv_msg("assistant", "unrelated beta", "m4"), "{}"),
        ("s1", 740, 736, _dv_msg("user", "active branch", "m5"), "{}"),
    ]
    _devin_db(home, sessions, nodes)
    result = scs.discover(root, home=home)
    assert [c["session_id"] for c in result["candidates"]] == ["s1"]
    assert result["candidates"][0]["has_user"] is True
    read = scs.read(root, agent="devin", session_id="s1", home=home)
    assert [m["text"] for m in read["messages"]] == [
        "export alpha",
        "alpha next step",
        "active branch",
    ]


def test_devin_missing_node_and_cycle_partial(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [
        ("gap", str(root), "c", "2026-11-02T10:00:00Z", "g", 2, 0),
        ("cyc", str(root), "c", "2026-11-02T10:01:00Z", "c", 1, 0),
    ]
    nodes = [
        ("gap", 2, 99, _dv_msg("user", "export alpha"), "{}"),
        ("cyc", 1, 2, _dv_msg("user", "export alpha"), "{}"),
        ("cyc", 2, 1, _dv_msg("assistant", "alpha next step"), "{}"),
    ]
    _devin_db(home, sessions, nodes)
    assert scs.read(root, agent="devin", session_id="gap", home=home)["complete"] is False
    assert scs.read(root, agent="devin", session_id="cyc", home=home)["complete"] is False


def test_devin_summary_flag_and_system_prefix(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s1", str(root), "c", "2026-11-02T10:00:00Z", "alpha", 3, 0)]
    nodes = [
        ("s1", 1, None, _dv_msg("user", "SYNTHETIC_SECRET_SENTINEL"), json.dumps({"is_system_prefix": True})),
        (
            "s1",
            2,
            1,
            _dv_msg("assistant", "compacted alpha"),
            json.dumps({"summarized_from": "n1", "is_system_prefix": False}),
        ),
        ("s1", 3, 2, _dv_msg("user", "export alpha"), "{}"),
    ]
    _devin_db(home, sessions, nodes)
    read = scs.read(root, agent="devin", session_id="s1", home=home)
    roles = [m["role"] for m in read["messages"]]
    assert roles == ["summary", "user"]
    assert read["messages"][0]["text"] == "compacted alpha"
    assert "SYNTHETIC_SECRET_SENTINEL" not in json.dumps(read)


def test_devin_oversized_node_and_schema_guard(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    big = _dv_msg("user", "z" * (300 * 1024))
    sessions = [("s1", str(root), "c", "2026-11-02T10:00:00Z", "alpha", 2, 0)]
    nodes = [
        ("s1", 1, None, _dv_msg("user", "export alpha"), "{}"),
        ("s1", 2, 1, big, "{}"),
    ]
    _devin_db(home, sessions, nodes)
    read = scs.read(root, agent="devin", session_id="s1", home=home)
    assert read["complete"] is False

    home2 = tmp_path / "home2"
    _devin_db(home2, [], [], schema_ok=False)
    result = scs.discover(root, home=home2)
    assert {s["agent"]: s for s in result["sources"]}["devin"]["status"] == "unsupported"


def test_devin_hidden_and_absent(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s1", str(root), "c", "2026-11-02T10:00:00Z", "alpha", 1, 1)]
    nodes = [("s1", 1, None, _dv_msg("user", "export alpha"), "{}")]
    _devin_db(home, sessions, nodes)
    assert scs.discover(root, home=home)["candidates"] == []

    empty = tmp_path / "empty-home"
    result = scs.discover(root, home=empty)
    assert {s["agent"]: s for s in result["sources"]}["devin"]["status"] == "absent"


def test_devin_read_only_leaves_db_unchanged(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s1", str(root), "c", "2026-11-02T10:00:00Z", "alpha", 1, 0)]
    nodes = [("s1", 1, None, _dv_msg("user", "export alpha"), "{}")]
    db = _devin_db(home, sessions, nodes)
    before = {p.name for p in db.parent.iterdir()}
    db_bytes = db.read_bytes()
    scs.discover(root, home=home)
    scs.read(root, agent="devin", session_id="s1", home=home)
    assert {p.name for p in db.parent.iterdir()} == before
    assert db.read_bytes() == db_bytes


# --------------------------------------------------------------------------- #
# Pi                                                                          #
# --------------------------------------------------------------------------- #


def _pi_write(
    home: Path,
    cwd: Path,
    session_id: str,
    records: list[dict],
    bucket_name=None,
    filename=None,
) -> Path:
    enc = bucket_name or f"--{''.join(c if c.isalnum() or c in '_-' else '-' for c in str(cwd))}--"
    bucket = home / ".pi" / "agent" / "sessions" / enc
    bucket.mkdir(parents=True, exist_ok=True)
    target = bucket / (filename or f"{session_id}.jsonl")
    header = {"type": "session", "version": 3, "id": session_id, "timestamp": "2026-11-02T09:00:00Z", "cwd": str(cwd)}
    with open(target, "w", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(header) + "\n")
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return target


def _pi_msg(eid: str, parent, role: str, text: str):
    return {
        "type": "message",
        "id": eid,
        "parentId": parent,
        "timestamp": "2026-11-02T10:00:00Z",
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    }


def _pi_compaction(cid: str, parent, kept: str, summary: str):
    return {
        "type": "compaction",
        "id": cid,
        "parentId": parent,
        "timestamp": "2026-11-02T10:01:00Z",
        "firstKeptEntryId": kept,
        "summary": summary,
        "systemMessage": "SYNTHETIC_SECRET_SENTINEL",
    }


def _pi_edit(eid: str, parent, target: str, replacement):
    return {
        "type": "context_edit",
        "id": eid,
        "parentId": parent,
        "timestamp": "2026-11-02T10:02:00Z",
        "targetId": target,
        "replacement": replacement,
    }


def test_pi_active_leaf_traces_parents(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p1",
        [
            _pi_msg("e1", None, "user", "export alpha"),
            _pi_msg("e4", "e1", "user", "discarded branch"),
            _pi_msg("e2", "e1", "assistant", "alpha next step"),
            _pi_msg("e3", "e2", "user", "active branch"),
            {"type": "model_change", "id": "mc", "parentId": "e3", "timestamp": "2026-11-02T10:03:00Z"},
        ],
    )
    result = scs.discover(root, home=home)
    assert [c["session_id"] for c in result["candidates"]] == ["p1"]
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    assert read["complete"] is True
    assert [m["text"] for m in read["messages"]] == [
        "export alpha",
        "alpha next step",
        "active branch",
    ]


def test_pi_disconnected_setting_leaf_means_empty_branch(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p1",
        [
            _pi_msg("e1", None, "user", "export alpha"),
            {"type": "model_change", "id": "mc", "parentId": None, "timestamp": "2026-11-02T10:03:00Z"},
        ],
    )
    assert scs.discover(root, home=home)["candidates"] == []


def test_pi_malformed_records_never_crash(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p1",
        [
            _pi_msg("e1", None, "user", "export alpha"),
            {
                "type": "message",
                "id": "e2",
                "parentId": "e1",
                "message": "not-a-dict",
                "timestamp": "2026-11-02T10:01:00Z",
            },
            {
                "type": "message",
                "id": "e3",
                "parentId": "e2",
                "message": {"role": {"odd": 1}, "content": "x"},
                "timestamp": "2026-11-02T10:02:00Z",
            },
            _pi_edit("ce1", "e3", "e1", {}),
            _pi_msg("e4", "ce1", "assistant", "alpha next step"),
        ],
    )
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    assert read["status"] == "ok"
    assert read["complete"] is False


def test_pi_header_cwd_enforced(tmp_path):
    root = _init_repo(tmp_path / "proj")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    home = tmp_path / "home"
    _pi_write(home, root, "p1", [_pi_msg("e1", None, "user", "export alpha")])
    enc = "".join(c if c.isalnum() or c in "_-" else "-" for c in str(root))
    bad = home / ".pi" / "agent" / "sessions" / f"--{enc}--" / "p2.jsonl"
    header = {"type": "session", "version": 3, "id": "p2", "timestamp": "2026-11-02T09:00:00Z", "cwd": str(foreign)}
    with open(bad, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(header) + "\n")
        handle.write(json.dumps(_pi_msg("e1", None, "user", "unrelated beta")) + "\n")
    result = scs.discover(root, home=home)
    assert [c["session_id"] for c in result["candidates"]] == ["p1"]
    assert "unrelated beta" not in json.dumps(result)


def test_pi_compaction_keeps_only_kept_span(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p1",
        [
            _pi_msg("e1", None, "user", "unrelated beta"),
            _pi_msg("e2", "e1", "user", "export alpha"),
            _pi_compaction("c1", "e2", "e2", "compacted alpha"),
            _pi_msg("e3", "c1", "assistant", "alpha next step"),
        ],
    )
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    assert [m["role"] for m in read["messages"]] == ["summary", "user", "assistant"]
    texts = [m["text"] for m in read["messages"]]
    assert texts == ["compacted alpha", "export alpha", "alpha next step"]
    assert "unrelated beta" not in json.dumps(read)
    assert "SYNTHETIC_SECRET_SENTINEL" not in json.dumps(read)


def test_pi_older_compaction_in_kept_span_emits_no_summary(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p1",
        [
            _pi_msg("e0", None, "user", "unrelated beta"),
            _pi_compaction("c0", "e0", "e1", "discarded branch"),
            _pi_msg("e1", "c0", "user", "export alpha"),
            _pi_compaction("c1", "e1", "c0", "compacted alpha"),
            _pi_msg("e2", "c1", "assistant", "active branch"),
        ],
    )
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    texts = [m["text"] for m in read["messages"]]
    assert texts == ["compacted alpha", "export alpha", "active branch"]
    assert "discarded branch" not in texts
    assert "unrelated beta" not in texts


def test_pi_compaction_missing_kept_boundary_partial(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p1",
        [
            _pi_msg("e1", None, "user", "export alpha"),
            _pi_compaction("c1", "e1", "ghost", "compacted alpha"),
            _pi_msg("e2", "c1", "assistant", "alpha next step"),
        ],
    )
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    assert read["complete"] is False

    home2 = tmp_path / "home2"
    no_kept = _pi_compaction("c1", "e1", "e1", "compacted alpha")
    del no_kept["firstKeptEntryId"]
    _pi_write(
        home2,
        root,
        "p1",
        [
            _pi_msg("e1", None, "user", "export alpha"),
            no_kept,
            _pi_msg("e2", "c1", "assistant", "alpha next step"),
        ],
    )
    assert scs.read(root, agent="pi", session_id="p1", home=home2)["complete"] is False


def test_pi_context_edit_replaces_and_omits(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p1",
        [
            _pi_msg("e1", None, "user", "export alpha"),
            _pi_edit("ce1", "e1", "e1", {"content": [{"type": "text", "text": "alpha next step"}]}),
            _pi_msg("e2", "ce1", "user", "unrelated beta"),
            _pi_edit("ce2", "e2", "e2", None),
            _pi_msg("e3", "ce2", "assistant", "active branch"),
        ],
    )
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    texts = [m["text"] for m in read["messages"]]
    assert texts == ["alpha next step", "active branch"]
    assert "unrelated beta" not in json.dumps(read)


def test_pi_context_edit_on_discarded_branch_ignored(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p1",
        [
            _pi_msg("e1", None, "user", "export alpha"),
            _pi_msg("e9", "e1", "user", "discarded branch"),
            _pi_edit("ce9", "e9", "e1", None),
            _pi_msg("e2", "e1", "assistant", "active branch"),
        ],
    )
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    texts = [m["text"] for m in read["messages"]]
    assert texts == ["export alpha", "active branch"]


def test_pi_context_edit_system_target_stays_excluded(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p1",
        [
            _pi_msg("e1", None, "user", "export alpha"),
            _pi_msg("e2", "e1", "system", "unrelated beta"),
            _pi_edit("ce1", "e2", "e2", {"content": [{"type": "text", "text": "alpha next step"}]}),
            _pi_msg("e3", "ce1", "assistant", "active branch"),
        ],
    )
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    texts = [m["text"] for m in read["messages"]]
    assert texts == ["export alpha", "active branch"]


def test_pi_context_edit_unknown_shape_partial(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p1",
        [
            _pi_msg("e1", None, "user", "export alpha"),
            _pi_edit("ce1", "e1", "e1", "unrelated beta"),
            _pi_msg("e2", "ce1", "assistant", "alpha next step"),
        ],
    )
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    assert read["complete"] is False
    assert "unrelated beta" not in json.dumps(read)


def test_pi_empty_and_system_only_sessions_not_candidates(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(home, root, "p-empty", [])
    _pi_write(home, root, "p-sys", [_pi_msg("e1", None, "system", "unrelated beta")])
    result = scs.discover(root, home=home)
    assert result["candidates"] == []


def test_pi_missing_parent_partial(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p1",
        [
            _pi_msg("e1", "ghost", "user", "export alpha"),
        ],
    )
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    assert read["complete"] is False


# --------------------------------------------------------------------------- #
# Cross-cutting                                                               #
# --------------------------------------------------------------------------- #


def test_source_statuses_distinct(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    result = scs.discover(root, home=home)
    statuses = {s["agent"]: s["status"] for s in result["sources"]}
    assert statuses["claude"] == "absent"
    assert statuses["codex"] == "absent"
    assert statuses["devin"] == "absent"
    assert statuses["pi"] == "absent"
    assert statuses["cursor"] == "absent"


def test_devin_numeric_timestamps_normalized(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s1", str(root), 1762000000, 1762003600, "alpha", 1, 0)]
    nodes = [("s1", 1, None, _dv_msg("user", "export alpha"), "{}")]
    _devin_db(home, sessions, nodes)
    result = scs.discover(root, home=home)
    updated = result["candidates"][0]["updated_at"]
    assert updated == "2025-11-01T13:26:40Z"


def test_devin_committed_wal_visible_and_closed_wal_usable(tmp_path):
    import urllib.parse

    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s1", str(root), 1, 1, "alpha", 1, 0)]
    nodes = [("s1", 1, None, _dv_msg("user", "export alpha"), "{}")]
    db = _devin_db(home, sessions, nodes)
    writer = sqlite3.connect(db)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT INTO sessions VALUES('s-wal',?,?,?,?,?,?)", (str(root), 1, 2, "w", 1, 0))
    writer.execute("INSERT INTO message_nodes VALUES('s-wal',1,NULL,?,?)", (_dv_msg("user", "active branch"), "{}"))
    writer.commit()
    control = sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?immutable=1", uri=True)
    try:
        seen = control.execute("SELECT count(*) FROM sessions WHERE id = 's-wal'").fetchone()[0]
    except sqlite3.Error:
        seen = 0
    control.close()
    assert seen == 0
    before = {p.name for p in db.parent.iterdir()}
    result = scs.discover(root, home=home)
    assert {c["session_id"] for c in result["candidates"]} == {"s1", "s-wal"}
    assert {p.name for p in db.parent.iterdir()} == before
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.close()
    (db.parent / "sessions.db-wal").unlink(missing_ok=True)
    (db.parent / "sessions.db-shm").unlink(missing_ok=True)
    before = {p.name for p in db.parent.iterdir()}
    result = scs.discover(root, home=home)
    assert {c["session_id"] for c in result["candidates"]} == {"s1", "s-wal"}
    assert {p.name for p in db.parent.iterdir()} == before


def test_devin_byte_cap_and_clipped_metadata_partial(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    big_meta = json.dumps({"pad": "m" * 40000})
    sessions = [("s1", str(root), 1, 1, "alpha", 1, 0)]
    nodes = [("s1", 1, None, _dv_msg("user", "export alpha"), big_meta)]
    _devin_db(home, sessions, nodes)
    read = scs.read(root, agent="devin", session_id="s1", home=home)
    assert read["complete"] is False

    home2 = tmp_path / "home2"
    nodes = [("s1", 1, None, _dv_msg("user", "SYNTHETIC_SECRET_SENTINEL"), big_meta)]
    _devin_db(home2, sessions, nodes)
    read = scs.read(root, agent="devin", session_id="s1", home=home2)
    assert read["complete"] is False


def test_codex_fallback_ignores_deep_tree(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    deep = home / ".codex" / "sessions" / "2026" / "11" / "02" / "nested" / "deeper"
    deep.mkdir(parents=True)
    meta = {"type": "session_meta", "payload": {"id": "tid-deep", "cwd": str(root)}}
    with open(deep / "rollout-x-tid-deep.jsonl", "w", encoding="utf-8") as handle:
        handle.write(json.dumps(meta) + "\n")
        handle.write(json.dumps(_cx_user("unrelated beta")) + "\n")
    result = scs.discover(root, home=home)
    assert result["candidates"] == []
    assert "unrelated beta" not in json.dumps(result)


def test_dir_streaming_stops_at_cap(monkeypatch, tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    for index in range(10):
        _claude_write(
            home,
            root,
            f"sid-{index}",
            [_cl_user(cwd, f"sid-{index}", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
        )
    import session_adapters

    monkeypatch.setattr(session_adapters, "DIR_LISTING_CAP", 3)
    consumed = {"n": 0}
    real_scandir = os.scandir

    class _Guard:
        def __init__(self, it):
            self._it = iter(it)

        def __iter__(self):
            return self

        def __next__(self):
            consumed["n"] += 1
            assert consumed["n"] <= 4
            return next(self._it)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(os, "scandir", lambda p: _Guard(real_scandir(p)))
    result = scs.discover(root, home=home)
    assert result["complete"] is False


def test_read_preserves_unavailable_source_reason(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    rollout = _codex_rollout(home, "tid-1", str(root), [_cx_user("export alpha")])
    db = _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "x")])
    (db.parent / "state_5.sqlite-wal").write_bytes(b"x" * 64)
    result = scs.read(root, agent="codex", session_id="tid-1", home=home)
    assert result["status"] == "unavailable"
    assert result["error"]["reason_code"] != "unknown-session"


def test_read_adapter_crash_is_classified_without_paths(monkeypatch, tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    _claude_write(
        home,
        root,
        "sid-1",
        [_cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha")],
    )
    import session_adapters

    def boom(ctx, cand):
        raise ValueError(f"boom {cand['_locator']['path']}")

    monkeypatch.setattr(session_adapters.ADAPTERS["claude"], "read_messages", boom)
    result = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert result["status"] != "ok"
    assert str(home) not in json.dumps(result)
    assert result["error"]["reason_code"] == "adapter-error"


def test_sanitize_terminal_and_quoted_secret_keys(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    record = _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha")
    record["message"]["content"] = [
        {"type": "text", "text": '\x1b[31mexport alpha\x1b[0m {"api_key": "SYNTHETIC_SECRET_SENTINEL"}'}
    ]
    _claude_write(home, root, "sid-1", [record])
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert "SYNTHETIC_SECRET_SENTINEL" not in json.dumps(read)
    assert "\x1b" not in json.dumps(read)


def test_pi_symlinked_sessions_dir_rejected(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    outside = tmp_path / "outside-sessions"
    outside.mkdir()
    base = home / ".pi" / "agent"
    base.mkdir(parents=True)
    (base / "sessions").symlink_to(outside)
    result = scs.discover(root, home=home)
    assert {s["agent"]: s for s in result["sources"]}["pi"]["status"] in {"absent", "unreadable"}


def test_r01_cursor_present_is_unsupported_and_incomplete(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    (home / ".cursor" / "projects").mkdir(parents=True)
    result = scs.discover(root, home=home)
    by_name = {s["agent"]: s for s in result["sources"]}
    assert by_name["cursor"]["status"] == "unsupported"
    assert by_name["cursor"]["complete"] is False
    assert result["complete"] is False
    assert result["status"] == "partial"


def test_r02_symlinked_native_ancestors_rejected(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    real_local = tmp_path / "real-local"
    (real_local / "share" / "devin" / "cli").mkdir(parents=True)
    base = home / ".local" / "share"
    base.parent.mkdir(parents=True)
    base.symlink_to(real_local / "share")
    sessions = [("s1", str(root), 1, 1, "alpha", 1, 0)]
    nodes = [("s1", 1, None, _dv_msg("user", "export alpha"), "{}")]
    db = real_local / "share" / "devin" / "cli" / "sessions.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE sessions(id TEXT, working_directory TEXT, created_at INTEGER, "
        "last_activity_at INTEGER, title TEXT, main_chain_id INTEGER, hidden INTEGER)"
    )
    conn.execute(
        "CREATE TABLE message_nodes(session_id TEXT, node_id INTEGER, "
        "parent_node_id INTEGER, chat_message TEXT, metadata TEXT)"
    )
    conn.executemany("INSERT INTO sessions VALUES(?,?,?,?,?,?,?)", sessions)
    conn.executemany("INSERT INTO message_nodes VALUES(?,?,?,?,?)", nodes)
    conn.commit()
    conn.close()
    result = scs.discover(root, home=home)
    assert {s["agent"]: s for s in result["sources"]}["devin"]["status"] == "unreadable"

    home2 = tmp_path / "home2"
    outside = tmp_path / "outside-claude"
    outside.mkdir()
    (home2 / ".claude").parent.mkdir(parents=True, exist_ok=True)
    (home2 / ".claude").symlink_to(outside)
    result = scs.discover(root, home=home2)
    assert {s["agent"]: s for s in result["sources"]}["claude"]["status"] != "ok"


def test_r03_rollback_db_uses_ro_not_immutable_and_journal_refused(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s1", str(root), 1, 1, "alpha", 1, 0)]
    nodes = [("s1", 1, None, _dv_msg("user", "export alpha"), "{}")]
    db = _devin_db(home, sessions, nodes)
    result = scs.discover(root, home=home)
    assert [c["session_id"] for c in result["candidates"]] == ["s1"]
    assert {p.name for p in db.parent.iterdir()} == {"sessions.db"}

    (db.parent / "sessions.db-journal").write_bytes(b"j" * 32)
    result = scs.discover(root, home=home)
    assert {s["agent"]: s for s in result["sources"]}["devin"]["status"] == "unreadable"
    assert (db.parent / "sessions.db-journal").read_bytes() == b"j" * 32


def test_r03_zero_length_wal_with_valid_shm_readable(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s1", str(root), 1, 1, "alpha", 1, 0)]
    nodes = [("s1", 1, None, _dv_msg("user", "export alpha"), "{}")]
    db = _devin_db(home, sessions, nodes)
    writer = sqlite3.connect(db)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT INTO sessions VALUES('s2',?,1,2,'b',1,0)", (str(root),))
    writer.execute("INSERT INTO message_nodes VALUES('s2',1,NULL,?,?)", (_dv_msg("user", "active branch"), "{}"))
    writer.commit()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    (db.parent / "sessions.db-wal").write_bytes(b"")
    result = scs.discover(root, home=home)
    writer.close()
    assert {s["agent"]: s for s in result["sources"]}["devin"]["status"] != "unreadable"
    assert {c["session_id"] for c in result["candidates"]} == {"s1", "s2"}


def test_r03_immutable_recheck_failure_refuses_read(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s1", str(root), 1, 1, "alpha", 1, 0)]
    nodes = [("s1", 1, None, _dv_msg("user", "export alpha"), "{}")]
    _devin_db(home, sessions, nodes)
    import session_adapters

    monkeypatch.setattr(session_adapters._SqliteGuard, "recheck", lambda self: False)
    result = scs.read(root, agent="devin", session_id="s1", home=home)
    assert result["status"] != "ok"
    assert result.get("messages", []) == []


def test_r04_metadata_bytes_charged_and_numeric_guards(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    big = "t" * 1000
    sessions = [("s1", str(root), 1, 1, big, 1, 0)]
    nodes = [("s1", 1, None, _dv_msg("user", "export alpha"), "{}")]
    _devin_db(home, sessions, nodes)
    import session_adapters

    ctx_calls = []
    real_take = session_adapters.Budget.take

    def spy(self, amount):
        ctx_calls.append(amount)
        return real_take(self, amount)

    monkeypatch.setattr(session_adapters.Budget, "take", spy)
    result = scs.discover(root, home=home)
    assert result["candidates"]
    assert any(amount >= 1000 for amount in ctx_calls)

    home2 = tmp_path / "home2"
    base = home2 / ".local" / "share" / "devin" / "cli"
    base.mkdir(parents=True)
    conn = sqlite3.connect(base / "sessions.db")
    conn.execute(
        "CREATE TABLE sessions(id TEXT, working_directory TEXT, created_at INTEGER, "
        "last_activity_at INTEGER, title TEXT, main_chain_id INTEGER, hidden INTEGER)"
    )
    conn.execute(
        "CREATE TABLE message_nodes(session_id TEXT, node_id INTEGER, "
        "parent_node_id INTEGER, chat_message TEXT, metadata TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES('s1',?,1,1,'a','not-an-int',0)", (str(root),))
    conn.execute("INSERT INTO message_nodes VALUES('s1',1,NULL,?,?)", (_dv_msg("user", "export alpha"), "{}"))
    conn.commit()
    conn.close()
    result = scs.discover(root, home=home2)
    assert result["complete"] is False


def test_r04_devin_metadata_malformed_withheld(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s1", str(root), 1, 1, "alpha", 2, 0)]
    nodes = [
        ("s1", 1, None, _dv_msg("user", "SYNTHETIC_SECRET_SENTINEL"), "not-json{{"),
        ("s1", 2, 1, _dv_msg("user", "export alpha"), "{}"),
    ]
    _devin_db(home, sessions, nodes)
    read = scs.read(root, agent="devin", session_id="s1", home=home)
    assert read["complete"] is False
    assert "export alpha" in json.dumps(read)
    assert "SYNTHETIC_SECRET_SENTINEL" not in json.dumps(read)


def test_r05_foreign_pi_files_do_not_exhaust_probe_budget(tmp_path):
    root = _init_repo(tmp_path / "proj")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    home = tmp_path / "home"
    bucket = home / ".pi" / "agent" / "sessions" / "--odd-bucket--"
    bucket.mkdir(parents=True)
    _pi_write(
        home,
        root,
        "p1",
        [_pi_msg("e1", None, "user", "export alpha")],
        bucket_name="--odd-bucket--",
        filename="p1.jsonl",
    )
    for index in range(3):
        header = {
            "type": "session",
            "version": 3,
            "id": f"big-{index}",
            "timestamp": "2026-11-02T09:00:00Z",
            "cwd": str(foreign),
        }
        with open(bucket / f"big-{index}.jsonl", "w", encoding="utf-8") as handle:
            handle.write(json.dumps(header) + "\n")
            handle.write(json.dumps(_pi_msg("e1", None, "user", "x" * 350000)) + "\n")
        os.utime(bucket / f"big-{index}.jsonl", (3000000000, 3000000000))
    os.utime(bucket / "p1.jsonl", (1000000000, 1000000000))
    result = scs.discover(root, home=home)
    assert [c["session_id"] for c in result["candidates"]] == ["p1"]


def test_r06_claude_markers_only_from_active_chain(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    mode_rec = {"type": "mode", "cwd": cwd, "sessionId": "sid-1", "uuid": "m1", "timestamp": "2026-11-02T10:03:00Z"}
    _claude_write(
        home,
        root,
        "sid-1",
        [
            _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha"),
            _cl_user(cwd, "sid-1", "u9", "u1", "2026-11-02T10:01:00Z", "discarded branch"),
            mode_rec,
            {"type": "last-prompt", "leafUuid": "m1", "sessionId": "sid-1", "cwd": cwd},
        ],
    )
    result = scs.discover(root, home=home)
    assert result["candidates"] == []


def test_r07_codex_has_user_reflects_compaction_replacement(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    records = [
        _cx_user("export alpha"),
        {
            "type": "compacted",
            "payload": {
                "message": "compacted alpha",
                "replacement_history": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "alpha next step"}],
                    },
                ],
            },
        },
    ]
    rollout = _codex_rollout(home, "tid-1", str(root), records)
    _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    result = scs.discover(root, home=home)
    assert result["candidates"][0]["has_user"] is False


def test_r08_codex_foreign_turn_context_and_worker_swap_refused(tmp_path):
    root = _init_repo(tmp_path / "proj")
    foreign = _init_repo(tmp_path / "foreign")
    home = tmp_path / "home"
    records = [
        _cx_user("export alpha"),
        {"type": "turn_context", "payload": {"cwd": str(foreign)}},
        _cx_user("active branch"),
    ]
    rollout = _codex_rollout(home, "tid-1", str(root), records)
    _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    read = scs.read(root, agent="codex", session_id="tid-1", home=home)
    assert read["status"] != "ok"

    home2 = tmp_path / "home2"
    rollout2 = _codex_rollout(home2, "tid-1", str(root), [_cx_user("export alpha")])
    lines = rollout2.read_text().splitlines()
    meta = json.loads(lines[0])
    meta["payload"]["source"] = '{"subagent": {"parent": "p"}}'
    lines[0] = json.dumps(meta)
    rollout2.write_text("\n".join(lines) + "\n")
    _codex_db(home2, [("tid-1", str(rollout2), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    read = scs.read(root, agent="codex", session_id="tid-1", home=home2)
    assert read["status"] != "ok"


def test_r09_codex_malformed_compacted_clears_old_prefix(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    records = [
        _cx_user("export alpha"),
        {"type": "compacted", "payload": "bad"},
        _cx_user("active branch"),
    ]
    rollout = _codex_rollout(home, "tid-1", str(root), records)
    _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "2026-11-02T10:00:00Z")])
    read = scs.read(root, agent="codex", session_id="tid-1", home=home)
    assert read["complete"] is False
    texts = [m["text"] for m in read["messages"]]
    assert "export alpha" not in texts
    assert "active branch" in texts


def test_r10_missing_node_is_graph_gap_not_byte_cap(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("gap", str(root), 1, 1, "g", 7, 0)]
    nodes = []
    _devin_db(home, sessions, nodes)
    read = scs.read(root, agent="devin", session_id="gap", home=home)
    assert read["complete"] is False
    assert read["truncation"] == "graph-gap"


def test_r13_pi_full_read_header_revalidated(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    home = tmp_path / "home"
    target = _pi_write(home, root, "p1", [_pi_msg("e1", None, "user", "export alpha")])
    import session_adapters

    real = session_adapters._first_json_record

    def swapped(path, ctx):
        if Path(path) == target:
            return {"type": "session", "version": 3, "id": "p1", "cwd": str(root)}
        return real(path, ctx)

    monkeypatch.setattr(session_adapters, "_first_json_record", swapped)
    lines = target.read_text().splitlines()
    header = json.loads(lines[0])
    header["cwd"] = str(foreign)
    lines[0] = json.dumps(header)
    target.write_text("\n".join(lines) + "\n")
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    assert read["status"] != "ok"
    assert "export alpha" not in json.dumps(read)


def test_r14_pi_context_edit_missing_and_primitive_replacement(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    no_repl = {
        "type": "context_edit",
        "id": "ce1",
        "parentId": "e1",
        "timestamp": "2026-11-02T10:02:00Z",
        "targetId": "e1",
    }
    _pi_write(home, root, "p1", [_pi_msg("e1", None, "user", "export alpha"), no_repl])
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    assert read["complete"] is False
    assert "export alpha" not in json.dumps(read)

    home2 = tmp_path / "home2"
    _pi_write(
        home2,
        root,
        "p1",
        [
            _pi_msg("e1", None, "user", "export alpha"),
            _pi_edit("ce1", "e1", "e1", {"content": 42}),
        ],
    )
    read = scs.read(root, agent="pi", session_id="p1", home=home2)
    assert read["complete"] is False
    assert "export alpha" not in json.dumps(read)


def test_r15_export_caps_and_safe_record_ids(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    big = "b" * 9000
    _claude_write(
        home,
        root,
        "sid-1",
        [
            _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", big),
            _cl_assistant(cwd, "sid-1", "u2", "u1", "2026-11-02T10:01:00Z", "alpha next step"),
        ],
    )
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert len(read["messages"]) == 2
    first = read["messages"][0]
    assert len(first["text"]) <= 6000 + 32
    assert first["text_truncated"] is True
    assert "omitted" in first["text"]
    assert read["complete"] is False
    assert read["truncation_reasons"]
    cand = read["candidate"]
    assert cand["project_path"] == "."
    assert cand["association"] == "native-cwd"
    assert cand["association_confidence"] == "verified"
    assert cand["source_format"] == "claude-jsonl"


def test_r15_unsafe_record_id_hashed(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    record = _cl_user(cwd, "sid-1", "../../etc/passwd\x1b", None, "2026-11-02T10:00:00Z", "export alpha")
    _claude_write(home, root, "sid-1", [record])
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    rid = read["messages"][0]["record_id"]
    assert rid.startswith("record-")
    assert "passwd" not in rid
    assert "\x1b" not in rid


def test_r16_devin_alias_session_discovered(tmp_path):
    root = _init_repo(tmp_path / "proj")
    alias = tmp_path / "alias-proj"
    alias.symlink_to(root)
    home = tmp_path / "home"
    sessions = [("s-alias", str(alias), 1, 9, "alias-session", 1, 0)]
    nodes = [("s-alias", 1, None, _dv_msg("user", "export alpha"), "{}")]
    _devin_db(home, sessions, nodes)
    result = scs.discover(root, home=home)
    assert "s-alias" in {c["session_id"] for c in result["candidates"]}
    read = scs.read(root, agent="devin", session_id="s-alias", home=home)
    assert read["status"] == "ok"
    assert read["messages"][0]["text"] == "export alpha"


def test_r16_devin_old_id_beyond_row_cap_targeted_read(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s-old", str(root), 1, 0, "old-session", 1, 0)]
    nodes = [("s-old", 1, None, _dv_msg("user", "export alpha"), "{}")]
    for index in range(210):
        sessions.append((f"s{index}", str(root), 1, 100 + index, "f", 1, 0))
        nodes.append((f"s{index}", 1, None, _dv_msg("user", "unrelated beta"), "{}"))
    _devin_db(home, sessions, nodes)
    result = scs.discover(root, home=home)
    assert "s-old" not in {c["session_id"] for c in result["candidates"]}
    assert result["complete"] is False
    read = scs.read(root, agent="devin", session_id="s-old", home=home)
    assert read["status"] == "ok"
    assert read["messages"][0]["text"] == "export alpha"


def test_r17_credential_and_terminal_redaction(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    record = _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha")
    record["message"]["content"] = [
        {
            "type": "text",
            "text": (
                "export alpha\n"
                "Authorization: Bearer SYNTHETIC_SECRET_SENTINEL\n"
                "Authorization: Basic SYNTHETIC_SECRET_SENTINEL\n"
                '{"password": "space SYNTHETIC_SECRET_SENTINEL"}\n'
                "\x1b]0;title SYNTHETIC_SECRET_SENTINEL\x07\n"
                "-----BEGIN " + "PRIVATE KEY-----\nSYNTHETIC_SECRET_SENTINEL no end marker"
            ),
        }
    ]
    _claude_write(home, root, "sid-1", [record])
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    out = json.dumps(read)
    assert "SYNTHETIC_SECRET_SENTINEL" not in out
    assert "export alpha" in out


def test_i01_metadata_rows_charged_before_node_preview(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    title = "汉" * 300
    sessions = [(f"s{i}", str(root), 1, 1, title, 1, 0) for i in range(3)]
    nodes = [(f"s{i}", 1, None, _dv_msg("user", "export alpha"), "{}") for i in range(3)]
    _devin_db(home, sessions, nodes)
    import session_adapters

    real_walk = session_adapters.DevinAdapter._walk
    seen = []

    def spy(self, conn, session_id, head, ctx, cap, chat_cap):
        seen.append(ctx.budget.remaining)
        return real_walk(self, conn, session_id, head, ctx, cap, chat_cap)

    import pytest

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(session_adapters.DevinAdapter, "_walk", spy)
        scs.discover(root, home=home)
    assert seen
    assert session_adapters.DISCOVERY_PROVIDER_BYTES - seen[0] >= 2700


def test_i05_codex_thread_rolled_back_clears_prefix(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    records = [
        _cx_user("export alpha"),
        {"type": "event_msg", "payload": {"type": "thread_rolled_back"}},
        _cx_user("active branch"),
    ]
    rollout = _codex_rollout(home, "tid-1", str(root), records)
    _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "x")])
    read = scs.read(root, agent="codex", session_id="tid-1", home=home)
    assert read["complete"] is False
    texts = [m["text"] for m in read["messages"]]
    assert "export alpha" not in texts
    assert "active branch" in texts


def test_i05_codex_second_foreign_meta_and_mixed_roots_refuse(tmp_path):
    root = _init_repo(tmp_path / "proj")
    foreign = _init_repo(tmp_path / "foreign")
    home = tmp_path / "home"
    records = [
        {"type": "session_meta", "payload": {"id": "tid-1", "cwd": str(foreign)}},
        _cx_user("export alpha"),
    ]
    rollout = _codex_rollout(home, "tid-1", str(root), records)
    _codex_db(home, [("tid-1", str(rollout), str(root), "t", "main", "s", "vscode", 0, "x")])
    read = scs.read(root, agent="codex", session_id="tid-1", home=home)
    assert read["status"] != "ok"

    home2 = tmp_path / "home2"
    rollout2 = _codex_rollout(home2, "tid-1", str(root), [_cx_user("export alpha")])
    lines = rollout2.read_text().splitlines()
    meta = json.loads(lines[0])
    meta["payload"]["runtime_workspace_roots"] = [str(root), str(foreign)]
    lines[0] = json.dumps(meta)
    rollout2.write_text("\n".join(lines) + "\n")
    _codex_db(home2, [("tid-1", str(rollout2), str(root), "t", "main", "s", "vscode", 0, "x")])
    read = scs.read(root, agent="codex", session_id="tid-1", home=home2)
    assert read["status"] != "ok"


def test_i08_pi_timestamped_filename_locate(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _pi_write(
        home,
        root,
        "p-old",
        [_pi_msg("e1", None, "user", "export alpha")],
        filename="2026-11-01T08-00-00_p-old.jsonl",
    )
    result = scs.read(root, agent="pi", session_id="p-old", home=home)
    assert result["status"] == "ok"
    assert result["messages"][0]["text"] == "export alpha"


def test_i09_pi_swapped_sampled_header_never_reaches_markers(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    home = tmp_path / "home"
    target = _pi_write(home, root, "p1", [_pi_msg("e1", None, "user", "export alpha")])
    import session_adapters

    real_probe = session_adapters._probe_records

    def swapped(path, ctx):
        records, flags = real_probe(path, ctx)
        if Path(path) == target and records:
            records[0] = dict(records[0], cwd=str(foreign))
        return records, flags

    monkeypatch.setattr(session_adapters, "_probe_records", swapped)
    result = scs.discover(root, home=home)
    assert result["candidates"] == []
    assert "export alpha" not in json.dumps(result)


def test_i06_sanitize_idempotent_and_dot_record_id(tmp_path):
    import session_adapters

    sample = '{"password": "space SYNTHETIC_SECRET_SENTINEL"}'
    once = session_adapters.sanitize_text(sample)
    twice = session_adapters.sanitize_text(once)
    assert once == twice
    assert '""' not in once and "'" not in once
    assert "SYNTHETIC_SECRET_SENTINEL" not in once
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    record = _cl_user(cwd, "sid-1", "..", None, "2026-11-02T10:00:00Z", "export alpha")
    _claude_write(home, root, "sid-1", [record])
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert read["messages"][0]["record_id"].startswith("record-")


def test_i06_strict_caps_and_additive_reasons(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    big = "b" * 9000
    _claude_write(
        home,
        root,
        "sid-1",
        [
            _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", big),
            _cl_assistant(cwd, "sid-1", "u2", "u1", "2026-11-02T10:01:00Z", "alpha next step"),
        ],
    )
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert all(len(m["text"]) <= 6000 for m in read["messages"])
    total = sum(len(m["text"]) for m in read["messages"])
    assert total <= 24000
    assert "text-clipped" in read["truncation_reasons"]


def test_i01b_fallback_rows_charged_before_previews(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    deep = "汉" * 70
    alias_cwds = []
    sessions = []
    nodes = []
    for index in range(3):
        parent = tmp_path / f"a{index}" / deep / deep
        parent.mkdir(parents=True)
        leaf = parent / "link"
        leaf.symlink_to(root)
        alias_cwds.append(str(leaf))
        sessions.append((f"s-a{index}", str(leaf), 1, 1, "汉" * 300, 1, 0))
        nodes.append((f"s-a{index}", 1, None, _dv_msg("user", "export alpha"), "{}"))
    _devin_db(home, sessions, nodes)
    import session_adapters

    real_walk = session_adapters.DevinAdapter._walk
    seen = []

    def spy(self, conn, session_id, head, ctx, cap, chat_cap):
        seen.append(session_adapters.DISCOVERY_PROVIDER_BYTES - ctx.budget.remaining)
        return real_walk(self, conn, session_id, head, ctx, cap, chat_cap)

    import pytest

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(session_adapters.DevinAdapter, "_walk", spy)
        scs.discover(root, home=home)
    assert seen
    expected = sum(len(cwd.encode("utf-8")) for cwd in alias_cwds) + len(("汉" * 300).encode("utf-8"))
    assert seen[0] >= expected


def test_i03_begin_precedes_encoding_and_schema(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sessions = [("s1", str(root), 1, 1, "alpha", 1, 0)]
    nodes = [("s1", 1, None, _dv_msg("user", "export alpha"), "{}")]
    _devin_db(home, sessions, nodes)
    import session_adapters

    order = []
    real_connect = sqlite3.connect

    class Rec:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *a):
            order.append(str(sql).strip().split("(")[0].upper())
            return self._conn.execute(sql, *a)

        def set_progress_handler(self, *a):
            return self._conn.set_progress_handler(*a)

        def rollback(self):
            return self._conn.rollback()

        def close(self):
            return self._conn.close()

    monkeypatch.setattr(session_adapters.sqlite3, "connect", lambda *a, **k: Rec(real_connect(*a, **k)))
    scs.discover(root, home=home)
    assert order.index("BEGIN") < order.index("PRAGMA ENCODING")
    assert order.index("BEGIN") < order.index("PRAGMA TABLE_INFO")


def test_i06_idempotent_all_credential_forms(tmp_path):
    import session_adapters

    for sample in (
        "password=SYNTHETIC_SECRET_SENTINEL",
        '"password": "a\\"b SYNTHETIC_SECRET_SENTINEL"',
        "Authorization: Bearer SYNTHETIC_SECRET_SENTINEL",
        "Authorization: Basic SYNTHETIC_SECRET_SENTINEL",
    ):
        once = session_adapters.sanitize_text(sample)
        twice = session_adapters.sanitize_text(once)
        assert once == twice, sample
        assert "SYNTHETIC_SECRET_SENTINEL" not in once


def test_i06_strict_total_cap_and_additive_reasons(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    records = []
    parent = None
    for index in range(30):
        text = f"export alpha token=SYNTHETIC_SECRET_SENTINEL {'b' * 1500}"
        records.append(_cl_user(cwd, "sid-1", f"u{index}", parent, f"2026-11-02T10:{index % 60:02d}:00Z", text))
        parent = f"u{index}"
    _claude_write(home, root, "sid-1", records)
    read = scs.read(root, agent="claude", session_id="sid-1", home=home, max_messages=100)
    assert len(read["messages"]) == 30
    assert all(len(m["text"]) <= 6000 for m in read["messages"])
    assert sum(len(m["text"]) for m in read["messages"]) <= 24000
    assert "SYNTHETIC_SECRET_SENTINEL" not in json.dumps(read)
    limited = scs.read(root, agent="claude", session_id="sid-1", home=home, max_messages=2)
    assert "message-limit" in limited["truncation_reasons"]


def test_privacy_whitelist_and_bounded_anchors(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    record = _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", "export alpha")
    record["gitBranch"] = "/abs/path token=SYNTHETIC_SECRET_SENTINEL"
    _claude_write(home, root, "sid-1", [record])
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert read["candidate"]["branch"] is None
    assert "SYNTHETIC_SECRET_SENTINEL" not in json.dumps(read)
    assert "/abs/path" not in json.dumps(read)
    result = scs.discover(root, home=home)
    leaked = [
        k
        for c in result["candidates"]
        for k in c
        if k.startswith("_")
        or k
        not in {
            "agent",
            "session_id",
            "title",
            "updated_at",
            "branch",
            "head",
            "relation",
            "has_user",
            "complete",
            "project_path",
            "association",
            "association_confidence",
            "source_format",
            "omissions",
        }
    ]
    assert leaked == []
    assert str(home) not in json.dumps(result)


def test_store_paths_masked_in_exports(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    store_path = str(home / ".codex" / "state_5.sqlite")
    record = _cl_user(cwd, "sid-1", "u1", None, "2026-11-02T10:00:00Z", f"export alpha {store_path}")
    _claude_write(home, root, "sid-1", [record])
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert store_path not in json.dumps(read)
    assert "[LOCAL_STORE]" in json.dumps(read)

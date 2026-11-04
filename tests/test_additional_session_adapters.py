import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import session_adapters  # noqa: E402
import session_context_support as scs  # noqa: E402

_GIT_ENV = {
    **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


def _git(cwd, *args):
    env = {**_GIT_ENV, "HOME": str(Path(cwd).parent)}
    return subprocess.run(["git", "-C", str(cwd), *args], env=env, check=True, capture_output=True)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    (path / "f").write_text("x")
    _git(path, "add", "f")
    _git(path, "commit", "-qm", "i")
    return path.resolve()


def _opencode_db(home: Path, sessions=(), messages=(), parts=(), legacy=False):
    root = home / ".local" / "share" / "opencode"
    root.mkdir(parents=True, exist_ok=True)
    db = root / "opencode.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE session_v2(id,directory,title,version,time_created,time_updated,parent_id,fork_session_id,revert)"
    )
    conn.execute("CREATE TABLE session_message(id,session_id,type,seq,data)")
    conn.execute("CREATE TABLE session_part(session_id,message_id,ordinal,type,data)")
    if legacy:
        conn.execute("CREATE TABLE session(id)")
    for row in sessions:
        conn.execute("INSERT INTO session_v2 VALUES(?,?,?,?,?,?,?,?,?)", row)
    for row in messages:
        conn.execute("INSERT INTO session_message VALUES(?,?,?,?,?)", row)
    for row in parts:
        conn.execute("INSERT INTO session_part VALUES(?,?,?,?,?)", row)
    conn.commit()
    conn.close()
    return db


def _oc_session(sid, cwd, title="t", fork=None, revert=None):
    return (sid, str(cwd), title, "v", "2026-11-02T10:00:00Z", "2026-11-02T10:00:00Z", None, fork, revert)


def _oc_user(msg_id, sid, seq, text):
    return (msg_id, sid, "user", seq, json.dumps({"text": text}))


def _oc_asst(msg_id, sid, seq):
    return (msg_id, sid, "assistant", seq, None)


def _oc_part(sid, msg_id, ordinal, text):
    return (sid, msg_id, ordinal, "text", json.dumps({"text": text}))


def test_opencode_scoped_user_and_assistant_parts(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _opencode_db(
        home,
        [_oc_session("s1", root, "alpha work")],
        [
            _oc_user("m1", "s1", 1, "export alpha"),
            _oc_asst("m2", "s1", 2),
            _oc_user("m3", "s1", 3, "SYNTHETIC_SECRET_SENTINEL_REASONING"),
        ],
        [
            _oc_part("s1", "m2", 0, "alpha next step"),
            ("s1", "m2", 1, "reasoning", json.dumps({"text": "hidden tool"})),
        ],
    )
    result = scs.discover(root, home=home)
    cand = [c for c in result["candidates"] if c["agent"] == "opencode"]
    assert len(cand) == 1
    read = scs.read(root, agent="opencode", session_id="s1", home=home)
    texts = [m["text"] for m in read["messages"]]
    assert texts == ["export alpha", "alpha next step", "SYNTHETIC_SECRET_SENTINEL_REASONING"]
    assert "hidden tool" not in json.dumps(read)


def test_opencode_fork_revert_location_block(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _opencode_db(
        home,
        [
            _oc_session("s-fork", root, "f", fork="parent-1"),
            _oc_session("s-revert", root, "r", revert="x"),
        ],
        [_oc_user("m1", "s-fork", 1, "export alpha"), _oc_user("m2", "s-revert", 1, "export alpha")],
        [],
    )
    result = scs.discover(root, home=home)
    cands = {c["session_id"]: c for c in result["candidates"] if c["agent"] == "opencode"}
    assert cands["s-fork"]["complete"] is False and cands["s-fork"]["has_user"] is None
    read = scs.read(root, agent="opencode", session_id="s-fork", home=home)
    assert read["status"] != "ok"
    assert "export alpha" not in json.dumps(read)


def test_opencode_compaction_shapes(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _opencode_db(
        home,
        [_oc_session("s1", root)],
        [
            _oc_user("m1", "s1", 1, "export alpha"),
            ("mc", "s1", "compaction", 2, json.dumps({"status": "completed", "summary": "the goal was alpha"})),
            _oc_user("m3", "s1", 3, "active branch"),
        ],
        [],
    )
    read = scs.read(root, agent="opencode", session_id="s1", home=home)
    texts = [m["text"] for m in read["messages"]]
    assert "export alpha" not in texts
    assert "the goal was alpha" in texts
    assert "active branch" in texts
    home2 = tmp_path / "home2"
    _opencode_db(
        home2,
        [_oc_session("s1", root)],
        [
            _oc_user("m1", "s1", 1, "export alpha"),
            ("mc", "s1", "compaction", 2, json.dumps({"status": "completed", "summary": "s", "messages": []})),
        ],
        [],
    )
    read2 = scs.read(root, agent="opencode", session_id="s1", home=home2)
    assert read2["complete"] is False
    assert "export alpha" not in [m["text"] for m in read2["messages"]]
    home3 = tmp_path / "home3"
    _opencode_db(
        home3,
        [_oc_session("s1", root)],
        [
            _oc_user("m1", "s1", 1, "export alpha"),
            ("mc", "s1", "compaction", 2, json.dumps({"status": "failed"})),
        ],
        [],
    )
    read3 = scs.read(root, agent="opencode", session_id="s1", home=home3)
    assert "export alpha" in [m["text"] for m in read3["messages"]]


def test_opencode_legacy_and_unknown_schema(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _opencode_db(home, [_oc_session("s1", root)], [_oc_user("m1", "s1", 1, "export alpha")], [], legacy=True)
    result = scs.discover(root, home=home)
    oc = [s for s in result["sources"] if s["agent"] == "opencode"][0]
    assert oc["complete"] is False
    home2 = tmp_path / "home2"
    db = _opencode_db(home2)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE session_v2")
    conn.execute("CREATE TABLE session_v2(id,title)")
    conn.commit()
    conn.close()
    result2 = scs.discover(root, home=home2)
    oc2 = [s for s in result2["sources"] if s["agent"] == "opencode"][0]
    assert oc2["status"] == "unsupported"


def _gemini_bucket(home: Path, cwd: Path, bucket_name: str, records, filename="session-abc12345.jsonl"):
    bucket = home / ".gemini" / "tmp" / bucket_name / "chats"
    bucket.mkdir(parents=True)
    if bucket_name != "hashonly":
        (bucket.parent / ".project_root").write_text(str(cwd))
    path = bucket / filename
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def _gem_meta(sid, cwd, extra=None):
    record = {"sessionId": sid, "projectHash": None, "kind": "main"}
    import hashlib

    record["projectHash"] = hashlib.sha256(str(cwd).encode("utf-8")).hexdigest()
    if extra:
        record.update(extra)
    return record


def _gem_msg(mid, text, kind="user"):
    return {"id": mid, "type": kind, "timestamp": "2026-11-02T10:00:00Z", "content": text}


def test_gemini_marker_bucket_and_basic_projection(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _gemini_bucket(
        home,
        root,
        "any-slug",
        [_gem_meta("s1", root), _gem_msg("e1", "export alpha"), _gem_msg("e2", "alpha next step", "gemini")],
    )
    result = scs.discover(root, home=home)
    cands = [c for c in result["candidates"] if c["agent"] == "gemini"]
    assert len(cands) == 1
    read = scs.read(root, agent="gemini", session_id="s1", home=home)
    assert [m["text"] for m in read["messages"]] == ["export alpha", "alpha next step"]
    assert "thought" not in json.dumps(read)


def test_gemini_patch_and_rewind(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _gemini_bucket(
        home,
        root,
        "b1",
        [
            _gem_meta("s1", root),
            _gem_msg("e1", "export alpha"),
            _gem_msg("e2", "stale"),
            {"$patch": {"id": "e2", "content": "alpha next step"}},
        ],
    )
    read = scs.read(root, agent="gemini", session_id="s1", home=home)
    assert [m["text"] for m in read["messages"]] == ["export alpha", "alpha next step"]
    home2 = tmp_path / "home2"
    _gemini_bucket(
        home2,
        root,
        "b1",
        [
            _gem_meta("s1", root),
            _gem_msg("e1", "export alpha"),
            _gem_msg("e2", "newer"),
            {"$rewindTo": "e2"},
        ],
    )
    read2 = scs.read(root, agent="gemini", session_id="s1", home=home2)
    assert [m["text"] for m in read2["messages"]] == ["export alpha"]


def test_gemini_foreign_bucket_no_text(tmp_path):
    root = _init_repo(tmp_path / "proj")
    foreign = _init_repo(tmp_path / "foreign")
    home = tmp_path / "home"
    _gemini_bucket(
        home,
        foreign,
        "any-slug",
        [_gem_meta("s1", foreign), _gem_msg("e1", "SYNTHETIC_SECRET_SENTINEL")],
    )
    result = scs.discover(root, home=home)
    assert [c for c in result["candidates"] if c["agent"] == "gemini"] == []
    assert "SYNTHETIC_SECRET_SENTINEL" not in json.dumps(result)


def _copilot_events(home: Path, sid: str, records):
    directory = home / ".copilot" / "session-state" / sid
    directory.mkdir(parents=True)
    path = directory / "events.jsonl"
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def _cp(kind, data, eid=None, parent=None, ts="2026-11-02T10:00:00Z", **extra):
    if kind == "session.start":
        data = {"version": 1, **data}
    record = {"type": kind, "data": data, "timestamp": ts}
    if eid:
        record["id"] = eid
        record["parentId"] = parent
    record.update(extra)
    return record


def test_copilot_main_chain_projection(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    sid = "cop-1"
    _copilot_events(
        home,
        sid,
        [
            _cp("session.start", {"sessionId": sid, "version": 1, "context": {"cwd": str(root)}}, "e0"),
            _cp("user.message", {"content": "export alpha"}, "e1", "e0"),
            _cp("assistant.message", {"content": "alpha next step"}, "e2", "e1"),
            _cp("assistant.message", {"content": "worker hidden"}, "e3", "e2", agentId="w1"),
        ],
    )
    result = scs.discover(root, home=home)
    cands = [c for c in result["candidates"] if c["agent"] == "copilot"]
    assert len(cands) == 1
    read = scs.read(root, agent="copilot", session_id=sid, home=home)
    assert [m["text"] for m in read["messages"]] == ["export alpha", "alpha next step"]
    assert read["complete"] is False
    assert "worker hidden" not in json.dumps(read)


def test_copilot_foreign_cwd_and_compaction(tmp_path):
    root = _init_repo(tmp_path / "proj")
    foreign = _init_repo(tmp_path / "foreign")
    home = tmp_path / "home"
    _copilot_events(
        home,
        "f-1",
        [
            _cp("session.start", {"sessionId": "f-1", "version": 1, "context": {"cwd": str(foreign)}}, "e0"),
            _cp("user.message", {"content": "SYNTHETIC_SECRET_SENTINEL"}, "e1", "e0"),
        ],
    )
    result = scs.discover(root, home=home)
    assert [c for c in result["candidates"] if c["agent"] == "copilot"] == []
    home2 = tmp_path / "home2"
    _copilot_events(
        home2,
        "c-1",
        [
            _cp("session.start", {"sessionId": "c-1", "version": 1, "context": {"cwd": str(root)}}, "e0"),
            _cp("user.message", {"content": "export alpha"}, "e1", "e0"),
            _cp("session.compaction_complete", {"success": True, "summaryContent": "goal alpha"}, "e2", "e1"),
            _cp("user.message", {"content": "active branch"}, "e3", "e2"),
        ],
    )
    read = scs.read(root, agent="copilot", session_id="c-1", home=home2)
    assert read["complete"] is False
    texts = [m["text"] for m in read["messages"]]
    assert "export alpha" not in texts
    assert "goal alpha" in texts and "active branch" in texts


def test_r05_six_foreign_pi_files_exhaust_probe(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    home = tmp_path / "home"
    bucket = home / ".pi" / "agent" / "sessions" / "--odd--"
    bucket.mkdir(parents=True)
    header_local = {"type": "session", "version": 3, "id": "p1", "timestamp": "2026-11-02T09:00:00Z", "cwd": str(root)}
    with open(bucket / "p1.jsonl", "w", encoding="utf-8") as handle:
        handle.write(json.dumps(header_local) + "\n")
        handle.write(
            json.dumps(
                {
                    "type": "message",
                    "id": "e1",
                    "parentId": None,
                    "timestamp": "t",
                    "message": {"role": "user", "content": [{"type": "text", "text": "export alpha"}]},
                }
            )
            + "\n"
        )
    order = []
    for index in range(6):
        header = {"type": "session", "version": 3, "id": f"big-{index}", "timestamp": "t", "cwd": str(foreign)}
        with open(bucket / f"big-{index}.jsonl", "w", encoding="utf-8") as handle:
            handle.write(json.dumps(header) + "\n")
            handle.write(
                json.dumps(
                    {
                        "type": "message",
                        "id": "e1",
                        "parentId": None,
                        "timestamp": "t",
                        "message": {"role": "user", "content": [{"type": "text", "text": "x" * 350000}]},
                    }
                )
                + "\n"
            )
        os.utime(bucket / f"big-{index}.jsonl", (3000000000, 3000000000))
    os.utime(bucket / "p1.jsonl", (1000000000, 1000000000))
    real = session_adapters._first_json_record

    def spy(path, ctx):
        order.append(Path(path).name)
        return real(path, ctx)

    monkeypatch.setattr(session_adapters, "_first_json_record", spy)
    result = scs.discover(root, home=home)
    foreign_read = [n for n in order if n.startswith("big-")]
    assert len(foreign_read) == 6
    assert "p1" in {c["session_id"] for c in result["candidates"]}
    assert order.index("p1.jsonl") > max(order.index(n) for n in foreign_read)


def test_a_opencode_native_messages_replacement_partial(tmp_path):
    root = _init_repo(tmp_path / "proj")
    for label, data in (
        ("completed-native", {"status": "completed", "summary": "STALE_SUMMARY", "native": {"messages": []}}),
        ("null-native", {"status": "completed", "summary": "STALE_SUMMARY", "native": {"messages": None}}),
    ):
        home = tmp_path / f"home-{label}"
        _opencode_db(
            home,
            [_oc_session("s1", root)],
            [
                _oc_user("m1", "s1", 1, "export alpha"),
                ("mc", "s1", "compaction", 2, json.dumps(data)),
                _oc_user("m3", "s1", 3, "active branch"),
            ],
            [],
        )
        read = scs.read(root, agent="opencode", session_id="s1", home=home)
        assert read["complete"] is False, label
        assert "native-replacement-unverified" in read["truncation_reasons"], label
        texts = [m["text"] for m in read["messages"]]
        assert "export alpha" not in texts and "STALE_SUMMARY" not in texts, label
        assert "active branch" in texts, label
    home = tmp_path / "home-unknown"
    _opencode_db(
        home,
        [_oc_session("s1", root)],
        [
            _oc_user("m1", "s1", 1, "export alpha"),
            ("mu", "s1", "some-unknown-control", 2, json.dumps({"x": 1})),
            _oc_user("m3", "s1", 3, "active branch"),
        ],
        [],
    )
    read = scs.read(root, agent="opencode", session_id="s1", home=home)
    assert read["complete"] is False
    assert "unhandled-record" in read["truncation_reasons"]
    assert "export alpha" not in [m["text"] for m in read["messages"]]


def test_b_opencode_duplicate_cursor_and_caps(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    db = _opencode_db(home, [_oc_session("s1", root)], [_oc_user("m1", "s1", 1, "export alpha")], [])
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO session_message VALUES(?,?,?,?,?)", _oc_user("m2", "s1", 1, "rewrite"))
    conn.execute("INSERT INTO session_message VALUES(?,?,?,?,?)", _oc_asst("m3", "s1", 2))
    conn.commit()
    conn.close()
    read = scs.read(root, agent="opencode", session_id="s1", home=home)
    assert read["complete"] is False
    assert "duplicate-cursor" in read["truncation_reasons"]
    assert "export alpha" not in json.dumps(read)
    assert "rewrite" not in json.dumps(read)
    home2 = tmp_path / "home2"
    db2 = _opencode_db(home2, [_oc_session("s1", root)], [_oc_asst("m3", "s1", 1)], [])
    conn = sqlite3.connect(db2)
    conn.execute(
        "INSERT INTO session_part VALUES(?,?,?,?,?)",
        _oc_part("s1", "m3", 0, "alpha next step"),
    )
    conn.execute(
        "INSERT INTO session_part VALUES(?,?,?,?,?)",
        _oc_part("s1", "m3", 0, "duplicate ordinal"),
    )
    conn.execute(
        "INSERT INTO session_part VALUES(?,?,?,?,?)", ("s1", "m3", 1, "reasoning", json.dumps({"text": "hidden"}))
    )
    conn.commit()
    conn.close()
    read2 = scs.read(root, agent="opencode", session_id="s1", home=home2)
    assert read2["complete"] is False
    assert "duplicate-cursor" in read2["truncation_reasons"]
    assert "hidden" not in json.dumps(read2)


def test_b_opencode_fork_revert_location_read_reasons(tmp_path):
    root = _init_repo(tmp_path / "proj")
    for label, row, expected in (
        ("fork", _oc_session("s1", root, fork="p"), "fork-context-unverified"),
        ("revert", _oc_session("s1", root, revert="x"), "revert-context-unverified"),
    ):
        home = tmp_path / f"home-{label}"
        _opencode_db(home, [row], [_oc_user("m1", "s1", 1, "export alpha")], [])
        read = scs.read(root, agent="opencode", session_id="s1", home=home)
        assert read["status"] != "ok", label
        assert read["error"]["reason_code"] == expected, label
    home = tmp_path / "home-loc"
    _opencode_db(home, [_oc_session("s1", root)], [_oc_user("m1", "s1", 1, "export alpha")], [])
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO session_message VALUES(?,?,?,?,?)", ("ml", "s1", "location-switched", 9, None))
    conn.commit()
    conn.close()
    read = scs.read(root, agent="opencode", session_id="s1", home=home)
    assert read["error"]["reason_code"] == "location-context-unverified"


def test_c_gemini_part_list_union_and_patch(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _gemini_bucket(
        home,
        root,
        "b1",
        [
            _gem_meta("s1", root),
            {
                "id": "e1",
                "type": "user",
                "content": [{"text": "THOUGHT_SENTINEL", "thought": True}, {"text": "export alpha"}],
            },
            {"id": "e2", "type": "gemini", "content": [{"functionCall": {"name": "x"}}, {"text": "alpha next step"}]},
            {"id": "e3", "type": "gemini", "displayContent": 123, "content": "hidden expanded"},
        ],
    )
    read = scs.read(root, agent="gemini", session_id="s1", home=home)
    texts = [m["text"] for m in read["messages"]]
    assert "THOUGHT_SENTINEL" not in json.dumps(read)
    assert "alpha next step" in texts
    assert "hidden expanded" not in json.dumps(read)
    home2 = tmp_path / "home2"
    _gemini_bucket(
        home2,
        root,
        "b1",
        [
            _gem_meta("s1", root),
            {"id": "e1", "type": "user", "content": "export alpha", "displayContent": "stale display"},
            {"$patch": {"updates": [{"id": "e1", "content": "fresh content"}]}},
        ],
    )
    read2 = scs.read(root, agent="gemini", session_id="s1", home=home2)
    assert [m["text"] for m in read2["messages"]] == ["fresh content"]
    home3 = tmp_path / "home3"
    _gemini_bucket(
        home3,
        root,
        "b1",
        [
            _gem_meta("s1", root),
            _gem_msg("e1", "one"),
            _gem_msg("e2", "two"),
            _gem_msg("e3", "three"),
            {"$patch": {"orderIds": ["e3"], "removeIds": ["e1"]}},
        ],
    )
    read3 = scs.read(root, agent="gemini", session_id="s1", home=home3)
    assert [m["text"] for m in read3["messages"]] == ["two", "three"]


def test_d_gemini_identity_controls(tmp_path):
    root = _init_repo(tmp_path / "proj")
    foreign = _init_repo(tmp_path / "foreign")
    home = tmp_path / "home"
    import hashlib

    foreign_hash = hashlib.sha256(str(foreign).encode("utf-8")).hexdigest()
    root_hash = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    _gemini_bucket(
        home,
        root,
        "b1",
        [
            _gem_meta("s1", root),
            _gem_msg("e1", "export alpha"),
            {"$set": {"projectHash": foreign_hash}},
            {"$set": {"projectHash": root_hash}},
            _gem_msg("e2", "active branch"),
        ],
    )
    read = scs.read(root, agent="gemini", session_id="s1", home=home)
    assert read["status"] != "ok"
    assert not read["messages"]
    home2 = tmp_path / "home2"
    _gemini_bucket(
        home2,
        root,
        "b1",
        [
            _gem_meta("s1", root),
            _gem_msg("e1", "export alpha"),
            {"$set": {"messages": [_gem_msg("n1", "replaced")]}},
        ],
    )
    read2 = scs.read(root, agent="gemini", session_id="s1", home=home2)
    assert [m["text"] for m in read2["messages"]] == ["replaced"]
    home3 = tmp_path / "home3"
    _gemini_bucket(
        home3,
        root,
        "b1",
        [_gem_meta("s1", root), _gem_msg("e1", "export alpha"), {"$set": {"messages": "nope"}}],
    )
    read3 = scs.read(root, agent="gemini", session_id="s1", home=home3)
    assert read3["complete"] is False
    assert "export alpha" not in [m["text"] for m in read3["messages"]]
    home4 = tmp_path / "home4"
    _gemini_bucket(
        home4,
        root,
        "b1",
        [
            _gem_meta("s1", root, {"kind": "subagent"}),
            _gem_msg("e1", "worker hidden"),
        ],
    )
    result = scs.discover(root, home=home4)
    assert [c for c in result["candidates"] if c["agent"] == "gemini"] == []


def test_d_gemini_resume_id_and_duplicate_conflict(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _gemini_bucket(
        home,
        root,
        "b1",
        [_gem_meta("old-id", root), {"$set": {"sessionId": "new-sid"}}, _gem_msg("e1", "export alpha")],
        filename="session-oldid12.jsonl",
    )
    read = scs.read(root, agent="gemini", session_id="new-sid", home=home)
    assert read["status"] == "ok"
    assert [message["text"] for message in read["messages"]] == ["export alpha"]
    assert scs.read(root, agent="gemini", session_id="old-id", home=home)["status"] != "ok"
    home2 = tmp_path / "home2"
    _gemini_bucket(home2, root, "b1", [_gem_meta("s1", root), _gem_msg("e1", "one")])
    _gemini_bucket(home2 / ".cache" / "", root, "b2", [_gem_meta("s1", root), _gem_msg("e1", "two")])
    result = scs.discover(root, home=home2)
    gem_src = [s for s in result["sources"] if s["agent"] == "gemini"][0]
    assert gem_src["complete"] is False
    duplicate = scs.read(root, agent="gemini", session_id="s1", home=home2)
    assert duplicate["error"]["reason_code"] == "duplicate-session-conflict"
    assert not duplicate["messages"]


def test_e_copilot_identity_strict(tmp_path):
    root = _init_repo(tmp_path / "proj")
    foreign = _init_repo(tmp_path / "foreign")
    home = tmp_path / "home"
    _copilot_events(
        home,
        "s1",
        [
            _cp("session.start", {"sessionId": "s1", "version": 1, "context": {"cwd": str(foreign)}}, "e0"),
            _cp("session.start", {"sessionId": "s1", "version": 1, "context": {"cwd": str(root)}}, "e1", "e0"),
            _cp("user.message", {"content": "export alpha"}, "e2", "e1"),
        ],
    )
    result = scs.discover(root, home=home)
    assert [c for c in result["candidates"] if c["agent"] == "copilot"] == []
    home2 = tmp_path / "home2"
    _copilot_events(
        home2,
        "s1",
        [
            _cp(
                "session.start",
                {"sessionId": "s1", "context": {"cwd": str(root)}, "version": False},
                "e0",
            ),
            _cp("user.message", {"content": "export alpha"}, "e1", "e0"),
        ],
    )
    result2 = scs.discover(root, home=home2)
    cands = [c for c in result2["candidates"] if c["agent"] == "copilot"]
    assert not cands
    read = scs.read(root, agent="copilot", session_id="s1", home=home2)
    assert read["status"] != "ok"
    assert not read["messages"]


def test_f_copilot_workers_and_barriers(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _copilot_events(
        home,
        "s1",
        [
            _cp("session.start", {"sessionId": "s1", "version": 1, "context": {"cwd": str(root)}}, "e0"),
            _cp("user.message", {"content": "export alpha"}, "e1", "e0"),
            _cp("session.snapshot_rewind", {}, "e2", "e1", ephemeral=True),
            _cp("user.message", {"content": "stale goal"}, "e3", "e2"),
        ],
    )
    read = scs.read(root, agent="copilot", session_id="s1", home=home)
    assert read["complete"] is False
    texts = [m["text"] for m in read["messages"]]
    assert "export alpha" not in texts
    home2 = tmp_path / "home2"
    _copilot_events(
        home2,
        "s1",
        [
            _cp("session.start", {"sessionId": "s1", "version": 1, "context": {"cwd": str(root)}}, "e0"),
            _cp("user.message", {"content": "export alpha"}, "e1", "e0"),
            _cp("session.context_cleared", {"initialMessage": {"content": "generated"}}, "e2", "e1"),
            _cp("user.message", {"content": "active branch"}, "e3", "e2"),
        ],
    )
    read2 = scs.read(root, agent="copilot", session_id="s1", home=home2)
    assert [m["text"] for m in read2["messages"]] == ["active branch"]
    assert "generated" not in json.dumps(read2)


def test_g_pi_old_id_amid_many_files_no_unrelated_probes(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    enc = "".join(c if c.isalnum() or c in "_-" else "-" for c in str(root))
    bucket = home / ".pi" / "agent" / "sessions" / f"--{enc}--"
    bucket.mkdir(parents=True)
    header = {"type": "session", "version": 3, "id": "p-old", "timestamp": "t", "cwd": str(root)}
    target = bucket / "2026-01-01T00-00-00_p-old.jsonl"
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(header) + "\n")
        handle.write(
            json.dumps(
                {
                    "type": "message",
                    "id": "e1",
                    "parentId": None,
                    "timestamp": "t",
                    "message": {"role": "user", "content": [{"type": "text", "text": "export alpha"}]},
                }
            )
            + "\n"
        )
    for index in range(100):
        with open(bucket / f"other-{index}.jsonl", "w", encoding="utf-8") as handle:
            handle.write(json.dumps(header) + "\n")
    import session_adapters

    real_probe = session_adapters._probe_records
    real_first = session_adapters._first_json_record

    def no_other_probe(path, ctx):
        assert Path(path) == target, f"unrelated probe {path}"
        return real_probe(path, ctx)

    def no_other_first(path, ctx):
        assert Path(path) == target, f"unrelated first {path}"
        return real_first(path, ctx)

    monkeypatch.setattr(
        session_adapters.PiAdapter, "discover", lambda *a, **k: (_ for _ in ()).throw(AssertionError("discover called"))
    )
    monkeypatch.setattr(session_adapters, "_probe_records", no_other_probe)
    monkeypatch.setattr(session_adapters, "_first_json_record", no_other_first)
    read = scs.read(root, agent="pi", session_id="p-old", home=home)
    assert read["status"] == "ok"
    assert read["messages"][0]["text"] == "export alpha"


def test_g_codex_malformed_turn_context_refused(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"

    def _cx_user(text):
        return {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        }

    for label, payload in (
        ("valid", {"cwd": str(root)}),
        ("primitive", "not-a-dict"),
        ("badcwd", {"cwd": 42}),
        ("nullcwd", {"cwd": None}),
    ):
        home = tmp_path / f"home-{label}"
        store = home / ".codex" / "sessions" / "2026" / "11" / "02"
        store.mkdir(parents=True)
        path = store / "rollout-tid-1.jsonl"
        records = [
            {"type": "session_meta", "payload": {"id": "tid-1", "cwd": str(root)}},
            {"type": "turn_context", "payload": payload},
            _cx_user("export alpha"),
        ]
        with open(path, "w") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        db = home / ".codex" / "state_5.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE threads(id TEXT, rollout_path TEXT, cwd TEXT, title TEXT, "
            "git_branch TEXT, git_sha TEXT, source TEXT, archived INTEGER, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO threads VALUES('tid-1',?,?,?,?,?,?,?,?)",
            (str(path), str(root), "t", "main", "a" * 40, "vscode", 0, "2026-11-02T10:00:00Z"),
        )
        conn.commit()
        conn.close()
        result = scs.discover(root, home=home)
        read = scs.read(root, agent="codex", session_id="tid-1", home=home)
        candidates = [c for c in result["candidates"] if c["agent"] == "codex"]
        if label == "valid":
            assert [candidate["session_id"] for candidate in candidates] == ["tid-1"]
            assert read["status"] == "ok"
            assert [message["text"] for message in read["messages"]] == ["export alpha"]
        else:
            assert not candidates, label
            assert read["status"] != "ok", label
            assert not read["messages"], label


def test_g_metadata_omissions_propagate(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    record = {
        "type": "user",
        "cwd": cwd,
        "sessionId": "sid-1",
        "uuid": "u1",
        "timestamp": "2026-11-02T10:00:00Z",
        "gitBranch": " " + "x" * 300,
        "native_path": str(home / ".claude" / "projects"),
        "message": {"role": "user", "content": [{"type": "text", "text": "export alpha"}]},
    }
    enc = "".join(c if c.isalnum() or c in "_-" else "-" for c in cwd)
    bucket = home / ".claude" / "projects" / enc
    bucket.mkdir(parents=True)
    with open(bucket / "sid-1.jsonl", "w") as handle:
        handle.write(json.dumps(record) + "\n")
    result = scs.discover(root, home=home)
    cand = [c for c in result["candidates"] if c["agent"] == "claude"][0]
    assert "native_path" not in cand
    read = scs.read(root, agent="claude", session_id="sid-1", home=home)
    assert read["candidate"]["branch"] is None
    assert result["complete"] is False
    assert result["status"] == "partial"
    assert "branch-withheld" in (read["candidate"].get("omissions") or [])
    assert read["complete"] is False
    assert "metadata-omitted" in read["truncation_reasons"]
    assert read["truncation"] == "metadata-omitted"
    assert " " + "x" * 300 not in json.dumps(read)
    assert str(home / ".claude") not in json.dumps(read)


def test_g_compound_caps_both_reasons(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    cwd = str(root)
    records = []
    parent = None
    for index in range(3):
        records.append(
            {
                "type": "user",
                "cwd": cwd,
                "sessionId": "sid-1",
                "uuid": f"u{index}",
                "parentUuid": parent,
                "timestamp": f"2026-11-02T10:0{index}:00Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "b" * 8000}]},
            }
        )
        parent = f"u{index}"
    enc = "".join(c if c.isalnum() or c in "_-" else "-" for c in cwd)
    bucket = home / ".claude" / "projects" / enc
    bucket.mkdir(parents=True)
    with open(bucket / "sid-1.jsonl", "w") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    read = scs.read(root, agent="claude", session_id="sid-1", home=home, max_messages=2)
    assert "message-limit" in read["truncation_reasons"]
    assert "text-clipped" in read["truncation_reasons"]
    assert all(len(m["text"]) <= 6000 for m in read["messages"])


def test_h_r05_six_foreign_before_local_and_spy(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    home = tmp_path / "home"
    bucket = home / ".pi" / "agent" / "sessions" / "--odd--"
    bucket.mkdir(parents=True)
    header_local = {"type": "session", "version": 3, "id": "p1", "timestamp": "t", "cwd": str(root)}
    with open(bucket / "p1.jsonl", "w", encoding="utf-8") as handle:
        handle.write(json.dumps(header_local) + "\n")
        handle.write(
            json.dumps(
                {
                    "type": "message",
                    "id": "e1",
                    "parentId": None,
                    "timestamp": "t",
                    "message": {"role": "user", "content": [{"type": "text", "text": "export alpha"}]},
                }
            )
            + "\n"
        )
    order = []
    for index in range(6):
        header = {"type": "session", "version": 3, "id": f"big-{index}", "timestamp": "t", "cwd": str(foreign)}
        with open(bucket / f"big-{index}.jsonl", "w", encoding="utf-8") as handle:
            handle.write(json.dumps(header) + "\n")
            handle.write(
                json.dumps(
                    {
                        "type": "message",
                        "id": "e1",
                        "parentId": None,
                        "timestamp": "t",
                        "message": {"role": "user", "content": [{"type": "text", "text": "x" * 350000}]},
                    }
                )
                + "\n"
            )
        os.utime(bucket / f"big-{index}.jsonl", (3000000000, 3000000000))
    os.utime(bucket / "p1.jsonl", (1000000000, 1000000000))
    real_first = session_adapters._first_json_record
    real_probe = session_adapters._probe_records

    def spy_first(path, ctx):
        order.append(Path(path).name)
        return real_first(path, ctx)

    def no_foreign_body(path, ctx):
        assert not Path(path).name.startswith("big-"), f"foreign body probed {path}"
        return real_probe(path, ctx)

    monkeypatch.setattr(session_adapters, "_first_json_record", spy_first)
    monkeypatch.setattr(session_adapters, "_probe_records", no_foreign_body)
    result = scs.discover(root, home=home)
    foreign_seen = [n for n in order if n.startswith("big-")]
    assert len(foreign_seen) == 6
    assert "p1" in {c["session_id"] for c in result["candidates"]}
    assert order.index("p1.jsonl") > max(order.index(n) for n in foreign_seen)
    read = scs.read(root, agent="pi", session_id="p1", home=home)
    assert read["status"] == "ok"
    assert read["messages"][0]["text"] == "export alpha"


def test_gemini_all_scope_updates_preflight_before_export(tmp_path):
    root = _init_repo(tmp_path / "proj")
    foreign = _init_repo(tmp_path / "foreign")
    for label, changes in (
        ("directories", [{"directories": [str(foreign)]}]),
        ("set-directories", [{"$set": {"directories": [str(foreign)]}}]),
        ("bad-directories", [{"$set": {"directories": [42]}}]),
        ("primitive-directories", [{"directories": "not-a-list"}]),
        ("worker-then-main", [{"$set": {"kind": "subagent"}}, {"$set": {"kind": "main"}}]),
    ):
        home = tmp_path / label
        _gemini_bucket(home, root, "b1", [_gem_meta("s1", root), _gem_msg("e1", "FOREIGN_SENTINEL"), *changes])
        read = scs.read(root, agent="gemini", session_id="s1", home=home)
        assert read["status"] != "ok", label
        assert "FOREIGN_SENTINEL" not in json.dumps(read), label


def test_gemini_malformed_patch_ids_and_unknown_controls_are_barriers(tmp_path):
    root = _init_repo(tmp_path / "proj")
    for index, edit in enumerate(
        (
            {"$patch": {"id": []}},
            {"$patch": {"updates": [{"id": {}}]}},
            {"$patch": {"orderIds": [{}]}},
            {"$patch": {"removeIds": [None]}},
            {"$unknown_rewrite": True},
        )
    ):
        home = tmp_path / f"home-{index}"
        _gemini_bucket(
            home,
            root,
            "b1",
            [_gem_meta("s1", root), _gem_msg("e1", "old goal"), edit, _gem_msg("e2", "new goal")],
        )
        read = scs.read(root, agent="gemini", session_id="s1", home=home)
        assert read["status"] == "ok", index
        assert read["complete"] is False, index
        assert [message["text"] for message in read["messages"]] == ["new goal"], index


def test_gemini_override_parents_both_stores(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    home.mkdir()
    parent = tmp_path / "override"
    _gemini_bucket(parent, root, "standard", [_gem_meta("s1", root), _gem_msg("e1", "one")])
    _gemini_bucket(parent / ".cache", root, "cached", [_gem_meta("s2", root), _gem_msg("e2", "two")])
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(parent))
    for name in ("XDG_DATA_HOME", "PI_CODING_AGENT_DIR", "COPILOT_HOME"):
        monkeypatch.delenv(name, raising=False)
    discovered = scs.discover(root, client="gemini")
    assert {c["session_id"] for c in discovered["candidates"] if c["agent"] == "gemini"} == {"s1", "s2"}
    for sid, expected in (("s1", "one"), ("s2", "two")):
        read = scs.read(root, agent="gemini", session_id=sid)
        assert [message["text"] for message in read["messages"]] == [expected]
    assert not scs.discover(root, home=home)["candidates"]


def test_gemini_unsafe_marker_and_marker_swap_do_not_admit_body(tmp_path, monkeypatch):
    root = _init_repo(tmp_path / "proj")
    foreign = _init_repo(tmp_path / "foreign")
    kinds = ["symlink", "broken", "oversized"]
    if hasattr(os, "mkfifo"):
        kinds.append("fifo")
    for kind in kinds:
        home = tmp_path / kind
        path = _gemini_bucket(home, root, "b1", [_gem_meta("s1", root), _gem_msg("e1", "DO_NOT_READ")])
        marker = path.parent.parent / ".project_root"
        marker.unlink()
        if kind == "symlink":
            destination = tmp_path / "marker-target"
            destination.write_text(str(root))
            marker.symlink_to(destination)
        elif kind == "broken":
            marker.symlink_to(tmp_path / "missing")
        elif kind == "fifo":
            os.mkfifo(marker)
        else:
            marker.write_text("x" * 5000)
        body_reads = []
        with monkeypatch.context() as scoped:

            def forbidden_probe(*args, **kwargs):
                body_reads.append(args[0])
                raise AssertionError("unsafe marker admitted a body")

            scoped.setattr(session_adapters, "_probe_records", forbidden_probe)
            result = scs.discover(root, home=home)
            assert not body_reads, kind
            assert not result["candidates"], kind
            assert next(s for s in result["sources"] if s["agent"] == "gemini")["complete"] is False
    home = tmp_path / "swap"
    path = _gemini_bucket(home, root, "b1", [_gem_meta("s1", root), _gem_msg("e1", "DO_NOT_READ")])
    original = session_adapters.GeminiAdapter.read_messages

    def swap(self, ctx, candidate):
        (path.parent.parent / ".project_root").write_text(str(foreign))
        return original(self, ctx, candidate)

    monkeypatch.setattr(session_adapters.GeminiAdapter, "read_messages", swap)
    read = scs.read(root, agent="gemini", session_id="s1", home=home)
    assert read["error"]["reason_code"] == "scope-mismatch"
    assert not read["messages"]


def test_copilot_scope_controls_and_versions_cannot_reset_trust(tmp_path):
    root = _init_repo(tmp_path / "proj")
    foreign = _init_repo(tmp_path / "foreign")
    variants = [
        _cp("session.start", {"sessionId": "other", "context": {"cwd": str(root)}}, "e2", "e1"),
        _cp("session.resume", {"context": {"cwd": str(foreign)}}, "e2", "e1"),
        _cp("session.resume", {"context": {"cwd": str(root), "gitRoot": str(foreign)}}, "e2", "e1"),
        _cp("session.resume", {"context": None}, "e2", "e1"),
        _cp("session.context_changed", {"cwd": str(root), "gitRoot": str(foreign)}, "e2", "e1"),
        _cp("session.context_changed", {"cwd": 42}, "e2", "e1"),
    ]
    for index, control in enumerate(variants):
        home = tmp_path / f"scope-{index}"
        _copilot_events(
            home,
            "s1",
            [
                _cp("session.start", {"sessionId": "s1", "context": {"cwd": str(root)}}, "e0"),
                _cp("user.message", {"content": "private goal"}, "e1", "e0"),
                control,
            ],
        )
        read = scs.read(root, agent="copilot", session_id="s1", home=home)
        assert read["status"] != "ok", index
        assert "private goal" not in json.dumps(read), index
    for index, version in enumerate((False, 0, "1", None)):
        home = tmp_path / f"version-{index}"
        _copilot_events(
            home,
            "s1",
            [
                _cp("session.start", {"sessionId": "s1", "context": {"cwd": str(root)}, "version": version}, "e0"),
                _cp("user.message", {"content": "private goal"}, "e1", "e0"),
            ],
        )
        assert scs.read(root, agent="copilot", session_id="s1", home=home)["status"] != "ok"


def test_copilot_final_ephemeral_rewrite_is_not_ignored(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _copilot_events(
        home,
        "s1",
        [
            _cp("session.start", {"sessionId": "s1", "context": {"cwd": str(root)}}, "e0"),
            _cp("user.message", {"content": "old goal"}, "e1", "e0"),
            _cp("session.snapshot_rewind", {}, "e2", "e1", ephemeral=True),
        ],
    )
    read = scs.read(root, agent="copilot", session_id="s1", home=home)
    assert read["complete"] is False
    assert "unsupported-control" in read["truncation_reasons"]
    assert not read["messages"]


def test_gemini_hash_only_membership_and_legacy_json(tmp_path):
    root = _init_repo(tmp_path / "proj")
    foreign = _init_repo(tmp_path / "foreign")
    home = tmp_path / "home"
    for sid, cwd in (("s1", root), ("foreign", foreign)):
        digest = _gem_meta(sid, cwd)["projectHash"]
        path = _gemini_bucket(home, cwd, digest, [_gem_meta(sid, cwd), _gem_msg("e1", sid)])
        (path.parent.parent / ".project_root").unlink()
    discovered = scs.discover(root, home=home)
    assert {c["session_id"] for c in discovered["candidates"] if c["agent"] == "gemini"} == {"s1"}
    assert scs.read(root, agent="gemini", session_id="foreign", home=home)["status"] != "ok"
    legacy_home = tmp_path / "legacy"
    path = _gemini_bucket(legacy_home, root, "b1", [])
    path.rename(path.with_suffix(".json"))
    discovered = scs.discover(root, home=legacy_home)
    assert not discovered["candidates"]
    source = next(source for source in discovered["sources"] if source["agent"] == "gemini")
    assert source["reason_code"] == "legacy-json-unverified"
    assert source["complete"] is False


def test_new_client_alias_prefers_only_scoped_sessions_and_excludes_current(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _gemini_bucket(home, root, "b1", [_gem_meta("s1", root), _gem_msg("e1", "Gemini goal")])
    encoded = "".join(char if char.isalnum() or char in "_-" else "-" for char in str(root))
    bucket = home / ".claude" / "projects" / encoded
    bucket.mkdir(parents=True)
    record = {
        "type": "user",
        "sessionId": "c1",
        "cwd": str(root),
        "uuid": "u1",
        "timestamp": "2099-11-02T10:00:00Z",
        "message": {"role": "user", "content": "Claude goal"},
    }
    (bucket / "c1.jsonl").write_text(json.dumps(record) + "\n")
    for client in ("gemini", "gemini-cli"):
        discovered = scs.discover(root, home=home, client=client)
        assert discovered["candidates"][0]["agent"] == "gemini", client
        remaining = scs.discover(root, home=home, client=client, current_session="s1")
        assert [candidate["agent"] for candidate in remaining["candidates"]] == ["claude"], client

"""CLI contract tests for scripts/spec_context.py.

Runs the real entrypoint as a subprocess against tmp HOME synthetic stores;
asserts exit codes, output formats, workspace anchoring, source identities
and the explicit absence of any automatic task decision.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SPEC_CONTEXT = SCRIPTS / "spec_context.py"


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


def _seed_claude(root: Path, home: Path, sid: str = "sid-1") -> None:
    bucket = home / ".claude" / "projects" / _claude_encode(str(root))
    bucket.mkdir(parents=True, exist_ok=True)
    cwd = str(root)
    records = [
        {
            "type": "user",
            "cwd": cwd,
            "sessionId": sid,
            "uuid": "u1",
            "parentUuid": None,
            "timestamp": "2026-11-02T10:00:00Z",
            "gitBranch": "main",
            "isSidechain": False,
            "message": {"role": "user", "content": [{"type": "text", "text": "export alpha"}]},
        },
        {
            "type": "assistant",
            "cwd": cwd,
            "sessionId": sid,
            "uuid": "u2",
            "parentUuid": "u1",
            "timestamp": "2026-11-02T10:01:00Z",
            "gitBranch": "main",
            "isSidechain": False,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "alpha next step"}]},
        },
    ]
    target = bucket / f"{sid}.jsonl"
    with open(target, "w", encoding="utf-8", newline="") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SPEC_CONTEXT), *args],
        capture_output=True,
        text=True,
    )


def test_discover_json_anchor_and_sources(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _seed_claude(root, home)
    proc = _run("discover", "--root", str(root), "--home", str(home), "--format", "json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema_version"] == 1
    assert payload["workspace"]["root"] == str(root.resolve())
    assert payload["workspace"]["git"] is True
    names = {s["agent"] for s in payload["sources"]}
    assert names == {"claude", "codex", "devin", "pi", "cursor", "opencode", "gemini", "copilot"}
    assert payload["candidates"][0]["session_id"] == "sid-1"
    assert str(home) not in proc.stdout


def test_discover_markdown_format(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _seed_claude(root, home)
    proc = _run("discover", "--root", str(root), "--home", str(home), "--format", "markdown")
    assert proc.returncode == 0, proc.stderr
    assert "claude" in proc.stdout
    assert "sid-1" in proc.stdout


def test_discover_empty_is_factual_exit_zero(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    home.mkdir()
    proc = _run("discover", "--root", str(root), "--home", str(home))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "none"
    assert payload["candidates"] == []


def test_read_json_messages_and_context_only_authority(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _seed_claude(root, home)
    proc = _run(
        "read",
        "--root",
        str(root),
        "--home",
        str(home),
        "--agent",
        "claude",
        "--session",
        "sid-1",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert payload["authority"] == "context-only"
    assert "untrusted-history" in payload["advisories"]
    assert "not-completion-evidence" in payload["advisories"]
    assert [m["text"] for m in payload["messages"]] == ["export alpha", "alpha next step"]
    assert "task" not in payload.get("decision", "")
    assert str(home) not in proc.stdout


def test_read_unknown_session_exits_one_with_diagnostic(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    _seed_claude(root, home)
    proc = _run(
        "read",
        "--root",
        str(root),
        "--home",
        str(home),
        "--agent",
        "claude",
        "--session",
        "sid-nope",
    )
    assert proc.returncode == 1
    assert "unknown" in proc.stderr.lower() or "unknown" in proc.stdout.lower()


def test_read_rejects_path_injection_session_id(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    for bad in ("../x", "a/b", "..", "a\\b"):
        proc = _run(
            "read",
            "--root",
            str(root),
            "--home",
            str(home),
            "--agent",
            "claude",
            f"--session={bad}",
        )
        assert proc.returncode == 2, (bad, proc.returncode, proc.stderr)


def test_read_rejects_unknown_agent(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home = tmp_path / "home"
    proc = _run(
        "read",
        "--root",
        str(root),
        "--home",
        str(home),
        "--agent",
        "madeup",
        "--session",
        "sid-1",
    )
    assert proc.returncode == 2


def test_missing_root_exits_one(tmp_path):
    home = tmp_path / "home"
    proc = _run("discover", "--root", str(tmp_path / "nope"), "--home", str(home))
    assert proc.returncode == 1
    assert proc.stderr.strip()


def test_usage_error_exits_two(tmp_path):
    proc = _run("discover", "--format", "yaml")
    assert proc.returncode == 2
    proc = _run("frobnicate")
    assert proc.returncode == 2


def test_home_isolation_no_real_home_read(tmp_path):
    root = _init_repo(tmp_path / "proj")
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    _seed_claude(root, home_b)
    proc = _run("discover", "--root", str(root), "--home", str(home_a))
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["candidates"] == []
    proc = _run("discover", "--root", str(root), "--home", str(home_b))
    assert len(json.loads(proc.stdout)["candidates"]) == 1

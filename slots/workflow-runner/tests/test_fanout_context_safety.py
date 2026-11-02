"""Offline regression tests for cache provenance and bounded worker execution."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import workflow_fanout as wf

requires_linux = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="real bounded execution requires Linux"
)


def write_plan(path: Path, prompt: str, item_id: str = "same") -> None:
    path.write_text(json.dumps({"items": [{"id": item_id, "prompt": prompt}]}), encoding="utf-8")


def run_main(plan: Path, out: Path, *extra: str) -> tuple[int, dict]:
    code = wf.main(["--backend", "pi", "--plan", str(plan), "--out", str(out), *extra])
    return code, json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture
def stub_worker(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(wf, "resolve_binary", lambda backend: sys.executable)

    def command(backend, prompt, output):
        calls.append(prompt)
        return [sys.executable, "-c", "import sys; print(sys.argv[1])", prompt]

    monkeypatch.setattr(wf, "build_command", command)
    return calls


@requires_linux
def test_default_unknown_context_redispatches(tmp_path, stub_worker):
    plan, out = tmp_path / "plan.json", tmp_path / "result.jsonl"
    write_plan(plan, "same request")
    assert run_main(plan, out)[0] == 0
    code, result = run_main(plan, out)
    assert code == 0 and result.get("cached") is not True
    assert stub_worker == ["same request", "same request"]
    assert not (tmp_path / "result.jsonl.d" / "cache").exists()


@requires_linux
def test_cache_same_id_a_b_a_keeps_each_immutable_artifact(tmp_path, monkeypatch, stub_worker):
    monkeypatch.setattr(wf, "_workspace_context", lambda value, binary: "proven-context" if value else None)
    plan, out = tmp_path / "plan.json", tmp_path / "result.jsonl"
    write_plan(plan, "A")
    _, first = run_main(plan, out, "--cache-context", "context")
    write_plan(plan, "B")
    _, second = run_main(plan, out, "--cache-context", "context")
    write_plan(plan, "A")
    code, third = run_main(plan, out, "--cache-context", "context")
    assert code == 0 and third["cached"] is True
    assert stub_worker == ["A", "B"]
    assert first["outputFile"] != second["outputFile"]
    assert third["outputFile"] == first["outputFile"]
    assert Path(first["outputFile"]).read_text().strip() == "A"
    assert Path(second["outputFile"]).read_text().strip() == "B"
    assert third["stdoutSummary"].strip() == "A"


@requires_linux
def test_result_atomic_replace_does_not_truncate_other_hardlink(tmp_path, stub_worker):
    plan, out, unrelated = tmp_path / "plan.json", tmp_path / "result.jsonl", tmp_path / "unrelated.txt"
    write_plan(plan, "A")
    unrelated.write_text("keep this unrelated content")
    os.link(unrelated, out)
    code, record = run_main(plan, out)
    assert code == 0 and record["ok"] is True
    assert unrelated.read_text() == "keep this unrelated content"
    assert out.stat().st_ino != unrelated.stat().st_ino
    assert not list(tmp_path.glob(".fanout-*.tmp"))


def test_result_concurrent_atomic_writers_publish_complete_jsonl(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    output = tmp_path / "result.jsonl"
    barrier = threading.Barrier(2)
    payloads = [[{"id": f"{worker}-{i}"} for i in range(50)] for worker in ("a", "b")]

    def publish(records):
        barrier.wait()
        wf._write_results(output, records)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(publish, payloads))
    actual = [json.loads(line) for line in output.read_text().splitlines()]
    assert actual in payloads
    assert not list(tmp_path.glob(".fanout-*.tmp"))


@requires_linux
def test_context_attestation_change_and_omission_do_not_reuse(tmp_path, monkeypatch, stub_worker):
    monkeypatch.setattr(wf, "_workspace_context", lambda value, binary: f"context:{value}" if value else None)
    plan, out = tmp_path / "plan.json", tmp_path / "result.jsonl"
    write_plan(plan, "A")
    assert run_main(plan, out, "--cache-context", "v1")[0] == 0
    _, same = run_main(plan, out, "--cache-context", "v1")
    assert same["cached"] is True
    _, changed = run_main(plan, out, "--cache-context", "v2")
    assert changed.get("cached") is not True
    _, omitted = run_main(plan, out)
    assert omitted.get("cached") is not True
    assert stub_worker == ["A", "A", "A"]


@requires_linux
def test_workspace_change_during_fresh_dispatch_does_not_save_cache(tmp_path, monkeypatch, stub_worker):
    contexts = iter(["snapshot-a", None])
    monkeypatch.setattr(wf, "_workspace_context", lambda value, binary: next(contexts))
    plan, out = tmp_path / "plan.json", tmp_path / "result.jsonl"
    write_plan(plan, "A")
    code, result = run_main(plan, out, "--cache-context", "context")
    assert code == 0 and result["ok"] is True
    assert not (tmp_path / "result.jsonl.d" / "cache").exists()


@pytest.mark.parametrize("damage", ["missing", "modified", "symlink"])
@requires_linux
def test_missing_or_changed_cache_artifact_is_a_miss(tmp_path, monkeypatch, stub_worker, damage):
    monkeypatch.setattr(wf, "_workspace_context", lambda value, binary: "proven-context" if value else None)
    plan, out = tmp_path / "plan.json", tmp_path / "result.jsonl"
    write_plan(plan, "A")
    _, first = run_main(plan, out, "--cache-context", "context")
    artifact = Path(first["outputFile"])
    if damage == "modified":
        artifact.write_text("tampered")
    elif damage == "missing":
        artifact.unlink()
    else:
        artifact.unlink()
        target = tmp_path / "external"
        target.write_text("A\n")
        artifact.symlink_to(target)
    code, second = run_main(plan, out, "--cache-context", "context")
    assert code == 0 and second.get("cached") is not True
    assert stub_worker == ["A", "A"]
    assert Path(second["outputFile"]).read_text().strip() == "A"


@requires_linux
def test_long_multibyte_item_id_does_not_break_generated_filenames(tmp_path, stub_worker):
    plan, out = tmp_path / "plan.json", tmp_path / "result.jsonl"
    item_id = "审" * 128
    write_plan(plan, "safe", item_id)
    code, result = run_main(plan, out)
    assert code == 0 and result["id"] == item_id and result["ok"] is True
    assert Path(result["outputFile"]).read_text().strip() == "safe"


def test_old_codex_final_is_not_this_attempts_output(tmp_path, monkeypatch):
    old = tmp_path / "same.out"
    old.write_text("old final message")
    monkeypatch.setattr(wf, "_run_bounded", lambda cmd, timeout: (
        subprocess.CompletedProcess(cmd, 0, "progress noise", ""), False, False
    ))
    record = wf.run_item("codex", {"id": "same", "prompt": "new task"}, tmp_path, 3)
    assert record["ok"] is False and "output read failed" in record["error"]
    assert old.read_text() == "old final message"
    assert record["outputFile"] != str(old)


def test_codex_explicit_write_requirement_fails_without_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "_run_bounded", lambda *args: pytest.fail("must not dispatch"))
    record = wf.run_item("codex", {"id": "build", "prompt": "implement", "requiresWrite": True}, tmp_path, 3)
    assert record["ok"] is False and "read-only" in record["error"]
    cmd = wf.build_command("codex", "inspect", tmp_path / "answer.out")
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"


def test_codex_requires_write_rejected_by_main_without_escalation(tmp_path, monkeypatch):
    plan, out = tmp_path / "plan.json", tmp_path / "result.jsonl"
    plan.write_text(json.dumps({"items": [{"id": "build", "prompt": "implement", "requiresWrite": True}]}))
    monkeypatch.setattr(wf, "resolve_binary", lambda backend: "codex-stub")
    monkeypatch.setattr(wf, "_workspace_context", lambda value, binary: "proven-context")
    monkeypatch.setattr(wf, "_run_bounded", lambda *args: pytest.fail("must not dispatch"))
    code = wf.main(["--backend", "codex", "--plan", str(plan), "--out", str(out), "--cache-context", "context"])
    record = json.loads(out.read_text())
    assert code == 2 and not record["ok"] and "read-only" in record["error"]
    assert record.get("cached") is not True


@requires_linux
def test_cached_success_is_invalid_if_context_changes_during_batch(tmp_path, monkeypatch, stub_worker):
    plan, out = tmp_path / "plan.json", tmp_path / "result.jsonl"
    write_plan(plan, "A")
    monkeypatch.setattr(wf, "_workspace_context", lambda value, binary: "snapshot-a")
    assert run_main(plan, out, "--cache-context", "context")[0] == 0
    contexts = iter(["snapshot-a", "snapshot-b"])
    monkeypatch.setattr(wf, "_workspace_context", lambda value, binary: next(contexts))
    code, result = run_main(plan, out, "--cache-context", "context")
    assert code == 2 and result["cached"] is True and result["ok"] is False
    assert "context changed" in result["error"]
    assert stub_worker == ["A"]


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is required for the isolated workspace probe")
@requires_linux
def test_cache_tracks_actual_clean_git_workspace(tmp_path, monkeypatch, stub_worker):
    # All Git state is a disposable test repository with global/system config disabled.
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], env=env, check=True, capture_output=True, text=True)

    git("init", "-q")
    source = repo / "source.txt"
    source.write_text("version one")
    (repo / ".gitignore").write_text("ignored.txt\n")
    git("add", "source.txt", ".gitignore")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial")
    monkeypatch.chdir(repo)
    plan, out = tmp_path / "plan.json", tmp_path / "result.jsonl"
    write_plan(plan, "inspect source")
    args = ("--cache-context", "external-worker-config-v1")
    assert wf._workspace_context(None, sys.executable) is None
    assert wf._workspace_context(args[1], sys.executable) is not None
    code, first = run_main(plan, out, *args)
    assert code == 0 and first.get("cached") is not True
    code, second = run_main(plan, out, *args)
    assert code == 0 and second["cached"] is True
    source.write_text("version two")
    assert wf._workspace_context(args[1], sys.executable) is None
    code, third = run_main(plan, out, *args)
    assert code == 0 and third.get("cached") is not True
    assert stub_worker == ["inspect source", "inspect source"]
    source.write_text("version one")
    executable = tmp_path / "worker-binary"
    executable.write_text("worker-v1")
    worker_v1 = wf._workspace_context(args[1], str(executable))
    assert worker_v1 is not None
    executable.write_text("worker-v2")
    assert wf._workspace_context(args[1], str(executable)) != worker_v1
    assert wf._workspace_context(args[1], None) is None
    ignored = repo / "ignored.txt"
    ignored.write_text("untracked ignored data")
    assert wf._workspace_context(args[1], sys.executable) is None
    ignored.unlink()
    git("update-index", "--skip-worktree", "source.txt")
    assert wf._workspace_context(args[1], sys.executable) is None
    git("update-index", "--no-skip-worktree", "source.txt")
    git("update-index", "--assume-unchanged", "source.txt")
    assert wf._workspace_context(args[1], sys.executable) is None
    git("update-index", "--no-assume-unchanged", "source.txt")
    assert wf._workspace_context(args[1], sys.executable) is not None
    (repo / "link.txt").symlink_to("source.txt")
    git("add", "link.txt")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "add symlink")
    assert wf._workspace_context(args[1], sys.executable) is None


@requires_linux
def test_pipe_eof_does_not_shorten_deadline():
    worker = "import os,time; os.close(1); os.close(2); time.sleep(2.2)"
    started = time.monotonic()
    completed, timed_out, limited = wf._run_bounded([sys.executable, "-c", worker], 6)
    assert time.monotonic() - started >= 2.0
    assert completed.returncode == 0 and not timed_out and not limited


@requires_linux
def test_pipe_eof_still_honors_real_timeout():
    worker = "import os,time; os.close(1); os.close(2); time.sleep(5)"
    started = time.monotonic()
    completed, timed_out, limited = wf._run_bounded([sys.executable, "-c", worker], 0.3)
    assert time.monotonic() - started < 4
    assert completed.returncode != 0 and timed_out and not limited


def test_unsupported_platform_fails_before_worker_spawn(monkeypatch):
    monkeypatch.setattr(wf.sys, "platform", "unsupported")
    monkeypatch.setattr(wf.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not launch worker"))
    with pytest.raises(OSError, match="verified process-group cleanup"):
        wf._run_bounded(["fake"], 1)

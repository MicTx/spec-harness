"""Checkpoint orchestration tests; fake workers do not claim OS cleanup proof."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import workflow_fanout as wf


@pytest.fixture
def driver(tmp_path, monkeypatch):
    calls = []
    plan, out = tmp_path / "plan.json", tmp_path / "results.jsonl"
    monkeypatch.setattr(wf, "resolve_binary", lambda _backend: sys.executable)

    def worker(backend, item, output_dir, _timeout, _model=None):
        calls.append(item["id"])
        output = output_dir / f"{uuid.uuid4().hex}.out"
        content = item["prompt"].encode()
        output.write_bytes(content)
        return {
            "id": item["id"],
            "backend": backend,
            "ok": True,
            "durationSec": 0.01,
            "outputFile": str(output),
            "outputSha256": hashlib.sha256(content).hexdigest(),
            "stdoutSummary": item["prompt"],
        }

    monkeypatch.setattr(wf, "run_item", worker)

    def run(items=None, *extra):
        if items is not None:
            plan.write_text(json.dumps({"items": items}))
        return wf.main(["--backend", "pi", "--plan", str(plan), "--out", str(out), *extra])

    return run, worker, calls, out


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_midbatch_checkpoint_and_cache_survive_interruption_then_resume_only_via_cache(driver, monkeypatch):
    run, worker, calls, out = driver
    monkeypatch.setattr(wf, "_workspace_context", lambda value, _binary: "strict-synthetic-context" if value else None)
    slow_started, release, cache_saved = threading.Event(), threading.Event(), threading.Event()
    cache_writer = wf.write_cache

    def cache(*args):
        cache_writer(*args)
        cache_saved.set()

    def interrupted(backend, item, output_dir, timeout, model=None):
        if item["id"] == "slow":
            calls.append("slow")
            slow_started.set()
            assert release.wait(10)
            raise KeyboardInterrupt("injected parent interruption before remaining result")
        assert slow_started.wait(10)
        return worker(backend, item, output_dir, timeout, model)

    monkeypatch.setattr(wf, "write_cache", cache)
    monkeypatch.setattr(wf, "run_item", interrupted)
    items = [{"id": "slow", "prompt": "second completion"}, {"id": "fast", "prompt": "first completion"}]
    with ThreadPoolExecutor(max_workers=1) as parent:
        future = parent.submit(run, items, "--cache-context", "attestation", "--concurrency", "2")
        try:
            assert cache_saved.wait(10), "completed item was not published/cached while sibling was still running"
            partial = rows(out)
            assert [record["id"] for record in partial] == ["fast"]
            first = partial[0]
            assert first["status"] == "succeeded" and first["batchComplete"] is False
            assert len(first["attemptId"]) == 32 and len(first["inputKey"]) == 64
            assert first["outputSha256"] == hashlib.sha256(Path(first["outputFile"]).read_bytes()).hexdigest()
            assert len(list((out.with_suffix(".jsonl.d") / "cache").glob("*.json"))) == 1
        finally:
            release.set()
        with pytest.raises(KeyboardInterrupt):
            future.result(timeout=10)
    assert rows(out) == partial
    monkeypatch.setattr(wf, "run_item", worker)
    assert run(None, "--cache-context", "attestation") == 0
    final = rows(out)
    assert [record["id"] for record in final] == ["slow", "fast"]
    assert Counter(calls) == Counter({"slow": 2, "fast": 1})
    assert final[1]["status"] == "cached" and final[1]["cached"] is True
    assert all(record["batchComplete"] for record in final)
    assert final[1]["attemptId"] != first["attemptId"]
    assert final[1]["sourceAttemptId"] == first["attemptId"]
    assert final[1]["inputKey"] == first["inputKey"]
    assert final[1]["outputFile"] == first["outputFile"]


@pytest.mark.parametrize("damage", ["tamper", "prompt", "context", "no-cache"])
def test_checkpoint_never_authorizes_reuse_when_cache_validation_misses(driver, monkeypatch, damage):
    run, _, calls, out = driver
    monkeypatch.setattr(wf, "_workspace_context", lambda value, _binary: f"context:{value}" if value else None)
    item = {"id": "one", "prompt": "first prompt"}
    assert run([item], "--cache-context", "v1") == 0
    first = rows(out)[0]
    extra = ["--cache-context", "v1"]
    if damage == "tamper":
        Path(first["outputFile"]).write_text("tampered output")
    elif damage == "prompt":
        item["prompt"] = "different prompt"
    elif damage == "context":
        extra = ["--cache-context", "v2"]
    else:
        extra += ["--no-cache"]
    assert run([item], *extra) == 0
    second = rows(out)[0]
    assert calls == ["one", "one"] and second.get("cached") is not True
    assert second["status"] == "succeeded" and second["attemptId"] != first["attemptId"]
    if damage in ("prompt", "context", "no-cache"):
        assert second["inputKey"] != first["inputKey"]


def test_stale_jsonl_without_cache_is_observation_only(driver):
    run, _, calls, out = driver
    out.write_text(
        json.dumps(
            {"id": "one", "ok": True, "batchComplete": True, "attemptId": "old", "outputFile": "/unknown/worker/output"}
        )
        + "\n"
    )
    assert run([{"id": "one", "prompt": "must dispatch"}]) == 0
    assert calls == ["one"] and rows(out)[0].get("cached") is not True
    assert not (out.with_suffix(".jsonl.d") / "cache").exists()


def test_initial_checkpoint_failure_starts_no_worker(driver, monkeypatch):
    run, _, calls, _ = driver

    def failed(*_args):
        raise OSError("injected disk failure")

    monkeypatch.setattr(wf, "_write_results", failed)
    assert run([{"id": "one", "prompt": "must not dispatch"}]) == 2
    assert calls == []


@pytest.mark.parametrize("stage", ["first-item", "final"])
def test_checkpoint_io_error_never_becomes_success(driver, monkeypatch, stage):
    run, _, _, out = driver
    original, fired = wf._write_results, False

    def failed_once(path, records):
        nonlocal fired
        if records and not fired and (records[0]["batchComplete"] is (stage == "final")):
            fired = True
            raise OSError("injected checkpoint I/O error")
        original(path, records)

    monkeypatch.setattr(wf, "_write_results", failed_once)
    assert run([{"id": "one", "prompt": "fixture"}]) == 2
    assert fired
    if stage == "final":
        assert rows(out)[0]["batchComplete"] is False


def test_result_write_fsyncs_file_then_parent(tmp_path, monkeypatch):
    calls = []
    original = wf.os.fsync

    def sync(fd):
        calls.append(fd)
        return original(fd)

    monkeypatch.setattr(wf.os, "fsync", sync)
    target = tmp_path / "results.jsonl"
    wf._write_results(target, [{"id": "one"}])
    assert len(calls) == 2
    assert rows(target) == [{"id": "one"}]
    assert not list(tmp_path.glob(".fanout-*.tmp"))


@pytest.mark.parametrize("change", ["untracked", "ignored", "dirty", "no-attestation"])
def test_real_clean_workspace_gate_remains_required_for_early_cache(driver, tmp_path, monkeypatch, change):
    run, worker, calls, out = driver
    root = tmp_path / "workspace"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q")
    (root / "tracked").write_text("original")
    (root / ".gitignore").write_text("ignored-file\n")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "fixture")
    monkeypatch.chdir(root)
    assert wf._workspace_context("attestation", sys.executable) is not None

    def mutating_worker(*args):
        result = worker(*args)
        if change == "untracked":
            (root / "new-file").write_text("untracked")
        elif change == "ignored":
            (root / "ignored-file").write_text("ignored")
        elif change == "dirty":
            (root / "tracked").write_text("modified")
        return result

    monkeypatch.setattr(wf, "run_item", mutating_worker)
    extra = () if change == "no-attestation" else ("--cache-context", "attestation")
    assert run([{"id": "one", "prompt": "fixture"}], *extra) == 0
    assert not (out.with_suffix(".jsonl.d") / "cache").exists()
    assert run(None, *extra) == 0
    assert calls == ["one", "one"]

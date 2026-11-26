"""C5: a failing checkpoint commit must not leave a ticked task without evidence.

The pre-commit hook fixture refuses the wip checkpoint after a passing verify;
the run must still be recorded, tasks.md and the index must roll back, and
gate/route must keep presenting the task as unfinished ready work.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo, started_package

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import gate  # noqa: E402
import gitx  # noqa: E402


def _tasks_bytes(root: Path) -> bytes:
    return (root / ".spec" / "specs" / SLUG / "tasks.md").read_bytes()


def _tasks_text(root: Path) -> str:
    return _tasks_bytes(root).decode("utf-8")


def _index_tree(root: Path) -> str:
    result = gitx.git(root, "write-tree", check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _index_entries(root: Path) -> str:
    return gitx.git(root, "ls-files", "--stage").stdout


def _run_rows(root: Path) -> list[dict]:
    path = root / ".spec" / "specs" / SLUG / "runs.jsonl"
    return [
        row
        for row in (json.loads(line) for line in path.read_text().splitlines() if line.strip())
        if row.get("kind", "run") == "run"
    ]


def _deny_commits(root: Path, name: str = "pre-commit") -> Path:
    hook = root / ".git" / "hooks" / name
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    os.chmod(hook, 0o755)
    return hook


def test_failed_checkpoint_rolls_back_tick_and_index_but_keeps_the_run(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    hook = _deny_commits(tmp_path)
    tasks_before = _tasks_bytes(tmp_path)
    tree_before = _index_tree(tmp_path)
    entries_before = _index_entries(tmp_path)

    result = cli(tmp_path, "verify", "task-value")

    assert result.returncode == 1
    assert "checkpoint" in result.stderr
    assert "spec.py verify task-value" in result.stderr
    assert _tasks_bytes(tmp_path) == tasks_before
    assert "- [ ] Change value" in _tasks_text(tmp_path)
    assert _index_tree(tmp_path) == tree_before
    assert _index_entries(tmp_path) == entries_before

    rows = _run_rows(tmp_path)
    assert len(rows) == 1
    record = rows[-1]
    # The verify command passed but the run is not passing evidence: exit is
    # the ledger's failure bit so gate/route keep the task unproven.
    assert record["verify_exit"] == 0
    assert record["exit"] != 0
    assert "checkpoint_error" in record
    assert "\n" not in record["checkpoint_error"]
    assert "checkpoint" not in record

    next_card = json.loads(cli(tmp_path, "next").stdout)
    assert next_card["task"]["id"] == "task-value"
    verdict = gate.compute(tmp_path, SLUG)
    assert any(item["code"] == "task-unfinished" and item["subject"] == "task-value" for item in verdict["findings"])
    assert (
        next(item for item in json.loads(cli(tmp_path, "next").stdout)["criteria"] if item["task"] == "task-value")[
            "passed"
        ]
        is False
    )

    hook.unlink()

    recovered = cli(tmp_path, "verify", "task-value")

    assert recovered.returncode == 0, recovered.stderr
    assert "- [x] Change value" in _tasks_text(tmp_path)
    rows = _run_rows(tmp_path)
    assert rows[-1]["exit"] == 0 and rows[-1]["checkpoint"]
    assert (
        gitx.git(tmp_path, "log", "-1", "--format=%s").stdout.strip() == "wip(2026-11-10_add-v3-fixture): Change value"
    )

    delivered = cli(tmp_path, "check")

    assert delivered.returncode == 0, delivered.stderr
    assert (tmp_path / ".spec" / "specs" / "archive" / SLUG / "completion-summary.md").is_file()


def test_verify_staging_outside_the_boundary_records_the_run_without_a_tick(tmp_path):
    repo(tmp_path)
    # The task boundary admits app.py; the passing verify stages a file the
    # boundary does not cover, so the checkpoint must fail without a tick.
    package = make_package(tmp_path, verify="git add outside.txt 2>/dev/null || true")
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("- in: app.py", "- in: app.py\n- in: outside.txt"),
        encoding="utf-8",
    )
    assert cli(tmp_path, "plan-go").returncode == 0
    (tmp_path / "outside.txt").write_text("OUT\n", encoding="utf-8")
    tasks_before = _tasks_bytes(tmp_path)
    entries_before = _index_entries(tmp_path)

    result = cli(tmp_path, "verify", "task-value")

    assert result.returncode == 1
    assert "outside.txt" in result.stderr
    assert _tasks_bytes(tmp_path) == tasks_before
    assert _index_entries(tmp_path) == entries_before
    assert (tmp_path / "outside.txt").read_text() == "OUT\n"
    rows = _run_rows(tmp_path)
    assert len(rows) == 1
    assert rows[-1]["verify_exit"] == 0
    assert "outside the task boundary" in rows[-1]["checkpoint_error"]

    next_card = json.loads(cli(tmp_path, "next").stdout)
    assert next_card["task"]["id"] == "task-value"


def test_blocking_records_state_without_a_git_checkpoint(tmp_path):
    repo(tmp_path)
    started_package(tmp_path, verify="test ! -e fail-marker")
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    for _ in range(3):
        assert cli(tmp_path, "verify", "task-value").returncode == 1
    tasks_before = _tasks_bytes(tmp_path)
    tree_before = _index_tree(tmp_path)
    hook = _deny_commits(tmp_path)

    result = cli(tmp_path, "verify", "task-value")

    assert result.returncode == 1
    assert "checkpoint" not in result.stderr
    tasks = _tasks_text(tmp_path)
    assert "- [!] Change value" in tasks
    assert tasks.encode() != tasks_before
    assert _index_tree(tmp_path) == tree_before
    rows = _run_rows(tmp_path)
    assert len(rows) == 4
    assert rows[-1]["blocked"] is True
    assert "checkpoint_error" not in rows[-1]
    hook.unlink()


def test_failed_checkpoint_in_local_mode_restores_evidence_index(tmp_path, monkeypatch):
    import evidence

    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "purge").returncode == 0
    # Local mode untracks .spec evidence inside checkpoint_commit, so the
    # commit itself has no product diff to fail on; drive the failure where a
    # real commit blocker would land (e.g. a refused commit or a staged
    # outside-boundary path after untrack) and prove the index restore also
    # puts the evidence entries back.
    tasks_before = _tasks_bytes(tmp_path)
    entries_before = _index_entries(tmp_path)

    def denied_commit(*_args, **_kwargs):
        raise ValueError("staged path outside the task boundary: outside.txt")

    monkeypatch.setattr(evidence, "checkpoint_commit", denied_commit)

    result = evidence.run_tasks(tmp_path, SLUG, ["task-value"])

    assert result["ready"] is False
    record = result["results"][0]
    assert record["verify_exit"] == 0
    assert "outside the task boundary" in record["checkpoint_error"]
    assert _tasks_bytes(tmp_path) == tasks_before
    assert _index_entries(tmp_path) == entries_before
    rows = _run_rows(tmp_path)
    assert len(rows) == 1
    assert rows[-1]["verify_exit"] == 0

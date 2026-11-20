"""Pin final-evidence reuse and plan-verify dedupe against fresh records."""

from __future__ import annotations

import json
from pathlib import Path

import evidence
import package
import plan

from tests.v3_support import SLUG, cli, repo, started_package

# Appends one 'x' per execution, so the file length counts every real run of
# the verify command (plan-go's record lint once, the execution verify once...).
COUNTER_CMD = "python3 -c \"open('counter.txt','a').write('x')\""


def _records(pkg: Path) -> list[dict]:
    path = pkg / "runs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _runs(counter: Path) -> int:
    return len(counter.read_text(encoding="utf-8"))


def test_final_reuse_skips_rerun(tmp_path):
    repo(tmp_path)
    pkg = started_package(tmp_path, verify=COUNTER_CMD)
    counter = tmp_path / "counter.txt"
    assert _runs(counter) == 1  # plan-go's record lint executed the verify once
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert _runs(counter) == 2
    result = evidence.run_tasks(tmp_path, SLUG, final=True)
    assert result["ready"] is True
    assert _runs(counter) == 2  # fresh evidence reused, no re-execution
    reused = [r for r in _records(pkg) if r.get("reused") and r.get("task") == "task-value"]
    assert reused and reused[-1].get("final") is True


def test_rerun_stale_tree(tmp_path):
    repo(tmp_path)
    pkg = started_package(tmp_path, verify=COUNTER_CMD)
    counter = tmp_path / "counter.txt"
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert _runs(counter) == 2
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = evidence.run_tasks(tmp_path, SLUG, final=True)
    assert result["ready"] is True
    assert _runs(counter) == 3  # product tree changed, so the verify re-runs
    assert not [r for r in _records(pkg) if r.get("reused")]


def test_plan_fresh_skip(tmp_path):
    repo(tmp_path)
    pkg = started_package(tmp_path, verify=COUNTER_CMD)
    counter = tmp_path / "counter.txt"
    assert _runs(counter) == 1  # plan-go's own lint already executed it once
    package.append_run(
        pkg,
        {
            "kind": "run",
            "task": "task-value",
            "cmd": COUNTER_CMD,
            "exit": 0,
            "sig": "0",
            "attempt": 1,
            "head": "",
            "duration": 0.0,
            "final": False,
            "ts": package.now(),
            "tree_fp": package.tree_fingerprint(tmp_path),
        },
    )
    result = plan.lint(tmp_path, SLUG)
    assert _runs(counter) == 1  # the lint consulted fresh evidence instead of running
    assert result["durations"].get("task-value") == 0.0
    assert all(gap["rule"] != "PLAN-001" or gap.get("task") != "task-value" for gap in result["gaps"])


def test_baseline_fp_recorded(tmp_path):
    repo(tmp_path)
    pkg = started_package(tmp_path, verify="true")
    records = _records(pkg)
    assert [r for r in records if r.get("kind") == "baseline" and r.get("task") == "task-value" and r.get("tree_fp")]

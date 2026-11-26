"""Offline tests for the zero-model overhead meter.

The public snapshot ships tests/ without bench/, so the whole module skips
itself there exactly like test_bench_speed.py does — which also means the
meter module is loaded lazily inside each test, never at collection time.
One real lifecycle run (plan-lint -> plan-go -> verify --all -> check in a
scratch repository) is kept behind SPEC_OVERHEAD_LIFECYCLE=1 because it
costs real seconds; the default section run only measures static surfaces,
which stays fast.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
METER = ROOT / "bench" / "overhead.py"
BUDGET = ROOT / "bench" / "overhead-budget.json"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.skipif(
    not METER.is_file(), reason="benchmark development tooling is absent in the public snapshot"
)


def _meter():
    spec = importlib.util.spec_from_file_location("bench_overhead_under_test", METER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def overhead():
    return _meter()


def run_meter(*args: str, timeout: float = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(METER), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=timeout,
    )


def test_fast_sections_emit_one_json_document_with_int_byte_metrics(overhead, tmp_path):
    # A budget that only covers the requested sections keeps the run green;
    # the shipped budget covers every section and would flag the rest missing.
    budgets = {key: 1 << 30 for key in (*overhead.SECTION_KEYS["surface"], *overhead.SECTION_KEYS["bytes"])}
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"schema_version": 1, "budgets": budgets}), encoding="utf-8")
    result = run_meter("--budget", str(budget), "--runs", "3", "--sections", "surface,bytes")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["host"]["python"].startswith("3.")
    assert set(payload["metrics"]) == set(overhead.SECTION_KEYS["surface"]) | set(overhead.SECTION_KEYS["bytes"])
    assert all(isinstance(payload["metrics"][key], int) for key in overhead.SECTION_KEYS["bytes"])
    assert payload["violations"] == []
    assert set(payload["budgets"]) == set(budgets)


def test_byte_metrics_measure_the_active_package(overhead, tmp_path):
    # Regression: bytes must be read while the package is active (post
    # plan-go). A delivered/archived package emits different guidance.
    budgets = {key: 1 << 30 for key in overhead.SECTION_KEYS["bytes"]}
    budget_path = tmp_path / "budget.json"
    budget_path.write_text(json.dumps({"schema_version": 1, "budgets": budgets}), encoding="utf-8")
    result = run_meter("--budget", str(budget_path), "--runs", "1", "--sections", "bytes")
    assert result.returncode == 0, result.stderr
    metrics = json.loads(result.stdout)["metrics"]
    assert metrics["hook_bytes_prompt"] > 0
    assert metrics["hook_bytes_start"] > 0
    assert metrics["hook_bytes_session_total"] > metrics["hook_bytes_start"]


def test_full_run_with_shipped_budget_exits_zero():
    result = run_meter("--runs", "1", timeout=600)
    payload = json.loads(result.stdout)
    assert result.returncode == 0, (result.stderr, payload["violations"])
    assert payload["violations"] == []
    assert payload["metrics"]["quick_calls"] == 2
    assert payload["metrics"]["quick_output_bytes"] < 512


def test_budget_violation_exits_one_and_names_the_metric(overhead, tmp_path):
    metric = next(iter(overhead.SECTION_KEYS["bytes"]))
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"schema_version": 1, "budgets": {metric: 1}}), encoding="utf-8")
    result = run_meter("--budget", str(budget), "--runs", "1", "--sections", "bytes")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert any(item["metric"] == metric and item["kind"] == "exceeded" for item in payload["violations"])


def test_budgeted_metric_missing_when_section_not_requested(tmp_path):
    budget = tmp_path / "budget.json"
    budget.write_text(
        json.dumps({"schema_version": 1, "budgets": {"python_startup_ms_median": 100000}}),
        encoding="utf-8",
    )
    result = run_meter("--budget", str(budget), "--runs", "1", "--sections", "surface")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["violations"] == [{"metric": "python_startup_ms_median", "kind": "missing", "value": None}]


def test_malformed_budget_exits_two(tmp_path):
    budget = tmp_path / "budget.json"
    budget.write_text("not json", encoding="utf-8")
    result = run_meter("--budget", str(budget), "--sections", "surface")
    assert result.returncode == 2
    assert result.stderr


def test_malformed_budget_value_exits_two(tmp_path):
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"budgets": {"hook_ms_x": "soon"}}), encoding="utf-8")
    result = run_meter("--budget", str(budget), "--sections", "surface")
    assert result.returncode == 2


def test_unknown_budget_key_exits_two(tmp_path):
    budget = tmp_path / "budget.json"
    budget.write_text(
        json.dumps({"schema_version": 1, "budgets": {"hook_bytes_promt": 100}}),
        encoding="utf-8",
    )
    result = run_meter("--budget", str(budget), "--sections", "surface")
    assert result.returncode == 2
    assert "hook_bytes_promt" in result.stderr


def test_shipped_budget_has_no_stale_keys_and_generous_limits(overhead):
    budgets = overhead.load_budgets(BUDGET)
    assert budgets, "the shipped budget must constrain at least one metric"
    assert set(budgets) <= overhead.metric_keys()
    for key, limit in budgets.items():
        assert isinstance(limit, (int, float)) and limit > 0, key
        assert overhead.budget_floor(key) is None or limit >= overhead.budget_floor(key), key


def test_metric_key_list_covers_every_section(overhead):
    keys = overhead.metric_keys()
    for section_keys in overhead.SECTION_KEYS.values():
        assert set(section_keys) <= keys
    assert "python_startup_ms_median" in keys


def test_hermetic_environment_carries_no_user_identity(overhead):
    env = overhead.hermetic_env(Path(tempfile.gettempdir()))
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_AUTHOR_NAME"] and env["GIT_COMMITTER_EMAIL"]
    for forbidden in ("GIT_DIR", "GIT_WORK_TREE", "SSH_AUTH_SOCK", "GIT_CONFIG_GLOBAL"):
        assert forbidden not in env


def test_scratch_cleanup_removes_everything(overhead, monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(overhead, "SCRATCH_PARENT", tmp_path / "scratch")
    scratch = overhead.Scratch()
    inner = scratch.root
    (inner / "file.txt").write_text("x", encoding="utf-8")
    scratch.close()
    assert not inner.exists()
    assert not (tmp_path / "scratch").exists()


def test_python_startup_metric_reports_a_median(overhead):
    value = overhead.measure_python_startup(2)
    assert value > 0


def test_ci_runs_the_meter_after_pytest():
    text = WORKFLOW.read_text(encoding="utf-8")
    pytest_step = text.index("- name: Run pytest")
    meter_step = text.index("- name: Overhead meter")
    assert meter_step > pytest_step
    remainder = text[meter_step:]
    next_step = remainder.find("\n      - name:", 1)
    step_body = remainder[:next_step] if next_step > 0 else remainder
    assert "run: python3 bench/overhead.py" in step_body


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for the lifecycle fixture")
@pytest.mark.skipif(
    os.environ.get("SPEC_OVERHEAD_LIFECYCLE") != "1",
    reason="real lifecycle run costs seconds; set SPEC_OVERHEAD_LIFECYCLE=1 to include it",
)
def test_full_meter_run_emits_lifecycle_metrics(overhead):
    result = run_meter("--runs", "1", timeout=600)
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    for key in overhead.SECTION_KEYS["lifecycle"]:
        assert key in payload["metrics"], key
        assert payload["metrics"][key] > 0

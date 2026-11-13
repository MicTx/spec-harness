"""Speed-budget pins for the benchmark acceptance path.

The regression suite's wall time is dominated by the benchmark harness: a
30-second version-probe stub and pytest startup inside every acceptance run.
These pins keep the fast stub, the autoload flag, and the representative e2e
matrix from silently regressing.

The public snapshot ships tests/ but excludes bench/ and the private
benchmark harness tests, so the pins skip themselves there and keep exported
suite collection clean while running fully in the development tree.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCORE_SOURCE = ROOT / "bench" / "score.py"
HARNESS_TEST_SOURCE = ROOT / "tests" / "test_benchmark_harness.py"

pytestmark = pytest.mark.skipif(
    not (SCORE_SOURCE.is_file() and HARNESS_TEST_SOURCE.is_file()),
    reason="benchmark development tooling is absent in the public snapshot",
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _score():
    return _load("bench_score_under_test", SCORE_SOURCE)


def _harness_tests():
    return _load("benchmark_harness_tests_under_test", HARNESS_TEST_SOURCE)


def test_timeout_stub_answers_version_first():
    body = _harness_tests().FAST_TIMEOUT_STUB_BODY

    assert "--version" in body
    assert "stub-claude 1.0 (test)" in body
    assert "sleep(5)" in body
    # The stub must stay silent before sleeping: partial usage output would
    # turn a killed process into fabricated token counts.
    assert "json" not in body


def test_acceptance_run_disables_plugin_autoload(tmp_path, monkeypatch):
    bench_score = _score()
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0, stdout="1 passed in 0.01s\n", stderr="")

    monkeypatch.setattr(bench_score.subprocess, "run", fake_run)

    code, tail = bench_score.run_acceptance(tmp_path, {"PATH": "/usr/bin"}, 30)

    assert code == 0
    assert "1 passed" in tail
    assert captured["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert captured["env"]["PATH"] == "/usr/bin"
    assert "no:cacheprovider" in captured["command"]
    assert bench_score.ACCEPTANCE_DEST in captured["command"]


def test_acceptance_run_keeps_caller_override(tmp_path, monkeypatch):
    bench_score = _score()
    captured = {}

    def fake_run(command, **kwargs):
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(bench_score.subprocess, "run", fake_run)

    bench_score.run_acceptance(tmp_path, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "0"}, 30)

    assert captured["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "0"


def test_e2e_matrix_is_representative():
    harness_tests = _harness_tests()
    bench_harness = harness_tests.bench_harness

    assert harness_tests.E2E_ARMS == ["solve", "fake_completion", "out_of_boundary"]
    assert len(harness_tests.E2E_SCENARIOS) == 3
    assert "s6-review-recall" in harness_tests.E2E_SCENARIOS
    matrix = bench_harness.build_matrix(
        bench_harness.discover_scenario(harness_tests.E2E_SCENARIOS, harness_tests.SCENARIOS_DIR),
        harness_tests.E2E_ARMS,
        1,
    )
    assert len(matrix) == 9
    discovered = bench_harness.discover_scenario(list(harness_tests.ALL_SEVEN_SCENARIOS), harness_tests.SCENARIOS_DIR)
    assert {scenario.name for scenario in discovered} == set(harness_tests.ALL_SEVEN_SCENARIOS)

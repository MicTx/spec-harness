"""Bounded, convergent verify evidence: noisy reruns converge, distinct failures hit a hard cap.

Loop-model lessons applied to the verify/repair loop: halt when successive
iterations stop changing (stable failure signatures), keep a hard iteration
cap because more iterations can make things worse (task failure limit and
continuation chain cap), and re-inject the original goal when the loop is
told to change strategy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from package import failure_signature  # noqa: E402

from tests.v3_support import SLUG, cli, repo, started_package

COUNTER = "n=$(cat .git/n 2>/dev/null || echo 0); echo $((n+1)) > .git/n"
# Same failure every run; only timing, temp paths, addresses and clocks move.
NOISY = (
    "test ! -e fail-marker || { "
    + COUNTER
    + '; echo "FAILED t.py::a - assert 1 == 2 at 0x7f00000$n"; '
    + 'echo "1 failed in 0.0${n}s /tmp/pytest-of-u/pytest-$n/t0 12:00:0$n" >&2; exit 1; }'
)
# A genuinely different failure every run: the loop never converges by itself.
DISTINCT = "test ! -e fail-marker || { " + COUNTER + '; echo "AssertionError: case $n diverged"; exit 1; }'


def _rows(package: Path) -> list[dict]:
    return [json.loads(line) for line in (package / "runs.jsonl").read_text().splitlines()]


def _failures(package: Path) -> list[dict]:
    return [row for row in _rows(package) if row.get("kind") == "run" and row.get("exit")]


def _failing(tmp_path: Path, verify: str) -> Path:
    repo(tmp_path)
    package = started_package(tmp_path, verify=verify)
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    return package


# --- signature: stable across noise, distinct across real differences ---


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (b"1 failed in 0.04s\n", b"1 failed in 12.31s\n"),
        (b"took 15ms", b"took 1203ms"),
        (b"at 2026-11-27T10:00:01.123Z boom", b"at 2026-11-28 23:59:59+08:00 boom"),
        (b"[10:00:01] boom", b"[23:59:59.120] boom"),
        (b"<obj at 0x7f3a2c1b9e80>", b"<obj at 0x10a2b3c4d>"),
        (b"/tmp/pytest-of-u/pytest-3/t0/x.py:4", b"/tmp/pytest-of-u/pytest-41/t0/x.py:4"),
        (b"/var/folders/aa/fixture1/T/tmpq1/x.py:4", b"/var/folders/zz/fixture2/T/tmp9z/x.py:4"),
        (b"worker pid=1234 died", b"worker pid=98 died"),
    ],
)
def test_signature_ignores_volatile_output(first, second):
    assert failure_signature(first, b"") == failure_signature(second, b"")
    assert failure_signature(b"", first) == failure_signature(b"", second)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (b"assert 1 == 2", b"assert 1 == 3"),
        (b"AssertionError: case 1", b"AssertionError: case 2"),
        (b"KeyError: 'name'", b"KeyError: 'email'"),
        (b"tests/test_a.py:4: error", b"tests/test_a.py:5: error"),
    ],
)
def test_signature_keeps_real_differences(first, second):
    assert failure_signature(first, b"") != failure_signature(second, b"")


def test_signature_shape_streams_and_tail():
    signature = failure_signature(b"out", b"err")
    assert re.fullmatch(r"[0-9a-f]{16}", signature)
    assert signature == failure_signature(b"out", b"err")
    assert failure_signature(b"A", b"B") != failure_signature(b"B", b"A")
    # Noise growing in front of the tail must not shift the compared window.
    tail = b"E assert 1 == 2\n" * 300
    assert failure_signature(b"x 0.1s\n" + tail, b"") == failure_signature(b"x 10.25s\n" + tail, b"")
    assert failure_signature(b"head\n" + tail + b"end A", b"") != failure_signature(tail + b"end B", b"")


# --- task loop: noisy repeats converge, distinct failures hit the hard cap ---


def test_noisy_repeated_failure_converges_and_blocks_on_fourth(tmp_path):
    package = _failing(tmp_path, NOISY)
    directives = []
    for _ in range(4):
        result = cli(tmp_path, "verify", "task-value")
        assert result.returncode == 1
        directives.append(_failures(package)[-1]["directive"])
    assert len({row["sig"] for row in _failures(package)}) == 1
    assert "smallest fix" in directives[0]
    assert "minimal reproduction" in directives[1]
    assert "change implementation path" in directives[2]
    assert "park the task" in directives[3]
    assert "- [!] Change value" in (package / "tasks.md").read_text()


def test_distinct_failures_escalate_and_stop_at_the_task_limit(tmp_path):
    import evidence

    package = _failing(tmp_path, DISTINCT)
    limit = evidence.TASK_FAILURE_LIMIT
    for attempt in range(1, limit + 1):
        assert cli(tmp_path, "verify", "task-value").returncode == 1
        blocked = "- [!] Change value" in (package / "tasks.md").read_text()
        assert blocked == (attempt == limit), attempt
    rows = _failures(package)
    assert len({row["sig"] for row in rows}) == limit
    assert "smallest fix" in rows[0]["directive"]
    assert "minimal reproduction" in rows[2]["directive"]
    assert "park the task" in rows[-1]["directive"]
    rejected = cli(tmp_path, "verify", "task-value")
    assert rejected.returncode == 1 and "blocked" in rejected.stderr
    assert len(_failures(package)) == limit


def test_unblock_restarts_the_failure_budget(tmp_path):
    import evidence

    package = _failing(tmp_path, DISTINCT)
    for _ in range(evidence.TASK_FAILURE_LIMIT):
        cli(tmp_path, "verify", "task-value")
    assert cli(tmp_path, "unblock", "task-value", "--reason", "new evidence").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    assert "- [ ] Change value" in (package / "tasks.md").read_text()
    assert "smallest fix" in _failures(package)[-1]["directive"]


def test_oscillating_failures_still_block(tmp_path):
    package = _failing(tmp_path, "test ! -e fail-marker || { " + COUNTER + '; echo "case $((n % 2))"; exit 1; }')
    for _ in range(7):
        cli(tmp_path, "verify", "task-value")
    assert len({row["sig"] for row in _failures(package)}) == 2
    assert "- [!] Change value" in (package / "tasks.md").read_text()


def test_failed_verify_reads_the_ledger_once(tmp_path, monkeypatch):
    import evidence

    _failing(tmp_path, NOISY)
    cli(tmp_path, "verify", "task-value")
    calls = []
    original = evidence.runs
    monkeypatch.setattr(evidence, "runs", lambda package: calls.append(1) or original(package))
    monkeypatch.chdir(tmp_path)
    result = evidence.run_tasks(tmp_path, SLUG, ["task-value"])
    assert result["results"][0]["exit"] == 1
    assert len(calls) == 1


def test_temp_paths_keep_the_failing_file_name():
    first = b"/tmp/pytest-of-u/pytest-3/t0/a.py:4: AssertionError"
    assert failure_signature(first, b"") == failure_signature(first.replace(b"pytest-3", b"pytest-9"), b"")
    assert failure_signature(first, b"") != failure_signature(first.replace(b"a.py", b"b.py"), b"")


def test_ladder_ignores_checkpoint_failures_and_holds_park_until_blocked():
    import evidence

    checkpoint = {"task": "t", "kind": "run", "exit": 1, "sig": "ok", "checkpoint_error": "denied"}
    assert evidence.ladder([checkpoint] * 10, "t", "ok") == (evidence.LADDER[0], False)
    distinct = [{"task": "t", "kind": "run", "exit": 1, "sig": str(n)} for n in range(evidence.TASK_FAILURE_LIMIT - 2)]
    directive, blocked = evidence.ladder(distinct, "t", "new")
    assert not blocked and directive == evidence.LADDER[2]
    assert evidence.ladder([*distinct, {"task": "other", "exit": 1, "sig": "x"}], "t", "new")[1] is False
    assert evidence.ladder([*distinct, {"task": "t", "exit": 1, "sig": "x"}], "t", "new") == (evidence.LADDER[3], True)


def test_ladder_counts_only_verify_runs_not_planning_probes():
    import evidence

    probes = [{"kind": "baseline", "task": "t", "exit": 1, "expect": "red"}] * 5
    run = {"kind": "run", "task": "t", "exit": 1, "sig": "a"}
    assert evidence.ladder([*probes, run], "t", "b") == (evidence.LADDER[0], False)
    legacy = {"task": "t", "exit": 1, "sig": "b"}
    assert evidence.ladder([legacy] * 3, "t", "b")[1] is True


# --- failure identity: exit reason and semantic paths stay distinct (C1) ---


def test_same_output_distinct_exit_codes_do_not_collapse(tmp_path):
    """Identical streams with different exit codes are different failures:
    the recorded identity must not merge 'assertion failed' with 'missing file'."""
    import evidence

    first, _, _ = evidence._execute(tmp_path, "echo boom >&2; exit 1")
    second, _, _ = evidence._execute(tmp_path, "echo boom >&2; exit 2")
    assert first["exit"] == 1 and second["exit"] == 2
    assert first["sig"] != second["sig"]


def test_timeout_is_distinct_from_a_process_that_returns_124(tmp_path, monkeypatch):
    """A killed-on-timeout run and a command that itself exits 124 share the
    numeric code but are different exit reasons; the identity must keep them
    apart instead of letting one masquerade as the other."""
    import subprocess

    import evidence

    returned, _, returned_stderr = evidence._execute(
        tmp_path, "printf 'partial out'; printf 'partial err' >&2; exit 124"
    )
    assert returned["exit"] == 124
    assert not returned_stderr.startswith("Spec verify timed out")

    def _timed_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"), output=b"partial out", stderr=b"partial err")

    monkeypatch.setattr(evidence.subprocess, "run", _timed_out)
    timeout_result, _, timeout_stderr = evidence._execute(tmp_path, "sleep 60")
    assert timeout_result["exit"] == 124
    assert timeout_stderr.startswith("Spec verify timed out")
    assert timeout_result["sig"] != returned["sig"]


@pytest.mark.parametrize(
    ("first", "second", "rerooted"),
    [
        # Same leaf name under different semantic temp subdirs (t0 vs t1 test dirs).
        (
            b"/tmp/pytest-of-u/pytest-3/t0/test_x.py:4: err",
            b"/tmp/pytest-of-u/pytest-3/t1/test_x.py:4: err",
            b"/tmp/pytest-of-u/pytest-77/t0/test_x.py:4: err",
        ),
        (
            b"/var/folders/aa/fixture/T/suite-a/test_x.py:4",
            b"/var/folders/aa/fixture/T/suite-b/test_x.py:4",
            b"/var/folders/zz/other99/T/suite-a/test_x.py:4",
        ),
    ],
)
def test_temp_root_noise_keeps_semantic_subdirectories_distinct(first, second, rerooted):
    """Only the run root (pytest-N, folders/XY, tmpXXXX) is runner metadata;
    the subdirectory below it names a real fixture and must not collapse."""
    assert failure_signature(first, b"") != failure_signature(second, b"")
    # Same subdir and leaf under a different run root still converges.
    assert failure_signature(first, b"") == failure_signature(rerooted, b"")


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # Assertion operands are failure data, not runner metadata.
        (b"assert 15ms == 20ms", b"assert 90ms == 20ms"),
        (b"assert elapsed took 1.5s", b"assert elapsed took 9.9s"),
        (b"assert 0xdeadbeef == 0x1234567", b"assert 0xaaaaaaa == 0x1234567"),
        (b"assert uid == 0x7f00000a", b"assert uid == 0x7f00000b"),
        (b"assert clock 12:00:01 == expected", b"assert clock 12:00:02 == expected"),
    ],
)
def test_assertion_operands_are_data_not_metadata(first, second):
    """Literals inside an assertion are the compared values: two failures that
    differ only in the asserted operand are distinct evidence, not one rerun."""
    assert failure_signature(first, b"") != failure_signature(second, b"")

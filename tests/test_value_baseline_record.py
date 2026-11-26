"""Pin the recorded Phase 1 real-model value baseline.

The record is data from one authorized real-model run (see bench/README.md).
These tests keep it consistent with its own summary and inside the authorized
budget. They skip when the record is absent, as in the public snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "bench" / "results" / "value-baseline.jsonl"
SUMMARY = ROOT / "bench" / "results" / "value-baseline.md"
BUDGETS = ROOT / "bench" / "budgets.json"
LABEL = "real-value-baseline"

pytestmark = pytest.mark.skipif(
    not (RUNS.is_file() and SUMMARY.is_file() and BUDGETS.is_file()),
    reason="benchmark record is absent in the public snapshot",
)


def _rows() -> list:
    return [json.loads(line) for line in RUNS.read_text(encoding="utf-8").splitlines() if line.strip()]


def _label() -> dict:
    return json.loads(BUDGETS.read_text(encoding="utf-8"))["authorization_labels"][LABEL]


def test_record_covers_exactly_the_authorized_matrix():
    matrix = _label()["allowed_matrix"]
    expected = {
        f"{scenario}::{arm}::r{repeat:02d}"
        for scenario in matrix["scenarios"]
        for arm in matrix["arms"]
        for repeat in range(1, matrix["repeats"] + 1)
    }
    units = [row["unit"] for row in _rows()]
    assert len(units) == len(set(units))
    assert set(units) == expected


def test_every_row_has_complete_host_usage():
    for row in _rows():
        assert row["usage_unknown"] is False, row["unit"]
        assert row["budget"]["label"] == LABEL, row["unit"]
        for field in ("tokens_in", "tokens_out", "tokens_cache", "tokens_cache_creation"):
            value = row[field]
            assert isinstance(value, int) and not isinstance(value, bool) and value >= 0, (row["unit"], field)
        assert isinstance(row["success"], bool) and isinstance(row["false_completion"], bool), row["unit"]
        assert row["wall_time_s"] > 0, row["unit"]


def test_totals_stay_inside_the_authorized_caps():
    caps = _label()["estimate_upper_bound"]
    rows = _rows()
    assert len(rows) <= caps["units"]
    # Cache creation is billed as input, so it counts against the input cap.
    assert sum(r["tokens_in"] + r["tokens_cache_creation"] for r in rows) <= caps["tokens_in"]
    assert sum(r["tokens_out"] for r in rows) <= caps["tokens_out"]
    assert sum(r["tokens_cache"] for r in rows) <= caps["cache_read"]
    assert sum(r["wall_time_s"] for r in rows) <= caps["wall_s"]


def test_record_holds_no_absolute_user_path():
    text = RUNS.read_text(encoding="utf-8") + SUMMARY.read_text(encoding="utf-8")
    for marker in ("/Users/", "/home/", "/private/", "/var/folders"):
        assert marker not in text, marker


def test_out_of_boundary_paths_are_spec_state_only():
    for row in _rows():
        paths = row["out_of_boundary_paths"]
        if row["arm"] == "native":
            assert paths == [], row["unit"]
        assert all(path.startswith(".spec/") for path in paths), row["unit"]


def test_summary_matches_the_rows():
    text = SUMMARY.read_text(encoding="utf-8")
    rows = _rows()
    for arm in ("native", "spec"):
        mine = [row for row in rows if row["arm"] == arm]
        assert f"{arm} {sum(row['success'] for row in mine)}/{len(mine)}" in text
        assert f"{arm} {sum(row['false_completion'] for row in mine)}/{len(mine)}" in text
        assert f"{sum(row['tokens_out'] for row in mine):,}" in text
    for row in rows:
        if not row["success"]:
            assert row["unit"] in text, row["unit"]

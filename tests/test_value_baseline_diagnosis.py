"""Pin the diagnostic rerun of the Phase 1 value baseline.

The rerun repeated the spec-arm units with the sandboxes kept. It showed that
Spec froze the work on a spec branch in a worktree while scoring read the main
checkout. These tests keep the stored rows, the summary and the README
consistent with that finding. They skip when the benchmark record is absent, as
in the public snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "bench" / "results"
BASELINE = RESULTS / "value-baseline.jsonl"
DIAGNOSIS = RESULTS / "value-baseline-diagnosis.jsonl"
SUMMARY = RESULTS / "value-baseline.md"
README = ROOT / "bench" / "README.md"

LABEL = "diag-spec-failures"
# Caps agreed for the diagnostic rerun; the label itself lived in a temporary manifest.
CAPS = {"units": 4, "tokens_in": 400000, "tokens_out": 120000, "cache_read": 4000000, "wall_s": 5400}
UNITS = {
    "s1-simple-fix::spec::r01",
    "s1-simple-fix::spec::r02",
    "s7-backend-unavailable::spec::r01",
    "s7-backend-unavailable::spec::r02",
}
FAILED = {
    "s1-simple-fix::spec::r01",
    "s1-simple-fix::spec::r02",
    "s7-backend-unavailable::spec::r01",
}
STALE_WORDING = ("原因未定", "没有更可靠")
PATH_MARKERS = ("/Users/", "/home/", "/private/", "/var/folders", "/tmp/")

pytestmark = pytest.mark.skipif(not BASELINE.is_file(), reason="benchmark record is absent in the public snapshot")


def _rows(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _diagnosis() -> list:
    assert DIAGNOSIS.is_file(), "diagnostic rows must be stored next to the baseline"
    return _rows(DIAGNOSIS)


def _section(text: str, title: str) -> str:
    parts = text.split(title, 1)
    assert len(parts) == 2, f"missing section {title}"
    return parts[1].split("\n## ", 1)[0]


def _table_row(text: str, unit: str) -> list:
    for line in text.splitlines():
        if line.startswith("|") and f" {unit} " in line:
            return [cell.strip() for cell in line.strip().strip("|").split("|")]
    raise AssertionError(f"no table row for {unit}")


def test_diagnosis_rows_are_the_four_spec_units():
    rows = _diagnosis()
    assert len(rows) == len(UNITS)
    assert {row["unit"] for row in rows} == UNITS
    for row in rows:
        assert row["arm"] == "spec", row["unit"]
        assert row["installed"] is True and row["hooks_installed"] is True, row["unit"]
        assert row["usage_unknown"] is False, row["unit"]
        assert row["budget"]["label"] == LABEL, row["unit"]


def test_diagnostic_usage_stays_inside_the_agreed_caps():
    rows = _diagnosis()
    assert len(rows) <= CAPS["units"]
    # Cache creation is billed as input, so it counts against the input cap.
    assert sum(r["tokens_in"] + r["tokens_cache_creation"] for r in rows) <= CAPS["tokens_in"]
    assert sum(r["tokens_out"] for r in rows) <= CAPS["tokens_out"]
    assert sum(r["tokens_cache"] for r in rows) <= CAPS["cache_read"]
    assert sum(r["wall_time_s"] for r in rows) <= CAPS["wall_s"]


def test_diagnostic_record_holds_no_absolute_path():
    text = (DIAGNOSIS.read_text(encoding="utf-8") if DIAGNOSIS.is_file() else "") + SUMMARY.read_text(encoding="utf-8")
    for marker in PATH_MARKERS:
        assert marker not in text, marker


def test_failed_units_passed_acceptance_on_the_spec_branch():
    rows = _diagnosis()
    assert {row["unit"] for row in rows if not row["success"]} == FAILED
    section = _section(SUMMARY.read_text(encoding="utf-8"), "## 诊断重跑")
    for row in rows:
        cells = _table_row(section, row["unit"])
        assert ("失败" in cells[1]) == (not row["success"]), row["unit"]
        if row["success"]:
            assert row["out_of_boundary_paths"] == [], row["unit"]
        else:
            assert row["out_of_boundary_paths"] == [".spec/charter.md"], row["unit"]
            assert "通过" in cells[2] and "失败" not in cells[2], row["unit"]
            assert "失败" in cells[3], row["unit"]


def test_summary_reports_both_outcomes():
    rows = _diagnosis()
    section = _section(SUMMARY.read_text(encoding="utf-8"), "## 诊断重跑")
    scored = sum(row["success"] for row in rows)
    assert f"按评分 {scored}/{len(rows)}" in section
    # Every failed row is shown above as passing on the branch, the rest never used a branch.
    assert f"按分支 {len(rows)}/{len(rows)}" in section


def test_original_failures_are_marked_unverified():
    section = _section(SUMMARY.read_text(encoding="utf-8"), "## 未解决")
    original = [row["unit"] for row in _rows(BASELINE) if row["arm"] == "spec" and not row["success"]]
    assert len(original) == 2
    for unit in original:
        assert unit in section, unit
    assert "吻合" in section and "未验证" in section


def test_stale_wording_is_gone():
    for path in (SUMMARY, README):
        text = path.read_text(encoding="utf-8")
        for phrase in STALE_WORDING:
            assert phrase not in text, (path.name, phrase)


def test_readme_points_at_the_diagnosis():
    assert "value-baseline-diagnosis.jsonl" in README.read_text(encoding="utf-8")
    assert "value-baseline-diagnosis.jsonl" in SUMMARY.read_text(encoding="utf-8")

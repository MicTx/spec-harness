"""Delivered ledgers must end with a passing final gate record."""

from __future__ import annotations

import json
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo


def _gates(package: Path) -> list[dict]:
    rows = [
        json.loads(line) for line in (package / "runs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return [row for row in rows if row.get("kind") == "gate"]


def test_delivered_ledger_ends_with_passing_final_gate(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0
    failed = cli(tmp_path, "check", "--check-only")
    assert failed.returncode == 1
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "check").returncode == 0
    archive = tmp_path / ".spec" / "specs" / "archive" / SLUG
    gates = _gates(archive)
    assert gates[-1]["passed"] is True
    assert gates[-1].get("final") is True
    assert gates[-1]["errors"] == []
    assert any(gate["passed"] is False for gate in gates)


def test_direct_done_records_final_gate(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "done").returncode == 0
    archive = tmp_path / ".spec" / "specs" / "archive" / SLUG
    gates = _gates(archive)
    assert gates[-1]["passed"] is True
    assert gates[-1].get("final") is True

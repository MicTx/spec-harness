"""Route suggestion surface: next exposes planning refinements for unfrozen plans."""

import json

from tests.v3_support import cli, make_package, repo


def test_next_surfaces_suggestions_for_trap_plan(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    pair = cli(
        tmp_path,
        "task-card",
        "--tdd",
        "--id",
        "task-trap",
        "--boundary",
        "app.py",
        "--test-boundary",
        "tests/test_trap.py",
        "--append",
        "--criterion",
        "Trap covered",
    )
    assert pair.returncode == 0, pair.stdout + pair.stderr
    card = json.loads(cli(tmp_path, "next").stdout)
    assert any(s["rule"] == "SUGGEST-001" and s["task"] == "task-trap-test" for s in card["suggestions"])
    assert "plan-lint --suggest" in card["next"]


def test_next_clean_plan_has_no_suggestions(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    card = json.loads(cli(tmp_path, "next").stdout)
    assert card["suggestions"] == []
    assert "--suggest" not in card["next"]


def test_frozen_plan_has_no_suggestions(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0
    card = json.loads(cli(tmp_path, "next").stdout)
    assert card["suggestions"] == []
    assert "--suggest" not in card["next"]

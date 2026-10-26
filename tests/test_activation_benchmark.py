"""Activation benchmark (task-activation-eval): stub units only, zero model spend.

Proves the SCORER and the GATES, never model behavior:
- no authorization -> zero model calls (the runner never even builds a command)
- a budget overrun stops the batch honestly
- the skill appearing AFTER the first implementation fails the unit
- a pre-injected discipline block never counts as proactive adoption
- a fabricated team state never counts as live
- a functional failure is a failure whatever the adoption record says
- ``verify`` demands the exact matrix, current tree, real host, and no mock
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "scripts"))
import activation as act  # noqa: E402

SCRIPT = ROOT / "bench" / "activation.py"
BASE = "20665b4640caab0ed5665744e3e43b8b94863ffb"


def events(*names):
    return [{"seq": i, "kind": kind, **({"tool": tool} if tool else {})} for i, (kind, tool) in enumerate(names)]


SKILL = ("skill_invoked", "Skill")
RECORD = ("package_recorded", None)
READ = ("tool_use", "Read")
EDIT = ("tool_use", "Edit")
DISPATCH = ("tool_use", "Agent")


def unit(scenario="P1", arm="candidate", **overrides):
    record = {
        "unit": f"{scenario}::{arm}::r01",
        "scenario": scenario,
        "arm": arm,
        "repeat": 1,
        "host": "claude-code",
        "host_version": "2.1.296",
        "model": "m-1",
        "mock": False,
        "revision": "a" * 40,
        "tree_fp": "t" * 16,
        "prompt_has_discipline": False,
        "team_source": "host",
        "events": events(READ, SKILL, RECORD, EDIT),
        "functional_pass": True,
        "false_positive_package": False,
        "out_of_scope_writes": 0,
        "tokens_in": 10,
        "tokens_out": 5,
        "wall_s": 3.0,
    }
    record.update(overrides)
    return record


# --- scoring: event order ----------------------------------------------------------


def test_adoption_before_the_first_edit_counts_for_a_positive_scenario():
    score = act.score_unit(unit("P1"))
    assert score["adopted_before_implementation"] is True
    assert score["passed"] is True


def test_skill_after_the_first_edit_fails_the_unit():
    late = unit("P1", events=events(READ, EDIT, SKILL, RECORD))
    score = act.score_unit(late)
    assert score["adopted_before_implementation"] is False
    assert score["passed"] is False and "adopted_late" in score["failures"]


def test_dispatch_counts_as_implementation_for_ordering():
    late = unit("P2", events=events(DISPATCH, SKILL, RECORD))
    assert act.score_unit(late)["adopted_before_implementation"] is False


def test_read_only_exploration_is_not_implementation():
    ok = unit("P1", events=events(READ, READ, SKILL, RECORD, EDIT))
    assert act.score_unit(ok)["adopted_before_implementation"] is True


def test_skill_call_without_a_record_is_not_adoption():
    only_skill = unit("P1", events=events(READ, SKILL, EDIT))
    score = act.score_unit(only_skill)
    assert score["adopted_before_implementation"] is False and "no_record" in score["failures"]


def test_a_skill_call_count_is_never_a_success_signal():
    noisy = unit("P1", events=events(SKILL, SKILL, SKILL, EDIT), functional_pass=True)
    assert act.score_unit(noisy)["passed"] is False


# --- scoring: honesty --------------------------------------------------------------


def test_preinjected_discipline_does_not_count_as_proactive():
    injected = unit("P1", prompt_has_discipline=True)
    score = act.score_unit(injected)
    assert score["passed"] is False and "prompt_not_clean" in score["failures"]


def test_fabricated_team_state_never_counts_as_live():
    fake = unit("P2", team_source="prompt-text")
    score = act.score_unit(fake)
    assert score["passed"] is False and "team_not_host_created" in score["failures"]


def test_functional_failure_fails_regardless_of_adoption():
    broken = unit("P1", functional_pass=False)
    score = act.score_unit(broken)
    assert score["adopted_before_implementation"] is True
    assert score["passed"] is False and "functional_failure" in score["failures"]


def test_out_of_scope_writes_fail_the_unit():
    score = act.score_unit(unit("P1", out_of_scope_writes=2))
    assert score["passed"] is False and "out_of_scope_writes" in score["failures"]


# --- negatives: no false positives ---------------------------------------------------


@pytest.mark.parametrize("scenario", ["N1", "N4", "N5"])
def test_a_negative_scenario_passes_without_a_package(scenario):
    clean = unit(scenario, events=events(READ, EDIT))
    assert act.score_unit(clean)["passed"] is True


@pytest.mark.parametrize("scenario", ["N1", "N4", "N5"])
def test_a_negative_scenario_that_creates_a_package_fails(scenario):
    noisy = unit(scenario, events=events(SKILL, RECORD, EDIT), false_positive_package=True)
    score = act.score_unit(noisy)
    assert score["passed"] is False and "false_positive" in score["failures"]


def test_resume_scenario_requires_reuse_not_a_second_package():
    ok = unit("P3", events=events(READ, SKILL, EDIT), package_action="resumed")
    dup = unit("P3", events=events(READ, SKILL, RECORD, EDIT), package_action="created_duplicate")
    assert act.score_unit(ok)["passed"] is True
    assert act.score_unit(dup)["passed"] is False


# --- matrix and verify --------------------------------------------------------------


def matrix_runs(**overrides):
    runs = []
    for scenario in ("P1", "P2", "P3", "N1", "N4", "N5"):
        for arm in ("baseline", "candidate"):
            for repeat in (1, 2):
                base = unit(scenario, arm, repeat=repeat, unit=f"{scenario}::{arm}::r{repeat:02d}")
                if scenario.startswith("N"):
                    base["events"] = events(READ, EDIT)
                if scenario == "P3":
                    base["events"] = events(READ, SKILL, EDIT)
                    base["package_action"] = "resumed"
                if arm == "baseline":
                    base["revision"] = BASE
                runs.append({**base, **overrides})
    return runs


def write_runs(path, runs):
    path.write_text("".join(json.dumps(r) + "\n" for r in runs), encoding="utf-8")


def verify(tmp_path, runs, *extra, tree="t" * 16):
    path = tmp_path / "runs.jsonl"
    write_runs(path, runs)
    return act.verify_runs(
        path,
        require_live=True,
        baseline_revision=BASE,
        current_tree=tree,
        expected_units=24,
        **{},
    )


def test_full_matrix_has_24_units_and_the_exact_scenarios():
    units = act.build_matrix()
    assert len(units) == 24
    assert {u["scenario"] for u in units} == {"P1", "P2", "P3", "N1", "N4", "N5"}
    assert {u["arm"] for u in units} == {"baseline", "candidate"}
    assert all(
        sum(1 for u in units if u["scenario"] == s and u["arm"] == a) == 2
        for s in {"P1", "N1"}
        for a in {"baseline", "candidate"}
    )


def test_verify_passes_only_on_a_complete_clean_live_matrix(tmp_path):
    ok, problems = verify(tmp_path, matrix_runs())
    assert ok is True, problems


@pytest.mark.parametrize(
    "mutation,needle",
    [
        (lambda runs: runs[:-1], "24"),
        (lambda runs: [{**r, "mock": True} for r in runs], "mock"),
        (lambda runs: [{**r, "host": "stub"} for r in runs], "host"),
        (lambda runs: [{**r, "tree_fp": "old"} if r["arm"] == "candidate" else r for r in runs], "tree"),
        (lambda runs: [{**r, "revision": "f" * 40} if r["arm"] == "baseline" else r for r in runs], "baseline"),
        (lambda runs: runs + [runs[0]], "duplicate"),
    ],
    ids=["missing-unit", "mock", "stub-host", "stale-tree", "wrong-baseline", "duplicate-unit"],
)
def test_verify_rejects_incomplete_or_untrustworthy_runs(tmp_path, mutation, needle):
    ok, problems = verify(tmp_path, mutation(matrix_runs()))
    assert ok is False
    assert any(needle in problem for problem in problems), problems


def test_live_verify_rejects_missing_usage_even_if_baseline_functionally_passed(tmp_path):
    rows = matrix_runs()
    victim = next(row for row in rows if row["unit"] == "P2::baseline::r01")
    victim["usage_unknown"] = True
    ok, problems = verify(tmp_path, rows)
    assert ok is False and any("usage" in problem for problem in problems)


def test_live_verify_rejects_a_baseline_route_error_even_if_functional(tmp_path):
    rows = matrix_runs()
    victim = next(row for row in rows if row["unit"] == "P2::baseline::r02")
    victim["notes"] = 'stderr:[claude-code:unrecognized_model] {"model":"glm-5.3"}'
    ok, problems = verify(tmp_path, rows)
    assert ok is False and any("unrecognized_model" in problem for problem in problems)


@pytest.mark.parametrize("arm", ["baseline", "candidate"])
@pytest.mark.parametrize("flag", ["source_escape", "source_audit_unknown"])
def test_live_verify_rejects_source_boundary_flags_even_if_count_is_zero(tmp_path, arm, flag):
    rows = matrix_runs()
    victim = next(row for row in rows if row["unit"] == f"P1::{arm}::r01")
    victim[flag] = True
    victim["out_of_scope_writes"] = 0
    ok, problems = verify(tmp_path, rows)
    assert ok is False and any("source boundary" in problem for problem in problems)


def test_completed_work_after_a_host_timeout_is_not_a_valid_candidate(tmp_path):
    rows = matrix_runs()
    victim = next(row for row in rows if row["unit"] == "P2::candidate::r01")
    victim["host_error"] = True
    ok, problems = verify(tmp_path, rows)
    assert ok is False and any("host_error" in problem for problem in problems)


def test_verify_requires_every_candidate_positive_to_adopt_in_time(tmp_path):
    runs = matrix_runs()
    victim = next(r for r in runs if r["arm"] == "candidate" and r["scenario"] == "P1")
    victim["events"] = events(READ, EDIT, SKILL, RECORD)
    ok, problems = verify(tmp_path, runs)
    assert ok is False and any("P1" in p for p in problems)


def test_verify_requires_every_candidate_negative_to_stay_clean(tmp_path):
    runs = matrix_runs()
    victim = next(r for r in runs if r["arm"] == "candidate" and r["scenario"] == "N4")
    victim.update(false_positive_package=True, events=events(SKILL, RECORD, EDIT))
    ok, problems = verify(tmp_path, runs)
    assert ok is False and any("N4" in p for p in problems)


def test_baseline_results_are_reported_not_assumed_to_fail(tmp_path):
    runs = matrix_runs()
    for r in runs:
        if r["arm"] == "baseline" and r["scenario"].startswith("P"):
            r["events"] = events(READ, EDIT)
            r["package_action"] = "none"
    ok, problems = verify(tmp_path, runs)
    assert ok is True, problems  # baseline outcomes never gate the verdict


def test_malformed_lines_are_a_failure_not_a_skip(tmp_path):
    path = tmp_path / "runs.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    ok, problems = act.verify_runs(
        path, require_live=True, baseline_revision=BASE, current_tree="t" * 16, expected_units=24
    )
    assert ok is False and any("invalid" in p for p in problems)


def test_missing_file_fails_cleanly(tmp_path):
    ok, problems = act.verify_runs(
        tmp_path / "none.jsonl", require_live=True, baseline_revision=BASE, current_tree="t", expected_units=24
    )
    assert ok is False and problems


# --- gates: zero model calls without authorization ----------------------------------


def test_run_without_authorization_makes_zero_model_calls(tmp_path):
    calls = []
    result = act.run_matrix(tmp_path / "out.jsonl", authorization=None, executor=lambda cmd, **kw: calls.append(cmd))
    assert result["exit"] == 2 and calls == []
    assert not (tmp_path / "out.jsonl").exists()


def test_run_with_an_unknown_authorization_makes_zero_model_calls(tmp_path):
    calls = []
    result = act.run_matrix(
        tmp_path / "out.jsonl", authorization="no-such-label", executor=lambda cmd, **kw: calls.append(cmd)
    )
    assert result["exit"] == 2 and calls == []


def test_dry_run_lists_units_and_never_calls_a_model(tmp_path):
    calls = []
    result = act.run_matrix(
        tmp_path / "out.jsonl", authorization=None, dry_run=True, executor=lambda cmd, **kw: calls.append(cmd)
    )
    assert result["exit"] == 0 and len(result["units"]) == 24 and calls == []


def test_run_only_selects_named_units_and_refuses_unknown_ones(tmp_path):
    wanted = "P1::candidate::r01,P3::candidate::r02"
    args = [
        sys.executable,
        str(SCRIPT),
        "run",
        "--out",
        str(tmp_path / "o.jsonl"),
        "--authorize-budget",
        "no-such-label",
        "--dry-run",
        "--only",
        wanted,
    ]
    completed = subprocess.run(args, capture_output=True, text=True, timeout=60)
    listed = json.loads(completed.stdout)
    assert completed.returncode == 0 and listed["units"] == ["P1::candidate::r01", "P3::candidate::r02"], listed
    bad = subprocess.run(
        args[:-1] + ["P1::candidate::r01,P9::candidate::r09"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert bad.returncode != 0 and "P9::candidate::r09" in bad.stdout


def test_budget_overrun_stops_the_batch_and_keeps_written_rows(tmp_path):
    budgets = {"label": "t", "caps": {"units": 24, "tokens_in": 25, "tokens_out": 1000, "wall_s": 10_000}}

    def executor(cmd, **kw):
        return unit(tokens_in=10)

    result = act.run_matrix(tmp_path / "out.jsonl", authorization="t", executor=executor, budgets=budgets)
    assert result["exit"] == 4
    rows = [json.loads(line) for line in (tmp_path / "out.jsonl").read_text().splitlines()]
    assert 0 < len(rows) < 24


@pytest.mark.parametrize(
    "metric,row_key",
    [
        ("tokens_in", "tokens_in_uncached"),
        ("tokens_out", "tokens_out"),
        ("wall_s", "wall_s"),
    ],
)
@pytest.mark.parametrize("unit_count", [1, 2])
@pytest.mark.parametrize("total", [10, 11])
def test_terminal_unit_checks_every_budget_cap(tmp_path, metric, row_key, unit_count, total):
    caps = {"units": unit_count, "tokens_in": 10, "tokens_out": 10, "wall_s": 10}
    selected = act.build_matrix()[:unit_count]
    emitted = []

    def executor(unit_def):
        row = {"unit": unit_def["unit"], "tokens_in_uncached": 1, "tokens_out": 1, "wall_s": 1.0}
        if len(emitted) == unit_count - 1:
            row[row_key] = total - (unit_count - 1)
        emitted.append(row)
        return row

    out = tmp_path / "terminal.jsonl"
    result = act.run_matrix(
        out,
        authorization="test",
        budgets={"label": "test", "caps": caps},
        executor=executor,
        units=selected,
    )
    assert len(emitted) == unit_count
    assert [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()] == emitted
    assert result["used"][metric] == total
    if total > caps[metric]:
        assert result["exit"] == act.EXIT_BUDGET_STOP
        assert result["exceeded"] == [metric]
        assert result["stopped_after"] == unit_count
    else:
        assert result["exit"] == act.EXIT_OK
        assert result["written"] == unit_count


@pytest.mark.parametrize(
    "label",
    [
        "live-activation-sweep5",
        "live-activation-sweep6",
        "live-activation-sweep7",
        "live-activation-sweep8",
    ],
)
def test_authorized_sweep_runs_a_fresh_full_matrix(tmp_path, label):
    assert act.AUTHORIZATIONS[label]["caps"] == {
        "units": 24,
        "tokens_in": 42_000_000,
        "tokens_out": 480_000,
        "wall_s": 28_800,
    }
    dispatched = []

    def executor(unit_def):
        dispatched.append(unit_def["unit"])
        return {"unit": unit_def["unit"], "tokens_in_uncached": 1, "tokens_out": 1, "wall_s": 1}

    out = tmp_path / f"{label}.jsonl"
    result = act.run_matrix(out, authorization=label, executor=executor)
    assert result["exit"] == 0
    assert dispatched == [unit["unit"] for unit in act.build_matrix()]
    assert len(out.read_text(encoding="utf-8").splitlines()) == 24


@pytest.mark.parametrize("label", ["live-activation-sweep7", "live-activation-sweep8"])
def test_full_sweep_authorizes_matrix_only_not_a_probe(tmp_path, label):
    seen = []

    def executor(unit):
        seen.append(unit["unit"])
        return {"unit": unit["unit"], "tokens_in_uncached": 1, "tokens_out": 1, "wall_s": 1}

    out = tmp_path / "sweep7.jsonl"
    for selected in (
        [],
        act.build_matrix()[:1],
        [unit for unit in act.build_matrix() if unit["unit"] == "P2::candidate::r01"],
    ):
        result = act.run_matrix(out, authorization=label, executor=executor, units=selected)
        assert result["exit"] == act.EXIT_REFUSED and not out.exists() and seen == []
        assert act.run_matrix(out, authorization=label, units=selected, dry_run=True)["exit"] == act.EXIT_REFUSED

    assert (
        act.main(
            [
                "run",
                "--out",
                str(out),
                "--authorize-budget",
                label,
                "--dry-run",
                "--only",
                "P2::candidate::r01",
            ]
        )
        == act.EXIT_REFUSED
    )
    assert seen == [] and not out.exists()


def test_p1_diagnostic_label_authorizes_only_the_two_candidate_units(tmp_path):
    label = "live-activation-p1-diagnostic"
    wanted = ["P1::candidate::r01", "P1::candidate::r02"]
    assert act.AUTHORIZATIONS[label]["caps"] == {
        "units": 2,
        "tokens_in": 2_000_000,
        "tokens_out": 40_000,
        "wall_s": 1800,
    }
    selected = [unit for unit in act.build_matrix() if unit["unit"] in wanted]
    called = []

    def executor(unit_def):
        called.append(unit_def["unit"])
        return {"unit": unit_def["unit"], "tokens_in_uncached": 10, "tokens_out": 1, "wall_s": 1}

    out = tmp_path / "diagnostic.jsonl"
    for wrong in (None, act.build_matrix()[:2], selected[:1], selected + [act.build_matrix()[0]]):
        result = act.run_matrix(out, authorization=label, executor=executor, units=wrong)
        assert result["exit"] == act.EXIT_REFUSED and not out.exists()
        assert called == []

    result = act.run_matrix(out, authorization=label, executor=executor, units=selected)
    assert result["exit"] == act.EXIT_OK
    assert called == wanted
    assert [json.loads(line)["unit"] for line in out.read_text(encoding="utf-8").splitlines()] == wanted


@pytest.mark.parametrize(
    "label,caps",
    [
        (
            "live-activation-p1-runtime-probe",
            {"units": 1, "tokens_in": 1_500_000, "tokens_out": 30_000, "wall_s": 1200},
        ),
        (
            "live-activation-p1-turn64-retest",
            {"units": 1, "tokens_in": 2_500_000, "tokens_out": 50_000, "wall_s": 1800},
        ),
    ],
)
def test_p1_runtime_probe_refuses_every_other_unit_before_host_launch(tmp_path, label, caps):
    wanted = "P1::candidate::r01"
    assert act.AUTHORIZATIONS[label]["caps"] == caps
    selected = [unit for unit in act.build_matrix() if unit["unit"] == wanted]
    other_repeat = [unit for unit in act.build_matrix() if unit["unit"] == "P1::candidate::r02"]
    calls = []

    def executor(unit_def):
        calls.append(unit_def["unit"])
        return {"unit": unit_def["unit"], "tokens_in_uncached": 1, "tokens_out": 1, "wall_s": 1}

    out = tmp_path / "one-unit.jsonl"
    for wrong in (None, [], act.build_matrix()[:1], other_repeat, selected + act.build_matrix()[:1]):
        result = act.run_matrix(out, authorization=label, executor=executor, units=wrong)
        assert result["exit"] == act.EXIT_REFUSED and not out.exists()
        assert calls == []
    assert act.run_matrix(out, authorization=label, units=selected, dry_run=True)["units"] == [wanted]
    result = act.run_matrix(out, authorization=label, executor=executor, units=selected)
    assert result["exit"] == act.EXIT_OK and calls == [wanted]
    assert [json.loads(line)["unit"] for line in out.read_text(encoding="utf-8").splitlines()] == [wanted]


def test_the_budget_is_charged_uncached_input_not_the_row_aggregate(tmp_path):
    """Sweep 1 stopped at 4/24 because the cap counted the wrong counter."""
    budgets = {"label": "t", "caps": {"units": 24, "tokens_in": 1000, "tokens_out": 10_000, "wall_s": 10_000}}

    def executor(cmd, **kw):
        # aggregate says 100, the provider billed 600 for this unit
        return {"unit": cmd["unit"], "tokens_in": 100, "tokens_in_uncached": 600, "tokens_out": 1, "wall_s": 1.0}

    result = act.run_matrix(tmp_path / "out.jsonl", authorization="t", executor=executor, budgets=budgets)
    assert result["exit"] == act.EXIT_BUDGET_STOP
    assert result["used"]["tokens_in"] == 1200  # two units x 600, not two x 100


def test_unknown_usage_stops_before_dispatching_another_unit(tmp_path):
    calls = []
    budgets = {"label": "t", "caps": {"units": 24, "tokens_in": 1000, "tokens_out": 1000, "wall_s": 10_000}}

    def executor(unit_def):
        calls.append(unit_def["unit"])
        return {
            "unit": unit_def["unit"],
            "tokens_in": 0,
            "tokens_out": 0,
            "wall_s": 900,
            "usage_unknown": True,
            "host_error": True,
        }

    out = tmp_path / "out.jsonl"
    result = act.run_matrix(out, authorization="t", executor=executor, budgets=budgets)
    assert result["exit"] == act.EXIT_BUDGET_STOP
    assert result["usage_unknown"] is True
    assert len(calls) == 1
    assert json.loads(out.read_text())["usage_unknown"] is True


def test_model_route_failure_stops_after_its_row_even_with_known_usage(tmp_path):
    calls = []
    budgets = {"label": "t", "caps": {"units": 24, "tokens_in": 1000, "tokens_out": 1000, "wall_s": 10_000}}

    def executor(unit_def):
        calls.append(unit_def["unit"])
        return {"unit": unit_def["unit"], "tokens_in_uncached": 10, "tokens_out": 1, "wall_s": 1.0, "route_error": True}

    out = tmp_path / "out.jsonl"
    result = act.run_matrix(out, authorization="t", executor=executor, budgets=budgets)
    assert result["exit"] == act.EXIT_FAILED
    assert result["route_error"] is True
    assert len(calls) == 1
    assert json.loads(out.read_text())["route_error"] is True


@pytest.mark.parametrize("flag", ["source_escape", "source_audit_unknown"])
def test_source_boundary_failure_stops_after_its_row_even_with_known_usage(tmp_path, flag):
    dispatched = []
    caps = {"units": 24, "tokens_in": 1000, "tokens_out": 1000, "wall_s": 10_000}

    def executor(unit_def):
        dispatched.append(unit_def["unit"])
        return {
            "unit": unit_def["unit"],
            "tokens_in_uncached": 10,
            "tokens_out": 1,
            "wall_s": 1.0,
            flag: True,
            "out_of_scope_writes": 1,
        }

    out = tmp_path / "out.jsonl"
    result = act.run_matrix(out, authorization="test", executor=executor, budgets={"label": "test", "caps": caps})
    assert result["exit"] == act.EXIT_FAILED
    assert len(dispatched) == 1
    assert json.loads(out.read_text())[flag] is True


def test_resume_carries_prior_uncached_input_into_the_cap(tmp_path):
    out = tmp_path / "out.jsonl"
    units = act.build_matrix()
    out.write_text(
        json.dumps(
            {
                "unit": units[0]["unit"],
                "tokens_in": 10,
                "tokens_in_uncached": 900,
                "tokens_out": 1,
                "wall_s": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    budgets = {"label": "t", "caps": {"units": 24, "tokens_in": 1000, "tokens_out": 10_000, "wall_s": 10_000}}
    calls = []

    def executor(cmd, **kw):
        calls.append(cmd["unit"])
        return {"unit": cmd["unit"], "tokens_in": 10, "tokens_in_uncached": 200, "tokens_out": 1, "wall_s": 1.0}

    result = act.run_matrix(
        out,
        authorization="t",
        executor=executor,
        budgets=budgets,
        units=units[1:],
        initial_used={"tokens_in": 900, "tokens_out": 1, "wall_s": 1.0, "units": 1},
    )
    assert result["exit"] == act.EXIT_BUDGET_STOP
    assert len(calls) == 1  # 900 carried + 200 = 1100 > 1000 on the second unit


def test_scenario_prompts_never_mention_the_workflow():
    for scenario in act.SCENARIOS.values():
        lowered = scenario["prompt"].lower()
        assert "spec" not in lowered and "任务包" not in lowered and "workflow" not in lowered


def test_cli_verify_exit_codes(tmp_path):
    path = tmp_path / "runs.jsonl"
    write_runs(path, matrix_runs())
    args = [
        sys.executable,
        str(SCRIPT),
        "verify",
        "--runs",
        str(path),
        "--require-live",
        "--baseline-revision",
        BASE,
        "--require-current-tree",
        "--expected-units",
        "24",
        "--tree-fp-override",
        "t" * 16,
    ]
    assert subprocess.run(args, capture_output=True, text=True, timeout=60).returncode == 0
    write_runs(path, matrix_runs()[:-1])
    assert subprocess.run(args, capture_output=True, text=True, timeout=60).returncode != 0


def test_current_tree_fingerprint_comes_from_the_shared_definition():
    from spec_verify import compute_tree_fingerprint

    assert act.worktree_fingerprint() == compute_tree_fingerprint(ROOT)


def test_verify_without_override_uses_the_real_tree_and_rejects_a_stale_candidate(tmp_path):
    path = tmp_path / "runs.jsonl"
    write_runs(path, matrix_runs())  # every candidate row carries a made-up fingerprint
    args = [
        sys.executable,
        str(SCRIPT),
        "verify",
        "--runs",
        str(path),
        "--require-live",
        "--baseline-revision",
        BASE,
        "--require-current-tree",
        "--expected-units",
        "24",
    ]
    completed = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert completed.returncode != 0
    assert "stale" in completed.stdout


def test_unresolvable_tree_fingerprint_is_a_refusal_not_a_pass(monkeypatch):
    import spec_verify

    monkeypatch.setattr(spec_verify, "compute_tree_fingerprint", lambda root: None)
    with pytest.raises(act.TreeUnavailable):
        act.worktree_fingerprint()


# --- a verdict must never read 0 while problems is non-empty -----------------------


def test_a_failed_verdict_exits_nonzero_so_a_gate_cannot_read_it_as_pass(tmp_path):
    path = tmp_path / "runs.jsonl"
    write_runs(path, matrix_runs()[:-1])  # 23 of 24 units: incomplete
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "verify",
            "--runs",
            str(path),
            "--require-live",
            "--baseline-revision",
            BASE,
            "--expected-units",
            "24",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # --require-current-tree is absent, so staleness is not the reason; the
    # incomplete matrix is, and the exit status must follow the verdict.
    assert completed.returncode != 0
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False and payload["problems"]


def test_an_unset_current_tree_never_silently_skips_the_staleness_check(tmp_path):
    ok, problems = act.verify_runs(
        tmp_path / "none.jsonl", require_live=True, baseline_revision=BASE, current_tree="", expected_units=24
    )
    assert ok is False  # the missing file still fails; nothing here may pass by omission
    assert any("cannot read" in p for p in problems)


def test_a_record_created_through_the_scripts_counts_as_adoption():
    """The criterion is a correct record before the first edit, not the Skill call.

    Sweep 2's P1 candidate created a real package (spec/tasks/adoption.json)
    through the packaged scripts, did the work, and was still scored
    `not_adopted` because `Skill` never appeared. That measures the entry point.
    """
    by_script = unit("P1", events=events(READ, RECORD, EDIT), package_action="created")
    score = act.score_unit(by_script)
    assert score["adopted_before_implementation"] is True
    assert score["failures"] == []


def test_a_skill_call_without_a_record_is_still_a_failure():
    """Calling the skill and then improvising is not adoption."""
    skill_only = unit("P1", events=events(READ, SKILL, EDIT), package_action="none")
    score = act.score_unit(skill_only)
    assert score["adopted_before_implementation"] is False
    assert "no_record" in score["failures"]


def test_a_record_after_the_first_edit_is_late_however_it_was_created():
    late = unit("P1", events=events(READ, EDIT, RECORD), package_action="created")
    score = act.score_unit(late)
    assert score["adopted_before_implementation"] is False
    assert "adopted_late" in score["failures"]


# --- cross-round acceptance -----------------------------------------------------------


def test_verify_cross_certifies_units_from_several_files(tmp_path):
    runs = matrix_runs()
    # split: first half in file A, second half in file B -> every unit in exactly one
    half = len(runs) // 2
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    write_runs(a, runs[:half])
    write_runs(b, runs[half:])
    ok, detail = act.verify_cross([a, b], baseline_revision=BASE, expected_units=24)
    assert ok is True, detail["problems"]
    assert len(detail["certified"]) == 24
    assert set(detail["files"]) == {"a.jsonl", "b.jsonl"}


def test_verify_cross_fails_when_a_unit_is_certified_nowhere(tmp_path):
    runs = [r for r in matrix_runs() if r["unit"] != "P1::candidate::r01"]
    path = tmp_path / "runs.jsonl"
    write_runs(path, runs)
    ok, detail = act.verify_cross([path], baseline_revision=BASE, expected_units=24)
    assert ok is False
    assert any("P1::candidate::r01" in p for p in detail["problems"])


def test_verify_cross_harness_fault_needs_rows_and_reproof(tmp_path):
    degraded = [r for r in matrix_runs() if r["unit"] != "P2::candidate::r01"]
    degraded.append(
        unit(
            "P2",
            package_action="created",
            functional_pass=False,
            usage_unknown=True,
            team_source=None,
            unit="P2::candidate::r01",
        )
    )
    path = tmp_path / "runs.jsonl"
    write_runs(path, degraded)
    # declared fault whose rejudge stream current code ACCEPTS (id-paired parallel setup)
    stream = tmp_path / "stream.jsonl"
    lines = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "u1", "name": "Agent", "input": {"name": "impl-1"}}]},
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "u2", "name": "Agent", "input": {"name": "impl-2"}}]},
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "u1",
                        "content": [{"type": "text", "text": "Async agent launched successfully."}],
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "u2",
                        "content": [{"type": "text", "text": "Async agent launched successfully."}],
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "u3", "name": "Agent", "input": {"name": "impl-3"}}]},
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "u3", "content": [{"type": "text", "text": "launched"}]}
                ],
            },
        },
    ]
    stream.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    ok, detail = act.verify_cross(
        [path],
        baseline_revision=BASE,
        expected_units=24,
        harness_faults={"P2::candidate::r01": "task-x"},
        rejudge_team_streams={"P2::candidate::r01": stream},
    )
    assert ok is True, detail["problems"]
    assert detail["exceptions"] == [{"unit": "P2::candidate::r01", "task": "task-x", "rejudged": True}]
    # without the rejudge proof the fault is only declared: still passes but rejudged False
    ok2, detail2 = act.verify_cross(
        [path],
        baseline_revision=BASE,
        expected_units=24,
        harness_faults={"P2::candidate::r01": "task-x"},
    )
    assert ok2 is True
    assert detail2["exceptions"][0]["rejudged"] is False
    # a stream current code still rejects does NOT count as proven
    bad_stream = tmp_path / "bad.jsonl"
    bad_stream.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "u9", "name": "Agent", "input": {"name": "impl-1"}}]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ok3, detail3 = act.verify_cross(
        [path],
        baseline_revision=BASE,
        expected_units=24,
        harness_faults={"P2::candidate::r01": "task-x"},
        rejudge_team_streams={"P2::candidate::r01": bad_stream},
    )
    assert ok3 is False
    assert any("not re-proven fixed" in p for p in detail3["problems"])


def test_verify_cross_rejects_mock_only_and_unknown_units(tmp_path):
    path = tmp_path / "runs.jsonl"
    write_runs(path, [{**r, "mock": True} for r in matrix_runs()])
    ok, detail = act.verify_cross([path], baseline_revision=BASE, expected_units=24)
    assert ok is False
    assert sum(1 for p in detail["problems"] if "no certifying row" in p) == 24
    extra = tmp_path / "extra.jsonl"
    write_runs(extra, [unit(scenario="P1", unit="ZZ::candidate::r01")])
    ok2, detail2 = act.verify_cross([path, extra], baseline_revision=BASE, expected_units=24)
    assert ok2 is False
    assert any("unknown units" in p for p in detail2["problems"])

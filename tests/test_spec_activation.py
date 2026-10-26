"""Fact-based admission policy (task-admission-policy).

``scripts/assess_spec_activation.py`` decides direct / create / resume / defer
from validated structured facts. It reads package and branch identity itself
(read-only) and never trusts a caller's claim that a package matches. It does
not parse natural language, mutate business state, create packages, or run Git
mutations. The fixture matrix is shared with the discovery contract tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "assess_spec_activation.py"
CASES = json.loads((ROOT / "tests/fixtures/spec_activation/cases.json").read_text(encoding="utf-8"))

sys.path.insert(0, str(ROOT / "scripts"))
import assess_spec_activation as policy  # noqa: E402

BASE = {
    "version": 1,
    "acceptance_surfaces": 1,
    "implementers": 1,
    "idle_members": 0,
    "scope_grew": False,
    "role": "main",
    "task_id": "task-a",
}


def facts(**overrides):
    merged = {**BASE, **overrides}
    return {key: value for key, value in merged.items() if value is not None}


def run_cli(payload, *args, cwd=None, raw=None):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=raw if raw is not None else json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    return completed


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def init_repo(root, branch="main"):
    git(root, "init", "-q", "-b", branch)
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("x\n")
    git(root, "add", "README.md")
    git(root, "commit", "-qm", "init")


def add_package(root, slug, branch=None):
    package = root / ".spec/specs" / slug
    package.mkdir(parents=True, exist_ok=True)
    integration = f"- Git integration branch：`{branch}`\n" if branch else ""
    (package / "spec.md").write_text(f"# t\n{integration}")
    (package / "tasks.md").write_text("# t\n")
    return package


def tree_snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.parts
    }


# --- matrix: the shared fixtures, expressed as facts ---------------------------------


def facts_for_case(case):
    f = case["facts"]
    payload = facts(
        acceptance_surfaces=f["acceptance_surfaces"],
        implementers=f["implementers"],
        idle_members=f.get("idle_members", 0),
        scope_grew=f.get("scope_grew_after_reading", False),
    )
    if f.get("opt_out"):
        # N6's whole point: this injection is what the assessor must refuse.
        payload["opt_out"] = True
    return payload


@pytest.mark.parametrize(
    "case",
    [c for c in CASES["cases"] if c["id"] in {"P1", "P2", "P4", "N1", "N2", "N3", "N4", "N5", "N6"}],
    ids=lambda c: c["id"],
)
def test_matrix_decisions_without_a_package(tmp_path, case):
    if case["expected"] == "invalid":
        with pytest.raises(policy.FactError):
            policy.assess(facts_for_case(case), root=tmp_path)
        return
    result = policy.assess(facts_for_case(case), root=tmp_path)
    assert result["decision"] == case["expected"], (case["id"], result)
    assert result["reasons"], "every decision carries traceable reason codes"


def test_same_facts_give_the_same_decision_whatever_the_wording():
    p1 = next(c for c in CASES["cases"] if c["id"] == "P1")
    decisions = {policy.assess(facts_for_case(p1), root=Path("/nonexistent"))["decision"] for _ in p1["prompts"]}
    assert decisions == {"create"}


def test_file_count_and_member_count_never_decide(tmp_path):
    base = facts(acceptance_surfaces=1, implementers=1)
    many_files = {**base, "files_touched": 40}
    crowd = {**base, "idle_members": 9}
    assert policy.assess(many_files, root=tmp_path)["decision"] == "direct"
    assert policy.assess(crowd, root=tmp_path)["decision"] == "direct"


def test_direct_result_rechecks_independent_outcomes_without_changing_the_decision(tmp_path):
    one_surface = facts(acceptance_surfaces=1, implementers=1, files_touched=12)
    result = policy.assess(one_surface, root=tmp_path)
    assert result["decision"] == "direct"
    assert "independently testable behavior" in result["shape_check"]
    assert "multiple files" in result["shape_check"]
    completed = run_cli(one_surface, "--root", str(tmp_path))
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["shape_check"] == result["shape_check"]
    assert "shape_check" not in policy.assess(facts(acceptance_surfaces=3), root=tmp_path)
    assert "shape_check" not in policy.assess(facts(acceptance_surfaces=0, implementers=0), root=tmp_path)
    assert "shape_check" not in policy.assess(facts(role="worker"), root=tmp_path)


def test_read_only_shaped_work_stays_direct_on_shape_not_a_flag(tmp_path):
    result = policy.assess(facts(idle_members=5, implementers=0, acceptance_surfaces=0), root=tmp_path)
    assert result["decision"] == "direct"
    assert result["reasons"] == ["single_acceptance_single_implementer"]


@pytest.mark.parametrize("key", ["opt_out", "reversible_small", "read_only"])
def test_verdict_facts_are_refused_as_input(tmp_path, key):
    """Live evidence (sweep 3, P1 stream): a candidate arm reached ``direct`` by
    passing ``--facts '{"opt_out":true}'``. Verdict-shaped facts are not inputs."""
    with pytest.raises(policy.FactError):
        policy.assess(facts(**{key: True}), root=tmp_path)


def test_the_sweep3_opt_out_bypass_shape_is_refused_end_to_end(tmp_path):
    completed = run_cli(
        {"version": 1, "acceptance_surfaces": 3, "implementers": 3, "opt_out": True},
        "--root",
        str(tmp_path),
        "--session",
        "sweep3",
    )
    assert completed.returncode == 2
    assert "opt_out" in completed.stderr and "unknown fact field" in completed.stderr
    from activation_state import read_assessment

    assert read_assessment(Path(tmp_path), "sweep3") is None


def test_unknown_facts_defer_instead_of_guessing(tmp_path):
    payload = facts(acceptance_surfaces=None)
    result = policy.assess(payload, root=tmp_path)
    assert result["decision"] == "defer"
    assert "unknown_acceptance_surfaces" in result["reasons"]


def test_unknown_marker_is_accepted_explicitly(tmp_path):
    result = policy.assess(facts(acceptance_surfaces="unknown"), root=tmp_path)
    assert result["decision"] == "defer"


def test_scope_growth_after_reading_upgrades_a_small_looking_task(tmp_path):
    result = policy.assess(facts(acceptance_surfaces=3, scope_grew=True), root=tmp_path)
    assert result["decision"] == "create"


def test_reversible_small_never_outvotes_multiple_acceptance_surfaces(tmp_path):
    """A multi-surface request is multi-step work even if each edit is small and reversible.

    Live evidence: a candidate arm passed ``reversible_small`` for the P1 request
    ("add filtering, a settings toggle, CSV export, and tests") and the assessor
    answered ``direct``, so the skill was never chosen before the first edit.
    """
    result = policy.assess(facts(acceptance_surfaces=3), root=tmp_path)
    assert result["decision"] == "create"


def test_reversible_small_never_outvotes_multiple_implementers(tmp_path):
    result = policy.assess(facts(acceptance_surfaces=1, implementers=2), root=tmp_path)
    assert result["decision"] == "create"


def test_direct_for_genuinely_small_work_comes_from_shape_not_self_report(tmp_path):
    """One surface, one implementer: ``direct`` rests on the shape itself, and
    a self-reported ``reversible_small`` cannot even be submitted any more."""
    result = policy.assess(facts(acceptance_surfaces=1, implementers=1), root=tmp_path)
    assert result["decision"] == "direct"
    assert result["reasons"] == ["single_acceptance_single_implementer"]
    with pytest.raises(policy.FactError):
        policy.assess(facts(acceptance_surfaces=1, implementers=1, reversible_small=True), root=tmp_path)


# --- identity: resume only on a verified match --------------------------------------


def test_resume_when_branch_and_task_match_the_active_package(tmp_path):
    init_repo(tmp_path)
    slug = "2026-10-23_add-export"
    add_package(tmp_path, slug, branch=f"spec/{slug}")
    git(tmp_path, "checkout", "-q", "-b", f"spec/{slug}")
    result = policy.assess(facts(acceptance_surfaces=2, package_slug=slug), root=tmp_path)
    assert result["decision"] == "resume" and result["package"] == slug


def test_claimed_package_that_does_not_match_the_branch_never_resumes(tmp_path):
    init_repo(tmp_path)
    add_package(tmp_path, "2026-10-23_add-export", branch="spec/2026-10-23_add-export")
    add_package(tmp_path, "2026-10-22_add-other", branch="spec/2026-10-22_add-other")
    git(tmp_path, "checkout", "-q", "-b", "feature/x")
    result = policy.assess(facts(acceptance_surfaces=2, package_slug="2026-10-23_add-export"), root=tmp_path)
    assert result["decision"] == "defer" and "package_identity_mismatch" in result["reasons"]


def test_foreign_active_package_defers_rather_than_binding(tmp_path):
    init_repo(tmp_path)
    add_package(tmp_path, "2026-10-22_add-other", branch="spec/2026-10-22_add-other")
    git(tmp_path, "checkout", "-q", "-b", "feature/x")
    result = policy.assess(facts(acceptance_surfaces=3), root=tmp_path)
    assert result["decision"] == "defer" and "foreign_active_package" in result["reasons"]


def test_newest_package_is_never_guessed(tmp_path):
    init_repo(tmp_path)
    add_package(tmp_path, "2026-10-01_add-a", branch="spec/2026-10-01_add-a")
    add_package(tmp_path, "2026-10-23_add-b", branch="spec/2026-10-23_add-b")
    git(tmp_path, "checkout", "-q", "-b", "feature/x")
    result = policy.assess(facts(acceptance_surfaces=3, package_slug="2026-10-23_add-b"), root=tmp_path)
    assert result["decision"] == "defer"


def test_protected_branch_defers_a_create(tmp_path):
    init_repo(tmp_path, branch="main")
    result = policy.assess(facts(acceptance_surfaces=3), root=tmp_path)
    assert result["decision"] == "defer" and "protected_branch" in result["reasons"]


def test_dirty_tree_with_running_teammates_defers(tmp_path):
    init_repo(tmp_path)
    git(tmp_path, "checkout", "-q", "-b", "feature/x")
    (tmp_path / "wip.txt").write_text("dirty\n")
    result = policy.assess(facts(acceptance_surfaces=3, implementers=3, teammates_running=True), root=tmp_path)
    assert result["decision"] == "defer" and "unsafe_state" in result["reasons"]


def test_worker_never_creates_or_adopts(tmp_path):
    init_repo(tmp_path)
    git(tmp_path, "checkout", "-q", "-b", "feature/x")
    result = policy.assess(facts(acceptance_surfaces=3, role="worker"), root=tmp_path)
    assert result["decision"] == "direct" and "worker_consumes_assignment" in result["reasons"]


def test_worker_role_cannot_persist_a_direct_record(tmp_path):
    """``role: worker`` answers ``direct`` as advice, but a worker claim must
    never be recordable: a main session asserting it is the same unlock hole."""
    completed = run_cli(facts(role="worker", acceptance_surfaces=3), "--root", str(tmp_path), "--session", "w1")
    assert completed.returncode == 2 and "advisory" in completed.stderr
    from activation_state import read_assessment

    assert read_assessment(Path(tmp_path), "w1") is None


def test_non_git_directory_can_still_be_assessed(tmp_path):
    result = policy.assess(facts(acceptance_surfaces=3), root=tmp_path)
    assert result["decision"] == "create"


# --- validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"version": 2},
        {"acceptance_surfaces": -1},
        {"acceptance_surfaces": True},
        {"acceptance_surfaces": 1.5},
        {"implementers": "many"},
        {"read_only": True},
        {"reversible_small": True},
        {"opt_out": False},
        {"role": "boss"},
        {"unknown_field": 1},
        {"task_id": "x" * 500},
        {"package_slug": "../etc"},
        {"package_slug": "Not A Slug"},
    ],
)
def test_invalid_input_raises_a_clear_error(tmp_path, bad):
    with pytest.raises(policy.FactError):
        policy.assess({**facts(), **bad}, root=tmp_path)


def test_oversized_input_is_rejected(tmp_path):
    completed = run_cli(None, "--root", str(tmp_path), raw="{" + '"a":1,' * 20000 + '"b":2}')
    assert completed.returncode == 2 and "too large" in completed.stderr


def test_malformed_json_is_a_clear_error(tmp_path):
    completed = run_cli(None, "--root", str(tmp_path), raw="not json")
    assert completed.returncode == 2 and "invalid JSON" in completed.stderr


# --- CLI contract and write-nothing guarantee ----------------------------------------


def test_cli_prints_one_json_object_and_never_writes(tmp_path):
    init_repo(tmp_path)
    git(tmp_path, "checkout", "-q", "-b", "feature/x")
    add_package(tmp_path, "2026-10-22_add-other", branch="spec/2026-10-22_add-other")
    before = tree_snapshot(tmp_path)
    head = git(tmp_path, "rev-parse", "HEAD")
    completed = run_cli(facts(acceptance_surfaces=3), "--root", str(tmp_path))
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["decision"] in {"direct", "create", "resume", "defer"}
    assert set(payload) >= {"decision", "reasons", "package", "facts_digest"}
    assert tree_snapshot(tmp_path) == before
    assert git(tmp_path, "rev-parse", "HEAD") == head
    assert git(tmp_path, "branch", "--show-current") == "feature/x"


def test_cli_without_any_spec_directory_works_and_creates_nothing(tmp_path):
    completed = run_cli(facts(acceptance_surfaces=3), "--root", str(tmp_path))
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["decision"] == "create"
    assert not (tmp_path / ".spec").exists()


def test_output_never_echoes_free_text_inputs(tmp_path):
    secret = "sk-live-" + "A" * 24
    completed = run_cli(facts(task_id=secret), "--root", str(tmp_path))
    assert secret not in completed.stdout and secret not in completed.stderr


def test_decision_is_bounded_and_deterministic(tmp_path):
    first = policy.assess(facts(acceptance_surfaces=3), root=tmp_path)
    second = policy.assess(facts(acceptance_surfaces=3), root=tmp_path)
    assert first == second
    assert len(json.dumps(first)) < 2000


def test_assessment_does_not_use_slot_recommendations():
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("loop_route", "workflow_route", "loopRecommended", "workflowRecommended", "route_decision"):
        assert forbidden not in source


def test_a_rename_from_spec_into_business_code_counts_as_a_dirty_tree(tmp_path):
    init_repo(tmp_path, branch="feature/x")
    (tmp_path / ".spec").mkdir()
    (tmp_path / ".spec" / "a.md").write_text("doc\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "doc")
    git(tmp_path, "mv", ".spec/a.md", "src_b.py")
    assert policy._worktree_dirty(tmp_path) is True


def test_spec_only_changes_still_do_not_count_as_dirty(tmp_path):
    init_repo(tmp_path, branch="feature/x")
    (tmp_path / ".spec").mkdir()
    (tmp_path / ".spec" / "a.md").write_text("doc\n")
    assert policy._worktree_dirty(tmp_path) is False


def test_dirty_check_does_not_rewrite_the_index(tmp_path):
    import os
    import time

    init_repo(tmp_path, branch="feature/x")
    index = tmp_path / ".git" / "index"
    time.sleep(0.05)
    os.utime(tmp_path / "README.md", None)
    before = index.read_bytes()
    policy._worktree_dirty(tmp_path)
    assert index.read_bytes() == before


# --- review: a self-reported flag must not defeat identity checks --------------------


def _bound_repo(tmp_path):
    init_repo(tmp_path, branch="feature/x")
    add_package(tmp_path, "2026-10-22_add-bound", branch="feature/x")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "pkg")
    return tmp_path


def test_new_task_flag_cannot_stack_a_second_package_on_a_bound_branch(tmp_path):
    repo = _bound_repo(tmp_path)
    wide = facts(acceptance_surfaces=3)
    result = policy.assess(wide, root=repo)
    assert result["decision"] == "defer"
    assert "active_package_unclaimed" in result["reasons"]


def test_new_task_flag_cannot_bypass_a_foreign_active_package(tmp_path):
    init_repo(tmp_path, branch="feature/x")
    add_package(tmp_path, "2026-10-22_add-other", branch="spec/2026-10-22_add-other")
    result = policy.assess(facts(acceptance_surfaces=3), root=tmp_path)
    assert result["decision"] == "defer" and "foreign_active_package" in result["reasons"]


def test_the_new_task_fact_is_no_longer_accepted(tmp_path):
    with pytest.raises(policy.FactError):
        policy.assess({**facts(), "new_task": True}, root=tmp_path)

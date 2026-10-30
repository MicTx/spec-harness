"""Discovery contract for proactive Spec admission (task-entry-contract).

The fixture matrix (``tests/fixtures/spec_activation/cases.json``) is the
executable form of ``activation-cases.md``: business requests that never use
workflow vocabulary, paired counter-cases that differ only in the decisive
fact, and unsafe states that must defer. The discovery text (SKILL.md and the
host listing) must name every decisive shape factor the matrix relies on, and
the stage pages must keep admission (should a record exist?) separate from
orchestration (how is an existing record executed?).

Scope guard: these tests prove the written contract and the fixtures agree;
they do not claim a model chooses correctly. That evidence is the live phase.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "tests/fixtures/spec_activation/cases.json").read_text(encoding="utf-8"))
BY_ID = {case["id"]: case for case in CASES["cases"]}

#: Words that would let a model match on vocabulary instead of on the work's shape.
WORKFLOW_VOCABULARY = (
    "spec",
    "任务包",
    "task package",
    "多步骤",
    "multi-step",
    "跨会话",
    "cross-session",
    "workflow",
    "验收范围",
)
REQUIRED_IDS = {"P1", "P2", "P3", "P4", "N1", "N2", "N3", "N4", "N5", "N6", "S1", "S2", "S3"}
POSITIVE = ("P1", "P2", "P3", "P4")
NEGATIVE = ("N1", "N2", "N3", "N4", "N5")
REFUSED = ("N6",)


def _skill() -> str:
    return (ROOT / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter_description() -> str:
    match = re.match(r"---\n(.*?)\n---\n", _skill(), re.DOTALL)
    assert match, "SKILL.md must open with frontmatter"
    return " ".join(match.group(1).split("description: >-", 1)[1].split())


def _when_to_use() -> str:
    text = _skill()
    start = text.index("## When to use")
    return text[start : text.index("\n## ", start + 1)]


def test_matrix_covers_every_required_case():
    assert set(BY_ID) == REQUIRED_IDS
    assert set(CASES["decisions"]) == {"direct", "create", "resume", "defer", "invalid"}
    for case in CASES["cases"]:
        assert case["expected"] in CASES["decisions"], case["id"]
        assert case["prompts"], case["id"]
        assert set(case["decisive"]) <= set(CASES["factors"]), case["id"]


def test_expected_decisions_split_cleanly_by_group():
    assert {BY_ID[i]["expected"] for i in POSITIVE} <= {"create", "resume"}
    assert {BY_ID[i]["expected"] for i in NEGATIVE} == {"direct"}
    assert {BY_ID[i]["expected"] for i in REFUSED} == {"invalid"}
    assert {BY_ID[i]["expected"] for i in ("S1", "S2", "S3")} == {"defer"}


def test_business_prompts_never_use_workflow_vocabulary():
    for case in CASES["cases"]:
        if case["id"] == "N6":
            continue  # the explicit opt-out names the record on purpose
        for prompt in case["prompts"]:
            lowered = prompt.lower()
            hit = [word for word in WORKFLOW_VOCABULARY if word in lowered]
            assert not hit, f"{case['id']} prompt leaks workflow vocabulary {hit}: {prompt}"


def test_p1_has_three_equivalent_wordings_with_one_set_of_facts():
    assert len(BY_ID["P1"]["prompts"]) >= 3
    assert len(set(BY_ID["P1"]["prompts"])) == len(BY_ID["P1"]["prompts"])


def test_file_count_alone_does_not_decide():
    """N4 and P1 touch the same number of files; only the acceptance surfaces differ."""
    p1, n4 = BY_ID["P1"]["facts"], BY_ID["N4"]["facts"]
    assert p1["files"] == n4["files"]
    assert p1["acceptance_surfaces"] > 1 >= n4["acceptance_surfaces"]
    assert (p1["implementers"], n4["implementers"]) == (1, 1)
    assert (BY_ID["P1"]["expected"], BY_ID["N4"]["expected"]) == ("create", "direct")


def test_member_count_alone_does_not_decide():
    """N5 and P2 both have a populated room; only actual implementers differ."""
    p2, n5 = BY_ID["P2"]["facts"], BY_ID["N5"]["facts"]
    assert p2["implementers"] > 1 >= n5["implementers"]
    assert n5["idle_members"] >= 3
    assert (BY_ID["P2"]["expected"], BY_ID["N5"]["expected"]) == ("create", "direct")


def test_read_only_investigation_with_many_members_stays_direct():
    n2 = BY_ID["N2"]
    assert n2["facts"]["read_only"] and n2["facts"]["idle_members"] >= 3
    assert n2["facts"]["implementers"] == 0 and n2["expected"] == "direct"


def test_verdict_fact_injection_is_refused_not_decided():
    """Sweep 3 measured an arm unlocking ``direct`` via ``opt_out``; the user's
    decline is honored at the host level, never by an agent-asserted fact."""
    n6, p1 = BY_ID["N6"]["facts"], BY_ID["P1"]["facts"]
    assert n6["acceptance_surfaces"] == p1["acceptance_surfaces"] and n6["opt_out"]
    assert BY_ID["N6"]["expected"] == "invalid"
    assert BY_ID["N6"]["decisive"] == ["verdict_fact_injection"]


def test_resume_requires_a_matching_active_package():
    assert BY_ID["P3"]["facts"]["matching_active_package"] is True
    assert BY_ID["S1"]["facts"]["matching_active_package"] is False
    assert BY_ID["S1"]["facts"].get("foreign_active_package") is True
    assert BY_ID["S1"]["expected"] == "defer"


def test_scope_growth_after_reading_upgrades_a_small_looking_request():
    p4 = BY_ID["P4"]
    assert p4["facts"]["scope_grew_after_reading"] is True
    assert p4["facts"]["acceptance_surfaces"] > 1 and p4["expected"] == "create"


def test_unsafe_states_defer_instead_of_creating():
    assert BY_ID["S2"]["facts"]["dirty_tree"] and BY_ID["S2"]["facts"]["teammates_running"]
    assert BY_ID["S3"]["facts"]["protected_branch"]
    assert all(BY_ID[i]["decisive"] == ["unsafe_state"] for i in ("S2", "S3"))


def test_discovery_text_names_every_decisive_shape():
    text = (_frontmatter_description() + "\n" + _when_to_use()).lower()
    for needle in (
        "independent acceptance",
        "implementer",
        "resume",
        "read-only",
        "small reversible",
        "opt out",
    ):
        assert needle in text, f"discovery text must name '{needle}'"


def test_discovery_text_rejects_the_non_sufficient_signals():
    when = _when_to_use().lower()
    assert "file count" in when
    assert "idle" in when
    assert "never need" in when or "without" in when


def test_description_no_longer_requires_the_user_to_name_the_workflow():
    description = _frontmatter_description().lower()
    assert "never" in description or "even when" in description or "without" in description
    assert "spec commands" in description, "explicit commands stay supported"


def test_frontmatter_stays_yaml_safe():
    lines = _skill().splitlines()
    assert "description: >-" in lines
    assert not any(line.startswith("description: ") and " when:" in line for line in lines)


def test_host_listing_stays_valid_and_explicit():
    body = (ROOT / "agents/openai.yaml").read_text(encoding="utf-8")
    assert "$spec" in body
    assert "allow_implicit_invocation: true" in body, "implicit invocation must be an explicit contract"


def test_admission_and_orchestration_stay_separate_layers():
    new = (ROOT / "references/stages/new.md").read_text(encoding="utf-8").lower()
    run = (ROOT / "references/stages/run.md").read_text(encoding="utf-8").lower()
    handoff = (ROOT / "references/handoff.md").read_text(encoding="utf-8").lower()
    assert "adopting in-flight work" in handoff and "--adopt" in handoff
    assert "when to use" in _skill().lower()
    assert "grant" not in new or "never" in new  # new.md stays the strict creation gate, unchanged
    assert "never decides admission" in run or "does not decide admission" in run


def test_lightweight_path_for_small_work_is_preserved():
    when = _when_to_use()
    assert "Spec: none" in when
    assert "the user decides" in when


def test_the_guard_is_documented_as_a_reminder_gate_not_a_security_boundary():
    for relative in ("hooks/claude_activation_guard.py", "scripts/assess_spec_activation.py"):
        text = (ROOT / relative).read_text(encoding="utf-8").lower()
        assert "self-reported" in text or "reported by the agent" in text, relative
    guard = (ROOT / "hooks/claude_activation_guard.py").read_text(encoding="utf-8").lower()
    assert "not a security boundary" in guard
    for readme in ("README.md", "README-en.md"):
        body = (ROOT / readme).read_text(encoding="utf-8")
        assert "安全边界" in body or "security boundary" in body, readme


# --- update rollback and adoption branch contract (week review, reproduced before the fix) ---


def _adoption_section() -> str:
    text = (ROOT / "references/handoff.md").read_text(encoding="utf-8")
    start = text.index("## Adopting in-flight work")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_adopted_update_checkpoint_stages_only_the_package():
    """An adopted package shares the tree with teammates: `git add -A` would commit their work and a
    whole-tree reset would discard it, so its recovery point and rollback stay inside the package."""
    update = (ROOT / "references/stages/update.md").read_text(encoding="utf-8")
    assert "adoption.json" in update
    assert "git add -- <pkg> && git commit" in update
    assert "git restore --source=<WIP-commit> --staged --worktree -- <pkg>" in update
    assert "stages/update.md" in _adoption_section()


def test_rollback_guidance_reverts_the_update_not_the_recovery_point():
    """The WIP commit holds the pre-update work: reverting it undoes that work and keeps the update."""
    update = (ROOT / "references/stages/update.md").read_text(encoding="utf-8")
    cli = (ROOT / "scripts/update_checkpoint.py").read_text(encoding="utf-8")
    gate = (ROOT / "scripts/check_spec_package.py").read_text(encoding="utf-8")
    assert re.search(r"git revert <WIP-commit>(?!\.\.)", update) is None
    assert "git revert --no-edit <WIP-commit>..HEAD" in update
    assert re.search(r"git revert <WIP 提交 sha>(?!\.\.)", cli) is None
    assert "git revert --no-edit <WIP 提交 sha>..HEAD" in cli
    assert "git revert 更新前 WIP 提交" not in gate and "git revert 其后的提交" in gate
    assert ".- **Resolution**" not in update, "two bullets of the retired-checkpoint list run together"
    # An interrupted update is usually not committed yet: WIP..HEAD is then empty and `git revert` fails.
    rollback = next(line for line in update.splitlines() if line.startswith("3. **Rollback"))
    assert "uncommitted" in rollback, rollback
    assert "改写尚未提交" in cli
    assert "未提交的改写" in gate


def test_adoption_doc_names_the_spec_branch_rename():
    section = _adoption_section()
    assert "pre-commit" in section and "full gate" in section
    assert "git branch -m <branch> spec/<name>" in section
    assert "Git integration branch" in section

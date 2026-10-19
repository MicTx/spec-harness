"""Prose-dedup doc contracts (F2, `.spec/specs/2026-10-19_refactor-skill-prose`).

The dedup map (`<package>/dedup-map.md`) classifies each compressed rule's
behavior witness into three tiers. This file is the doc-contract tier: minimal
semantic anchors for prose-only contracts, each paired with a mutation
negative sample (string surgery on a copy, the REVERT_PAIRS precedent in
``test_command_text_consistency.py``) proving the assertion can fail. D1–D3
additionally fail against the pre-compression text by construction — the
compression removed exactly what they now ban.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# Each check runs over a text string so the mutation negatives reuse it.


def _d1_skill_intro(skill: str) -> None:
    assert "checklist.md` holds acceptance" not in skill, (
        "SKILL trio description must not present legacy checklist.md as a current default member"
    )
    assert "`tasks.md` holds executable work" in skill, "spec/tasks duo description must stay"


def _d2_self_check_pointer(commands: str) -> None:
    assert "Quick self-check" in commands, "commands.md must point at the philosophy self-check"
    assert "are the assumptions written down" not in commands, (
        "the four questions stay single-sourced in engineering-philosophy.md"
    )


def _d3_invocation_layer(output_contracts: str) -> None:
    assert "remain `/spec`, `/spec:new`" not in output_contracts, (
        "the invocation layer must not re-enumerate the command inventory (single source: SKILL.md)"
    )
    assert "`SKILL.md`" in output_contracts.split("## Invocation compatibility layer")[1].split("##")[0], (
        "the invocation layer must point at the SKILL.md command inventory"
    )


def _d4_delegation_gate(orchestration: str) -> None:
    intro = orchestration.split("## When to use")[0]
    assert "route decision" in intro and "assignment contract" in intro, (
        "the delegation gate (route decision + written assignment contract) must stay in the intro"
    )


def _d5_shared_glue(orchestration: str) -> None:
    section = orchestration.split("## Ownership")[1].split("## Verification gate")[0]
    assert "Shared glue files stay in the main session" in section


def _d6_no_quantity_limit(orchestration: str) -> None:
    section = orchestration.split("## Constrained-channel dispatch")[1].split("\n## ")[0]
    assert "no `max_parallel`" in section, "the deliberate quantity-limit ban must stay named"


def _d7_one_package_one_branch(new_stage: str) -> None:
    assert "one package one branch" in new_stage


def _d8_spec_writing_requirements(templates: str) -> str:
    block = templates.split("## `spec.md`", 1)[1]
    block = block.split("Writing requirements:", 1)[1].split("## ", 1)[0]
    assert "must not be empty" in block, "assumptions-must-not-be-empty rule must stay"
    assert "at least 3 items" in block, "minimal-path >=3-items rule must stay"
    return block


def _d9_one_language_per_section(templates: str) -> None:
    assert "Keep exactly one language per gated section" in templates


def _d10_distillation_never_auto(templates: str) -> None:
    tail = templates.split("## AGENTS.md proposal", 1)[1]
    assert "never applied automatically" in tail, "distillation proposals must stay never-auto-applied"


def _d11_footer_semantics(naming: str) -> None:
    assert "`Spec: <slug>`" in naming, "the Spec footer anchor must stay documented"
    assert "Spec-WIP" in naming, "the in-flight WIP footer channel must stay documented"


def _d12_four_questions(philosophy: str) -> None:
    section = philosophy.split("## Quick self-check")[1]
    for question in (
        "write down the premises",
        "minimal viable path",
        "must not touch",
        "prove the task complete",
    ):
        assert question in section, f"Quick self-check question missing: {question}"


def _d13_autorun_fail_closed(autorun: str) -> None:
    assert "exit 3" in autorun, "the planning-gap fail-closed exit must stay named"


CHECKS = {
    "skill": (_read("SKILL.md"), _d1_skill_intro),
    "commands": (_read("references", "commands.md"), _d2_self_check_pointer),
    "output_contracts": (_read("references", "output-contracts.md"), _d3_invocation_layer),
    "orchestration_gate": (_read("references", "orchestration.md"), _d4_delegation_gate),
    "orchestration_glue": (_read("references", "orchestration.md"), _d5_shared_glue),
    "orchestration_quantity": (_read("references", "orchestration.md"), _d6_no_quantity_limit),
    "new_stage": (_read("references", "stages", "new.md"), _d7_one_package_one_branch),
    "templates_spec_rules": (_read("references", "templates.md"), _d8_spec_writing_requirements),
    "templates_one_language": (_read("references", "templates.md"), _d9_one_language_per_section),
    "templates_distillation": (_read("references", "templates.md"), _d10_distillation_never_auto),
    "naming_footer": (_read("references", "naming-and-commits.md"), _d11_footer_semantics),
    "philosophy_questions": (_read("references", "engineering-philosophy.md"), _d12_four_questions),
    "autorun_fail_closed": (_read("references", "stages", "autorun.md"), _d13_autorun_fail_closed),
}


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_doc_contract_holds(name: str) -> None:
    text, check = CHECKS[name]
    check(text)


# Mutation negatives: each pair is (anchor, drifted rewrite); applying it to a
# copy must make the same check fail, proving the assertion is load-bearing.
MUTATIONS: dict[str, tuple[str, str]] = {
    "skill": ("`tasks.md` holds executable work", "`tasks.md` and `checklist.md` hold all work"),
    "commands": ("Quick self-check", "Four questions"),
    "output_contracts": ("`SKILL.md`", "`references/commands.md`"),
    "orchestration_gate": ("route decision", "gut feeling"),
    "orchestration_glue": ("Shared glue files stay in the main session", "Glue files float freely"),
    "orchestration_quantity": ("no `max_parallel`", "a tuned `max_parallel`"),
    "new_stage": ("one package one branch", "packages share branches freely"),
    "templates_spec_rules": ("must not be empty", "may be empty"),
    "templates_one_language": ("Keep exactly one language per gated section", "Mix languages freely"),
    "templates_distillation": ("never applied automatically", "applied automatically"),
    "naming_footer": ("`Spec: <slug>`", "`[Spec-#N]`"),
    "philosophy_questions": ("prove the task complete", "hope it works"),
    "autorun_fail_closed": ("exit 3", "exit 0"),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_doc_contract_can_fail(name: str) -> None:
    text, check = CHECKS[name]
    current, drifted = MUTATIONS[name]
    assert current in text, f"mutation anchor drifted from real text: {name}"
    with pytest.raises(AssertionError):
        check(text.replace(current, drifted))

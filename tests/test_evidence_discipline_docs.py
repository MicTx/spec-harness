"""Documentation assertions for the evidence freshness & completeness discipline.

Anchors the three doc carriers of the feature without touching the init
template fences (references/templates.md:3-5 — only the three fences may
change when templates change; these requirements live OUTSIDE the fences).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from spec_package_support import load_reference_templates

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "references" / "templates.md"
# The check-stage contract lives in stages/check.md since the stage-contract
# split; commands.md keeps only the navigation index.
STAGES_CHECK = ROOT / "references" / "stages" / "check.md"


def _section_text(content: str, start_heading: str, end_heading: str) -> str:
    start = content.index(start_heading)
    end = content.index(end_heading, start)
    return content[start:end]


def test_templates_tasks_section_requires_runtime_behavioral_evidence_for_migrations():
    content = TEMPLATES.read_text(encoding="utf-8")
    tasks_section = _section_text(content, "## `tasks.md`", "## Success criteria")
    assert "Migration/upgrade tasks" in tasks_section
    assert "runtime behavioral evidence" in tasks_section
    assert "static scan may only supplement it" in tasks_section


def test_templates_checklist_section_requires_truncated_evidence_recast():
    content = TEMPLATES.read_text(encoding="utf-8")
    checklist_section = _section_text(content, "## Legacy `checklist.md`", "## `completion-summary.md`")
    assert "truncated" in checklist_section
    assert "narrower scope" in checklist_section
    assert "never counts as a passing basis" in checklist_section


def test_templates_checklist_section_documents_freshness_anchor_discipline():
    content = TEMPLATES.read_text(encoding="utf-8")
    checklist_section = _section_text(content, "## Legacy `checklist.md`", "## `completion-summary.md`")
    assert "证据锚点：HEAD <sha> @ <ISO-8601 时间>" in checklist_section
    assert "证据过期需重跑取证" in checklist_section


def test_init_template_fences_untouched_by_writing_requirements():
    """The two init fences must not carry the new prose (templates.md intro)."""
    templates = load_reference_templates(ROOT)
    assert set(templates) == {"spec.md", "tasks.md"}
    for name, template in templates.items():
        assert "truncated" not in template, name
        assert "证据锚点" not in template, name
        assert "Migration/upgrade" not in template, name
        assert "narrower scope" not in template, name


def test_commands_check_section_documents_anchor_semantics():
    content = STAGES_CHECK.read_text(encoding="utf-8")
    assert "- 证据锚点：HEAD <sha> @ <ISO-8601 时间>" in content
    assert "证据过期需重跑取证" in content
    assert "archived" in content
    assert "non-Git" in content
    assert "truncated" in content


def test_decisive_claim_requires_preimplementation_falsifier():
    for relative in ("SKILL.md", "references/stages/new.md", "references/templates.md"):
        content = (ROOT / relative).read_text(encoding="utf-8")
        for required in ("1–2", "before implementation", "primary", "falsifier", "verify"):
            assert required in content, (relative, required)
    run = (ROOT / "references/stages/run.md").read_text(encoding="utf-8")
    assert "assumption is falsified or scope changes" in run
    assert "§2.5" in run
    check = STAGES_CHECK.read_text(encoding="utf-8")
    assert "claim → primary source → falsifier → actual result" in check
    assert "Execution/freshness does not prove semantic correctness" in check


def test_independent_oracle_examples_reject_self_confirmation():
    content = TEMPLATES.read_text(encoding="utf-8")
    for required in (
        "implementation-generated expected values",
        "trusted raw data",
        "independent reference method",
        "different agent",
        "same model",
        "已核验 / 推断 / 未核验",
        "Missing material",
        "does not prove model compliance",
    ):
        assert required in content, required
    for relative in ("agents/reviewer.md", "agents/confirmer.md"):
        content = (ROOT / relative).read_text(encoding="utf-8")
        assert "判据来源" in content, relative
        assert "换 agent" in content, relative
        assert "缺材料" in content, relative
    output = (ROOT / "references/output-contracts.md").read_text(encoding="utf-8")
    assert "已核验 / 推断 / 未核验" in output
    assert "not five new output sections" in output


def test_new_goal_requires_scope_update_before_implementation():
    for relative in ("SKILL.md", "references/stages/run.md", "references/templates.md"):
        content = (ROOT / relative).read_text(encoding="utf-8")
        for required in ("all-green", "new goal", "resume", "scope and unfinished tasks", "before implementation"):
            assert required in content, (relative, required)
    assert "resume is package identity, not scope coverage" in STAGES_CHECK.read_text(encoding="utf-8")

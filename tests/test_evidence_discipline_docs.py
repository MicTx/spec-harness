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
COMMANDS = ROOT / "references" / "commands.md"


def _section_text(content: str, start_heading: str, end_heading: str) -> str:
    start = content.index(start_heading)
    end = content.index(end_heading, start)
    return content[start:end]


def test_templates_tasks_section_requires_runtime_behavioral_evidence_for_migrations():
    content = TEMPLATES.read_text(encoding="utf-8")
    tasks_section = _section_text(content, "## `tasks.md`", "## `checklist.md`")
    assert "Migration/upgrade tasks" in tasks_section
    assert "runtime behavioral evidence" in tasks_section
    assert "static scan may only supplement it" in tasks_section


def test_templates_checklist_section_requires_truncated_evidence_recast():
    content = TEMPLATES.read_text(encoding="utf-8")
    checklist_section = _section_text(content, "## `checklist.md`", "## `completion-summary.md`")
    assert "truncated" in checklist_section
    assert "narrower scope" in checklist_section
    assert "never counts as a passing basis" in checklist_section


def test_templates_checklist_section_documents_freshness_anchor_discipline():
    content = TEMPLATES.read_text(encoding="utf-8")
    checklist_section = _section_text(content, "## `checklist.md`", "## `completion-summary.md`")
    assert "证据锚点：HEAD <sha> @ <ISO-8601 时间>" in checklist_section
    assert "证据过期需重跑取证" in checklist_section


def test_init_template_fences_untouched_by_writing_requirements():
    """The three init fences must not carry the new prose (templates.md:3-5)."""
    templates = load_reference_templates(ROOT)
    assert set(templates) == {"spec.md", "tasks.md", "checklist.md"}
    for name, template in templates.items():
        assert "truncated" not in template, name
        assert "证据锚点" not in template, name
        assert "Migration/upgrade" not in template, name
        assert "narrower scope" not in template, name


def test_commands_check_section_documents_anchor_semantics():
    content = COMMANDS.read_text(encoding="utf-8")
    check_section = _section_text(content, "## `/spec:check`", "## `/spec:done`")
    assert "- 证据锚点：HEAD <sha> @ <ISO-8601 时间>" in check_section
    assert "证据过期需重跑取证" in check_section
    assert "archived" in check_section
    assert "non-Git" in check_section
    assert "truncated" in check_section

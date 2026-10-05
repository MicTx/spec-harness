"""Reference-document content contracts.

These tests pin the prose contracts of the reference carriers so silent
drift fails a test instead of surprising a gate author:

- ``references/engineering-philosophy.md``: the Gate design section carries
  the fail-closed doctrine and the four-row risk→carrier selection table,
  and does not copy the gate inventory (gate lists live in the scripts and
  stage docs only).
- ``references/templates.md``: the ``checklist.md`` init fence ships the
  ``## 边界回归`` boundary-regression section with the three negative-sample
  classes and the inline N/A escape.
- ``references/commands.md``: the Script working-directory conventions
  section states the output channel contract (contract output → stdout,
  diagnostics → stderr).
- ``references/templates.md`` / ``references/commands.md``: the checklist
  init fence ships the decisive-check acceptance-evidence line and the
  ``/spec:check`` prose describes it (check ↔ template linkage).
- ``references/orchestration.md``: the independent-review section carries
  the one-mechanism cap and the three confirmation paths, and the handoff
  ``### Findings`` template marks unconfirmed findings via a status field
  instead of a disposition.
- ``references/storage-and-archive.md``: the standard layout tree lists the
  optional ``orchestration/`` package asset directory (script + run ledger).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (ROOT / "references" / name).read_text(encoding="utf-8")


def _section(content: str, heading: str, next_heading_prefix: str = "## ") -> str:
    """Return the body of a ``## <heading>`` section (heading excluded)."""
    start = content.index(heading)
    body_start = content.index("\n", start) + 1
    next_heading = content.find(f"\n{next_heading_prefix}", body_start)
    if next_heading < 0:
        return content[body_start:]
    return content[body_start:next_heading]


def _checklist_fence(templates: str) -> str:
    """Return the markdown fence that holds the checklist init template."""
    blocks = []
    lines = templates.splitlines()
    inside = False
    current: list[str] = []
    for line in lines:
        if line.strip() == "```markdown":
            inside = True
            current = []
            continue
        if inside and line.strip() == "```":
            inside = False
            blocks.append("\n".join(current))
            continue
        if inside:
            current.append(line)
    checklist_blocks = [block for block in blocks if "# [项目名称] - 验收清单" in block]
    assert len(checklist_blocks) == 1, "templates.md must hold exactly one checklist init fence"
    return checklist_blocks[0]


# -- engineering-philosophy.md: Gate design --


def test_gate_design_section_exists():
    content = _read("engineering-philosophy.md")
    assert "## Gate design" in content
    assert "- Gate design" in content.split("## Four principles")[0], "Contents must list the Gate design section"


def test_gate_design_states_fail_closed_doctrine():
    section = _section(_read("engineering-philosophy.md"), "## Gate design")
    assert "未运行=未通过" in section
    assert "run event" in section
    assert "business conclusion" in section
    assert "terminal state" in section
    assert "never a success verdict" in section
    assert "Fail closed" in section


def test_gate_design_carries_four_row_selection_table():
    section = _section(_read("engineering-philosophy.md"), "## Gate design")
    for risk_shape in ("Static structure", "Needs human review", "Dynamic condition", "Host side effect"):
        assert risk_shape in section, f"selection table missing risk row: {risk_shape}"
    for carrier in ("check_spec_package.py", "/spec:check", "hooks/claude_stop_guard.py", "workflow-runner"):
        assert carrier in section, f"selection table missing carrier: {carrier}"


def test_gate_design_does_not_copy_gate_inventory():
    """门禁清单不复制进哲学载体：小节内不出现具体门禁条目名（commands.md 先例）。"""
    section = _section(_read("engineering-philosophy.md"), "## Gate design")
    for gate_label in ("跨载体一致性", "项目结构与文档可信度", "边界回归", "证据与回填", "证据新鲜度"):
        assert gate_label not in section, f"Gate design must not copy gate label: {gate_label}"


def test_gate_design_points_to_channel_contract():
    section = _section(_read("engineering-philosophy.md"), "## Gate design")
    assert "stdout" in section and "stderr" in section
    assert "references/commands.md" in section


# -- templates.md: boundary regression fence --


def test_checklist_fence_ships_boundary_regression_section():
    fence = _checklist_fence(_read("templates.md"))
    assert "## 边界回归" in fence


def test_checklist_fence_boundary_regression_covers_three_negative_classes():
    fence = _checklist_fence(_read("templates.md"))
    section = _section(fence, "## 边界回归", next_heading_prefix="## ")
    for negative_class in ("越界负样本", "顺序交换负样本", "旁路负样本"):
        assert negative_class in section, f"boundary regression missing negative class: {negative_class}"
    assert "boundary" in section
    assert "verify" in section


def test_checklist_fence_boundary_regression_documents_na_escape():
    fence = _checklist_fence(_read("templates.md"))
    section = _section(fence, "## 边界回归", next_heading_prefix="## ")
    assert "N/A：" in section


def test_templates_writing_requirements_name_boundary_regression_gate():
    content = _read("templates.md")
    tail = content[content.index("Writing requirements:", content.index("# [项目名称] - 验收清单")) :]
    assert "`## 边界回归` (boundary regression)" in tail, (
        "checklist writing requirements must name the boundary regression optional gate"
    )
    assert "N/A：<理由>" in tail, "writing requirements must document the inline N/A escape"
    assert "ships `## 边界回归` by default" in tail


# -- templates.md: decisive-check acceptance-evidence line --


def test_checklist_fence_acceptance_evidence_ships_decisive_check_line():
    fence = _checklist_fence(_read("templates.md"))
    section = _section(fence, "## 验收证据", next_heading_prefix="## ")
    assert section.startswith("- 裁决性检查：<命令> @ <时间>"), (
        "decisive-check line must ship as the top optional line of the acceptance evidence section"
    )


def test_templates_writing_requirements_name_decisive_check_line():
    content = _read("templates.md")
    tail = content[content.index("Writing requirements:", content.index("# [项目名称] - 验收清单")) :]
    assert "裁决性检查：<命令> @ <时间>" in tail, (
        "checklist writing requirements must document the decisive-check optional line"
    )
    assert "该检查在本项目不存在" in tail, "out-of-scope marking requires the not-in-project factual basis"
    assert "以慢为由" in tail, "slowness must be named as an invalid out-of-scope reason"
    assert "absent line keeps the legacy behavior" in tail, "optional-line semantics must stay documented"


# -- commands.md: output channel contract --


def test_script_conventions_state_channel_contract():
    content = _read("commands.md")
    section = _section(content, "## Script working-directory conventions", next_heading_prefix="\u0000")
    assert "Output channel contract" in section
    assert "stdout" in section and "stderr" in section
    assert "parseable" in section


def test_check_section_matches_shipped_template_gates():
    content = _read("commands.md")
    section = _section(content, "## `/spec:check`")
    assert "## 边界回归" in section, "/spec:check section must describe the shipped boundary regression section"
    assert "contains no optional gate headings" not in content


def test_check_section_matches_shipped_decisive_check_line():
    section = _check_section()
    assert "先选检查再验收" in section, "/spec:check must rank the project's checks before acceptance"
    assert "裁决性检查必真跑一次" in section, "/spec:check must demand the decisive check actually run"
    fence = _checklist_fence(_read("templates.md"))
    assert "裁决性检查：<命令> @ <时间>" in fence, (
        "check prose and the shipped decisive-check template line must stay linked"
    )


def test_check_section_uses_declared_verification_scope_instead_of_default_full_suite():
    section = _check_section()
    assert "### 5.1 验证策略" in section
    assert "package" in section and "integration" in section and "project" in section
    assert "not required for a package/integration scope" in section


def test_spec_template_declares_package_scope_and_upgrade_trigger():
    content = _read("templates.md")
    spec_fence = next(block for block in content.split("```markdown")[1:] if "# [项目名称] - 项目范围" in block)
    assert "### 5.1 验证策略" in spec_fence
    assert "范围级别：package" in spec_fence
    assert "升级触发" in spec_fence


# -- commands.md: /spec:check structured writeback & dual stop conditions --


def _check_section() -> str:
    return _section(_read("commands.md"), "## `/spec:check`")


def test_check_section_carries_two_field_failure_writeback():
    section = _check_section()
    for field in ("未通过项", "处置枚举", "说明"):
        assert field in section, f"structured writeback missing field: {field}"
    assert "two required fields" in section, "writeback must state the two required fields"
    assert "four fixed fields" not in section, "legacy four-field per-item template must be gone"
    assert "required when the disposition is" in section, "conditional `说明` line must state its trigger"


def test_check_section_names_check_stage_disposition_vocabulary():
    section = _check_section()
    for word in ("fix_this_round", "followup", "accepted_risk"):
        assert word in section, f"check-stage disposition vocabulary missing: {word}"


def test_check_section_separates_check_words_from_archive_closure_types():
    section = _check_section()
    for closure_type in ("resolved_current", "resolved_followup", "external_blocked", "non_actionable"):
        assert closure_type in section, f"archive closure boundary missing type: {closure_type}"
    assert "never be mixed" in section, "check words and archive closure types must state a no-mixing boundary"


def test_check_section_states_respond_prohibition():
    section = _check_section()
    assert "严禁把未通过项回写成成功结果" in section, "respond prohibition sentence missing"


def test_check_section_states_dual_stop_conditions():
    section = _check_section()
    assert "无新增项" in section, "semantic stop condition must be readable directly from the doc"
    assert "最多 3 轮" in section, "default budget-cap stop condition must be readable directly from the doc"
    assert "may declare a different N" in section, "budget cap must stay overridable, never a declaration requirement"


def test_check_section_states_stop_loss_attribution_rule():
    section = _check_section()
    assert "同一门禁连续 N 轮失败" in section
    assert "停止重跑" in section
    assert "拆细" in section, "attribution must name splitting an over-broad standard"
    assert "verify" in section, "attribution must allow fixing the verify itself"
    assert "转人工" in section, "attribution must allow marking a human escalation"


def test_check_section_reuses_task_stable_ids_for_dedup():
    section = _check_section()
    assert "id: task-" in section, "cross-round dedupe must reuse the tasks stable id convention"


def test_check_section_records_three_state_round_count():
    section = _check_section()
    assert "three-state count" in section, "round report must hand back a three-state count"


# -- templates.md: completion-summary optional process metrics section --


def test_templates_field_order_names_optional_process_metrics_section():
    content = _read("templates.md")
    tail = content[content.index("Generated by `scripts/complete_spec_package.py`") :]
    assert "gate evidence → process metrics (optional)" in tail, "field order prose must place process metrics"
    assert "`## 过程指标`" in tail
    assert "`## 门禁证据`" in tail and "`## 遗留事项`" in tail, "insertion position must stay documented"
    assert "--process-metric" in tail


# -- orchestration.md: independent review & finding confirmation --


def _independent_review_section() -> str:
    return _section(_read("orchestration.md"), "## 独立复审与发现确认")


def _handoff_format_section() -> str:
    """Return the ``## Handoff format`` section, fence headings included.

    ``_section()`` alone would stop at the fence's ``## HANDOFF:`` heading,
    so slice to the next real section heading instead.
    """
    content = _read("orchestration.md")
    start = content.index("## Handoff format")
    end = content.index("\n## `### 5.4 编排策略`", start)
    return content[start:end]


def test_independent_review_section_states_one_mechanism_cap():
    section = _independent_review_section()
    assert "never stacked unconditionally" in section, "review must stay risk-triggered, not unconditional"
    assert "一交付物一机制上限" in section
    assert "两种复核机制是天花板" in section, "the cap must name two mechanisms as the ceiling"
    assert "不计入本预算" in section, "package-level structural gates must stay outside the budget"


def test_independent_review_section_lists_three_confirmation_paths():
    section = _independent_review_section()
    assert "发现确认三路" in section
    assert "已有人看过" in section, "findings must not self-vouch as already reviewed"
    assert "命令能裁决" in section, "path 1: a command decides via its exit code"
    assert "agents/confirmer.md" in section, "path 2: an independent confirmer contract reproduces"
    assert "unconfirmed" in section and "绝不静默丢弃" in section, (
        "path 3: failed or untried confirmation keeps the finding"
    )


def test_handoff_findings_status_is_a_field_not_a_disposition():
    handoff = _handoff_format_section()
    assert "Per finding (status is a field on the finding, not a disposition):" in handoff
    assert "- status: verified | unconfirmed" in handoff
    for field in ("- where:", "- what:", "- evidence:"):
        assert field in handoff, f"per-finding structure missing field: {field}"
    for disposition in ("fix_this_round", "followup", "accepted_risk"):
        assert disposition not in handoff, f"unconfirmed is a status field, never a disposition: {disposition}"


def test_storage_layout_tree_lists_optional_orchestration_assets():
    """The layout truth source names the per-package orchestration asset home."""
    text = _read("storage-and-archive.md")
    assert "orchestration/" in text, "layout tree must list the orchestration/ asset directory"
    assert "optional orchestration assets: script + run ledger" in text, (
        "the orchestration/ entry must stay annotated as optional script + run ledger"
    )

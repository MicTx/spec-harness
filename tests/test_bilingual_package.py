# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bilingual package-format regressions (F18 / AUD-21).

The gates identify sections by stable machine keys; display titles are
localization. These tests lock the three-sided contract:

1. the alias table covers *every* heading the engine parses, and the Chinese
   side is frozen (aliases only grow — historical packages never re-grade);
2. key anchors (`<!-- spec:key -->`) bind a heading regardless of its display
   language, and a registered anchor wins over the display title;
3. mixing languages in one document creates no third semantics, and the
   verdict vocabulary (验收结果/Result, 阻塞/blocked) is bilingual.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_spec_package import compute_gate_results
from spec_package_support import (
    PACKAGE_SECTION_KEYS,
    SECTION_CONFIRMED_FACTS,
    SECTION_KEY_ASSUMPTIONS,
    SECTION_MINIMAL_PATH,
    SECTION_OUT_OF_SCOPE,
    SPEC_TITLE_SUFFIXES,
    SectionKey,
    checklist_complete,
    checklist_passed,
    clarification_gaps,
    extract_evidence_fields,
    extract_heading_title_from_content,
    extract_pending_questions,
    extract_problem_definition_values,
    extract_section_bullets,
    fold_section_key_anchors,
    heading_line_matches,
    orchestration_strategy_errors,
    parse_task_records,
    section_checkboxes,
    section_exists,
    verification_scope_errors,
    verification_scope_fields,
)

# Every heading the parsing layer consumes, as enumerated by
# `rg '"### |"## ' scripts/*.py` at implementation time (F18 task_0001
# evidence). The engine has no other heading contracts.
FROZEN_CHINESE_CONTRACT_HEADINGS = {
    "### 2.1 已确认事实",
    "### 2.2 关键假设",
    "### 2.3 待确认问题",
    "### 3.3 不在范围内",
    "## 4. 最小实现路径",
    "### 5.1 验证策略",
    "### 5.4 编排策略",
    "## 验收证据",
    "### 6.1 行为成效指标",
    "## 跨载体一致性",
    "## 项目结构与文档可信度",
    "## 边界回归",
}

EXPECTED_SECTION_KEYS = {
    "confirmed-facts",
    "key-assumptions",
    "open-questions",
    "out-of-scope",
    "minimal-path",
    "verification-strategy",
    "orchestration",
    "acceptance-evidence",
    "behavior-metrics",
    "cross-carrier-consistency",
    "structure-credibility",
    "boundary-regression",
}


# --------------------------------------------------------------------------- #
# Alias table coverage and freezing
# --------------------------------------------------------------------------- #


def test_alias_registry_covers_every_parsed_contract_section():
    assert set(PACKAGE_SECTION_KEYS) == EXPECTED_SECTION_KEYS
    for section in PACKAGE_SECTION_KEYS.values():
        assert isinstance(section, SectionKey)
        assert section.key in section.key
        # Bilingual: at least the canonical Chinese plus one English alias,
        # canonical first (messages quote it).
        assert len(section.aliases) >= 2
        assert section.canonical in FROZEN_CHINESE_CONTRACT_HEADINGS
        english = section.aliases[1]
        assert section.canonical.split(" ", 1)[0] == english.split(" ", 1)[0]
        assert any(chr(ord(char)) > "\u2e80" for char in section.canonical)
        assert not any(chr(ord(char)) > "\u2e80" for char in english)


def test_chinese_alias_set_is_frozen_mapping_only_grows():
    """No Chinese contract heading may be dropped or reworded."""
    assert {section.canonical for section in PACKAGE_SECTION_KEYS.values()} == FROZEN_CHINESE_CONTRACT_HEADINGS


def test_english_headings_resolve_through_shared_scanners():
    doc = "\n".join(
        [
            "### 2.1 Confirmed Facts",
            "- real fact",
            "### 2.2 Key Assumptions",
            "- real assumption",
            "### 2.3 Open Questions",
            "- None",
            "### 3.3 Out of Scope",
            "- real exclusion",
            "## 4. Minimal Implementation Path",
            "- real approach: do the thing",
            "- not introducing: extra machinery",
            "- abstractions not built: plugin registry",
            "## Later",
        ]
    )
    assert section_exists(doc, SECTION_CONFIRMED_FACTS)
    assert extract_section_bullets(doc, SECTION_CONFIRMED_FACTS) == ["real fact"]
    assert extract_section_bullets(doc, SECTION_KEY_ASSUMPTIONS) == ["real assumption"]
    assert extract_pending_questions(doc) == []
    assert extract_section_bullets(doc, SECTION_OUT_OF_SCOPE) == ["real exclusion"]
    assert len(extract_section_bullets(doc, SECTION_MINIMAL_PATH)) == 3
    # Exact-match semantics survive localization: a longer English title of a
    # *different* section is not a prefix hit.
    assert not heading_line_matches("### 2.2 Key Assumptions Extended", SECTION_KEY_ASSUMPTIONS)
    assert heading_line_matches("### 2.2 Key Assumptions (recorded)", SECTION_KEY_ASSUMPTIONS)


def test_chinese_headings_still_resolve_after_keying():
    doc = "### 2.1 已确认事实\n- 事实 A\n### 2.2 关键假设\n- 假设 A\n"
    assert section_exists(doc, SECTION_CONFIRMED_FACTS)
    assert section_checkboxes(doc, SECTION_CONFIRMED_FACTS) == (0, 0)
    assert extract_section_bullets(doc, SECTION_KEY_ASSUMPTIONS) == ["假设 A"]


# --------------------------------------------------------------------------- #
# Key anchors
# --------------------------------------------------------------------------- #


def test_standalone_anchor_line_binds_localized_heading():
    doc = "\n".join(
        [
            "<!-- spec:confirmed-facts -->",
            "",
            "### 2.1 既知の事実ではなく任意の表示題",
            "- real fact",
        ]
    )
    assert section_exists(doc, SECTION_CONFIRMED_FACTS)
    assert extract_section_bullets(doc, SECTION_CONFIRMED_FACTS) == ["real fact"]
    # The anchor targets exactly one section: the display title no longer
    # smuggles the content into the assumptions section.
    assert extract_section_bullets(doc, SECTION_KEY_ASSUMPTIONS) == []


def test_trailing_anchor_on_heading_line_binds():
    doc = "<!-- spec-format: 2 -->\n### 2.3 Whatever Display Title <!-- spec:open-questions -->\n- [ ] open one\n"
    assert extract_pending_questions(doc) == ["open one"]


def test_registered_anchor_wins_over_display_title():
    doc = "<!-- spec-format: 2 -->\n### 2.1 已确认事实 <!-- spec:open-questions -->\n- [ ] real question\n"
    assert extract_pending_questions(doc) == ["real question"]
    assert extract_section_bullets(doc, SECTION_CONFIRMED_FACTS) == []


def test_unregistered_anchor_is_inert_prose():
    doc = "### 2.1 已确认事实 <!-- spec:not-a-registered-key -->\n- real fact\n"
    assert extract_section_bullets(doc, SECTION_CONFIRMED_FACTS) == ["real fact"]


def test_bare_anchor_line_is_never_a_heading_itself():
    doc = "body\n\n<!-- spec:confirmed-facts -->\n\nnot a heading\n"
    assert not section_exists(doc, SECTION_CONFIRMED_FACTS)


def test_anchor_inside_code_fence_is_prose():
    doc = "\n".join(
        [
            "```markdown",
            "<!-- spec:verification-strategy -->",
            "### 5.1 Verification Strategy",
            "- scope: package",
            "```",
        ]
    )
    # Only a real (non-fenced) 5.1 counts; a fenced template quote does not.
    _, headings = verification_scope_errors.__globals__["_verification_scope_section_lines"](doc)
    assert headings == 0


def test_fold_restores_stray_anchor_and_tolerates_fences():
    content = "\n".join(
        [
            "<!-- spec:confirmed-facts -->",
            "not a heading, so the anchor was just a comment",
            "```markdown",
            "<!-- spec:confirmed-facts -->",
            "### 2.1 Confirmed Facts",
            "```",
        ]
    )
    folded = fold_section_key_anchors(content)
    # The stray anchor survives as a comment; the fenced one is untouched.
    assert "<!-- spec:confirmed-facts -->" in folded
    heading_lines = [line for line in folded.splitlines() if line.startswith("###")]
    assert heading_lines == ["### 2.1 Confirmed Facts"]


# --------------------------------------------------------------------------- #
# Mixed language: no third semantics
# --------------------------------------------------------------------------- #


def test_mixed_language_document_resolves_each_section_independently():
    doc = "\n".join(
        [
            "### 2.1 已确认事实",
            "- 中文事实",
            "### 2.3 Open Questions",
            "- None",
            "<!-- spec:out-of-scope -->",
            "### 3.3 本轮不做",
            "- 中文排除项",
            "## 4. Minimal Implementation Path",
            "- real approach",
            "- second step",
            "- third step",
        ]
    )
    assert extract_section_bullets(doc, SECTION_CONFIRMED_FACTS) == ["中文事实"]
    assert extract_pending_questions(doc) == []
    assert extract_section_bullets(doc, SECTION_OUT_OF_SCOPE) == ["中文排除项"]
    assert len(extract_section_bullets(doc, SECTION_MINIMAL_PATH)) == 3


def test_duplicate_verification_strategy_across_languages_still_fails_uniqueness():
    doc = "\n".join(
        [
            "### 5.1 验证策略",
            "- 范围级别：package",
            "- 变更对象：x",
            "- 快速检查：`cmd`",
            "- 集成检查：适用外：无跨模块路径",
            "- 全项目检查：仅在收口执行",
            "- 升级触发：见模板",
            "### 5.1 Verification Strategy",
            "- scope: package",
        ]
    )
    errors = verification_scope_errors(doc)
    assert any("必须唯一" in error for error in errors)


# --------------------------------------------------------------------------- #
# Bilingual verdict vocabulary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "line,passed",
    [
        ("**验收结果**：通过", True),
        ("**Result**: pass", True),
        ("**Result**：pass", True),
        ("**result**: PASS", True),
        ("**Result**: fail", False),
        ("**Result**: passed", False),  # only 通过/pass are the verdict tokens
        ("**验收结果**：未通过", False),
        ("结果：通过", False),  # missing bold markers
    ],
)
def test_acceptance_result_line_is_bilingual(line, passed):
    assert checklist_passed(line + "\n") is passed


def test_checklist_complete_bilingual():
    ok = "## 验收证据\n- 脚本验证：`pytest -q` 全绿\n\n**Result**: pass\n"
    assert checklist_complete(ok)
    unfinished = ok.replace("pass", "pass") + "- [ ] one more\n"
    assert not checklist_complete(unfinished)


def test_task_title_blocked_markers_are_bilingual():
    tasks = "\n".join(
        [
            "- [ ] blocked on upstream API decision",
            "  - boundary: x",
            "  - verify: `true`",
            "- [ ] Blocker: needs user input",
            "  - boundary: x",
            "  - verify: `true`",
            "- [ ] blocked-in-name-only 已完成的阻塞描述",
            "  - boundary: x",
            "  - verify: `true`",
            "- [ ] unblocked flows are normal work",
            "  - boundary: x",
            "  - verify: `true`",
            "- [x] resolved blocker: done and recorded",
            "  - boundary: x",
            "  - verify: `true`",
            "- [ ] 中文阻塞：等待授权",
            "  - boundary: x",
            "  - verify: `true`",
        ]
    )
    records = {record.title: record for record in parse_task_records(tasks)}
    assert records["blocked on upstream API decision"].blocked is True
    assert records["Blocker: needs user input"].blocked is True
    assert records["unblocked flows are normal work"].blocked is False
    # Checked tasks describing resolved blocking are not blocked.
    assert records["resolved blocker: done and recorded"].blocked is False
    assert records["中文阻塞：等待授权"].blocked is True
    # "blocked-in-name-only" title still matches: title semantics unchanged
    # for Chinese packages, and English word boundaries hold.
    assert records["blocked-in-name-only 已完成的阻塞描述"].blocked is True


# --------------------------------------------------------------------------- #
# 5.1 / 5.4 / §1 field vocabulary
# --------------------------------------------------------------------------- #


def _scope_doc(fields: list[str]) -> str:
    return "### 5.1 Verification Strategy\n" + "\n".join(f"- {item}" for item in fields) + "\n"


def test_verification_scope_english_fields_normalize_to_canonical_keys():
    doc = _scope_doc(
        [
            "scope: integration",
            "changed surface: parsing layer and gates",
            "quick check: `pytest tests/test_bilingual_package.py -q`",
            "integration check: `pytest -q -k spec_package`",
            "full-project check: only at closeout",
            "escalation trigger: hooks contract touched",
        ]
    )
    fields = verification_scope_fields(doc)
    assert fields["范围级别"] == "integration"
    assert fields["变更对象"] == "parsing layer and gates"
    assert verification_scope_errors(doc) == []


def test_verification_scope_missing_english_field_fails_with_canonical_message():
    doc = _scope_doc(
        [
            "scope: package",
            "changed surface: x",
            "integration check: n/a here",
            "full-project check: only at closeout",
            "escalation trigger: none",
        ]
    )
    errors = verification_scope_errors(doc)
    assert any("缺少字段：快速检查" in error for error in errors)


def test_verification_scope_cross_language_duplicate_field_fails():
    doc = _scope_doc(
        [
            "范围级别：package",
            "scope: package",
            "changed surface: x",
            "quick check: `cmd`",
            "integration check: 适用外：无",
            "full-project check: 仅在收口",
            "escalation trigger: 模板",
        ]
    )
    errors = verification_scope_errors(doc)
    assert any("字段 范围级别 出现 2 次" in error for error in errors)


def test_orchestration_english_heading_and_route():
    doc = "### 5.4 Orchestration Strategy\n- route: build\n"
    assert orchestration_strategy_errors(doc) == []
    doc_opt_out = "### 5.4 Orchestration Strategy\n- not enabled (single-threaded main session)\n"
    assert orchestration_strategy_errors(doc_opt_out) == []
    doc_bad = "### 5.4 Orchestration Strategy\n- route: build（主线程）+ review\n"
    assert orchestration_strategy_errors(doc_bad) != []


def test_problem_definition_english_labels_and_placeholders():
    doc = "\n".join(
        [
            "## 1. Problem Definition",
            "- **Goal**: real goal",
            "- **Target Users**: real users",
            "- **Core Value**: real value",
        ]
    )
    values = extract_problem_definition_values(doc)
    assert set(values) == {"项目目标", "目标用户", "核心价值"}
    assert values["项目目标"] == "real goal"
    template_doc = "\n".join(
        [
            "## 1. Problem Definition",
            "- **Goal**: one sentence describing the problem this project solves",
            "- **Target Users**: who will use this product or system",
            "- **Core Value**: the lasting value delivered once complete",
            "### 2.1 Confirmed Facts",
            "- Fact A",
            "### 2.2 Key Assumptions",
            "- Assumption A",
            "### 2.3 Open Questions",
            "- Question A",
            "### 3.3 Out of Scope",
            "- explicitly list what this round will not do",
            "## 4. Minimal Implementation Path",
            "- simplest viable approach: xxx",
            "- not introducing yet: xxx",
            "- abstractions/configurability not built: xxx",
        ]
    )
    gaps = clarification_gaps(template_doc)
    # The unfilled English template is placeholder content in every slot.
    assert len(gaps) == 3 + 4 + 1
    assert "`spec.md` 缺少有效项目目标" in gaps
    assert "`spec.md` 缺少有效区块：### 2.1 已确认事实" in gaps
    assert "`spec.md` 缺少有效区块：## 4. 最小实现路径" in gaps


def test_spec_title_suffix_bilingual():
    assert extract_heading_title_from_content("# Demo - Project Scope\n", "fallback", SPEC_TITLE_SUFFIXES) == "Demo"
    assert extract_heading_title_from_content("# Demo - 项目范围\n", "fallback", SPEC_TITLE_SUFFIXES) == "Demo"
    assert extract_heading_title_from_content("# Demo Package\n", "fallback", SPEC_TITLE_SUFFIXES) == "Demo Package"


def test_acceptance_evidence_english_heading_matches():
    doc = "## Acceptance Evidence\n- script verification: `pytest -q`\n\n**Result**: pass\n"
    fields = extract_evidence_fields(doc)
    assert "script verification" in fields


# --------------------------------------------------------------------------- #
# Full-gate equivalence: one package, two localizations (F18 task_0002)
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_ROOT = REPO_ROOT / ".spec" / "specs" / "archive"
FIXTURE_SLUG = "2026-01-01_bilingual-fixture"

CHINESE_V2_SPEC = """\
# 双语固定件 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：验证中英两种标题的任务包获得同等的门禁结论
- **目标用户**：本仓维护者与公开使用者
- **核心价值**：门禁结论由机器键决定，显示标题可本地化

## 2. 假设与待确认

### 2.1 已确认事实
- 现有归档全部为中文标题包，历史归档永不改写

### 2.2 关键假设
- 键锚点与显示标题不产生第三语义

### 2.3 待确认问题
- 无

### 2.4 可选解释与取舍
- 锚点内联 vs 独立行 -> 理由：两者等价，由折叠层统一

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 功能 A：键识别层

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 存量归档的任何改写

## 4. 最小实现路径
- 最简单可行方案：别名表集中定义
- 暂不引入：per-package locale 配置
- 不做的抽象/配置化：用户自定义标题映射

## 5. 技术决策
- Git integration branch：`spec/2026-01-01_bilingual-fixture`

### 5.1 验证策略
- 范围级别：integration
- 变更对象：解析层与门禁消费面
- 快速检查：`python3 -m pytest tests/test_bilingual_package.py -q`
- 集成检查：`python3 -m pytest -q -k spec_package`
- 全项目检查：仅在收口执行一次
- 升级触发：波及 hooks 契约时升级

### 5.4 编排策略
- route: local

## 6. 成功标准与验证方式
- [x] 双语对照结论一致 -> verify: `python3 -m pytest tests/test_bilingual_package.py -q`

## 7. 风险与约束
- 风险点：别名漏覆盖 -> 缓解：冻结测试
"""

ENGLISH_V2_SPEC = """\
# Bilingual Fixture - Project Scope

<!-- spec-format: 2 -->

## 1. Problem Definition
- **Goal**: prove Chinese- and English-titled packages get identical gate verdicts
- **Target Users**: this repository's maintainers and public users
- **Core Value**: gate verdicts come from machine keys; display titles localize

## 2. Assumptions & Open Questions

### 2.1 Confirmed Facts
- every existing archive is Chinese-titled and archives are never rewritten

### 2.2 Key Assumptions
- key anchors and display titles create no third semantics

### 2.3 Open Questions
- None

### 2.4 Interpretations & Trade-offs
- inline vs standalone anchor -> equivalent, unified by the folding layer

## 3. Feature Scope

### 3.1 Core Features (MVP)
- [x] Feature A: key recognition layer

### 3.2 Extended Features
- None

### 3.3 Out of Scope
- any rewrite of existing archives

## 4. Minimal Implementation Path
- simplest viable approach: one centralized alias table
- not introducing yet: per-package locale configuration
- abstractions/configurability not built: user-defined title mappings

## 5. Technical Decisions
- Git integration branch: `spec/2026-01-01_bilingual-fixture`

### 5.1 Verification Strategy
- scope: integration
- changed surface: parsing layer and gate consumers
- quick check: `python3 -m pytest tests/test_bilingual_package.py -q`
- integration check: `python3 -m pytest -q -k spec_package`
- full-project check: only at closeout
- escalation trigger: hooks contract touched

### 5.4 Orchestration Strategy
- route: local

## 6. Success Criteria & Verification
- [x] bilingual verdicts agree -> verify: `python3 -m pytest tests/test_bilingual_package.py -q`

## 7. Risks & Constraints
- risk: alias coverage gap -> mitigation: frozen-set tests
"""

FIXTURE_TASKS = """\
## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`

## 阶段一：实现
- [x] task one completed
  - boundary: parsing layer only
  - verify: `python3 -m pytest tests/test_bilingual_package.py -q`
"""

FIXTURE_TASKS_EN = """\
## Usage Rules
- every task carries `boundary` and `verify`

## Stage 1: Implementation
- [x] task one completed
  - boundary: parsing layer only
  - verify: `python3 -m pytest tests/test_bilingual_package.py -q`
"""


def _gate_summary(spec: str, tasks: str, checklist: str | None = None, slug: str = FIXTURE_SLUG):
    results = compute_gate_results(spec, tasks, checklist, slug=slug)
    return {
        "overall_ok": results.overall_ok,
        "base_ok": results.base_ok,
        "assumptions_ok": results.assumptions_ok,
        "simplicity_ok": results.simplicity_ok,
        "boundary_ok": results.boundary_ok,
        "function_ok": results.function_ok,
        "passed_checklist": results.passed_checklist,
        "gaps": results.spec_clarification_gaps,
        "pending": results.pending_questions,
        "orchestration_errors": results.orchestration_errors,
        "verification_scope": results.verification_scope,
        "verification_scope_errors": results.verification_scope_errors,
        "template_warning": results.template_warning,
        "success_criteria_ok": results.success_criteria_ok,
    }


def test_full_gate_verdicts_are_identical_across_localizations():
    chinese = _gate_summary(CHINESE_V2_SPEC, FIXTURE_TASKS)
    english = _gate_summary(ENGLISH_V2_SPEC, FIXTURE_TASKS_EN)
    assert chinese["overall_ok"] is True, chinese
    assert chinese == english


def test_full_gate_failures_are_identical_across_localizations():
    broken_zh = CHINESE_V2_SPEC.replace("- 现有归档全部为中文标题包，历史归档永不改写", "- 事实 A")
    broken_en = ENGLISH_V2_SPEC.replace(
        "- every existing archive is Chinese-titled and archives are never rewritten",
        "- Fact A",
    )
    chinese = _gate_summary(broken_zh, FIXTURE_TASKS)
    english = _gate_summary(broken_en, FIXTURE_TASKS_EN)
    assert chinese["overall_ok"] is False
    assert chinese == english
    assert "`spec.md` 缺少有效区块：### 2.1 已确认事实" in chinese["gaps"]


def test_mixed_v1_checklist_with_english_headings_keeps_legacy_gates():
    """Legacy checklists are frozen-Chinese field contracts; their *headings*
    still localize (keyed), which is what an English-titled archive render
    would exercise. Canonical field keys keep the historical gates intact."""
    spec = CHINESE_V2_SPEC.replace("<!-- spec-format: 2 -->\n\n", "")
    checklist_zh = "\n".join(
        [
            "## 验收证据",
            "- 脚本验证：`python3 -m pytest tests/test_bilingual_package.py -q` 全绿",
            "- 验证范围：integration",
            "",
            "**验收结果**：通过",
        ]
    )
    checklist_en_headings = "\n".join(
        [
            "## Acceptance Evidence",
            "- 脚本验证：`python3 -m pytest tests/test_bilingual_package.py -q` 全绿",
            "- 验证范围：integration",
            "",
            "**Result**: pass",
        ]
    )
    chinese = _gate_summary(spec, FIXTURE_TASKS, checklist_zh)
    english = _gate_summary(spec, FIXTURE_TASKS, checklist_en_headings)
    assert chinese["overall_ok"] is True, chinese
    assert chinese == english


def test_real_archived_chinese_packages_keep_pre_keying_verdicts():
    """Verdict lock against real archives, captured before the keying change
    (rg-audited snapshot, 2026-10-13): conclusions must not move."""
    expectations = {
        "2026-10-06_add-autorun-command": {
            "checklist_present": True,
            "overall_ok": True,
            "total_tasks": 9,
            "completed_tasks": 9,
            "verification_scope": "integration",
        },
        "2026-10-13_gate-goal-push-and-organize-apply": {
            "checklist_present": False,
            "overall_ok": True,
            "total_tasks": 4,
            "completed_tasks": 4,
            "verification_scope": "integration",
        },
    }
    for slug, expected in expectations.items():
        package = ARCHIVE_ROOT / slug
        spec = (package / "spec.md").read_text(encoding="utf-8")
        tasks = (package / "tasks.md").read_text(encoding="utf-8")
        checklist_path = package / "checklist.md"
        checklist = checklist_path.read_text(encoding="utf-8") if checklist_path.exists() else None
        results = compute_gate_results(spec, tasks, checklist, slug=slug)
        assert results.checklist_present == expected["checklist_present"], slug
        assert results.overall_ok is expected["overall_ok"], slug
        assert results.total_tasks == expected["total_tasks"], slug
        assert results.completed_tasks == expected["completed_tasks"], slug
        assert results.verification_scope == expected["verification_scope"], slug
        assert results.spec_clarification_gaps == [], slug
        assert results.pending_questions == [], slug
        assert results.orchestration_errors == [], slug

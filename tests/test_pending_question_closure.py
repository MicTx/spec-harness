"""Question closure is represented by checkboxes, answers and decision authority."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_spec_package import compute_gate_results, gate_failure_details  # noqa: E402
from spec_package_support import (  # noqa: E402
    clarification_gaps,
    extract_pending_questions,
    spec_format_version,
)

SLUG = "2026-09-13_fix-address-matching"


def _make_spec() -> str:
    return (
        """# 地址匹配 - 项目范围
<!-- spec-format: 2 -->
## 1. 问题定义
- **项目目标**：完成地址匹配前端
- **目标用户**：地址查询用户
- **核心价值**：如实提供匹配与地图状态
## 2. 假设与待确认
### 2.1 已确认事实
- 已确认
### 2.2 关键假设
- 部署配置由部署方维护
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不更新部署侧配置
## 4. 最小实现路径
- 保留现有接口
- 实现前端地图
- 验证未配置行为
## 5. 技术决策
## 6. 成功标准与验证方式
- [x] 地图状态符合预期
"""
        + f"- Git integration branch：`spec/{SLUG}`\n"
    )


def _make_tasks(done: bool = True) -> str:
    return (
        f"- [{'x' if done else ' '}] 实现前端地图\n  - boundary: 只修改前端地图\n  - verify: `python3 -c 'print(1)'`\n"
    )


def _make_checklist(passed: bool = True) -> str:
    return (
        "## 基础\n- [x] 地图状态回归\n"
        "## 验收证据\n- 脚本验证：pytest -q\n"
        f"**验收结果**：{'通过' if passed else '待修复'}\n"
    )


def spec_with_questions(items: tuple[str, ...] | list[str]) -> str:
    return _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n" + "".join(f"- {item}\n" for item in items),
    )


@pytest.mark.parametrize("item", ["无", "无。", "none", "None."])
def test_explicit_empty_section_has_no_pending_question(item):
    spec = spec_with_questions([item])
    assert extract_pending_questions(spec) == []
    results = compute_gate_results(spec, _make_tasks(), _make_checklist())
    assert not results.pending_questions
    assert not results.question_errors
    assert results.overall_ok


@pytest.mark.parametrize(
    "question",
    [
        "[ ] [decision] 是否发布 | reason: permission",
        "[ ] [fact] 配置格式是什么",
        "[ ] [reversible] 是否复用接口",
        "[ ] 是否兼容 3.8",
        "是否授权新事项？",
    ],
)
def test_open_questions_remain_pending_and_block_delivery(question):
    spec = spec_with_questions([question])
    results = compute_gate_results(spec, _make_tasks(), _make_checklist())
    assert len(extract_pending_questions(spec)) == 1
    assert results.pending_questions == extract_pending_questions(spec)
    assert not results.overall_ok
    assert "待确认问题" in gate_failure_details(results)


@pytest.mark.parametrize("kind", ["fact", "reversible", "decision", "unclassified"])
@pytest.mark.parametrize("answer", ["", " ", "待补充", "待完善", "N/A", "xxx"])
def test_closed_question_requires_non_placeholder_answer(kind, answer):
    label = "" if kind == "unclassified" else f"[{kind}] "
    authority = " | authority: user:本次确认" if kind == "decision" else ""
    spec = spec_with_questions([f"[x] {label}检查范围 | answer: {answer}{authority}"])
    results = compute_gate_results(spec, _make_tasks(), _make_checklist())
    assert results.pending_questions == []
    assert any("answer:" in error for error in results.question_errors)
    assert not results.overall_ok
    assert "待确认问题" in gate_failure_details(results)


@pytest.mark.parametrize("kind", ["fact", "reversible", "unclassified"])
def test_closed_non_decision_needs_only_an_answer(kind):
    label = "" if kind == "unclassified" else f"[{kind}] "
    spec = spec_with_questions([f"[x] {label}配置格式 | answer: JSON"])
    results = compute_gate_results(spec, _make_tasks(), _make_checklist())
    assert not results.pending_questions
    assert not results.question_errors
    assert results.overall_ok


@pytest.mark.parametrize("authority", ["", "agent:self", "user:", "user:待补充", "user:xxx"])
def test_closed_decision_rejects_missing_or_non_user_authority(authority):
    spec = spec_with_questions([f"[x] [decision] 是否发布 | answer: 不发布 | authority: {authority}"])
    results = compute_gate_results(spec, _make_tasks(), _make_checklist())
    assert any("authority: user:" in error for error in results.question_errors)
    assert not results.overall_ok


def test_closed_decision_with_user_reference_passes_question_gate():
    spec = spec_with_questions(["[x] [decision] 是否发布 | answer: 不发布 | authority: user:本次确认"])
    results = compute_gate_results(spec, _make_tasks(), _make_checklist())
    assert not results.pending_questions
    assert not results.question_errors
    assert results.overall_ok


def test_section_does_not_read_questions_in_later_headings():
    spec = spec_with_questions(["[ ] 问题甲", "[x] 问题乙 | answer: 已确定", "[ ] 问题丙"])
    spec += "\n## 10. 附录\n- [ ] 不在问题区的事项\n"
    assert extract_pending_questions(spec) == ["问题甲", "问题丙"]
    results = compute_gate_results(spec, _make_tasks(), _make_checklist())
    assert results.pending_questions == ["问题甲", "问题丙"]


@pytest.mark.parametrize("items", [[], ["问题 A"], ["Question A"]])
def test_missing_or_template_section_requires_clarification(items):
    spec = spec_with_questions(items)
    results = compute_gate_results(spec, _make_tasks(), _make_checklist())
    assert any("2.3" in gap for gap in clarification_gaps(spec))
    assert not results.overall_ok


@pytest.mark.parametrize("item", ["待补充", "N/A"])
def test_unresolved_placeholder_item_blocks_as_pending(item):
    spec = spec_with_questions([item])
    results = compute_gate_results(spec, _make_tasks(), _make_checklist())
    assert results.pending_questions == [item]
    assert not results.overall_ok


def test_format_marker_is_informational():
    assert spec_format_version(_make_spec()) == 2
    assert spec_format_version(_make_spec().replace("<!-- spec-format: 2 -->\n", "")) == 1


def test_legacy_free_text_is_not_inferred_as_a_closed_question():
    item = "~~地图服务允许源待部署方提供~~ → 用户追加授权"
    spec = spec_with_questions([item])
    assert extract_pending_questions(spec) == [item]
    assert not compute_gate_results(spec, _make_tasks(), _make_checklist()).overall_ok

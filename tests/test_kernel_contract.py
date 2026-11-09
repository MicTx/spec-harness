"""Delivery contract: plain answers, one final batch, no format ceremonies."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_spec_package import compute_gate_results  # noqa: E402
from spec_package_support import (  # noqa: E402
    autonomous_question_contract,
    verification_scope_errors,
    workflow_ledger_errors,
)

SPEC = """# Fixture
<!-- spec-format: 2 -->
## 1. 问题定义
- **项目目标**：Verify the delivered output.
## 2. 假设与待确认
### 2.2 关键假设
- 无
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- Remote publication.
## 4. 最小实现路径
- One command.
## 5. 技术决策
- Workflow: LITE
- 证据协议：evidence: runs
### 5.1 验证策略
- 范围级别：project
- 快速检查：`python3 -c 'print(1)'`
## 6. 成功标准与验证方式
- [x] Output exists -> verify: task_0001
"""
TASKS = "- [x] Verify output\n  - boundary: fixture\n  - verify: `python3 -c 'print(1)'`\n"


def test_scope_needs_only_level_and_one_real_command():
    assert verification_scope_errors(SPEC) == []
    assert verification_scope_errors(SPEC.replace("project", "invalid"))


def test_closed_questions_need_answers_but_not_task_fingerprints():
    spec = SPEC.replace("- 无\n## 3.", "- [x] [fact] Output shape | answer: a JSON list\n## 3.")
    questions, pending, errors = autonomous_question_contract(spec, "")
    assert not pending
    assert not errors
    assert questions[0].closed


@pytest.mark.parametrize("authority,valid", [("", False), ("agent:checked", False), ("user:current goal", True)])
def test_decision_authority_is_required(authority, valid):
    question = f"- [x] [decision] Publish? | answer: local only | authority: {authority}"
    spec = SPEC.replace("- 无\n## 3.", question + "\n## 3.")
    assert (not autonomous_question_contract(spec, "")[2]) is valid


def test_full_requires_literal_and_an_alternative_only():
    spec = SPEC.replace("Workflow: LITE", "Workflow: FULL")
    spec += (
        "\n- **Literal ask**: finish this task\n### 4.1 方案阶梯\n"
        "- B0 | implement directly\n- B1 | reuse an existing function\n"
    )
    assert workflow_ledger_errors(spec) == []


def test_optional_formatting_is_advisory():
    spec = SPEC + "\n### 5.5 修正日志\n- no formatted table\n"
    assert compute_gate_results(spec, TASKS, None).overall_ok


def test_show_uses_task_and_tail_not_page_tokens(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "spec_verify.py"
    result = subprocess.run([sys.executable, str(script), "show", "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--tail" in result.stdout
    assert "--page-token" not in result.stdout

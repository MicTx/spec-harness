"""Delivery gate contracts: six checks, v1 fallback, render and CLI.

The de-defense contract collapsed the gate to six delivery checks:
definition, tasks/boundaries, pending questions, success criteria, runs
evidence, branch binding. Everything that inspected prose style, anchors or
boundary-regression bookkeeping is gone; those behaviours are intentionally
not tested here anymore.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_spec_package import (
    compute_gate_results,
    convergence_state,
    gate_failure_details,
    gate_rows,
    has_blocked_tasks,
    is_likely_template,
    overall_check_passed,
    parse_tasks,
    render,
    result_label,
)

CHECK_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_spec_package.py"

# -- parse_tasks --


def test_parse_tasks_all_checked():
    content = """\
## Phase
- [x] Task A
  - boundary: only x
  - verify: passes
- [x] Task B
  - boundary: only y
  - verify: works
"""
    assert parse_tasks(content) == (2, 2, 0, 0)


def test_parse_tasks_missing_boundary():
    assert parse_tasks("- [ ] Task without boundary\n  - verify: something\n") == (1, 0, 1, 0)


def test_parse_tasks_missing_verify():
    assert parse_tasks("- [ ] Task without verify\n  - boundary: something\n") == (1, 0, 0, 1)


def test_parse_tasks_mixed():
    content = """\
- [x] Done task
  - boundary: x
  - verify: x
- [ ] Incomplete task
- [ ] Another incomplete
  - boundary: only y
"""
    assert parse_tasks(content) == (3, 1, 1, 2)


# -- is_likely_template --


TEMPLATE_TASKS = """\
## 阶段一：澄清与初始化
- [ ] 实现核心功能 A
  - boundary: 只更新 spec.md
  - verify: spec.md 已写明
- [ ] test_critical_path
  - boundary: 只创建必须目录
  - verify: 最小启动命令通过
"""

USER_TASKS = """\
## 阶段一
- [ ] 实现支付回调
  - boundary: 仅支付模块
  - verify: 回调测试通过
"""


def test_is_likely_template_true():
    assert is_likely_template(TEMPLATE_TASKS) is True


def test_is_likely_template_false():
    assert is_likely_template(USER_TASKS) is False


def test_is_likely_template_mixed_rows_are_not_template():
    assert is_likely_template("- [ ] 固化问题定义、关键假设与非目标\n- [ ] 实现支付回调\n") is False


def test_result_label():
    assert result_label(True) == "通过"
    assert result_label(False) == "待修复"
    assert result_label(False, partial=True) == "部分通过"


# -- fixtures --


def _make_spec(*, goal="- **项目目标**：实际目标", success="- [x] 完成 A", extra=""):
    return f"""\
# Test - 项目范围

## 1. 问题定义
{goal}

## 2. 假设与待确认
### 2.2 关键假设
- 假设
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不做 X
## 4. 最小实现路径
- 路径1
## 6. 成功标准
{success}
{extra}
"""


def _make_tasks(done=True, marker=None, details=True):
    token = marker if marker is not None else ("x" if done else " ")
    detail = "\n  - boundary: only x\n  - verify: passes" if details else ""
    return f"## Phase\n- [{token}] Task A{detail}\n"


def _converged(**overrides):
    spec = overrides.pop("spec", _make_spec())
    tasks = overrides.pop("tasks", _make_tasks(done=True))
    return compute_gate_results(spec, tasks, overrides.pop("checklist", None), **overrides)


# -- the six delivery checks --


def test_gate_rows_are_exactly_six_delivery_checks():
    labels = [label for label, _passed, _detail in gate_rows(_converged())]
    assert labels == ["项目定义", "任务与边界", "待确认问题", "成功标准", "运行证据", "分支绑定"]


def test_converged_package_passes_every_applicable_gate():
    results = _converged()
    assert results.overall_ok is True
    statuses = {label: passed for label, passed, _detail in gate_rows(results)}
    assert statuses["项目定义"] is True
    assert statuses["任务与边界"] is True
    assert statuses["待确认问题"] is True
    assert statuses["成功标准"] is True
    assert statuses["运行证据"] is None  # not evaluated without the runs protocol
    assert statuses["分支绑定"] is True
    assert gate_failure_details(results) == {}
    assert convergence_state(results) == (True, [])


def test_unchecked_task_fails_task_gate():
    results = _converged(tasks=_make_tasks(done=False))
    failures = gate_failure_details(results)
    assert "任务与边界" in failures
    assert "任务完成 0/1" in failures["任务与边界"]
    assert convergence_state(results)[0] is False


@pytest.mark.parametrize("details", [True, False])
def test_missing_boundary_or_verify_fails_task_gate(details):
    tasks = "## Phase\n- [x] Task A\n" if not details else "## Phase\n- [x] Task A\n  - boundary: x\n"
    results = _converged(tasks=tasks)
    assert "任务与边界" in gate_failure_details(results)


def test_template_tasks_fail_definition_gate():
    results = _converged(tasks=TEMPLATE_TASKS)
    assert "任务仍是初始化模板" in gate_failure_details(results)["项目定义"]


def test_missing_goal_fails_definition_gate():
    results = _converged(spec=_make_spec(goal="- **项目目标**：一句话描述项目要解决什么问题"))
    assert "缺少有效项目目标" in gate_failure_details(results)["项目定义"]


def test_missing_required_section_fails_definition_gate():
    spec = _make_spec().replace("### 3.3 不在范围内\n- 不做 X\n", "")
    results = _converged(spec=spec)
    assert "不在范围内" in gate_failure_details(results)["项目定义"]


def test_open_question_fails_question_gate():
    spec = _make_spec().replace("### 2.3 待确认问题\n- 无\n", "### 2.3 待确认问题\n- [ ] 是否支持离线？\n")
    results = _converged(spec=spec)
    assert results.pending_questions
    assert "待确认问题" in gate_failure_details(results)


def test_closed_decision_without_user_authority_fails_question_gate():
    spec = _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n- [x] [decision] 选 A？ | answer: A\n",
    )
    results = _converged(spec=spec)
    assert results.question_errors
    assert "待确认问题" in gate_failure_details(results)


def test_closed_decision_with_user_authority_passes_question_gate():
    spec = _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n- [x] [decision] 选 A？ | answer: A | authority: user:boss\n",
    )
    assert _converged(spec=spec).overall_ok is True


def test_unchecked_success_criterion_fails_success_gate():
    results = _converged(spec=_make_spec(success="- [x] 完成 A\n- [ ] 完成 B"))
    detail = gate_failure_details(results)["成功标准"]
    assert "SPEC-SUCCESS-001" in detail
    assert "1/2" in detail


def test_absent_success_section_without_checklist_fails_success_gate():
    spec = _make_spec(success="")
    results = _converged(spec=spec)
    assert "成功标准" in gate_failure_details(results)


def test_v1_checklist_passed_marks_success_when_section_six_absent():
    spec = _make_spec(success="")
    results = _converged(spec=spec, checklist="# c\n\n**验收结果**：通过\n")
    assert results.success_criteria_total == 1
    assert results.success_criteria_checked == 1
    assert "成功标准" not in gate_failure_details(results)


def test_v1_checklist_pending_fails_success_gate():
    spec = _make_spec(success="")
    results = _converged(spec=spec, checklist="# c\n\n**验收结果**：待修复\n")
    assert "成功标准" in gate_failure_details(results)


def test_runs_protocol_without_records_fails_evidence_gate(tmp_path):
    spec = _make_spec() + "\nevidence: runs\n"
    tasks = "## Phase\n- [x] Task A\n  - boundary: only x\n  - verify: `true`\n"
    package = tmp_path / ".spec" / "specs" / "2026-06-12_fix-test"
    package.mkdir(parents=True)
    results = compute_gate_results(spec, tasks, None, slug="2026-06-12_fix-test", root=tmp_path, package_dir=package)
    assert results.runs_gate_ok is False
    assert any("SPEC-EVID-001" in error for error in results.runs_errors)
    assert "运行证据" in gate_failure_details(results)


def test_branch_binding_failure_is_a_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "check_spec_package.validate_branch_bound_package",
        lambda *_args, **_kwargs: (False, "当前分支与包绑定不一致"),
    )
    results = compute_gate_results(_make_spec(), _make_tasks(), None, slug="2026-06-12_fix-test", root=tmp_path)
    assert gate_failure_details(results)["分支绑定"] == "当前分支与包绑定不一致"


def test_branch_binding_not_evaluated_without_root_and_slug():
    results = compute_gate_results(_make_spec(), _make_tasks())
    assert any(label == "分支绑定" and passed for label, passed, _detail in gate_rows(results))


# -- blocked markers --


def test_blocked_marker_is_authoritative():
    assert has_blocked_tasks("## s\n- [!] 等待外部系统\n") is True
    assert has_blocked_tasks("## s\n阻塞：等待外部系统\n") is True


def test_completed_title_with_block_word_is_not_blocked():
    assert has_blocked_tasks("## s\n- [x] 修复阻塞显示\n") is False


def test_unchecked_title_with_block_word_is_not_a_marker():
    assert has_blocked_tasks("## s\n- [ ] task 阻塞：waiting\n") is False


def test_blocked_task_fails_task_gate():
    results = _converged(tasks="## Phase\n- [!] 等待外部系统\n  - boundary: x\n  - verify: x\n")
    assert results.blocked_tasks is True
    assert "任务与边界" in gate_failure_details(results)


# -- verification scope --


def test_valid_verification_scope_does_not_block():
    spec = _make_spec(extra="### 5.1 验证策略\n- 范围级别：package\n- 快速检查：`pytest -q`\n")
    results = _converged(spec=spec)
    assert results.verification_scope == "package"
    assert results.verification_scope_errors == []


def test_invalid_verification_scope_level_blocks_definition():
    spec = _make_spec(extra="### 5.1 验证策略\n- 范围级别：whole-world\n- 快速检查：`pytest -q`\n")
    results = _converged(spec=spec)
    assert "项目定义" in gate_failure_details(results)


def test_placeholder_quick_check_blocks_definition():
    spec = _make_spec(extra="### 5.1 验证策略\n- 范围级别：package\n- 快速检查：<待填写>\n")
    results = _converged(spec=spec)
    assert "SPEC-VERIFY-001" in gate_failure_details(results)["项目定义"]


# -- overall_check_passed / render --


def test_overall_check_passes_converged_package():
    assert overall_check_passed(_make_spec(), _make_tasks(done=True)) is True


def test_overall_check_fails_incomplete_package():
    assert overall_check_passed(_make_spec(), _make_tasks(done=False)) is False


def test_overall_check_accepts_v1_checklist_acceptance():
    assert overall_check_passed(_make_spec(success=""), _make_tasks(), "**验收结果**：通过\n") is True


def test_render_converged_package():
    output = render("2026-06-12_fix-test", "Test", _make_spec(), _make_tasks(done=True))
    assert output.startswith("# Test\n")
    assert "- 已完成：1/1" in output
    assert "整理交付结果并完成归档" in output
    assert "- 收敛状态：已收敛" in output
    assert "## 流水线" not in output
    assert "门禁" not in output


def test_render_incomplete_package():
    output = render("2026-06-12_fix-test", "Test", _make_spec(), _make_tasks(done=False))
    assert "- 已完成：0/1" in output
    assert "任务完成 0/1" in output
    assert "- 收敛状态：未收敛" in output
    assert "- 差距：" in output
    assert "## 正在处理" not in output


def test_render_includes_definition_gaps():
    output = render(
        "2026-06-12_fix-test",
        "Test",
        _make_spec(goal="- **项目目标**：一句话描述项目要解决什么问题"),
        _make_tasks(),
    )
    assert "## 需要关注" in output
    assert "项目定义：" in output
    assert "缺少有效项目目标" in output


def test_render_uses_precomputed_gate_results(monkeypatch):
    spec, tasks = _make_spec(), _make_tasks(done=True)
    results = compute_gate_results(spec, tasks, slug="2026-06-12_fix-render")

    def fail_if_recomputed(*_args, **_kwargs):
        raise AssertionError("render recomputed gate results")

    monkeypatch.setattr("check_spec_package.compute_gate_results", fail_if_recomputed)
    output = render("2026-06-12_fix-render", "Test", spec, tasks, results=results)
    assert output.startswith("# Test\n")
    assert "- 收敛状态：已收敛" in output


# -- CLI --


def _write_package(root: Path, slug: str, spec: str | None = None, tasks: str | None = None) -> Path:
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(spec or _make_spec(), encoding="utf-8")
    (package / "tasks.md").write_text(tasks or _make_tasks(done=True), encoding="utf-8")
    return package


def _run_check(*args):
    return subprocess.run([sys.executable, str(CHECK_SCRIPT), *args], capture_output=True, text=True)


def test_cli_exit_code_matches_gate():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        slug = "2026-07-13_fix-check-exit"
        _write_package(root, slug)
        passed = _run_check("--root", str(root), "--slug", slug)
        assert passed.returncode == 0, passed.stderr
        failing = _run_check("--root", str(root), "--slug", slug)  # still green
        assert failing.returncode == 0
        (root / ".spec" / "specs" / slug / "tasks.md").write_text(_make_tasks(done=False), encoding="utf-8")
        failed = _run_check("--root", str(root), "--slug", slug)
        assert failed.returncode == 1
        assert "任务完成 0/1" in failed.stdout


def test_cli_json_contract():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        slug = "2026-07-13_fix-check-json"
        _write_package(root, slug)
        completed = _run_check("--root", str(root), "--slug", slug, "--format", "json")
        payload = json.loads(completed.stdout)
        assert payload["slug"] == slug
        assert payload["overall"] is True
        assert payload["converged"] is True
        assert payload["gaps"] == []
        assert payload["overallGaps"] == []
        assert payload["openDecisions"] == []
        assert payload["tasksDone"] is True
        assert payload["archived"] is False


def test_cli_archived_package_is_frozen():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        slug = "2026-07-13_fix-check-archive"
        archived = root / ".spec" / "specs" / "archive" / slug
        archived.mkdir(parents=True)
        completed = _run_check("--root", str(root), "--slug", slug, "--format", "json")
        assert completed.returncode == 0
        payload = json.loads(completed.stdout)
        assert payload == {"slug": slug, "overall": True, "converged": True, "gaps": [], "archived": True}


def test_cli_missing_package_errors():
    with tempfile.TemporaryDirectory() as tmpdir:
        completed = _run_check("--root", tmpdir, "--slug", "2026-07-13_fix-missing")
        assert completed.returncode == 1
        assert "error:" in completed.stderr

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
    gate_recovery_routes,
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
    assert result_label(True) == "pass"
    assert result_label(False) == "needs fixes"
    assert result_label(False, partial=True) == "partial pass"


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
    assert labels == [
        "Project definition",
        "Tasks and boundaries",
        "Open questions",
        "Success criteria",
        "Runs evidence",
        "Branch binding",
    ]


def test_converged_package_passes_every_applicable_gate():
    results = _converged()
    assert results.overall_ok is True
    statuses = {label: passed for label, passed, _detail in gate_rows(results)}
    assert statuses["Project definition"] is True
    assert statuses["Tasks and boundaries"] is True
    assert statuses["Open questions"] is True
    assert statuses["Success criteria"] is True
    assert statuses["Runs evidence"] is None  # not evaluated without the runs protocol
    assert statuses["Branch binding"] is True
    assert gate_failure_details(results) == {}
    assert convergence_state(results) == (True, [])


def test_unchecked_task_fails_task_gate():
    results = _converged(tasks=_make_tasks(done=False))
    failures = gate_failure_details(results)
    assert "Tasks and boundaries" in failures
    assert "tasks complete 0/1" in failures["Tasks and boundaries"]
    assert convergence_state(results)[0] is False


@pytest.mark.parametrize("details", [True, False])
def test_missing_boundary_or_verify_fails_task_gate(details):
    tasks = "## Phase\n- [x] Task A\n" if not details else "## Phase\n- [x] Task A\n  - boundary: x\n"
    results = _converged(tasks=tasks)
    assert "Tasks and boundaries" in gate_failure_details(results)


def test_template_tasks_fail_definition_gate():
    results = _converged(tasks=TEMPLATE_TASKS)
    assert "tasks are still the initialization template" in gate_failure_details(results)["Project definition"]


def test_missing_goal_fails_definition_gate():
    results = _converged(spec=_make_spec(goal="- **项目目标**：一句话描述项目要解决什么问题"))
    assert "is missing a valid goal" in gate_failure_details(results)["Project definition"]


def test_missing_required_section_fails_definition_gate():
    spec = _make_spec().replace("### 3.3 不在范围内\n- 不做 X\n", "")
    results = _converged(spec=spec)
    assert "Out of Scope" in gate_failure_details(results)["Project definition"]


def test_open_question_fails_question_gate():
    spec = _make_spec().replace("### 2.3 待确认问题\n- 无\n", "### 2.3 待确认问题\n- [ ] 是否支持离线？\n")
    results = _converged(spec=spec)
    assert results.pending_questions
    assert "Open questions" in gate_failure_details(results)


def test_closed_decision_without_user_authority_fails_question_gate():
    spec = _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n- [x] [decision] 选 A？ | answer: A\n",
    )
    results = _converged(spec=spec)
    assert results.question_errors
    assert "Open questions" in gate_failure_details(results)


def test_closed_decision_with_user_authority_passes_question_gate():
    spec = _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n- [x] [decision] 选 A？ | answer: A | authority: user:boss\n",
    )
    assert _converged(spec=spec).overall_ok is True


def test_unchecked_success_criterion_fails_success_gate():
    results = _converged(spec=_make_spec(success="- [x] 完成 A\n- [ ] 完成 B"))
    detail = gate_failure_details(results)["Success criteria"]
    assert "SPEC-SUCCESS-001" in detail
    assert "1/2" in detail


def test_absent_success_section_without_checklist_fails_success_gate():
    spec = _make_spec(success="")
    results = _converged(spec=spec)
    assert "Success criteria" in gate_failure_details(results)


def test_v1_checklist_passed_marks_success_when_section_six_absent():
    spec = _make_spec(success="")
    results = _converged(spec=spec, checklist="# c\n\n**验收结果**：通过\n")
    assert results.success_criteria_total == 1
    assert results.success_criteria_checked == 1
    assert "Success criteria" not in gate_failure_details(results)


def test_v1_checklist_pending_fails_success_gate():
    spec = _make_spec(success="")
    results = _converged(spec=spec, checklist="# c\n\n**验收结果**：待修复\n")
    assert "Success criteria" in gate_failure_details(results)


# -- recovery routing: check -> update/run/authority/environment --


@pytest.mark.parametrize("marker", ["x", "X"])
def test_resolved_task_detail_does_not_remain_a_blocker(marker):
    tasks = f"## Phase\n- [{marker}] Done\n  - boundary: x\n  - verify: `true`\n  - blocked: historical, resolved\n"
    results = _converged(tasks=tasks)
    assert results.blocked_tasks is False
    assert results.overall_ok


@pytest.mark.parametrize(
    "blocked_line", ["  blocked: approval required", "  - blocked: release freeze", "\t阻塞：等待审批"]
)
def test_unowned_indented_blocker_fails_completed_gate(blocked_line):
    tasks = f"## Status\n{blocked_line}\n## Phase\n- [x] Done\n  - boundary: x\n  - verify: `true`\n"
    results = _converged(tasks=tasks)
    assert results.blocked_tasks
    assert not results.overall_ok
    assert gate_recovery_routes(results)["Tasks and boundaries"] == "environment"


def test_indented_global_blocker_before_first_task_prevents_archive_after_parsing(tmp_path):
    root = tmp_path
    slug = "2026-11-09_indented-global-blocker"
    spec = _make_spec()
    tasks = "## Status\n  blocked: owner approval required\n## Phase\n- [x] Done\n  - boundary: x\n  - verify: `true`\n"
    package = _write_package(root, slug, spec=spec, tasks=tasks)
    check = _run_check("--root", str(root), "--slug", slug, "--format", "json")
    payload = json.loads(check.stdout)
    assert check.returncode == 1 and payload["overall"] is False
    complete = subprocess.run(
        [
            sys.executable,
            str(CHECK_SCRIPT.with_name("complete_spec_package.py")),
            "--root",
            str(root),
            "--slug",
            slug,
            "--archive",
        ],
        capture_output=True,
        text=True,
    )
    assert complete.returncode != 0
    assert package.is_dir()


def test_unowned_blocker_after_completed_task_prevents_gate():
    tasks = "## Phase\n- [x] Done\n  - boundary: x\n  - verify: `true`\n\n  blocked: approval required\n"
    results = _converged(tasks=tasks)
    assert results.package_blocked and not results.overall_ok


def test_v2_empty_success_is_not_replaced_by_legacy_checklist():
    spec = _make_spec(success="").replace("# Test - 项目范围", "# Test - 项目范围\n<!-- spec-format: 2 -->")
    for checklist in ("# c\n**验收结果**：待修复\n", "# c\n**验收结果**：通过\n"):
        results = _converged(spec=spec, checklist=checklist)
        assert results.success_criteria_total == 0
        assert gate_recovery_routes(results)["Success criteria"] == "update"


def test_closed_fact_title_cannot_spoof_decision_authority_error():
    spec = _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n- [x] [fact] Why does closing a decision requires approval?\n",
    )
    results = _converged(spec=spec)
    assert results.authority_question_errors == []
    assert gate_recovery_routes(results) == {"Open questions": "update"}


def test_closed_decision_title_preserves_error_marker_text():
    title = "What if closing a decision requires approval?"
    spec = _make_spec().replace("### 2.3 待确认问题\n- 无\n", f"### 2.3 待确认问题\n- [x] [decision] {title}\n")
    results = _converged(spec=spec)
    assert all(title in error for error in results.authority_question_errors)
    assert gate_recovery_routes(results) == {"Open questions": "authority"}


def test_title_blocked_without_marker_gets_environment_recovery():
    tasks = "## Phase\n- [ ] blocked: waiting for service\n  - boundary: local\n  - verify: `true`\n"
    results = _converged(tasks=tasks)
    assert "Tasks and boundaries" in gate_failure_details(results)
    assert gate_recovery_routes(results)["Tasks and boundaries"] == "environment"


def test_unreadable_runs_path_cannot_spoof_verify_error(tmp_path):
    root = tmp_path / "SPEC-VERIFY-002"
    package = root / ".spec" / "specs" / "2026-11-09_corrupt-evidence"
    package.mkdir(parents=True)
    (package / "runs.jsonl").mkdir()
    spec = _make_spec() + "\nevidence: runs\n"
    results = compute_gate_results(spec, "- [x] A\n  - boundary: x\n  - verify: `true`\n", None, None, root, package)
    assert results.runs_errors[0].startswith("[SPEC-EVID-003]")
    assert gate_recovery_routes(results)["Runs evidence"] == "environment"


def test_unreadable_runs_path_ending_with_verify_error_stays_environment(tmp_path):
    message = "verify requires a backtick command or a manual: reason"
    package = tmp_path / message / ".spec" / "specs" / "2026-11-09_corrupt-evidence"
    package.mkdir(parents=True)
    (package / "runs.jsonl").mkdir()
    spec = _make_spec() + "\nevidence: runs\n"
    results = compute_gate_results(
        spec, "- [x] A\n  - boundary: x\n  - verify: `true`\n", None, None, tmp_path, package
    )
    assert gate_recovery_routes(results)["Runs evidence"] == "environment"


def test_invalid_only_ready_verify_maps_both_gates_to_update(tmp_path):
    package = tmp_path / ".spec" / "specs" / "2026-11-09_invalid-only"
    package.mkdir(parents=True)
    spec = _make_spec() + "\nevidence: runs\n"
    tasks = "## Phase\n- [ ] Invalid\n  - boundary: x\n  - verify: passes\n"
    results = compute_gate_results(spec, tasks, None, None, tmp_path, package)
    assert results.ready_tasks == 0
    routes = gate_recovery_routes(results)
    assert routes["Tasks and boundaries"] == "update"
    assert routes["Runs evidence"] == "update"


def test_wrong_branch_check_does_not_recommend_ready_task(tmp_path):
    slug = "2026-11-09_branch-check"
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    spec = _make_spec() + f"\n- Git integration branch: `spec/{slug}`\n"
    tasks = "## Phase\n- [ ] Implement\n  - boundary: local\n  - verify: `true`\n"
    _write_package(tmp_path, slug, spec=spec, tasks=tasks)
    result = _run_check("--root", str(tmp_path), "--slug", slug, "--format", "json")
    payload = json.loads(result.stdout)
    assert payload["recovery"]["Branch binding"] == "environment"
    assert all("run" not in value.split("+") for value in payload["recovery"].values())
    markdown = _run_check("--root", str(tmp_path), "--slug", slug).stdout
    assert "Switch to the package branch" in markdown.split("## Next")[-1]
    assert "Implement" not in markdown.split("## Next")[-1]


def test_invalid_legacy_task_ids_preserve_machine_evidence_failure(tmp_path):
    package = tmp_path / ".spec" / "specs" / "2026-11-09_invalid-evidence"
    package.mkdir(parents=True)
    spec = _make_spec() + "\nevidence: runs\n"
    tasks = (
        "## Phase\n- [ ] A\n  - id: task-a\n  - boundary: x\n  - verify: `true`\n"
        "- [ ] B\n  - id: task-a\n  - boundary: x\n  - verify: `true`\n"
    )
    results = compute_gate_results(spec, tasks, None, None, tmp_path, package)
    assert results.runs_protocol and results.runs_gate_ok is False
    assert results.overall_ok is False
    assert gate_recovery_routes(results)["Tasks and boundaries"] == "update"
    assert gate_recovery_routes(results)["Runs evidence"] == "update"


def test_cli_duplicate_task_ids_return_structured_recovery(tmp_path):
    slug = "2026-11-09_duplicate-tasks"
    tasks = (
        "## Phase\n- [ ] A\n  - id: task-a\n  - boundary: x\n  - verify: `true`\n"
        "- [ ] B\n  - id: task-a\n  - boundary: x\n  - verify: `true`\n"
    )
    _write_package(tmp_path, slug, tasks=tasks)
    result = _run_check("--root", str(tmp_path), "--slug", slug, "--format", "json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "duplicate task id" in payload["gateFailures"]["Tasks and boundaries"]
    assert payload["recovery"]["Tasks and boundaries"] == "update"
    markdown = _run_check("--root", str(tmp_path), "--slug", slug)
    assert markdown.returncode == 1
    assert "- Recovery: Tasks and boundaries -> update" in markdown.stdout


def test_recovery_routes_unchecked_tasks_to_run():
    results = _converged(tasks=_make_tasks(done=False))
    assert gate_recovery_routes(results) == {"Tasks and boundaries": "run"}


def test_recovery_routes_contract_gaps_to_update():
    results = _converged(tasks="## Phase\n- [ ] Task A\n")
    assert gate_recovery_routes(results) == {"Tasks and boundaries": "update"}
    assert "missing boundary" in gate_failure_details(results)["Tasks and boundaries"]


def test_recovery_routes_open_decision_to_authority():
    spec = _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n- [ ] [decision] 发布？\n",
    )
    results = _converged(spec=spec)
    assert gate_recovery_routes(results) == {"Open questions": "authority"}


def test_recovery_routes_autonomous_question_to_investigation():
    spec = _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n- [ ] [fact] 本地格式是什么？\n",
    )
    results = _converged(spec=spec)
    assert gate_recovery_routes(results) == {"Open questions": "run"}


def test_recovery_routes_unmet_success_criteria_to_run_and_empty_to_update():
    results = _converged(spec=_make_spec(success="- [x] 完成 A\n- [ ] 完成 B"))
    assert gate_recovery_routes(results) == {"Success criteria": "run"}
    empty = _converged(spec=_make_spec(success=""))
    assert gate_recovery_routes(empty) == {"Success criteria": "update"}


@pytest.mark.parametrize(
    "question",
    [
        "[x] [decision] 发布？ | answer: 发布",
        "[x] [decision] 发布？ | authority: user:boss",
        "[x] [decision] 发布？ | answer: 发布 | authority: user:待补充",
    ],
)
def test_recovery_routes_closed_decision_gaps_to_authority_only(question):
    spec = _make_spec().replace("### 2.3 待确认问题\n- 无\n", f"### 2.3 待确认问题\n- {question}\n")
    results = _converged(spec=spec)
    assert results.question_errors
    assert gate_recovery_routes(results) == {"Open questions": "authority"}


def test_recovery_routes_closed_fact_missing_answer_to_update():
    spec = _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n- [x] [fact] 本地格式？\n",
    )
    results = _converged(spec=spec)
    assert results.question_errors
    assert gate_recovery_routes(results) == {"Open questions": "update"}


def test_recovery_routes_mixed_question_errors_keep_authority_and_update():
    spec = _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n- [x] [fact] 本地格式？\n- [x] [decision] 发布？ | answer: 发布\n",
    )
    results = _converged(spec=spec)
    assert gate_recovery_routes(results) == {"Open questions": "update+authority"}


def test_recovery_routes_failed_evidence_to_run(tmp_path):
    root = tmp_path
    slug = "2026-11-09_fixture-recovery"
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    spec = _make_spec().replace("## 6. 成功标准", "本包启用 evidence: runs 协议。\n## 6. 成功标准")
    tasks = "## Phase\n- [ ] Task A\n  - boundary: x\n  - verify: `true`\n"
    results = compute_gate_results(spec, tasks, None, slug, root, package)
    routes = gate_recovery_routes(results)
    assert routes["Runs evidence"] == "run"
    assert routes["Tasks and boundaries"] == "run"


def test_recovery_routes_malformed_verify_to_update(tmp_path):
    root = tmp_path
    slug = "2026-11-09_fixture-verify-contract"
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    spec = _make_spec().replace("## 6. 成功标准", "本包启用 evidence: runs 协议。\n## 6. 成功标准")
    tasks = "## Phase\n- [x] Task A\n  - boundary: x\n  - verify: passes\n"
    results = compute_gate_results(spec, tasks, None, slug, root, package)
    assert results.runs_gate_ok is False
    assert any("requires a backtick" in error for error in results.runs_errors)
    routes = gate_recovery_routes(results)
    assert routes == {"Runs evidence": "update"}
    assert set(routes) == set(gate_failure_details(results))


def test_recovery_routes_blocked_and_ready_without_inferring_user_authority():
    tasks = (
        "## Phase\n"
        "- [!] 等外部系统\n  - boundary: x\n  - verify: `true`\n"
        "- [ ] 可立即执行的修复\n  - boundary: y\n  - verify: `true`\n"
    )
    results = _converged(tasks=tasks)
    assert results.blocked_tasks is True
    assert results.ready_tasks == 1
    assert gate_recovery_routes(results) == {"Tasks and boundaries": "run+environment"}


def test_recovery_routes_blocked_tasks_to_environment():
    results = _converged(tasks="## Phase\n- [!] 等外部系统\n  - boundary: x\n  - verify: `true`\n")
    assert gate_recovery_routes(results) == {"Tasks and boundaries": "environment"}


def test_recovery_routes_contract_and_evidence_share_labels(tmp_path):
    root = tmp_path
    slug = "2026-11-09_fixture-mixed"
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    spec = _make_spec().replace("## 6. 成功标准", "本包启用 evidence: runs 协议。\n## 6. 成功标准")
    tasks = (
        "## Phase\n"
        "- [ ] Task A\n  - boundary: x\n  - verify: passes\n"
        "- [ ] Task B\n  - boundary: y\n  - verify: `true`\n"
    )
    results = compute_gate_results(spec, tasks, None, slug, root, package)
    routes = gate_recovery_routes(results)
    assert routes["Tasks and boundaries"] == "run"
    assert routes["Runs evidence"] == "update+run"
    assert set(routes) == set(gate_failure_details(results))


def test_recovery_routes_combined_failures():
    spec = _make_spec(success="")
    results = _converged(spec=spec, tasks=_make_tasks(done=False))
    assert gate_recovery_routes(results) == {
        "Tasks and boundaries": "run",
        "Success criteria": "update",
    }


def test_empty_task_list_routes_to_update():
    results = _converged(tasks="## Phase\n")
    assert gate_failure_details(results)["Tasks and boundaries"] == "tasks complete 0/0"
    assert gate_recovery_routes(results) == {"Tasks and boundaries": "update"}


def test_mixed_open_questions_keep_both_autonomous_and_authority_routes():
    spec = _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n- [ ] [fact] What format?\n- [ ] [decision] Publish?\n",
    )
    results = _converged(spec=spec)
    assert gate_recovery_routes(results) == {"Open questions": "run+authority"}


def test_falsy_evidence_does_not_invent_task_failure_from_io_error(tmp_path):
    package = tmp_path / ".spec" / "specs" / "2026-11-09_fixture-unreadable"
    package.mkdir(parents=True)
    (package / "runs.jsonl").write_text("{broken\n", encoding="utf-8")
    spec = _make_spec() + "\nevidence: runs\n"
    tasks = "- [x] A\n  - boundary: x\n  - verify: `true`\n"
    results = compute_gate_results(spec, tasks, None, None, tmp_path, package)
    assert gate_recovery_routes(results) == {"Runs evidence": "environment"}
    assert set(gate_recovery_routes(results)) == set(gate_failure_details(results))


def test_duplicate_verify_is_an_update_not_a_run(tmp_path):
    package = tmp_path / ".spec" / "specs" / "2026-11-09_fixture-duplicate"
    package.mkdir(parents=True)
    spec = _make_spec() + "\nevidence: runs\n"
    tasks = "- [x] A\n  - boundary: x\n  - verify: `true`\n  - verify: `true`\n"
    results = compute_gate_results(spec, tasks, None, None, tmp_path, package)
    assert gate_recovery_routes(results) == {"Runs evidence": "update"}
    assert set(gate_recovery_routes(results)) == set(gate_failure_details(results))


def test_recovery_for_branch_mismatch_is_environment(monkeypatch, tmp_path):
    monkeypatch.setattr("check_spec_package.validate_branch_bound_package", lambda *_args, **_kwargs: (False, "wrong"))
    results = compute_gate_results(_make_spec(), _make_tasks(), None, "2026-11-09_fixture-branch", tmp_path)
    assert gate_recovery_routes(results) == {"Branch binding": "environment"}


def test_runs_protocol_without_records_fails_evidence_gate(tmp_path):
    spec = _make_spec() + "\nevidence: runs\n"
    tasks = "## Phase\n- [x] Task A\n  - boundary: only x\n  - verify: `true`\n"
    package = tmp_path / ".spec" / "specs" / "2026-06-12_fix-test"
    package.mkdir(parents=True)
    results = compute_gate_results(spec, tasks, None, slug="2026-06-12_fix-test", root=tmp_path, package_dir=package)
    assert results.runs_gate_ok is False
    assert any("SPEC-EVID-001" in error for error in results.runs_errors)
    assert "Runs evidence" in gate_failure_details(results)


def test_branch_binding_failure_is_a_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "check_spec_package.validate_branch_bound_package",
        lambda *_args, **_kwargs: (False, "当前分支与包绑定不一致"),
    )
    results = compute_gate_results(_make_spec(), _make_tasks(), None, slug="2026-06-12_fix-test", root=tmp_path)
    assert gate_failure_details(results)["Branch binding"] == "当前分支与包绑定不一致"


def test_branch_binding_not_evaluated_without_root_and_slug():
    results = compute_gate_results(_make_spec(), _make_tasks())
    assert any(label == "Branch binding" and passed for label, passed, _detail in gate_rows(results))


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
    assert "Tasks and boundaries" in gate_failure_details(results)


# -- verification scope --


def test_valid_verification_scope_does_not_block():
    spec = _make_spec(extra="### 5.1 验证策略\n- 范围级别：package\n- 快速检查：`pytest -q`\n")
    results = _converged(spec=spec)
    assert results.verification_scope == "package"
    assert results.verification_scope_errors == []


def test_invalid_verification_scope_level_blocks_definition():
    spec = _make_spec(extra="### 5.1 验证策略\n- 范围级别：whole-world\n- 快速检查：`pytest -q`\n")
    results = _converged(spec=spec)
    assert "Project definition" in gate_failure_details(results)


def test_placeholder_quick_check_blocks_definition():
    spec = _make_spec(extra="### 5.1 验证策略\n- 范围级别：package\n- 快速检查：<待填写>\n")
    results = _converged(spec=spec)
    assert "SPEC-VERIFY-001" in gate_failure_details(results)["Project definition"]


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
    assert "- Completed: 1/1" in output
    assert "Finalize the delivery record and archive" in output
    assert "- Convergence: converged" in output
    assert "## Pipeline" not in output
    assert "## Gate" not in output


def test_render_incomplete_package():
    output = render("2026-06-12_fix-test", "Test", _make_spec(), _make_tasks(done=False))
    assert "- Completed: 0/1" in output
    assert "tasks complete 0/1" in output
    assert "- Convergence: not converged" in output
    assert "- Gap:" in output
    assert "## In Progress" not in output


def test_render_includes_definition_gaps():
    output = render(
        "2026-06-12_fix-test",
        "Test",
        _make_spec(goal="- **项目目标**：一句话描述项目要解决什么问题"),
        _make_tasks(),
    )
    assert "## Attention" in output
    assert "Project definition:" in output
    assert "is missing a valid goal" in output


def test_render_uses_precomputed_gate_results(monkeypatch):
    spec, tasks = _make_spec(), _make_tasks(done=True)
    results = compute_gate_results(spec, tasks, slug="2026-06-12_fix-render")

    def fail_if_recomputed(*_args, **_kwargs):
        raise AssertionError("render recomputed gate results")

    monkeypatch.setattr("check_spec_package.compute_gate_results", fail_if_recomputed)
    output = render("2026-06-12_fix-render", "Test", spec, tasks, results=results)
    assert output.startswith("# Test\n")
    assert "- Convergence: converged" in output


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
        assert "tasks complete 0/1" in failed.stdout


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
        assert payload["gateFailures"] == {}
        assert payload["recovery"] == {}
        assert payload["openDecisions"] == []
        assert payload["tasksDone"] is True
        assert payload["archived"] is False


def test_cli_json_recovery_matches_markdown():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        slug = "2026-11-09_fixture-recovery"
        _write_package(root, slug, tasks=_make_tasks(done=False))
        json_result = _run_check("--root", str(root), "--slug", slug, "--format", "json")
        assert json_result.returncode == 1
        payload = json.loads(json_result.stdout)
        assert payload["gateFailures"] == {"Tasks and boundaries": "tasks complete 0/1"}
        assert payload["recovery"] == {"Tasks and boundaries": "run"}
        markdown_result = _run_check("--root", str(root), "--slug", slug)
        assert markdown_result.returncode == 1
        assert "- Recovery: Tasks and boundaries -> run" in markdown_result.stdout


def test_cli_archived_package_is_frozen():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        slug = "2026-07-13_fix-check-archive"
        archived = root / ".spec" / "specs" / "archive" / slug
        archived.mkdir(parents=True)
        completed = _run_check("--root", str(root), "--slug", slug, "--format", "json")
        assert completed.returncode == 0
        payload = json.loads(completed.stdout)
        assert payload == {
            "slug": slug,
            "overall": True,
            "converged": True,
            "gaps": [],
            "gateFailures": {},
            "recovery": {},
            "archived": True,
        }


def test_cli_missing_package_errors():
    with tempfile.TemporaryDirectory() as tmpdir:
        completed = _run_check("--root", tmpdir, "--slug", "2026-07-13_fix-missing")
        assert completed.returncode == 1
        assert "error:" in completed.stderr

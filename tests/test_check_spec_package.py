import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_spec_package import (
    compute_gate_results,
    gate_failure_details,
    gate_rows,
    is_likely_template,
    overall_check_passed,
    parse_tasks,
    render,
    result_label,
)

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
    total, completed, miss_b, miss_v = parse_tasks(content)
    assert total == 2
    assert completed == 2
    assert miss_b == 0
    assert miss_v == 0


def test_parse_tasks_missing_boundary():
    content = "- [ ] Task without boundary\n  - verify: something\n"
    total, completed, miss_b, miss_v = parse_tasks(content)
    assert total == 1
    assert miss_b == 1
    assert miss_v == 0


def test_parse_tasks_missing_verify():
    content = "- [ ] Task without verify\n  - boundary: something\n"
    total, completed, miss_b, miss_v = parse_tasks(content)
    assert total == 1
    assert miss_b == 0
    assert miss_v == 1


def test_parse_tasks_mixed():
    content = """\
- [x] Done task
  - boundary: x
  - verify: x
- [ ] Incomplete task
- [ ] Another incomplete
  - boundary: only y
"""
    total, completed, miss_b, miss_v = parse_tasks(content)
    assert total == 3
    assert completed == 1
    assert miss_b == 1
    assert miss_v == 2


# -- is_likely_template --

TEMPLATE_TASKS = """\
## 阶段一：澄清与初始化
- [ ] 固化问题定义、关键假设与非目标
  - boundary: 只更新 spec.md
  - verify: spec.md 已写明
- [ ] 初始化最小项目骨架
  - boundary: 只创建必须目录
  - verify: 最小启动命令通过
"""

USER_TASKS = """\
## 阶段一
- [ ] 实现支付回调
  - boundary: 仅支付模块
  - verify: 回调测试通过
- [ ] 配置日志
  - boundary: 仅 logging 配置
  - verify: 日志输出正常
"""


def test_is_likely_template_true():
    assert is_likely_template(TEMPLATE_TASKS) is True


def test_is_likely_template_false():
    assert is_likely_template(USER_TASKS) is False


def test_is_likely_template_mixed():
    mixed = """\
- [ ] 固化问题定义、关键假设与非目标
- [ ] 实现支付回调
"""
    assert is_likely_template(mixed) is False


# -- result_label --


def test_result_label():
    assert result_label(True) == "通过"
    assert result_label(False) == "待修复"
    assert result_label(False, partial=True) == "部分通过"


# -- render --


def _make_spec():
    return """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：实际目标
- **目标用户**：实际用户
- **核心价值**：实际价值

## 2. 假设与待确认
### 2.1 已确认事实
- 已确认
### 2.2 关键假设
- 假设
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不做什么
## 4. 最小实现路径
- 路径1
- 路径2
- 路径3
"""


def _make_tasks(done=True):
    marker = "x" if done else " "
    return f"""\
## Phase
- [{marker}] Task A
  - boundary: only x
  - verify: passes
- [{marker}] Task B
  - boundary: only y
  - verify: works
"""


def _make_checklist(passed=False, verification_scope=None):
    result = "**验收结果**：通过" if passed else "**验收结果**：待修复"
    evidence = "- 脚本验证：pytest -q" if passed else ""
    if passed and verification_scope:
        evidence += f"\n- 验证范围：{verification_scope}"
    return f"""\
## 基础
- [x] Item 1

## 验收证据
{evidence}
{result}
"""


def test_render_all_done():
    output = render(
        "2026-06-12_fix-test",
        "Test",
        _make_spec(),
        _make_tasks(done=True),
        _make_checklist(passed=True),
    )
    assert output.startswith("# Test\n")
    assert "- 已完成：2/2" in output
    assert "Task A：完成" in output
    assert "验收结果：通过" in output
    assert "有效证据：1 项" in output
    assert "## 流水线" not in output
    assert "门禁" not in output


def test_render_incomplete():
    output = render("2026-06-12_fix-test", "Test", _make_spec(), _make_tasks(done=False), _make_checklist())
    assert output.startswith("# Test\n")
    assert "- 已完成：0/2" in output
    assert "Task A：进行" in output
    assert "项目尚未完成验收" in output
    assert "任务完成 0/2" in output
    assert "## 正在处理" in output
    assert "## 接下来" not in output


def test_render_uses_precomputed_gate_results(monkeypatch):
    spec = _make_spec()
    tasks = _make_tasks(done=True)
    checklist = _make_checklist(passed=True)
    results = compute_gate_results(spec, tasks, checklist, slug="2026-06-12_fix-render")

    def fail_if_recomputed(*_args, **_kwargs):
        raise AssertionError("render recomputed gate results")

    monkeypatch.setattr("check_spec_package.compute_gate_results", fail_if_recomputed)

    output = render("2026-06-12_fix-render", "Test", spec, tasks, checklist, results)
    assert output.startswith("# Test\n")
    assert "验收结果：通过" in output
    assert "## 需要关注" not in output


def test_render_missing_clarification():
    output = render("2026-06-12_fix-test", "Test", FRESH_SPEC, _make_tasks(done=False), _make_checklist())
    assert "## 需要关注" in output
    assert "需求范围尚未就绪" in output
    assert "待确认 1" in output
    assert "项目尚未完成验收" in output


def test_render_omits_non_blocking_slug_style_advisory():
    output = render(
        "2026-06-12_spec-push-stage",
        "Test",
        _make_spec(),
        _make_tasks(done=True),
        _make_checklist(passed=True),
    )
    assert "verb-object" not in output
    assert "naming-and-commits.md" not in output


def test_render_slug_verb_advisory_absent_for_verb_object():
    output = render(
        "2026-06-12_add-push-stage",
        "Test",
        _make_spec(),
        _make_tasks(done=True),
        _make_checklist(passed=True),
    )
    assert "verb-object" not in output


def _make_spec_with_orchestration():
    return _make_spec()


def _make_complete_checklist(include_orchestration=True):
    return """\
## 跨载体一致性
- [x] 模板字段在参考文档、初始化输出和校验逻辑中无冲突

## 项目结构与文档可信度
- [x] 已基于第一性原理判断目录结构是否合理，并记录结论

## 验收证据
- 外部对标：task package state machine
- 脚本验证：pytest
- 旧新对比：aliases checked
- 差异边界：bounded

**验收结果**：通过
"""


def test_overall_check_passes_complete_checklist():
    spec = _make_spec_with_orchestration()
    checklist = _make_complete_checklist()
    assert overall_check_passed(spec, _make_tasks(done=True), checklist) is True


def test_overall_check_rejects_non_development_record_slug():
    spec = _make_spec_with_orchestration()
    checklist = _make_complete_checklist(include_orchestration=True)
    assert overall_check_passed(spec, _make_tasks(done=True), checklist, slug="test-pkg") is False


def test_overall_check_accepts_development_record_slug():
    spec = _make_spec_with_orchestration()
    checklist = _make_complete_checklist(include_orchestration=True)
    assert overall_check_passed(spec, _make_tasks(done=True), checklist, slug="2026-06-12_test-pkg") is True


FRESH_SPEC = """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：xxx
- **目标用户**：xxx
- **核心价值**：xxx

## 2. 假设与待确认
### 2.1 已确认事实
- 事实 A
### 2.2 关键假设
- 假设 A
### 2.3 待确认问题
- 是否支持离线模式？
## 3. 功能范围
### 3.3 不在范围内
- 明确列出本轮不做的内容
## 4. 最小实现路径
- 路径
"""


def test_check_cli_exit_code_matches_rendered_result():
    script = Path(__file__).resolve().parent.parent / "scripts" / "check_spec_package.py"
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        slug = "2026-07-13_fix-check-exit"
        package = root / ".spec" / "specs" / slug
        package.mkdir(parents=True)
        (package / "spec.md").write_text(
            _make_spec_with_orchestration() + f"\n- Git integration branch：`spec/{slug}`\n",
            encoding="utf-8",
        )
        (package / "tasks.md").write_text(_make_tasks(done=True), encoding="utf-8")
        checklist = package / "checklist.md"
        checklist.write_text("## 验收证据\n**验收结果**：待修复\n", encoding="utf-8")

        failed = subprocess.run(
            [sys.executable, str(script), "--root", str(root), "--slug", slug],
            capture_output=True,
            text=True,
        )
        assert failed.returncode != 0
        assert failed.stdout.startswith("# Test\n")
        assert "## 需要关注" in failed.stdout

        checklist.write_text(_make_complete_checklist(), encoding="utf-8")
        passed = subprocess.run(
            [sys.executable, str(script), "--root", str(root), "--slug", slug],
            capture_output=True,
            text=True,
        )
        assert passed.returncode == 0
        assert "验收结果：通过" in passed.stdout
        assert "有效证据：4 项" in passed.stdout
        assert "## 需要关注" not in passed.stdout


def test_check_cli_rejects_package_from_wrong_git_branch(tmp_path):
    script = Path(__file__).resolve().parent.parent / "scripts" / "check_spec_package.py"
    slug = "2026-09-05_fix-wrong-branch"
    package = tmp_path / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(_make_spec() + f"\n- Git integration branch：`spec/{slug}`\n", encoding="utf-8")
    (package / "tasks.md").write_text(_make_tasks(done=False), encoding="utf-8")
    (package / "checklist.md").write_text(_make_checklist(), encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--slug", slug, "--format", "json"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "belongs to branch" in result.stderr
    assert result.stdout == ""

    """T1: naked 验收结果：通过 is not enough."""
    bare = "**验收结果**：通过\n"
    assert overall_check_passed(_make_spec(), _make_tasks(done=True), bare, slug="2026-07-27_fix-gate") is False


def test_overall_check_accepts_nested_script_evidence():
    spec = _make_spec_with_orchestration()
    checklist = """\
## 跨载体一致性
- [x] ok
## 项目结构与文档可信度
- [x] structure checked
## 验收证据
- 外部对标：real baseline
- 脚本验证：
  - pytest -q
- 旧新对比：old -> new
- 差异边界：scripts only
**验收结果**：通过
"""
    assert overall_check_passed(spec, _make_tasks(done=True), checklist, slug="2026-07-27_fix-gate") is True


def test_overall_check_rejects_unchecked_structure_governance_when_present():
    spec = _make_spec_with_orchestration()
    checklist = """\
## 跨载体一致性
- [x] ok
## 项目结构与文档可信度
- [ ] structure checked
## 验收证据
- 外部对标：real baseline
- 脚本验证：pytest
- 旧新对比：old -> new
- 差异边界：bounded
**验收结果**：通过
"""
    assert overall_check_passed(spec, _make_tasks(done=True), checklist, slug="2026-07-27_fix-gate") is False


# -- convergence semantics (v0.7) --


def test_convergence_state_not_converged_lists_task_gap():
    from check_spec_package import convergence_state

    results = compute_gate_results(_make_spec(), _make_tasks(done=False), _make_checklist(), slug="2026-06-12_fix-test")
    converged, gaps = convergence_state(results)
    assert converged is False
    assert "未勾任务 2 项" in gaps
    assert "验收未通过" in gaps
    assert any(gap.startswith("证据缺口") for gap in gaps)


def test_convergence_state_converged_when_tasks_checklist_acceptance_evidence_ok():
    from check_spec_package import convergence_state

    results = compute_gate_results(
        _make_spec(), _make_tasks(done=True), _make_checklist(passed=True), slug="2026-06-12_fix-test"
    )
    converged, gaps = convergence_state(results)
    assert converged is True
    assert gaps == []


def test_convergence_state_reports_unchecked_checklist_and_blocked_tasks():
    from check_spec_package import convergence_state

    tasks = """\
## Phase
- [x] Task A
  - boundary: only x
  - verify: passes
- [!] Task B 阻塞在外部依赖
  - boundary: only y
  - verify: works
"""
    checklist = """\
## 基础
- [x] Item 1
- [ ] Item 2

## 验收证据
- 脚本验证：pytest -q

**验收结果**：通过
"""
    results = compute_gate_results(_make_spec(), tasks, checklist, slug="2026-06-12_fix-test")
    converged, gaps = convergence_state(results)
    assert converged is False
    assert "未勾任务 1 项" in gaps
    assert "未勾检查清单 1 项" in gaps
    assert "存在阻塞任务" in gaps
    assert "验收未通过" not in gaps


def test_render_incomplete_appends_not_converged_section_with_gaps():
    output = render("2026-06-12_fix-test", "Test", _make_spec(), _make_tasks(done=False), _make_checklist())
    assert "## 收敛状态" in output
    assert "收敛状态：未收敛" in output
    assert "- 差距：未勾任务 2 项" in output
    assert "- 差距：验收未通过" in output


def test_render_all_done_appends_converged_line_without_gaps():
    output = render("2026-06-12_fix-test", "Test", _make_spec(), _make_tasks(done=True), _make_checklist(passed=True))
    assert "## 收敛状态" in output
    assert "收敛状态：已收敛" in output
    assert "- 差距：" not in output


def test_render_compact_keeps_convergence_as_single_lines():
    output = render(
        "2026-06-12_fix-test",
        "Test",
        _make_spec(),
        _make_tasks(done=False),
        _make_checklist(),
        compact=True,
    )
    assert "## 收敛状态" not in output
    assert "- 收敛状态：未收敛" in output
    assert "- 差距：未勾任务 2 项" in output


def test_check_cli_json_format_has_typed_convergence_fields():
    script = Path(__file__).resolve().parent.parent / "scripts" / "check_spec_package.py"
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        slug = "2026-07-13_fix-convergence-json"
        package = root / ".spec" / "specs" / slug
        package.mkdir(parents=True)
        (package / "spec.md").write_text(
            _make_spec() + f"\n- Git integration branch：`spec/{slug}`\n",
            encoding="utf-8",
        )
        (package / "tasks.md").write_text(_make_tasks(done=False), encoding="utf-8")
        (package / "checklist.md").write_text(_make_checklist(), encoding="utf-8")

        failed = subprocess.run(
            [sys.executable, str(script), "--root", str(root), "--slug", slug, "--format", "json"],
            capture_output=True,
            text=True,
        )
        assert failed.returncode != 0
        payload = json.loads(failed.stdout)
        assert isinstance(payload["converged"], bool)
        assert isinstance(payload["gaps"], list)
        assert all(isinstance(gap, str) for gap in payload["gaps"])
        assert payload["converged"] is False
        assert any("未勾任务 2 项" in gap for gap in payload["gaps"])
        assert payload["slug"] == slug

        (package / "tasks.md").write_text(_make_tasks(done=True), encoding="utf-8")
        (package / "checklist.md").write_text(_make_checklist(passed=True), encoding="utf-8")
        passed = subprocess.run(
            [sys.executable, str(script), "--root", str(root), "--slug", slug, "--format", "json"],
            capture_output=True,
            text=True,
        )
        assert passed.returncode == 0
        payload = json.loads(passed.stdout)
        assert payload["converged"] is True
        assert payload["gaps"] == []


# -- archived package fallback --


def _write_triad(package_dir: Path, spec: str, tasks: str, checklist: str) -> None:
    package_dir.mkdir(parents=True)
    (package_dir / "spec.md").write_text(spec, encoding="utf-8")
    (package_dir / "tasks.md").write_text(tasks, encoding="utf-8")
    (package_dir / "checklist.md").write_text(checklist, encoding="utf-8")


def _run_check(root: Path, slug: str, *extra: str):
    import subprocess

    script = Path(__file__).resolve().parent.parent / "scripts" / "check_spec_package.py"
    return subprocess.run(
        [sys.executable, str(script), "--root", str(root), "--slug", slug, *extra],
        capture_output=True,
        text=True,
    )


def test_archived_package_falls_back_and_marks_archived(tmp_path):
    root = tmp_path / "repo"
    _write_triad(
        root / ".spec/specs/archive/2026-01-01_fix-archived",
        _make_spec(),
        _make_tasks(done=True),
        _make_checklist(passed=True),
    )
    result = _run_check(root, "2026-01-01_fix-archived", "--format", "json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["archived"] is True
    assert payload["converged"] is True

    text = _run_check(root, "2026-01-01_fix-archived")
    assert text.returncode == 0, text.stderr
    assert "包状态：已归档（只读复查）" in text.stdout


def test_active_package_still_wins_over_archive(tmp_path):
    root = tmp_path / "repo"
    _write_triad(
        root / ".spec/specs/2026-01-01_fix-active",
        _make_spec(),
        _make_tasks(done=False),
        _make_checklist(passed=False),
    )
    _write_triad(
        root / ".spec/specs/archive/2026-01-01_fix-active",
        _make_spec(),
        _make_tasks(done=True),
        _make_checklist(passed=True),
    )
    result = _run_check(root, "2026-01-01_fix-active", "--format", "json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["archived"] is False
    assert payload["converged"] is False


def test_missing_everywhere_still_fails(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    result = _run_check(root, "2026-01-01_fix-missing", "--format", "json")
    assert result.returncode == 1
    assert "required file missing" in result.stderr


def test_archived_package_skips_branch_binding(tmp_path):
    import subprocess as sp

    repo = tmp_path / "repo"
    sp.run(["git", "init", "-q", str(repo)], check=True)
    sp.run(["git", "-C", str(repo), "checkout", "-q", "-b", "other"], check=True)
    spec = _make_spec() + "\n- Git integration branch：`spec/2026-01-01_fix-archived`\n"
    _write_triad(
        repo / ".spec/specs/archive/2026-01-01_fix-archived",
        spec,
        _make_tasks(done=True),
        _make_checklist(passed=True),
    )
    result = _run_check(repo, "2026-01-01_fix-archived", "--format", "json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["archived"] is True


# -- G2/G3: review-round gate-machinery fixes --


def test_json_overall_gaps_explain_nonzero_exit_when_converged(tmp_path):
    """G2: converged=true + exit 1 must carry machine-readable failure details."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "check_spec_package.py"
    slug = "2026-09-08_fix-overall-gaps"
    package = tmp_path / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    # A spec whose 2.3 bullet is neither a placeholder nor a recognized
    # no-pending declaration keeps tasks/checklist converged while the
    # assumptions gate fails: converged=true, overall=false, exit=1.
    spec = _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n- 待确认：上游接口 9 月是否可用\n",
    )
    (package / "spec.md").write_text(spec + f"\n- Git integration branch：`spec/{slug}`\n", encoding="utf-8")
    (package / "tasks.md").write_text(_make_tasks(done=True), encoding="utf-8")
    (package / "checklist.md").write_text(_make_checklist(passed=True), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--slug", slug, "--format", "json"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["converged"] is True
    assert payload["overall"] is False
    assert payload["gaps"] == []
    assert any(gap.startswith("假设与范围：") and "待确认 1" in gap for gap in payload["overallGaps"]), payload[
        "overallGaps"
    ]


def test_completed_task_title_with_block_word_is_not_blocked(tmp_path):
    """G3: a completed task titled 解除依赖阻塞并修复 must not gate-block."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "check_spec_package.py"
    slug = "2026-09-08_fix-blocked-completed"
    package = tmp_path / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(_make_spec() + f"\n- Git integration branch：`spec/{slug}`\n", encoding="utf-8")
    (package / "tasks.md").write_text(
        """## Phase
- [x] 解除依赖阻塞并修复
  - boundary: only x
  - verify: passes
""",
        encoding="utf-8",
    )
    (package / "checklist.md").write_text(_make_checklist(passed=True), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--slug", slug, "--format", "json"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["converged"] is True
    assert "存在阻塞任务" not in payload["gaps"]


def test_unchecked_task_title_with_block_word_still_blocks():
    """G3 guard: the 阻塞 keyword still blocks while the task is unchecked."""
    from check_spec_package import compute_gate_results, convergence_state

    tasks = """## Phase
- [ ] 解除依赖阻塞并修复
  - boundary: only x
  - verify: passes
"""
    results = compute_gate_results(_make_spec(), tasks, _make_checklist(), slug="2026-09-08_fix-blocked-open")
    converged, gaps = convergence_state(results)
    assert converged is False
    assert "存在阻塞任务" in gaps


def test_orchestration_section_absent_does_not_block():
    from check_spec_package import compute_gate_results

    results = compute_gate_results(
        _make_spec(), _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_add-orchestration-core"
    )
    assert results.orchestration_section_exists is False
    assert results.orchestration_ok is True
    assert results.overall_ok is True


def test_orchestration_single_token_route_passes():
    from check_spec_package import compute_gate_results

    spec = _make_spec() + "\n### 5.4 编排策略\n- route: `explore`\n- ownership: 主线程\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_add-orchestration-core"
    )
    assert results.orchestration_section_exists is True
    assert results.orchestration_ok is True
    assert results.overall_ok is True


def test_orchestration_multi_token_route_fails():
    from check_spec_package import compute_gate_results

    spec = _make_spec() + "\n### 5.4 编排策略\n- route: build（主线程关键路径）+ 局部 review\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_add-orchestration-core"
    )
    assert results.orchestration_ok is False
    assert results.overall_ok is False
    assert any("单 token" in err for err in results.orchestration_errors)


def test_orchestration_opt_out_counts_as_local():
    from check_spec_package import compute_gate_results

    spec = _make_spec() + "\n### 5.4 编排策略（仅在需要时填写）\n- 适用外：单线执行，未启用编排。\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_add-orchestration-core"
    )
    assert results.orchestration_section_exists is True
    assert results.orchestration_ok is True
    assert results.orchestration_errors == []


def test_orchestration_archive_samples_pass():
    """Historical 5.4 forms (backticks, titled heading, opt-out) stay valid."""
    from spec_package_support import orchestration_strategy_errors

    samples = [
        Path(__file__).resolve().parent.parent / ".spec/specs/archive/2026-09-09_fix-late-wakeup-review/spec.md",
        Path(__file__).resolve().parent.parent / ".spec/specs/archive/2026-06-12_update-all-docs/spec.md",
        Path(__file__).resolve().parent.parent / ".spec/specs/archive/2026-06-15_add-spec-training-slides/spec.md",
        Path(__file__).resolve().parent.parent / ".spec/specs/archive/2026-06-14_fix-smoke-output/spec.md",
    ]
    for path in samples:
        content = path.read_text(encoding="utf-8")
        assert orchestration_strategy_errors(content) == [], path.name


# -- 5.4 gate hardening: an opt-out bullet is a declaration, not a mask over an
# explicit route. Anything that names a route value must be validated first.


@pytest.mark.parametrize("sibling", ["ownership", "waiting strategy", "verification gate"])
@pytest.mark.parametrize("mask", ["未启用", "适用外", "不启用", "单线", "单线程"])
def test_orchestration_opt_out_words_in_sibling_bullets_do_not_mask_invalid_route(sibling, mask):
    from check_spec_package import compute_gate_results

    spec = _make_spec() + f"\n### 5.4 编排策略\n- route: build + review\n- {sibling}: 主线程{mask}合流\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_fix-orchestration-review-gaps"
    )
    assert results.orchestration_section_exists is True
    assert results.orchestration_ok is False, f"{mask!r} in {sibling!r} must not mask an invalid route"
    assert results.overall_ok is False
    assert any("单 token" in err for err in results.orchestration_errors)


@pytest.mark.parametrize("mask", ["未启用", "适用外", "不启用", "单线", "单线程"])
def test_orchestration_opt_out_words_do_not_mask_invalid_route_as_sole_bullet(mask):
    from check_spec_package import compute_gate_results

    spec = _make_spec() + f"\n### 5.4 编排策略\n- route: review + build\n- ownership: {mask}\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_fix-orchestration-review-gaps"
    )
    assert results.orchestration_ok is False, f"sole {mask!r} sibling must not mask an invalid route"
    assert results.overall_ok is False


@pytest.mark.parametrize(
    "opt_out",
    [
        "- 未启用（本包 route=local，无 sidecar）",
        "- 适用外：单线执行，未启用编排。",
        "- 适用外（单会话执行，不启用编排）",
    ],
)
def test_orchestration_anchored_opt_out_declaration_still_counts_as_local(opt_out):
    from check_spec_package import compute_gate_results

    spec = _make_spec() + f"\n### 5.4 编排策略（仅在需要时填写）\n{opt_out}\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_fix-orchestration-review-gaps"
    )
    assert results.orchestration_section_exists is True
    assert results.orchestration_ok is True
    assert results.orchestration_errors == []


def test_orchestration_standalone_opt_out_bullet_without_route_still_local():
    from check_spec_package import compute_gate_results

    spec = _make_spec() + "\n### 5.4 编排策略\n- 未启用\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_fix-orchestration-review-gaps"
    )
    assert results.orchestration_ok is True


def test_orchestration_nested_route_cannot_rescue_invalid_top_level():
    from check_spec_package import compute_gate_results

    spec = _make_spec() + "\n### 5.4 编排策略\n- route: nonsense\n- example:\n  - route: local\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_fix-orchestration-review-gaps"
    )
    assert results.orchestration_ok is False, "nested route: local must not rescue an invalid top-level route"


def test_orchestration_nested_route_cannot_poison_valid_top_level():
    from check_spec_package import compute_gate_results

    spec = _make_spec() + "\n### 5.4 编排策略\n- route: local\n- ownership:\n  - route: nonsense\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_fix-orchestration-review-gaps"
    )
    assert results.orchestration_ok is True, "nested route: nonsense must not poison a valid top-level route"


def test_orchestration_duplicate_top_level_route_fails_closed():
    from check_spec_package import compute_gate_results

    spec = _make_spec() + "\n### 5.4 编排策略\n- route: local\n- route: review\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_fix-orchestration-review-gaps"
    )
    assert results.orchestration_ok is False
    assert any("route" in err for err in results.orchestration_errors)


@pytest.mark.parametrize(
    "route_line",
    [
        "- route : build + review",
        "-\troute: nonsense",
        "- route：build + review",
    ],
)
def test_orchestration_spaced_or_tabbed_route_is_still_an_explicit_route(route_line):
    from check_spec_package import compute_gate_results

    spec = _make_spec() + f"\n### 5.4 编排策略\n{route_line}\n- 未启用\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_fix-orchestration-review-gaps"
    )
    assert results.orchestration_ok is False, f"{route_line!r} must be validated before opt-out"
    assert results.overall_ok is False
    assert any("单 token" in err or "route" in err for err in results.orchestration_errors)


def test_orchestration_spaced_duplicate_route_fails_closed():
    from check_spec_package import compute_gate_results

    spec = _make_spec() + "\n### 5.4 编排策略\n- route: local\n- route : nonsense\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_fix-orchestration-review-gaps"
    )
    assert results.orchestration_ok is False
    assert any("route" in err for err in results.orchestration_errors)


def test_orchestration_titled_section_route_error_is_visible_in_render_and_json():
    from check_spec_package import compute_gate_results, gate_failure_details, render

    spec = _make_spec() + "\n### 5.4 编排策略: review\n- route: bad\n"
    results = compute_gate_results(
        spec, _make_tasks(), _make_checklist(passed=True), slug="2026-09-21_fix-orchestration-review-gaps"
    )
    assert results.orchestration_ok is False
    failure_details = gate_failure_details(results, "2026-09-21_fix-orchestration-review-gaps")
    assert "编排策略" in failure_details, "route errors must surface in failure details, not hide as out-of-scope"
    output = render(
        "2026-09-21_fix-orchestration-review-gaps",
        "T",
        spec,
        _make_tasks(),
        _make_checklist(passed=True),
        results,
    )
    assert "编排策略尚未满足" in output


def test_overall_ok_rejects_standalone_blocked_status_line():
    tasks = _make_tasks(done=True) + "\n阻塞：等待外部系统\n"
    results = compute_gate_results(
        _make_spec(),
        tasks,
        _make_checklist(passed=True),
        slug="2026-09-22_fix-checkpoint-blocker-gates",
    )
    assert results.blocked_tasks
    assert not results.overall_ok


# -- evidence freshness anchor gate (证据锚点：HEAD <sha> @ <iso>) --

ANCHOR_SLUG = "2026-09-25_add-evidence-freshness-anchor"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Spec Test")


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _checklist_with_anchor(sha: str, *, passed: bool = True) -> str:
    result = "**验收结果**：通过" if passed else "**验收结果**：待修复"
    evidence = "- 脚本验证：pytest -q" if passed else ""
    return f"## 验收证据\n{evidence}\n- 证据锚点：HEAD {sha} @ 2026-09-25T10:00:00+08:00\n{result}\n"


def test_anchor_gate_stale_when_head_moves(tmp_path):
    from check_spec_package import gate_failure_details

    repo = tmp_path / "proj"
    _init_repo(repo)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    sha1 = _commit_all(repo, "base")
    (repo / "second.txt").write_text("second\n", encoding="utf-8")
    _commit_all(repo, "second")

    results = compute_gate_results(
        _make_spec(), _make_tasks(done=True), _checklist_with_anchor(sha1), slug=ANCHOR_SLUG, root=repo
    )
    assert results.evidence_anchor is not None
    assert results.evidence_anchor_ok is False
    assert not results.overall_ok
    details = gate_failure_details(results, ANCHOR_SLUG)
    assert "证据新鲜度" in details
    assert "证据过期需重跑取证" in details["证据新鲜度"]


def test_anchor_gate_passes_when_head_matches(tmp_path):
    repo = tmp_path / "proj"
    _init_repo(repo)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    sha1 = _commit_all(repo, "base")

    results = compute_gate_results(
        _make_spec(), _make_tasks(done=True), _checklist_with_anchor(sha1), slug=ANCHOR_SLUG, root=repo
    )
    assert results.evidence_anchor_ok is True
    assert results.overall_ok
    assert "一致" in results.evidence_anchor_detail


def test_no_anchor_keeps_behavior_unchanged(tmp_path):
    repo = tmp_path / "proj"
    _init_repo(repo)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")

    checklist = _make_checklist(passed=True)
    with_root = compute_gate_results(_make_spec(), _make_tasks(done=True), checklist, slug=ANCHOR_SLUG, root=repo)
    without_root = compute_gate_results(_make_spec(), _make_tasks(done=True), checklist, slug=ANCHOR_SLUG)
    assert with_root.evidence_anchor is None
    assert with_root.evidence_anchor_ok is None
    assert with_root.evidence_anchor_evaluated is True
    assert with_root.overall_ok == without_root.overall_ok
    assert with_root.base_ok == without_root.base_ok


def test_malformed_anchor_is_treated_as_absent(tmp_path):
    repo = tmp_path / "proj"
    _init_repo(repo)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")

    checklist = "## 验收证据\n- 脚本验证：pytest -q\n- 证据锚点：待补充\n**验收结果**：通过\n"
    results = compute_gate_results(_make_spec(), _make_tasks(done=True), checklist, slug=ANCHOR_SLUG, root=repo)
    assert results.evidence_anchor is None
    assert results.evidence_anchor_ok is None
    assert results.overall_ok


def test_anchor_skipped_on_non_git_root(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    results = compute_gate_results(
        _make_spec(), _make_tasks(done=True), _checklist_with_anchor("a" * 40), slug=ANCHOR_SLUG, root=plain
    )
    assert results.evidence_anchor_ok is None
    assert results.overall_ok
    assert "适用外" in results.evidence_anchor_detail


def test_anchor_not_evaluated_without_root():
    results = compute_gate_results(
        _make_spec(), _make_tasks(done=True), _checklist_with_anchor("b" * 40), slug=ANCHOR_SLUG
    )
    assert results.evidence_anchor_ok is None
    assert results.evidence_anchor_evaluated is False


def test_anchor_gate_head_unresolvable_repo_skips(tmp_path):
    repo = tmp_path / "fresh"
    _init_repo(repo)  # git repo without commits: HEAD unresolvable
    results = compute_gate_results(
        _make_spec(), _make_tasks(done=True), _checklist_with_anchor("c" * 40), slug=ANCHOR_SLUG, root=repo
    )
    assert results.evidence_anchor_ok is None
    assert results.overall_ok


def test_check_cli_anchor_stale_then_fresh(tmp_path):
    """Fixture 实跑两次：旧锚点 exit 1（含 证据过期需重跑取证），更新锚点后 exit 0。"""
    slug = "2026-09-25_fix-anchor-cli"
    repo = tmp_path
    _init_repo(repo)
    _git(repo, "checkout", "-q", "-b", f"spec/{slug}")
    package = repo / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(_make_spec() + f"\n- Git integration branch：`spec/{slug}`\n", encoding="utf-8")
    (package / "tasks.md").write_text(_make_tasks(done=True), encoding="utf-8")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    sha1 = _commit_all(repo, "base")
    (package / "checklist.md").write_text(_checklist_with_anchor(sha1), encoding="utf-8")
    sha2 = _commit_all(repo, "move head past the anchor")

    stale = _run_check(repo, slug, "--format", "json")
    assert stale.returncode == 1
    payload = json.loads(stale.stdout)
    assert payload["overall"] is False
    assert any("证据过期需重跑取证" in gap for gap in payload["overallGaps"]), payload["overallGaps"]

    (package / "checklist.md").write_text(_checklist_with_anchor(sha2), encoding="utf-8")
    fresh = _run_check(repo, slug, "--format", "json")
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    payload = json.loads(fresh.stdout)
    assert payload["overall"] is True


def test_check_cli_archived_package_skips_anchor_comparison(tmp_path):
    slug = "2026-09-25_fix-anchor-archived"
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    sha1 = _commit_all(repo, "base")
    (repo / "second.txt").write_text("second\n", encoding="utf-8")
    _commit_all(repo, "second")  # HEAD moved past the anchor
    spec = _make_spec() + "\n- Git integration branch：`spec/{}`\n".format(slug)
    _write_triad(
        repo / ".spec" / "specs" / "archive" / slug,
        spec,
        _make_tasks(done=True),
        _checklist_with_anchor(sha1),
    )
    result = _run_check(repo, slug, "--format", "json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["archived"] is True
    assert payload["overall"] is True


def test_anchor_gate_short_sha_fails_closed_with_guidance(tmp_path):
    """12 位短 SHA 锚点：门判失败且报错明示 40 位指引，不得误判为 HEAD 已前移。"""
    from check_spec_package import gate_failure_details

    repo = tmp_path / "proj"
    _init_repo(repo)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    sha1 = _commit_all(repo, "base")
    (repo / "second.txt").write_text("second\n", encoding="utf-8")
    _commit_all(repo, "second")

    short_sha = sha1[:12]
    results = compute_gate_results(
        _make_spec(), _make_tasks(done=True), _checklist_with_anchor(short_sha), slug=ANCHOR_SLUG, root=repo
    )
    assert results.evidence_anchor is not None
    assert results.evidence_anchor_ok is False
    assert not results.overall_ok
    assert "证据锚点须为完整 40 位 SHA" in results.evidence_anchor_detail
    assert "git rev-parse HEAD" in results.evidence_anchor_detail
    assert "已前移" not in results.evidence_anchor_detail
    details = gate_failure_details(results, ANCHOR_SLUG)
    assert "证据新鲜度" in details
    assert "证据锚点须为完整 40 位 SHA" in details["证据新鲜度"]


def test_anchor_gate_full_sha_passes_format_gate(tmp_path):
    """全量 40 位 SHA 锚点照常通过格式门；大写 40 位与既有小写归一语义一致。"""
    repo = tmp_path / "proj"
    _init_repo(repo)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    sha1 = _commit_all(repo, "base")

    results = compute_gate_results(
        _make_spec(), _make_tasks(done=True), _checklist_with_anchor(sha1), slug=ANCHOR_SLUG, root=repo
    )
    assert results.evidence_anchor_ok is True
    assert results.overall_ok
    assert "一致" in results.evidence_anchor_detail

    upper_results = compute_gate_results(
        _make_spec(), _make_tasks(done=True), _checklist_with_anchor(sha1.upper()), slug=ANCHOR_SLUG, root=repo
    )
    assert upper_results.evidence_anchor_ok is True
    assert upper_results.overall_ok


def test_check_cli_short_sha_anchor_reports_guidance(tmp_path):
    """CLI 实跑：12 位锚点 exit 1、overallGaps 含 40 位指引、无 traceback（不崩溃）。"""
    slug = "2026-09-26_fix-anchor-short-sha-cli"
    repo = tmp_path
    _init_repo(repo)
    _git(repo, "checkout", "-q", "-b", f"spec/{slug}")
    package = repo / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(_make_spec() + f"\n- Git integration branch：`spec/{slug}`\n", encoding="utf-8")
    (package / "tasks.md").write_text(_make_tasks(done=True), encoding="utf-8")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    sha1 = _commit_all(repo, "base")
    (package / "checklist.md").write_text(_checklist_with_anchor(sha1[:12]), encoding="utf-8")

    result = _run_check(repo, slug, "--format", "json")
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["overall"] is False
    guidance_gaps = [gap for gap in payload["overallGaps"] if "证据锚点须为完整 40 位 SHA" in gap]
    assert guidance_gaps, payload["overallGaps"]
    assert all("已前移" not in gap for gap in guidance_gaps)


# -- boundary regression optional gate (## 边界回归) --


def _complete_checklist_with_boundary_regression(*, unchecked_rows: int = 0):
    regression_rows = [
        "越界负样本：boundary 外改动会被识别或回滚",
        "顺序交换负样本：任务/规则顺序交换不改变验收结论",
        "旁路负样本：不存在绕过 verify 的完成入口",
    ]
    lines = [f"- [{' ' if index < unchecked_rows else 'x'}] {row}" for index, row in enumerate(regression_rows)]
    return f"""\
## 跨载体一致性
- [x] 模板字段在参考文档、初始化输出和校验逻辑中无冲突

## 边界回归
{chr(10).join(lines)}

## 验收证据
- 外部对标：task package state machine
- 脚本验证：pytest
- 旧新对比：aliases checked
- 差异边界：bounded

**验收结果**：通过
"""


def test_overall_check_passes_when_boundary_regression_all_checked():
    checklist = _complete_checklist_with_boundary_regression()
    assert overall_check_passed(_make_spec(), _make_tasks(done=True), checklist, slug="2026-09-25_add-gate") is True


def test_overall_check_fails_when_boundary_regression_has_unchecked_row():
    checklist = _complete_checklist_with_boundary_regression(unchecked_rows=1)
    assert overall_check_passed(_make_spec(), _make_tasks(done=True), checklist, slug="2026-09-25_add-gate") is False


def test_boundary_regression_absent_is_not_applicable():
    """节不存在 = 完全不生效：gate_rows 值为 None，且不触发 new-gate 证据前置。"""
    checklist = _make_complete_checklist()
    results = compute_gate_results(_make_spec(), _make_tasks(done=True), checklist, slug="2026-09-25_add-gate")
    rows = {label: passed for label, passed, _evidence in gate_rows(results, "2026-09-25_add-gate")}
    assert rows["边界回归"] is None
    assert results.has_new_gate_markers is True  # 跨载体一致性 alone still drives it
    assert overall_check_passed(_make_spec(), _make_tasks(done=True), checklist, slug="2026-09-25_add-gate") is True

    # 完全无可选节的旧包：markers 维持 False，行为与历史一致
    legacy = compute_gate_results(_make_spec(), _make_tasks(done=True), _make_checklist(passed=True))
    assert legacy.has_new_gate_markers is False
    legacy_rows = {label: passed for label, passed, _evidence in gate_rows(legacy, "2026-09-25_add-gate")}
    assert legacy_rows["边界回归"] is None


def test_boundary_regression_alone_joins_new_gate_markers_evidence_prerequisite():
    """有「## 边界回归」节即并入 has_new_gate_markers：命令证据成为前置（对齐跨载体一致性先例）。"""
    bare = """\
## 跨载体一致性
- [x] 模板字段在参考文档、初始化输出和校验逻辑中无冲突

## 边界回归
- [x] 越界负样本：无
- [x] 顺序交换负样本：无
- [x] 旁路负样本：无

**验收结果**：通过
"""
    results = compute_gate_results(_make_spec(), _make_tasks(done=True), bare, slug="2026-09-25_add-gate")
    assert results.boundary_regression_section_exists is True
    assert results.has_new_gate_markers is True
    assert results.evidence_refill_ok is False  # bare 验收结果：通过 has no command evidence
    assert results.overall_ok is False

    refilled = bare.replace(
        "**验收结果**：通过",
        "## 验收证据\n- 脚本验证：pytest -q\n- 差异边界：bounded\n**验收结果**：通过",
        1,
    )
    refilled_results = compute_gate_results(_make_spec(), _make_tasks(done=True), refilled, slug="2026-09-25_add-gate")
    assert refilled_results.evidence_refill_ok is True
    assert refilled_results.overall_ok is True


def test_boundary_regression_rows_report_checked_counts():
    checklist = _complete_checklist_with_boundary_regression(unchecked_rows=1)
    results = compute_gate_results(_make_spec(), _make_tasks(done=True), checklist, slug="2026-09-25_add-gate")
    assert results.boundary_regression_total == 3
    assert results.boundary_regression_checked == 2
    rows = {label: (passed, evidence) for label, passed, evidence in gate_rows(results, "2026-09-25_add-gate")}
    passed, evidence = rows["边界回归"]
    assert passed is False
    assert "勾选 2/3" in evidence


def test_boundary_regression_failure_reaches_alerts_and_json_overall_gaps(tmp_path):
    slug = "2026-09-25_add-boundary-gate"
    root = tmp_path / "repo"
    _write_triad(
        root / ".spec" / "specs" / slug,
        _make_spec(),
        _make_tasks(done=True),
        _complete_checklist_with_boundary_regression(unchecked_rows=1),
    )
    markdown = _run_check(root, slug)
    assert markdown.returncode == 1
    assert "边界回归尚未满足" in markdown.stdout
    assert "勾选 2/3" in markdown.stdout

    payload = json.loads(_run_check(root, slug, "--format", "json").stdout)
    assert payload["overall"] is False
    assert any(gap.startswith("边界回归：") for gap in payload["overallGaps"]), payload["overallGaps"]


def test_boundary_regression_section_after_evidence_still_parsed():
    """顺序交换负样本自身：节位于验收证据之后仍被解析，结论不变。"""
    checklist = """\
## 跨载体一致性
- [x] 模板字段在参考文档、初始化输出和校验逻辑中无冲突

## 验收证据
- 外部对标：task package state machine
- 脚本验证：pytest
- 旧新对比：aliases checked
- 差异边界：bounded
**验收结果**：通过

## 边界回归
- [x] 越界负样本：无
- [x] 顺序交换负样本：无
- [x] 旁路负样本：无
"""
    results = compute_gate_results(_make_spec(), _make_tasks(done=True), checklist, slug="2026-09-25_add-gate")
    assert results.boundary_regression_ok is True
    assert results.overall_ok is True


def test_consistency_absence_does_not_block_boundary_regression_package():
    """可选门缺席不参与判定（commands.md /spec:check 契约）：仅有边界回归节的包在全勾+有命令证据时应通过。"""
    checklist = """\
## 边界回归
- [x] 越界负样本：无
- [x] 顺序交换负样本：无
- [x] 旁路负样本：无

## 验收证据
- 外部对标：task package state machine
- 脚本验证：pytest -q
- 旧新对比：old -> new
- 差异边界：bounded
**验收结果**：通过
"""
    results = compute_gate_results(_make_spec(), _make_tasks(done=True), checklist, slug="2026-09-25_add-gate")
    assert results.has_new_gate_markers is True
    assert results.consistency_section_exists is False
    rows = {label: passed for label, passed, _evidence in gate_rows(results, "2026-09-25_add-gate")}
    assert rows["跨载体一致性"] is None
    assert rows["边界回归"] is True
    assert results.overall_ok is True


# -- spec.md functional-checkbox gate (unchecked feature box = fail closed) --


def _spec_with_function_boxes(markers: tuple[str, ...]) -> str:
    """Return a spec whose §3.1 carries the given checkbox markers."""
    boxes = "\n".join(f"- [{marker}] 功能 {index}：描述" for index, marker in enumerate(markers, start=1))
    return _make_spec().replace(
        "## 3. 功能范围\n",
        f"## 3. 功能范围\n### 3.1 核心功能（MVP）\n{boxes}\n",
    )


def test_spec_unchecked_function_box_fails_overall_and_reports_gap():
    """spec.md 含未勾功能框 → overall=False 且差距含「spec.md 未勾功能框 N 项」。"""
    from check_spec_package import convergence_state

    spec = _spec_with_function_boxes((" ", "x", " "))
    results = compute_gate_results(spec, _make_tasks(done=True), _make_checklist(passed=True), slug="2026-09-25_fix-x")
    assert results.unchecked_spec_functions == 2
    assert results.function_ok is False
    assert results.overall_ok is False
    _converged, gaps = convergence_state(results)
    assert "spec.md 未勾功能框 2 项" in gaps
    rows = {label: passed for label, passed, _evidence in gate_rows(results, "2026-09-25_fix-x")}
    assert rows["功能与验证"] is False


def test_spec_all_function_boxes_checked_passes():
    """spec.md 功能框全勾且其余门禁齐备 → 通过。"""
    spec = _spec_with_function_boxes(("x", "X"))
    results = compute_gate_results(spec, _make_tasks(done=True), _make_checklist(passed=True), slug="2026-09-25_fix-x")
    assert results.unchecked_spec_functions == 0
    assert results.function_ok is True
    assert results.overall_ok is True


def test_spec_without_any_checkboxes_is_vacuous_pass():
    """spec.md 无任何复选框 → vacuous 通过（既有 boxless spec 行为不变）。"""
    assert "- [" not in _make_spec()
    results = compute_gate_results(
        _make_spec(), _make_tasks(done=True), _make_checklist(passed=True), slug="2026-09-25_fix-x"
    )
    assert results.unchecked_spec_functions == 0
    assert results.overall_ok is True


def _scope_spec(level: str = "package"):
    return (
        _make_spec()
        + f"""
### 5.1 验证策略
- 范围级别：{level}
- 变更对象：changed module
- 快速检查：pytest tests/test_changed.py
- 集成检查：适用外：无直接受影响链路
- 全项目检查：适用外：发布门禁
- 升级触发：共享基础设施或跨模块契约变化
"""
    )


def test_check_reports_declared_verification_scope():
    results = compute_gate_results(
        _scope_spec("package"),
        _make_tasks(done=True),
        _make_checklist(passed=True, verification_scope="package"),
        slug="2026-09-25_fix-scope",
    )
    assert results.verification_scope == "package"
    assert results.verification_scope_section_exists is True
    assert results.verification_scope_errors == []
    rows = {label: passed for label, passed, _evidence in gate_rows(results, "2026-09-25_fix-scope")}
    assert rows["验证范围"] is True
    assert results.overall_ok is True


@pytest.mark.parametrize("evidence_scope", [None, "package"])
def test_check_rejects_missing_or_mismatched_verification_scope_evidence(evidence_scope):
    results = compute_gate_results(
        _scope_spec("project"),
        _make_tasks(done=True),
        _make_checklist(passed=True, verification_scope=evidence_scope),
        slug="2026-09-25_fix-scope",
    )
    assert results.overall_ok is False
    assert any("验证范围" in error for error in results.verification_scope_errors)
    assert gate_failure_details(results, "2026-09-25_fix-scope")["验证范围"]


def test_check_rejects_malformed_declared_verification_scope():
    results = compute_gate_results(
        _scope_spec("unknown"), _make_tasks(done=True), _make_checklist(passed=True), slug="2026-09-25_fix-scope"
    )
    assert results.verification_scope_errors
    assert results.overall_ok is False
    assert gate_failure_details(results, "2026-09-25_fix-scope")["验证范围"]

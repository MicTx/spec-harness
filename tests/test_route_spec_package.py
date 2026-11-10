import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_spec_package import compute_gate_results, has_blocked_tasks
from report_spec_package import (
    package_status_summary,
    parse_tasks,
    render_multi_status,
    render_status,
    render_tasks,
)
from route_spec_package import active_packages, route_for_package
from spec_package_support import write_text


def _create_package(specs_dir, slug, spec_text="# T\n", tasks_text="# T\n", checklist_text="# T\n"):
    pkg = specs_dir / slug
    pkg.mkdir(parents=True, exist_ok=True)
    write_text(pkg / "spec.md", spec_text)
    write_text(pkg / "tasks.md", tasks_text)
    # checklist_text=None models a v2 package (F11): no checklist.md at all.
    if checklist_text is not None:
        write_text(pkg / "checklist.md", checklist_text)
    return pkg


COMPLETE_SPEC = """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：目标
- **目标用户**：用户
- **核心价值**：价值

## 2. 假设与待确认
### 2.1 已确认事实
- 事实
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


COMPLETE_CHECKLIST = "# C\n- [x] Item\n## 验收证据\n- 脚本验证：pytest -q\n**验收结果**：通过\n"
COMPLETED_PROJECTION_TASKS = "## 实现\n" + "".join(
    f"- [{marker}] {title}\n  - boundary: 状态投影\n  - verify: pytest -q\n"
    for marker, title in (
        ("x", "修复阻塞与状态显示"),
        ("X", "解除依赖阻塞"),
        ("x", "补充回归测试"),
        ("X", "核验输出合同"),
    )
)


@pytest.mark.parametrize("ascii_mode,compact", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("blocker", [None, ("!", "等待授权")])
def test_completed_task_projection_agrees_across_route_and_report(tmp_path, ascii_mode, compact, blocker):
    """Route and report project structured markers identically.

    AUD-10: only ``!``/status-line blocks are gate-level, and only marker
    blocks keep the route↔report package-state agreement; title-word
    samples are covered by test_title_block_word_is_display_only.
    """
    slug = "2026-09-14_completed-projection"
    tasks_text = COMPLETED_PROJECTION_TASKS
    if blocker:
        marker, title = blocker
        tasks_text += f"- [{marker}] {title}\n  - boundary: 依赖\n  - verify: pytest -q\n"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text=tasks_text,
        checklist_text=COMPLETE_CHECKLIST,
    )
    gate = compute_gate_results(COMPLETE_SPEC, tasks_text, COMPLETE_CHECKLIST, slug=slug)
    assert gate.blocked_tasks is bool(blocker)
    assert gate.all_tasks_done is (blocker is None)
    if blocker is None:
        assert gate.overall_ok

    sections, tasks = parse_tasks(tasks_text)
    summary = package_status_summary(slug, tasks_text, COMPLETE_SPEC, COMPLETE_CHECKLIST, sections, tasks)
    state = _route_state(tmp_path, slug)
    assert state["health"] == summary["state"] == ("阻塞" if blocker else "正常")
    assert state["stage"] == ("status" if blocker else "done")
    assert state["completed"] == 4
    assert state["total"] == (5 if blocker else 4)
    assert state["currentTask"] is None
    assert state["dependencyErrors"] == []
    assert state["verificationScope"] is None
    assert summary["progress"] == ("4/5" if blocker else "4/4")
    assert summary["blocked"] == (blocker[1] if blocker else "")
    assert summary["verification_scope"] == "not declared in legacy package"

    task_list = render_tasks(slug, sections, tasks)
    route = route_for_package(tmp_path, slug, ascii_mode=ascii_mode, compact=compact)
    report = render_status(
        slug,
        "Test",
        tasks_text,
        COMPLETE_SPEC,
        COMPLETE_CHECKLIST,
        sections,
        tasks,
        ascii_mode=ascii_mode,
        compact=compact,
    )
    for task in tasks[:4]:
        assert f"- [x] {task.text}" in task_list
        for output in (route, report):
            assert f"{task.text}: done" in output
            assert f"Task blocked: {task.text}" not in output
            assert f"Unblock task: {task.text}" not in output
    for output in (route, report):
        assert f"- Completed: {summary['progress']}" in output
        if blocker:
            assert f"{blocker[1]}: blocked" in output
            assert output.count("Task blocked: ") == 1
            assert f"Task blocked: {blocker[1]}" in output
            assert f"Unblock task: {blocker[1]}" in output
            assert "Prepare delivery and archive the record" not in output
        else:
            assert "Task blocked: " not in output
            assert "Unblock task: " not in output
            assert "## Attention" not in output
            assert "Prepare delivery and archive the record" in output
    if blocker:
        assert f"- [!] {blocker[1]}" in task_list
    else:
        assert "- [!]" not in task_list


def test_anchored_blocked_title_is_not_runnable(tmp_path):
    """An anchored blocked title is not a literal marker, but it is not runnable."""
    slug = "2026-09-14_completed-projection"
    tasks_text = COMPLETED_PROJECTION_TASKS + "- [ ] 阻塞：等待外部依赖\n  - boundary: 依赖\n  - verify: pytest -q\n"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text=tasks_text,
        checklist_text=COMPLETE_CHECKLIST,
    )
    gate = compute_gate_results(COMPLETE_SPEC, tasks_text, COMPLETE_CHECKLIST, slug=slug)
    assert gate.blocked_tasks is True
    state = _route_state(tmp_path, slug)
    assert state["health"] == "阻塞"
    assert state["stage"] == "status"
    sections, tasks = parse_tasks(tasks_text)
    summary = package_status_summary(slug, tasks_text, COMPLETE_SPEC, COMPLETE_CHECKLIST, sections, tasks)
    assert summary["blocked"] == "阻塞：等待外部依赖"

    task_list = render_tasks(slug, sections, tasks)
    route = route_for_package(tmp_path, slug, ascii_mode=False, compact=False)
    assert "Task blocked: 阻塞：等待外部依赖" in route
    # Display promotes the declared blocked title, while the source marker stays unchecked.
    assert "- [ ] 阻塞：等待外部依赖" in tasks_text
    assert "- [!] 阻塞：等待外部依赖" in task_list


def test_ready_task_continues_with_independent_blocked_task(tmp_path):
    slug = "2026-09-14_mixed-blocked-ready"
    tasks_text = (
        "## 实现\n"
        "- [!] Waiting for external service\n  - id: task-blocked\n  - boundary: a\n  - verify: `true`\n"
        "- [ ] Fix independent issue\n  - id: task-ready\n  - boundary: b\n  - verify: `true`\n"
        "- [ ] Dependent task\n  - id: task-dependent\n  - depends-on: task-blocked\n"
        "  - boundary: c\n  - verify: `true`\n"
    )
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text=tasks_text,
        checklist_text=COMPLETE_CHECKLIST,
    )
    state = _route_state(tmp_path, slug)
    assert state["stage"] == "run"
    assert state["health"] == "阻塞"
    assert state["currentTask"] == "Fix independent issue"
    assert state["nextAction"] == "Fix independent issue"
    assert state["humanQuestions"] == []
    route = route_for_package(tmp_path, slug)
    assert "Task blocked: Waiting for external service" in route
    sections, tasks = parse_tasks(tasks_text)
    status = render_status(slug, "Test", tasks_text, COMPLETE_SPEC, COMPLETE_CHECKLIST, sections, tasks)
    assert "Fix independent issue" in status.split("## Next")[-1]
    assert "Unblock task" not in status.split("## Next")[-1]


@pytest.mark.parametrize(
    "blocked_line",
    ["  - blocked: awaiting authorization", "  blocked: awaiting authorization", "  - 阻塞：等待授权"],
)
@pytest.mark.parametrize("explicit_id", [True, False])
def test_ready_task_skips_explicit_task_detail_blocker(tmp_path, blocked_line, explicit_id):
    slug = "2026-11-09_blocked-detail"
    tasks_text = (
        "## Implementation\n"
        "- [ ] Await approval\n  - id: task-blocked\n  - boundary: external\n  - verify: `true`\n"
        f"{blocked_line}\n"
        "- [ ] Independent work\n  - id: task-ready\n  - boundary: local\n  - verify: `true`\n"
        "- [ ] Dependent work\n  - id: task-dependent\n  - depends-on: task-blocked\n"
        "  - boundary: local\n  - verify: `true`\n"
    )
    if not explicit_id:
        tasks_text = (
            tasks_text.replace("  - id: task-blocked\n", "")
            .replace("  - id: task-ready\n", "")
            .replace("  - id: task-dependent\n", "")
            .replace("depends-on: task-blocked", "depends-on: task_0001")
        )
    _create_package(tmp_path / ".spec" / "specs", slug, COMPLETE_SPEC, tasks_text, COMPLETE_CHECKLIST)
    state = json.loads(route_for_package(tmp_path, slug, output_format="json", brief=True))
    assert state["stage"] == "run"
    assert state["health"] == "阻塞"
    assert state["currentTask"] == state["nextAction"] == "Independent work"
    assert state["ready"] == ["task-ready" if explicit_id else "task_0002"]
    assert next(task for task in state["tasks"] if task["title"] == "Await approval")["state"] == "blocked"
    assert any("Await approval" in alert for alert in state["alerts"])
    sections, tasks = parse_tasks(tasks_text)
    status = render_status(slug, "Test", tasks_text, COMPLETE_SPEC, COMPLETE_CHECKLIST, sections, tasks)
    assert "Task blocked: Await approval" in status
    assert "Fix independent issue" not in status.split("## Next")[-1]
    assert "Independent work" in status.split("## Next")[-1]


def test_completed_task_detail_does_not_poison_an_independent_task(tmp_path):
    slug = "2026-11-09_completed-detail"
    tasks_text = (
        "## Implementation\n- [x] Previously blocked\n  - id: task-done\n  - boundary: local\n"
        "  - verify: `true`\n  - blocked: historical issue resolved\n"
        "- [ ] Independent work\n  - id: task-ready\n  - boundary: local\n  - verify: `true`\n"
    )
    _create_package(tmp_path / ".spec" / "specs", slug, COMPLETE_SPEC, tasks_text, COMPLETE_CHECKLIST)
    state = json.loads(route_for_package(tmp_path, slug, output_format="json", brief=True))
    assert state["stage"] == "run"
    assert state["currentTask"] == "Independent work"
    assert state["ready"] == ["task-ready"]


@pytest.mark.parametrize(
    "tasks_text",
    [
        "## Implementation\n- [!] Blocked\n  - boundary: local\n  - verify: `true`\n"
        "- [ ] Ready\n  - boundary: local\n  - verify: `true`\n",
        "## Implementation\n- [x] Done\n  - boundary: local\n  - verify: `true`\n",
    ],
)
def test_explicit_slug_on_wrong_branch_never_routes_to_execution(tmp_path, tasks_text):
    slug = "2026-11-09_wrong-branch"
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    spec = COMPLETE_SPEC + f"\n- Git integration branch: `spec/{slug}`\n"
    spec = spec.replace("### 2.3 待确认问题\n- 无", "### 2.3 待确认问题\n- [ ] [fact] Investigate locally")
    _create_package(tmp_path / ".spec" / "specs", slug, spec, tasks_text, COMPLETE_CHECKLIST)
    state = _route_state(tmp_path, slug)
    assert state["stage"] == "status"
    assert state["currentTask"] is None
    assert "branch" in state["nextAction"].lower()
    assert "main" in state["branchError"] and f"spec/{slug}" in state["branchError"]
    output = route_for_package(tmp_path, slug)
    assert "main" in output and f"spec/{slug}" in output
    sections, tasks = parse_tasks(tasks_text)
    status = render_status(slug, "Test", tasks_text, spec, COMPLETE_CHECKLIST, sections, tasks, root=tmp_path)
    assert "Switch to the package branch" in status.split("## Next")[-1]
    assert "main" in status and f"spec/{slug}" in status


def test_ready_selection_excludes_invalid_verify_contract(tmp_path):
    slug = "2026-11-09_invalid-ready"
    spec = COMPLETE_SPEC + "\nevidence: runs\n"
    tasks_text = (
        "## Implementation\n"
        "- [!] Blocked\n  - id: task-blocked\n  - boundary: local\n  - verify: `true`\n"
        "- [ ] Invalid\n  - id: task-invalid\n  - boundary: local\n  - verify: passes\n"
        "- [ ] Runnable\n  - id: task-valid\n  - boundary: local\n  - verify: `true`\n"
    )
    _create_package(tmp_path / ".spec" / "specs", slug, spec, tasks_text, COMPLETE_CHECKLIST)
    state = json.loads(route_for_package(tmp_path, slug, output_format="json", brief=True))
    assert state["stage"] == "run"
    assert state["currentTask"] == "Runnable"
    assert state["ready"] == ["task-valid"]
    assert next(task for task in state["tasks"] if task["id"] == "task-invalid")["state"] == "pending"
    sections, tasks = parse_tasks(tasks_text)
    status = render_status(slug, "Test", tasks_text, spec, COMPLETE_CHECKLIST, sections, tasks)
    assert "Runnable" in status.split("## Next")[-1]
    assert "Invalid" not in status.split("## Next")[-1]


@pytest.mark.parametrize("blocked_line", ["  blocked: freeze", "- blocked: freeze", "  - 阻塞：维护"])
def test_unowned_package_blocker_prevents_ready_work(tmp_path, blocked_line):
    slug = "2026-11-09_package-blocker"
    tasks_text = (
        f"## Implementation\n{blocked_line}\n"
        "- [ ] Independent-looking task\n  - id: task-ready\n  - boundary: local\n  - verify: `true`\n"
    )
    _create_package(tmp_path / ".spec" / "specs", slug, COMPLETE_SPEC, tasks_text, COMPLETE_CHECKLIST)
    state = json.loads(route_for_package(tmp_path, slug, output_format="json", brief=True))
    assert state["stage"] == "status"
    assert state["health"] == "阻塞"
    assert state["currentTask"] is None and state["ready"] == []
    assert "blocked" in state["nextAction"].lower()
    sections, tasks = parse_tasks(tasks_text)
    status = render_status(slug, "Test", tasks_text, COMPLETE_SPEC, COMPLETE_CHECKLIST, sections, tasks)
    assert "Independent-looking task" not in status.split("## Next")[-1]


@pytest.mark.parametrize("malformed", ["passes", "`   `", "`true`\n  - verify: `true`"])
def test_invalid_verify_without_ready_task_never_advises_execution(tmp_path, malformed):
    slug = "2026-11-09_no-ready-verify"
    spec = COMPLETE_SPEC + "\nevidence: runs\n"
    tasks_text = f"## Implementation\n- [ ] Invalid\n  - boundary: local\n  - verify: {malformed}\n"
    _create_package(tmp_path / ".spec" / "specs", slug, spec, tasks_text, COMPLETE_CHECKLIST)
    state = json.loads(route_for_package(tmp_path, slug, output_format="json", brief=True))
    assert state["stage"] != "run"
    assert state["health"] != "正常"
    assert state["ready"] == []
    assert "verify" in state["nextAction"].lower()
    sections, tasks = parse_tasks(tasks_text)
    status = render_status(slug, "Test", tasks_text, spec, COMPLETE_CHECKLIST, sections, tasks)
    assert "archive" not in status.split("## Next")[-1].lower()
    assert "verify" in status.split("## Next")[-1].lower()


def test_no_ready_missing_boundary_has_repair_action_in_all_views(tmp_path):
    slug = "2026-11-09_missing-boundary"
    tasks_text = "## Implementation\n- [ ] Incomplete\n  - verify: `true`\n"
    _create_package(tmp_path / ".spec" / "specs", slug, COMPLETE_SPEC, tasks_text, COMPLETE_CHECKLIST)
    state = json.loads(route_for_package(tmp_path, slug, output_format="json", brief=True))
    assert state["stage"] == "status"
    assert state["health"] == "风险"
    assert state["ready"] == [] and state["currentTask"] is None
    assert "task" in state["nextAction"].lower() and "fix" in state["nextAction"].lower()
    sections, tasks = parse_tasks(tasks_text)
    status = render_status(slug, "Test", tasks_text, COMPLETE_SPEC, COMPLETE_CHECKLIST, sections, tasks)
    assert "archive" not in status.split("## Next")[-1].lower()


def test_duplicate_ids_keep_title_blocker_visible_in_both_views(tmp_path):
    slug = "2026-11-09_duplicate-blocked"
    tasks_text = (
        "## Implementation\n- [ ] blocked: Await approval\n  - id: task-dup\n  - boundary: local\n  - verify: `true`\n"
        "- [ ] Other\n  - id: task-dup\n  - boundary: local\n  - verify: `true`\n"
    )
    _create_package(tmp_path / ".spec" / "specs", slug, COMPLETE_SPEC, tasks_text, COMPLETE_CHECKLIST)
    state = json.loads(route_for_package(tmp_path, slug, output_format="json", brief=True))
    assert state["stage"] == "status" and state["health"] == "阻塞"
    assert any("blocked" in alert.lower() for alert in state["alerts"])
    sections, tasks = parse_tasks(tasks_text)
    status = render_status(slug, "Test", tasks_text, COMPLETE_SPEC, COMPLETE_CHECKLIST, sections, tasks)
    assert "Task blocked: blocked: Await approval" in status


def test_route_and_status_expose_declared_verification_scope(tmp_path):
    slug = "2026-10-03_fix-route-scope"
    scoped_spec = (
        COMPLETE_SPEC
        + """
### 5.1 验证策略
- 范围级别：package
- 变更对象：changed module
- 快速检查：pytest tests/test_changed.py
- 集成检查：适用外：无直接受影响链路
- 全项目检查：适用外：发布门禁
- 升级触发：共享基础设施或跨模块契约变化
"""
    )
    scoped_checklist = COMPLETE_CHECKLIST.replace(
        "## 验收证据\n",
        "## 验收证据\n- 验证范围：package；实际执行命令必须属于该范围\n",
        1,
    )
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=scoped_spec,
        tasks_text=COMPLETED_PROJECTION_TASKS,
        checklist_text=scoped_checklist,
    )
    state = _route_state(tmp_path, slug)
    assert state["verificationScope"] == "package"
    sections, tasks = parse_tasks(COMPLETED_PROJECTION_TASKS)
    summary = package_status_summary(slug, COMPLETED_PROJECTION_TASKS, scoped_spec, scoped_checklist, sections, tasks)
    assert summary["verification_scope"] == "package"
    status = render_status(slug, "Test", COMPLETED_PROJECTION_TASKS, scoped_spec, scoped_checklist, sections, tasks)
    assert "## Verification Scope" in status
    assert "Level: package" in status

    invalid_spec = scoped_spec.replace("- 范围级别：package", "- 范围级别：invalid")
    _create_package(
        tmp_path / ".spec" / "specs",
        "2026-10-03_fix-route-scope-invalid",
        spec_text=invalid_spec,
        tasks_text=COMPLETED_PROJECTION_TASKS,
        checklist_text=scoped_checklist,
    )
    invalid_route = route_for_package(tmp_path, "2026-10-03_fix-route-scope-invalid")
    assert "Verification scope requirements not met" in invalid_route
    invalid_status = render_status(
        "2026-10-03_fix-route-scope-invalid",
        "Test",
        COMPLETED_PROJECTION_TASKS,
        invalid_spec,
        scoped_checklist,
        sections,
        tasks,
    )
    assert "Verification scope requirements not met" in invalid_status


@pytest.mark.parametrize("ascii_mode,compact", [(False, False), (True, False), (False, True), (True, True)])
def test_route_completed_tasks_do_not_override_standalone_blocked_gate(tmp_path, ascii_mode, compact):
    slug = "2026-09-14_standalone-blocked"
    tasks_text = COMPLETED_PROJECTION_TASKS + "\n阻塞：等待外部依赖\n"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text=tasks_text,
        checklist_text=COMPLETE_CHECKLIST,
    )
    gate = compute_gate_results(COMPLETE_SPEC, tasks_text, COMPLETE_CHECKLIST, slug=slug)
    assert not gate.all_tasks_done
    assert gate.blocked_tasks
    state = _route_state(tmp_path, slug)
    assert state["stage"] == "status"
    assert state["health"] == "阻塞"
    assert state["completed"] == state["total"] == 4
    output = route_for_package(tmp_path, slug, ascii_mode=ascii_mode, compact=compact)
    assert "Tasks are blocked; see the task list for details" in output
    assert "Resolve blocked package" in output
    assert "Task blocked: " not in output
    assert "Prepare delivery and archive the record" not in output


def _route_state(root: Path, slug: str) -> dict:
    return json.loads(route_for_package(root, slug, output_format="json"))


def test_active_packages_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        assert active_packages(Path(tmpdir) / "specs") == []


def test_active_packages_excludes_archive():
    with tempfile.TemporaryDirectory() as tmpdir:
        specs = Path(tmpdir) / "specs"
        (specs / "archive").mkdir(parents=True)
        _create_package(specs, "pkg-a")
        assert active_packages(specs) == ["pkg-a"]


def test_active_packages_multiple():
    with tempfile.TemporaryDirectory() as tmpdir:
        specs = Path(tmpdir) / "specs"
        _create_package(specs, "pkg-a")
        _create_package(specs, "pkg-b")
        assert active_packages(specs) == ["pkg-a", "pkg-b"]


def test_active_packages_incomplete_skipped():
    with tempfile.TemporaryDirectory() as tmpdir:
        specs = Path(tmpdir) / "specs"
        pkg = specs / "incomplete"
        pkg.mkdir(parents=True)
        (pkg / "spec.md").write_text("# T", encoding="utf-8")
        assert active_packages(specs) == []


def test_has_blocked():
    assert has_blocked_tasks("- [!] Blocked task")
    assert has_blocked_tasks("- [ ] Task\n阻塞：等待依赖")
    assert not has_blocked_tasks("- [x] Done task")
    assert not has_blocked_tasks("- [ ] Parser test\n  - verify: 阻塞文本只是测试数据")


def test_route_cli_prefers_package_bound_to_current_branch(tmp_path):
    first = "2026-09-05_fix-first-route"
    second = "2026-09-06_fix-second-route"
    for slug in (first, second):
        spec = COMPLETE_SPEC + f"\n## 5. 技术决策\n- Git integration branch：`spec/{slug}`\n"
        _create_package(
            tmp_path / ".spec" / "specs",
            slug,
            spec_text=spec,
            tasks_text=f"- [ ] Task {slug}\n  - boundary: x\n  - verify: x\n",
            checklist_text="**验收结果**：待修复\n",
        )
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "switch", "-qc", f"spec/{first}"], cwd=tmp_path, check=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts" / "route_spec_package.py"),
            "--root",
            str(tmp_path),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout)["slug"] == first

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _create_package(
            root / ".spec" / "specs",
            "test-pkg",
            spec_text="# Fresh\n",
            tasks_text="- [ ] Task\n",
            checklist_text="# C\n",
        )
        assert _route_state(root, "test-pkg")["stage"] == "status"


def test_route_json_preserves_internal_stage_for_execution():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _create_package(
            root / ".spec" / "specs",
            "test-pkg",
            spec_text=COMPLETE_SPEC,
            tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
            checklist_text="# C\n**验收结果**：待修复\n",
        )
        state = _route_state(root, "test-pkg")
        assert state["stage"] == "run"
        assert state["completed"] == 0
        assert state["total"] == 1


def test_route_json_preserves_internal_stage_for_acceptance():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _create_package(
            root / ".spec" / "specs",
            "test-pkg",
            spec_text=COMPLETE_SPEC,
            tasks_text="- [x] Task A\n  - boundary: x\n  - verify: x\n",
            checklist_text="# C\n**验收结果**：待修复\n",
        )
        assert _route_state(root, "test-pkg")["stage"] == "check"


def test_route_json_preserves_internal_stage_for_delivery():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        slug = "2026-06-12_test-pkg"
        _create_package(
            root / ".spec" / "specs",
            slug,
            spec_text=COMPLETE_SPEC,
            tasks_text="- [x] Task A\n  - boundary: x\n  - verify: x\n",
            checklist_text="# C\n- [x] Item\n## 验收证据\n- 脚本验证：pytest -q\n**验收结果**：通过\n",
        )
        assert _route_state(root, slug)["stage"] == "done"


def test_route_markdown_uses_project_language_not_process_commands():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _create_package(
            root / ".spec" / "specs",
            "test-pkg",
            spec_text=COMPLETE_SPEC,
            tasks_text="## 实现\n- [ ] 实现支付回调\n  - boundary: x\n  - verify: x\n",
            checklist_text="# C\n**验收结果**：待修复\n",
        )
        output = route_for_package(root, "test-pkg")
        assert output.startswith("# Test\n")
        assert "- Completed: 0/1" in output
        assert "实现支付回调: todo" in output
        assert "## Next" in output
        assert "/spec:" not in output
        assert "## Pipeline" not in output
        assert "Gate" not in output


def test_route_markdown_translates_package_gaps_for_users():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _create_package(
            root / ".spec" / "specs",
            "test-pkg",
            spec_text="# Fresh\n",
            tasks_text="- [ ] Task\n",
            checklist_text="# C\n",
        )
        output = route_for_package(root, "test-pkg")
        assert "Project definition incomplete: Project goal is not defined" in output
        assert "Key assumptions are not recorded" in output
        assert "Complete the project definition: Project goal is not defined" in output
        assert "spec.md" not in output
        assert "任务包三件套" not in output
        assert "Minimal implementation path is not defined" in output


def test_route_markdown_describes_acceptance_gaps_as_project_facts():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _create_package(
            root / ".spec" / "specs",
            "test-pkg",
            spec_text=COMPLETE_SPEC,
            tasks_text="- [x] Task A\n  - boundary: x\n",
            checklist_text="# C\n- [ ] Item\n**验收结果**：通过\n",
        )
        output = route_for_package(root, "test-pkg")
        assert "Task instructions incomplete" in output
        assert "1 missing verification methods" in output
        assert "checklist" not in output
        assert "boundary" not in output
        assert "/spec:" not in output


def _run_route(root: Path, slug: str, output_format: str = "markdown", *flags: str):
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "route_spec_package.py"),
            "--root",
            str(root),
            "--slug",
            slug,
            "--format",
            output_format,
            *flags,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )


@pytest.mark.parametrize("output_format", ["markdown", "json"])
def test_route_cli_tolerates_missing_checklist_v2(tmp_path, output_format):
    """v2 packages (F11) ship no checklist.md: its absence switches the
    acceptance surface, it is never an unreadable-triad recovery — the
    package routes normally with completed/total intact."""
    slug = "2026-10-12_v2-no-checklist"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text="- [x] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text=None,
    )
    result = _run_route(tmp_path, slug, output_format, "--ascii", "--compact")
    assert result.returncode == 0, result.stderr
    assert "Completed: unknown" not in result.stdout
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["slug"] == slug
        assert payload["completed"] == 1
        assert payload["total"] == 1


# -- 5.4 route errors must block the run stage, not surface only at check/done.


ORCHESTRATION_BLOCK_SPEC = """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：目标
- **目标用户**：用户
- **核心价值**：价值

## 2. 假设与待确认
### 2.1 已确认事实
- 事实
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
## 5. 技术决策
### 5.4 编排策略
- route: build + review
- ownership: 主线程
"""


ORCHESTRATION_VALID_SPEC = ORCHESTRATION_BLOCK_SPEC.replace("- route: build + review", "- route: build")


def test_route_invalid_54_is_advisory(tmp_path):
    slug = "2026-09-21_fix-public-example-09"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=ORCHESTRATION_BLOCK_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="**验收结果**：待修复\n",
    )
    state = _route_state(tmp_path, slug)
    assert state["stage"] == "run"
    assert "orchestrationErrors" not in state
    assert "Orchestration requirements not met" not in route_for_package(tmp_path, slug)


def test_route_and_report_agree_on_invalid_54_with_completed_tasks(tmp_path):
    """All tasks done + invalid 5.4: routing hints stay advisory, so both
    views advertise the same converged state."""
    slug = "2026-09-21_fix-public-example-09-done"
    tasks_text = "## 实现\n- [x] Task A\n  - boundary: x\n  - verify: x\n"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=ORCHESTRATION_BLOCK_SPEC,
        tasks_text=tasks_text,
        checklist_text=COMPLETE_CHECKLIST,
    )
    state = _route_state(tmp_path, slug)
    assert state["stage"] == "done"
    assert "orchestrationErrors" not in state

    sections, tasks = parse_tasks(tasks_text)
    summary = package_status_summary(slug, tasks_text, ORCHESTRATION_BLOCK_SPEC, COMPLETE_CHECKLIST, sections, tasks)
    assert summary["state"] == "正常", "summary health must agree with route health"
    assert "orchestration" not in summary
    assert summary["gate_failed"] == "no"

    report = render_status(
        slug,
        "Test",
        tasks_text,
        ORCHESTRATION_BLOCK_SPEC,
        COMPLETE_CHECKLIST,
        sections,
        tasks,
    )
    assert "Orchestration requirements not met" not in report
    assert "Prepare delivery and archive the record" in report

    overview = render_multi_status(tmp_path, tmp_path / ".spec", [slug])
    assert "Orchestration requirements not met" not in overview


def test_route_valid_54_still_runs_and_absent_54_still_runs(tmp_path):
    slug_valid = "2026-09-21_fix-public-example-09-ok"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug_valid,
        spec_text=ORCHESTRATION_VALID_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="**验收结果**：待修复\n",
    )
    assert _route_state(tmp_path, slug_valid)["stage"] == "run"

    slug_absent = "2026-09-21_fix-public-example-09-absent"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug_absent,
        spec_text=COMPLETE_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="**验收结果**：待修复\n",
    )
    assert _route_state(tmp_path, slug_absent)["stage"] == "run"


# -- Final projection follow-up: combination states and real status entrypoints.

REPORT_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "report_spec_package.py"
NO_EVIDENCE_COMPLETE_CHECKLIST = "# C\n- [x] Item\n\n**验收结果**：通过\n"
ONE_COMPLETED_TASK = "## 实现\n- [x] Task A\n  - boundary: x\n  - verify: x\n"


def test_report_overview_projects_empty_package_as_blocked(tmp_path):
    slug = "2026-09-22_fix-status-overview-no-actionable-tasks"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text=ONE_COMPLETED_TASK,
        checklist_text=COMPLETE_CHECKLIST,
    )
    (pkg / "tasks.md").write_text("# no actionable tasks\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(REPORT_SCRIPT), "--root", str(tmp_path), "--view", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Attention" in proc.stdout
    assert "no actionable tasks defined" in proc.stdout
    assert "Continue current project tasks" not in proc.stdout


def test_report_summary_projects_completed_package_with_legacy_checklist_evidence_as_done(tmp_path):
    slug = "2026-09-22_fix-status-evidence"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text=ONE_COMPLETED_TASK,
        checklist_text=NO_EVIDENCE_COMPLETE_CHECKLIST,
    )
    sections, tasks = parse_tasks(ONE_COMPLETED_TASK)
    summary = package_status_summary(
        slug,
        ONE_COMPLETED_TASK,
        COMPLETE_SPEC,
        NO_EVIDENCE_COMPLETE_CHECKLIST,
        sections,
        tasks,
    )
    route_state = _route_state(tmp_path, slug)
    assert route_state["stage"] == "done"
    assert route_state["health"] == "正常"
    assert summary["state"] == "正常"
    assert summary["needs_work"] == "no"
    assert summary["gate_failed"] == "no"

    overview = render_multi_status(tmp_path, tmp_path / ".spec", [slug])
    assert "completed 1/1" in overview
    assert "acceptance failed" not in overview


@pytest.mark.parametrize(
    "spec_text,tasks_text",
    [
        (
            ORCHESTRATION_BLOCK_SPEC,
            "## 实现\n- [!] Blocked\n  - boundary: x\n  - verify: x\n",
        ),
        (
            COMPLETE_SPEC,
            "## 实现\n- [x] Done\n  - depends-on: task-ghost\n  - boundary: x\n  - verify: x\n\n阻塞：等待外部系统\n",
        ),
    ],
)
def test_report_summary_never_downgrades_blocked_to_risk(tmp_path, spec_text, tasks_text):
    slug = "2026-09-22_fix-status-health"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=spec_text,
        tasks_text=tasks_text,
        checklist_text=COMPLETE_CHECKLIST,
    )
    sections, tasks = parse_tasks(tasks_text)
    summary = package_status_summary(slug, tasks_text, spec_text, COMPLETE_CHECKLIST, sections, tasks)
    assert _route_state(tmp_path, slug)["health"] == "阻塞"
    assert summary["state"] == "阻塞"


def test_report_next_step_matches_route_when_definition_gap_precedes_dependency_error(tmp_path):
    slug = "2026-09-22_fix-status-priority"
    spec_text = COMPLETE_SPEC.replace("- **项目目标**：目标\n", "")
    tasks_text = "## 实现\n- [x] Done\n  - depends-on: task-ghost\n  - boundary: x\n  - verify: x\n"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=spec_text,
        tasks_text=tasks_text,
        checklist_text=COMPLETE_CHECKLIST,
    )
    sections, tasks = parse_tasks(tasks_text)
    route_output = route_for_package(tmp_path, slug)
    report_output = render_status(
        slug,
        "Test",
        tasks_text,
        spec_text,
        COMPLETE_CHECKLIST,
        sections,
        tasks,
    )
    assert "Complete the project definition" in route_output.split("## Next")[-1]
    assert "Complete the project definition" in report_output.split("## Next")[-1]
    assert "Fix task dependencies" not in report_output.split("## Next")[-1]


def test_report_overview_prioritizes_dependency_before_orchestration(tmp_path):
    slug = "2026-09-22_fix-status-overview-priority"
    tasks_text = "## 实现\n- [x] Done\n  - depends-on: task-ghost\n  - boundary: x\n  - verify: x\n"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=ORCHESTRATION_BLOCK_SPEC,
        tasks_text=tasks_text,
        checklist_text=COMPLETE_CHECKLIST,
    )
    route_output = route_for_package(tmp_path, slug)
    overview = render_multi_status(tmp_path, tmp_path / ".spec", [slug])
    assert "task-ghost" in route_output.split("## Next")[-1]
    assert "Fix project task dependencies" in overview.split("## Next")[-1]
    assert "Fix the project orchestration strategy" not in overview.split("## Next")[-1]


# --------------------------------------------------------------------------- #
# F3 brief / three-branch JSON (doc 03 §2.2, D-1 legacy-defect fix)
# --------------------------------------------------------------------------- #


def _route_cli(root, *args):
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts" / "route_spec_package.py"),
            "--root",
            str(root),
            *args,
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("extra_args", [["--format", "json"], ["--format", "json", "--brief"]])
def test_route_cli_json_covers_no_package_branch(tmp_path, extra_args, monkeypatch):
    """D-1 regression: --format json used to fall back to markdown here."""
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    completed = _route_cli(tmp_path, *extra_args)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["slug"] is None
    assert payload["stage"] == "new"


def test_route_cli_json_covers_unbound_branch(tmp_path):
    slug_a = "2026-09-01_fix-first-unbound"
    slug_b = "2026-09-02_fix-second-unbound"
    for slug in (slug_a, slug_b):
        spec = COMPLETE_SPEC + f"\n## 5. 技术决策\n- Git integration branch：`spec/{slug}`\n"
        _create_package(
            tmp_path / ".spec" / "specs",
            slug,
            spec_text=spec,
            tasks_text="- [ ] Task\n  - boundary: x\n  - verify: x\n",
            checklist_text="# C\n",
        )
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    completed = _route_cli(tmp_path, "--format", "json")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)  # markdown before the D-1 fix
    assert payload["slug"] is None
    assert payload["stage"] == "status"
    assert set(payload["candidates"]) == {slug_a, slug_b}


def test_route_cli_brief_envelope_superset(tmp_path):
    slug = "2026-09-03_fix-brief-envelope"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC + f"\n## 5. 技术决策\n- Git integration branch：`spec/{slug}`\n",
        tasks_text=(
            "- [ ] Task A\n  - boundary: 边界A\n  - verify: `pytest -q`\n"
            "- [x] Task B\n  - boundary: 边界B\n  - verify: `pytest -q`\n"
        ),
        checklist_text="# C\n**验收结果**：待修复\n",
    )
    plain = json.loads(_route_cli(tmp_path, "--slug", slug, "--format", "json").stdout)
    brief = json.loads(_route_cli(tmp_path, "--slug", slug, "--format", "json", "--brief").stdout)
    for key, value in plain.items():
        assert brief[key] == value, key
    assert {task["state"] for task in brief["tasks"]} == {"ready", "done"}
    assert brief["handoff"]["fresh"] == "absent"


def test_route_cli_brief_missing_slug_is_classified_error(tmp_path):
    completed = _route_cli(tmp_path, "--slug", "2026-09-04_fix-no-such", "--format", "json", "--brief")
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "task package not found" in completed.stderr

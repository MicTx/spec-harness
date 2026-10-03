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
from update_checkpoint_support import (
    begin_update_checkpoint,
    checkpoint_path,
    complete_update_checkpoint,
    rollback_update_checkpoint,
)


def _create_package(specs_dir, slug, spec_text="# T\n", tasks_text="# T\n", checklist_text="# T\n"):
    pkg = specs_dir / slug
    pkg.mkdir(parents=True, exist_ok=True)
    write_text(pkg / "spec.md", spec_text)
    write_text(pkg / "tasks.md", tasks_text)
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
@pytest.mark.parametrize("blocker", [None, (" ", "阻塞：等待外部依赖"), ("!", "等待授权")])
def test_completed_task_projection_agrees_across_route_and_report(tmp_path, ascii_mode, compact, blocker):
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
    assert state["updateCheckpoint"] == "none"
    assert summary["progress"] == ("4/5" if blocker else "4/4")
    assert summary["blocked"] == (blocker[1] if blocker else "无")
    assert summary["verification_scope"] == "历史包未声明"

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
            assert f"{task.text}：{'done' if ascii_mode else '完成'}" in output
            assert f"任务受阻：{task.text}" not in output
            assert f"解除阻塞：{task.text}" not in output
    for output in (route, report):
        assert f"已完成：{summary['progress']}" in output
        if blocker:
            assert f"{blocker[1]}：{'blocked' if ascii_mode else '阻塞'}" in output
            assert output.count("任务受阻：") == 1
            assert f"任务受阻：{blocker[1]}" in output
            assert f"解除阻塞：{blocker[1]}" in output
            assert "整理交付结果并完成归档" not in output
        else:
            assert "任务受阻：" not in output
            assert "解除阻塞：" not in output
            assert "需要关注" not in output
            assert "整理交付结果并完成归档" in output
    if blocker:
        assert f"- [!] {blocker[1]}" in task_list
    else:
        assert "- [!]" not in task_list


def test_route_and_status_expose_declared_verification_scope(tmp_path):
    slug = "2026-10-03_fix-route-scope"
    scoped_spec = COMPLETE_SPEC + """
### 5.1 验证策略
- 范围级别：package
- 变更对象：changed module
- 快速检查：pytest tests/test_changed.py
- 集成检查：适用外：无直接受影响链路
- 全项目检查：适用外：发布门禁
- 升级触发：共享基础设施或跨模块契约变化
"""
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
    assert "## 验证范围" in status
    assert "级别：package" in status

    invalid_spec = scoped_spec.replace("- 范围级别：package", "- 范围级别：project")
    _create_package(
        tmp_path / ".spec" / "specs",
        "2026-10-03_fix-route-scope-invalid",
        spec_text=invalid_spec,
        tasks_text=COMPLETED_PROJECTION_TASKS,
        checklist_text=scoped_checklist,
    )
    invalid_route = route_for_package(tmp_path, "2026-10-03_fix-route-scope-invalid")
    assert "验证范围尚未满足" in invalid_route
    invalid_status = render_status(
        "2026-10-03_fix-route-scope-invalid",
        "Test",
        COMPLETED_PROJECTION_TASKS,
        invalid_spec,
        scoped_checklist,
        sections,
        tasks,
    )
    assert "验证范围尚未满足" in invalid_status


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
    assert gate.all_tasks_done
    assert gate.blocked_tasks
    state = _route_state(tmp_path, slug)
    assert state["stage"] == "status"
    assert state["health"] == "阻塞"
    assert state["completed"] == state["total"] == 4
    output = route_for_package(tmp_path, slug, ascii_mode=ascii_mode, compact=compact)
    assert "项目中存在受阻任务，具体原因已记录在任务清单" in output
    assert "处理任务阻塞" in output
    assert "任务受阻：" not in output
    assert "整理交付结果并完成归档" not in output


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
        assert "已完成：0/1" in output
        assert "实现支付回调：进行" in output
        assert "## 接下来" in output
        assert "/spec:" not in output
        assert "## 流水线" not in output
        assert "门禁" not in output


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
        assert "项目定义不完整：尚未明确项目目标" in output
        assert "尚未记录关键假设" in output
        assert "补齐项目定义：尚未明确项目目标" in output
        assert "spec.md" not in output
        assert "任务包三件套" not in output
        assert "其余 3 项见 项目记录" in output


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
        assert "任务执行说明不完整" in output
        assert "缺验证方式 1 项" in output
        assert "项目尚有 1 项验收未完成" in output
        assert "checklist" not in output
        assert "boundary" not in output
        assert "/spec:" not in output


# --- update checkpoint blocking ---------------------------------------------


def test_route_blocks_on_active_update_checkpoint(tmp_path):
    slug = "2026-09-13_update-pkg"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="# C\n**验收结果**：待修复\n",
    )
    begin_update_checkpoint(pkg, "追加数据导出任务")

    output = route_for_package(tmp_path, slug)

    assert "存在未完成 update checkpoint：更新「追加数据导出任务」尚未确认完成或回滚" in output
    assert "## 需要关注" in output
    assert "处理未完成 update checkpoint：rollback 回滚更新，或 complete 确认保留" in output
    state = _route_state(tmp_path, slug)
    assert state["stage"] == "status"
    assert state["health"] == "阻塞"
    assert state["updateCheckpoint"] == "active"


def test_route_checkpoint_block_detects_without_modifying_files(tmp_path):
    slug = "2026-09-13_update-pkg"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="# C\n**验收结果**：待修复\n",
    )
    begin_update_checkpoint(pkg, "追加任务")
    before = {p.name: p.read_bytes() for p in sorted(pkg.iterdir())}

    route_for_package(tmp_path, slug)
    route_for_package(tmp_path, slug, output_format="json")

    after = {p.name: p.read_bytes() for p in sorted(pkg.iterdir())}
    assert after == before  # route detects; it never rewrites package state


def test_route_checkpoint_block_lifts_after_complete(tmp_path):
    slug = "2026-09-13_update-pkg"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="# C\n**验收结果**：待修复\n",
    )
    begin_update_checkpoint(pkg, "追加任务")
    write_text(
        pkg / "tasks.md",
        "- [x] Task A\n  - boundary: x\n  - verify: x\n\n- [ ] Task B\n  - boundary: x\n  - verify: x\n",
    )
    complete_update_checkpoint(pkg)

    output = route_for_package(tmp_path, slug)
    state = _route_state(tmp_path, slug)

    assert "存在未完成 update checkpoint" not in output
    assert state["stage"] == "run"
    assert state["health"] == "正常"
    assert state["updateCheckpoint"] == "none"


def test_route_checkpoint_block_lifts_after_rollback(tmp_path):
    slug = "2026-09-13_update-pkg"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="# C\n**验收结果**：待修复\n",
    )
    begin_update_checkpoint(pkg, "追加任务")
    rollback_update_checkpoint(pkg)

    output = route_for_package(tmp_path, slug)
    state = _route_state(tmp_path, slug)

    assert "存在未完成 update checkpoint" not in output
    assert state["stage"] == "run"
    assert state["updateCheckpoint"] == "none"


def test_route_blocks_on_corrupted_update_checkpoint(tmp_path):
    slug = "2026-09-13_update-pkg"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="# C\n**验收结果**：待修复\n",
    )
    begin_update_checkpoint(pkg, "追加任务")
    checkpoint_path(pkg).write_text("{ not json", encoding="utf-8")

    output = route_for_package(tmp_path, slug)
    state = _route_state(tmp_path, slug)

    assert "存在未完成 update checkpoint" in output
    assert "检查点记录损坏，需人工恢复" in output
    assert state["stage"] == "status"
    assert state["health"] == "阻塞"
    assert state["updateCheckpoint"] == "corrupted"


def test_route_output_has_no_checkpoint_block_when_clean(tmp_path):
    slug = "2026-09-13_update-pkg"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="# C\n**验收结果**：待修复\n",
    )

    output = route_for_package(tmp_path, slug)
    state = _route_state(tmp_path, slug)

    assert "update checkpoint" not in output
    assert state["stage"] == "run"
    assert state["updateCheckpoint"] == "none"


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


@pytest.mark.parametrize("state", ["active", "corrupted"])
@pytest.mark.parametrize("member", ["spec.md", "tasks.md", "checklist.md"])
@pytest.mark.parametrize("damage", ["missing", "invalid-utf8", "directory"])
@pytest.mark.parametrize("output_format", ["markdown", "json"])
def test_route_cli_recovers_with_unreadable_triad(tmp_path, state, member, damage, output_format):
    slug = "2026-09-13_update-pkg"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text="- [x] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="**验收结果**：待修复\n",
    )
    begin_update_checkpoint(pkg, "追加任务")
    if state == "corrupted":
        checkpoint_path(pkg).write_text("{ not json", encoding="utf-8")
    path = pkg / member
    path.unlink()
    if damage == "invalid-utf8":
        path.write_bytes(b"\xff")
    elif damage == "directory":
        path.mkdir()
    before = {p.name: p.read_bytes() if p.is_file() else None for p in pkg.iterdir()}

    result = _run_route(tmp_path, slug, output_format, "--ascii", "--compact")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "存在未完成 update checkpoint" in result.stdout
    assert "rollback" in result.stdout and "complete" in result.stdout
    if state == "corrupted":
        assert "需人工恢复" in result.stdout
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["slug"] == slug
        assert payload["stage"] == "status"
        assert payload["health"] == "阻塞"
        assert payload["updateCheckpoint"] == state
        assert payload["completed"] is None
        assert payload["total"] is None
        assert payload["currentTask"] is None
        assert payload["dependencyErrors"] == []
        assert "rollback" in payload["nextStep"]
    else:
        assert result.stdout.startswith(f"# {slug}\n")
        assert "阻塞" in result.stdout and "status" in result.stdout
        assert "已完成：未知" in result.stdout
        assert "0/0" not in result.stdout and "1/1" not in result.stdout
        assert "## 需要关注" in result.stdout
        assert "## 接下来" in result.stdout
    assert {p.name: p.read_bytes() if p.is_file() else None for p in pkg.iterdir()} == before


@pytest.mark.parametrize("damage", ["missing", "invalid-utf8", "directory"])
@pytest.mark.parametrize("output_format", ["markdown", "json"])
def test_route_cli_unreadable_triad_without_checkpoint_keeps_error(tmp_path, damage, output_format):
    slug = "2026-09-13_update-pkg"
    pkg = _create_package(tmp_path / ".spec" / "specs", slug)
    path = pkg / "tasks.md"
    path.unlink()
    if damage == "invalid-utf8":
        path.write_bytes(b"\xff")
    elif damage == "directory":
        path.mkdir()
    before = {p.name: p.read_bytes() if p.is_file() else None for p in pkg.iterdir()}

    result = _run_route(tmp_path, slug, output_format)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error:")
    assert "Traceback" not in result.stderr
    assert "update checkpoint" not in result.stderr
    assert {p.name: p.read_bytes() if p.is_file() else None for p in pkg.iterdir()} == before


@pytest.mark.parametrize("state", ["active", "corrupted"])
@pytest.mark.parametrize("output_format", ["markdown", "json"])
@pytest.mark.parametrize("link_kind", ["package-external", "package-internal", "triad-external"])
def test_route_checkpoint_does_not_bypass_path_validation(tmp_path, state, output_format, link_kind):
    root = tmp_path / "project"
    specs = root / ".spec" / "specs"
    slug = "2026-09-13_update-pkg"
    pkg = _create_package(specs, slug)
    begin_update_checkpoint(pkg, "追加任务")
    if state == "corrupted":
        checkpoint_path(pkg).write_text("{ not json", encoding="utf-8")
    if link_kind.startswith("package-"):
        target_root = root / "storage" if link_kind == "package-internal" else tmp_path / "outside"
        target_root.mkdir(parents=True)
        target = target_root / slug
        pkg.rename(target)
        pkg.symlink_to(target, target_is_directory=True)
        # Recovery must not mask an unsafe directory even with missing tasks.
        (target / "tasks.md").unlink()
    else:
        target = tmp_path / "outside"
        target.mkdir()
        (target / "tasks.md").write_text("external task data", encoding="utf-8")
        (pkg / "tasks.md").unlink()
        (pkg / "tasks.md").symlink_to(target / "tasks.md")
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    checkpoint_before = checkpoint_path(pkg).read_bytes()

    result = _run_route(root, slug, output_format)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error:")
    assert "Traceback" not in result.stderr
    assert "must resolve under specs root" in result.stderr or "must not be a symlink" in result.stderr
    assert {p.name: p.read_bytes() for p in target.iterdir()} == before
    assert checkpoint_path(pkg).read_bytes() == checkpoint_before


@pytest.mark.parametrize("state", ["active", "corrupted"])
def test_route_checks_checkpoint_before_permission_denied_triad(tmp_path, monkeypatch, state):
    import route_spec_package as route_module

    slug = "2026-09-13_update-pkg"
    pkg = _create_package(tmp_path / ".spec" / "specs", slug)
    begin_update_checkpoint(pkg, "追加任务")
    if state == "corrupted":
        checkpoint_path(pkg).write_text("{ not json", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in pkg.iterdir()}
    calls = []
    detect = route_module.detect_update_checkpoint

    def detect_first(package):
        calls.append("checkpoint")
        return detect(package)

    def deny_read(path):
        assert calls == ["checkpoint"]
        calls.append("triad")
        raise PermissionError(f"permission denied: {path}")

    def forbid_gate(*args, **kwargs):
        pytest.fail("unreadable triad must not be evaluated as task progress")

    monkeypatch.setattr(route_module, "detect_update_checkpoint", detect_first)
    monkeypatch.setattr(route_module, "read_regular_text", deny_read)
    monkeypatch.setattr(route_module, "compute_gate_results", forbid_gate)

    payload = json.loads(route_for_package(tmp_path, slug, output_format="json"))

    assert calls == ["checkpoint", "triad"]
    assert payload["stage"] == "status"
    assert payload["health"] == "阻塞"
    assert payload["updateCheckpoint"] == state
    assert payload["completed"] is None and payload["total"] is None
    assert "rollback" in payload["nextStep"] and "complete" in payload["nextStep"]
    assert {p.name: p.read_bytes() for p in pkg.iterdir()} == before


@pytest.mark.parametrize("damage", ["deep-json", "intent-surrogate", "snapshot-surrogate"])
@pytest.mark.parametrize("readable_triad", [True, False])
def test_route_malformed_checkpoint_keeps_corrupted_recovery(tmp_path, damage, readable_triad):
    slug = "2026-09-13_update-pkg"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="**验收结果**：待修复\n",
    )
    begin_update_checkpoint(pkg, "追加任务")
    path = checkpoint_path(pkg)
    if damage == "deep-json":
        path.write_text("[" * 1100 + "0" + "]" * 1100, encoding="utf-8")
    else:
        document = json.loads(path.read_text(encoding="utf-8"))
        if damage == "intent-surrogate":
            document["intent"] = "\ud800"
        else:
            document["snapshot"]["tasks.md"] = "\ud800"
        path.write_text(json.dumps(document, ensure_ascii=True), encoding="utf-8")
    if not readable_triad:
        (pkg / "tasks.md").unlink()
    before = {p.name: p.read_bytes() for p in pkg.iterdir()}

    output = route_for_package(tmp_path, slug)
    assert "检查点记录损坏，需人工恢复" in output
    assert "rollback" in output and "complete" in output
    state = _route_state(tmp_path, slug)
    assert state["updateCheckpoint"] == "corrupted"
    assert state["stage"] == "status"
    assert state["health"] == "阻塞"
    for output_format in ("markdown", "json"):
        result = _run_route(tmp_path, slug, output_format)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert "Traceback" not in result.stdout
        if output_format == "json":
            assert json.loads(result.stdout) == state
        else:
            assert "检查点记录损坏，需人工恢复" in result.stdout
            assert "rollback" in result.stdout and "complete" in result.stdout
        assert {p.name: p.read_bytes() for p in pkg.iterdir()} == before


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


def test_route_invalid_54_blocks_run_before_execution(tmp_path):
    slug = "2026-09-21_fix-orchestration-lifecycle"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=ORCHESTRATION_BLOCK_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="**验收结果**：待修复\n",
    )
    state = _route_state(tmp_path, slug)
    assert state["stage"] != "run", "invalid 5.4 route must not stay executable"
    assert state["health"] in ("风险", "阻塞")
    assert any("编排" in gap or "route" in gap for gap in state.get("orchestrationErrors", []))
    markdown = route_for_package(tmp_path, slug)
    assert "编排策略尚未满足" in markdown


def test_route_and_report_agree_on_invalid_54_with_completed_tasks(tmp_path):
    """All tasks done + invalid 5.4: route blocks, and the status views must
    not advertise done/archiving for the same package state."""
    slug = "2026-09-21_fix-orchestration-lifecycle-done"
    tasks_text = "## 实现\n- [x] Task A\n  - boundary: x\n  - verify: x\n"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=ORCHESTRATION_BLOCK_SPEC,
        tasks_text=tasks_text,
        checklist_text=COMPLETE_CHECKLIST,
    )
    state = _route_state(tmp_path, slug)
    assert state["stage"] == "status"
    assert state["health"] == "风险"
    assert state["orchestrationErrors"]

    sections, tasks = parse_tasks(tasks_text)
    summary = package_status_summary(slug, tasks_text, ORCHESTRATION_BLOCK_SPEC, COMPLETE_CHECKLIST, sections, tasks)
    assert summary["state"] == "风险", "summary health must agree with route health"
    assert summary["orchestration"] != "无"
    assert summary["gate_failed"] == "是"

    report = render_status(
        slug,
        "Test",
        tasks_text,
        ORCHESTRATION_BLOCK_SPEC,
        COMPLETE_CHECKLIST,
        sections,
        tasks,
    )
    assert "编排策略尚未满足" in report
    assert "整理交付结果并完成归档" not in report

    overview = render_multi_status(tmp_path, tmp_path / ".spec", [slug])
    assert "编排策略尚未满足" in overview


def test_route_valid_54_still_runs_and_absent_54_still_runs(tmp_path):
    slug_valid = "2026-09-21_fix-orchestration-lifecycle-ok"
    _create_package(
        tmp_path / ".spec" / "specs",
        slug_valid,
        spec_text=ORCHESTRATION_VALID_SPEC,
        tasks_text="- [ ] Task A\n  - boundary: x\n  - verify: x\n",
        checklist_text="**验收结果**：待修复\n",
    )
    assert _route_state(tmp_path, slug_valid)["stage"] == "run"

    slug_absent = "2026-09-21_fix-orchestration-lifecycle-absent"
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


@pytest.mark.parametrize("checkpoint_state", ["active", "corrupted"])
def test_report_overview_projects_update_checkpoint_as_blocked(tmp_path, checkpoint_state):
    slug = f"2026-09-22_fix-status-checkpoint-{checkpoint_state}"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text=ONE_COMPLETED_TASK,
        checklist_text=COMPLETE_CHECKLIST,
    )
    begin_update_checkpoint(pkg, "修复状态投影")
    if checkpoint_state == "corrupted":
        checkpoint_path(pkg).write_text("{ bad json", encoding="utf-8")

    route_state = _route_state(tmp_path, slug)
    assert route_state["health"] == "阻塞"
    proc = subprocess.run(
        [sys.executable, str(REPORT_SCRIPT), "--root", str(tmp_path), "--view", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "update checkpoint" in proc.stdout
    assert "阻塞" in proc.stdout
    assert "继续推进当前项目任务" not in proc.stdout
    if checkpoint_state == "corrupted":
        assert "需人工恢复" in proc.stdout.split("## 接下来")[-1]
        assert "rollback" not in proc.stdout.split("## 接下来")[-1]
    else:
        assert "rollback" in proc.stdout.split("## 接下来")[-1]


@pytest.mark.parametrize("checkpoint_state", ["active", "corrupted"])
@pytest.mark.parametrize("missing_member", ["spec.md", "tasks.md", "checklist.md"])
def test_report_overview_discovers_incomplete_checkpoint_package(tmp_path, checkpoint_state, missing_member):
    slug = f"2026-09-22_fix-status-discovery-{checkpoint_state}-{missing_member[:-3]}"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text=ONE_COMPLETED_TASK,
        checklist_text=COMPLETE_CHECKLIST,
    )
    begin_update_checkpoint(pkg, "修复状态投影")
    if checkpoint_state == "corrupted":
        checkpoint_path(pkg).write_text("{ bad json", encoding="utf-8")
    (pkg / missing_member).unlink()

    proc = subprocess.run(
        [sys.executable, str(REPORT_SCRIPT), "--root", str(tmp_path), "--view", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "当前没有进行中的项目" not in proc.stdout
    assert "update checkpoint" in proc.stdout
    assert "未知" in proc.stdout
    assert "继续推进当前项目任务" not in proc.stdout


@pytest.mark.parametrize("checkpoint_state", ["active", "corrupted"])
def test_report_single_status_projects_readable_checkpoint_as_blocked(tmp_path, checkpoint_state):
    slug = f"2026-09-22_fix-status-single-checkpoint-{checkpoint_state}"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text=ONE_COMPLETED_TASK,
        checklist_text=COMPLETE_CHECKLIST,
    )
    begin_update_checkpoint(pkg, "修复状态投影")
    if checkpoint_state == "corrupted":
        checkpoint_path(pkg).write_text("{ bad json", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--root",
            str(tmp_path),
            "--slug",
            slug,
            "--view",
            "status",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "## 需要关注" in proc.stdout
    assert "存在未完成 update checkpoint" in proc.stdout
    assert "整理交付结果并完成归档" not in proc.stdout
    next_step = proc.stdout.split("## 接下来")[-1]
    if checkpoint_state == "corrupted":
        assert "需人工恢复" in next_step
        assert "rollback" not in next_step
    else:
        assert "rollback" in next_step


@pytest.mark.parametrize("checkpoint_state", ["active", "corrupted"])
def test_report_single_status_recovers_when_checkpoint_triad_is_unreadable(tmp_path, checkpoint_state):
    slug = f"2026-09-22_fix-status-unreadable-{checkpoint_state}"
    pkg = _create_package(
        tmp_path / ".spec" / "specs",
        slug,
        spec_text=COMPLETE_SPEC,
        tasks_text=ONE_COMPLETED_TASK,
        checklist_text=COMPLETE_CHECKLIST,
    )
    begin_update_checkpoint(pkg, "修复状态投影")
    if checkpoint_state == "corrupted":
        checkpoint_path(pkg).write_text("{ bad json", encoding="utf-8")
    (pkg / "tasks.md").unlink()

    proc = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--root",
            str(tmp_path),
            "--slug",
            slug,
            "--view",
            "status",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "update checkpoint" in proc.stdout
    assert "未知" in proc.stdout
    assert "整理交付结果并完成归档" not in proc.stdout


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
    assert "需要关注" in proc.stdout
    assert "读取失败" in proc.stdout or "尚未拆出可执行任务" in proc.stdout
    assert "继续推进当前项目任务" not in proc.stdout


def test_report_summary_projects_completed_package_with_missing_evidence_as_risk(tmp_path):
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
    assert route_state["stage"] == "check"
    assert route_state["health"] == "风险"
    assert summary["state"] == "风险"
    assert summary["needs_work"] == "是"
    assert summary["gate_failed"] == "是"

    overview = render_multi_status(tmp_path, tmp_path / ".spec", [slug])
    assert "验收" in overview
    assert "继续推进当前项目任务" not in overview


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
    spec_text = COMPLETE_SPEC.replace("- **目标用户**：用户\n", "")
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
    assert "补齐项目定义" in route_output.split("## 接下来")[-1]
    assert "补齐项目定义" in report_output.split("## 接下来")[-1]
    assert "修正任务依赖" not in report_output.split("## 接下来")[-1]


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
    assert "task-ghost" in route_output.split("## 接下来")[-1]
    assert "修正项目任务依赖" in overview.split("## 接下来")[-1]
    assert "修正项目编排策略" not in overview.split("## 接下来")[-1]

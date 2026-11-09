import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_spec_package import has_blocked_tasks
from report_spec_package import (
    Task,
    package_status_summary,
    parse_tasks,
    render_multi_status,
    render_status,
    render_tasks,
)

# -- Task dataclass --


@pytest.mark.parametrize("marker", ["x", "X"])
@pytest.mark.parametrize("text", ["done", "修复阻塞与状态显示"])
def test_task_completed(marker, text):
    content = f"## S\n- [{marker}] {text}\n"
    assert not has_blocked_tasks(content)
    _, tasks = parse_tasks(content)
    for t in (Task(section="S", text=text, indent=0, marker=marker), tasks[0]):
        assert t.is_completed
        assert not t.is_blocked
        assert not t.is_pending
        assert not t.is_explicit_in_progress


@pytest.mark.parametrize("text", ["blocked", "阻塞：waiting"])
def test_task_blocked(text):
    t = Task(section="S", text=text, indent=0, marker="!")
    assert not t.is_completed
    assert t.is_blocked
    assert not t.is_pending


def test_task_in_progress():
    t = Task(section="S", text="wip", indent=0, marker=">")
    assert not t.is_completed
    assert not t.is_blocked
    assert not t.is_pending
    assert t.is_explicit_in_progress


def test_task_pending():
    t = Task(section="S", text="todo", indent=0, marker=" ")
    assert not t.is_completed
    assert not t.is_blocked
    assert t.is_pending


@pytest.mark.parametrize("marker", [" ", ">"])
def test_task_blocked_by_text(marker):
    """Display keeps the anchored title tag for task rows; the gate reads markers only."""
    t = Task(section="S", text="阻塞：waiting", indent=0, marker=marker)
    assert not t.is_completed
    assert t.is_blocked
    assert not t.is_pending
    # AUD-10: the gate no longer reads titles (structured markers only);
    # only the status/task-view row tagging keeps the heuristic.
    assert not has_blocked_tasks(f"- [{marker}] {t.text}")


def test_status_reports_unchecked_checklist_as_risk_and_check_next_step():
    tasks = [Task(section="Phase", text="done", indent=0, marker="x")]
    output = render_status(
        "2026-08-23_fix-status",
        "Status",
        "## Phase\n- [x] done\n",
        """# Status - 项目范围
## 1. 问题定义
- **项目目标**：goal
- **目标用户**：user
- **核心价值**：value
## 2. 假设与待确认
### 2.1 已确认事实
- fact
### 2.2 关键假设
- assumption
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- exclusion: everything outside this fixture's boundary

## 4. 最小实现路径
- one
- two
- three
### 6.1 行为成效指标
- metric 1 -> verify: test
- metric 2 -> verify: test
- metric 3 -> verify: test
""",
        "## Phase\n- [ ] still unchecked\n**验收结果**：通过\n",
        ["Phase"],
        tasks,
    )
    assert output.startswith("# Status\n")
    assert "- Completed: 1/1" in output
    assert "done: done" in output
    assert "## Next" in output
    assert "Fix failed acceptance checks" in output
    assert "Gate" not in output
    assert "/spec:" not in output


# -- Orchestration strategy projection: /spec:status must agree with route/check --

VALID_STATUS_SPEC = """\
# Status - 项目范围
## 1. 问题定义
- **项目目标**：goal
- **目标用户**：user
- **核心价值**：value
## 2. 假设与待确认
### 2.1 已确认事实
- fact
### 2.2 关键假设
- assumption
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- exclusion: everything outside this fixture's boundary
## 4. 最小实现路径
- one
- two
- three
"""


def _status_spec_with_54(route_line: str) -> str:
    return VALID_STATUS_SPEC + f"## 5. 技术决策\n### 5.4 编排策略\n- {route_line}\n- ownership: 主线程\n"


PASSED_CHECKLIST = """\
## 假设与范围对齐
- [x] 无阻塞性待确认项
- [x] 范围内/范围外与实现一致
- [x] 本轮发现的可执行问题已回写任务包并完成，未甩给用户
## 简洁性
- [x] 没有未请求的扩展、抽象或配置化
- [x] 当前方案保持最小可行
## 变更边界
- [x] 每项改动都能追溯到明确任务
- [x] 没有无关重构或顺手清理
## 功能完整性
- [x] MVP 功能全部实现
- [x] 边界条件已处理
- [x] 错误处理符合预期
## 测试与验证
- [x] 核心逻辑有测试或等价证据
- [x] 关键路径已验证
## 文档同步
- [x] 受影响文档已更新，或记录适用外
- [x] `spec.md` / `tasks.md` / `checklist.md` 已同步
## 部署验证（如适用）
- [x] 本地运行正常，或已记录适用外理由
- [x] 构建成功，或已记录适用外理由
## 验收证据
- 外部对标：对标包
- 脚本验证：pytest 全绿
- 旧新对比：一致
- 差异边界：只改 x
- 行为成效：门禁通过
- 构建：适用外
- 测试：pytest 1 passed
- 手工验证：适用外

---

**验收结果**：通过
"""


def test_status_illegal_orchestration_route_is_advisory_not_a_gate():
    tasks = [Task(section="Phase", text="done", indent=0, marker="x", boundary="x", verify="x")]
    output = render_status(
        "2026-09-21_fix-public-example-09",
        "Status",
        "## Phase\n- [x] done\n  - boundary: x\n  - verify: x\n",
        _status_spec_with_54("route: build + review"),
        PASSED_CHECKLIST,
        ["Phase"],
        tasks,
    )
    # Routing hints are advisory: delivery state never blocks on 5.4.
    assert "Orchestration requirements not met" not in output
    assert "Prepare delivery and archive the record" in output


def test_status_legal_orchestration_route_stays_quiet():
    tasks = [Task(section="Phase", text="done", indent=0, marker="x", boundary="x", verify="x")]
    output = render_status(
        "2026-09-21_fix-public-example-09-ok",
        "Status",
        "## Phase\n- [x] done\n  - boundary: x\n  - verify: x\n",
        _status_spec_with_54("route: build"),
        PASSED_CHECKLIST,
        ["Phase"],
        tasks,
    )
    assert "Orchestration requirements not met" not in output
    assert "Prepare delivery and archive the record" in output


def test_status_absent_54_section_stays_quiet():
    tasks = [Task(section="Phase", text="done", indent=0, marker="x", boundary="x", verify="x")]
    output = render_status(
        "2026-09-21_fix-public-example-09-none",
        "Status",
        "## Phase\n- [x] done\n  - boundary: x\n  - verify: x\n",
        VALID_STATUS_SPEC,
        PASSED_CHECKLIST,
        ["Phase"],
        tasks,
    )
    assert "Orchestration requirements not met" not in output
    assert "Prepare delivery and archive the record" in output


# -- report must agree with route on every gate fact it already computes --

HEALTHY_EVIDENCE_CHECKLIST = """\
## 假设与范围对齐
- [x] 无阻塞性待确认项
- [x] 范围内/范围外与实现一致
- [x] 本轮发现的可执行问题已回写任务包并完成，未甩给用户
## 简洁性
- [x] 没有未请求的扩展、抽象或配置化
- [x] 当前方案保持最小可行
## 变更边界
- [x] 每项改动都能追溯到明确任务
- [x] 没有无关重构或顺手清理
## 功能完整性
- [x] MVP 功能全部实现
- [x] 边界条件已处理
- [x] 错误处理符合预期
## 测试与验证
- [x] 核心逻辑有测试或等价证据
- [x] 关键路径已验证
## 文档同步
- [x] 受影响文档已更新，或记录适用外
- [x] `spec.md` / `tasks.md` / `checklist.md` 已同步
## 部署验证（如适用）
- [x] 本地运行正常，或已记录适用外理由
- [x] 构建成功，或已记录适用外理由
## 验收证据
- 外部对标：对标包
- 脚本验证：pytest 全绿
- 旧新对比：一致
- 差异边界：只改 x
- 行为成效：门禁通过

---

**验收结果**：通过
"""

NO_EVIDENCE_CHECKLIST = "# c\n- [x] item\n\n**验收结果**：通过\n"


def test_status_next_step_prefers_definition_gaps_over_advisory_routing():
    """Routing hints are advisory: an illegal 5.4 route never becomes the
    next step while definition gaps remain."""
    tasks = [Task(section="P", text="todo", indent=0, marker=" ", boundary="x", verify="x")]
    incomplete_spec = _status_spec_with_54("route: build + review").replace("- **项目目标**：goal\n", "")
    output = render_status(
        "2026-09-21_fix-public-example-09-prio",
        "Status",
        "## P\n- [ ] todo\n  - boundary: x\n  - verify: x\n",
        incomplete_spec,
        "**验收结果**：待修复\n",
        ["P"],
        tasks,
    )
    assert "Orchestration requirements not met" not in output
    assert output.count("Project definition incomplete") >= 1
    assert "Fix the orchestration strategy" not in output.split("## Next")[-1]
    assert "Complete the project definition" in output


def test_status_projects_dependency_errors():
    tasks = [Task(section="P", text="done", indent=0, marker="x", boundary="x", verify="x")]
    output = render_status(
        "2026-09-21_fix-public-example-09-dep",
        "Status",
        "## P\n- [x] done\n  - depends-on: task-ghost\n  - boundary: x\n  - verify: x\n",
        VALID_STATUS_SPEC,
        HEALTHY_EVIDENCE_CHECKLIST,
        ["P"],
        tasks,
    )
    assert "task-ghost" in output
    assert "Prepare delivery and archive the record" not in output


def test_status_projects_standalone_blocked_line():
    tasks = [Task(section="P", text="done", indent=0, marker="x", boundary="x", verify="x")]
    output = render_status(
        "2026-09-21_fix-public-example-09-blocked",
        "Status",
        "## P\n- [x] done\n\n阻塞：等待外部系统恢复\n",
        VALID_STATUS_SPEC,
        HEALTHY_EVIDENCE_CHECKLIST,
        ["P"],
        tasks,
    )
    assert "## Attention" in output
    assert "Task blocked: 阻塞：等待外部系统恢复" in output.split("## Attention")[-1]
    assert "Prepare delivery and archive the record" not in output


def test_status_projects_missing_evidence_gate():
    tasks = [Task(section="P", text="done", indent=0, marker="x", boundary="x", verify="x")]
    output = render_status(
        "2026-09-21_fix-public-example-09-ev",
        "Status",
        "## P\n- [x] done\n",
        VALID_STATUS_SPEC,
        NO_EVIDENCE_CHECKLIST,
        ["P"],
        tasks,
    )
    assert "Prepare delivery and archive the record" not in output
    assert "Fix failed acceptance checks" in output


def test_tasks_view_preserves_explicit_parallel_markers_and_blocked_precedence():
    content = """\
## 实现
- [ ] 待做的前项
  - boundary: a
  - verify: a
- [>] 并行甲
  - boundary: b
  - verify: b
- [!] 等待系统
  - boundary: c
  - verify: c
- [>] 并行乙
  - boundary: d
  - verify: d
- [>] 阻塞：接口不可用
  - boundary: e
  - verify: e
- [x] 阻塞修复完成
  - boundary: f
  - verify: f
"""
    sections, tasks = parse_tasks(content)
    text = render_tasks("project", sections, tasks)
    rows = [
        "- [ ] 待做的前项",
        "- [>] 并行甲",
        "- [!] 等待系统",
        "- [>] 并行乙",
        "- [!] 阻塞：接口不可用",
        "- [x] 阻塞修复完成",
    ]
    assert all(row in text for row in rows)
    assert [text.index(row) for row in rows] == sorted(text.index(row) for row in rows)
    summary = package_status_summary(
        "2026-06-12_dash-run", content, VALID_STATUS_SPEC, PASSED_CHECKLIST, sections, tasks
    )
    assert summary["primary"] == "并行甲, 并行乙"
    assert summary["blocked"] == "等待系统"


def test_tasks_view_and_summary_do_not_promote_a_pending_task():
    content = "## 实现\n- [ ] 待做甲\n  - boundary: a\n  - verify: a\n- [ ] 待做乙\n  - boundary: b\n  - verify: b\n"
    sections, tasks = parse_tasks(content)
    output = render_tasks("project", sections, tasks)
    assert "- [ ] 待做甲" in output
    assert "- [ ] 待做乙" in output
    assert "[>]" not in output
    summary = package_status_summary(
        "2026-06-12_dash-run", content, VALID_STATUS_SPEC, PASSED_CHECKLIST, sections, tasks
    )
    assert summary["primary"] == ""


def test_multi_status_reports_all_active_tasks_without_promoting_pending(tmp_path):
    specs_root = tmp_path / ".spec"
    projects = [
        ("2026-06-12_first", "First", "- [ ] 待做甲\n- [>] 并行甲\n- [>] 并行乙\n"),
        ("2026-06-12_second", "Second", "- [ ] 待做乙\n- [>] 并行丙\n"),
        ("2026-06-12_third", "Third", "- [ ] 待做丙\n"),
    ]
    for slug, title, task_lines in projects:
        package = specs_root / "specs" / slug
        package.mkdir(parents=True)
        (package / "spec.md").write_text(VALID_STATUS_SPEC.replace("# Status", f"# {title}"), encoding="utf-8")
        (package / "tasks.md").write_text("## 实现\n" + task_lines, encoding="utf-8")
        (package / "checklist.md").write_text(PASSED_CHECKLIST, encoding="utf-8")
    text = render_multi_status(tmp_path, specs_root, [slug for slug, _, _ in projects])
    current = text.split("## In Progress\n", 1)[1].split("\n## ", 1)[0]
    assert "- First: 并行甲, 并行乙" in current
    assert "- Second: 并行丙" in current
    assert "待做" not in current
    assert "First: completed 0/3; current: 并行甲, 并行乙" in text
    assert "Second: completed 0/2; current: 并行丙" in text
    third_row = next(line for line in text.splitlines() if line.startswith("- Third:"))
    assert "current:" not in third_row


def test_multi_status_preserves_punctuation_inside_task_titles(tmp_path):
    specs_root = tmp_path / ".spec"
    package = specs_root / "specs" / "2026-06-12_first"
    package.mkdir(parents=True)
    (package / "spec.md").write_text(VALID_STATUS_SPEC.replace("# Status", "# First"), encoding="utf-8")
    (package / "tasks.md").write_text("## 实现\n- [>] 接口、缓存联调\n- [>] 并行乙\n", encoding="utf-8")
    (package / "checklist.md").write_text(PASSED_CHECKLIST, encoding="utf-8")
    text = render_multi_status(tmp_path, specs_root, ["2026-06-12_first"])
    current = text.split("## In Progress\n", 1)[1].split("\n## ", 1)[0]
    assert "- First: 接口、缓存联调, 并行乙" in current
    assert "First: completed 0/2; current: 接口、缓存联调, 并行乙" in text

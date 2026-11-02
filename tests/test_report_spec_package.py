import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import report_spec_package as report
from check_spec_package import has_blocked_tasks, package_physical_state_errors
from report_spec_package import (
    Task,
    package_status_summary,
    parse_tasks,
    render_multi_status,
    render_status,
    render_tasks,
)
from update_checkpoint_support import detect_update_checkpoint

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
    """Display keeps the title tag for task rows; the gate reads markers only."""
    t = Task(section="S", text="task 阻塞：waiting", indent=0, marker=marker)
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
    assert "- 已完成：1/1" in output
    assert "done：完成" in output
    assert "## 接下来" in output
    assert "完成剩余验收项" in output
    assert "门禁" not in output
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


def test_status_projects_illegal_orchestration_route_as_risk_not_done():
    tasks = [Task(section="Phase", text="done", indent=0, marker="x")]
    output = render_status(
        "2026-09-21_fix-orchestration-lifecycle",
        "Status",
        "## Phase\n- [x] done\n",
        _status_spec_with_54("route: build + review"),
        PASSED_CHECKLIST,
        ["Phase"],
        tasks,
    )
    assert "编排策略尚未满足" in output, "illegal 5.4 route must surface in the status view"
    assert "整理交付结果并完成归档" not in output, "must not advertise archiving while strategy is invalid"


def test_status_legal_orchestration_route_stays_quiet():
    tasks = [Task(section="Phase", text="done", indent=0, marker="x", boundary="x", verify="x")]
    output = render_status(
        "2026-09-21_fix-orchestration-lifecycle-ok",
        "Status",
        "## Phase\n- [x] done\n  - boundary: x\n  - verify: x\n",
        _status_spec_with_54("route: build"),
        PASSED_CHECKLIST,
        ["Phase"],
        tasks,
    )
    assert "编排策略尚未满足" not in output
    assert "整理交付结果并完成归档" in output


def test_status_absent_54_section_stays_quiet():
    tasks = [Task(section="Phase", text="done", indent=0, marker="x", boundary="x", verify="x")]
    output = render_status(
        "2026-09-21_fix-orchestration-lifecycle-none",
        "Status",
        "## Phase\n- [x] done\n  - boundary: x\n  - verify: x\n",
        VALID_STATUS_SPEC,
        PASSED_CHECKLIST,
        ["Phase"],
        tasks,
    )
    assert "编排策略尚未满足" not in output
    assert "整理交付结果并完成归档" in output


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


def test_status_next_step_respects_gate_priority_before_orchestration():
    """Orchestration is the LAST priority (route ordering): with definition
    gaps open, status must say 补齐项目定义, not 修正编排策略."""
    tasks = [Task(section="P", text="todo", indent=0, marker=" ", boundary="x", verify="x")]
    incomplete_spec = _status_spec_with_54("route: build + review").replace("- **目标用户**：user\n", "")
    output = render_status(
        "2026-09-21_fix-orchestration-lifecycle-prio",
        "Status",
        "## P\n- [ ] todo\n  - boundary: x\n  - verify: x\n",
        incomplete_spec,
        "**验收结果**：待修复\n",
        ["P"],
        tasks,
    )
    assert "编排策略尚未满足" in output
    assert output.count("项目定义不完整") >= 1
    assert "修正编排策略" not in output.split("## 接下来")[-1], "definition gaps outrank orchestration"
    assert "补齐项目定义" in output


def test_status_projects_dependency_errors():
    tasks = [Task(section="P", text="done", indent=0, marker="x", boundary="x", verify="x")]
    output = render_status(
        "2026-09-21_fix-orchestration-lifecycle-dep",
        "Status",
        "## P\n- [x] done\n  - depends-on: task-ghost\n  - boundary: x\n  - verify: x\n",
        VALID_STATUS_SPEC,
        HEALTHY_EVIDENCE_CHECKLIST,
        ["P"],
        tasks,
    )
    assert "task-ghost" in output
    assert "整理交付结果并完成归档" not in output


def test_status_projects_standalone_blocked_line():
    tasks = [Task(section="P", text="done", indent=0, marker="x", boundary="x", verify="x")]
    output = render_status(
        "2026-09-21_fix-orchestration-lifecycle-blocked",
        "Status",
        "## P\n- [x] done\n\n阻塞：等待外部系统恢复\n",
        VALID_STATUS_SPEC,
        HEALTHY_EVIDENCE_CHECKLIST,
        ["P"],
        tasks,
    )
    assert "阻塞" in output.split("## 需要关注")[-1] if "## 需要关注" in output else "阻塞" in output
    assert "整理交付结果并完成归档" not in output


def test_status_projects_missing_evidence_gate():
    tasks = [Task(section="P", text="done", indent=0, marker="x", boundary="x", verify="x")]
    output = render_status(
        "2026-09-21_fix-orchestration-lifecycle-ev",
        "Status",
        "## P\n- [x] done\n",
        VALID_STATUS_SPEC,
        NO_EVIDENCE_CHECKLIST,
        ["P"],
        tasks,
    )
    assert "整理交付结果并完成归档" not in output
    assert "完成剩余验收项" in output or "修复验收未通过项" in output


def test_status_projects_update_checkpoint_block(tmp_path, capsys):
    """Status must surface an interrupted checkpoint and not claim completion."""
    pkg = tmp_path / ".spec" / "specs" / "2026-09-21_fix-orchestration-lifecycle-ck"
    pkg.mkdir(parents=True)
    (pkg / "spec.md").write_text(_status_spec_with_54("route: build"), encoding="utf-8")
    (pkg / "tasks.md").write_text("## P\n- [x] done\n  - boundary: x\n  - verify: x\n", encoding="utf-8")
    (pkg / "checklist.md").write_text("**验收结果**：通过\n", encoding="utf-8")
    (pkg / "update-checkpoint.json").write_text("{not json", encoding="utf-8")

    from update_checkpoint_support import detect_update_checkpoint

    status = detect_update_checkpoint(pkg)
    assert status.interrupted
    sections, tasks = parse_tasks((pkg / "tasks.md").read_text(encoding="utf-8"))
    output = render_status(
        "2026-09-21_fix-orchestration-lifecycle-ck",
        "Status",
        (pkg / "tasks.md").read_text(encoding="utf-8"),
        (pkg / "spec.md").read_text(encoding="utf-8"),
        (pkg / "checklist.md").read_text(encoding="utf-8"),
        sections,
        tasks,
        checkpoint_pending=status.interrupted,
    )
    assert "整理交付结果并完成归档" not in output
    assert "update checkpoint" in output
    assert "## 需要关注" in output
    assert "存在未完成 update checkpoint" in output


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
    assert summary["primary"] == "并行甲、并行乙"
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
    assert summary["primary"] == "无"


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
    current = text.split("## 正在处理\n", 1)[1].split("\n## ", 1)[0]
    assert "- First：并行甲、并行乙" in current
    assert "- Second：并行丙" in current
    assert "待做" not in current
    assert "First：已完成 0/3；当前 并行甲、并行乙" in text
    assert "Second：已完成 0/2；当前 并行丙" in text
    third_row = next(line for line in text.splitlines() if line.startswith("- Third："))
    assert "当前" not in third_row


def _make_checkpoint_package(root, slug, kind, *, readable=True):
    """Only generate synthetic records in pytest's temporary directory."""
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    if readable:
        (package / "spec.md").write_text(VALID_STATUS_SPEC, encoding="utf-8")
        (package / "tasks.md").write_text("## P\n- [x] done\n  - boundary: x\n  - verify: x\n", encoding="utf-8")
        (package / "checklist.md").write_text(PASSED_CHECKLIST, encoding="utf-8")
    payload = {
        "version": 1,
        "package": "another-package" if kind == "mismatch" else slug,
        "intent": "测试更新意图",
        "createdAt": "2026-09-21T12:00:00+08:00",
        "snapshot": {"spec.md": "old spec\n", "tasks.md": "old tasks\n"},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["checksum"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if kind == "bad-checksum":
        payload["snapshot"]["tasks.md"] = "tampered snapshot\n"
    data = "{not json" if kind == "corrupted" else json.dumps(payload, ensure_ascii=False)
    (package / "update-checkpoint.json").write_text(data, encoding="utf-8")
    return package


def _assert_safe_checkpoint_guidance(text):
    steps = [
        "先备份当前文件和 checkpoint",
        "核验记录结构、校验和与包身份",
        "审阅有效 snapshot",
        "可信备份",
        "逐路径检查 Git 历史",
        "可能缺少未提交内容",
        "不恢复文件模式或索引",
        "缺失项不表示应删除",
        "损坏或身份不符的记录不可相信",
        "确认安全后选择保留现状或逐路径安全恢复",
        "无法确认安全状态则保持门禁阻塞",
        "问题解决且 checkpoint（update-checkpoint.json）未变后",
        "才删除 update-checkpoint.json 这一工作树文件并重跑门禁",
        "未经单独授权不得变更索引",
        "references/update-recovery.md",
    ]
    assert all(step in text for step in steps), text
    assert [text.index(step) for step in steps] == sorted(text.index(step) for step in steps)
    assert "按 Git 历史恢复三件套后删除" not in text
    assert "按 Git 历史确认/恢复三件套后删除" not in text


@pytest.mark.parametrize("kind", ["active", "corrupted", "bad-checksum", "mismatch"])
@pytest.mark.parametrize("readable", [True, False])
def test_single_status_guides_historical_checkpoint_recovery_without_writing(
    tmp_path, monkeypatch, capsys, kind, readable
):
    slug = "2026-09-21_checkpoint-report"
    package = _make_checkpoint_package(tmp_path, slug, kind, readable=readable)
    before = {path.name: path.read_bytes() for path in package.iterdir()}
    monkeypatch.setattr(
        sys, "argv", ["report_spec_package.py", "--root", str(tmp_path), "--view", "status", "--slug", slug]
    )
    assert report.main() == 0
    output = capsys.readouterr().out
    _assert_safe_checkpoint_guidance(output)
    if kind in {"corrupted", "bad-checksum"}:
        assert "记录已损坏，不可信任" in output
        assert detect_update_checkpoint(package).state == "corrupted"
    elif kind == "mismatch":
        assert "记录包身份不符，不可信任" in output
        assert "测试更新意图" not in output
    else:
        assert "更新「测试更新意图」未确认完成" in output
    if not readable:
        assert "任务记录缺失或不可读" in output
        assert "状态：阻塞" in output
    assert detect_update_checkpoint(package).interrupted
    assert package_physical_state_errors(package), "unresolved checkpoint must still block the physical-state gate"
    assert {path.name: path.read_bytes() for path in package.iterdir()} == before


def test_checkpoint_next_step_and_unknown_status_require_safe_resolution():
    next_step = report.status_next_step(
        None, [], [], True, PASSED_CHECKLIST, [], checkpoint_pending=True, spec_content=VALID_STATUS_SPEC
    )
    _assert_safe_checkpoint_guidance(next_step)
    _assert_safe_checkpoint_guidance(report.checkpoint_alert(None))


@pytest.mark.parametrize("compact", [False, True])
def test_multi_status_uses_safe_checkpoint_guidance_for_each_kind(tmp_path, compact):
    kinds = [
        ("2026-09-21_active", "active", True),
        ("2026-09-21_corrupted", "corrupted", True),
        ("2026-09-21_mismatch", "mismatch", False),
    ]
    packages = [_make_checkpoint_package(tmp_path, slug, kind, readable=readable) for slug, kind, readable in kinds]
    before = [{path.name: path.read_bytes() for path in package.iterdir()} for package in packages]
    output = render_multi_status(tmp_path, tmp_path / ".spec", [slug for slug, _, _ in kinds], compact=compact)
    assert "3 个项目存在历史遗留 update checkpoint" in output
    assert "记录已损坏，不可信任" in output
    assert "记录包身份不符，不可信任" in output
    assert "任务记录不可读" in output
    _assert_safe_checkpoint_guidance(output)
    assert all(package_physical_state_errors(package) for package in packages)
    assert [{path.name: path.read_bytes() for path in package.iterdir()} for package in packages] == before

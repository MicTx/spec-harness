import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_spec_package import render as render_check
from dashboard_support import (
    ASCII_GLYPHS,
    GLYPH_ACTIVE,
    GLYPH_BLOCKED,
    GLYPH_DONE,
    GLYPH_PENDING,
    HEALTH_OK,
    HEALTH_RISK,
    Dashboard,
    fold_strip,
)
from report_spec_package import parse_tasks, render_status
from route_spec_package import route_for_package

FORBIDDEN_PROCESS_TEXT = ("## 流水线", "## 指标", "## 告警", "## 下一步", "门禁：", "/spec:")

COMPLETE_SPEC = """\
# Test - 项目范围

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

TASKS = """\
## 实现
- [ ] Task A
  - boundary: x
  - verify: x
- [ ] Task B
  - boundary: y
  - verify: y
"""
CHECKLIST = "# C\n**验收结果**：待修复\n"


def _package(specs_dir, slug, spec=COMPLETE_SPEC, tasks=TASKS, checklist=CHECKLIST):
    pkg = specs_dir / slug
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "spec.md").write_text(spec, encoding="utf-8")
    (pkg / "tasks.md").write_text(tasks, encoding="utf-8")
    (pkg / "checklist.md").write_text(checklist, encoding="utf-8")
    return pkg


def test_dashboard_starts_with_project_and_actual_progress():
    dash = Dashboard(
        slug="2026-06-12_add-payment-callback",
        stage="run",
        title="支付回调",
        done=1,
        total=2,
        health=HEALTH_OK,
        next_step="补齐失败重试测试",
    )
    text = dash.render()
    assert text.startswith("# 支付回调\n")
    assert "## 项目进展" in text
    assert "- 已完成：1/2" in text
    assert "## 接下来" in text
    assert "- 补齐失败重试测试" in text
    assert "2026-06-12_add-payment-callback" not in text
    assert " run " not in text
    assert all(value not in text for value in FORBIDDEN_PROCESS_TEXT)


def test_dashboard_empty_sections_are_omitted():
    text = Dashboard(slug="s", stage="run", title="项目").render()
    assert text == "# 项目"
    assert "- 无" not in text
    assert "## 项目进展" not in text
    assert "## 任务" not in text
    assert "## 正在处理" not in text
    assert "## 需要关注" not in text


def test_dashboard_tasks_keep_project_order_and_all_active_items():
    strip = [GLYPH_DONE, GLYPH_ACTIVE, GLYPH_ACTIVE, GLYPH_BLOCKED, GLYPH_PENDING]
    labels = ["固化需求", "实现校验", "补测试", "接入依赖", "写总结"]
    text = Dashboard(slug="s", stage="run", title="项目", strip=strip, strip_labels=labels).render()
    assert "- 固化需求：完成" in text
    assert "- 实现校验：进行" in text
    assert "- 补测试：进行" in text
    assert "- 接入依赖：阻塞" in text
    assert "- 写总结：待办" in text
    task_section = text.split("## 任务", 1)[1].split("## 正在处理", 1)[0]
    assert task_section.count("：进行") == 2
    current_section = text.split("## 正在处理", 1)[1]
    assert "- 实现校验" in current_section
    assert "- 补测试" in current_section
    positions = [text.index(label) for label in labels]
    assert positions == sorted(positions)


def test_dashboard_alerts_only_show_real_attention_items_and_pointer():
    text = Dashboard(
        slug="s",
        stage="check",
        title="项目",
        health=HEALTH_RISK,
        alerts=[f"验收问题 {index}" for index in range(8)],
        alerts_pointer="checklist.md",
    ).render()
    assert "## 需要关注" in text
    assert "验收问题 0" in text
    assert "验收问题 7" not in text
    assert "其余 3 项见 checklist.md" in text
    assert "## 告警" not in text


def test_dashboard_delta_and_delivery_information_follow_project_language():
    text = Dashboard(
        slug="s",
        stage="done",
        title="项目",
        delta=["支付回调已上线", "回归测试已通过"],
        next_step="观察生产指标",
        detail=["完成总结：/tmp/summary.md"],
    ).render()
    assert "## 本轮完成" in text
    assert "- 支付回调已上线" in text
    assert "## 接下来" in text
    assert "- 观察生产指标" in text
    assert "## 交付信息" in text
    assert "完成总结：/tmp/summary.md" in text
    assert text.index("## 本轮完成") < text.index("## 接下来") < text.index("## 交付信息")


def test_dashboard_compact_mode_keeps_project_content_without_headings():
    dash = Dashboard(
        slug="s",
        stage="run",
        title="项目",
        done=1,
        total=2,
        strip=[GLYPH_DONE, GLYPH_PENDING],
        strip_labels=["固化范围", "实现校验"],
        ascii_mode=True,
        compact=True,
    )
    text = dash.render()
    assert text.startswith("项目\n")
    assert "- 已完成：1/2" in text
    assert ASCII_GLYPHS[GLYPH_DONE] in text
    task_line = next(line for line in text.splitlines() if line.startswith("- 任务："))
    assert "固化范围" in task_line
    assert "实现校验" in task_line
    assert "## " not in text
    assert "/spec:" not in text


def test_dashboard_strip_folds_beyond_twenty():
    kinds = [GLYPH_DONE] * 12 + [GLYPH_ACTIVE] + [GLYPH_BLOCKED] + [GLYPH_PENDING] * 7
    assert fold_strip(kinds) == "完成x12 进行 阻塞 待办x7"


def test_scripted_outputs_share_project_progress_contract():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        specs = root / ".spec" / "specs"
        _package(specs, "2026-06-12_dash-run")

        route = route_for_package(root, "2026-06-12_dash-run")
        status = render_status(
            "2026-06-12_dash-run",
            "Test",
            TASKS,
            COMPLETE_SPEC,
            CHECKLIST,
            ["实现"],
            _parse_tasks(TASKS),
        )
        check = render_check("2026-06-12_dash-run", "Test", COMPLETE_SPEC, TASKS, CHECKLIST)

        for output in (route, status, check):
            assert output.startswith("# Test\n")
            assert "## 项目进展" in output
            assert "- 已完成：0/2" in output
            assert "## 任务" in output
            assert "Task A：待办" in output
            assert "Task B：待办" in output
            assert "## 正在处理" not in output
            assert "## 流水线" not in output
            assert "## 指标" not in output
            assert "/spec:" not in output
        assert "## 接下来" in route
        assert "## 接下来" in status
        assert "## 接下来" in check


def _parse_tasks(content):
    from report_spec_package import parse_tasks as _parse

    _sections, tasks = _parse(content)
    return tasks


MIXED_TASKS = """\
## 实现
- [ ] 先排队
  - boundary: a
  - verify: a
- [>] 并行甲
  - boundary: b
  - verify: b
- [!] 等待服务
  - boundary: c
  - verify: c
- [>] 并行乙
  - boundary: d
  - verify: d
- [x] 阻塞修复已完成
  - boundary: e
  - verify: e
- [>] 阻塞：外部接口
  - boundary: f
  - verify: f
"""

NO_ACTIVE_TASKS = """\
## 实现
- [x] 先完成
  - boundary: a
  - verify: a
- [!] 等待服务
  - boundary: b
  - verify: b
- [ ] 之后排队
  - boundary: c
  - verify: c
"""


def _task_area(text, compact):
    if compact:
        return next(line for line in text.splitlines() if line.startswith("- 任务："))
    return text.split("## 任务\n", 1)[1].split("\n## ", 1)[0]


def _current_area(text, compact):
    if compact:
        return [line.removeprefix("- 正在处理：") for line in text.splitlines() if line.startswith("- 正在处理：")]
    if "## 正在处理\n" not in text:
        return []
    section = text.split("## 正在处理\n", 1)[1].split("\n## ", 1)[0]
    return [line[2:] for line in section.splitlines() if line.startswith("- ")]


@pytest.mark.parametrize("ascii_mode", [False, True])
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize(
    ("content", "expected_rows", "expected_current"),
    [
        (
            MIXED_TASKS,
            [("先排队", GLYPH_PENDING), ("并行甲", GLYPH_ACTIVE), ("等待服务", GLYPH_BLOCKED),
             ("并行乙", GLYPH_ACTIVE), ("阻塞修复已完成", GLYPH_DONE), ("阻塞：外部接口", GLYPH_BLOCKED)],
            ["并行甲", "并行乙"],
        ),
        (NO_ACTIVE_TASKS, [("先完成", GLYPH_DONE), ("等待服务", GLYPH_BLOCKED),
                           ("之后排队", GLYPH_PENDING)], []),
        (TASKS, [("Task A", GLYPH_PENDING), ("Task B", GLYPH_PENDING)], []),
        ("## 实现\n- [x] 全部完成\n  - boundary: x\n  - verify: x\n",
         [("全部完成", GLYPH_DONE)], []),
    ],
)
def test_route_status_check_project_the_same_explicit_task_states(
    tmp_path, ascii_mode, compact, content, expected_rows, expected_current
):
    slug = "2026-06-12_dash-run"
    _package(tmp_path / ".spec" / "specs", slug, tasks=content)
    sections, tasks = parse_tasks(content)
    outputs = [
        route_for_package(tmp_path, slug, ascii_mode=ascii_mode, compact=compact),
        render_status(slug, "Test", content, COMPLETE_SPEC, CHECKLIST, sections, tasks,
                      ascii_mode=ascii_mode, compact=compact),
        render_check(slug, "Test", COMPLETE_SPEC, content, CHECKLIST,
                     ascii_mode=ascii_mode, compact=compact),
    ]
    for text in outputs:
        task_area = _task_area(text, compact)
        rows = [f"{name}：{ASCII_GLYPHS[kind] if ascii_mode else {'done': '完成', 'active': '进行', 'blocked': '阻塞', 'pending': '待办'}[kind]}"
                for name, kind in expected_rows]
        assert all(row in task_area for row in rows), text
        assert [task_area.index(row) for row in rows] == sorted(task_area.index(row) for row in rows)
        assert _current_area(text, compact) == expected_current, text


def test_ready_json_candidate_does_not_imply_human_active_state(tmp_path):
    slug = "2026-06-12_dash-run"
    _package(tmp_path / ".spec" / "specs", slug)
    payload = json.loads(route_for_package(tmp_path, slug, output_format="json"))
    text = route_for_package(tmp_path, slug)
    assert payload["currentTask"] == "Task A"  # legacy field means next ready candidate
    assert "Task A：待办" in text
    assert "## 正在处理" not in text
    assert "## 接下来\n\n- Task A" in text


def test_dashboard_current_fallback_and_strip_precedence():
    text = Dashboard(slug="s", stage="run", current="实际工作", next_step="实际工作").render()
    assert "## 正在处理\n\n- 实际工作" in text
    assert "## 接下来" not in text
    text = Dashboard(slug="s", stage="check", strip=[GLYPH_PENDING, GLYPH_BLOCKED],
                     strip_labels=["排队", "受阻"], current="排队", next_step="排队").render()
    assert "## 正在处理" not in text
    assert "## 接下来\n\n- 排队" in text
    text = Dashboard(slug="s", stage="run", strip=[GLYPH_ACTIVE, GLYPH_ACTIVE],
                     strip_labels=["甲", "乙"], next_step="乙").render()
    assert _current_area(text, False) == ["甲", "乙"]
    assert "## 接下来" not in text


def test_dashboard_preserves_active_tasks_beyond_the_task_list_limit():
    labels = [f"任务 {i:02}" for i in range(23)]
    text = Dashboard(slug="s", stage="run", strip=[GLYPH_ACTIVE] * len(labels), strip_labels=labels).render()
    assert "其余 3 项见任务包" in text
    assert _current_area(text, False) == labels


def test_goal_output_stops_locally_and_does_not_default_to_a_push_command():
    contract = (Path(__file__).resolve().parent.parent / "references" / "output-contracts.md").read_text(encoding="utf-8")
    goal = contract.split("## `/spec:goal`", 1)[1].split("\n## ", 1)[0]
    assert "stops at the local commit by default" in goal
    assert "local commit outcome" in goal
    assert "Never push without explicit user authorization" in goal
    assert "only after an authorized push has actually completed" in goal
    assert "Give a push command only when the user explicitly requests one, not by default" in goal

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

FORBIDDEN_PROCESS_TEXT = ("## Pipeline", "## Metrics", "## Alerts", "## Next Step", "Gate:", "/spec:")

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


def test_dashboard_accepts_legacy_and_english_metric_keys():
    for metrics in (
        {"阻塞": "1", "待确认": "2", "范围": "project"},
        {"Blocked": "1", "Pending decisions": "2", "Scope": "project"},
    ):
        text = Dashboard(slug="s", stage="check", metrics=metrics).render()
        assert "## Verification Scope\n\n- Level: project" in text
        assert "- Pending decisions: 2" in text
        assert "- Blocked: 1" in text


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
    assert "## Project Progress" in text
    assert "- Completed: 1/2" in text
    assert "## Next" in text
    assert "- 补齐失败重试测试" in text
    assert "2026-06-12_add-payment-callback" not in text
    assert " run " not in text
    assert all(value not in text for value in FORBIDDEN_PROCESS_TEXT)


def test_dashboard_empty_sections_are_omitted():
    text = Dashboard(slug="s", stage="run", title="项目").render()
    assert text == "# 项目"
    assert "- 无" not in text
    assert "## Project Progress" not in text
    assert "## Tasks" not in text
    assert "## In Progress" not in text
    assert "## Attention" not in text


def test_dashboard_tasks_keep_project_order_and_all_active_items():
    strip = [GLYPH_DONE, GLYPH_ACTIVE, GLYPH_ACTIVE, GLYPH_BLOCKED, GLYPH_PENDING]
    labels = ["固化需求", "实现校验", "补测试", "接入依赖", "写总结"]
    text = Dashboard(slug="s", stage="run", title="项目", strip=strip, strip_labels=labels).render()
    assert "- 固化需求: done" in text
    assert "- 实现校验: in progress" in text
    assert "- 补测试: in progress" in text
    assert "- 接入依赖: blocked" in text
    assert "- 写总结: todo" in text
    task_section = text.split("## Tasks", 1)[1].split("## In Progress", 1)[0]
    assert task_section.count(": in progress") == 2
    current_section = text.split("## In Progress", 1)[1]
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
    assert "## Attention" in text
    assert "验收问题 0" in text
    assert "验收问题 7" not in text
    assert "See checklist.md for 3 more items" in text
    assert "## Alerts" not in text


def test_dashboard_delta_and_delivery_information_follow_project_language():
    text = Dashboard(
        slug="s",
        stage="done",
        title="项目",
        delta=["支付回调已上线", "回归测试已通过"],
        next_step="观察生产指标",
        detail=["完成总结：/tmp/summary.md"],
    ).render()
    assert "## Completed This Round" in text
    assert "- 支付回调已上线" in text
    assert "## Next" in text
    assert "- 观察生产指标" in text
    assert "## Delivery Details" in text
    assert "完成总结：/tmp/summary.md" in text
    assert text.index("## Completed This Round") < text.index("## Next") < text.index("## Delivery Details")


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
    assert "- Completed: 1/2" in text
    assert ASCII_GLYPHS[GLYPH_DONE] in text
    task_line = next(line for line in text.splitlines() if line.startswith("- Tasks: "))
    assert "固化范围" in task_line
    assert "实现校验" in task_line
    assert "## " not in text
    assert "/spec:" not in text


def test_dashboard_strip_folds_beyond_twenty():
    kinds = [GLYPH_DONE] * 12 + [GLYPH_ACTIVE] + [GLYPH_BLOCKED] + [GLYPH_PENDING] * 7
    assert fold_strip(kinds) == "donex12 in progress blocked todox7"


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
            assert "## Project Progress" in output
            assert "- Completed: 0/2" in output
            assert "## Tasks" in output
            assert "Task A: todo" in output
            assert "Task B: todo" in output
            assert "## In Progress" not in output
            assert "## Pipeline" not in output
            assert "## Metrics" not in output
            assert "/spec:" not in output
        assert "## Next" in route
        assert "## Next" in status
        assert "## Next" in check


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
        return next(line for line in text.splitlines() if line.startswith("- Tasks: "))
    return text.split("## Tasks\n", 1)[1].split("\n## ", 1)[0]


def _current_area(text, compact):
    if compact:
        return [
            line.removeprefix("- In Progress: ") for line in text.splitlines() if line.startswith("- In Progress: ")
        ]
    if "## In Progress\n" not in text:
        return []
    section = text.split("## In Progress\n", 1)[1].split("\n## ", 1)[0]
    return [line[2:] for line in section.splitlines() if line.startswith("- ")]


@pytest.mark.parametrize("ascii_mode", [False, True])
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize(
    ("content", "expected_rows", "expected_current"),
    [
        (
            MIXED_TASKS,
            [
                ("先排队", GLYPH_PENDING),
                ("并行甲", GLYPH_ACTIVE),
                ("等待服务", GLYPH_BLOCKED),
                ("并行乙", GLYPH_ACTIVE),
                ("阻塞修复已完成", GLYPH_DONE),
                ("阻塞：外部接口", GLYPH_BLOCKED),
            ],
            ["并行甲", "并行乙"],
        ),
        (NO_ACTIVE_TASKS, [("先完成", GLYPH_DONE), ("等待服务", GLYPH_BLOCKED), ("之后排队", GLYPH_PENDING)], []),
        (TASKS, [("Task A", GLYPH_PENDING), ("Task B", GLYPH_PENDING)], []),
        ("## 实现\n- [x] 全部完成\n  - boundary: x\n  - verify: x\n", [("全部完成", GLYPH_DONE)], []),
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
        render_status(
            slug, "Test", content, COMPLETE_SPEC, CHECKLIST, sections, tasks, ascii_mode=ascii_mode, compact=compact
        ),
        render_check(slug, "Test", COMPLETE_SPEC, content, CHECKLIST, ascii_mode=ascii_mode, compact=compact),
    ]
    labels = {"done": "done", "active": "in progress", "blocked": "blocked", "pending": "todo"}
    for text in outputs:
        task_area = _task_area(text, compact)
        rows = [f"{name}: {ASCII_GLYPHS[kind] if ascii_mode else labels[kind]}" for name, kind in expected_rows]
        assert all(row in task_area for row in rows), text
        assert [task_area.index(row) for row in rows] == sorted(task_area.index(row) for row in rows)
        assert _current_area(text, compact) == expected_current, text


def test_ready_json_candidate_does_not_imply_human_active_state(tmp_path):
    slug = "2026-06-12_dash-run"
    _package(tmp_path / ".spec" / "specs", slug)
    payload = json.loads(route_for_package(tmp_path, slug, output_format="json"))
    text = route_for_package(tmp_path, slug)
    assert payload["currentTask"] == "Task A"  # legacy field means next ready candidate
    assert "Task A: todo" in text
    assert "## In Progress" not in text
    assert "## Next\n\n- Task A" in text


def test_dashboard_current_fallback_and_strip_precedence():
    text = Dashboard(slug="s", stage="run", current="实际工作", next_step="实际工作").render()
    assert "## In Progress\n\n- 实际工作" in text
    assert "## Next" not in text
    text = Dashboard(
        slug="s",
        stage="check",
        strip=[GLYPH_PENDING, GLYPH_BLOCKED],
        strip_labels=["排队", "受阻"],
        current="排队",
        next_step="排队",
    ).render()
    assert "## In Progress" not in text
    assert "## Next\n\n- 排队" in text
    text = Dashboard(
        slug="s", stage="run", strip=[GLYPH_ACTIVE, GLYPH_ACTIVE], strip_labels=["甲", "乙"], next_step="乙"
    ).render()
    assert _current_area(text, False) == ["甲", "乙"]
    assert "## Next" not in text


def test_dashboard_preserves_active_tasks_beyond_the_task_list_limit():
    labels = [f"任务 {i:02}" for i in range(23)]
    text = Dashboard(slug="s", stage="run", strip=[GLYPH_ACTIVE] * len(labels), strip_labels=labels).render()
    assert "See the task package for 3 more items" in text
    assert _current_area(text, False) == labels

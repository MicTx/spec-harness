import sys
import tempfile
from pathlib import Path

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
from report_spec_package import render_status
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
- none
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


def test_dashboard_tasks_keep_project_order_and_one_active_item():
    strip = [GLYPH_DONE, GLYPH_ACTIVE, GLYPH_ACTIVE, GLYPH_BLOCKED, GLYPH_PENDING]
    labels = ["固化需求", "实现校验", "补测试", "接入依赖", "写总结"]
    text = Dashboard(slug="s", stage="run", title="项目", strip=strip, strip_labels=labels).render()
    assert "- 固化需求：完成" in text
    assert "- 实现校验：进行" in text
    assert "- 补测试：待办" in text
    assert "- 接入依赖：阻塞" in text
    assert "- 写总结：待办" in text
    task_section = text.split("## 任务", 1)[1]
    assert task_section.count("：进行") == 1
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
            assert "Task A：进行" in output
            assert "## 流水线" not in output
            assert "## 指标" not in output
            assert "/spec:" not in output
        assert "## 接下来" in route
        assert "## 接下来" not in status
        assert "## 接下来" not in check


def _parse_tasks(content):
    from report_spec_package import parse_tasks as _parse

    _sections, tasks = _parse(content)
    return tasks

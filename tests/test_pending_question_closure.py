"""Closed question records affect shared semantics, not just rendered labels."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_spec_package import compute_gate_results, gate_failure_details
from spec_package_support import clarification_gaps, extract_pending_questions

SLUG = "2026-09-13_fix-address-matching"


def _make_spec() -> str:
    return (
        """# 地址匹配 - 项目范围
## 1. 问题定义
- **项目目标**：完成地址匹配前端
- **目标用户**：地址查询用户
- **核心价值**：如实提供匹配与地图状态
## 2. 假设与待确认
### 2.1 已确认事实
- 已确认
### 2.2 关键假设
- 部署配置由部署方维护
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不更新部署侧配置
## 4. 最小实现路径
- 保留现有接口
- 实现前端地图
- 验证未配置行为
## 5. 技术决策
"""
        + f"- Git integration branch：`spec/{SLUG}`\n"
    )


def _make_tasks(done: bool = True) -> str:
    return f"- [{'x' if done else ' '}] 实现前端地图\n  - boundary: 只修改前端地图\n  - verify: 地图状态回归通过\n"


def _make_checklist(passed: bool = True) -> str:
    return (
        "## 基础\n- [x] 地图状态回归\n"
        "## 验收证据\n- 脚本验证：pytest -q\n"
        f"**验收结果**：{'通过' if passed else '待修复'}\n"
    )


REAL_MAP_CLOSURE = (
    "~~地图服务允许源待部署方提供~~ → 2026-09-13 用户追加地图多坐标系授权（见第 11 节）："
    "不再阻塞前端地图能力；`MAP_SERVICE_ALLOWLIST` 仍为部署侧配置，"
    "未配置时管理/发现接口如实报 503/错误，内置底图与运行时模板不受影响。"
)

# Verbatim reported address-matching records; no access to the user package.
CLOSED_RECORDS = (
    "~~待授权：是否更新旧 NLP 镜像~~ → 已于2026-09-13用户授权（见第10节），已执行完成。",
    REAL_MAP_CLOSURE,
    "本轮 Docker 前端自身方案无待决策项。",
)

RSAI_NO_BLOCKING = "无（无需阻塞）：采用下述保守默认值先设计和实现；目标GPU实测是硬件验收项，不是假装已有的性能保证。"

# Exact literals copied from the approved legacy samples, not live archives.
LEGACY_NO_PENDING_RECORDS = (
    "无（已确认）：最终成交价、联系方式、闲鱼链接、店铺名称和是否提供人工安装服务均已决策为发布变量；"
    "资料包使用集中式占位符和双层报价模板，发布前由售卖方按实际业务替换，不影响本轮制品结构或执行路径。",
    "无（已确认）：`push` 是 Git 收尾脚本，但其安全门禁会读取任务包状态，不能再写成“不读取任务包目录”。",
    "无（已确认：两个关键问题——竞品是否已出现「蜂群 + harness manifest + 门禁」对位功能、"
    "v0.7 优先补齐还是差异化——已内化为阶段二调研任务，由调研结果回答，无需用户决策）",
    "无（已确认：治疗判据全部证据化——租约过期/心跳停滞/显式 failed/宿主适配器错误；"
    '"阻塞但租约未过"必须由监工 observation 事件作证据，机器不做墙钟猜测）',
    "无（已确认：结构审计已在执行前完成，无阻塞性待确认项）。",
)

CLOSED_VARIANTS = (
    "无。",
    "无.",
    RSAI_NO_BLOCKING,
    RSAI_NO_BLOCKING.replace("（无需阻塞）：", "(无需阻塞):"),
    "无（无需阻塞）:采用保守默认值。",
    "无(无需阻塞)：采用保守默认值。",
    "~~地图服务允许源待部署方提供~~ → 用户追加授权：不再阻塞前端地图能力；"
    "MAP_SERVICE_ALLOWLIST仍为部署侧配置，未配置时相关接口如实报503。",
    "~~旧问题~~ → 2026-09-13 用户追加授权：不再阻塞前端地图能力。",
    "~~旧问题~~ → 用户追加地图多坐标系授权：不再阻塞前端地图能力。",
    "~~旧问题~~ → 用户追加授权（见第 11 节）：不再阻塞前端地图能力。",
    "~~旧问题~~ -> 用户追加地图多坐标系授权(见第11节):不再阻塞前端地图能力。",
    REAL_MAP_CLOSURE.replace("503/错误", "503"),
    REAL_MAP_CLOSURE.replace("503/错误", "错误"),
    REAL_MAP_CLOSURE.replace("，内置底图与运行时模板不受影响", ""),
    "~~是否保留旧接口？~~ → 已解决。",
    "~~旧问题~~ -> 已关闭（见第10节）。",
    "~~旧问题~~ → 已执行完成。",
    "~~旧问题~~ → 已解决(本轮沿用旧接口)。",
    "~~旧问题~~ → 已用户授权，已执行完成。",
    "~~旧问题~~ → 用户追加授权：不再阻塞前端地图能力。",
    "无待决策项。",
    "本轮方案无待确认问题。",
    "无（已澄清，本轮沿用原方案）。",
    "无(已确认，本轮沿用原方案)",
    "无：本轮为审查修复型任务。",
    "无（无需授权，本轮仅审查）。",
    "无（不需要授权，本轮仅审查）。",
    "无（已确认）：是否启用导出均已决策为租户配置。",
    "无（已确认：是否增加离线缓存已内化为性能调研任务，无需用户决策）",
    "无（已确认）：采用本地默认值，但部署配置由部署方维护。",
    "无（已确认：无阻塞性待确认项；采用默认配置）。",
    "No pending",
    "none",
    "nothing pending",
    "无",
)

PENDING_RECORDS = (
    "无。仍待授权。",
    "无？",
    "无.尚未确认部署地址。",
    REAL_MAP_CLOSURE.split(" → ")[0],
    REAL_MAP_CLOSURE.replace("：不再阻塞前端地图能力", ""),
    REAL_MAP_CLOSURE.replace("用户追加地图多坐标系授权", "已授权"),
    REAL_MAP_CLOSURE.replace("地图多坐标系", "任意新事项"),
    REAL_MAP_CLOSURE.replace("见第 11 节", "见第 11 节；新事项待授权"),
    REAL_MAP_CLOSURE.replace("见第 11 节", "部署方已授权"),
    REAL_MAP_CLOSURE.replace("（见第 11 节）", "（见第 11 节"),
    REAL_MAP_CLOSURE.replace("503/错误", "503/成功"),
    REAL_MAP_CLOSURE.replace("内置底图与运行时模板不受影响", "其他事项已授权"),
    REAL_MAP_CLOSURE.replace("不受影响", "仍需授权"),
    "~~旧问题~~",
    "~~是否更新镜像？~~",
    "~~旧问题~~ → 已确认。",
    "已确认旧问题，是否授权新事项？",
    "已解决旧问题，但仍需授权新事项",
    "~~旧问题~~但仍需授权新事项",
    "~~旧问题~~ → 已解决，但仍需授权新事项",
    "~~旧问题~~ → 已解决？",
    "~~旧问题~~ → 已解决。是否更新新镜像",
    "~~旧问题~~ → 已解决（尚未解决授权问题）。",
    "~~旧问题~~ → 已解决（新事项未确认）。",
    "~~旧问题~~ → 已解决（新事项未确定）。",
    "~~旧问题~~ → 已解决（新事项待决策）。",
    "~~旧问题~~ → 已解决（仍需授权新事项）。",
    "~~旧问题~~ → 已解决（新事项需要用户授权）。",
    "~~旧问题~~ → 已解决（无权限如何执行）。",
    "~~旧问题~~ → 已解决（无数据能否上线）。",
    "~~旧问题~~ → 用户追加授权：不再阻塞前端地图能力，但新事项待授权。",
    "~~旧问题~~ → 用户追加授权：不再阻塞前端地图能力；其他事项待确认。",
    "~~旧问题~~ → 已于2026-09-13用户授权（见第10节）。",
    "~~旧问题~~ → 已于2026-09-13用户授权（见第10节），尚未执行完成。",
    "~~旧问题~~ → 已于2026-09-13用户授权（其他事项未确认），已执行完成。",
    "~~旧问题~~ → 已解决；~~新问题~~",
    "新事项待授权；~~旧问题~~ → 已解决。",
    "无权限是否可以执行？",
    "无数据能否上线",
    "无（已确认旧事项；新事项尚未解决）",
    "无（已确认）：是否授权新事项",
    "无（已确认：是否采用新方案）",
    "无（已确认）：旧方案是否保留均已决策为发布变量；新方案是否启用",
    "无（已确认）：是否采用新方案已内化为调研任务，但仍需用户决策",
    "无（已确认）：是否采用新方案均已决策为发布变量？",
    "无（已确认：无阻塞性待确认项；新事项待确认）",
    "无（已确认）：是否保留旧接口；其他事项均已决策为发布变量",
    "无（已确认）：是否保留旧接口。其他事项均已决策为发布变量",
    "无（已确认）：是否启用新接口，是否保留旧接口均已决策为发布变量",
    "无（已确认）：是否仍需授权均已决策为发布变量",
    "无（已确认）：是否提供无数据场景均已决策为发布变量",
    "无（已确认）：是否保留旧接口均已决策为发布变量，新接口能否开放",
    "无（已确认）：是否启用新接口已内化为调研任务，由谁负责，无需用户决策",
    "无（新事项未确认）",
    "无：旧事项已解决，新事项仍需授权",
    "无（已确认）但仍需授权新事项",
    "无（无需阻塞：采用保守默认值。",
    "无（无需阻塞）采用保守默认值。",
    "无（无需阻塞）：",
    "无（无需阻塞）：无权限执行。",
    "无（无需阻塞）：无数据验收。",
    "无（无需阻塞）：方案是否可以执行？",
    "无（无需阻塞）：采用默认值，但仍需授权。",
    "无（无需阻塞）：采用默认值；尚未确认。",
    "无（已确认",
    "本轮 Docker 前端自身方案无待决策项？",
    "本轮 Docker 前端自身方案无待决策项。但部署问题未确认。",
    "本轮新事项尚未解决，前端无待决策项。",
    "~~本轮 Docker 前端自身方案无待决策项。~~",
    "~~~旧问题~~~ → 已解决。",
    "~~ ~~ → 已解决。",
    "~~旧问题 → 已解决。",
    "~~旧问题~~ → 已解决（见第10节。",
)


def spec_with_questions(items: tuple[str, ...] | list[str]) -> str:
    return _make_spec().replace(
        "### 2.3 待确认问题\n- 无\n",
        "### 2.3 待确认问题\n" + "".join(f"- {item}\n" for item in items),
    )


@pytest.mark.parametrize("item", CLOSED_RECORDS + CLOSED_VARIANTS + LEGACY_NO_PENDING_RECORDS)
def test_explicit_closure_is_not_pending_and_satisfies_clarification(item):
    spec = spec_with_questions([item])
    assert extract_pending_questions(spec) == []
    assert clarification_gaps(spec) == []
    results = compute_gate_results(spec, _make_tasks(), _make_checklist(passed=True))
    assert results.pending_questions == []
    assert results.assumptions_ok
    assert results.overall_ok


@pytest.mark.parametrize("item", PENDING_RECORDS)
def test_residual_and_ambiguous_questions_keep_original_text_and_gate(item):
    spec = spec_with_questions([item])
    assert extract_pending_questions(spec) == [item]
    results = compute_gate_results(spec, _make_tasks(), _make_checklist(passed=True))
    assert results.pending_questions == [item]
    assert not results.assumptions_ok
    assert not results.overall_ok
    assert "待确认 1" in gate_failure_details(results, None)["假设与范围"]


@pytest.mark.parametrize(
    "residual",
    ("但仍需授权新事项", "但仍需部署方确认域名", "是否允许部署", "；新事项待授权", "？", "?", "；未确认", "；尚未解决"),
)
@pytest.mark.parametrize("closed", (*CLOSED_RECORDS, RSAI_NO_BLOCKING, *LEGACY_NO_PENDING_RECORDS))
def test_appending_residual_to_real_closed_record_reopens_whole_item(closed, residual):
    item = closed + residual
    assert extract_pending_questions(spec_with_questions([item])) == [item]


@pytest.mark.parametrize("closed", LEGACY_NO_PENDING_RECORDS)
@pytest.mark.parametrize(
    "residual",
    ("仍需授权", "尚未确认", "待授权", "无权限执行", "无数据验收", "是否开放新接口", "如何部署", "？", "?"),
)
def test_legacy_declaration_cannot_hide_new_question_in_explanation(closed, residual):
    # Exercise residuals inside the accepted grammar, not just invalid suffixes.
    item = closed.replace("）", f"；{residual}）", 1)
    spec = spec_with_questions([item])
    assert extract_pending_questions(spec) == [item]
    assert not compute_gate_results(spec, _make_tasks(), _make_checklist(passed=True)).overall_ok


@pytest.mark.parametrize("closed", LEGACY_NO_PENDING_RECORDS)
def test_legacy_declaration_followed_by_separate_new_question(closed):
    question = "是否授权新的部署目标？"
    spec = spec_with_questions([closed, question])
    assert extract_pending_questions(spec) == [question]
    results = compute_gate_results(spec, _make_tasks(), _make_checklist(passed=True))
    assert results.pending_questions == [question]
    assert not results.assumptions_ok


@pytest.mark.parametrize("residual", ["", "？", "但仍需授权", "；尚未确认"])
def test_rsai_no_blocking_declaration_in_gate(residual):
    spec = spec_with_questions([RSAI_NO_BLOCKING + residual])
    results = compute_gate_results(spec, _make_tasks(), _make_checklist(passed=True))
    assert results.pending_questions == ([RSAI_NO_BLOCKING + residual] if residual else [])
    assert results.assumptions_ok is (not residual)


def test_mixed_section_preserves_pending_order_and_does_not_cross_headings():
    first, second = "是否授权新事项？", "无数据能否验收"
    spec = spec_with_questions([CLOSED_RECORDS[0], first, *CLOSED_RECORDS[1:], second])
    spec += "\n## 10. 授权记录\n- 是否归档？\n"
    assert extract_pending_questions(spec) == [first, second]
    results = compute_gate_results(spec, _make_tasks(), _make_checklist(passed=True))
    assert not results.assumptions_ok
    assert "待确认 2" in gate_failure_details(results, None)["假设与范围"]


@pytest.mark.parametrize("items", ([], ["问题 A"], ["待补充"], ["`xxx`"], ["N/A"]))
def test_empty_or_placeholder_section_still_requires_clarification(items):
    spec = spec_with_questions(items)
    assert extract_pending_questions(spec) == []
    assert "`spec.md` 缺少有效区块：### 2.3 待确认问题" in clarification_gaps(spec)
    assert not compute_gate_results(spec, _make_tasks(), _make_checklist(passed=True)).assumptions_ok


def test_closed_questions_do_not_erase_other_clarification_gaps():
    spec = spec_with_questions(CLOSED_RECORDS).replace("- 已确认\n", "- 事实 A\n")
    results = compute_gate_results(spec, _make_tasks(), _make_checklist(passed=True))
    assert results.pending_questions == []
    assert "`spec.md` 缺少有效区块：### 2.1 已确认事实" in results.spec_clarification_gaps
    assert not results.assumptions_ok
    assert not results.overall_ok


@pytest.mark.parametrize(
    "residual",
    (
        None,
        "~~旧问题~~ → 已解决，但仍需授权新事项",
        REAL_MAP_CLOSURE + "但仍需部署方确认域名",
        REAL_MAP_CLOSURE + "是否允许部署",
    ),
)
def test_reported_address_matching_records_in_check(tmp_path, residual):
    # Isolated fixture only: the original task-package Markdown is untouched.
    package = tmp_path / ".spec" / "specs" / SLUG
    package.mkdir(parents=True)
    (package / "tasks.md").write_text(_make_tasks(done=False), encoding="utf-8")
    (package / "checklist.md").write_text(_make_checklist(passed=False), encoding="utf-8")
    items = list(CLOSED_RECORDS) + ([residual] if residual else [])
    spec = spec_with_questions(items)
    (package / "spec.md").write_text(spec, encoding="utf-8")
    before = (package / "spec.md").read_bytes()
    results = compute_gate_results(spec, _make_tasks(), _make_checklist(passed=True))
    assert results.pending_questions == ([residual] if residual else [])
    assert results.assumptions_ok is (residual is None)
    assert (package / "spec.md").read_bytes() == before

    # Route is compared with the same package's explicit-none baseline: this
    # catches a display-only fix that leaves the routing clarification gate on.
    route_argv = [
        sys.executable,
        str(Path(__file__).resolve().parent.parent / "scripts" / "route_spec_package.py"),
        "--root",
        str(tmp_path),
        "--slug",
        SLUG,
    ]
    routed = subprocess.run(route_argv, capture_output=True, text=True, timeout=10)
    assert routed.returncode == 0, routed.stderr
    assert (package / "spec.md").read_bytes() == before
    (package / "spec.md").write_text(spec_with_questions(["无"]), encoding="utf-8")
    baseline = subprocess.run(route_argv, capture_output=True, text=True, timeout=10)
    assert baseline.returncode == 0, baseline.stderr
    if residual:
        assert routed.stdout != baseline.stdout
        assert f"需要确认需求：{residual}" in routed.stdout
    else:
        assert routed.stdout == baseline.stdout

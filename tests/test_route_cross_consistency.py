"""三层决策器组合语义回归钉：route_decision × loop_route × workflow_route。

实测基线：2026-09-25 @ HEAD 3f6f35c（分支 spec/2026-09-25_add-route-decision-table），
三脚本对全量候选文本实跑后入库；入库语料的任何一条失配都只调语料、不调启发式
（「语料失配只调语料」是本文件的入库纪律）。

四组断言：

(a) 书面不变量——workflow-runner README「Relationship to route_decision.py and
    team-loop」写明该 slot 只激活 explore/build/review 的执行段。校准语料断言保留
    （排除循环收敛形态文本，每条实测 workflowRecommended=True）。F11（2026-10-08）
    升级为泛化不变量：黄金语料全集（GOLDEN_TRIPLES + workflow slot POSITIVES/
    REVIEWER_REPRO_CASES（按名动态读取，slot 新增正例自动入不变量语料）+
    INVARIANT_CORPUS）逐条实测，非显式、非登记共存的 workflowRecommended=True 行
    route ∈ {explore, build, review}。两类例外层显式登记、各自单独钉住：显式
    workflow 请求（WORKFLOW_EXPLICIT 词表命中）route=local + wf=True 是已知有意
    的例外（用户显式指定执行面，advisory 建议不改写五路由判定）；panel 最小形状
    探针的 local 共存按 spec §8 回写裁定（2026-10-08）登记为
    PANEL_LOCAL_COEXISTENCE_TEXTS 冻结共层——advisory 建议在书面激活域外不激活，
    是合法设计态（与 local+loop 共存同构）。

(b) 显式负分支——空/乱码文本 ⇒ route=local 且 score=0（tests/test_route_decision.py
    空文本先例）、loopRecommended=False 且 mode=none（loop_route.py 空文本分支）、
    workflowRecommended=False 且 mode=none（workflow_route.py 空文本分支）。
    超长全零文本按 route_decision.py「全零信号按字数粗分」落到 explore（score 仍 0），
    一并钉住。

(c) 组合语义黄金白名单——冻结实测三元组 (route, loopRecommended, workflowRecommended)，
    显式含 local+True 共存例：这是合法语义而非矛盾（slot 激活独立于编排路由，
    见 SKILL.md「Activation rule」与 commands.md `/spec:run` 第 3 步）。
    任一决策器行为漂移即红，强制有意识复核而非静默语义变更。
    更新白名单必须是显式 diff 评审：改动本文件语料表的提交须单独可读、注明
    复核结论，禁止顺手重录。

(d) 文档断言——orchestration.md「Task-shape decision table」判定表五行齐备
    （局部/探索/并行扇出/循环收敛/外部隔离，各含决策脚本/前置条件/降级路径）、
    表内 token ⊆ 五 route 词表 ∪ 既有 slot mode 名、loopRecommended=true 与
    route=local 共存合法性说明在位；workflow-runner README 核心协议第 3 步含
    Claude Code 分支自包含委派包（五字段）与四模式验收点。F11（2026-10-08）
    扩容：Hard precondition 1 强形态信号门禁（score ≥ 2 + strong shape signal，
    弱信号不激活）、两 slot README 交界让出（REVIEW_FIX_DEFER_PATTERNS →
    loopRecommended=false，workflow 侧承接 / team-loop 侧让出）、team-loop
    README batch-fanout 显式 init / 不推荐说明、判定表循环收敛行不含
    batch-fanout 且含让出说明、Operating model 总述显式初始化 / 降级面措辞；
    负样本用 F11 前历史漂移片段字符串走同一断言助手（不动仓库文件），缺任一
    语义关键词即红。

历史裁定（2026-10-04_remove-process-bloat）：
- team-loop 已让出 batch-fanout / review-fix 触发域（归 workflow-runner，仓库自有
  执行面裁定），loop_route.py 不再对批量/评审-修复形态做推荐。

已知缺口（workflow 侧）——已全部收口：
- 泛化「批量」过触发缺口已于 2026-10-08（F9，spec 包
  2026-10-08_fix-workflow-route-triggers）收口：workflow_route.py 改为强弱分权
  （强命中 ×2 + 弱命中 ×1，推荐需强命中 ≥1），泛化「批量」单词不再独立过推荐阈。
  原缺口文本「批量更新这 3 个配置文件的版本号」从已知缺口登记退役为收口断言
  （local + workflowRecommended=False，见 RETIRED_BATCH_GAP_TEXTS）。依据
  plans/03-route-semantics.md F9 强弱分权裁定。
- 循环收敛形态缺口已于 2026-10-08（F10，spec 包
  2026-10-08_fix-loop-converge-semantics）收口：workflow_route.py 的 LOOP 形态
  拆强（评审-修复复合）/弱（裸收敛标记）两表，`iterate until all tests pass` 等
  裸收敛文本不再推荐 workflow（裸收敛语义归 team-loop until-converged）；loop 侧
  同步补完评审-修复显式让出早退（REVIEW_FIX_DEFER_PATTERNS，与 workflow 侧 LOOP
  强表行为级镜像）。原缺口文本从已知缺口登记退役为收口断言（route=local +
  loopRecommended=True + workflowRecommended=False，wf reason 含「裸收敛(弱)」，见
  RETIRED_LOOP_GAP_TEXTS）。依据 plans/03-route-semantics.md F10 强弱拆分裁定与
  R3 改判（2026-10-07，选项 a）。
- F11（2026-10-08，spec 包 2026-10-08_align-route-activation-docs）例外层登记：
  显式 workflow 请求（`用 workflow 编排这个任务`/`ultracode` 等 WORKFLOW_EXPLICIT
  词表命中）route=local + wf=True 是已知有意的例外——用户显式指定执行面，
  advisory 建议不改写五路由判定（见 WORKFLOW_EXPLICIT_EXCEPTIONS，不入泛化不变量）；
  panel 最小形状探针（`出多个独立方案对比评分` 等）的 local 共存按 spec §8 回写
  裁定登记为 PANEL_LOCAL_COEXISTENCE_TEXTS 冻结共层（route=local + loop=False +
  wf=True + mode=perspective-panel；非 F9/F10 收口残留，panel 词表自始强信号；
  advisory 建议在书面激活域外不激活，合法设计态；成员增删即红，须显式 diff 复核）。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
ROUTE_SCRIPT = ROOT / "scripts" / "route_decision.py"
LOOP_SCRIPT = ROOT / "slots" / "team-loop" / "scripts" / "loop_route.py"
WORKFLOW_SCRIPT = ROOT / "slots" / "workflow-runner" / "scripts" / "workflow_route.py"
WORKFLOW_SLOT_SCRIPTS = ROOT / "slots" / "workflow-runner" / "scripts"
WORKFLOW_SLOT_TEST = ROOT / "slots" / "workflow-runner" / "tests" / "test_workflow_route.py"
ORCHESTRATION_DOC = ROOT / "references" / "orchestration.md"
WORKFLOW_README = ROOT / "slots" / "workflow-runner" / "README.md"
TEAM_LOOP_README = ROOT / "slots" / "team-loop" / "README.md"

ROUTES = ("local", "explore", "build", "review", "external")
LOOP_MODES = ("until-converged", "fixed-rounds", "none")
WORKFLOW_MODES = ("batch-fanout", "parallel-review", "perspective-panel", "review-fix-loop", "none")
ALLOWED_TOKENS = frozenset(ROUTES + LOOP_MODES + WORKFLOW_MODES)


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def route_decision() -> Any:
    return _load("route_decision_cross", ROUTE_SCRIPT)


@pytest.fixture(scope="module")
def loop_route() -> Any:
    return _load("loop_route_cross", LOOP_SCRIPT)


@pytest.fixture(scope="module")
def workflow_route() -> Any:
    return _load("workflow_route_cross", WORKFLOW_SCRIPT)


@pytest.fixture(scope="module")
def workflow_slot_corpus() -> dict[str, list[str]]:
    """workflow-runner slot 测试正例语料文本（POSITIVES / REVIEWER_REPRO_CASES）。

    语料真源在 slot 测试文件，按名读取、不复制：slot 侧新增正例自动进入泛化
    不变量语料；常量改名/删除使加载在此即红（有意识复核，不静默漂移）。加载需
    临时把 slot scripts 目录挂上 sys.path（slot 测试顶层 `from workflow_route
    import route` 依赖，同 slot 自带 conftest 行为），加载后即摘除。
    """
    scripts_dir = str(WORKFLOW_SLOT_SCRIPTS)
    sys.path.insert(0, scripts_dir)
    try:
        module = _load("workflow_route_slot_corpus", WORKFLOW_SLOT_TEST)
    finally:
        sys.path.remove(scripts_dir)
    return {
        "positives": [text for text, _mode in module.POSITIVES],
        "reviewer_repro": [text for text, _mode in module.REVIEWER_REPRO_CASES],
    }


def _triple(route_mod: Any, loop_mod: Any, workflow_mod: Any, text: str) -> tuple[str, bool, bool]:
    """三层同文实测：(route, loopRecommended, workflowRecommended)。"""
    route_payload = route_mod.decide(text)
    loop_payload = loop_mod.route(text)
    workflow_payload = workflow_mod.route(text)
    return (
        route_payload["route"],
        loop_payload["loopRecommended"],
        workflow_payload["workflowRecommended"],
    )


# -- (a) 书面不变量：workflowRecommended=true ⇒ route ∈ {explore, build, review} --

# 校准语料：排除循环收敛形态文本；每条 2026-09-25 @ 3f6f35c 实测
# workflowRecommended=True 且 route ∈ {explore, build, review}。
# 语料失配只调语料（附实测记录），不调启发式。
INVARIANT_CORPUS = [
    # review：批量评审 + 逐个独立审查（wf mode=batch-fanout）
    "批量评审 20 个模块的鉴权代码，逐个独立审查后汇总",
    # review：并行评审信号（wf mode=parallel-review）
    "多代理并行评审这个风险改动",
    # explore：批量梳理 + 结构不清（wf mode=batch-fanout）
    "批量梳理支付失败链路，结构不清需要多面并行探索",
    # build：批量实现 + 切片分工（wf mode=batch-fanout）
    "批量实现导出功能，前端/后端/测试切片分工",
    # build：批量重构 + 可拆分（wf mode=batch-fanout）
    "批量重构支付回调模块，模块边界清晰可拆分",
]

# 循环收敛形态文本不得进入校准语料（书面不变量只约束非 loop 形态任务）。
LOOP_FORM_MARKERS = ("循环", "直至", "直到", "多轮", "反复", "iterate", "until")

# F10（2026-10-08）收口退役登记：原「循环收敛文本落 local + wf=True」缺口文本。
# 拆分后实测：route=local + loopRecommended=True（team-loop 承接）+
# workflowRecommended=False（弱信号单独不过阈，wf reason 含「裸收敛(弱)」）。
# 依据：plans/03-route-semantics.md F10 强弱拆分 + R3 改判；改回 True 须显式 diff 复核。
RETIRED_LOOP_GAP_TEXTS = ("iterate until all tests pass",)

# F9（2026-10-08）收口退役登记：原「泛化批量单词独立过推荐阈」缺口文本。
# 弱信号 alone score=1 不过阈：实测 route=local + workflowRecommended=False + mode=none。
# 依据：plans/03-route-semantics.md F9 强弱分权；改回 True 须显式 diff 复核。
RETIRED_BATCH_GAP_TEXTS = ("批量更新这 3 个配置文件的版本号",)

# F11（2026-10-08）显式例外语料层：显式 workflow 请求（WORKFLOW_EXPLICIT 词表命中）
# 是已知有意的例外——用户显式指定执行面，route=local + wf=True 合法（advisory 建议
# 不改写五路由判定）。仅收纯显式文本：带路由信号的显式文本 route 随信号走（如
# `ultracode 全面审查这个模块的缺陷` 实测 route=review，直接满足泛化不变量，不属本层）。
# 泛化不变量按词表命中排除显式家族（家族级规则，对新增显式文本稳健），本层逐条钉住。
# (text, expected_mode) 全部 2026-10-08 实测；更新须显式 diff 评审，禁止顺手重录。
WORKFLOW_EXPLICIT_EXCEPTIONS = [
    ("用 workflow 编排这个任务", "batch-fanout"),
    ("ultracode", "batch-fanout"),
    ("用 workflow 工具跑这个任务", "batch-fanout"),
]

# F11（2026-10-08）panel 共存冻结层（spec §8 回写裁定：收口语料）：panel 最小形状
# 探针无路由层信号（rscore=0）、loop 不承接、wf 以 panel 强信号推荐——advisory 建议
# 在书面激活域外即「不激活」，是与 local+loop 共存同构的合法设计态。非 F9/F10
# 收口残留（panel 词表自始强信号）。泛化不变量按精确文本排除本层；成员须仍在
# slot 语料库（登记过期即红）；改任一冻结值或成员集须显式 diff 评审。
# (text, route, loopRecommended, workflowRecommended, mode) 全部 2026-10-08 实测。
PANEL_LOCAL_COEXISTENCE_TEXTS = [
    ("出 3 个独立方案对比评分，从胜者综合", "local", False, True, "perspective-panel"),
    ("出多个独立方案对比评分", "local", False, True, "perspective-panel"),
    ("judge panel 评一下三个方案", "local", False, True, "perspective-panel"),
]


def test_invariant_corpus_stays_loop_form_free():
    for text in INVARIANT_CORPUS:
        lowered = text.lower()
        for marker in LOOP_FORM_MARKERS:
            assert marker not in lowered, f"校准语料须排除循环收敛形态文本: {text!r} 含 {marker!r}"
    for text in RETIRED_LOOP_GAP_TEXTS + RETIRED_BATCH_GAP_TEXTS:
        assert text not in INVARIANT_CORPUS, f"退役缺口文本不得混入校准语料: {text!r}"


def test_workflow_recommendation_implies_exploratory_route_on_calibration_corpus(
    route_decision, loop_route, workflow_route
):
    for text in INVARIANT_CORPUS:
        workflow_payload = workflow_route.route(text)
        assert workflow_payload["workflowRecommended"] is True, f"语料失配（预期 wf=True，调语料不调启发式）: {text!r}"
        route_payload = route_decision.decide(text)
        assert route_payload["route"] in ("explore", "build", "review"), (
            f"书面不变量失配（workflow-runner README 激活域）: {text!r} -> route={route_payload['route']}"
        )


def test_retired_loop_gap_texts_stay_local_with_loop_takeover_without_workflow(
    route_decision, loop_route, workflow_route
):
    """F10 收口退役钉：裸收敛文本 route=local、loop 承接（until-converged）、wf 不推荐
    （回退即红，须显式 diff 复核）。交界归位：裸收敛语义归 team-loop，workflow 只认
    评审-修复复合形态。"""
    for text in RETIRED_LOOP_GAP_TEXTS:
        route_payload = route_decision.decide(text)
        assert route_payload["route"] == "local", f"{text!r} -> {route_payload['route']}"
        loop_payload = loop_route.route(text)
        assert loop_payload["loopRecommended"] is True, f"{text!r} 裸收敛语义须由 team-loop 承接: {loop_payload!r}"
        assert loop_payload["mode"] == "until-converged", f"{text!r} -> {loop_payload['mode']}"
        workflow_payload = workflow_route.route(text)
        assert workflow_payload["workflowRecommended"] is False, (
            f"{text!r} F10 强弱拆分收口回退：裸收敛标记不应再过推荐阈"
        )
        assert workflow_payload["mode"] == "none", f"{text!r} 收口后 mode 应为 none"
        assert "裸收敛(弱)" in workflow_payload["reason"], (
            f"{text!r} 弱信号计数应保留在 reason 供审计: {workflow_payload['reason']!r}"
        )


def test_retired_batch_gap_texts_stay_local_without_workflow_recommendation(route_decision, loop_route, workflow_route):
    """F9 收口退役钉：泛化「批量」弱信号 alone 不得再推荐 workflow（回退即红，须显式复核）。"""
    for text in RETIRED_BATCH_GAP_TEXTS:
        route_payload = route_decision.decide(text)
        workflow_payload = workflow_route.route(text)
        assert route_payload["route"] == "local", f"{text!r} -> {route_payload['route']}"
        assert workflow_payload["workflowRecommended"] is False, (
            f"{text!r} F9 强弱分权收口回退：泛化批量弱信号不应再过推荐阈"
        )
        assert workflow_payload["mode"] == "none", f"{text!r} 收口后 mode 应为 none"
        assert "泛化批量(弱)" in workflow_payload["reason"], (
            f"{text!r} 弱信号计数应保留在 reason 供审计: {workflow_payload['reason']!r}"
        )


def _explicit_hits(workflow_mod: Any, text: str) -> int:
    """按 workflow_route.py 自身语义（re.IGNORECASE 逐 pattern search）计显式命中数。"""
    return sum(1 for pattern in workflow_mod.WORKFLOW_EXPLICIT if re.search(pattern, text, re.IGNORECASE))


def test_workflow_explicit_requests_allow_local_true_coexistence(route_decision, workflow_route):
    """F11 显式例外层钉：纯显式 workflow 请求 route=local + wf=True 合法（已知有意的
    例外，用户显式指定执行面；mode 沿用 batch-fanout 兼容，不新增 explicit mode）。"""
    for text, expected_mode in WORKFLOW_EXPLICIT_EXCEPTIONS:
        assert _explicit_hits(workflow_route, text) >= 1, f"例外语料须命中 WORKFLOW_EXPLICIT 词表: {text!r}"
        route_payload = route_decision.decide(text)
        assert route_payload["route"] == "local", (
            f"{text!r} 纯显式请求（无路由信号）应落 local: {route_payload['route']}"
        )
        workflow_payload = workflow_route.route(text)
        assert workflow_payload["workflowRecommended"] is True, f"{text!r} 显式请求应推荐 workflow"
        assert workflow_payload["mode"] == expected_mode, f"{text!r} 显式例外层 mode 漂移: {workflow_payload['mode']}"


def test_panel_minimal_probes_stay_registered_local_advisory_coexistence(
    route_decision, loop_route, workflow_route, workflow_slot_corpus
):
    """F11 panel 共存冻结层钉（spec §8 回写裁定 2026-10-08）：三文本全值冻结
    (route=local + loop=False + wf=True + mode=perspective-panel)，成员须仍在
    slot 语料库。改任一值、成员增删或登记过期即红，须显式 diff 复核。"""
    slot_texts = set(workflow_slot_corpus["positives"]) | set(workflow_slot_corpus["reviewer_repro"])
    for text, expected_route, expected_loop, expected_workflow, expected_mode in PANEL_LOCAL_COEXISTENCE_TEXTS:
        assert text in slot_texts, f"登记成员已不在 slot 语料库（登记过期，须复核收口）: {text!r}"
        actual = _triple(route_decision, loop_route, workflow_route, text)
        assert actual == (expected_route, expected_loop, expected_workflow), (
            f"panel 共存层冻结值漂移: {text!r} "
            f"expected={(expected_route, expected_loop, expected_workflow)} actual={actual}"
        )
        workflow_payload = workflow_route.route(text)
        assert workflow_payload["mode"] == expected_mode, f"{text!r} panel 共存层 mode 漂移: {workflow_payload['mode']}"


def test_nonexplicit_workflow_recommendation_stays_inside_written_activation_domain(
    route_decision, workflow_route, workflow_slot_corpus
):
    """F11 泛化不变量：黄金语料全集（GOLDEN_TRIPLES + slot POSITIVES/
    REVIEWER_REPRO_CASES（动态读取）+ INVARIANT_CORPUS）逐条实测，非显式、非登记
    共存的 wf=True 行 route ∈ {explore, build, review}。显式家族按 WORKFLOW_EXPLICIT
    词表命中排除（家族级规则，对新增显式文本稳健）；panel 共存层按精确文本排除
    （§8 登记冻结层）。slot 侧新增正例自动入不变量语料——新违反即红，按 spec §8
    停线裁定收口，不得为绿而绿。"""
    corpus = [row[0] for row in GOLDEN_TRIPLES]
    corpus += workflow_slot_corpus["positives"]
    corpus += workflow_slot_corpus["reviewer_repro"]
    corpus += INVARIANT_CORPUS
    registered_panels = {row[0] for row in PANEL_LOCAL_COEXISTENCE_TEXTS}
    offenders = []
    for text in dict.fromkeys(corpus):  # 去重保序：跨源重复文本只测一次
        if _explicit_hits(workflow_route, text) or text in registered_panels:
            continue
        workflow_payload = workflow_route.route(text)
        if workflow_payload["workflowRecommended"] is not True:
            continue
        route_payload = route_decision.decide(text)
        if route_payload["route"] not in ("explore", "build", "review"):
            offenders.append((text, route_payload["route"]))
    assert not offenders, (
        "书面激活域失配（非显式、非登记的 wf=True 行 route 不在 explore/build/review；"
        "语料失配只调语料，新违反按 spec §8 停线裁定，不得为绿而绿）: " + repr(offenders)
    )


# -- (b) 显式负分支：空/乱码/无法分类文本的结构化早退 --

NEGATIVE_TEXTS = ["", "!!!", "\x00\x01", "🤖🤖"]


def test_negative_text_fails_closed_on_all_three_layers(route_decision, loop_route, workflow_route):
    for text in NEGATIVE_TEXTS:
        route_payload = route_decision.decide(text)
        assert route_payload["route"] == "local", f"{text!r}: 负样本必须落 local"
        assert route_payload["score"] == 0, f"{text!r}: 负样本 score 必须为 0"

        loop_payload = loop_route.route(text)
        assert loop_payload["loopRecommended"] is False, f"{text!r}: 负样本不得推荐 loop"
        assert loop_payload["mode"] == "none", f"{text!r}: 负样本 mode 必须为 none"

        workflow_payload = workflow_route.route(text)
        assert workflow_payload["workflowRecommended"] is False, f"{text!r}: 负样本不得推荐 workflow"
        assert workflow_payload["mode"] == "none", f"{text!r}: 负样本 mode 必须为 none"


def test_long_unclassifiable_text_falls_back_to_explore_with_zero_score(route_decision):
    """route_decision.py「全零信号按字数粗分」分支：超长全零文本 explore（理解先行）、score 仍 0。"""
    payload = route_decision.decide("a" * 10000)
    assert payload["route"] == "explore"
    assert payload["score"] == 0


# -- (c) 组合语义黄金白名单：冻结实测三元组 --

# (text, route, loopRecommended, workflowRecommended)
# 全部 2026-09-25 @ HEAD 3f6f35c 三脚本实跑后入库；更新须显式 diff 评审，禁止顺手重录。
GOLDEN_TRIPLES = [
    # local + loop 共存例（合法语义：slot 激活独立于路由，命中即移交执行段）
    # F10（2026-10-08）改判：裸收敛标记归弱表单独不推荐 -> local + loop 承接 + wf=False；
    # 复核结论：LOOP 强弱拆分收口（plans/03-route-semantics.md F10），原 (local, True, True) 退役。
    ("iterate until all tests pass", "local", True, False),
    ("对这 12 个章节循环抽取元数据，直至校验全部通过", "local", True, False),
    # F9（2026-10-08）改判：泛化「批量」弱信号 alone 不过推荐阈 -> local + wf=False；
    # 复核结论：强弱分权收口（plans/03-route-semantics.md F9），弱 label 仅计 score 不参与推荐。
    ("批量更新这 3 个配置文件的版本号", "local", False, False),
    # F10（2026-10-08）新增：R3 改判（2026-10-07，选项 a）锚点——修复+收敛无再审语义，
    # loop 承接（until-converged）、wf 弱信号单独不推荐；原 REVIEWER_REPRO 同文本改判 team-loop。
    ("fix results and iterate until converge", "local", True, False),
    # F10（2026-10-08）新增：评审-修复复合形态收口实测值——loop 显式让出、wf 推荐，
    # route=review 落在书面激活域内（workflow-runner README）。
    ("修复后再审一次直到评审通过", "review", False, True),
    # 并行评审扇出：route=review，loop/wf 双推荐
    ("批量评审 20 个模块的鉴权代码，逐个独立审查后汇总", "review", False, True),
    ("多代理并行评审这个风险改动", "review", False, True),
    # 探索扇出
    ("批量梳理支付失败链路，结构不清需要多面并行探索", "explore", False, True),
    ("梳理支付失败链路：这个代码库结构不清，需要多面并行探索", "explore", False, False),
    # 实现扇出与局部实现
    ("批量实现导出功能，前端/后端/测试切片分工", "build", False, True),
    ("实现支付失败修复，模块边界清晰可并行开发，前端/后端/测试可拆分", "build", False, False),
    # 纯局部负例
    ("修复 README 中的一个错别字", "local", False, False),
]


def test_golden_triples_freeze_cross_layer_semantics(route_decision, loop_route, workflow_route):
    for text, expected_route, expected_loop, expected_workflow in GOLDEN_TRIPLES:
        actual = _triple(route_decision, loop_route, workflow_route, text)
        assert actual == (expected_route, expected_loop, expected_workflow), (
            f"组合语义漂移（强制有意识复核，禁止顺手重录白名单）: {text!r} "
            f"expected={((expected_route, expected_loop, expected_workflow))} actual={actual}"
        )


def test_golden_triples_include_local_loop_coexistence():
    coexistence = [row for row in GOLDEN_TRIPLES if row[1] == "local" and row[2] is True]
    assert coexistence, "白名单必须显式含 local+loopRecommended=true 共存例"


# -- (d) 文档断言：判定表与 README 核心协议 --


def _section(content: str, heading: str) -> str:
    start = content.index(heading)
    body_start = content.index("\n", start) + 1
    next_heading = content.find("\n## ", body_start)
    if next_heading < 0:
        return content[body_start:]
    return content[body_start:next_heading]


def _table_rows(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells[0] in ("任务形状", "") or set(cells[0]) <= {"-", ":", " "}:
            continue
        rows.append(cells)
    return rows


DECISION_TABLE_HEADING = "## Task-shape decision table"
TABLE_SHAPES = ("局部", "探索", "并行扇出", "循环收敛", "外部隔离")


def test_decision_table_has_five_complete_shape_rows():
    section = _section(ORCHESTRATION_DOC.read_text(encoding="utf-8"), DECISION_TABLE_HEADING)
    rows = _table_rows(section)
    shapes = tuple(row[0] for row in rows)
    assert shapes == TABLE_SHAPES, f"判定表必须恰好五行且按序: {shapes}"
    for row in rows:
        assert len(row) >= 5, f"{row[0]} 行缺少列: {row}"
        for label, cell in zip(("决策脚本", "前置条件", "降级路径"), row[2:5]):
            assert cell.strip(), f"{row[0]} 行的 {label} 不得为空"


def test_decision_table_tokens_stay_inside_existing_vocabularies():
    section = _section(ORCHESTRATION_DOC.read_text(encoding="utf-8"), DECISION_TABLE_HEADING)
    for row in _table_rows(section):
        surface_cell = row[1]
        tokens = re.findall(r"`([^`]+)`", surface_cell)
        assert tokens, f"{row[0]} 行执行面列缺 token: {surface_cell!r}"
        for token in tokens:
            assert token in ALLOWED_TOKENS, (
                f"{row[0]} 行执行面 token {token!r} 越界（只允许五 route 词表与既有 slot mode 名）"
            )


def test_decision_table_documents_loop_local_coexistence_legality():
    section = _section(ORCHESTRATION_DOC.read_text(encoding="utf-8"), DECISION_TABLE_HEADING)
    assert "共存" in section and "合法" in section, "判定表必须写明共存合法性"
    assert "SKILL.md" in section, "共存说明须引用 SKILL.md Activation rule"
    assert "commands.md" in section, "共存说明须引用 commands.md /spec:run 第 3 步"


def test_workflow_readme_plan_step_carries_self_contained_delegation_package():
    content = WORKFLOW_README.read_text(encoding="utf-8")
    step3 = content[content.index("3. **Author `plan.json`**") : content.index("4. **Bound the work**")]
    assert "自包含" in step3, "plan.json item prompt 须写明自包含委派包"
    for field in ("目标", "范围", "排除", "输出", "验证"):
        assert field in step3, f"自包含委派包缺字段: {field}"


def test_workflow_readme_plan_step_carries_acceptance_points_for_all_four_modes():
    content = WORKFLOW_README.read_text(encoding="utf-8")
    step3 = content[content.index("3. **Author `plan.json`**") : content.index("4. **Bound the work**")]
    for mode in ("batch-fanout", "parallel-review", "perspective-panel", "review-fix-loop"):
        assert f"`{mode}` 验收点" in step3, f"模式 {mode} 缺验收点"


# -- F11（2026-10-08）书面激活域一致性扩容：钉语义关键词，不钉整句 --

HARD_PRECONDITIONS_HEADING = "## Hard preconditions (checked in this order, every time)"
WORKFLOW_RELATIONSHIP_HEADING = "## Relationship to scripts/route_decision.py and team-loop"
TEAM_LOOP_TRIGGER_HEADING = "## Trigger paths (gap 1: hook + code dual triggering)"
OPERATING_MODEL_HEADING = "## Operating model"


def _assert_hard_precondition_gate(section: str) -> None:
    """Hard precondition 1：score ≥ 2 量化门 + 强形态信号门 + 弱信号不激活说明。"""
    assert "score ≥ 2" in section, "Hard precondition 1 须保留 score ≥ 2 量化门"
    assert "strong shape signal" in section, "Hard precondition 1 须含强形态信号门禁"
    assert "do not activate" in section, "Hard precondition 1 须写明泛化弱信号不激活"


def _assert_relationship_boundary_yield(section: str) -> None:
    """workflow 侧承接 + loop_route 显式让出（触发交界 F10/F11 语义）。"""
    assert "review-fix" in section and "workflow-runner" in section, (
        "Relationship 交界须写明评审-修复组合形态归 workflow-runner 承接"
    )
    assert "REVIEW_FIX_DEFER_PATTERNS" in section, "交界让出须点名 REVIEW_FIX_DEFER_PATTERNS"
    assert "loopRecommended=false" in section, "交界让出须写明 loopRecommended=false"
    assert "defers" in section, "loop_route.py 让出语义（defers）须在场"


def _assert_team_loop_trigger_yield(section: str) -> None:
    """team-loop 侧让出 + batch-fanout 显式 init / 不推荐说明。"""
    assert "REVIEW_FIX_DEFER_PATTERNS" in section and "loopRecommended=false" in section, (
        "team-loop 触发节须写明评审-修复让出（REVIEW_FIX_DEFER_PATTERNS → loopRecommended=false）"
    )
    assert "workflow-runner" in section, "team-loop 触发节须写明归 workflow-runner 承接"
    assert "init --mode batch-fanout" in section, "batch-fanout 显式 init 须在场"
    assert "does not recommend" in section, "loop_route.py 不推荐 batch-fanout 须写明"


def _assert_convergence_row_defers_review_fix(row: list[str]) -> None:
    """判定表循环收敛行：不再列 batch-fanout，且含让出说明。"""
    row_text = " | ".join(row)
    assert "batch-fanout" not in row_text, "循环收敛行不得再列 batch-fanout（路由推荐面已让出）"
    assert "评审-修复" in row_text, "循环收敛行须含评审-修复让出说明"
    assert "workflow-runner" in row_text, "让出承接面（workflow-runner）须点名"
    assert "让出" in row_text, "让出语义（loop_route 显式让出）须在场"


def _assert_preamble_names_explicit_init_and_degradation(section: str) -> None:
    """Operating model 总述：路由触发归 workflow-runner + 显式初始化/降级面措辞。"""
    assert "workflow-runner" in section and "loop_route.py" in section, (
        "总述须写明批量/评审-修复路由触发归 workflow-runner（loop_route.py 让出）"
    )
    assert "explicit initialization" in section, "总述须含 batch-fanout 显式初始化措辞"
    assert "degradation" in section, "总述须含降级面措辞"


def test_workflow_readme_hard_precondition_gate_pins_strong_signal_semantics():
    _assert_hard_precondition_gate(_section(WORKFLOW_README.read_text(encoding="utf-8"), HARD_PRECONDITIONS_HEADING))


def test_workflow_readme_relationship_documents_boundary_yield():
    _assert_relationship_boundary_yield(
        _section(WORKFLOW_README.read_text(encoding="utf-8"), WORKFLOW_RELATIONSHIP_HEADING)
    )


def test_team_loop_readme_trigger_documents_defer_and_explicit_init():
    _assert_team_loop_trigger_yield(_section(TEAM_LOOP_README.read_text(encoding="utf-8"), TEAM_LOOP_TRIGGER_HEADING))


def test_decision_table_convergence_row_defers_review_fix_to_workflow_runner():
    section = _section(ORCHESTRATION_DOC.read_text(encoding="utf-8"), DECISION_TABLE_HEADING)
    for row in _table_rows(section):
        if row[0] == "循环收敛":
            _assert_convergence_row_defers_review_fix(row)
            return
    raise AssertionError("判定表缺「循环收敛」行")


def test_orchestration_preamble_names_explicit_init_and_degradation_face():
    _assert_preamble_names_explicit_init_and_degradation(
        _section(ORCHESTRATION_DOC.read_text(encoding="utf-8"), OPERATING_MODEL_HEADING)
    )


# F11 前历史漂移片段（spec §2.1 实测引文），仅作负样本字符串，非仓库现状。
NEG_GATE_SNIPPET = (
    "## Hard preconditions (checked in this order, every time)\n\n"
    "1. The task's shape matched `slots/workflow-runner/scripts/workflow_route.py` "
    "(score ≥ 2): batch fan-out, parallel review,\n   perspective panel, or review-fix loop."
)
NEG_RELATIONSHIP_SNIPPET = (
    "## Relationship to scripts/route_decision.py and team-loop\n\n"
    "**Trigger disambiguation with `team-loop`**: both slots list batch fan-out and "
    "review-fix loops. Use `workflow-runner` when the repository-owned driver and a "
    "worker CLI are available; use `team-loop` for loop state, retries, heartbeats, or "
    "when the driver cannot run. Host identity and host-native orchestration APIs never "
    "select an execution surface."
)
NEG_TRIGGER_SNIPPET = (
    "## Trigger paths (gap 1: hook + code dual triggering)\n\n"
    "| Path | Mechanism | Notes |\n|---|---|---|\n"
    '| Code decision | Any session runs `loop_route.py --text "<goal>"` directly | '
    "Emits a JSON decision + suggested configuration, programmatically consumable |\n"
    "| Explicit request | The user names a loop / iterative advance / multi-round "
    "convergence | Enter initialization directly |"
)
NEG_ROW_SNIPPET = [
    "循环收敛",
    "任意 route × team-loop（`until-converged` / `fixed-rounds` / `batch-fanout`）",
    "`slots/team-loop/scripts/loop_route.py --text`",
    "`loopRecommended=true`（命中即移交执行段）；agents-team 工具面已加载",
    "工具面缺失时显式报错，不静默降级为单会话循环",
]
NEG_PREAMBLE_SNIPPET = (
    "## Operating model\n\n"
    "The `team-loop` slot remains the managed protocol for loop-until-converged / "
    "batch fan-out / review-fix loops. Orchestration routing and the team-loop protocol "
    "are complementary: routing chooses topology; the slot owns disk-truth loop state "
    "when its triggers hit. When the same-shape batch fan-out / review-fix-loop "
    "triggers hit both slots, use the repository-owned `workflow-runner` driver when a "
    "worker CLI is available; otherwise use `team-loop` (agents-team tool surface)."
)


@pytest.mark.parametrize(
    ("assertion, doctored_snippet"),
    [
        pytest.param(_assert_hard_precondition_gate, NEG_GATE_SNIPPET, id="gate_without_strong_signal"),
        pytest.param(_assert_relationship_boundary_yield, NEG_RELATIONSHIP_SNIPPET, id="relationship_without_yield"),
        pytest.param(_assert_team_loop_trigger_yield, NEG_TRIGGER_SNIPPET, id="trigger_without_defer"),
        pytest.param(_assert_convergence_row_defers_review_fix, NEG_ROW_SNIPPET, id="row_still_lists_batch_fanout"),
        pytest.param(
            _assert_preamble_names_explicit_init_and_degradation,
            NEG_PREAMBLE_SNIPPET,
            id="preamble_without_boundary_semantics",
        ),
    ],
)
def test_negative_doctored_doc_snippets_fail_the_same_assertion_helpers(assertion, doctored_snippet):
    """负样本构造：F11 前历史漂移措辞走同一断言助手必须红（不改仓库文件）。"""
    with pytest.raises(AssertionError):
        assertion(doctored_snippet)

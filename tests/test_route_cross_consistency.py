"""三层决策器组合语义回归钉：route_decision × loop_route × workflow_route。

实测基线：2026-09-25 @ HEAD 3f6f35c（分支 spec/2026-09-25_add-route-decision-table），
三脚本对全量候选文本实跑后入库；入库语料的任何一条失配都只调语料、不调启发式
（「语料失配只调语料」是本文件的入库纪律）。

四组断言：

(a) 书面不变量——workflow-runner README「Relationship to route_decision.py and
    team-loop」写明该 slot 只激活 explore/build/review 的执行段。在校准语料
    （排除循环收敛形态文本，每条实测 workflowRecommended=True）上断言
    route ∈ {explore, build, review}。

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
    Claude Code 分支自包含委派包（五字段）与四模式验收点。

已知缺口（边界决定本轮不改启发式，仅记录为建议性语义的局限，留待有书面
决策的独立小包）：
- 泛化「批量」单词即可过 workflow_route 推荐阈（score=2）：「批量更新这 3 个配置
  文件的版本号」实测 route=local + workflowRecommended=True，与 README 书面激活域
  （只激活 explore/build/review 执行段）存在张力。workflow_route.py docstring 明示
  「激活只是建议」，该缺口是建议性语义的局限；本文件只钉住现状、守护漂移。
- 循环收敛形态文本（如「iterate until all tests pass」）同样落在 route=local 且
  workflowRecommended=True（review-fix-loop 推荐）；同理只钉住不修复。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
ROUTE_SCRIPT = ROOT / "scripts" / "route_decision.py"
LOOP_SCRIPT = ROOT / "slots" / "team-loop" / "scripts" / "loop_route.py"
WORKFLOW_SCRIPT = ROOT / "slots" / "workflow-runner" / "scripts" / "workflow_route.py"
ORCHESTRATION_DOC = ROOT / "references" / "orchestration.md"
WORKFLOW_README = ROOT / "slots" / "workflow-runner" / "README.md"

ROUTES = ("local", "explore", "build", "review", "external")
LOOP_MODES = ("until-converged", "fixed-rounds", "batch-fanout", "none")
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

# 已知缺口样例（实测 local + workflowRecommended=True，2026-09-25 @ 3f6f35c）：
# 不入库、不修复，仅在此登记；见模块 docstring「已知缺口」。
KNOWN_GAP_TEXTS = (
    "批量更新这 3 个配置文件的版本号",
    "iterate until all tests pass",
)


def test_invariant_corpus_stays_loop_form_free():
    for text in INVARIANT_CORPUS:
        lowered = text.lower()
        for marker in LOOP_FORM_MARKERS:
            assert marker not in lowered, f"校准语料须排除循环收敛形态文本: {text!r} 含 {marker!r}"
    for text in KNOWN_GAP_TEXTS:
        assert text not in INVARIANT_CORPUS, f"已知缺口文本不得混入校准语料: {text!r}"


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


def test_known_gap_texts_stay_pinned_as_local_with_workflow_recommendation(route_decision, loop_route, workflow_route):
    """已知缺口现状钉：local + wf=True 的张力漂移即红（修复须走独立小包并显式改本断言）。"""
    for text in KNOWN_GAP_TEXTS:
        route_payload = route_decision.decide(text)
        workflow_payload = workflow_route.route(text)
        assert route_payload["route"] == "local", f"{text!r} -> {route_payload['route']}"
        assert workflow_payload["workflowRecommended"] is True, f"{text!r} 推荐 semantics 漂移"


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
    ("iterate until all tests pass", "local", True, True),
    ("对这 12 个章节循环抽取元数据，直至校验全部通过", "local", True, False),
    # 已知缺口锚点：泛化「批量」过 wf 推荐阈 -> local + wf=True
    ("批量更新这 3 个配置文件的版本号", "local", True, True),
    # 并行评审扇出：route=review，loop/wf 双推荐
    ("批量评审 20 个模块的鉴权代码，逐个独立审查后汇总", "review", True, True),
    ("多代理并行评审这个风险改动", "review", False, True),
    # 探索扇出
    ("批量梳理支付失败链路，结构不清需要多面并行探索", "explore", True, True),
    ("梳理支付失败链路：这个代码库结构不清，需要多面并行探索", "explore", False, False),
    # 实现扇出与局部实现
    ("批量实现导出功能，前端/后端/测试切片分工", "build", True, True),
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

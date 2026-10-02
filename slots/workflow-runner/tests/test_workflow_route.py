"""workflow_route 路由判定测试：正例路由、反例不路由、配置生成、评审回补盲区。"""

from __future__ import annotations

from workflow_route import route

POSITIVES = [
    ("批量处理这 40 个条目，每个都独立并行完成", "batch-fanout"),
    ("fan out one reviewer per changed file, then synthesize", "batch-fanout"),
    ("对本次 diff 做多维度并行评审，再对发现逐项对抗验证", "parallel-review"),
    (
        "review the changes across correctness/security/perf dimensions, adversarially verify each finding",
        "parallel-review",
    ),
    ("出 3 个独立方案对比评分，从胜者综合", "perspective-panel"),
    ("评审后修复再评审，直到所有审查通过", "review-fix-loop"),
    ("ultracode 全面审查这个模块的缺陷", "parallel-review"),
]

# 评审回补：workflow 打样确认的形态样例（单信号 loop/panel、panel+review 共存、explicit-only）
REVIEWER_REPRO_CASES = [
    ("对这个 PR 评审后修复", "review-fix-loop"),
    ("评审-修复-再验证闭环", "review-fix-loop"),
    ("review it, fix it, then review again", "review-fix-loop"),
    ("fix results and iterate until converge", "review-fix-loop"),
    ("修复并复审，直到审查无问题", "review-fix-loop"),
    ("出多个独立方案对比评分", "perspective-panel"),
    ("judge panel 评一下三个方案", "perspective-panel"),
    ("出 3 个独立方案对比评分择优，再对胜者做多维度评审", "perspective-panel"),
    ("全面审阅这个模块", "parallel-review"),
    ("用 workflow 编排：批量并行评审所有 diff 文件", "parallel-review"),
]

NEGATIVES = [
    "",
    "帮我写一个 hello world",
    "解释一下这段代码",
    "重命名这个变量",
    "单轮就能完成的简单问题",
    "把 spec.md 第二段润色一下",
    # 评审回补：loop/review 词不构成形态时不误触发
    "帮我修复这个 bug",
    "fix resource leak in worker",
]


def test_positive_routes():
    for text, expected_mode in POSITIVES:
        decision = route(text)
        assert decision["workflowRecommended"], f"should recommend: {text!r}"
        assert decision["mode"] == expected_mode, f"{text!r}: expected {expected_mode}, got {decision['mode']}"
        assert decision["score"] >= 2
        assert decision["reason"]
        assert decision["suggestedConfig"] is not None
        assert decision["suggestedConfig"]["mode"] == expected_mode


def test_reviewer_repro_cases():
    """workflow 打样评审确认的全部复现样例在修复后逐条路由正确。"""
    for text, expected_mode in REVIEWER_REPRO_CASES:
        decision = route(text)
        assert decision["workflowRecommended"], f"should recommend: {text!r}"
        assert decision["mode"] == expected_mode, f"{text!r}: expected {expected_mode}, got {decision['mode']}"


def test_single_signal_shapes_cross_gate():
    """单信号形态（1x 命中）也能过门禁——评审第 1 条：权重对称。"""
    for text, mode in [
        ("评审-修复-再验证闭环", "review-fix-loop"),
        ("judge panel", "perspective-panel"),
    ]:
        decision = route(text)
        assert decision["workflowRecommended"], f"single-signal shape must cross gate: {text!r}"
        assert decision["mode"] == mode


def test_negative_routes():
    for text in NEGATIVES:
        decision = route(text)
        assert not decision["workflowRecommended"], f"should not recommend: {text!r}"
        assert decision["mode"] == "none"
        assert decision["suggestedConfig"] is None


def test_empty_text_never_raises():
    decision = route("")
    assert decision == {
        "workflowRecommended": False,
        "mode": "none",
        "score": 0,
        "reason": "空文本",
        "suggestedConfig": None,
        "suggestedSurface": None,
    }


def test_suggested_surface_is_repository_owned():
    """推荐时只给仓库自有执行面；不推荐时为 None。"""
    decision = route("批量处理这 12 个章节，每个都独立并行完成")
    surface = decision["suggestedSurface"]
    assert isinstance(surface, list) and len(surface) == 1
    assert surface[0] == {
        "surface": "subprocess-fanout",
        "owner": "spec-harness",
        "how": "slots/workflow-runner/scripts/workflow_fanout.py --backend <pi|codex>",
    }
    assert "host" not in surface[0]

    off = route("帮我写一个 hello world")
    assert off["suggestedSurface"] is None


def test_config_carries_pattern_map():
    decision = route("批量处理这 12 个章节，每个都独立并行完成")
    config = decision["suggestedConfig"]
    assert config["pattern"] == "pipeline(items, stageA, stageB) — 每条链独立推进，无阶段间屏障"
    assert config["defaultEffort"] == "low"
    assert config["verifyEffort"] == "high"
    assert "agentBudgetGuideline" in config


def test_panel_with_review_co_occurrence_stays_panel():
    """panel+review 共存时保持 panel——评审第 3 条：panel 显式信号优先。"""
    decision = route("出 3 个独立方案对比评分择优，再对胜者做多维度评审")
    assert decision["mode"] == "perspective-panel"

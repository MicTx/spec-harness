"""workflow_route 路由判定测试：正例路由、反例不路由、配置生成、评审回补盲区。

F9 强弱分权收口：泛化「批量」降为弱信号（单独不推荐、共现补分），
四条实测过触发文本进 NEGATIVES，另钉弱+强共现、score==1 与 explicit-only 遗留兼容。
F10 循环收敛拆分：LOOP 形态拆强（评审-修复复合）/弱（裸收敛标记）两表，
`iterate until all tests pass` 等裸收敛文本不再推荐 workflow（归 team-loop），
R3 改判 `fix results and iterate until converge` 入 NEGATIVES，另钉弱收敛+强 fanout
共现时 mode 仍由强信号决定。
"""

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
    ("review the code then fix then review again until converge", "review-fix-loop"),
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
    # F9 收口：四条实测过触发文本（2026-10-08 @ 5de8944 旧「任一命中 ×2 过阈」下 route=local + wf=True）
    "批量",
    "批量改个错别字",
    "批量更新这 3 个配置文件的版本号",
    "先批量看一眼再说",
    # F10 收口：裸收敛标记归弱表，单独不推荐（team-loop until-converged 承接）；
    # `fix results and iterate until converge` 系 R3 裁定（2026-10-07）改判 team-loop
    "iterate until all tests pass",
    "循环执行直到所有测试通过",
    "fix results and iterate until converge",
    "反复修复这个 bug 直到测试通过",
    "修复这个 bug 然后迭代收敛",
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


def test_weak_signal_alone_scores_one_and_never_recommends():
    """F9 强弱分权：泛化「批量」单弱信号只计 1 分，不过阈、不推荐（近阈值 reason 可读）。"""
    decision = route("批量更新这 3 个配置文件的版本号")
    assert decision["score"] == 1
    assert decision["workflowRecommended"] is False
    assert decision["mode"] == "none"
    assert decision["suggestedConfig"] is None
    assert "泛化批量(弱)" in decision["reason"]


def test_weak_and_strong_co_occurrence_still_recommends():
    """F9 弱+强共现：弱信号补分但不主导，mode 由强信号决定。"""
    decision = route("批量更新版本号，并对所有页面都做并行评审")
    assert decision["workflowRecommended"] is True
    assert decision["mode"] == "parallel-review"
    assert decision["score"] == 5
    assert "泛化批量(弱)" in decision["reason"]


def test_explicit_only_request_keeps_legacy_batch_fanout_compat():
    """F9 遗留兼容：仅显式 workflow 词命中时沿用 batch-fanout mode，不新增 explicit mode，reason 标显式请求。"""
    decision = route("用 workflow 编排跑这个任务")
    assert decision["workflowRecommended"] is True
    assert decision["mode"] == "batch-fanout"
    assert "显式" in decision["reason"]
    assert decision["suggestedConfig"] is not None
    assert decision["suggestedConfig"]["mode"] == "batch-fanout"


def test_bare_convergence_weak_signal_alone_never_recommends():
    """F10 弱收敛拆分：裸收敛标记归弱表，单独只计 1 分不过阈，不推荐（交界归 team-loop）。"""
    decision = route("iterate until all tests pass")
    assert decision["score"] == 1
    assert decision["workflowRecommended"] is False
    assert decision["mode"] == "none"
    assert decision["suggestedConfig"] is None
    assert "裸收敛(弱)" in decision["reason"]


def test_bare_convergence_with_strong_fanout_keeps_fanout_mode():
    """F10 弱收敛+强 fanout 共现：弱信号补分但不抢 mode，mode 仍由强信号决定。"""
    decision = route("批量处理这 40 个条目，每个都独立完成，直到全部通过")
    assert decision["workflowRecommended"] is True
    assert decision["mode"] == "batch-fanout"
    assert decision["score"] >= 2
    assert "裸收敛(弱)" in decision["reason"]
    assert decision["suggestedConfig"] is not None
    assert decision["suggestedConfig"]["mode"] == "batch-fanout"

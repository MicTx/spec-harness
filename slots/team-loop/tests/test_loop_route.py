"""loop_route 路由判定测试：正例路由、反例不路由、配置生成。"""

from __future__ import annotations

from loop_route import route

POSITIVES = [
    ("对这 12 个章节循环抽取元数据，直至校验全部通过", "until-converged"),
    ("多轮实现并评审-修复，直到测试通过", "until-converged"),
    ("iterate until all tests pass", "until-converged"),
    ("loop over these files until validation green", "until-converged"),
    ("批量处理这 40 个条目，每个都独立并行完成", "batch-fanout"),
    ("5 个独立调研目标并行 fan-out 完成后汇总", "batch-fanout"),
    ("用子代理团队反复打磨方案直至达标", "until-converged"),
    # check 轮回归：收敛语义无循环关键词 / 英文进行时
    ("把这份报告改到领导满意为止", "until-converged"),
    ("keep iterating on the API until the flaky test passes", "until-converged"),
    ("方案需要多轮打磨到团队认可为止", "until-converged"),
]

NEGATIVES = [
    "",
    "帮我写一个 hello world",
    "解释一下这段代码",
    "重命名这个变量",
    "单轮就能完成的简单问题",
]


def test_positive_routes():
    for text, expected_mode in POSITIVES:
        decision = route(text)
        assert decision["loopRecommended"] is True, text
        assert decision["mode"] == expected_mode, f"{text} -> {decision['mode']}"
        assert decision["suggestedConfig"] is not None
        assert decision["score"] >= 2


def test_negative_no_route():
    for text in NEGATIVES:
        decision = route(text)
        assert decision["loopRecommended"] is False, text
        assert decision["mode"] == "none"
        assert decision["suggestedConfig"] is None


def test_explicit_rounds_config():
    decision = route("分 3 轮推进这个重构任务")
    assert decision["loopRecommended"] is True
    assert decision["mode"] == "fixed-rounds"
    assert decision["suggestedConfig"]["maxRounds"] == 3


def test_batch_mode_concurrency_bump():
    decision = route("批量处理所有文件的元数据，每个都独立完成")
    assert decision["mode"] == "batch-fanout"
    assert decision["suggestedConfig"]["concurrency"] == 4


def test_reason_is_explainable():
    decision = route("循环重试直至通过")
    assert "循环" in decision["reason"]
    decision = route("没什么特征的任务文本")
    assert decision["reason"] == "无循环特征"

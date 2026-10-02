from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "route_decision.py"


def load_module():
    spec = importlib.util.spec_from_file_location("route_decision", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mod():
    return load_module()


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
    )


def test_routes_vocabulary_is_the_canonical_five(mod):
    assert mod.ROUTES == ("local", "explore", "build", "review", "external")


def test_decide_returns_structured_result_for_every_route(mod):
    samples = {
        "local": "修复 README 中的一个错别字",
        "explore": "梳理支付失败链路：这个代码库结构不清，需要多面并行探索",
        "build": "实现支付失败修复，模块边界清晰可并行开发，前端/后端/测试可拆分",
        "review": "代码已经写完，需要并发评审正确性、安全与用户路径",
        "external": "需要隔离 git 分支长时间运行构建测试，外部 worktree 并行执行",
    }
    for expected_route, text in samples.items():
        result = mod.decide(text)
        assert result["route"] == expected_route, text
        assert result["score"] >= 0
        assert result["reason"]
        assert isinstance(result["lanes"], list)


def test_decide_empty_text_fails_structured_not_raises(mod):
    result = mod.decide("")
    assert result["route"] == "local"
    assert result["score"] == 0


def test_decide_never_raises_on_garbage(mod):
    # Any input must produce a structured decision, never an exception.
    for weird in ("!!!", "\x00\x01", "a" * 10000, "🤖🤖"):
        result = mod.decide(weird)
        assert result["route"] in mod.ROUTES


def test_decide_prefers_local_for_small_tight_tasks(mod):
    result = mod.decide("改一行配置")
    assert result["route"] == "local"


def test_cli_outputs_valid_json():
    proc = run_cli("--text", "多代理并行评审这个风险改动")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["route"] in ("local", "explore", "build", "review", "external")
    assert "lanes" in payload


def test_cli_route_argument_overrides_when_in_vocabulary():
    proc = run_cli("--text", "anything", "--route", "review")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["route"] == "review"
    assert payload["override"] is True


def test_cli_rejects_unknown_route_argument():
    proc = run_cli("--text", "anything", "--route", "build（主线程）+ review")
    assert proc.returncode == 2
    assert "local / explore / build / review / external" in proc.stderr


def test_cli_help_lists_vocabulary():
    proc = run_cli("--help")
    assert proc.returncode == 0
    assert "local" in proc.stdout and "external" in proc.stdout


def test_lanes_are_suggested_for_parallel_shapes(mod):
    result = mod.decide("并行探索 API、前端和测试三个面")
    assert result["route"] == "explore"
    assert len(result["lanes"]) >= 2


def test_local_route_has_no_lanes(mod):
    result = mod.decide("修一个 typo")
    assert result["route"] == "local"
    assert result["lanes"] == []


def test_risks_trigger_security_review_lane(mod):
    result = mod.decide("实现支付失败修复，模块边界清晰")
    assert "计费 / 支付" in result["risks"]
    assert "安全评审" in result["lanes"]


def test_security_risk_review_route_still_proposes_security_lane(mod):
    """Review-route texts carrying trust-boundary risks keep the security lane.

    The risk gate (orchestration.md) requires an explicit security review
    lane whenever `risks` is non-empty, independent of the chosen route's
    business lanes.
    """
    for text in ("审查认证登录流程", "review auth session handling"):
        result = mod.decide(text)
        assert result["route"] == "review", text
        assert "安全评审" in result["lanes"], f"risks={result['risks']!r} must add a security lane"


# -- channel_profile: advisory shared-pool signal; never a quantity limit.


def test_channel_profile_flags_shared_pool(mod):
    for text in (
        "单网关单模型，主线程与子 agent 共享池",
        "single gateway single model pool, dispatch subagents",
        "推理通道受限",
    ):
        profile = mod.infer_channel_profile(text)
        assert profile["shared_pool"] is True, text
        assert profile["note"], text


def test_channel_profile_default_is_not_shared(mod):
    for text in ("多代理并行评审这个风险改动", "实现支付失败修复", ""):
        profile = mod.infer_channel_profile(text)
        assert profile["shared_pool"] is False, text


def test_decide_payload_carries_channel_profile_without_quantity(mod):
    result = mod.decide("单网关 单模型 并行探索 API 与前端")
    assert result["channel_profile"]["shared_pool"] is True
    assert "max_parallel" not in json.dumps(result, ensure_ascii=False)
    plain = mod.decide("并行探索 API 与前端")
    assert plain["channel_profile"]["shared_pool"] is False


def test_cli_emits_channel_profile_and_no_quantity_limit():
    proc = run_cli("--text", "单网关 单模型 共享池 通道受限，分发 subagent 评审")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["channel_profile"]["shared_pool"] is True
    assert "max_parallel" not in proc.stdout, "efficiency-first: no quantity clamp may ship"
    assert "lane_budget" not in proc.stdout


def test_cli_override_keeps_channel_profile():
    proc = run_cli("--text", "单模型共享池梳理链路", "--route", "review")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["route"] == "review"
    assert payload["override"] is True
    assert payload["channel_profile"]["shared_pool"] is True, "override must not drop the profile"


# -- CLI override consistency: an overridden route must not keep the
# heuristic's contradictory reason/score/lanes.


def test_cli_override_local_recomputes_route_dependent_fields():
    proc = run_cli("--text", "build API、前端和测试切片", "--route", "local")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["route"] == "local"
    assert payload["override"] is True
    assert payload["lanes"] == [], "local override must clear heuristic implementation lanes"
    assert "route=build" not in payload["reason"], payload["reason"]


def test_cli_override_build_recomputes_lanes_from_text():
    # "测试" is a lane surface; heuristic route is local because of "修一个 typo",
    # the override must still propose the surface lanes a build route would use.
    proc = run_cli("--text", "修一个 typo 并补测试", "--route", "build")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["route"] == "build"
    assert payload["override"] is True
    assert payload["lanes"], "build override must suggest lanes from the text"
    assert "route=local" not in payload["reason"], payload["reason"]


def _write_package(root: Path, slug: str, spec: str, tasks: str) -> None:
    pkg = root / ".spec" / "specs" / slug
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "spec.md").write_text(spec, encoding="utf-8")
    (pkg / "tasks.md").write_text(tasks, encoding="utf-8")
    (pkg / "checklist.md").write_text("**验收结果**：待修复\n", encoding="utf-8")


_PACKAGE_SPEC = """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：更新接口
- **目标用户**：用户
- **核心价值**：价值

## 2. 假设与待确认
### 2.1 已确认事实
- 事实
### 2.2 关键假设
- 假设
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不做什么
## 4. 最小实现路径
- 路径1
- 路径2
- 路径3
"""


def test_cli_package_context_changes_decision_when_open_tasks_change(tmp_path):
    slug = "2026-09-21_fix-orchestration-lifecycle"
    copy_tasks = "- [ ] 改文案\n  - boundary: README.md\n  - verify: rg\n"
    auth_tasks = "- [ ] 认证 token 与 session 存储\n  - boundary: auth/storage\n  - verify: pytest -q\n"
    _write_package(tmp_path, slug, _PACKAGE_SPEC, copy_tasks)
    copy_payload = json.loads(run_cli("--text", "更新接口", "--root", str(tmp_path), "--slug", slug).stdout)
    _write_package(tmp_path, slug, _PACKAGE_SPEC, auth_tasks)
    auth_payload = json.loads(run_cli("--text", "更新接口", "--root", str(tmp_path), "--slug", slug).stdout)
    assert copy_payload != auth_payload, "open-task context must change the decision"
    assert "认证 / 信任边界" in auth_payload["risks"] or "安全评审" in auth_payload["lanes"]


def test_cli_package_explicit_54_route_overrides_heuristic(tmp_path):
    slug = "2026-09-21_fix-orchestration-lifecycle"
    spec = _PACKAGE_SPEC + "\n## 5. 技术决策\n### 5.4 编排策略\n- route: review\n- ownership: 主线程\n"
    tasks = "- [ ] 梳理结构不清的代码库\n  - boundary: src/\n  - verify: notes\n"
    _write_package(tmp_path, slug, spec, tasks)
    payload = json.loads(run_cli("--text", "梳理结构不清的代码库", "--root", str(tmp_path), "--slug", slug).stdout)
    assert payload["route"] == "review"
    assert payload.get("override") is True

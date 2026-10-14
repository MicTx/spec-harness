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


# -- F12（AUD-11）：零信号路由与文本长度无关，一律 local。


def test_zero_signal_long_text_stays_local(mod):
    """零信号一律 local：超长全零文本不再按字数分流到 explore。"""
    result = mod.decide("a" * 10000)
    assert result["route"] == "local"
    assert result["score"] == 0
    assert result["lanes"] == []


def test_same_fix_short_and_long_descriptions_route_identically(mod):
    """长短描述同路由：同一修复的简述与详述必须得到相同 route（AUD-01 实测
    复现对：详述曾因 len>=40 被分流到 explore，删字数回退后两端一致）。"""
    pairs = [
        # 带局部信号：错别字修复的长短两种说法
        (
            "修复 README 中的一个错别字",
            "修复 README 快速开始一节中的一个错别字：把「安装」写成了「按装」，"
            "涉及标题、正文与代码注释共三处，改完检查渲染无差异，不要动其他章节",
        ),
        # 带局部信号：顺手小配置改动
        (
            "顺手改个配置",
            "顺手在默认配置里把超时从 30 秒改成 60 秒，只动这一处数值；注释、格式"
            "与相邻配置项保持原样，改完在本地跑一遍冒烟确认行为符合预期后收工",
        ),
        # 零信号：同一参数调整的简述与详述，两端都必须落 local
        (
            "调整缓存参数",
            "调整缓存参数：把 L1 的容量上限从 256 调到 512，同时把过期时间从 60 秒"
            "拉长到 5 分钟，观察命中率变化并记录前后对比数据，如果命中率先升后降"
            "就回退到原值并说明原因",
        ),
    ]
    for short, long in pairs:
        short_result = mod.decide(short)
        long_result = mod.decide(long)
        assert short_result["route"] == long_result["route"], (
            f"同一修复长短描述路由漂移: {short!r} -> {short_result['route']} / {long!r} -> {long_result['route']}"
        )
        assert short_result["route"] == "local", short


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
    assert copy_payload != auth_payload, "open-task structure must change the decision"
    assert copy_payload["route"] == "local", "单任务小包零强结构信号应落 local"
    assert auth_payload["route"] == "review", "boundary 命中认证风险标签应落 review"
    assert "认证 / 信任边界" in auth_payload["risks"]
    assert "安全评审" in auth_payload["lanes"]
    assert auth_payload["basis"] == "package-structure"


def test_cli_package_explicit_54_route_overrides_heuristic(tmp_path):
    slug = "2026-09-21_fix-orchestration-lifecycle"
    spec = _PACKAGE_SPEC + "\n## 5. 技术决策\n### 5.4 编排策略\n- route: review\n- ownership: 主线程\n"
    tasks = "- [ ] 梳理结构不清的代码库\n  - boundary: src/\n  - verify: notes\n"
    _write_package(tmp_path, slug, spec, tasks)
    payload = json.loads(run_cli("--text", "梳理结构不清的代码库", "--root", str(tmp_path), "--slug", slug).stdout)
    assert payload["route"] == "review"
    assert payload.get("override") is True


# -- F12（AUD-11）：单一入口按包结构决策；--text 只读诊断 --


def _write_structure_package(root: Path, slug: str, task_rows: list[tuple[str, str, str, str]]) -> None:
    """Build a package from (title, boundary, verify, depends-on) rows."""
    chunks = []
    for title, boundary, verify, deps in task_rows:
        block = f"- [ ] {title}\n  - boundary: {boundary}\n  - verify: {verify}\n"
        if deps:
            block += f"  - depends-on: {deps}\n"
        chunks.append(block)
    _write_package(root, slug, _PACKAGE_SPEC, "".join(chunks))


def _run_package(root: Path, slug: str, *extra: str) -> dict:
    proc = run_cli("--root", str(root), "--slug", slug, *extra)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_package_mode_multi_task_wide_boundary_routes_explore(tmp_path):
    """多任务宽边界 → explore：结构指标可解释，输出带依据=包结构字段。"""
    slug = "2026-10-12_structure-route-probe"
    rows = [
        ("实现入口", "只改 scripts/entry.py", "pytest -q scripts", ""),
        ("补测试", "只改 tests/test_entry.py", "pytest -q tests", "task_0001"),
        ("同步文档", "只改 references/entry.md", "rg -c entry references", "task_0001"),
        ("服务端适配", "只改 server/entry_api.py", "pytest -q server", "task_0001"),
        ("清理旧接口", "只改 scripts/legacy.py", "python3 -m compileall scripts", ""),
        ("回归", "只改 tests/test_legacy.py", "pytest -q tests", "task_0005"),
    ]
    _write_structure_package(tmp_path, slug, rows)
    payload = _run_package(tmp_path, slug)
    assert payload["route"] == "explore"
    assert payload["basis"] == "package-structure"
    assert payload["reason"].startswith("依据=包结构")
    assert payload["structure"]["task_count"] == 6
    assert payload["structure"]["module_count"] == 4
    assert set(payload["structure"]["boundary_modules"]) == {"scripts", "tests", "references", "server"}
    assert payload["lanes"], "explore 路由应给出 boundary 模块 lane 草案"


def test_package_mode_mid_size_clean_slices_route_build(tmp_path):
    """中等规模可切分（≥4 任务 × ≥3 模块 × 依赖宽度≤2）→ build。"""
    slug = "2026-10-12_structure-route-probe"
    rows = [
        ("实现核心", "只改 scripts/core.py", "pytest -q scripts", ""),
        ("补测试", "只改 tests/test_core.py", "pytest -q tests", "task_0001"),
        ("同步文档", "只改 references/core.md", "rg -c core references", "task_0001"),
        ("清理", "只改 scripts/old.py", "python3 -m compileall scripts", ""),
    ]
    _write_structure_package(tmp_path, slug, rows)
    payload = _run_package(tmp_path, slug)
    assert payload["route"] == "build"
    assert payload["basis"] == "package-structure"
    assert payload["structure"]["dep_width"] == 1


def test_package_mode_dep_tangle_routes_explore(tmp_path):
    """依赖纠缠（单任务依赖 ≥3）→ explore：宽依赖说明切片不干净，理解先行。"""
    slug = "2026-10-12_structure-route-probe"
    rows = [
        ("基础", "只改 scripts/a.py", "pytest -q", ""),
        ("次级", "只改 scripts/b.py", "pytest -q", ""),
        ("文档", "只改 references/a.md", "rg -c a references", ""),
        ("汇合", "只改 tests/test_all.py", "pytest -q tests", "task_0001, task_0002, task_0003"),
    ]
    _write_structure_package(tmp_path, slug, rows)
    payload = _run_package(tmp_path, slug)
    assert payload["route"] == "explore"
    assert payload["structure"]["dep_width"] == 3
    assert "纠缠" in payload["reason"]


def test_package_mode_small_package_routes_local(tmp_path):
    slug = "2026-10-12_structure-route-probe"
    rows = [("小修", "只改 scripts/one.py", "pytest -q scripts", "")]
    _write_structure_package(tmp_path, slug, rows)
    payload = _run_package(tmp_path, slug)
    assert payload["route"] == "local"
    assert payload["lanes"] == []
    assert "无强结构信号" in payload["reason"]


def test_package_mode_ignores_text_diagnostic_input(tmp_path):
    """--text 不是路由输入：小包即使配探索体描述也落 local（AUD-11 核心裁定）。"""
    slug = "2026-10-12_structure-route-probe"
    _write_structure_package(tmp_path, slug, [("小修", "只改 scripts/one.py", "pytest -q scripts", "")])
    payload = _run_package(tmp_path, slug, "--text", "梳理支付失败链路：这个代码库结构不清，需要多面并行探索")
    assert payload["route"] == "local"
    assert payload["basis"] == "package-structure"


def test_package_mode_counts_only_open_tasks(tmp_path):
    """已完成任务不计入剩余工作形状：勾完 6/7 后只剩单任务小边界应落 local。"""
    slug = "2026-10-12_structure-route-probe"
    rows = [
        ("实现入口", "只改 scripts/entry.py", "pytest -q", ""),
        ("补测试", "只改 tests/test_entry.py", "pytest -q", "task_0001"),
        ("同步文档", "只改 references/entry.md", "rg -c entry", "task_0001"),
        ("服务端适配", "只改 server/entry_api.py", "pytest -q", "task_0001"),
        ("清理旧接口", "只改 scripts/legacy.py", "python3 -m compileall", ""),
        ("回归", "只改 tests/test_legacy.py", "pytest -q", "task_0005"),
        ("收尾", "只改 scripts/final.py", "pytest -q", ""),
    ]
    _write_structure_package(tmp_path, slug, rows)
    pkg = tmp_path / ".spec" / "specs" / slug
    tasks = (pkg / "tasks.md").read_text(encoding="utf-8")
    # 勾掉前 6 个任务，只留「收尾」
    for title in ("实现入口", "补测试", "同步文档", "服务端适配", "清理旧接口", "回归"):
        tasks = tasks.replace(f"- [ ] {title}", f"- [x] {title}")
    (pkg / "tasks.md").write_text(tasks, encoding="utf-8")
    payload = _run_package(tmp_path, slug)
    assert payload["structure"]["task_count"] == 1
    assert payload["route"] == "local"


def test_package_mode_requires_root_and_slug_together():
    proc = run_cli("--root", "/tmp")
    assert proc.returncode == 2
    assert "together" in proc.stderr


def test_cli_without_any_input_fails_closed():
    proc = run_cli()
    assert proc.returncode == 2
    assert "nothing to route" in proc.stderr


def test_package_mode_missing_package_fails_closed(tmp_path):
    """包不可读时 fail closed：绝不退回猜文本（那正是 AUD-11 要消除的语义）。"""
    proc = run_cli("--root", str(tmp_path), "--slug", "2026-99-99_no-such-package")
    assert proc.returncode == 1
    assert "refusing to route from text" in proc.stderr


def test_text_mode_is_marked_diagnostic():
    proc = run_cli("--text", "多代理并行评审这个风险改动")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["basis"] == "text-diagnostic"
    assert payload["route"] == "review"


# -- F12：历史 5.4 兼容——显式 route / opt-out 声明与结构路由的共存结论不变 --


def test_package_mode_54_local_override_beats_structure(tmp_path):
    """显式 `- route: local` 仍可把强结构信号（多任务宽边界）收回 local。"""
    slug = "2026-10-12_structure-route-probe"
    spec = _PACKAGE_SPEC + "\n## 5. 技术决策\n### 5.4 编排策略\n- route: local\n- ownership: 主线程\n"
    rows = [
        ("实现入口", "只改 scripts/entry.py", "pytest -q", ""),
        ("补测试", "只改 tests/test_entry.py", "pytest -q", "task_0001"),
        ("同步文档", "只改 references/entry.md", "rg -c entry", "task_0001"),
        ("服务端适配", "只改 server/entry_api.py", "pytest -q", "task_0001"),
        ("清理旧接口", "只改 scripts/legacy.py", "python3 -m compileall", ""),
        ("回归", "只改 tests/test_legacy.py", "pytest -q", "task_0005"),
    ]
    _write_structure_package(tmp_path, slug, rows)
    pkg = tmp_path / ".spec" / "specs" / slug
    (pkg / "spec.md").write_text(spec, encoding="utf-8")
    payload = _run_package(tmp_path, slug)
    assert payload["route"] == "local"
    assert payload["override"] is True
    assert payload["source"] == "package-5.4"
    assert payload["lanes"] == []


def test_package_mode_54_optout_declarations_do_not_override_or_crash(tmp_path):
    """未启用/适用外等 opt-out 声明不是 route token：路由器不覆盖、不抛错，按结构
    决策（opt-out 的合法性结论由 run 门禁的 orchestration_strategy_errors 判定，
    该层语义本包不动）。"""
    slug = "2026-10-12_structure-route-probe"
    tasks = "- [ ] 小修\n  - boundary: scripts/one.py\n  - verify: pytest -q\n"
    for decl in ("- 未启用：单线执行。", "- 适用外：单会话执行，未启用编排。"):
        spec = _PACKAGE_SPEC + f"\n## 5. 技术决策\n### 5.4 编排策略\n{decl}\n"
        _write_package(tmp_path, slug, spec, tasks)
        payload = _run_package(tmp_path, slug)
        assert payload["route"] == "local", decl
        assert payload["override"] is False, decl

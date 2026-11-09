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


def test_cli_rejects_unknown_route_argument():
    proc = run_cli("--root", "/tmp", "--slug", "x", "--route", "build（主线程）+ review")
    assert proc.returncode == 2
    assert "local / explore / build / review / external" in proc.stderr


def test_cli_help_lists_vocabulary():
    proc = run_cli("--help")
    assert proc.returncode == 0
    assert "local" in proc.stdout and "external" in proc.stdout


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


def _run_package(root: Path, slug: str, *extra: str) -> dict:
    proc = run_cli("--root", str(root), "--slug", slug, *extra)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# -- C3 output contract: the decision is exactly {route, reason, risks} --


def test_output_is_route_reason_risks_only(tmp_path):
    slug = "2026-10-12_structure-route-probe"
    _write_package(
        tmp_path,
        slug,
        _PACKAGE_SPEC,
        "- [ ] 小修\n  - boundary: 只改 scripts/one.py\n  - verify: pytest -q\n",
    )
    payload = _run_package(tmp_path, slug)
    assert set(payload) == {"route", "reason", "risks"}, "decision output must be route/reason/risks only"
    assert payload["route"] in ("local", "explore", "build", "review", "external")
    assert isinstance(payload["reason"], str) and payload["reason"].startswith("依据=包结构")
    assert isinstance(payload["risks"], list)


def test_cli_package_context_changes_decision_when_open_tasks_change(tmp_path):
    slug = "2026-09-21_fix-public-example-09"
    copy_tasks = "- [ ] 改文案\n  - boundary: README.md\n  - verify: rg\n"
    auth_tasks = "- [ ] 认证 token 与 session 存储\n  - boundary: auth/storage\n  - verify: pytest -q\n"
    _write_package(tmp_path, slug, _PACKAGE_SPEC, copy_tasks)
    copy_payload = _run_package(tmp_path, slug)
    _write_package(tmp_path, slug, _PACKAGE_SPEC, auth_tasks)
    auth_payload = _run_package(tmp_path, slug)
    assert copy_payload != auth_payload, "open-task structure must change the decision"
    assert copy_payload["route"] == "local", "单任务小包零强结构信号应落 local"
    assert auth_payload["route"] == "review", "boundary 命中认证风险标签应落 review"
    assert "认证 / 信任边界" in auth_payload["risks"]


def test_cli_package_explicit_54_route_overrides_heuristic(tmp_path):
    slug = "2026-09-21_fix-public-example-09"
    spec = _PACKAGE_SPEC + "\n## 5. 技术决策\n### 5.4 编排策略\n- route: review\n- ownership: 主线程\n"
    tasks = "- [ ] 梳理结构不清的代码库\n  - boundary: src/\n  - verify: notes\n"
    _write_package(tmp_path, slug, spec, tasks)
    payload = _run_package(tmp_path, slug)
    assert payload["route"] == "review"
    assert "显式覆盖 route=review" in payload["reason"]


# -- F12（AUD-11）：单一入口按包结构决策 --


def _write_structure_package(root: Path, slug: str, task_rows: list[tuple[str, str, str, str]]) -> None:
    """Build a package from (title, boundary, verify, depends-on) rows."""
    chunks = []
    for title, boundary, verify, deps in task_rows:
        block = f"- [ ] {title}\n  - boundary: {boundary}\n  - verify: {verify}\n"
        if deps:
            block += f"  - depends-on: {deps}\n"
        chunks.append(block)
    _write_package(root, slug, _PACKAGE_SPEC, "".join(chunks))


def test_package_mode_multi_task_wide_boundary_routes_explore(tmp_path):
    """多任务宽边界 → explore：结构指标可解释。"""
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
    assert payload["reason"].startswith("依据=包结构")


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
    assert payload["reason"].startswith("依据=包结构")


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
    assert "纠缠" in payload["reason"]


def test_package_mode_small_package_routes_local(tmp_path):
    slug = "2026-10-12_structure-route-probe"
    rows = [("小修", "只改 scripts/one.py", "pytest -q scripts", "")]
    _write_structure_package(tmp_path, slug, rows)
    payload = _run_package(tmp_path, slug)
    assert payload["route"] == "local"
    assert "无强结构信号" in payload["reason"]


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
    assert payload["route"] == "local"


def test_package_mode_requires_root_and_slug_together():
    proc = run_cli("--root", "/tmp")
    assert proc.returncode == 2
    assert "--root and --slug" in proc.stderr


def test_cli_without_any_input_fails_closed():
    proc = run_cli()
    assert proc.returncode == 2
    assert "--root and --slug" in proc.stderr


def test_package_mode_missing_package_fails_closed(tmp_path):
    """包不可读时 fail closed：绝不退回猜文本（那正是 AUD-11 要消除的语义）。"""
    proc = run_cli("--root", str(tmp_path), "--slug", "2026-99-99_no-such-package")
    assert proc.returncode == 1
    assert "refusing to route from text" in proc.stderr


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
    assert "显式覆盖 route=local" in payload["reason"]


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

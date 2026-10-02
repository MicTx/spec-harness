#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""
Behavior-level smoke test for the spec skill scripts.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
PYTHON = sys.executable


def run(*args: str) -> str:
    completed = run_result(*args)
    completed.check_returncode()
    return completed.stdout


def run_result(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def replace_all(path: Path, replacements: dict[str, str]) -> None:
    content = path.read_text(encoding="utf-8")
    for source, target in replacements.items():
        if source not in content:
            raise AssertionError(f"expected placeholder not found in {path}: {source}")
        content = content.replace(source, target)
    path.write_text(content, encoding="utf-8")


def mark_all_tasks_done(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    content = content.replace("- [ ]", "- [x]")
    path.write_text(content, encoding="utf-8")


def mark_checklist_passed(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    content = content.replace("- [ ]", "- [x]")
    content = content.replace("**验收结果**：待修复", "**验收结果**：通过")
    replacements = {
        "- 脚本验证：": "- 脚本验证：smoke script assertions passed",
        "- 旧新对比：": "- 旧新对比：fresh package now routes to status instead of run",
        "- 差异边界：": "- 差异边界：scripts/ references/ README.md only",
        "- 行为成效：": "- 行为成效：clarification before execution enforced",
        "- 构建：": "- 构建：python3 -m py_compile scripts/*.py",
        "- 测试：": "- 测试：python3 scripts/smoke_test_spec_skill.py",
        "- 手工验证：": "- 手工验证：route/status/check outputs inspected",
    }
    for source, target in replacements.items():
        content = content.replace(source, target)
    path.write_text(content, encoding="utf-8")


def populate_spec(path: Path) -> None:
    replace_all(
        path,
        {
            "一句话描述项目要解决什么问题": "修复 spec helper scripts 的状态机与模板一致性",
            "谁会使用这个产品或系统": "维护 spec 技能的 agent 与仓库作者",
            "完成后能持续提供什么价值": "让 route/status/check 输出可依赖",
            "- 事实 A": "- 已确认 fresh package 需要先澄清而不是直接执行",
            "- 假设 A": "- 保持标准库实现，不引入第三方依赖",
            "- 问题 A": "- 无",
            # spec.md functional boxes must be checked once delivered — the
            # checker's spec.md checkbox gate fails any unchecked box.
            "- [ ] 功能 A：描述": "- [x] 功能 A：收敛模板源并修补状态机判断",
            "- 明确列出本轮不做的内容": "- 不重写整个 spec workflow",
            "- 最简单可行方案：xxx": "- 最简单可行方案：收敛模板源并修补状态机判断",
            "- 暂不引入：xxx": "- 暂不引入：测试框架和额外配置层",
            "- 不做的抽象/配置化：xxx": "- 不做的抽象/配置化：不引入 DSL 或数据库",
            "- 风险点：xxx -> 缓解措施": "- 风险点：修复破坏旧输出 -> 缓解措施：用 smoke test 覆盖关键分支",
        },
    )


def setup_git_repo(root: Path) -> None:
    """Promote non-Git fixtures to distinct branch-bound packages for Git checks."""
    branch_placeholder = "Git integration branch：`spec/YYYY-MM-DD_<slug>` 或适用外理由"
    for specs_dir in (".spec", "spec-state"):
        for spec_path in sorted((root / specs_dir / "specs").glob("*/spec.md")):
            branch = (
                "spec/test" if spec_path.parent.name == "2026-06-12_smoke-test" else f"spec/{spec_path.parent.name}"
            )
            replace_all(spec_path, {branch_placeholder: f"Git integration branch：`{branch}`"})
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.email", "smoke@test"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Smoke Test"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "smoke init", "-m", "Spec: 2026-06-12_smoke-test"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", "-b", "spec/test"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.invalid/spec.git"],
        cwd=root,
        check=True,
        capture_output=True,
    )


def assert_contains(text: str, needle: str) -> None:
    if needle not in text:
        raise AssertionError(f"expected to find {needle!r} in output:\n{text}")


def log(message: str) -> None:
    """Print a one-line smoke progress marker so a silent exit-0 can never
    masquerade as a real run."""
    print(f"smoke: {message}", flush=True)


def assert_runtime_layout(root: Path) -> None:
    required_paths = (
        "SKILL.md",
        "install.sh",
        "agents/openai.yaml",
        "agents/orchestrator.md",
        "agents/planner.md",
        "hooks/pre-commit",
        "hooks/pre-push",
        "hooks/spec_disk_truth_gate.py",
        "scripts/safe_open_support.py",
        "scripts/issue_closure_support.py",
        "scripts/init_spec_package.py",
        "scripts/read_version.py",
        "scripts/route_spec_package.py",
        "scripts/route_decision.py",
        "scripts/report_spec_package.py",
        "scripts/check_spec_package.py",
        "scripts/check_all_spec_packages.py",
        "scripts/complete_spec_package.py",
        "scripts/doctor_spec_environment.py",
        "scripts/push_spec_package.py",
        "scripts/smoke_test_spec_skill.py",
        "scripts/spec_package_support.py",
        "scripts/slot_registry.py",
        "references/commands.md",
        "references/output-contracts.md",
        "references/orchestration.md",
    )
    for relative in required_paths:
        path = root / relative
        if not path.exists():
            raise AssertionError(f"missing runtime file: {path}")
    registry_path = root / "scripts" / "slot_registry.py"
    registry_spec = importlib.util.spec_from_file_location("runtime_slot_registry", registry_path)
    if registry_spec is None or registry_spec.loader is None:
        raise AssertionError(f"cannot load slot registry: {registry_path}")
    registry = importlib.util.module_from_spec(registry_spec)
    registry_spec.loader.exec_module(registry)
    slots = registry.discover_slots(root)
    expected_slots = {"team-loop", "workflow-runner"}
    actual_slots = {slot.name for slot in slots}
    if not expected_slots <= actual_slots:
        raise AssertionError(f"missing runtime slots: {sorted(expected_slots - actual_slots)}")
    slot_problems = {}
    for slot in slots:
        problems = registry.validate_slot(slot)
        if problems:
            slot_problems[slot.name] = problems
    if slot_problems:
        raise AssertionError(f"invalid runtime slots: {slot_problems}")
    legacy_install = root / "scripts" / "install.sh"
    if legacy_install.exists():
        raise AssertionError(f"legacy runtime file must not exist: {legacy_install}")
    doc = (root / "SKILL.md").read_text(encoding="utf-8")
    if "assemble" in doc or "combat" in doc or "marshal" in doc or "terminal Worker" in doc:
        raise AssertionError("runtime SKILL.md still advertises retired swarm/worker machinery")
    doctor_script = str(root / "scripts" / "doctor_spec_environment.py")
    completed = run_result(doctor_script, "--root", str(REPO_ROOT), "--format", "json")
    if completed.returncode not in (0, 1):
        raise AssertionError(f"doctor exit must be 0 or 1:\n{completed.stdout}\n{completed.stderr}")
    payload = json.loads(completed.stdout)
    ids = {check["id"] for check in payload["checks"]}
    if not {"python.runtime", "skill.layout", "project.spec"} <= ids:
        raise AssertionError(f"doctor check catalog drifted: {sorted(ids)}")
    log("doctor self-check ok")


def assert_doctor_self_check(root: Path) -> None:
    """The shipped doctor must run against its own package and emit a valid report."""
    doctor_script = str(root / "scripts" / "doctor_spec_environment.py")
    completed = run_result(doctor_script, "--root", str(REPO_ROOT), "--format", "json")
    if completed.returncode not in (0, 1):
        raise AssertionError(f"doctor exit must be 0 or 1:\n{completed.stdout}\n{completed.stderr}")
    payload = json.loads(completed.stdout)
    ids = {check["id"] for check in payload["checks"]}
    if not {"python.runtime", "skill.layout", "project.spec"} <= ids:
        raise AssertionError(f"doctor check catalog drifted: {sorted(ids)}")
    log("doctor self-check ok")


def assert_gate_counterexamples() -> None:
    """Behavior probes for T1/T2/T3 that must not regress."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    from check_spec_package import overall_check_passed  # type: ignore
    from spec_package_support import (  # type: ignore
        _is_no_pending_declaration,
        extract_evidence_fields,
        has_command_evidence,
        section_checkboxes,
        section_exists,
    )

    base_spec = """# T - 项目范围
## 1. 问题定义
- **项目目标**：真实目标
- **目标用户**：真实用户
- **核心价值**：真实价值
### 2.1 已确认事实
- 事实一
### 2.2 关键假设
- 假设一
### 2.3 待确认问题
- 无
### 3.3 不在范围内
- 不做X
## 4. 最小实现路径
- 方案一
- 方案二
- 方案三
"""
    tasks = """## s
- [x] done
  - boundary: only a
  - verify: ran true
"""
    bare = """**验收结果**：通过
"""
    if overall_check_passed(base_spec, tasks, bare, slug="2026-07-27_fix-gate"):
        raise AssertionError("bare 验收结果：通过 must not pass overall_check_passed")

    if section_exists("## 行为成效回填\n- [x] a\n", "## 行为成效"):
        raise AssertionError("## 行为成效 must not match ## 行为成效回填")
    if section_checkboxes("## 行为成效回填\n- [x] a\n- [x] b\n", "## 行为成效") != (0, 0):
        raise AssertionError("section_checkboxes must ignore prefix-colliding headings")

    nested_cl = """## 跨载体一致性
- [x] ok
## 行为成效
- [x] ok
## 验收证据
- 外部对标：真实对标说明
- 脚本验证：
  - python3 scripts/smoke_test_spec_skill.py
- 旧新对比：旧弱门禁 -> 新强制脚本证据
- 差异边界：scripts only
- 行为成效：无关改动 0
---
**验收结果**：通过
"""
    fields = extract_evidence_fields(nested_cl)
    if "python3 scripts/smoke_test_spec_skill.py" not in fields.get("脚本验证", ""):
        raise AssertionError(f"nested 脚本验证 not extracted: {fields!r}")
    if not has_command_evidence(nested_cl):
        raise AssertionError("nested script evidence must count as command evidence")
    if not overall_check_passed(base_spec, tasks, nested_cl, slug="2026-07-27_fix-gate"):
        raise AssertionError("package with nested script evidence must pass")
    log("gate counterexamples ok (bare fail / heading / nested evidence)")

    # Negated pending vocabulary inside a 无-declaration is a closure note,
    # not a live blocker (archived package false positive).
    negated_decl = (
        "无（前端运行时外部 connect 路径已逐文件排查为零，见 2.1；"
        "Esri 瓦片未来若改用 fetch 加载需同步调整 CSP，已记入第 7 节风险而非待确认项）"
    )
    if not _is_no_pending_declaration(negated_decl):
        raise AssertionError("negated 待确认项 inside 无-declaration must not stay pending")
    # Real blockers still stay pending, including separated pending verbs.
    for live in (
        "无（仍需用户授权后才能继续）",
        "无（尚未确认采集端是否受 CSP 约束）",
        "无（待确认 Esri 瓦片加载方式）",
        "无（范围决策待用户授权）",
    ):
        if _is_no_pending_declaration(live):
            raise AssertionError(f"live blocker must stay pending: {live}")
    log("declaration negation probes ok (false positive / live blockers)")


def assert_orchestration_runtime() -> None:
    """The orchestration assets must actually run, not just exist on disk."""
    route_script = str(SCRIPTS_DIR / "route_decision.py")
    completed = run_result(route_script, "--text", "审查认证登录流程")
    if completed.returncode != 0:
        raise AssertionError(f"route_decision must succeed:\n{completed.stdout}\n{completed.stderr}")
    payload = json.loads(completed.stdout)
    if payload.get("route") != "review":
        raise AssertionError(f"expected review route, got {payload!r}")
    if "安全评审" not in payload.get("lanes", []):
        raise AssertionError(f"auth risk must add a security lane, got {payload!r}")
    rejected = run_result(route_script, "--text", "anything", "--route", "build + review")
    if rejected.returncode != 2:
        raise AssertionError(f"multi-token --route must exit 2, got {rejected.returncode}: {rejected.stderr}")
    log("orchestration runtime ok")


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="spec-smoke-"))
    try:
        assert_runtime_layout(REPO_ROOT)
        assert_contains((REPO_ROOT / "references" / "commands.md").read_text(encoding="utf-8"), "`/spec:goal`")
        assert_contains((REPO_ROOT / "references" / "output-contracts.md").read_text(encoding="utf-8"), "`/spec:goal`")
        log("runtime layout ok")
        assert_doctor_self_check(REPO_ROOT)
        assert_gate_counterexamples()
        assert_orchestration_runtime()
        run(
            str(SCRIPTS_DIR / "init_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
            "--title",
            "Smoke Test",
        )
        log("init created task package")
        package_dir = temp_root / ".spec" / "specs" / "2026-06-12_smoke-test"
        spec_path = package_dir / "spec.md"
        tasks_path = package_dir / "tasks.md"
        checklist_path = package_dir / "checklist.md"

        run(
            str(SCRIPTS_DIR / "init_spec_package.py"),
            "--root",
            str(temp_root),
            "--specs-dir",
            "spec-state",
            "--slug",
            "2026-06-12_custom-specs",
            "--title",
            "Custom Specs",
        )
        custom_package = temp_root / "spec-state" / "specs" / "2026-06-12_custom-specs"

        # Second default-root package for multi-status overview.
        run(
            str(SCRIPTS_DIR / "init_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_second-pack",
            "--title",
            "Second Pack",
        )
        multi_status = run(
            str(SCRIPTS_DIR / "report_spec_package.py"),
            "--root",
            str(temp_root),
            "--view",
            "status",
        )
        assert_contains(multi_status, "## 项目概览")
        assert_contains(multi_status, "Smoke Test：已完成 0/7")
        assert_contains(multi_status, "Second Pack：已完成 0/7")
        log("multi-package status overview ok")

        fresh_route = run(
            str(SCRIPTS_DIR / "route_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
        )
        assert_contains(fresh_route, "# Smoke Test")
        assert_contains(fresh_route, "## 项目进展")
        assert_contains(fresh_route, "项目定义不完整")
        assert "spec.md" not in fresh_route
        assert "/spec:" not in fresh_route
        fresh_route_state = run(
            str(SCRIPTS_DIR / "route_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
            "--format",
            "json",
        )
        assert_contains(fresh_route_state, '"stage": "status"')

        fresh_status = run(
            str(SCRIPTS_DIR / "report_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
            "--view",
            "status",
        )
        assert_contains(fresh_status, "# Smoke Test")
        assert_contains(fresh_status, "## 项目进展")
        assert_contains(fresh_status, "项目定义不完整")
        assert "spec.md" not in fresh_status
        assert "## 流水线" not in fresh_status

        fresh_check_result = run_result(
            str(SCRIPTS_DIR / "check_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
        )
        if fresh_check_result.returncode == 0:
            raise AssertionError("failed spec check must return a non-zero exit status")
        assert_contains(fresh_check_result.stdout, "# Smoke Test")
        assert_contains(fresh_check_result.stdout, "## 需要关注")
        assert_contains(fresh_check_result.stdout, "项目尚未完成验收")

        all_check_result = run_result(
            str(SCRIPTS_DIR / "check_all_spec_packages.py"),
            "--root",
            str(temp_root),
            "--specs-dir",
            "spec-state",
        )
        if all_check_result.returncode == 0:
            raise AssertionError("repository-wide check must fail for an incomplete active package")
        assert_contains(all_check_result.stdout, "2026-06-12_smoke-test")
        assert_contains(all_check_result.stdout, "2026-06-12_custom-specs")

        setup_git_repo(temp_root)

        blocked_push = run_result(
            str(SCRIPTS_DIR / "push_spec_package.py"),
            "--root",
            str(temp_root),
            "--branch",
            "spec/test",
            "--all-packages",
        )
        if blocked_push.returncode == 0:
            raise AssertionError("push script must reject incomplete active Spec packages")
        assert_contains(blocked_push.stderr, "2026-06-12_smoke-test")
        # custom-specs lives under spec-state/ which push does not auto-discover
        # (only .spec/.trae are auto-discovered); push without --specs-dir
        # correctly ignores it.

        incomplete_archive = run_result(
            str(SCRIPTS_DIR / "complete_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
            "--allow-incomplete",
            "--archive",
        )
        if incomplete_archive.returncode == 0:
            raise AssertionError("--allow-incomplete must never archive a package")
        assert_contains(incomplete_archive.stderr, "cannot be combined with --archive")
        log("fresh package routes to status (clarification-first)")

        populate_spec(spec_path)
        active_route = run(
            str(SCRIPTS_DIR / "route_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
        )
        assert_contains(active_route, "固化问题定义、关键假设与非目标：进行")
        assert "/spec:" not in active_route
        active_route_state = run(
            str(SCRIPTS_DIR / "route_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
            "--format",
            "json",
        )
        assert_contains(active_route_state, '"stage": "run"')

        mark_all_tasks_done(tasks_path)
        check_route = run(
            str(SCRIPTS_DIR / "route_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
        )
        unchecked = checklist_path.read_text().count("- [ ]")
        assert_contains(check_route, f"项目尚有 {unchecked} 项验收未完成")
        check_route_state = run(
            str(SCRIPTS_DIR / "route_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
            "--format",
            "json",
        )
        assert_contains(check_route_state, '"stage": "check"')
        log("populated spec -> run; tasks done -> check")

        mark_checklist_passed(checklist_path)
        populate_spec(custom_package / "spec.md")
        mark_all_tasks_done(custom_package / "tasks.md")
        mark_checklist_passed(custom_package / "checklist.md")
        # Second package must also pass repo-wide check.
        second = temp_root / ".spec" / "specs" / "2026-06-12_second-pack"
        populate_spec(second / "spec.md")
        mark_all_tasks_done(second / "tasks.md")
        mark_checklist_passed(second / "checklist.md")
        passed_check_result = run_result(
            str(SCRIPTS_DIR / "check_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
        )
        if passed_check_result.returncode != 0:
            raise AssertionError(
                f"passed spec check must return zero:\n{passed_check_result.stdout}\n{passed_check_result.stderr}"
            )
        assert_contains(passed_check_result.stdout, "验收结果：通过")
        assert_contains(passed_check_result.stdout, "有效证据：4 项")
        assert "## 需要关注" not in passed_check_result.stdout
        passed_all_result = run_result(
            str(SCRIPTS_DIR / "check_all_spec_packages.py"),
            "--root",
            str(temp_root),
            "--specs-dir",
            "spec-state",
        )
        if passed_all_result.returncode != 0:
            raise AssertionError(
                f"repository-wide check must pass:\n{passed_all_result.stdout}\n{passed_all_result.stderr}"
            )
        assert_contains(passed_all_result.stdout, "- result: passed")
        done_route = run(
            str(SCRIPTS_DIR / "route_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
        )
        assert_contains(done_route, "- 已完成：7/7")
        assert_contains(done_route, "整理交付结果并完成归档")
        done_route_state = run(
            str(SCRIPTS_DIR / "route_spec_package.py"),
            "--root",
            str(temp_root),
            "--slug",
            "2026-06-12_smoke-test",
            "--format",
            "json",
        )
        assert_contains(done_route_state, '"stage": "done"')
        log("checklist passed -> done")

        export_script = SCRIPTS_DIR / "export_skill_package.py"
        if export_script.exists():
            export_root = temp_root / "exported-skill"
            export_args = [str(export_script), "--output", str(export_root), "--force"]
            identity = Path(os.environ.get("SPEC_SIGNING_IDENTITY", REPO_ROOT / ".watermark-identity.json"))
            key = Path(os.environ.get("SPEC_SIGNING_KEY", REPO_ROOT / ".watermark-key"))
            if not (identity.is_file() and key.is_file()):
                export_args.append("--skip-signing")
            run(*export_args)
            assert_runtime_layout(export_root)
            assert_doctor_self_check(export_root)
            exported_target_root = Path(tempfile.mkdtemp(prefix="spec-export-target-"))
            try:
                completed = run_result(
                    str(export_root / "scripts" / "init_spec_package.py"),
                    "--root",
                    str(exported_target_root),
                    "--slug",
                    "2026-06-12_export-smoke",
                    "--title",
                    "Export Smoke",
                )
                if completed.returncode != 0:
                    raise AssertionError(f"exported init must succeed:\n{completed.stdout}\n{completed.stderr}")
                assert_contains(completed.stdout, "ready:")
            finally:
                shutil.rmtree(exported_target_root, ignore_errors=True)

        log("OK")
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Environment self-check for the spec skill.

Five probes, each owned by exactly one check:

  python.runtime  Python >= 3.9 on PATH
  git.available   git on PATH
  skill.layout    installed package carries the runtime files its profile needs
  project.hooks   the rendered Git gate hooks point at this skill's checker
  hooks.stop      the host's Stop hook is wired (Claude settings reference)

Plus two advisories that never fail the run:
  a leftover ``update-checkpoint.json`` from the retired update mechanism
  (safe to delete), and a retired ``claude_activation_guard`` hook entry still
  referenced by a Claude settings file (safe to remove).

Host install directories are read from each install's own
``install-manifest.json`` (written by install.sh), not re-derived here.

Exit codes: 0 = no failing check, 1 = at least one failing check, 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, List, Optional, Sequence

SKILL_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
MINIMUM_PYTHON = (3, 9)
MANIFEST_NAME = "install-manifest.json"
CHECKER_NAME = "check_all_spec_packages.py"
HOOK_CHECKER_RE = re.compile(r'^checker="([^"]+)"', re.MULTILINE)
ACTIVATION_HOOK = "hooks/claude_activation_guard.py"

# Runtime files every healthy install must carry. The profile stamped into the
# manifest selects the face: core drops the extension components.
REQUIRED_RUNTIME_FILES: tuple[str, ...] = (
    "SKILL.md",
    "install.sh",
    "agents/openai.yaml",
    "hooks/pre-commit",
    "hooks/pre-push",
    "references/commands.md",
    "references/output-contracts.md",
    "references/orchestration.md",
    "references/templates.md",
    "scripts/check_all_spec_packages.py",
    "scripts/check_spec_package.py",
    "scripts/complete_spec_package.py",
    "scripts/doctor_spec_environment.py",
    "scripts/init_spec_package.py",
    "scripts/install_git_hooks.py",
    "scripts/payload_contract.py",
    "scripts/push_spec_package.py",
    "scripts/read_version.py",
    "scripts/route_spec_package.py",
    "scripts/route_decision.py",
    "scripts/slot_registry.py",
    "scripts/spec_package_support.py",
    "scripts/spec_verify.py",
)
CORE_DROP_PREFIXES = ("agents/orchestrator", "agents/planner", "agents/reviewer", "agents/confirmer", "slots/")

PROBE_IDS: tuple[str, ...] = (
    "python.runtime",
    "git.available",
    "skill.layout",
    "project.hooks",
    "hooks.stop",
    "advice.checkpoint",
    "advice.activation",
)


def _home() -> Path:
    return Path.home()


@dataclass
class Check:
    """One doctor finding. Statuses: ok / info / warn / fail / fixed."""

    id: str
    title: str
    status: str
    detail: str
    fix_note: str = ""


@dataclass
class Doctor:
    # Consulted copy of install.sh USER_STAGES; tests/test_stage_inventory.py
    # fails when either list drifts.
    USER_STAGES: ClassVar[tuple[str, ...]] = (
        "new",
        "goal",
        "autoplan",
        "autorun",
        "run",
        "check",
        "done",
        "push",
        "update",
        "status",
        "doctor",
        "organize",
    )

    skill_root: Path
    project_root: Path
    fix: bool = False
    checks: List[Check] = field(default_factory=list)

    def record(self, check_id: str, title: str, status: str, detail: str, fix_note: str = "") -> None:
        for index, existing in enumerate(self.checks):
            if existing.id == check_id:
                self.checks[index] = Check(check_id, title, status, detail, fix_note)
                return
        self.checks.append(Check(check_id, title, status, detail, fix_note))

    def failing(self) -> List[Check]:
        return [check for check in self.checks if check.status == "fail"]

    # -------------------------------------------------------------- manifest

    def install_manifest(self, root: Path | None = None) -> dict:
        """Read install-manifest.json; {} when absent or unreadable."""
        root = root or self.skill_root
        try:
            data = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def install_profile(self) -> str:
        profile = str(self.install_manifest().get("profile", "full")).lower()
        return profile if profile in ("core", "full") else "full"

    def _version(self, root: Path) -> str:
        try:
            match = re.search(
                r'^version\s*=\s*"([^"]+)"',
                (root / "pyproject.toml").read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        except OSError:
            return "unknown"
        return match.group(1) if match else "unknown"

    # ---------------------------------------------------------------- probes

    def check_python_runtime(self) -> None:
        version = ".".join(str(part) for part in sys.version_info[:3])
        if sys.version_info >= MINIMUM_PYTHON:
            self.record("python.runtime", "Python 运行时", "ok", f"Python {version}")
        else:
            self.record(
                "python.runtime",
                "Python 运行时",
                "fail",
                f"Python {version} 低于最低要求 {'.'.join(str(p) for p in MINIMUM_PYTHON)}",
                "安装 Python >= 3.9 并确保 python3 在 PATH 上",
            )

    def check_git_available(self) -> None:
        import shutil

        if shutil.which("git"):
            completed = subprocess.run(["git", "--version"], capture_output=True, text=True, env=self._clean_env())
            self.record("git.available", "Git 可用性", "ok", completed.stdout.strip() or "git")
        else:
            self.record(
                "git.available",
                "Git 可用性",
                "fail",
                "PATH 上没有 git；done/push 与 Git hooks 门禁都依赖它",
                "安装 Git 后重跑 doctor",
            )

    @staticmethod
    def _clean_env() -> dict:
        return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def required_runtime_files(self) -> tuple[str, ...]:
        if self.install_profile() == "core":
            return tuple(name for name in REQUIRED_RUNTIME_FILES if not name.startswith(CORE_DROP_PREFIXES))
        return REQUIRED_RUNTIME_FILES

    def check_skill_layout(self) -> None:
        missing = [name for name in self.required_runtime_files() if not (self.skill_root / name).is_file()]
        version = self._version(self.skill_root)
        profile = self.install_profile()
        if missing:
            self.record(
                "skill.layout",
                "Skill 安装完整性",
                "fail",
                "缺少运行时文件：" + "、".join(missing[:8]) + ("…" if len(missing) > 8 else ""),
                "从源码检出重新执行 bash install.sh",
            )
            return
        # Compile-check every shipped script in memory (no bytecode written).
        broken: List[str] = []
        for script in sorted((self.skill_root / "scripts").glob("*.py")):
            try:
                compile(script.read_bytes(), str(script), "exec", dont_inherit=True)
            except (OSError, SyntaxError, ValueError):
                broken.append(script.name)
        if broken:
            self.record(
                "skill.layout",
                "Skill 安装完整性",
                "fail",
                "脚本编译失败：" + "、".join(broken[:5]),
                "安装可能被截断；重新执行 bash install.sh",
            )
            return
        self.record(
            "skill.layout",
            "Skill 安装完整性",
            "ok",
            f"{len(self.required_runtime_files())} 个运行时文件齐全，版本 {version}（profile={profile}）",
        )

    def _resolve_hooks_dir(self, toplevel: Path) -> Path:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=str(toplevel),
            capture_output=True,
            text=True,
            env=self._clean_env(),
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return toplevel / ".git" / "hooks"
        hooks_dir = Path(completed.stdout.strip())
        if not hooks_dir.is_absolute():
            hooks_dir = (toplevel / hooks_dir).resolve()
        return hooks_dir

    def check_project_hooks(self) -> None:
        toplevel_run = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            env=self._clean_env(),
        )
        if toplevel_run.returncode != 0:
            self.record(
                "project.hooks",
                "Git hooks 指针",
                "info",
                f"{self.project_root} 不是 Git 工作树，跳过（适用外）",
            )
            return
        toplevel = Path(toplevel_run.stdout.strip())
        hooks_dir = self._resolve_hooks_dir(toplevel)
        current_checker = (self.skill_root / "scripts" / CHECKER_NAME).resolve()
        managed: List[str] = []
        problems: List[str] = []
        for name in ("pre-commit", "pre-push"):
            hook = hooks_dir / name
            if not hook.is_file():
                continue
            content = hook.read_text(encoding="utf-8", errors="replace")
            match = HOOK_CHECKER_RE.search(content)
            if match is None or Path(match.group(1)).name != CHECKER_NAME:
                continue  # project-owned hook, not ours
            rendered = Path(match.group(1))
            if not rendered.is_file():
                problems.append(f"{name}：指向不存在的 {rendered}")
            elif not os.access(hook, os.X_OK):
                problems.append(f"{name}：缺少执行权限")
            elif rendered.resolve() != current_checker:
                problems.append(f"{name}：指向另一份 skill 副本（{rendered}）")
            else:
                managed.append(name)
        if problems:
            self.record(
                "project.hooks",
                "Git hooks 指针",
                "fail",
                "；".join(problems),
                f"python3 {self.skill_root / 'scripts' / 'install_git_hooks.py'} --root {toplevel}",
            )
        elif managed:
            self.record("project.hooks", "Git hooks 指针", "ok", "、".join(managed) + "：指向当前 skill")
        else:
            self.record(
                "project.hooks",
                "Git hooks 指针",
                "warn",
                f"未安装磁盘真源 hooks（门禁不生效；hooks 目录 {hooks_dir}）",
                f"python3 {self.skill_root / 'scripts' / 'install_git_hooks.py'} --root {toplevel}",
            )

    def check_stop_hook(self) -> None:
        """The Stop hook converges unfinished packages; it must be wired."""
        settings_files = [
            _home() / ".claude" / "settings.json",
            self.project_root / ".claude" / "settings.json",
        ]
        wired: List[str] = []
        for settings in settings_files:
            try:
                if "claude_stop_guard" in settings.read_text(encoding="utf-8"):
                    wired.append(str(settings))
            except OSError:
                continue
        if wired:
            self.record("hooks.stop", "Stop hook 接线", "ok", "已接线：" + "、".join(sorted(wired)))
        else:
            self.record(
                "hooks.stop",
                "Stop hook 接线",
                "warn",
                "Claude settings 未引用 claude_stop_guard；任务未完成时不会有停止守卫",
                "在 Claude settings 的 Stop hook 中登记 hooks/claude_stop_guard.py",
            )

    # ------------------------------------------------------------ advisories

    def check_checkpoint_leftover(self) -> None:
        """A retired update-checkpoint.json is safe to delete; advisory only."""
        leftovers = sorted(self.project_root.rglob("update-checkpoint.json"))
        if leftovers:
            self.record(
                "advice.checkpoint",
                "遗留 update-checkpoint",
                "info",
                "发现已退役的 update-checkpoint.json：" + "、".join(str(p) for p in leftovers[:3]),
                "确认无需恢复后删除这些文件",
            )

    def check_activation_leftover(self) -> None:
        """A retired claude_activation_guard settings entry is safe to remove."""
        settings_files = [
            _home() / ".claude" / "settings.json",
            self.project_root / ".claude" / "settings.json",
        ]
        wired: List[str] = []
        for settings in settings_files:
            try:
                if ACTIVATION_HOOK in settings.read_text(encoding="utf-8"):
                    wired.append(str(settings))
            except OSError:
                continue
        if wired:
            self.record(
                "advice.activation",
                "已退役的启用守卫",
                "info",
                "Claude settings 仍引用 claude_activation_guard.py：" + "、".join(sorted(wired)),
                "删除这些 settings 中引用该 hook 的条目",
            )

    # ------------------------------------------------------------------- run

    def run(self) -> int:
        self.check_python_runtime()
        self.check_git_available()
        self.check_skill_layout()
        self.check_project_hooks()
        self.check_stop_hook()
        self.check_checkpoint_leftover()
        self.check_activation_leftover()
        return 1 if self.failing() else 0

    # ---------------------------------------------------------------- output

    STATUS_ORDER = ("fail", "warn", "fixed", "ok", "info")
    STATUS_TITLES = {
        "fail": "需要修复",
        "warn": "需要关注",
        "fixed": "已自动修复",
        "ok": "通过",
        "info": "提示",
    }

    def render_markdown(self) -> str:
        lines: List[str] = ["# Spec 环境体检", ""]
        lines.append(f"- skill 版本：{self._version(self.skill_root)}（{self.skill_root}）")
        lines.append(f"- 项目根：{self.project_root}")
        lines.append("")
        for status in self.STATUS_ORDER:
            group = [check for check in self.checks if check.status == status]
            if not group:
                continue
            lines.append(f"## {self.STATUS_TITLES[status]}")
            lines.append("")
            for check in group:
                lines.append(f"- {check.id}：{check.detail}")
                if check.fix_note:
                    lines.append(f"  - 修复：{check.fix_note}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def render_json(self) -> str:
        from datetime import datetime, timezone

        summary = {status: sum(1 for c in self.checks if c.status == status) for status in self.STATUS_ORDER}
        payload = {
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "skillRoot": str(self.skill_root),
            "skillVersion": self._version(self.skill_root),
            "projectRoot": str(self.project_root),
            "checks": [
                {
                    "id": c.id,
                    "title": c.title,
                    "status": c.status,
                    "detail": c.detail,
                    "fixNote": c.fix_note,
                }
                for c in self.checks
            ],
            "summary": summary,
            "exitCode": 1 if self.failing() else 0,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Self-check the spec environment (Python, git, installed skill package, "
            "rendered Git hooks, Stop hook wiring)."
        )
    )
    parser.add_argument("--root", default=".", help="Target project root (default: current directory)")
    parser.add_argument(
        "--skill-root",
        default=None,
        help="Skill package root to inspect (default: the package this script lives in)",
    )
    parser.add_argument("--fix", action="store_true", help="Apply safe repairs (installer-owned paths only)")
    parser.add_argument(
        "--host",
        default=None,
        help="Accepted for backwards compatibility; host scan is manifest-driven now",
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    project_root = Path(args.root).resolve()
    skill_root = Path(args.skill_root).resolve() if args.skill_root else SKILL_ROOT_DEFAULT
    if not skill_root.is_dir():
        print(f"error: skill root is not a directory: {skill_root}", file=sys.stderr)
        return 2
    if not project_root.is_dir():
        print(f"error: project root is not a directory: {project_root}", file=sys.stderr)
        return 2
    doctor = Doctor(skill_root=skill_root, project_root=project_root, fix=args.fix)
    exit_code = doctor.run()
    output = doctor.render_json() if args.format == "json" else doctor.render_markdown()
    print(output)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""
Environment self-check and repair for the spec skill.

Verifies the host foundations the skill depends on (Python, git), the
integrity of the installed skill package, every agent-host installation,
the Claude Code stage command files (including legacy layout leftovers),
the target project's `.spec` skeleton, and rendered Git hook drift.
`--fix` applies the repairs that are safe
to automate: only paths owned by this skill's installer are ever touched,
and anything removed is moved under the shared backup root first.

Exit codes: 0 = no failing check, 1 = at least one failing check
(after fixes), 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

try:
    from slot_registry import discover_slots, validate_slot
except ImportError:  # pragma: no cover - direct source invocation always succeeds
    discover_slots = None  # type: ignore[assignment]
    validate_slot = None  # type: ignore[assignment]

SKILL_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
MINIMUM_PYTHON = (3, 9)
MARKER_FILENAME = ".spec-skill-install"
BACKUP_ROOT_ENV = "BACKUP_ROOT"
STALE_STAGE_ALIASES = ("route", "tasks")
# Sidecar agent contracts published into Claude Code's native agent registry
# (agents live in ~/.claude/agents, not inside the skill directory).
NATIVE_AGENT_NAMES = ("orchestrator", "planner", "reviewer", "confirmer")
BUILTIN_SLOT_NAMES = ("team-loop", "workflow-runner")


def claude_agents_dir() -> Path:
    override = os.environ.get("CLAUDE_AGENTS_DIR")
    if override:
        return Path(override)
    return _home() / ".claude" / "agents"


# Retired stages that once shipped as commands/spec/<stage>.md in the current
# subdirectory layout. The swarm stages were removed with the multi-session
# worker machinery; upgrades and doctor --fix must sweep them like any other
# retired layout artifact.
STALE_SUBDIR_STAGES = ("assemble", "combat", "marshal")
HOOK_CHECKER_FINGERPRINT = "SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER"

# Runtime files every healthy install must carry. Mirrors the layout
# assertion in smoke_test_spec_skill.py; export/build scripts are
# authoring-repo only and intentionally absent from installed copies.
# The orchestration assets (route_decision, orchestration contract, the four
# sidecar agent contracts) are part of the runtime surface since the
# orchestration migration: an install without them cannot route.
REQUIRED_RUNTIME_FILES: Tuple[str, ...] = (
    "SKILL.md",
    "install.sh",
    "agents/openai.yaml",
    "agents/orchestrator.md",
    "agents/planner.md",
    "agents/reviewer.md",
    "agents/confirmer.md",
    "hooks/pre-commit",
    "hooks/pre-push",
    "hooks/spec_disk_truth_gate.py",
    "references/00-readme.md",
    "references/commands.md",
    "references/output-contracts.md",
    "references/orchestration.md",
    "references/templates.md",
    "references/storage-and-archive.md",
    "scripts/check_all_spec_packages.py",
    "scripts/check_spec_package.py",
    "scripts/complete_spec_package.py",
    "scripts/dashboard_support.py",
    "scripts/doctor_spec_environment.py",
    "scripts/generate_changelog.py",
    "scripts/init_spec_package.py",
    "scripts/install_git_hooks.py",
    "scripts/issue_closure_support.py",
    "scripts/push_spec_package.py",
    "scripts/report_spec_package.py",
    "scripts/read_version.py",
    "scripts/route_spec_package.py",
    "scripts/route_decision.py",
    "scripts/safe_open_support.py",
    "scripts/smoke_test_spec_skill.py",
    "scripts/slot_registry.py",
    "scripts/spec_package_support.py",
    "scripts/update_checkpoint.py",
    "scripts/update_checkpoint_support.py",
    "server/README.md",
    "server/install.sh",
    "server/server.py",
)

# Host matrix mirrors install.sh: the same env override names and the same
# default directories, so doctor finds exactly what the installer writes.
# (host_id, label, env_var, default_dir_resolver)
HostSpec = Tuple[str, str, str, Callable[[], Path]]


def _home() -> Path:
    return Path.home()


def _xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(_home() / ".config")))


def _hermes_home() -> Path:
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        return Path(hermes_home)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "hermes"
    return _home() / ".hermes"


def _pi_home() -> Path:
    return Path(os.environ.get("PI_DIR", str(_home() / ".pi")))


def _host_specs() -> List[HostSpec]:
    return [
        ("claude", "Claude Code", "CLAUDE_SKILLS_DIR", lambda: _home() / ".claude" / "skills"),
        (
            "claude-desktop",
            "Claude Desktop",
            "CLAUDE_DESKTOP_SKILLS_DIR",
            lambda: _home() / ".claude-desktop" / "skills",
        ),
        ("codex", "Codex", "CODEX_SKILLS_DIR", lambda: _home() / ".codex" / "skills"),
        ("gemini", "Gemini CLI", "GEMINI_SKILLS_DIR", lambda: _home() / ".gemini" / "skills"),
        ("grok", "Grok Build", "GROK_SKILLS_DIR", lambda: _home() / ".grok" / "skills"),
        ("opencode", "OpenCode", "OPENCODE_SKILLS_DIR", lambda: _xdg_config() / "opencode" / "skills"),
        ("openclaw", "OpenClaw", "OPENCLAW_SKILLS_DIR", lambda: _home() / ".openclaw" / "skills"),
        ("hermes", "Hermes", "HERMES_SKILLS_DIR", lambda: _hermes_home() / "skills"),
        ("pi", "Pi", "PI_SKILLS_DIR", lambda: _pi_home() / "agent" / "skills"),
        ("zcode", "ZCode", "ZCODE_SKILLS_DIR", lambda: _home() / ".zcode" / "skills"),
    ]


def claude_commands_dir() -> Path:
    override = os.environ.get("CLAUDE_COMMANDS_DIR")
    if override:
        return Path(override)
    return _home() / ".claude" / "commands"


def backup_root() -> Path:
    override = os.environ.get(BACKUP_ROOT_ENV)
    if override:
        return Path(override)
    return _home() / ".spec-skill-backups"


class DoctorError(ValueError):
    """A user-actionable doctor argument or path error."""


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
    skill_root: Path
    project_root: Path
    fix: bool = False
    host_filter: Optional[Sequence[str]] = None
    checks: List[Check] = field(default_factory=list)
    fix_log: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------ utils

    def record(self, check_id: str, title: str, status: str, detail: str, fix_note: str = "") -> None:
        # Same-id checks replace: a post-fix re-scan must not stack a stale
        # duplicate entry on top of the pre-fix one.
        for index, existing in enumerate(self.checks):
            if existing.id == check_id:
                self.checks[index] = Check(check_id, title, status, detail, fix_note)
                return
        self.checks.append(Check(check_id, title, status, detail, fix_note))

    def failing(self) -> List[Check]:
        return [check for check in self.checks if check.status == "fail"]

    USER_STAGES: Tuple[str, ...] = (
        "new",
        "goal",
        "run",
        "check",
        "done",
        "push",
        "update",
        "status",
        "doctor",
        "organize",
    )

    def user_stages(self) -> List[str]:
        return list(self.USER_STAGES)

    def _pyproject_version(self, root: Path) -> str:
        pyproject = root / "pyproject.toml"
        try:
            for line in pyproject.read_text(encoding="utf-8").splitlines():
                match = re.match(r'^version\s*=\s*"([^"]+)"', line.strip())
                if match:
                    return match.group(1)
        except (OSError, ValueError):
            pass
        return "unknown"

    def skill_version(self) -> str:
        return self._pyproject_version(self.skill_root)

    def host_skill_dir(self, host: HostSpec) -> Path:
        host_id, _label, env_var, default_resolver = host
        override = os.environ.get(env_var)
        if override:
            return Path(override) / "spec"
        return default_resolver() / "spec"

    def active_hosts(self) -> List[HostSpec]:
        hosts = _host_specs()
        if not self.host_filter:
            return hosts
        wanted = {name.strip() for name in self.host_filter if name.strip()}
        unknown = wanted - {host[0] for host in hosts}
        if unknown:
            raise DoctorError(
                "unknown --host entries: "
                + ", ".join(sorted(unknown))
                + "; valid: "
                + ", ".join(host[0] for host in hosts)
            )
        return [host for host in hosts if host[0] in wanted]

    def backup_path(self, target: Path) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        root = backup_root() / f"doctor-{stamp}"
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", str(target.name))
        candidate = root / safe_name
        counter = 0
        while candidate.exists():
            counter += 1
            candidate = root / f"{safe_name}-{counter}"
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def move_to_backup(self, target: Path) -> Path:
        destination = self.backup_path(target)
        shutil.move(str(target), str(destination / target.name))
        # File targets carry their ownership marker as a sidecar; move it too
        # so no orphan .spec-skill-install is left behind at the live path.
        sidecar = target.with_name(target.name + MARKER_FILENAME)
        if sidecar.is_file() and not sidecar.is_symlink():
            shutil.move(str(sidecar), str(destination / sidecar.name))
        return destination / target.name

    # ------------------------------------------------------------- ownership

    @staticmethod
    def read_marker(target: Path) -> dict:
        """Parse the installer ownership marker for a directory or file target."""
        marker = target / MARKER_FILENAME if target.is_dir() else target.with_name(target.name + ".spec-skill-install")
        if not marker.is_file():
            return {}
        values: dict = {}
        try:
            for line in marker.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip()
        except OSError:
            return {}
        return values

    def is_installer_owned(self, target: Path) -> bool:
        if not (target.exists() or target.is_symlink()):
            return False
        marker = self.read_marker(target)
        return marker.get("installed_by") == "spec/install.sh"

    def is_foreign_skill(self, target: Path) -> bool:
        """A same-name skill from a different source: own SKILL.md, no marker."""
        if target.is_symlink() or not target.is_dir():
            return False
        return (target / "SKILL.md").is_file() and not self.is_installer_owned(target)

    # ---------------------------------------------------------------- checks

    def check_python_runtime(self) -> None:
        version = ".".join(str(part) for part in sys.version_info[:3])
        if sys.version_info >= MINIMUM_PYTHON:
            self.record(
                "python.runtime",
                "Python 运行时",
                "ok",
                f"Python {version}（要求 ≥ {'.'.join(str(p) for p in MINIMUM_PYTHON)}）",
            )
        else:
            self.record(
                "python.runtime",
                "Python 运行时",
                "fail",
                f"Python {version} 低于最低要求 {'.'.join(str(p) for p in MINIMUM_PYTHON)}；安装新版 Python 后重试",
                "安装 Python ≥ 3.9 并确保 python3 在 PATH 上（Windows 注意 Store 存根问题；"
                "脚本使用 3.9 的 str.removeprefix 等特性）",
            )

    def check_git_available(self) -> None:
        git_path = shutil.which("git")
        if git_path:
            completed = subprocess.run(["git", "--version"], capture_output=True, text=True, env=self.clean_env())
            description = completed.stdout.strip() or "git"
            self.record("git.available", "Git 可用性", "ok", description)
        else:
            self.record(
                "git.available",
                "Git 可用性",
                "fail",
                "PATH 上没有 git；done/push 与 Git hooks 门禁都依赖它",
                "安装 Git（https://git-scm.com）后重跑 doctor",
            )

    @staticmethod
    def clean_env() -> dict:
        return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def check_skill_layout(self) -> bool:
        missing = [name for name in REQUIRED_RUNTIME_FILES if not (self.skill_root / name).is_file()]
        slots_root = self.skill_root / "slots"
        for slot_name in BUILTIN_SLOT_NAMES:
            if not (slots_root / slot_name / "manifest.json").is_file():
                missing.append(f"slots/{slot_name}/manifest.json")
        if missing:
            self.record(
                "skill.layout",
                "Skill 安装完整性",
                "fail",
                "缺少运行时文件：" + "、".join(missing),
                "从源码检出重新执行 bash install.sh，或按 README 远程安装一次",
            )
            return False
        slot_problems: List[str] = []
        if discover_slots is not None and validate_slot is not None:
            for slot_dir in discover_slots(self.skill_root):
                slot_problems.extend(f"{slot_dir.name}: {problem}" for problem in validate_slot(slot_dir))
        if slot_problems:
            self.record(
                "skill.layout",
                "Skill 安装完整性",
                "fail",
                "slot 资产无效：" + "；".join(slot_problems[:8]) + ("…" if len(slot_problems) > 8 else ""),
                "重新导出运行时包，确保 slots/<name>/manifest.json、scripts、hooks、tests 全部随包分发",
            )
            return False
        self.record(
            "skill.layout",
            "Skill 安装完整性",
            "ok",
            f"{len(REQUIRED_RUNTIME_FILES)} 个运行时文件与 {len(BUILTIN_SLOT_NAMES)} 个 slot 资产齐全",
        )
        return True

    def check_skill_scripts(self) -> None:
        scripts_dir = self.skill_root / "scripts"
        failures: List[str] = []
        script_paths = sorted(scripts_dir.glob("*.py"))
        script_paths.extend(sorted((self.skill_root / "slots").rglob("*.py")))
        for script in script_paths:
            try:
                py_compile.compile(str(script), doraise=True)
            except py_compile.PyCompileError as exc:
                failures.append(f"{script.name}: {str(exc).splitlines()[-1] if str(exc) else 'compile error'}")
        if failures:
            self.record(
                "skill.scripts",
                "脚本可编译",
                "fail",
                "编译失败：" + "；".join(failures[:5]) + ("…" if len(failures) > 5 else ""),
                "安装可能被截断；重新执行 bash install.sh",
            )
        else:
            script_count = len(script_paths)
            self.record("skill.scripts", "脚本可编译", "ok", f"{script_count} 个脚本全部编译通过")

    def check_skill_marker(self) -> None:
        marker = self.read_marker(self.skill_root)
        if not marker:
            self.record(
                "skill.marker",
                "安装所有权标记",
                "warn",
                "没有 .spec-skill-install 标记（手工复制或第三方分发）；升级与覆盖保护不可用",
                "从源码检出执行 bash install.sh 转为安装器接管",
            )
            return
        source = marker.get("source", "")
        self.record("skill.marker", "安装所有权标记", "ok", f"安装器接管；source={source or '未知'}")
        source_dir = Path(source) if source else None
        if source_dir and source_dir.is_dir() and (source_dir / "SKILL.md").is_file():
            drifted = self._drifted_files(source_dir)
            if drifted:
                self.record(
                    "skill.drift",
                    "与源码差异",
                    "warn",
                    f"{len(drifted)} 个文件与 source 检出不一致："
                    + "、".join(drifted[:5])
                    + ("…" if len(drifted) > 5 else ""),
                    "在源码检出重跑 bash install.sh 刷新安装",
                )
            else:
                self.record("skill.drift", "与源码差异", "ok", "与 source 检出逐文件一致")

    def _drifted_files(self, source_dir: Path) -> List[str]:
        drifted: List[str] = []
        for name in REQUIRED_RUNTIME_FILES:
            installed = self.skill_root / name
            origin = source_dir / name
            if not origin.is_file():
                continue
            if not installed.is_file():
                drifted.append(f"{name}（缺失）")
                continue
            if origin.read_bytes() != installed.read_bytes():
                drifted.append(name)
        return drifted

    def check_hosts_installed(self) -> None:
        findings: List[str] = []
        collisions: List[str] = []
        versions: List[str] = []
        self_dir = self.skill_root.resolve()
        for host in self.active_hosts():
            host_id, label, _env, _default = host
            skill_dir = self.host_skill_dir(host).resolve()
            if skill_dir == self_dir:
                versions.append(f"{label}（当前运行副本，{self.skill_version()}）")
                continue
            if self.is_foreign_skill(skill_dir):
                collisions.append(f"{label}：{skill_dir} 是外来同名 skill")
                continue
            if not skill_dir.is_dir():
                continue
            if (
                not (skill_dir / "SKILL.md").is_file()
                or not (skill_dir / "scripts" / "spec_package_support.py").is_file()
            ):  # noqa: E501
                findings.append(f"{label}：{skill_dir} 目录存在但缺少 SKILL.md/脚本（残缺安装）")
                continue
            version = self._pyproject_version(skill_dir)
            marker = self.read_marker(skill_dir)
            if version == "unknown" and marker.get("version"):
                version = str(marker["version"])
            owned = "安装器接管" if marker else "无标记"
            versions.append(f"{label} {version}（{owned}）")
            changed = []
            for name in REQUIRED_RUNTIME_FILES:
                source, installed = self.skill_root / name, skill_dir / name
                if source.is_file() and (not installed.is_file() or source.read_bytes() != installed.read_bytes()):
                    changed.append(name)
            if changed:
                findings.append(f"{label}：安装内容与当前源码漂移：" + "、".join(changed[:6]))
        if not versions and not collisions and not findings:
            self.record(
                "hosts.installed",
                "宿主安装面",
                "warn",
                "未在任何已知宿主目录发现 spec skill 安装（当前副本可能是导出包）",
                "需要宿主集成时执行 bash install.sh",
            )
            return
        status = "ok"
        if collisions:
            status = "warn"
        if findings:
            status = "warn"
        detail = "；".join(versions + findings + collisions)
        fix_note = ""
        if collisions:
            fix_note = "外来同名 skill 需人工确认：FORCE=1 bash install.sh 会先备份再替换"
        if findings:
            fix_note = (fix_note + "；" if fix_note else "") + "残缺安装请重跑 bash install.sh"
        self.record("hosts.installed", "宿主安装面", status, detail, fix_note)

    def check_claude_commands(self) -> bool:
        claude_dir = self.host_skill_dir(self._find_host("claude"))
        commands_dir = claude_commands_dir()
        has_skill = claude_dir.exists() or claude_dir.is_symlink()
        has_command_files = (commands_dir / "spec").is_dir()
        if not has_skill and not has_command_files:
            # Host not in use: nothing owned by us to verify or repair.
            return True
        if self.is_foreign_skill(claude_dir):
            self.record(
                "claude.commands",
                "Claude 命令文件",
                "info",
                f"{claude_dir} 被外来同名 skill 占用；命令文件检查跳过",
            )
            return True
        stages = self.user_stages()
        command_files = [commands_dir / "spec" / f"{stage}.md" for stage in stages]
        missing = [path.name for path in command_files if not path.is_file()]
        empty = [path.name for path in command_files if path.is_file() and path.stat().st_size == 0]
        stale = self._stale_claude_artifacts(stages)

        problems: List[str] = []
        if missing:
            problems.append(f"缺少阶段命令文件：{', '.join(missing)}（/spec:<stage> 无法触发）")
        if empty:
            problems.append(f"阶段命令文件为空：{', '.join(empty)}")
        if stale:
            stale_list = "、".join(str(item) for item in stale[:5]) + ("…" if len(stale) > 5 else "")
            problems.append("发现旧版布局残留：" + stale_list)

        if not problems:
            self.record(
                "claude.commands",
                "Claude 命令文件",
                "ok",
                f"{len(stages)} 个阶段命令文件齐全，无旧版残留",
            )
            return True

        if self.fix:
            fixed_parts: List[str] = []
            remaining = list(problems)
            if stale:
                removed, skipped = self._remove_stale_artifacts(stale)
                if removed:
                    fixed_parts.append(f"清除残留 {len(removed)} 项（已备份）")
                if skipped:
                    remaining.append(f"非安装器所有的残留需手工清理：{', '.join(str(item) for item in skipped)}")
                else:
                    remaining = [item for item in remaining if not item.startswith("发现旧版布局残留")]
            if missing or empty:
                reinstalled = self._reinstall_claude_host()
                if reinstalled:
                    fixed_parts.append("已重跑 install.sh 再生 Claude 命令文件")
                    remaining = [
                        item
                        for item in remaining
                        if not item.startswith("缺少阶段命令文件") and not item.startswith("阶段命令文件为空")
                    ]
            if fixed_parts and not remaining:
                self.record("claude.commands", "Claude 命令文件", "fixed", "；".join(fixed_parts))
                return True
            if fixed_parts:
                combined = "；".join(fixed_parts) + "；" + "；".join(remaining)
                self.record("claude.commands", "Claude 命令文件", "warn", combined)
                return False
            self.record(
                "claude.commands",
                "Claude 命令文件",
                "warn" if stale else "fail",
                "；".join(remaining),
                self._claude_fix_command(),
            )
            return False

        self.record(
            "claude.commands",
            "Claude 命令文件",
            "fail" if (missing or empty) else "warn",
            "；".join(problems),
            self._claude_fix_command(),
        )
        return False

    def _claude_fix_command(self) -> str:
        return f"INSTALL_HOSTS=claude bash {self.skill_root / 'install.sh'}"

    def check_claude_agents(self) -> None:
        """Verify the native ~/.claude/agents copies of the sidecar contracts.

        Claude Code discovers named subagents from its own agents directory,
        not from a skill's bundled `agents/` folder. The skill copy stays the
        portable contract source; the installer publishes installer-owned
        copies into the native registry so lanes can be spawned by name.

        Activation matches ``check_claude_commands``: only an installer-owned
        Claude skill install is in scope. Leftover user files in
        ``~/.claude/agents``, a ``--host`` that excludes claude, and a foreign
        same-name skill must not fail the doctor.
        """
        if self.host_filter is not None and "claude" not in {name.strip() for name in self.host_filter}:
            return
        skills_claude_dir = self.host_skill_dir(self._find_host("claude"))
        if self.is_foreign_skill(skills_claude_dir):
            self.record(
                "claude.agents",
                "Claude native agents",
                "info",
                f"{skills_claude_dir} 被外来同名 skill 占用；native agent 检查跳过",
            )
            return
        if not self.is_installer_owned(skills_claude_dir):
            return
        agents_dir = claude_agents_dir()
        problems: List[str] = []
        for name in NATIVE_AGENT_NAMES:
            source = self.skill_root / "agents" / f"{name}.md"
            target = agents_dir / f"{name}.md"
            if not target.is_file():
                problems.append(f"缺少 native agent：{target}")
                continue
            if not self.is_installer_owned(target):
                problems.append(f"{target} 是用户自有文件（无安装器标记），未接管")
                continue
            if source.is_file() and target.read_bytes() != source.read_bytes():
                problems.append(f"{name}.md 与当前 skill 源不一致（安装内容漂移）")
        if not problems:
            self.record(
                "claude.agents",
                "Claude native agents",
                "ok",
                f"{len(NATIVE_AGENT_NAMES)} 个编排 sidecar agent 已发布：orchestrator、planner、reviewer、confirmer",
            )
            return
        if self.fix:
            reinstalled = self._reinstall_claude_host()
            if reinstalled:
                remaining: List[str] = []
                for name in NATIVE_AGENT_NAMES:
                    source = self.skill_root / "agents" / f"{name}.md"
                    target = agents_dir / f"{name}.md"
                    if not target.is_file():
                        remaining.append(f"缺少 native agent：{target}")
                    elif not self.is_installer_owned(target):
                        remaining.append(f"{target} 是用户自有文件（无安装器标记），未接管")
                    elif source.is_file() and target.read_bytes() != source.read_bytes():
                        remaining.append(f"{name}.md 与当前 skill 源不一致（安装内容漂移）")
                if not remaining:
                    self.record(
                        "claude.agents",
                        "Claude native agents",
                        "fixed",
                        "已重跑 install.sh 发布 Claude native agents",
                    )
                    return
                self.record(
                    "claude.agents",
                    "Claude native agents",
                    "warn",
                    "已尝试重装；" + "；".join(remaining),
                    self._claude_fix_command(),
                )
                return
        self.record(
            "claude.agents",
            "Claude native agents",
            "fail",
            "；".join(problems),
            self._claude_fix_command(),
        )

    def _stale_claude_artifacts(self, stages: Sequence[str]) -> List[Path]:
        stale: List[Path] = []
        commands_dir = claude_commands_dir()
        skills_dir = self.host_skill_dir(self._find_host("claude"))
        base_command = commands_dir / "spec.md"
        if base_command.exists() or base_command.is_symlink():
            stale.append(base_command)
        for stage in (*stages, *STALE_STAGE_ALIASES):
            colon_command = commands_dir / f"spec:{stage}.md"
            if colon_command.exists() or colon_command.is_symlink():
                stale.append(colon_command)
            alias_dir = skills_dir / f"spec:{stage}"
            if alias_dir.exists() or alias_dir.is_symlink():
                stale.append(alias_dir)
        for stage in STALE_SUBDIR_STAGES:
            subdir_command = commands_dir / "spec" / f"{stage}.md"
            if subdir_command.exists() or subdir_command.is_symlink():
                stale.append(subdir_command)
        return stale

    def _find_host(self, host_id: str) -> HostSpec:
        for host in _host_specs():
            if host[0] == host_id:
                return host
        raise DoctorError(f"unknown host: {host_id}")

    def _remove_stale_artifacts(self, stale: List[Path]) -> Tuple[List[Path], List[Path]]:
        removed: List[Path] = []
        skipped: List[Path] = []
        for target in stale:
            if self.is_installer_owned(target):
                backed_up = self.move_to_backup(target)
                self.fix_log.append(f"removed stale {target} -> {backed_up}")
                removed.append(target)
            else:
                skipped.append(target)
        return removed, skipped

    def _reinstall_claude_host(self) -> bool:
        install_sh = self.skill_root / "install.sh"
        if not install_sh.is_file():
            self.fix_log.append("reinstall skipped: install.sh missing from skill root")
            return False
        bash = shutil.which("bash")
        if not bash:
            self.fix_log.append("reinstall skipped: no bash on PATH; run manually: " + self._claude_fix_command())
            return False
        env = dict(os.environ, INSTALL_HOSTS="claude", SPEC_SKIP_SIGNING="1")
        completed = subprocess.run(
            [bash, str(install_sh)], capture_output=True, text=True, env=env, cwd=str(self.skill_root)
        )
        self.fix_log.append(
            "reinstall claude host: exit "
            + str(completed.returncode)
            + (("\n" + completed.stdout[-500:]) if completed.stdout else "")
            + (("\n" + completed.stderr[-500:]) if completed.stderr else "")
        )
        if completed.returncode != 0:
            return False
        stages = self.user_stages()
        commands_dir = claude_commands_dir()
        return all((commands_dir / "spec" / f"{stage}.md").is_file() for stage in stages)

    def check_zcode_symlink(self) -> None:
        zcode_dir = self.host_skill_dir(self._find_host("zcode"))
        if not zcode_dir.is_symlink():
            return
        target = Path(os.readlink(str(zcode_dir)))
        if not target.is_absolute():
            target = zcode_dir.parent / target
        if target.exists():
            self.record("zcode.symlink", "ZCode 链接", "info", f"{zcode_dir} -> {target}")
        else:
            self.record(
                "zcode.symlink",
                "ZCode 链接",
                "fail",
                f"悬空符号链接：{zcode_dir} -> {target}（目标已不存在）",
                f"删除悬空链接后重跑 INSTALL_HOSTS=zcode bash {self.skill_root / 'install.sh'}",
            )

    def check_project_git(self) -> Optional[Path]:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            env=self.clean_env(),
        )
        if completed.returncode == 0:
            toplevel = Path(completed.stdout.strip())
            self.record("project.git", "项目 Git 仓库", "ok", f"{toplevel}")
            return toplevel
        self.record(
            "project.git",
            "项目 Git 仓库",
            "warn",
            f"{self.project_root} 不是 Git 工作树；done/push 与 hooks 门禁不可用",
            "git init 并完成首次提交，或对已托管仓库运行 doctor",
        )
        return None

    def check_project_spec(self) -> None:
        spec_root = self.project_root / ".spec"
        specs_dir = spec_root / "specs"
        archive_dir = specs_dir / "archive"
        if not spec_root.is_dir():
            if self.fix:
                specs_dir.mkdir(parents=True, exist_ok=True)
                archive_dir.mkdir(parents=True, exist_ok=True)
                self.record("project.spec", "项目 .spec 结构", "fixed", f"已创建 {specs_dir} 与 {archive_dir}")
            else:
                self.record(
                    "project.spec",
                    "项目 .spec 结构",
                    "info",
                    "尚未初始化 .spec（还没有任务包属于正常状态）",
                    "需要时运行带 --fix 的 doctor 创建骨架，或直接开始第一个任务包",
                )
            return
        missing: List[Path] = [path for path in (specs_dir, archive_dir) if not path.is_dir()]
        if missing:
            if self.fix:
                for path in missing:
                    path.mkdir(parents=True, exist_ok=True)
                    self.fix_log.append(f"created {path}")
                self.record(
                    "project.spec",
                    "项目 .spec 结构",
                    "fixed",
                    "已补建：" + "、".join(str(path) for path in missing),
                )
            else:
                self.record(
                    "project.spec",
                    "项目 .spec 结构",
                    "warn",
                    "缺少目录：" + "、".join(str(path) for path in missing),
                    "带 --fix 运行 doctor 自动补建",
                )
            return
        package_count = sum(1 for child in specs_dir.iterdir() if child.is_dir() and child.name != "archive")
        self.record(
            "project.spec",
            "项目 .spec 结构",
            "ok",
            f"结构齐全；活跃任务包 {package_count} 个" + ("（尚无任务包）" if package_count == 0 else ""),
        )

    def check_project_hooks(self, git_toplevel: Optional[Path]) -> None:
        if git_toplevel is None:
            self.record(
                "project.hooks",
                "Git hooks 指针",
                "info",
                "非 Git 仓库，跳过（适用外）",
            )
            return
        hooks_dir = git_toplevel / ".git" / "hooks"
        managed: List[str] = []
        other_copy: List[str] = []
        unmanaged: List[str] = []
        problems: List[str] = []
        current_checker = (self.skill_root / "scripts" / "check_all_spec_packages.py").resolve()
        for name in ("pre-commit", "pre-push"):
            hook = hooks_dir / name
            if not hook.is_file():
                continue
            content = hook.read_text(encoding="utf-8")
            if HOOK_CHECKER_FINGERPRINT not in content:
                unmanaged.append(f"{name}：项目自有 hook（未接管）")
                continue
            match = re.search(r'^checker="([^"]+)"', content, re.MULTILINE)
            rendered = Path(match.group(1)) if match else None
            if rendered is None or "__SPEC_CHECK_ALL_" in str(rendered):
                problems.append(f"{name}：模板未渲染，checker 占位符仍在")
            elif not rendered.is_file():
                problems.append(f"{name}：指向不存在的 {rendered}（skill 已移动/删除）")
            elif rendered.resolve() != current_checker:
                other_copy.append(f"{name}：可用但指向另一份 skill 副本（{rendered}）")
            else:
                managed.append(f"{name}：指向当前 skill")
        if problems:
            if self.fix:
                fixed = self._reinstall_hooks(git_toplevel, problems)
                if fixed:
                    self.record("project.hooks", "Git hooks 指针", "fixed", "已备份旧 hook 并重新渲染指针")
                    return
            self.record(
                "project.hooks",
                "Git hooks 指针",
                "fail",
                "；".join(problems),
                f"python3 {self.skill_root / 'scripts' / 'install_git_hooks.py'} --root {git_toplevel}",
            )
            return
        detail_parts = managed + other_copy + unmanaged
        if not detail_parts:
            self.record(
                "project.hooks",
                "Git hooks 指针",
                "info",
                "未安装磁盘真源 hooks（可选能力）",
                f"需要时运行 python3 {self.skill_root / 'scripts' / 'install_git_hooks.py'} --root {git_toplevel}",
            )
            return
        if other_copy:
            self.record("project.hooks", "Git hooks 指针", "warn", "；".join(detail_parts))
        elif managed:
            self.record("project.hooks", "Git hooks 指针", "ok", "；".join(detail_parts))
        else:
            self.record("project.hooks", "Git hooks 指针", "info", "；".join(detail_parts))

    def _reinstall_hooks(self, git_toplevel: Path, problems: List[str]) -> bool:
        hooks_dir = git_toplevel / ".git" / "hooks"
        stale_ok = True
        for name in ("pre-commit", "pre-push"):
            hook = hooks_dir / name
            if not hook.is_file():
                continue
            content = hook.read_text(encoding="utf-8")
            # Only spec-owned hooks are backed up; a refusal from the
            # installer on project-owned hooks leaves them untouched.
            if HOOK_CHECKER_FINGERPRINT in content:
                backed_up = self.move_to_backup(hook)
                self.fix_log.append(f"backed up stale {hook} -> {backed_up}")
            else:
                stale_ok = False
        if not stale_ok:
            return False
        completed = subprocess.run(
            [sys.executable, str(self.skill_root / "scripts" / "install_git_hooks.py"), "--root", str(git_toplevel)],
            capture_output=True,
            text=True,
        )
        self.fix_log.append(
            "install_git_hooks: exit "
            + str(completed.returncode)
            + (("\n" + completed.stdout[-300:]) if completed.stdout else "")
            + (("\n" + completed.stderr[-300:]) if completed.stderr else "")
        )
        if completed.returncode != 0:
            return False
        return (hooks_dir / "pre-commit").is_file() and (hooks_dir / "pre-push").is_file()

    # ------------------------------------------------------------------- run

    def run(self) -> int:
        self.check_python_runtime()
        self.check_git_available()
        layout_ok = self.check_skill_layout()
        self.check_skill_scripts()
        self.check_skill_marker()
        self.check_hosts_installed()
        if layout_ok:
            self.check_claude_commands()
            self.check_claude_agents()
        self.check_zcode_symlink()
        git_toplevel = self.check_project_git()
        self.check_project_spec()
        self.check_project_hooks(git_toplevel)
        if self.fix and self.fix_log:
            # Repairs (installer re-runs) can change host versions; refresh the
            # host scan so one report never mixes pre-fix and post-fix facts.
            self.check_hosts_installed()
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
        lines.append(f"- skill 版本：{self.skill_version()}（{self.skill_root}）")
        lines.append(f"- 项目根：{self.project_root}")
        lines.append(f"- 修复模式：{'开（--fix）' if self.fix else '关'}")
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
        if self.fix_log:
            lines.append("## 修复日志")
            lines.append("")
            for entry in self.fix_log:
                for line in entry.splitlines():
                    lines.append(f"- {line}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def render_json(self) -> str:
        summary = {status: sum(1 for check in self.checks if check.status == status) for status in self.STATUS_ORDER}
        payload = {
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "fix": self.fix,
            "skillRoot": str(self.skill_root),
            "skillVersion": self.skill_version(),
            "projectRoot": str(self.project_root),
            "checks": [
                {
                    "id": check.id,
                    "title": check.title,
                    "status": check.status,
                    "detail": check.detail,
                    "fixNote": check.fix_note,
                }
                for check in self.checks
            ],
            "summary": summary,
            "fixLog": self.fix_log,
            "exitCode": 1 if self.failing() else 0,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Self-check and repair the spec environment (skill install, host mounts, project skeleton, git hooks)."
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
        help="Comma-separated host ids to scan (default: all known hosts)",
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
    host_filter = [name for name in (args.host.split(",") if args.host else [])]
    doctor = Doctor(
        skill_root=skill_root,
        project_root=project_root,
        fix=args.fix,
        host_filter=host_filter or None,
    )
    try:
        exit_code = doctor.run()
    except DoctorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    output = doctor.render_json() if args.format == "json" else doctor.render_markdown()
    print(output)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

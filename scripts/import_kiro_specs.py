#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""
Import AWS Kiro Feature Specs (.kiro/specs/<name>/) into a Spec Harness
Development Record (.spec/specs/<slug>/).

Default is dry-run: print the planned file tree and mapping table without
writing anything. Pass --apply to write spec.md / tasks.md / checklist.md.
Fidelity first: Kiro design.md and requirements.md originals are preserved
verbatim in spec.md appendices; boundary/verify scaffolds are honest markers,
never fabricated calibration content.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from spec_package_support import (
    add_specs_dir_arg,
    is_development_record_slug,
    load_reference_templates,
    read_text,
    resolve_specs_child,
    resolve_specs_root,
    validate_slug,
    write_text,
)

TOOL_ID = "import_kiro_specs.py"
TOOL_VERSION = "1.0"
SLUG_INFIX = "import-kiro"
REQUIRED_FILE = "requirements.md"
SUPPORTED_FILES = ("requirements.md", "design.md", "tasks.md")


class ImportError_(ValueError):
    """Raised for user-facing import failures (bad source, conflicts, invalid input)."""


@dataclass
class Scenario:
    title: str
    ears: list[str] = field(default_factory=list)


@dataclass
class Requirement:
    title: str
    summary: str = ""
    scenarios: list[Scenario] = field(default_factory=list)
    ears: list[str] = field(default_factory=list)


@dataclass
class KiroTask:
    number: str
    text: str
    done: bool
    details: list[str] = field(default_factory=list)


@dataclass
class KiroSpec:
    name: str
    source_dir: Path
    requirements_text: str
    design_text: str | None
    tasks_text: str
    requirements: list[Requirement]
    purpose: str | None
    tasks: list[KiroTask]

    @property
    def scenario_count(self) -> int:
        return sum(len(req.scenarios) for req in self.requirements)

    @property
    def done_task_count(self) -> int:
        return sum(1 for task in self.tasks if task.done)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def normalize_ears(line: str) -> str | None:
    """Return normalized EARS text for a WHEN/THEN style line, else None."""
    text = line.strip()
    bullet = re.match(r"^[-*]\s+(.*)$", text)
    if bullet:
        text = bullet.group(1)
    text = text.replace("**", "").strip()
    if re.match(r"^(WHEN|WHILE|WHERE|IF|THEN|AND)\b", text, re.I):
        return text
    if re.match(r"^(THE SYSTEM|THE USER)\s+SHALL\b", text, re.I):
        return text
    return None


def parse_requirements(text: str) -> tuple[list[Requirement], str | None]:
    """Parse Kiro requirements.md (EARS notation).

    Supports the canonical Kiro structure (``## Requirements`` with
    ``### Requirement:`` blocks and ``#### Scenario:`` WHEN/THEN bullets) and a
    plain-EARS fallback where WHEN/THE SYSTEM SHALL lines are grouped under the
    nearest markdown heading. Returns (requirements, purpose).
    """
    requirements: list[Requirement] = []
    pseudo: dict[str, Requirement] = {}
    order: list[str] = []
    purpose_lines: list[str] = []
    current_req: Requirement | None = None
    current_scn: Scenario | None = None
    group_title = "EARS 需求"
    in_purpose = False
    canonical = False

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue

        if re.match(r"^#{1,6}\s+Purpose\s*$", stripped, re.I):
            in_purpose = True
            current_scn = None
            continue

        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", stripped)
        if heading:
            in_purpose = False
            current_scn = None
            title = heading.group(1)
            req_match = re.match(r"^Requirement:\s*(.+)$", title)
            scn_match = re.match(r"^Scenario:\s*(.+)$", title)
            if req_match:
                canonical = True
                current_req = Requirement(title=req_match.group(1).strip())
                requirements.append(current_req)
                continue
            if scn_match:
                scenario = Scenario(title=scn_match.group(1).strip())
                if current_req is None:
                    current_req = Requirement(title="需求（隐式）")
                    requirements.append(current_req)
                current_req.scenarios.append(scenario)
                current_scn = scenario
                continue
            if current_req is None and title.lower() != "requirements":
                group_title = title
                if title not in pseudo:
                    pseudo[title] = Requirement(title=title)
                    order.append(title)
            continue

        if in_purpose:
            purpose_lines.append(stripped)
            continue

        ears = normalize_ears(stripped)
        if ears is not None:
            if current_scn is not None:
                current_scn.ears.append(ears)
            elif current_req is not None:
                current_req.ears.append(ears)
            else:
                if group_title not in pseudo:
                    pseudo[group_title] = Requirement(title=group_title)
                    order.append(group_title)
                pseudo[group_title].ears.append(ears)
        elif current_req is not None and current_scn is None:
            if not current_req.summary and not current_req.scenarios and not current_req.ears:
                current_req.summary = stripped

    if not canonical and not requirements:
        requirements = [pseudo[key] for key in order]

    meaningful = [req for req in requirements if req.scenarios or req.ears or req.summary]
    purpose = " ".join(purpose_lines).strip() or None
    return meaningful, purpose


def parse_tasks(text: str) -> list[KiroTask]:
    """Parse Kiro tasks.md checkbox lines (``- [ ] 1. ...``, optionally nested)."""
    tasks: list[KiroTask] = []
    current: KiroTask | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        checkbox = re.match(r"^[-*]\s\[( |x|X)\]\s+(.+)$", stripped)
        if checkbox:
            done = checkbox.group(1).lower() == "x"
            body = checkbox.group(2).strip()
            numbered = re.match(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$", body)
            if numbered:
                number, body_text = numbered.group(1), numbered.group(2).strip()
            else:
                number, body_text = str(len(tasks) + 1), body
            current = KiroTask(number=number, text=body_text, done=done)
            tasks.append(current)
            continue
        if current is not None and not stripped.startswith("#"):
            detail = re.match(r"^[-*]\s+(.+)$", stripped)
            current.details.append(detail.group(1).strip() if detail else stripped)
    return tasks


def extract_purpose(text: str) -> str | None:
    requirements, purpose = parse_requirements(text)
    del requirements
    return purpose


def summarize_design(text: str, limit: int = 8) -> list[str]:
    """Auto-extract section headings and top bullets from Kiro design.md."""
    items: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        heading = re.match(r"^#{2,4}\s+(.+?)\s*$", stripped)
        if heading:
            items.append(heading.group(1))
        else:
            bullet = re.match(r"^[-*]\s+(.+)$", stripped)
            if bullet:
                items.append(bullet.group(1).strip())
        if len(items) >= limit:
            break
    return items


# --------------------------------------------------------------------------- #
# Slug building
# --------------------------------------------------------------------------- #


def normalize_kiro_name(name: str) -> str:
    """Normalize a Kiro spec directory name into a slug-safe token."""
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def build_slug(migration_date: str, name: str) -> str:
    normalized = normalize_kiro_name(name)
    if not normalized:
        raise ImportError_(
            f"cannot build a legal slug from Kiro spec name: {name!r}; "
            "rename the source spec directory to an ASCII name and retry"
        )
    slug = f"{migration_date}_{SLUG_INFIX}-{normalized}"
    validate_slug(slug)
    if not is_development_record_slug(slug):
        raise ImportError_(f"generated slug is not a valid Development Record slug: {slug}")
    return slug


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def fence_for(text: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def standard_labels(count: int) -> list[str]:
    labels: list[str] = []
    value = 0
    for _ in range(count):
        value += 1
        label = ""
        remaining = value
        while remaining:
            remaining, remainder = divmod(remaining - 1, 26)
            label = chr(ord("A") + remainder) + label
        labels.append(label)
    return labels


def requirement_standards(req: Requirement) -> list[tuple[str, str]]:
    """Map one Kiro Requirement into (context, EARS text) success-criteria entries."""
    entries: list[tuple[str, str]] = []
    for scenario in req.scenarios:
        entries.append((f"{req.title} / {scenario.title}", " ".join(scenario.ears).strip()))
    covered = " ".join(ears for scenario in req.scenarios for ears in scenario.ears).lower()
    for ears in req.ears:
        if ears.lower() in covered:
            continue
        entries.append((req.title, ears))
    if not entries:
        entries.append((req.title, req.summary))
    return [(context, ears) for context, ears in entries if context or ears]


def render_spec(spec: KiroSpec, slug: str, migration_date: str) -> str:
    req_count = len(spec.requirements)
    scenario_count = spec.scenario_count
    task_count = len(spec.tasks)
    done_count = spec.done_task_count
    has_design = spec.design_text is not None

    if spec.purpose:
        goal = f"{spec.purpose}（迁移自 Kiro requirements.md Purpose，待人工校准为一句目标）"
    else:
        goal = f"实现 Kiro spec `{spec.name}` 定义的需求（{req_count} 个 Requirement，待人工校准为一句目标）"

    standards: list[tuple[str, str]] = []
    for req in spec.requirements:
        standards.extend(requirement_standards(req))
    labels = standard_labels(len(standards))

    lines: list[str] = []
    lines.append(f"# Kiro 迁移：{spec.name} - 项目范围")
    lines.append("")
    lines.append(
        f"> 来源：Kiro spec `{spec.name}`（`{spec.source_dir}`），迁移日期 {migration_date}，"
        f"工具 `{TOOL_ID} v{TOOL_VERSION}`。"
    )
    lines.append(
        "> 本文件由迁移工具生成：映射内容来自源制品；标注「待人工」的内容为诚实脚手架，"
        "待人工校准补充，不可当作已确认信息。"
    )
    lines.append("")
    lines.append("## 1. 问题定义")
    lines.append(f"- **项目目标**：{goal}")
    lines.append(f"- **目标用户**：Kiro spec `{spec.name}` 的既有干系人与评审人（迁移脚手架，待人工确认）")
    lines.append(
        "- **核心价值**：把 Kiro spec 迁出 `.kiro/` 私有目录，成为可跨 CLI 执行、"
        "带门禁的开放任务包（迁移工具生成，待人工校准）"
    )
    lines.append("")
    lines.append("## 2. 假设与待确认")
    lines.append("")
    lines.append("### 2.1 已确认事实")
    lines.append(
        f"- 源制品 `{spec.source_dir}` 含 requirements.md（{req_count} 个 Requirement、"
        f"{scenario_count} 个 Scenario）与 tasks.md（{task_count} 个任务，已完成 {done_count} 个）"
    )
    design_fact = "design.md 已完整保留于附录 A" if has_design else "源 spec 未提供 design.md，附录 A 省略"
    lines.append(f"- {design_fact}；requirements.md 原文保留于附录 B")
    lines.append(
        f"- 本 Development Record 由 `{TOOL_ID} v{TOOL_VERSION}` 于 {migration_date} 生成，"
        f"slug `{slug}`，映射规则遵循 `references/open-task-package-standard.md`"
    )
    lines.append("")
    lines.append("### 2.2 关键假设")
    lines.append("- 假设 Kiro EARS 语义可完整承载于「3.1 核心功能 + 6. 成功标准」，未映射内容以附录原文为准")
    lines.append("- 假设任务 boundary/verify 脚手架经人工校准后，本包可进入正常执行与门禁流程（工具不伪造校准内容）")
    lines.append("- 假设 Kiro 审批门、Correctness(PBT)、wave 并行等运行时能力无需迁移对位，收敛由本仓 check 门禁承担")
    lines.append("")
    lines.append("### 2.3 待确认问题")
    lines.append("- 无（无需阻塞：迁移脚手架的校准项已列为 tasks.md 阶段一任务）")
    lines.append("")
    lines.append("### 2.4 可选解释与取舍")
    lines.append(
        "- 当前选择：design.md 与 requirements.md 原文完整保留进附录，仅自动抽取摘要与映射 "
        "-> 理由：保真优先，摘要只作导航不作替代"
    )
    lines.append(
        "- 当前选择：boundary/verify 只生成脚手架（`迁移自 Kiro 任务 N，待人工校准` / `待迁移补齐`） "
        "-> 理由：迁移工具不具备源项目上下文，不伪造校准内容"
    )
    lines.append("- 当前选择：迁移单向（Kiro → .spec），不回写 `.kiro/` -> 理由：源制品只读，避免双向漂移")
    lines.append("")
    lines.append("## 3. 功能范围")
    lines.append("")
    lines.append("### 3.1 核心功能（MVP）")
    for req in spec.requirements:
        detail = req.summary or (req.ears[0] if req.ears else "")
        if detail:
            lines.append(f"- [ ] {req.title}：{detail}")
        else:
            lines.append(f"- [ ] {req.title}")
    lines.append("")
    lines.append("### 3.2 扩展功能")
    lines.append("- 无")
    lines.append("")
    lines.append("### 3.3 不在范围内")
    lines.append("- Kiro 审批门、Correctness(PBT)、wave 并行执行等运行时专属能力（无对位，不迁移）")
    lines.append("- `.kiro/steering/` 等非三件套制品（本轮迁移轨道只覆盖 Feature Specs 三件套）")
    lines.append("")
    lines.append("## 4. 最小实现路径")
    lines.append("- 最简单可行方案：以本迁移产物为起点，人工校准脚手架后按 tasks.md 执行")
    lines.append("- 暂不引入：不把 Kiro PBT/审批门映射为本仓门禁（收敛由 check 承担）")
    lines.append("- 不做的抽象/配置化：不做多源（spec-kit/OpenSpec）迁移插件化框架（v0.8+）")
    lines.append("- 这次为什么不做更多：迁移轨道 MVP 以保真迁移为先，语义增强待校准反馈后再议")
    lines.append("")
    lines.append("## 5. 技术决策")
    if has_design:
        lines.append("- 技术栈：沿用源 Kiro design.md 决策（见 5.1 摘要与附录 A 原文，待人工确认）")
    else:
        lines.append("- 技术栈：待人工确认（源 spec 未提供 design.md）")
    lines.append(f"- 本轮允许改动：`.spec/specs/{slug}/` 三件套的迁移脚手架与校准内容")
    lines.append("- 本轮不应触碰：`.kiro/` 源制品（迁移为只读单向）")
    lines.append(f"- Git integration branch：`spec/{slug}`（由迁移工具建议，创建动作由人工或 init 流程完成）")
    lines.append("")
    if has_design:
        lines.append("### 5.1 技术决策摘要（自动抽取自 Kiro design.md，待人工提炼）")
        for item in summarize_design(spec.design_text or ""):
            lines.append(f"- {item}")
    else:
        lines.append("### 5.1 技术决策摘要")
        lines.append("- 源 spec 未提供 design.md，技术决策待人工补充（附录 A 省略）")
    lines.append("")
    lines.append("## 6. 成功标准与验证方式")
    for label, (context, ears) in zip(labels, standards):
        if ears:
            lines.append(f"- 标准 {label}（{context}：{ears}）-> verify: 待迁移补齐")
        else:
            lines.append(f"- 标准 {label}（{context}）-> verify: 待迁移补齐")
    lines.append("")
    lines.append("## 7. 风险与约束")
    lines.append("- 风险点：EARS 语义在中文骨架中的措辞漂移 -> 缓解：WHEN/THEN 原文保留于成功标准括注与附录 B")
    lines.append("- 风险点：boundary/verify 脚手架未校准即开工 -> 缓解：阶段一校准任务强制先行，check 门禁拦截未收敛包")
    lines.append("")
    if has_design:
        fence = fence_for(spec.design_text or "")
        lines.append("## 附录 A：Kiro design.md 原文（迁移保真）")
        lines.append("")
        lines.append(f"{fence}markdown")
        lines.append((spec.design_text or "").strip())
        lines.append(fence)
        lines.append("")
    lines.append("## 附录 B：Kiro requirements.md 原文（迁移保真）")
    lines.append("")
    fence = fence_for(spec.requirements_text)
    lines.append(f"{fence}markdown")
    lines.append(spec.requirements_text.strip())
    lines.append(fence)
    lines.append("")
    return "\n".join(lines)


def render_tasks(spec: KiroSpec, slug: str, migration_date: str) -> str:
    lines: list[str] = []
    lines.append(f"# Kiro 迁移：{spec.name} - 任务拆解")
    lines.append("")
    lines.append("## 使用规则")
    lines.append("- 每个任务都要写清楚 `boundary` 和 `verify`")
    lines.append("- 如果一个任务没有验证方式，就不能开始")
    lines.append("- 发现的可执行问题必须回写本包，并在本轮做完")
    lines.append("- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`")
    lines.append(
        f"- 本包由 `{TOOL_ID} v{TOOL_VERSION}` 于 {migration_date} 自 Kiro spec `{spec.name}` 迁移生成；"
        "Kiro 原任务保持原文与编号，脚手架条目待人工校准"
    )
    lines.append("")
    lines.append("## 阶段一：迁移校准")
    lines.append("- [ ] 校准 spec.md 迁移脚手架（问题定义、假设、技术决策摘要、成功标准 verify）")
    lines.append(f"  - boundary: 只更新 `.spec/specs/{slug}/spec.md` 中标注「待人工」的脚手架内容")
    lines.append("  - verify: `spec.md` 无「待迁移补齐」残留，`check_spec_package.py` 澄清缺口为 0")
    lines.append("- [ ] 校准任务项 boundary/verify 脚手架")
    lines.append(f"  - boundary: 只更新 `.spec/specs/{slug}/tasks.md` 阶段二任务的脚手架条目")
    lines.append("  - verify: `tasks.md` 无「待迁移补齐」残留且每项任务 boundary/verify 齐备")
    lines.append("")
    lines.append("## 阶段二：核心实现（迁移自 Kiro tasks.md）")
    for task in spec.tasks:
        marker = "x" if task.done else " "
        lines.append(f"- [{marker}] {task.number}. {task.text}")
        for detail in task.details:
            lines.append(f"  - {detail}")
        lines.append(f"  - boundary: 迁移自 Kiro 任务 {task.number}，待人工校准")
        lines.append("  - verify: 待迁移补齐")
    if not spec.tasks:
        lines.append("- [ ] 源 Kiro tasks.md 无任务，待人工拆解实现任务")
        lines.append(f"  - boundary: 只在 `.spec/specs/{slug}/tasks.md` 本节追加任务")
        lines.append("  - verify: 追加的任务均带 boundary/verify 且验证方式可执行")
    lines.append("")
    lines.append("## 阶段三：集成与验收")
    lines.append("- [ ] 完成验收检查并补齐文档（迁移包收口）")
    lines.append("  - boundary: 只更新 `checklist.md`、任务勾选与必要修复")
    lines.append(f"  - verify: `python3 scripts/check_spec_package.py --root . --slug {slug}` 退出码 0")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**当前进度**：由脚本计算，无需手动维护")
    lines.append("")
    return "\n".join(lines)


def fill_evidence_line(checklist: str, key: str, value: str) -> str:
    prefix = f"- {key}："
    lines = checklist.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{prefix}{value}"
            break
    return "\n".join(lines)


def render_checklist(template: str, spec: KiroSpec, command: str) -> str:
    title = f"Kiro 迁移：{spec.name}"
    rendered = template.replace("[项目名称]", title)
    rendered = fill_evidence_line(
        rendered,
        "外部对标",
        f"源 Kiro spec（`{spec.source_dir}` 三件套，映射规则见 `references/open-task-package-standard.md`）",
    )
    rendered = fill_evidence_line(rendered, "脚本验证", f"`{command}`（本包由该迁移命令生成）")
    return rendered


# --------------------------------------------------------------------------- #
# Source loading
# --------------------------------------------------------------------------- #


def resolve_source(source: Path) -> Path:
    """Resolve the Kiro source directory; accept .kiro, a specs root, or one spec dir."""
    if not source.exists() or not source.is_dir():
        raise ImportError_(f"Kiro source directory not found: {source}")
    if source.name == ".kiro" and (source / "specs").is_dir():
        return source / "specs"
    return source


def discover_spec_dirs(source: Path) -> list[tuple[str, Path]]:
    """Return (name, dir) pairs for every Kiro spec found under source."""
    if (source / REQUIRED_FILE).is_file():
        return [(source.name, source)]
    specs: list[tuple[str, Path]] = []
    for child in sorted(source.iterdir()):
        if child.is_dir() and not child.is_symlink() and (child / REQUIRED_FILE).is_file():
            specs.append((child.name, child))
    return specs


def load_spec(name: str, spec_dir: Path) -> KiroSpec:
    missing = [filename for filename in ("requirements.md", "tasks.md") if not (spec_dir / filename).is_file()]
    if missing:
        raise ImportError_(f"Kiro spec `{name}` is missing required file(s): {', '.join(missing)} ({spec_dir})")
    try:
        requirements_text = read_text(spec_dir / "requirements.md")
        tasks_text = read_text(spec_dir / "tasks.md")
        design_text = read_text(spec_dir / "design.md") if (spec_dir / "design.md").is_file() else None
    except (OSError, UnicodeError) as exc:
        raise ImportError_(f"cannot read Kiro spec `{name}` under {spec_dir}: {exc}") from exc

    requirements, purpose = parse_requirements(requirements_text)
    if not requirements:
        raise ImportError_(
            f"Kiro spec `{name}` requirements.md has no recognizable EARS/Requirement content ({spec_dir})"
        )
    tasks = parse_tasks(tasks_text)
    return KiroSpec(
        name=name,
        source_dir=spec_dir,
        requirements_text=requirements_text,
        design_text=design_text,
        tasks_text=tasks_text,
        requirements=requirements,
        purpose=purpose,
        tasks=tasks,
    )


# --------------------------------------------------------------------------- #
# Plan / dry-run / apply
# --------------------------------------------------------------------------- #


@dataclass
class ImportPlan:
    spec: KiroSpec
    slug: str
    package_dir: Path
    spec_path: Path
    tasks_path: Path
    checklist_path: Path


def build_plans(specs: list[KiroSpec], specs_root: Path, migration_date: str) -> list[ImportPlan]:
    plans: list[ImportPlan] = []
    seen_slugs: dict[str, str] = {}
    for spec in specs:
        slug = build_slug(migration_date, spec.name)
        if slug in seen_slugs:
            raise ImportError_(
                f"slug conflict: Kiro specs `{seen_slugs[slug]}` and `{spec.name}` both map to {slug}; "
                "rename one source spec or use a different --date"
            )
        seen_slugs[slug] = spec.name
        package_dir = resolve_specs_child(specs_root, "specs", slug)
        if package_dir.exists():
            raise ImportError_(
                f"task package already exists: {package_dir}; use a different --date (YYYY-MM-DD) "
                "or rename the source Kiro spec to change the slug"
            )
        plans.append(
            ImportPlan(
                spec=spec,
                slug=slug,
                package_dir=package_dir,
                spec_path=resolve_specs_child(specs_root, "specs", slug, "spec.md"),
                tasks_path=resolve_specs_child(specs_root, "specs", slug, "tasks.md"),
                checklist_path=resolve_specs_child(specs_root, "specs", slug, "checklist.md"),
            )
        )
    return plans


def print_plan(plan: ImportPlan, index: int, total: int, specs_root: Path) -> None:
    spec = plan.spec
    print(f"[{index}/{total}] Kiro spec: {spec.name} -> {plan.package_dir}")
    print(f"{plan.package_dir.relative_to(specs_root.parent)}/")
    print("├── spec.md")
    print(f"│   ├── 3.1 核心功能 <- requirements.md（{len(spec.requirements)} 个 Requirement）")
    print(f"│   ├── 5.1 技术决策摘要 <- design.md（{'含原文附录 A' if spec.design_text else '缺失，脚手架标注'}）")
    standards_count = sum(len(requirement_standards(req)) for req in spec.requirements)
    print(f"│   ├── 6. 成功标准 <- requirements.md（{standards_count} 条，verify: 待迁移补齐）")
    print("│   └── 附录 <- requirements.md 原文（+ design.md 原文，保真）")
    print(
        f"├── tasks.md <- tasks.md（{len(spec.tasks)} 个任务，已完成 {spec.done_task_count} 个；"
        "boundary/verify 脚手架）"
    )
    print("└── checklist.md（验收清单，外部对标与脚本证据已回填）")
    print("映射表：")
    print("  requirements.md -> spec.md 3.1（Requirement）+ 6.（Scenario/EARS，verify 待迁移补齐）+ 附录 B 原文")
    if spec.design_text:
        print("  design.md      -> spec.md 5.1 摘要 + 附录 A 原文（保真优先）")
    else:
        print("  design.md      -> （源缺失，5.1 脚手架标注待人工补充）")
    print("  tasks.md       -> tasks.md 阶段二（boundary/verify 脚手架，待人工校准）")
    print()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import AWS Kiro Feature Specs (.kiro/specs/<name>/) into a Spec Harness "
            "Development Record (.spec/specs/<slug>/). Default is dry-run; --apply writes files."
        )
    )
    parser.add_argument(
        "source",
        help=(
            "Kiro source: one spec dir (.kiro/specs/<name>), a specs root (.kiro/specs), or the .kiro directory itself"
        ),
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root where the .spec directory lives (default: current directory)",
    )
    add_specs_dir_arg(parser)
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Migration date YYYY-MM-DD used as the slug prefix (default: today)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the imported package (default: dry-run, print the plan only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        migration_date = date.fromisoformat(args.date).isoformat()
    except ValueError:
        print(f"error: --date must be an ISO date YYYY-MM-DD, got: {args.date}", file=sys.stderr)
        return 1

    root = Path(args.root).resolve()
    try:
        specs_root = resolve_specs_root(root, args.specs_dir)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        source = resolve_source(Path(args.source).resolve())
        discovered = discover_spec_dirs(source)
        if not discovered:
            raise ImportError_(f"no Kiro specs found under {source} (expected .kiro/specs/<name>/{REQUIRED_FILE})")
        specs = [load_spec(name, spec_dir) for name, spec_dir in discovered]
        plans = build_plans(specs, specs_root, migration_date)
    except ImportError_ as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.apply:
        print("dry-run（未落盘；追加 --apply 以写入）")
        print(f"specs found: {len(plans)}")
        print()
        for index, plan in enumerate(plans, start=1):
            print_plan(plan, index, len(plans), specs_root)
        return 0

    skill_root = Path(__file__).resolve().parent.parent
    try:
        templates = load_reference_templates(skill_root)
    except (OSError, ValueError) as exc:
        print(f"error: cannot load checklist template: {exc}", file=sys.stderr)
        return 1

    specs_dir_flag = "" if args.specs_dir == ".spec" else f" --specs-dir {args.specs_dir}"
    for plan in plans:
        plan.package_dir.mkdir(parents=True, exist_ok=False)
        write_text(plan.spec_path, render_spec(plan.spec, plan.slug, migration_date))
        write_text(plan.tasks_path, render_tasks(plan.spec, plan.slug, migration_date))
        command = (
            f"python3 scripts/{TOOL_ID} {args.source} --root {args.root}{specs_dir_flag} "
            f"--date {migration_date} --apply"
        )
        write_text(plan.checklist_path, render_checklist(templates["checklist.md"], plan.spec, command))
        print(f"created: {plan.spec_path}")
        print(f"created: {plan.tasks_path}")
        print(f"created: {plan.checklist_path}")
    print(f"imported: {len(plans)} spec(s) -> {specs_root / 'specs'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

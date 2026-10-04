#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""Shared monitoring-dashboard renderer for user-facing Spec stage output."""

from __future__ import annotations

from dataclasses import dataclass, field

HEALTH_OK = "正常"
HEALTH_RISK = "风险"
HEALTH_BLOCKED = "阻塞"
HEALTHS = (HEALTH_OK, HEALTH_RISK, HEALTH_BLOCKED)
_HEALTH_ORDER = {HEALTH_OK: 0, HEALTH_RISK: 1, HEALTH_BLOCKED: 2}

MAIN_STAGES = ("new", "run", "check", "done", "push")

GLYPH_DONE = "done"
GLYPH_ACTIVE = "active"
GLYPH_BLOCKED = "blocked"
GLYPH_PENDING = "pending"
GLYPH_NA = "na"
GLYPH_KINDS = (GLYPH_DONE, GLYPH_ACTIVE, GLYPH_BLOCKED, GLYPH_PENDING, GLYPH_NA)

UNICODE_GLYPHS = {
    GLYPH_DONE: "完成",
    GLYPH_ACTIVE: "进行",
    GLYPH_BLOCKED: "阻塞",
    GLYPH_PENDING: "待办",
    GLYPH_NA: "无",
}
ASCII_GLYPHS = {
    GLYPH_DONE: "done",
    GLYPH_ACTIVE: "now",
    GLYPH_BLOCKED: "blocked",
    GLYPH_PENDING: "todo",
    GLYPH_NA: "n/a",
}

METRIC_COLUMNS = ("进度", "任务带", "阻塞", "待确认", "门禁", "证据")
METRIC_VALUE_COLUMNS = ("阻塞", "待确认", "门禁", "证据")

MAX_STRIP_ITEMS = 20
MAX_DELTA_LINES = 3
DEFAULT_MAX_ALERTS = 5

_PROJECT_GAP_MESSAGES = {
    "`spec.md` 缺少有效项目目标": "尚未明确项目目标",
    "`spec.md` 缺少有效目标用户": "尚未明确目标用户",
    "`spec.md` 缺少有效核心价值": "尚未明确项目价值",
    "`spec.md` 缺少有效区块：### 2.1 已确认事实": "尚未记录已确认的需求事实",
    "`spec.md` 缺少有效区块：### 2.2 关键假设": "尚未记录关键假设",
    "`spec.md` 缺少有效区块：### 2.3 待确认问题": "尚未明确是否还有待确认问题",
    "`spec.md` 缺少有效区块：### 3.3 不在范围内": "尚未明确本轮不做的内容",
    "`spec.md` 缺少有效区块：## 4. 最小实现路径": "尚未确定最小实现路径",
}


def project_gap_message(gap: str) -> str:
    """Translate package diagnostics into project-facing language."""
    return _PROJECT_GAP_MESSAGES.get(gap, gap.replace("`spec.md`", "项目定义").strip())


def worst_health(*healths: str) -> str:
    worst = HEALTH_OK
    for value in healths:
        if value not in _HEALTH_ORDER:
            raise ValueError(f"unknown health: {value}")
        if _HEALTH_ORDER[value] > _HEALTH_ORDER[worst]:
            worst = value
    return worst


def glyph(kind: str, ascii_mode: bool = False) -> str:
    table = ASCII_GLYPHS if ascii_mode else UNICODE_GLYPHS
    return table.get(kind, kind)


def default_pipeline(stage: str) -> dict[str, str]:
    """Pipeline states with ``stage`` active and earlier main stages done."""
    if stage not in MAIN_STAGES:
        return {name: GLYPH_PENDING for name in MAIN_STAGES}
    states: dict[str, str] = {}
    reached = False
    for name in MAIN_STAGES:
        if name == stage:
            states[name] = GLYPH_ACTIVE
            reached = True
        else:
            states[name] = GLYPH_PENDING if reached else GLYPH_DONE
    return states


def fold_strip(kinds: list[str], ascii_mode: bool = False) -> str:
    glyphs = [glyph(kind, ascii_mode) for kind in kinds]
    if len(glyphs) <= MAX_STRIP_ITEMS:
        return " ".join(glyphs)
    parts: list[str] = []
    for kind in (GLYPH_DONE, GLYPH_ACTIVE, GLYPH_BLOCKED, GLYPH_PENDING):
        count = kinds.count(kind)
        if count == 0:
            continue
        mark = glyph(kind, ascii_mode)
        parts.append(f"{mark}x{count}" if count > 1 else mark)
    return " ".join(parts) if parts else glyph(GLYPH_PENDING, ascii_mode)


def task_strip_from_records(tasks) -> tuple[list[str], list[str]]:
    """Build dashboard task rows from tasks.md records, not pipeline stages."""
    kinds: list[str] = []
    labels: list[str] = []
    current_assigned = False
    for task in tasks:
        label = str(getattr(task, "text", "") or "").strip()
        if not label:
            continue
        labels.append(label)
        completed = bool(getattr(task, "is_completed", False) or getattr(task, "completed", False))
        blocked = bool(getattr(task, "is_blocked", False))
        in_progress = bool(getattr(task, "is_explicit_in_progress", False))
        if completed:
            kinds.append(GLYPH_DONE)
        elif blocked:
            kinds.append(GLYPH_BLOCKED)
        elif in_progress and not current_assigned:
            kinds.append(GLYPH_ACTIVE)
            current_assigned = True
        elif not current_assigned:
            kinds.append(GLYPH_ACTIVE)
            current_assigned = True
        else:
            kinds.append(GLYPH_PENDING)
    return kinds, labels


@dataclass
class Dashboard:
    """Render project progress while keeping Spec lifecycle state internal."""

    slug: str
    stage: str
    title: str | None = None
    done: int = 0
    total: int = 0
    health: str = HEALTH_OK
    pipeline: dict[str, str] | None = None
    strip: list[str] = field(default_factory=list)
    strip_labels: list[str] = field(default_factory=list)
    metrics: dict[str, str] = field(default_factory=dict)
    current: str | None = None
    alerts: list[str] = field(default_factory=list)
    delta: list[str] = field(default_factory=list)
    show_delta_slot: bool = False
    next_step: str = ""
    detail: list[str] = field(default_factory=list)
    max_alerts: int = DEFAULT_MAX_ALERTS
    alerts_pointer: str = "项目记录"
    detail_title: str = "交付信息"
    ascii_mode: bool = False
    compact: bool = False

    def _mark(self, kind: str) -> str:
        return glyph(kind, self.ascii_mode)

    def _is_empty_value(self, value: str | None) -> bool:
        if value is None:
            return True
        text = str(value).strip()
        return text == "" or text in {"无", "—", "n/a"}

    def _item(self, label: str, value: str | None = None) -> str | None:
        if value is None:
            if self._is_empty_value(label):
                return None
            return f"- {label}"
        if self._is_empty_value(value):
            return None
        return f"- {label}：{value}"

    def _section(self, title: str, items: list[str | None]) -> list[str]:
        visible = [item for item in items if item]
        if not visible:
            return []
        if self.compact:
            return visible
        return [f"## {title}", "", *visible]

    def _na(self) -> str:
        return self._mark(GLYPH_NA)

    def _header_lines(self) -> list[str]:
        project_title = (self.title or self.slug).strip()
        mark = "" if self.ascii_mode else "# "
        return [f"{mark}{project_title}"]

    def _progress_lines(self) -> list[str]:
        if self.total <= 0:
            return []
        return self._section("项目进展", [self._item("已完成", f"{self.done}/{self.total}")])

    def _verification_scope_lines(self) -> list[str]:
        scope = self.metrics.get("范围", "")
        if not scope or scope == "历史包未声明":
            return []
        return self._section("验证范围", [self._item("级别", scope)])

    def _metric_value(self, column: str) -> str:
        if column == "进度":
            return f"{self.done}/{self.total}"
        return self.metrics.get(column, self._na())

    @property
    def normalized_strip(self) -> list[str]:
        """Display-level strip: only the first active item is kept."""
        seen_active = False
        kinds: list[str] = []
        for kind in self.strip:
            if kind == GLYPH_ACTIVE:
                if seen_active:
                    kinds.append(GLYPH_PENDING)
                    continue
                seen_active = True
            kinds.append(kind)
        return kinds

    def _metrics_lines(self) -> list[str]:
        if self.alerts:
            return []
        items = []
        for label in ("待确认", "阻塞"):
            value = self.metrics.get(label)
            if value and value not in {"0", "通过"}:
                items.append(self._item(label, value))
        return self._section("需要关注", items)

    def _strip_items(self) -> list[str]:
        kinds = self.normalized_strip
        if not kinds:
            return []
        labels = list(self.strip_labels)
        if len(labels) < len(kinds):
            labels.extend([""] * (len(kinds) - len(labels)))
        items: list[str] = []
        for index, kind in enumerate(kinds):
            status = self._mark(kind)
            label = labels[index].strip()
            if not label:
                continue
            item = self._item(label, status)
            if item:
                items.append(item)
        if len(items) > MAX_STRIP_ITEMS:
            extra = len(items) - MAX_STRIP_ITEMS
            overflow = self._item(f"其余 {extra} 项见任务包")
            return items[:MAX_STRIP_ITEMS] + ([overflow] if overflow else [])
        return items

    def _strip_lines(self) -> list[str]:
        if self.compact:
            items = self._strip_items()
            names = [item[2:] for item in items]
            if not names:
                return []
            summary = "；".join(names)
            item = self._item("任务", summary)
            return [item] if item else []
        return self._section("任务", self._strip_items())

    def _labeled_items(self, title: str, values: list[str]) -> list[str]:
        if self.compact:
            return [item for item in (self._item(title, value) for value in values) if item]
        return [item for item in (self._item(value) for value in values) if item]

    def _current_lines(self) -> list[str]:
        return self._section("正在处理", self._labeled_items("正在处理", [self.current] if self.current else []))

    def _alert_lines(self) -> list[str]:
        alerts = [line for line in self.alerts if line and line not in {"—", "无"}]
        if not alerts:
            return []
        hidden = len(alerts) - self.max_alerts
        shown = alerts[: self.max_alerts]
        values = list(shown)
        if hidden > 0:
            values.append(f"其余 {hidden} 项见 {self.alerts_pointer}")
        return self._section("需要关注", self._labeled_items("需要关注", values))

    def _delta_lines(self) -> list[str]:
        if not self.delta:
            return []
        shown = self.delta[:MAX_DELTA_LINES]
        return self._section("本轮完成", self._labeled_items("本轮完成", shown))

    def _next_lines(self) -> list[str]:
        next_step = self.next_step.strip()
        if not next_step or (self.current and next_step == self.current.strip()):
            return []
        return self._section("接下来", self._labeled_items("接下来", [next_step]))

    def _detail_lines(self) -> list[str]:
        items = []
        for line in self.detail:
            if not line:
                continue
            items.append(line if line.startswith("- ") else self._item(line))
        return self._section(self.detail_title, items) if items else []

    def _join_blocks(self, blocks: list[list[str]]) -> str:
        parts: list[str] = []
        for block in blocks:
            if not block:
                continue
            if parts and not self.compact:
                parts.append("")
            parts.extend(block)
        return "\n".join(parts)

    def render(self) -> str:
        return self._join_blocks(
            [
                self._header_lines(),
                self._progress_lines(),
                self._verification_scope_lines(),
                self._strip_lines(),
                self._current_lines(),
                self._metrics_lines(),
                self._alert_lines(),
                self._delta_lines(),
                self._next_lines(),
                self._detail_lines(),
            ]
        )

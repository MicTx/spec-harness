#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
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
    GLYPH_DONE: "done",
    GLYPH_ACTIVE: "in progress",
    GLYPH_BLOCKED: "blocked",
    GLYPH_PENDING: "todo",
    GLYPH_NA: "n/a",
}
ASCII_GLYPHS = {
    GLYPH_DONE: "done",
    GLYPH_ACTIVE: "now",
    GLYPH_BLOCKED: "blocked",
    GLYPH_PENDING: "todo",
    GLYPH_NA: "n/a",
}

METRIC_COLUMNS = ("Progress", "Tasks", "Blocked", "Pending decisions", "Gate", "Evidence")
METRIC_VALUE_COLUMNS = ("Blocked", "Pending decisions", "Gate", "Evidence")
_METRIC_ALIASES = {
    "Blocked": "阻塞",
    "Pending decisions": "待确认",
    "Gate": "门禁",
    "Evidence": "证据",
    "Scope": "范围",
}

MAX_STRIP_ITEMS = 20
MAX_DELTA_LINES = 3
DEFAULT_MAX_ALERTS = 5

_PROJECT_GAP_MESSAGES = {
    "`spec.md` 缺少有效项目目标": "Project goal is not defined",
    "`spec.md` is missing a valid goal": "Project goal is not defined",
    "`spec.md` 缺少有效目标用户": "Target users are not defined",
    "`spec.md` 缺少有效核心价值": "Project value is not defined",
    "`spec.md` 缺少有效区块：### 2.1 已确认事实": "Confirmed requirements are not recorded",
    "`spec.md` 缺少有效区块：### 2.2 关键假设": "Key assumptions are not recorded",
    "`spec.md` is missing a valid section: ### 2.2 Key Assumptions": "Key assumptions are not recorded",
    "`spec.md` 缺少有效区块：### 2.3 待确认问题": "Open questions are not identified",
    "`spec.md` is missing a valid section: ### 2.3 Open Questions": "Open questions are not identified",
    "`spec.md` 缺少有效区块：### 3.3 不在范围内": "Out-of-scope work is not defined",
    "`spec.md` is missing a valid section: ### 3.3 Out of Scope": "Out-of-scope work is not defined",
    "`spec.md` 缺少有效区块：## 4. 最小实现路径": "Minimal implementation path is not defined",
    "`spec.md` is missing a valid section: ## 4. Minimal Implementation Path": (
        "Minimal implementation path is not defined"
    ),
}


def project_gap_message(gap: str) -> str:
    """Translate package diagnostics into project-facing language."""
    return _PROJECT_GAP_MESSAGES.get(gap, gap.replace("`spec.md`", "Project definition").strip())


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
    """Build dashboard task rows from tasks.md records, not pipeline stages.

    Accepts both the report-layer ``Task`` objects (``text``/``is_completed``)
    and ``spec_package_support.TaskRecord`` (``title``/``checked``/``blocked``).
    """
    kinds: list[str] = []
    labels: list[str] = []
    for task in tasks:
        label = str(getattr(task, "text", "") or getattr(task, "title", "") or "").strip()
        if not label:
            continue
        labels.append(label)
        completed = bool(
            getattr(task, "is_completed", False) or getattr(task, "completed", False) or getattr(task, "checked", False)
        )
        blocked = bool(getattr(task, "is_blocked", False) or getattr(task, "blocked", False))
        in_progress = bool(
            getattr(task, "is_explicit_in_progress", False) or str(getattr(task, "block_text", "")).startswith("- [>]")
        )
        if completed:
            kinds.append(GLYPH_DONE)
        elif blocked:
            kinds.append(GLYPH_BLOCKED)
        elif in_progress:
            kinds.append(GLYPH_ACTIVE)
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
    alerts_pointer: str = "project record"
    detail_title: str = "Delivery Details"
    ascii_mode: bool = False
    compact: bool = False
    current_items: list[str] = field(default_factory=list)

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
        return f"- {label}: {value}"

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
        return self._section("Project Progress", [self._item("Completed", f"{self.done}/{self.total}")])

    def _metric(self, key: str, default: str = "") -> str:
        return self.metrics.get(key, self.metrics.get(_METRIC_ALIASES.get(key, ""), default))

    def _verification_scope_lines(self) -> list[str]:
        scope = self._metric("Scope")
        if not scope or scope in {"历史包未声明", "not declared in legacy package"}:
            return []
        return self._section("Verification Scope", [self._item("Level", scope)])

    def _metric_value(self, column: str) -> str:
        if column in {"Progress", "进度"}:
            return f"{self.done}/{self.total}"
        return self._metric(column, self._na())

    @property
    def normalized_strip(self) -> list[str]:
        """Preserve the source task states, including parallel active tasks."""
        return list(self.strip)

    def _metrics_lines(self) -> list[str]:
        if self.alerts:
            return []
        items = []
        for label in ("Pending decisions", "Blocked"):
            value = self._metric(label)
            if value and value not in {"0", "通过", "Passed"}:
                items.append(self._item(label, value))
        return self._section("Attention", items)

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
            overflow = self._item(f"See the task package for {extra} more items")
            return items[:MAX_STRIP_ITEMS] + ([overflow] if overflow else [])
        return items

    def _strip_lines(self) -> list[str]:
        if self.compact:
            items = self._strip_items()
            names = [item[2:] for item in items]
            if not names:
                return []
            summary = "; ".join(names)
            item = self._item("Tasks", summary)
            return [item] if item else []
        return self._section("Tasks", self._strip_items())

    def _labeled_items(self, title: str, values: list[str]) -> list[str]:
        if self.compact:
            return [item for item in (self._item(title, value) for value in values) if item]
        return [item for item in (self._item(value) for value in values) if item]

    def _current_items(self) -> list[str]:
        if self.strip:
            # A task strip is the authoritative state: never promote a pending
            # or blocked task supplied by an older caller as "current".
            return [
                label.strip()
                for kind, label in zip(self.normalized_strip, self.strip_labels)
                if kind == GLYPH_ACTIVE and label.strip()
            ]
        if self.current_items:
            return [item.strip() for item in self.current_items if item.strip()]
        return [self.current.strip()] if self.current and self.current.strip() else []

    def _current_lines(self) -> list[str]:
        return self._section("In Progress", self._labeled_items("In Progress", self._current_items()))

    def _alert_lines(self) -> list[str]:
        alerts = [line for line in self.alerts if line and line not in {"—", "无"}]
        if not alerts:
            return []
        hidden = len(alerts) - self.max_alerts
        shown = alerts[: self.max_alerts]
        values = list(shown)
        if hidden > 0:
            values.append(f"See {self.alerts_pointer} for {hidden} more items")
        return self._section("Attention", self._labeled_items("Attention", values))

    def _delta_lines(self) -> list[str]:
        if not self.delta:
            return []
        shown = self.delta[:MAX_DELTA_LINES]
        return self._section("Completed This Round", self._labeled_items("Completed This Round", shown))

    def _next_lines(self) -> list[str]:
        next_step = self.next_step.strip()
        if not next_step or next_step in self._current_items():
            return []
        return self._section("Next", self._labeled_items("Next", [next_step]))

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

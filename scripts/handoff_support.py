# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared handoff-document semantics for the optional package ``handoff.md``.

A handoff document makes session boundaries cheap: the departing collaborator
(one human, a teammate, an agent session, or an agent-cluster lane) appends one
structured entry, and any successor resumes from generated disk truth instead
of re-deriving the repository. The file has two zones:

- ``## 快照`` — a machine-readable snapshot rewritten by the CLI on every
  update (branch, HEAD anchor, task progress, next ready tasks, freshness
  inputs). Generated, never hand-written.
- ``## 条目`` — an append-only entry log. Each entry carries a fenced scalar
  field block (event/collab/actor/status) plus narrative slots; required slots
  adapt to the collaboration mode.

The mode lattice (``solo`` ⊂ ``{team, agent}`` ⊂ ``cluster``) is the dynamic
core: what a successor must find in an entry depends on who they are.

Vocabulary is closed and validated: collab modes ``solo|team|agent|cluster``,
events ``pause|takeover|lane-end|escalation|close``, statuses
``in-progress|blocked-on-human|blocked-on-agent|done``. Validation reuses one
function everywhere (CLI ``validate``, ``check_spec_package``); absence of the
file never blocks anything — legacy packages stay compatible.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from spec_package_support import (
    count_tasks,
    extract_integration_branch,
    git_head_sha,
    parse_task_records,
    read_regular_text,
    ready_task_ids,
)

HANDOFF_FILENAME = "handoff.md"
SCHEMA_VERSION = 1

COLLAB_MODES = ("solo", "team", "agent", "cluster")
ENTRY_EVENTS = ("pause", "takeover", "lane-end", "escalation", "close")
ENTRY_STATUSES = ("in-progress", "blocked-on-human", "blocked-on-agent", "done")

# Mode lattice: cluster requires everything solo/team/agent require.
MODE_REQUIRED_SECTIONS: dict[str, tuple[str, ...]] = {
    "solo": ("上下文", "下一步"),
    "team": ("上下文", "下一步", "所有权", "等待中"),
    "agent": ("上下文", "下一步", "恢复"),
    "cluster": ("上下文", "下一步", "所有权", "等待中", "恢复", "lanes"),
}
# Generated entries always include these; validation leaves them optional.
OPTIONAL_SECTIONS = ("发现", "风险与应急", "回执")

SNAPSHOT_HEADING = "## 快照"
ENTRIES_HEADING = "## 条目"
SNAPSHOT_KEYS = (
    "schema",
    "package",
    "branch",
    "head",
    "recorded-at",
    "progress",
    "next-tasks",
    "blockers",
    "collab",
    "entries",
    "latest-entry",
)
ENTRY_KEYS = ("entry", "event", "collab", "actor", "status")

TIMESTAMP_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$")
ENTRY_HEADING_PATTERN = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]\s+"
    r"event=(?P<event>[a-z-]+)\s+collab=(?P<collab>[a-z-]+)\s+actor=(?P<actor>.+)$"
)
SCALAR_LINE_PATTERN = re.compile(r"^\s*([a-z][a-z0-9-]*):\s*(.*?)\s*$")

FRESH_FRESH = "fresh"
FRESH_STALE = "stale"
FRESH_UNKNOWN = "unknown"

DEFAULT_ACTOR = "main"
ENTRY_NOTE_PLACEHOLDER = "（由接手会话补充：做了什么、为什么在这里停）"

CONTRACT_NOTE = (
    "> 维护契约：快照区由 `python3 scripts/spec_handoff.py update` 重写；"
    "条目区 append-only，不删不改旧条目；`validate` 校验结构。"
)


class HandoffError(Exception):
    """Operational or validation failure with a user-facing message."""


@dataclass
class HandoffEntry:
    """One session-boundary entry from the append-only log."""

    heading: str
    timestamp: str
    fields: dict[str, str] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)
    duplicate_keys: list[str] = field(default_factory=list)

    @property
    def number(self) -> int:
        try:
            return int(self.fields.get("entry", "0"))
        except ValueError:
            return 0

    @property
    def collab(self) -> str:
        return self.fields.get("collab", "")

    @property
    def event(self) -> str:
        return self.fields.get("event", "")


@dataclass
class HandoffDoc:
    """Parsed handoff document: snapshot fields plus the entry log."""

    snapshot: dict[str, str] = field(default_factory=dict)
    snapshot_duplicate_keys: list[str] = field(default_factory=list)
    entries: list[HandoffEntry] = field(default_factory=list)

    @property
    def latest(self) -> HandoffEntry | None:
        return self.entries[-1] if self.entries else None


def utc_timestamp(moment: datetime | None = None) -> str:
    now = moment or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_timestamp(value: str) -> datetime | None:
    match = TIMESTAMP_PATTERN.match(value.strip())
    if not match:
        return None
    parts = [int(part) for part in match.groups()]
    try:
        return datetime(*parts, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_scalar_block(text: str) -> dict[str, str]:
    """Parse ``key: value`` scalar lines; the first occurrence of a key wins.

    First-wins keeps a hand-edited duplicate key from silently overriding the
    value the heading line already agrees with; validation flags the duplicate
    itself so the ambiguity is never silent.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = SCALAR_LINE_PATTERN.match(line)
        if match:
            fields.setdefault(match.group(1), match.group(2))
    return fields


def _duplicate_keys(text: str) -> list[str]:
    """Keys appearing more than once in a scalar block, in first-seen order."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for line in text.splitlines():
        match = SCALAR_LINE_PATTERN.match(line)
        if not match:
            continue
        key = match.group(1)
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
    return duplicates


def _split_sections(body: str) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Split an entry body into its fenced field block and ``####`` sections.

    The *first* fence before any ``####`` heading is the field block; fences
    inside a narrative section are content — a ``####`` line inside such a
    fence must not split the section. Returns ``(fields, sections, duplicate
    keys in the field block)``.
    """
    fields: dict[str, str] = {}
    sections: dict[str, str] = {}
    duplicates: list[str] = []
    fence_pattern = re.compile(r"^```[a-zA-Z0-9_-]*\s*$")
    in_fence = False
    fence_lines: list[str] = []
    fields_seen = False
    current: str | None = None
    current_lines: list[str] = []
    for line in body.splitlines():
        if fence_pattern.match(line):
            if in_fence and current is None and not fields_seen:
                fields = parse_scalar_block("\n".join(fence_lines))
                duplicates = _duplicate_keys("\n".join(fence_lines))
                fields_seen = True
            in_fence = not in_fence
            if in_fence and current is None and not fields_seen:
                fence_lines = []
            if current is not None:
                current_lines.append(line)
            continue
        if in_fence and current is None:
            if not fields_seen:
                fence_lines.append(line)
            continue
        if in_fence and current is not None:
            current_lines.append(line)
            continue
        heading = re.match(r"^####\s+(.+?)\s*$", line)
        if heading:
            if current is not None:
                sections[current] = "\n".join(current_lines).strip()
            current = heading.group(1)
            current_lines = []
            continue
        if current is not None:
            current_lines.append(line)
    if current is not None:
        sections[current] = "\n".join(current_lines).strip()
    return fields, sections, duplicates


def _extract_top_section(content: str, heading: str) -> str | None:
    """Return the body under a ``##`` heading up to the next same-level one."""
    lines = content.splitlines()
    body: list[str] | None = None
    for index, line in enumerate(lines):
        is_target = body is None and re.match(rf"^{re.escape(heading)}\s*$", line)
        if body is not None and re.match(r"^##\s+", line) and not is_target:
            return "\n".join(body)
        if is_target:
            body = []
            continue
        if body is not None:
            body.append(line)
    return "\n".join(body) if body is not None else None


def parse_handoff(content: str) -> HandoffDoc:
    """Parse a handoff document into snapshot fields and ordered entries."""
    doc = HandoffDoc()
    snapshot_body = _extract_top_section(content, SNAPSHOT_HEADING)
    if snapshot_body is not None:
        fence = re.search(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n```", snapshot_body, re.DOTALL | re.MULTILINE)
        if fence:
            doc.snapshot = parse_scalar_block(fence.group(1))
            doc.snapshot_duplicate_keys = _duplicate_keys(fence.group(1))
    entries_body = _extract_top_section(content, ENTRIES_HEADING)
    if entries_body is not None:
        blocks = re.split(r"(?m)^(?=###\s)", entries_body)
        for block in blocks:
            match = re.match(r"^###\s+(.+?)\s*$", block, re.MULTILINE)
            if not match:
                continue
            heading = match.group(1)
            fields, sections, duplicates = _split_sections(block[match.end() :])
            parsed = ENTRY_HEADING_PATTERN.match(heading)
            timestamp = parsed.group("ts") if parsed else ""
            doc.entries.append(
                HandoffEntry(
                    heading=heading,
                    timestamp=timestamp,
                    fields=fields,
                    sections=sections,
                    duplicate_keys=duplicates,
                )
            )
    return doc


def _snapshot_state(root: Path, slug: str, package_dir: Path, tasks_content: str, spec_content: str) -> dict[str, str]:
    total, completed = count_tasks(tasks_content)
    branch = extract_integration_branch(spec_content) or "unknown"
    head = git_head_sha(root)
    next_tasks: list[str] = []
    blockers = "none"
    try:
        records = parse_task_records(tasks_content)
        ready = ready_task_ids(records)
        next_tasks = [record.id for record in records if record.id in ready and not record.checked][:3]
        blocked = next((record.title for record in records if record.blocked and not record.checked), None)
        if blocked:
            blockers = blocked
    except Exception:  # noqa: BLE001 - snapshot stays best-effort; validate owns structure
        next_tasks = []
    return {
        "schema": str(SCHEMA_VERSION),
        "package": slug,
        "branch": branch,
        "head": head[:12] if head else "unknown",
        "recorded-at": utc_timestamp(),
        "progress": f"{completed}/{total}",
        "next-tasks": ",".join(next_tasks) if next_tasks else "none",
        "blockers": blockers,
    }


def _render_snapshot(fields: dict[str, str]) -> str:
    lines = [f"{key}: {fields.get(key, '')}" for key in SNAPSHOT_KEYS]
    return f"{SNAPSHOT_HEADING}\n\n```yaml\n" + "\n".join(lines) + "\n```"


_STRUCTURE_LINE_PATTERN = re.compile(r"^(?:#{1,6}\s|```|entry:\s|event:\s|collab:\s|actor:\s|status:)")


def sanitize_narrative(text: str) -> str:
    """Neutralize narrative lines that would forge handoff structure.

    A note line starting with a heading marker, a fence, or an entry field
    key could otherwise inject a phantom entry or fake fields on the next
    parse; indenting such lines by one space keeps them readable while
    breaking every structural match.
    """
    return "\n".join((" " + line if _STRUCTURE_LINE_PATTERN.match(line) else line) for line in text.splitlines())


def normalize_actor(actor: str) -> str:
    """Collapse whitespace so an actor stays on one heading line."""
    return re.sub(r"\s+", " ", actor).strip()


def render_entry(
    number: int,
    timestamp: str,
    event: str,
    collab: str,
    actor: str,
    status: str,
    note: str = "",
) -> str:
    """Render one entry with the generated slot layout for its collab mode."""
    fields = {
        "entry": str(number),
        "event": event,
        "collab": collab,
        "actor": actor,
        "status": status,
    }
    heading = f"### [{timestamp}] event={event} collab={collab} actor={actor}"
    block = "\n".join(f"{key}: {fields[key]}" for key in ENTRY_KEYS)
    sections: list[tuple[str, str]] = [
        ("上下文", sanitize_narrative(note.strip()) or ENTRY_NOTE_PLACEHOLDER),
        ("发现", "（指针式结论 file:line，不整段誊抄；可写 无）"),
        ("下一步", "（动作 + 目标 + 怎么验证）"),
        ("风险与应急", "（若 X 坏则做 Y；可写 无）"),
    ]
    sections.extend(
        (name, default)
        for name, default in (
            ("所有权", "（谁拥有什么）"),
            ("等待中", "（waiting-on 清单或 无）"),
            ("恢复", "（resume 命令 + 下一任务 boundary/verify 指针）"),
            ("lanes", "（lane 表：id/状态/ownership/指针/汇合次序）"),
        )
        if name in MODE_REQUIRED_SECTIONS[collab]
    )
    sections.append(("回执", "—（接手方填写）"))
    body = "\n\n".join(f"#### {name}\n{content}" for name, content in sections)
    return f"{heading}\n\n```yaml\n{block}\n```\n\n{body}"


def read_handoff(package_dir: Path) -> str | None:
    path = package_dir / HANDOFF_FILENAME
    if not path.is_file():
        return None
    return read_regular_text(path)


def write_atomic_text(path: Path, content: str) -> None:
    """Replace ``path`` with ``content`` via an fsync'd temp file and rename.

    Mirrors the update-checkpoint atomic-write convention so a crash mid-write
    can never truncate the append-only log.
    """
    directory = path.parent
    try:
        target_mode = path.stat().st_mode & 0o777
    except OSError:
        target_mode = 0o600
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), target_mode)
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def write_handoff(package_dir: Path, snapshot: dict[str, str], entries_markdown: str) -> None:
    parts = [
        f"# handoff - {snapshot.get('package', '')}",
        "",
        CONTRACT_NOTE,
        "",
        _render_snapshot(snapshot),
        "",
        ENTRIES_HEADING,
        "",
        entries_markdown.rstrip("\n"),
        "",
    ]
    write_atomic_text(package_dir / HANDOFF_FILENAME, "\n".join(parts))


def append_entry(
    package_dir: Path,
    root: Path,
    slug: str,
    tasks_content: str,
    spec_content: str,
    *,
    event: str,
    collab: str | None,
    actor: str | None,
    status: str | None,
    note: str = "",
    timestamp: str | None = None,
) -> int:
    """Regenerate the snapshot and append one entry; returns the entry number."""
    _ensure_vocabulary(event=event, collab=collab, actor=actor, status=status)
    if event == "close":
        raise HandoffError("close is reserved for the done/archive write point; use pause or escalation")
    return _append_entry_impl(
        package_dir,
        root,
        slug,
        tasks_content,
        spec_content,
        event=event,
        collab=collab,
        actor=actor,
        status=status,
        note=note,
        timestamp=timestamp,
    )


def _append_entry_impl(
    package_dir: Path,
    root: Path,
    slug: str,
    tasks_content: str,
    spec_content: str,
    *,
    event: str,
    collab: str | None,
    actor: str | None,
    status: str | None,
    note: str = "",
    timestamp: str | None = None,
) -> int:
    existing = read_handoff(package_dir)
    doc = parse_handoff(existing) if existing else HandoffDoc()
    if existing:
        errors = validate_handoff(existing)
        if errors:
            raise HandoffError(f"refusing to append to an invalid handoff document: {errors[0]}")
        if doc.latest is not None and doc.latest.fields.get("event") == "close":
            raise HandoffError("handoff log already closed; append is refused on a frozen document")
    latest = doc.latest
    resolved_collab = collab or (latest.collab if latest else "solo")
    resolved_actor = (
        normalize_actor(actor or "")
        or (normalize_actor(latest.fields.get("actor", "")) if latest else "")
        or DEFAULT_ACTOR
    )
    resolved_status = status or ("blocked-on-human" if event == "escalation" else "in-progress")
    _ensure_vocabulary(collab=resolved_collab, actor=resolved_actor, status=resolved_status)

    snapshot = _snapshot_state(root, slug, package_dir, tasks_content, spec_content)
    number = (latest.number if latest else 0) + 1
    stamp = timestamp or utc_timestamp()
    snapshot["collab"] = resolved_collab
    snapshot["entries"] = str(number)
    snapshot["latest-entry"] = stamp

    entry_markdown = render_entry(number, stamp, event, resolved_collab, resolved_actor, resolved_status, note)
    previous_entries = ""
    if existing:
        entries_body = _extract_top_section(existing, ENTRIES_HEADING)
        if entries_body is not None:
            previous_entries = entries_body.strip("\n")
    combined = (previous_entries + "\n\n" + entry_markdown) if previous_entries else entry_markdown
    write_handoff(package_dir, snapshot, combined)
    # Write-then-verify: a generated document that does not round-trip through
    # its own validator is rolled back instead of shipped.
    written = read_handoff(package_dir)
    if written is None:
        raise HandoffError("handoff write verification failed: file unreadable after write")
    post_errors = validate_handoff(written)
    if post_errors:
        if existing is not None:
            write_atomic_text(package_dir / HANDOFF_FILENAME, existing)
        else:
            (package_dir / HANDOFF_FILENAME).unlink(missing_ok=True)
        raise HandoffError(f"handoff write verification failed, previous content restored: {post_errors[0]}")
    return number


def append_close_entry(package_dir: Path, root: Path, slug: str, tasks_content: str, spec_content: str) -> int | None:
    """Append the terminal close entry at done time; no-op without a handoff.

    Idempotent: when the log already ends with a close entry (a retried
    ``done --archive``), the existing terminal entry is kept — a second close
    would freeze an invalid document into the archive.
    """
    existing = read_handoff(package_dir)
    if existing is None:
        return None
    doc = parse_handoff(existing)
    latest = doc.latest
    if latest is not None and latest.fields.get("event") == "close":
        return latest.number
    number = _append_entry_impl(
        package_dir,
        root,
        slug,
        tasks_content,
        spec_content,
        event="close",
        collab=None,
        actor=DEFAULT_ACTOR,
        status="done",
        note="Development Record 收口：done 归档，交接冻结为只读历史。",
    )
    written = read_handoff(package_dir)
    if written is not None:
        post_errors = validate_handoff(written)
        if post_errors:
            raise HandoffError(f"handoff close verification failed: {post_errors[0]}")
    return number


def _ensure_vocabulary(
    *,
    event: str | None = None,
    collab: str | None = None,
    actor: str | None = None,
    status: str | None = None,
) -> None:
    if event is not None and event not in ENTRY_EVENTS:
        raise HandoffError(f"unknown handoff event: {event} (expected one of {'/'.join(ENTRY_EVENTS)})")
    if collab is not None and collab not in COLLAB_MODES:
        raise HandoffError(f"unknown handoff collab mode: {collab} (expected one of {'/'.join(COLLAB_MODES)})")
    if status is not None and status not in ENTRY_STATUSES:
        raise HandoffError(f"unknown handoff status: {status} (expected one of {'/'.join(ENTRY_STATUSES)})")
    if actor is not None and not actor.strip():
        raise HandoffError("handoff actor must be a non-empty identifier")


def validate_handoff(content: str) -> list[str]:
    """Validate structure and vocabulary; every finding is a user-facing error."""
    errors: list[str] = []
    snapshot_body = _extract_top_section(content, SNAPSHOT_HEADING)
    if snapshot_body is None:
        return [f"handoff 缺少 `{SNAPSHOT_HEADING}` 区块"]
    doc = parse_handoff(content)
    if not doc.snapshot:
        errors.append("handoff 快照缺少 fenced 字段块")
    else:
        for key in doc.snapshot_duplicate_keys:
            errors.append(f"handoff 快照字段块存在重复键：{key}")
        for key in SNAPSHOT_KEYS:
            if key not in doc.snapshot:
                errors.append(f"handoff 快照缺少字段：{key}")
        if doc.snapshot.get("schema") != str(SCHEMA_VERSION):
            errors.append(f"handoff 快照 schema 必须为 {SCHEMA_VERSION}")
        if not doc.snapshot.get("package", "").strip():
            errors.append("handoff 快照 package 不能为空")
        if parse_utc_timestamp(doc.snapshot.get("recorded-at", "")) is None:
            errors.append("handoff 快照 recorded-at 必须为 YYYY-MM-DDTHH:MM:SSZ")
        head = doc.snapshot.get("head", "")
        if head != "unknown" and not re.fullmatch(r"[0-9a-f]{12}", head):
            errors.append("handoff 快照 head 必须为 12 位小写十六进制或 unknown")
        if not re.fullmatch(r"\d+/\d+", doc.snapshot.get("progress", "")):
            errors.append("handoff 快照 progress 必须为 done/total 形式")

    entries_body = _extract_top_section(content, ENTRIES_HEADING)
    if entries_body is None:
        errors.append(f"handoff 缺少 `{ENTRIES_HEADING}` 区块")
        return errors
    if not doc.entries:
        errors.append("handoff 条目区为空")
        return errors

    now = datetime.now(timezone.utc)
    expected_number = 1
    previous_timestamp: datetime | None = None
    for entry in doc.entries:
        parsed = ENTRY_HEADING_PATTERN.match(entry.heading)
        if not parsed:
            expected_heading = "`### [<ISO-8601 UTC>] event=<v> collab=<v> actor=<v>`"
            errors.append(f"条目 {expected_number} 标题行格式非法（应为 {expected_heading}）")
        else:
            if parsed.group("event") != entry.fields.get("event"):
                errors.append(f"条目 {expected_number} 标题与字段块的 event 不一致")
            if parsed.group("collab") != entry.fields.get("collab"):
                errors.append(f"条目 {expected_number} 标题与字段块的 collab 不一致")
            if parsed.group("actor").strip() != entry.fields.get("actor", "").strip():
                errors.append(f"条目 {expected_number} 标题与字段块的 actor 不一致")
        moment = parse_utc_timestamp(entry.timestamp)
        if moment is None:
            errors.append(f"条目 {expected_number} 时间戳非法（应为 YYYY-MM-DDTHH:MM:SSZ）")
        else:
            if moment > now:
                errors.append(f"条目 {expected_number} 时间戳晚于当前时刻")
            if previous_timestamp is not None and moment < previous_timestamp:
                errors.append(f"条目 {expected_number} 时间戳早于上一条目（append-only 日志必须单调不减）")
            previous_timestamp = moment
        if not entry.fields:
            errors.append(f"条目 {expected_number} 缺少 fenced 字段块")
        else:
            for key in entry.duplicate_keys:
                errors.append(f"条目 {expected_number} 字段块存在重复键：{key}")
            for key in ENTRY_KEYS:
                if key not in entry.fields:
                    errors.append(f"条目 {expected_number} 缺少字段：{key}")
        if entry.fields.get("event") not in ENTRY_EVENTS:
            errors.append(f"条目 {expected_number} event 非法：{entry.fields.get('event', '')}")
        if entry.fields.get("collab") not in COLLAB_MODES:
            errors.append(f"条目 {expected_number} collab 非法：{entry.fields.get('collab', '')}")
        if entry.fields.get("status") not in ENTRY_STATUSES:
            errors.append(f"条目 {expected_number} status 非法：{entry.fields.get('status', '')}")
        if not entry.fields.get("actor", "").strip():
            errors.append(f"条目 {expected_number} actor 不能为空")
        if entry.number != expected_number:
            errors.append(f"条目序号不连续：期望 {expected_number}，实得 {entry.number or '缺失'}")
        for name in MODE_REQUIRED_SECTIONS.get(entry.fields.get("collab", ""), ()):
            if name not in entry.sections:
                errors.append(f"条目 {expected_number} 缺少 {entry.fields.get('collab')} 模式必填小节：#### {name}")
        expected_number += 1

    close_positions = [index for index, entry in enumerate(doc.entries) if entry.fields.get("event") == "close"]
    last_index = len(doc.entries) - 1
    for index in close_positions:
        if index != last_index:
            errors.append(f"条目 {index + 1} 的 close 必须是最后一条（append-only 日志不允许 close 后续写）")
    latest = doc.latest
    if latest is not None and doc.snapshot:
        if latest.fields.get("event") == "close" and latest.fields.get("status") != "done":
            errors.append("close 条目 status 必须为 done")
        try:
            declared = int(doc.snapshot.get("entries", "0"))
        except ValueError:
            declared = -1
        if declared != len(doc.entries):
            errors.append(f"快照 entries 计数与条目数不一致：{declared} vs {len(doc.entries)}")
        if doc.snapshot.get("latest-entry", "") != latest.timestamp:
            errors.append("快照 latest-entry 与最后一条目时间戳不一致")
        if doc.snapshot.get("collab", "") != latest.fields.get("collab", ""):
            errors.append("快照 collab 与最后一条目 collab 不一致")
    return errors


def freshness(doc: HandoffDoc, root: Path, tasks_content: str) -> tuple[str, str]:
    """Compare the snapshot anchor with current disk truth.

    ``stale`` means the HEAD or task progress moved after the snapshot was
    recorded — the narrative may still be useful, but numeric state is not.
    ``unknown`` covers non-Git roots and unreadable anchors; it never blocks.
    """
    if not doc.snapshot:
        return FRESH_UNKNOWN, "快照缺失，无法判定新鲜度"
    recorded_head = doc.snapshot.get("head", "")
    current_head = git_head_sha(root)
    if recorded_head == "unknown":
        # The snapshot never anchored a HEAD (non-Git root or git timeout at
        # record time); claiming "fresh" in a repo that now resolves would be
        # a false assurance, so the comparison stays honestly unresolved.
        if current_head:
            return FRESH_UNKNOWN, "快照未锚定 HEAD，无法比对"
    else:
        if not current_head:
            return FRESH_UNKNOWN, "当前无法解析 git HEAD"
        if current_head[:12] != recorded_head:
            return FRESH_STALE, f"HEAD 已移动（快照 {recorded_head}）"
    total, completed = count_tasks(tasks_content)
    if doc.snapshot.get("progress", "") != f"{completed}/{total}":
        return FRESH_STALE, f"任务进度已变化（快照 {doc.snapshot.get('progress', '?')}）"
    return FRESH_FRESH, "与磁盘真源一致"


def handoff_brief(package_dir: Path, root: Path, tasks_content: str) -> str | None:
    """One-line resume hint for route/status views; ``None`` without a file.

    An unrenderable or structurally invalid document reports itself as such
    instead of trusting a parse that may be mid-edit.
    """
    try:
        content = read_handoff(package_dir)
    except (OSError, UnicodeError, ValueError):
        return "handoff 不可读（超大/非 UTF-8/非常规文件），运行 validate 诊断"
    if content is None:
        return None
    if validate_handoff(content):
        return "handoff 结构非法，运行 validate 诊断"
    doc = parse_handoff(content)
    latest = doc.latest
    if latest is None:
        return "handoff 存在但无条目"
    label, _detail = freshness(doc, root, tasks_content)
    return f"handoff {latest.collab}/{label}（最新 {latest.timestamp}，event={latest.event}）"

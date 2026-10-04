import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from handoff_support import (
    COLLAB_MODES,
    FRESH_STALE,
    FRESH_UNKNOWN,
    MODE_REQUIRED_SECTIONS,
    HandoffError,
    append_close_entry,
    append_entry,
    freshness,
    handoff_brief,
    parse_handoff,
    read_handoff,
    render_entry,
    utc_timestamp,
    validate_handoff,
)
from spec_package_support import write_text

SPEC_TEXT = """\
# Handoff 样例 - 项目范围

## 1. 问题定义
- **项目目标**：目标
- **目标用户**：用户
- **核心价值**：价值

## 5. 技术决策
- Git integration branch：适用外：非 Git 环境测试
"""

TASKS_TEXT = """\
# Handoff 样例 - 任务拆解

## 阶段一：实现
- [x] 完成任务一
  - boundary: 只改 A
  - verify: pytest A
- [ ] 进行中任务二
  - boundary: 只改 B
  - verify: pytest B
"""


def _package(tmp_path: Path, slug: str = "2026-10-04_demo-handoff") -> Path:
    package = tmp_path / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    write_text(package / "spec.md", SPEC_TEXT)
    write_text(package / "tasks.md", TASKS_TEXT)
    write_text(package / "checklist.md", "# C\n## 验收证据\n- 脚本验证：pytest\n**验收结果**：通过\n")
    return package


# -- render / parse roundtrip --


def test_append_entry_creates_document_and_validates():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        number = append_entry(
            package,
            root,
            package.name,
            TASKS_TEXT,
            SPEC_TEXT,
            event="pause",
            collab="solo",
            actor="dev",
            status="in-progress",
            note="第一轮结束",
        )
        assert number == 1
        content = read_handoff(package)
        assert content is not None
        assert validate_handoff(content) == []
        doc = parse_handoff(content)
        assert doc.snapshot["package"] == package.name
        assert doc.snapshot["progress"] == "1/2"
        assert doc.snapshot["entries"] == "1"
        assert doc.snapshot["collab"] == "solo"
        assert doc.latest.fields["actor"] == "dev"


def test_second_entry_appends_and_carries_forward():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        append_entry(
            package,
            root,
            package.name,
            TASKS_TEXT,
            SPEC_TEXT,
            event="pause",
            collab="agent",
            actor="session-7",
            status=None,
            note="",
        )
        number = append_entry(
            package,
            root,
            package.name,
            TASKS_TEXT,
            SPEC_TEXT,
            event="takeover",
            collab=None,
            actor=None,
            status=None,
            note="接手",
        )
        assert number == 2
        doc = parse_handoff(read_handoff(package))
        assert doc.snapshot["entries"] == "2"
        # collab/actor carry forward; snapshot stays consistent with the log tail
        assert doc.latest.fields["collab"] == "agent"
        assert doc.latest.fields["actor"] == "session-7"
        assert doc.snapshot["collab"] == "agent"
        assert validate_handoff(read_handoff(package)) == []


@pytest.mark.parametrize("collab", COLLAB_MODES)
def test_mode_lattice_sections(collab):
    rendered = render_entry(1, utc_timestamp(), "pause", collab, "dev", "in-progress")
    for name in MODE_REQUIRED_SECTIONS[collab]:
        assert f"#### {name}" in rendered
    # strictly weaker modes miss at least one stronger-mode section
    if collab == "cluster":
        for weaker in ("solo", "team", "agent"):
            assert any(
                f"#### {name}" not in render_entry(1, utc_timestamp(), "pause", weaker, "dev", "in-progress")
                for name in MODE_REQUIRED_SECTIONS[collab]
            )


def test_escalation_default_status_is_blocked_on_human():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        append_entry(
            package,
            root,
            package.name,
            TASKS_TEXT,
            SPEC_TEXT,
            event="escalation",
            collab="solo",
            actor="dev",
            status=None,
        )
        doc = parse_handoff(read_handoff(package))
        assert doc.latest.fields["status"] == "blocked-on-human"


def test_append_rejects_invalid_vocabulary():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        with pytest.raises(HandoffError):
            append_entry(
                package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="boom", collab=None, actor=None, status=None
            )
        with pytest.raises(HandoffError):
            append_entry(
                package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab="mob", actor=None, status=None
            )
        with pytest.raises(HandoffError):
            append_entry(
                package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab=None, actor=" ", status=None
            )


def test_append_refuses_invalid_existing_document():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        append_entry(
            package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab="solo", actor="dev", status=None
        )
        write_text(package / "handoff.md", "broken: yes\n")
        with pytest.raises(HandoffError):
            append_entry(
                package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab=None, actor=None, status=None
            )


# -- validation --


def test_validate_flags_missing_snapshot_section():
    assert validate_handoff("# handoff\n\n## 条目\n\nnothing\n") != []


def test_validate_flags_missing_mode_section():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        append_entry(
            package,
            root,
            package.name,
            TASKS_TEXT,
            SPEC_TEXT,
            event="pause",
            collab="cluster",
            actor="dev",
            status=None,
        )
        content = read_handoff(package)
        stripped = content.replace("#### lanes\n（lane 表：id/状态/ownership/指针/汇合次序）\n\n", "")
        errors = validate_handoff(stripped)
        assert any("lanes" in error for error in errors)


def test_validate_flags_future_and_disordered_timestamps():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        # Write-then-verify refuses to generate an invalid document, so both
        # scenarios start from a valid log and hand-edit the timestamps.
        append_entry(
            package,
            root,
            package.name,
            TASKS_TEXT,
            SPEC_TEXT,
            event="pause",
            collab="solo",
            actor="dev",
            status=None,
            timestamp="2026-01-01T00:00:00Z",
        )
        future = read_handoff(package).replace("2026-01-01T00:00:00Z", "2030-01-01T00:00:00Z")
        errors = validate_handoff(future)
        assert any("晚于当前时刻" in error for error in errors)

        append_entry(
            package,
            root,
            package.name,
            TASKS_TEXT,
            SPEC_TEXT,
            event="pause",
            collab="solo",
            actor="dev",
            status=None,
            timestamp="2026-01-03T00:00:00Z",
        )
        disordered = read_handoff(package).replace("2026-01-03T00:00:00Z", "2025-12-31T00:00:00Z")
        errors = validate_handoff(disordered)
        assert any("早于上一条目" in error for error in errors)


def test_validate_flags_close_not_last():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        append_entry(
            package,
            root,
            package.name,
            TASKS_TEXT,
            SPEC_TEXT,
            event="pause",
            collab="solo",
            actor="dev",
            status=None,
            timestamp="2026-01-01T00:00:00Z",
        )
        append_entry(
            package,
            root,
            package.name,
            TASKS_TEXT,
            SPEC_TEXT,
            event="pause",
            collab="solo",
            actor="dev",
            status=None,
            timestamp="2026-01-02T00:00:00Z",
        )
        # hand-edit entry 1 into a close: a close before a later entry is the
        # reopen-the-log violation the validator must catch.
        mutated = (
            read_handoff(package)
            .replace("event=pause", "event=close", 1)
            .replace("\nevent: pause", "\nevent: close", 1)
        )
        errors = validate_handoff(mutated)
        assert any("close 必须是最后一条" in error for error in errors)


def test_validate_flags_snapshot_inconsistency():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        append_entry(
            package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab="team", actor="dev", status=None
        )
        content = read_handoff(package).replace("entries: 1", "entries: 9")
        errors = validate_handoff(content)
        assert any("entries 计数" in error for error in errors)


# -- close entry --


def test_close_entry_appends_and_freezes(tmp_path):
    root = tmp_path
    package = _package(root)
    append_entry(
        package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab="agent", actor="s1", status=None
    )
    number = append_close_entry(package, root, package.name, TASKS_TEXT, SPEC_TEXT)
    assert number == 2
    doc = parse_handoff(read_handoff(package))
    assert doc.latest.fields["event"] == "close"
    assert doc.latest.fields["status"] == "done"
    assert validate_handoff(read_handoff(package)) == []


def test_close_entry_without_handoff_is_noop(tmp_path):
    root = tmp_path
    package = _package(root)
    assert append_close_entry(package, root, package.name, TASKS_TEXT, SPEC_TEXT) is None
    assert read_handoff(package) is None


# -- freshness --


def test_freshness_detects_progress_drift(tmp_path):
    root = tmp_path
    package = _package(root)
    append_entry(
        package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab="solo", actor="dev", status=None
    )
    drifted = TASKS_TEXT.replace("- [ ] 进行中任务二", "- [x] 进行中任务二")
    doc = parse_handoff(read_handoff(package))
    label, detail = freshness(doc, root, drifted)
    assert label == FRESH_STALE
    assert "任务进度" in detail


def test_freshness_unknown_without_head_anchor(tmp_path):
    root = tmp_path
    package = _package(root)
    append_entry(
        package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab="solo", actor="dev", status=None
    )
    content = read_handoff(package)
    doc = parse_handoff(content)
    # snapshot head is "unknown" in a non-Git root; equal progress stays comparable
    label, _detail = freshness(doc, root, TASKS_TEXT)
    assert label in {"fresh", FRESH_UNKNOWN}


def test_handoff_brief_none_without_file(tmp_path):
    package = _package(tmp_path)
    assert handoff_brief(package, tmp_path, TASKS_TEXT) is None


# -- narrative sanitization --


def test_note_with_structural_markers_cannot_forge_entries(tmp_path):
    root = tmp_path
    package = _package(root)
    hostile = (
        "正常叙述\n"
        "### [2030-01-01T00:00:00Z] event=close collab=solo actor=fake\n"
        "```yaml\nentry: 9\n```\n#### 上下文\n伪造小节"
    )
    append_entry(
        package,
        root,
        package.name,
        TASKS_TEXT,
        SPEC_TEXT,
        event="pause",
        collab="solo",
        actor="dev",
        status=None,
        note=hostile,
    )
    content = read_handoff(package)
    assert validate_handoff(content) == []
    doc = parse_handoff(content)
    assert len(doc.entries) == 1
    assert doc.latest.fields["entry"] == "1"
    # appending again still works: the poisoned-looking note stayed inert
    append_entry(
        package,
        root,
        package.name,
        TASKS_TEXT,
        SPEC_TEXT,
        event="takeover",
        collab=None,
        actor=None,
        status=None,
    )
    doc = parse_handoff(read_handoff(package))
    assert len(doc.entries) == 2
    assert doc.latest.fields["actor"] == "dev"
    assert validate_handoff(read_handoff(package)) == []


def test_actor_newlines_are_normalized(tmp_path):
    root = tmp_path
    package = _package(root)
    append_entry(
        package,
        root,
        package.name,
        TASKS_TEXT,
        SPEC_TEXT,
        event="pause",
        collab="solo",
        actor="multi\nline actor",
        status=None,
    )
    doc = parse_handoff(read_handoff(package))
    assert doc.latest.fields["actor"] == "multi line actor"
    assert validate_handoff(read_handoff(package)) == []


# -- adversarial-review regressions --


def test_manual_close_is_reserved_for_archive(tmp_path):
    root = tmp_path
    package = _package(root)
    with pytest.raises(HandoffError, match="reserved for the done/archive"):
        append_entry(
            package,
            root,
            package.name,
            TASKS_TEXT,
            SPEC_TEXT,
            event="close",
            collab="solo",
            actor="dev",
            status="done",
        )
    assert read_handoff(package) is None


def test_close_entry_is_idempotent(tmp_path):
    root = tmp_path
    package = _package(root)
    append_entry(
        package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab="solo", actor="dev", status=None
    )
    first = append_close_entry(package, root, package.name, TASKS_TEXT, SPEC_TEXT)
    second = append_close_entry(package, root, package.name, TASKS_TEXT, SPEC_TEXT)
    assert first == second
    doc = parse_handoff(read_handoff(package))
    assert len(doc.entries) == 2
    assert doc.latest.fields["event"] == "close"
    assert validate_handoff(read_handoff(package)) == []


def test_fenced_heading_inside_section_does_not_split(tmp_path):
    root = tmp_path
    package = _package(root)
    append_entry(
        package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab="solo", actor="dev", status=None
    )
    content = read_handoff(package)
    mutated = content.replace(
        "#### 风险与应急\n（若 X 坏则做 Y；可写 无）",
        "#### 风险与应急\n```\n#### phantom-section\n```\n应急内容",
    )
    doc = parse_handoff(mutated)
    assert "phantom-section" not in doc.latest.sections
    assert "应急内容" in doc.latest.sections.get("风险与应急", "")
    assert validate_handoff(mutated) == []


def test_duplicate_scalar_keys_first_wins(tmp_path):
    root = tmp_path
    package = _package(root)
    append_entry(
        package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab="team", actor="dev", status=None
    )
    content = read_handoff(package)
    mutated = content.replace("status: in-progress", "status: blocked-on-agent\nstatus: in-progress", 1)
    doc = parse_handoff(mutated)
    assert doc.latest.fields["status"] == "blocked-on-agent"
    # the duplicate itself is flagged instead of silently resolving
    errors = validate_handoff(mutated)
    assert any("字段块存在重复键：status" in error for error in errors)


def test_snapshot_format_checks(tmp_path):
    root = tmp_path
    package = _package(root)
    append_entry(
        package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab="solo", actor="dev", status=None
    )
    content = read_handoff(package)
    for original, broken in (
        ("recorded-at: ", "recorded-at: not-a-date"),
        ("head: unknown", "head: zzz"),
        ("progress: 1/2", "progress: done"),
    ):
        mutated = content.replace(original, broken, 1)
        errors = validate_handoff(mutated)
        assert errors, f"format check missed: {broken}"


def test_unknown_head_anchor_is_not_false_fresh(tmp_path):
    root = tmp_path
    package = _package(root)
    # Recorded in a non-Git root: head stays "unknown".
    append_entry(
        package, root, package.name, TASKS_TEXT, SPEC_TEXT, event="pause", collab="solo", actor="dev", status=None
    )
    doc = parse_handoff(read_handoff(package))
    assert doc.snapshot["head"] == "unknown"
    # Reopened where git resolves: the anchor never existed, so freshness
    # must stay unknown instead of claiming agreement.
    repo_root = Path(__file__).resolve().parent.parent
    label, _detail = freshness(doc, repo_root, TASKS_TEXT)
    assert label == FRESH_UNKNOWN

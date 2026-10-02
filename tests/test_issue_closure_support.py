from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import issue_closure_support as closure_module  # noqa: E402
from issue_closure_support import (  # noqa: E402
    IssueClosureError,
    extract_issue_closure,
    parse_issue_disposition_arg,
    render_issue_closure,
    validate_completion_summary,
    validate_issue_dispositions,
)


def _complete_summary(issues: list[dict] | None = None) -> str:
    return "\n".join(
        [
            "# Follow-up - 完成总结",
            "## 交付结论",
            "- 结果：完成",
            "## 假设回顾",
            "- verified",
            "## 交付范围",
            "- delivered",
            "## 简化决策",
            "- simple",
            "## 变更边界",
            "- scripts",
            "## 验证证据",
            "- pytest passed",
            "## 门禁证据",
            "- gate passed",
            render_issue_closure(issues or []),
        ]
    )


def _write_archived_followup(root: Path, slug: str, *, tasks: str | None = None, issues=None) -> Path:
    package = root / ".spec" / "specs" / "archive" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(
        """# Follow-up - 项目范围
## 1. 问题定义
- **项目目标**：验证 follow-up 闭环
- **目标用户**：维护者
- **核心价值**：后续问题已执行
## 2. 假设与待确认
### 2.1 已确认事实
- follow-up 已归档
### 2.2 关键假设
- 标准门禁可用
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不扩大范围
## 4. 最小实现路径
- 执行任务
- 验证任务
- 归档任务
""",
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        tasks or "- [x] Fixed\n  - boundary: fixture\n  - verify: pytest\n",
        encoding="utf-8",
    )
    (package / "checklist.md").write_text(
        "- [x] Verified\n## 验收证据\n- 脚本验证：pytest\n**验收结果**：通过\n",
        encoding="utf-8",
    )
    (package / "completion-summary.md").write_text(_complete_summary(issues), encoding="utf-8")
    return package


def _issue(disposition: str, **fields) -> dict:
    return {
        "id": f"finding-{disposition}",
        "summary": f"verify {disposition}",
        "actionable": disposition != "non_actionable",
        "disposition": disposition,
        **fields,
    }


def test_validate_all_supported_dispositions(tmp_path: Path):
    specs_root = tmp_path / ".spec"
    followup = "2026-08-22_fix-followup"
    _write_archived_followup(tmp_path, followup)
    tasks = "- [x] Current task\n"
    issues = [
        _issue("resolved_current", taskId="task_0001", evidence="pytest passed"),
        _issue("resolved_followup", followUpSpecSlug=followup, evidence="follow-up archived"),
        _issue(
            "accepted_risk",
            owner="maintainer",
            rationale="documented compatibility tradeoff",
            reviewTrigger="next major release",
        ),
        _issue(
            "external_blocked",
            dependency="vendor production credential",
            owner="maintainer",
            retryTrigger="credential becomes available",
        ),
        _issue("non_actionable", rationale="historical context only"),
    ]

    assert (
        validate_issue_dispositions(
            issues,
            specs_root=specs_root,
            current_slug="2026-08-22_fix-current",
            current_tasks_content=tasks,
        )
        == []
    )


@pytest.mark.parametrize(
    ("issue", "fragment"),
    [
        ({"summary": "missing id", "disposition": "non_actionable", "actionable": False}, ".id"),
        (_issue("accepted_risk", owner="maintainer", rationale="why"), "reviewTrigger"),
        (_issue("external_blocked", dependency="vendor", owner="maintainer"), "retryTrigger"),
        (_issue("non_actionable", actionable=True, rationale="note"), "actionable=false"),
        (_issue("resolved_current", taskId="task_0002", evidence="done"), "does not exist"),
    ],
)
def test_validate_rejects_incomplete_or_unverifiable_dispositions(tmp_path: Path, issue: dict, fragment: str):
    failures = validate_issue_dispositions(
        [issue],
        specs_root=tmp_path / ".spec",
        current_slug="2026-08-22_fix-current",
        current_tasks_content="- [x] Task\n",
    )
    assert any(fragment in failure for failure in failures)


def test_resolved_current_requires_completed_task(tmp_path: Path):
    failures = validate_issue_dispositions(
        [_issue("resolved_current", taskId="task_0001", evidence="claimed")],
        specs_root=tmp_path / ".spec",
        current_slug="2026-08-22_fix-current",
        current_tasks_content="- [ ] Task\n",
    )
    assert any("not completed" in failure for failure in failures)


def test_resolved_followup_requires_archived_passed_package(tmp_path: Path):
    slug = "2026-08-22_fix-followup"
    active = tmp_path / ".spec" / "specs" / slug
    active.mkdir(parents=True)
    issue = _issue("resolved_followup", followUpSpecSlug=slug, evidence="delegated")

    failures = validate_issue_dispositions(
        [issue], specs_root=tmp_path / ".spec", current_slug="2026-08-22_fix-current"
    )
    assert any("not archived" in failure for failure in failures)

    archive_root = tmp_path / ".spec" / "specs" / "archive"
    archive_root.mkdir(parents=True)
    active.rename(archive_root / slug)
    archived = archive_root / slug
    for filename in ("spec.md", "completion-summary.md"):
        (archived / filename).write_text("# x\n", encoding="utf-8")
    (archived / "tasks.md").write_text("- [ ] still pending\n", encoding="utf-8")
    (archived / "checklist.md").write_text("- [x] yes\n**验收结果**：通过\n", encoding="utf-8")
    failures = validate_issue_dispositions(
        [issue], specs_root=tmp_path / ".spec", current_slug="2026-08-22_fix-current"
    )
    assert any("unfinished tasks" in failure for failure in failures)


def test_resolved_followup_cycle_is_rejected(tmp_path: Path):
    first = "2026-08-22_fix-first"
    second = "2026-08-22_fix-second"
    first_issue = _issue("resolved_followup", followUpSpecSlug=second, evidence="second archived")
    second_issue = _issue("resolved_followup", followUpSpecSlug=first, evidence="first archived")
    _write_archived_followup(tmp_path, first, issues=[first_issue])
    _write_archived_followup(tmp_path, second, issues=[second_issue])

    failures = validate_issue_dispositions(
        [_issue("resolved_followup", followUpSpecSlug=first, evidence="first archived")],
        specs_root=tmp_path / ".spec",
        current_slug="2026-08-22_fix-current",
    )
    assert any("cycle detected" in failure for failure in failures)


def test_followup_graph_is_depth_bounded_without_recursion(tmp_path: Path):
    slugs = [f"2026-08-22_fix-depth-{index:02d}" for index in range(35)]
    for index, slug in enumerate(slugs):
        issues = (
            [_issue("resolved_followup", followUpSpecSlug=slugs[index + 1], evidence="next archived")]
            if index + 1 < len(slugs)
            else []
        )
        _write_archived_followup(tmp_path, slug, issues=issues)

    failures = validate_issue_dispositions(
        [_issue("resolved_followup", followUpSpecSlug=slugs[0], evidence="chain archived")],
        specs_root=tmp_path / ".spec",
        current_slug="2026-08-22_fix-current",
    )
    assert any("exceeds depth" in failure for failure in failures)


def test_followup_graph_memoizes_shared_nodes(tmp_path: Path, monkeypatch):
    shared = "2026-08-22_fix-shared"
    left = "2026-08-22_fix-left"
    right = "2026-08-22_fix-right"
    _write_archived_followup(tmp_path, shared)
    _write_archived_followup(
        tmp_path,
        left,
        issues=[_issue("resolved_followup", followUpSpecSlug=shared, evidence="shared archived")],
    )
    _write_archived_followup(
        tmp_path,
        right,
        issues=[_issue("resolved_followup", followUpSpecSlug=shared, evidence="shared archived")],
    )
    counts: dict[str, int] = {}
    original = closure_module._load_archive_node

    def counted(specs_root, slug, *args):
        counts[slug] = counts.get(slug, 0) + 1
        return original(specs_root, slug, *args)

    monkeypatch.setattr(closure_module, "_load_archive_node", counted)
    failures = closure_module.validate_followup_archives(tmp_path / ".spec", [left, right])
    assert failures == []
    assert counts[shared] == 1


def test_followup_archive_rejects_oversized_closure_file(tmp_path: Path):
    slug = "2026-08-22_fix-oversized"
    package = _write_archived_followup(tmp_path, slug)
    (package / "completion-summary.md").write_bytes(b"x" * (closure_module.MAX_CLOSURE_FILE_BYTES + 1))

    failures = validate_issue_dispositions(
        [_issue("resolved_followup", followUpSpecSlug=slug, evidence="claimed")],
        specs_root=tmp_path / ".spec",
        current_slug="2026-08-22_fix-current",
    )
    assert any("exceeds" in failure for failure in failures)


def _spec_with_unchecked_function_box(package: Path) -> None:
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "## 3. 功能范围\n", "## 3. 功能范围\n### 3.1 核心功能（MVP）\n- [ ] 功能 A：描述\n", 1
        ),
        encoding="utf-8",
    )


def test_followup_spec_gate_exempt_for_baseline_equivalent_archive(tmp_path: Path):
    """基线字节等价的 follow-up 归档跳过当前门禁重评，结构校验保留。"""
    slug = "2026-08-22_fix-legacy-boxes"
    package = _write_archived_followup(tmp_path, slug)
    _spec_with_unchecked_function_box(package)
    issue = _issue("resolved_followup", followUpSpecSlug=slug, evidence="legacy archived")

    failures = validate_issue_dispositions(
        [issue],
        specs_root=tmp_path / ".spec",
        current_slug="2026-09-25_fix-current",
        baseline_equivalent=lambda candidate: candidate == slug,
    )
    assert not any("Spec gate" in failure for failure in failures)
    # Structural checks stay in force even for baseline-equivalent archives.
    (package / "tasks.md").write_text("- [ ] still pending\n", encoding="utf-8")
    failures = validate_issue_dispositions(
        [issue],
        specs_root=tmp_path / ".spec",
        current_slug="2026-09-25_fix-current",
        baseline_equivalent=lambda candidate: candidate == slug,
    )
    assert any("unfinished tasks" in failure for failure in failures)


def test_followup_spec_gate_still_fails_when_not_baseline_equivalent(tmp_path: Path):
    """与基线有差异（或未声明豁免）的 follow-up 归档仍被当前门禁打红。"""
    slug = "2026-08-22_fix-drifted-boxes"
    package = _write_archived_followup(tmp_path, slug)
    _spec_with_unchecked_function_box(package)

    failures = validate_issue_dispositions(
        [_issue("resolved_followup", followUpSpecSlug=slug, evidence="drifted archived")],
        specs_root=tmp_path / ".spec",
        current_slug="2026-09-25_fix-current",
        baseline_equivalent=lambda candidate: False,
    )
    assert any("Spec gate is not passed" in failure for failure in failures)

    failures = validate_issue_dispositions(
        [_issue("resolved_followup", followUpSpecSlug=slug, evidence="no exemption wired")],
        specs_root=tmp_path / ".spec",
        current_slug="2026-09-25_fix-current",
    )
    assert any("Spec gate is not passed" in failure for failure in failures)


def test_issue_closure_markdown_round_trip():
    issues = [_issue("non_actionable", rationale="literal } ``` marker")]
    rendered = render_issue_closure(issues)
    payload = extract_issue_closure(rendered)
    assert payload == {"version": 1, "issues": issues}


def test_parse_issue_disposition_accepts_inline_and_regular_file(tmp_path: Path):
    issue = _issue("non_actionable", rationale="context only")
    assert parse_issue_disposition_arg(json.dumps(issue)) == issue
    path = tmp_path / "issue.json"
    path.write_text(json.dumps(issue), encoding="utf-8")
    assert parse_issue_disposition_arg(f"@{path}") == issue
    with pytest.raises(IssueClosureError, match="valid JSON"):
        parse_issue_disposition_arg("not-json")


def test_completion_summary_requires_v1_block_and_no_placeholders(tmp_path: Path):
    issues = [_issue("resolved_current", taskId="task_0001", evidence="pytest passed")]
    summary = "\n".join(
        [
            "# Complete - 完成总结",
            "## 交付结论",
            "- 结果：完成",
            "## 假设回顾",
            "- verified",
            "## 交付范围",
            "- delivered",
            "## 简化决策",
            "- simple",
            "## 变更边界",
            "- scripts",
            "## 验证证据",
            "- pytest passed",
            "## 门禁证据",
            "- gate passed",
            render_issue_closure(issues),
        ]
    )
    assert (
        validate_completion_summary(
            summary,
            specs_root=tmp_path / ".spec",
            current_slug="2026-08-22_fix-current",
            current_tasks_content="- [x] done\n",
        )
        == []
    )
    failures = validate_completion_summary(summary.replace("delivered", "待补充"))
    assert any("placeholder" in failure for failure in failures)

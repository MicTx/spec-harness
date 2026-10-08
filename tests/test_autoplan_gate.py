"""Tests for scripts/autoplan_gate.py (the /spec:autoplan cluster facts and readiness gate)."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from autoplan_gate import (  # noqa: E402  # type: ignore
    _has_goal_section,
    discover_payload,
    gate_payload,
    main,
)


def write_master(tmp_path: Path, text: str) -> Path:
    """Seed the canonical planning master (``plans/README.md``)."""
    root = tmp_path / "plans"
    root.mkdir(parents=True, exist_ok=True)
    master = root / "README.md"
    master.write_text(text, encoding="utf-8")
    return master


def write_good_cluster(tmp_path: Path):
    plan = tmp_path / "plans" / "README.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    detail = tmp_path / "plans" / "01-core.md"
    detail.write_text("# Core phase\n\n## Sign In\n\nbusiness logic, data model, flows\n", encoding="utf-8")
    plan.write_text(
        "# Project plan\n\n## Goal\n\nBuild a small demo product.\n\n## Phases\n\n"
        "- [ ] sign-in flow ([design](01-core.md#sign-in))\n"
        "- [x] scaffold\n",
        encoding="utf-8",
    )
    return plan, detail


class TestDiscoverCommand:
    def test_reports_phase_boundary_with_cluster(self, tmp_path):
        write_good_cluster(tmp_path)
        payload = discover_payload(tmp_path, None)
        assert payload["run_mode"] == "phase-boundary"
        assert [doc["path"] for doc in payload["docs"]] == ["plans/README.md"]
        assert payload["totals"] == {"checked": 1, "unchecked": 1, "total": 2}

    def test_reports_project_start_without_cluster(self, tmp_path, capsys):
        assert main(["discover", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["run_mode"] == "project-start"
        assert payload["docs"] == []

    def test_plans_root_master_qualifies(self, tmp_path):
        (tmp_path / "plans").mkdir()
        write_master(tmp_path, "# plan\n\n- [ ] first feature\n")
        (tmp_path / "PRD.md").write_text("# PRD\n\n- [ ] legacy feature\n", encoding="utf-8")
        payload = discover_payload(tmp_path, None)
        assert [doc["path"] for doc in payload["docs"]] == ["plans/README.md"]

    def test_explicit_path_must_exist(self, tmp_path, capsys):
        assert main(["discover", "--root", str(tmp_path), "--plan", "missing.md"]) == 1
        assert "does not exist" in capsys.readouterr().err

    def test_explicit_path_must_stay_under_root(self, tmp_path):
        outside = tmp_path.parent / "outside-plan.md"
        outside.write_text("- [ ] x\n", encoding="utf-8")
        with pytest.raises(Exception):
            discover_payload(tmp_path, str(outside))


class TestGateInvariants:
    def test_good_cluster_is_ready(self, tmp_path, capsys):
        write_good_cluster(tmp_path)
        assert main(["gate", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ready"] is True
        assert [check["id"] for check in payload["checks"]] == [
            "cluster-present",
            "has-unchecked-features",
            "detail-references-resolve",
            "goal-section-present",
        ]

    def test_no_cluster_fails_closed_with_exit_three(self, tmp_path, capsys):
        assert main(["gate", "--root", str(tmp_path), "--format", "json"]) == 3
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["ready"] is False
        assert "cluster-present" in captured.err

    def test_fully_checked_cluster_is_not_a_plan(self, tmp_path):
        write_master(tmp_path, "# plan\n\n## Goal\n\ndemo\n\n- [x] done\n")
        payload = gate_payload(tmp_path, None)
        failed = {check["id"] for check in payload["checks"] if check["status"] == "fail"}
        assert failed == {"has-unchecked-features"}
        assert payload["ready"] is False

    def test_missing_detail_file_fails(self, tmp_path):
        (tmp_path / "plans").mkdir()
        write_master(tmp_path, "# plan\n\n## Goal\n\ndemo\n\n- [ ] feature ([design](01-gone.md))\n")
        payload = gate_payload(tmp_path, None)
        failed = {check["id"] for check in payload["checks"] if check["status"] == "fail"}
        assert failed == {"detail-references-resolve"}
        assert "target file does not exist" in payload["checks"][2]["detail"]

    def test_broken_anchor_fails(self, tmp_path):
        detail = tmp_path / "plans" / "01.md"
        detail.parent.mkdir(parents=True, exist_ok=True)
        detail.write_text("# phase\n\n## Real Heading\n\ncontent\n", encoding="utf-8")
        write_master(tmp_path, "# plan\n\n## Goal\n\ndemo\n\n- [ ] feature ([design](01.md#no-such-heading))\n")
        payload = gate_payload(tmp_path, None)
        failed = {check["id"] for check in payload["checks"] if check["status"] == "fail"}
        assert failed == {"detail-references-resolve"}
        assert "anchor" in payload["checks"][2]["detail"]

    def test_reference_escaping_root_fails(self, tmp_path):
        secret = tmp_path.parent / "outside-ref.md"
        secret.write_text("# outside\n")
        write_master(tmp_path, "# plan\n\n## Goal\n\ndemo\n\n- [ ] feature ([x](../../outside-ref.md))\n")
        payload = gate_payload(tmp_path, None)
        failed = {check["id"] for check in payload["checks"] if check["status"] == "fail"}
        assert failed == {"detail-references-resolve"}

    def test_missing_goal_section_fails(self, tmp_path):
        write_master(tmp_path, "# plan\n\n- [ ] feature\n")
        payload = gate_payload(tmp_path, None)
        failed = {check["id"] for check in payload["checks"] if check["status"] == "fail"}
        assert failed == {"goal-section-present"}

    def test_chinese_goal_heading_qualifies(self, tmp_path):
        write_master(tmp_path, "# 计划\n\n## 项目目标\n\n做一个小工具。\n\n- [ ] 功能一\n")
        payload = gate_payload(tmp_path, None)
        assert payload["ready"] is True

    def test_absolute_urls_and_images_are_not_validated(self, tmp_path):
        write_master(
            tmp_path,
            "# plan\n\n## Goal\n\ndemo\n\n- [ ] feature ([docs](https://example.com/a.md), "
            "![logo](img/logo.png), [frag](#section))\n",
        )
        payload = gate_payload(tmp_path, None)
        assert payload["ready"] is True

    def test_orphan_detail_doc_is_warning_only(self, tmp_path):
        write_good_cluster(tmp_path)
        orphan = tmp_path / "plans" / "99-old.md"
        orphan.write_text("# old\n\n## Legacy\n\nlegacy design\n")
        payload = gate_payload(tmp_path, None)
        assert payload["ready"] is True
        assert [warning["path"] for warning in payload["warnings"]] == ["plans/99-old.md"]

    def test_qualifying_detail_doc_is_not_orphan(self, tmp_path):
        write_good_cluster(tmp_path)
        detail = tmp_path / "plans" / "02-schema.md"
        detail.parent.mkdir(parents=True, exist_ok=True)
        detail.write_text("# schema\n\n- [ ] define tables\n", encoding="utf-8")
        payload = gate_payload(tmp_path, None)
        assert payload["ready"] is True
        assert payload["warnings"] == []


class TestGoalSectionDetection:
    def test_heading_with_content_counts(self):
        assert _has_goal_section("## Goal\n\ndeliver a demo\n") is True

    def test_empty_goal_section_does_not_count(self):
        assert _has_goal_section("## Goal\n\n## Phases\n\n- [ ] a\n") is False

    def test_goal_word_inside_body_does_not_count(self):
        assert _has_goal_section("## Phases\n\nthe goal is clear\n") is False


class TestChannelSplit:
    def test_error_goes_to_stderr_stdout_stays_parseable(self, tmp_path, capsys):
        assert main(["gate", "--root", str(tmp_path), "--format", "json"]) == 3
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["ready"] is False
        assert captured.err.startswith("error:")

    def test_bad_root_refused(self, capsys):
        assert main(["gate", "--root", "/definitely/not/a/dir"]) == 1
        assert "not a directory" in capsys.readouterr().err

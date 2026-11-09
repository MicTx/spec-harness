"""Question kinds share answer closure; only decisions require user authority."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_spec_package import compute_gate_results  # noqa: E402
from report_spec_package import package_status_summary, parse_tasks, render_status  # noqa: E402
from route_spec_package import route_for_package  # noqa: E402
from spec_package_support import autonomous_question_contract, extract_pending_questions  # noqa: E402
from spec_verify import RUNS_FILENAME  # noqa: E402

from tests.test_pending_question_closure import _make_checklist, _make_spec  # noqa: E402
from tests.test_spec_verify import git, init_repo, run_recorder  # noqa: E402

SLUG = "2026-11-07_test-autonomous-question"
TASKS = """- [x] Inspect format
  - id: task-inspect
  - boundary: local fixture only
  - verify: `python3 -c "print('ok')"`
"""


def make_spec(question: str) -> str:
    return _make_spec().replace("- 无\n", f"- {question}\n", 1)


def package_fixture(root: Path, question: str, tasks: str = TASKS) -> Path:
    git(root, "checkout", "-qb", f"spec/{SLUG}")
    package = root / ".spec" / "specs" / SLUG
    package.mkdir(parents=True)
    spec = make_spec(question).replace("spec/2026-09-13_fix-address-matching", f"spec/{SLUG}")
    (package / "spec.md").write_text(spec + "\n- evidence: runs\n", encoding="utf-8")
    (package / "tasks.md").write_text(tasks, encoding="utf-8")
    return package


@pytest.mark.parametrize("kind", ["fact", "reversible"])
def test_open_non_decision_is_autonomous_not_a_human_decision(kind):
    spec = make_spec(f"[ ] [{kind}] Confirm local format")
    questions, human, errors = autonomous_question_contract(spec, TASKS)
    assert [(question.kind, question.text, question.closed) for question in questions] == [
        (kind, "Confirm local format", False)
    ]
    assert human == []
    assert errors == []
    assert extract_pending_questions(spec) == [f"[{kind}] Confirm local format"]


def test_open_decision_is_user_facing():
    spec = make_spec("[ ] [decision] Choose service tier | reason: preference")
    questions, human, errors = autonomous_question_contract(spec, TASKS)
    assert questions == []
    assert human == ["Choose service tier"]
    assert errors == []
    assert extract_pending_questions(spec) == ["[decision] Choose service tier"]


@pytest.mark.parametrize("kind", ["fact", "reversible"])
@pytest.mark.parametrize("task_detail", ["", " | task: task-missing"])
def test_closed_autonomous_question_does_not_require_machine_task(kind, task_detail):
    spec = make_spec(f"[x] [{kind}] Confirm format | answer: JSON{task_detail}")
    questions, human, errors = autonomous_question_contract(spec, "")
    assert len(questions) == 1 and questions[0].closed
    assert not human and not errors
    results = compute_gate_results(spec, TASKS, _make_checklist())
    assert not results.pending_questions and not results.question_errors
    assert results.overall_ok


@pytest.mark.parametrize("kind", ["fact", "reversible", "decision"])
def test_checked_question_missing_answer_is_a_gate_error(kind):
    authority = " | authority: user:approval" if kind == "decision" else ""
    spec = make_spec(f"[x] [{kind}] Confirm format{authority}")
    _questions, _human, errors = autonomous_question_contract(spec, TASKS)
    assert any("answer:" in error for error in errors)
    results = compute_gate_results(spec, TASKS, _make_checklist())
    assert results.question_errors
    assert not results.overall_ok


@pytest.mark.parametrize("authority", ["", "agent:self", "user:", "user:待补充", "user:xxx"])
def test_decision_needs_a_meaningful_user_reference(authority):
    spec = make_spec(f"[x] [decision] Choose tier | answer: free | authority: {authority}")
    _questions, _human, errors = autonomous_question_contract(spec, TASKS)
    assert any("authority: user:" in error for error in errors)


def test_decision_with_user_reference_closes_without_autonomous_task():
    spec = make_spec("[x] [decision] Choose tier | answer: free | authority: user:current goal")
    questions, human, errors = autonomous_question_contract(spec, "")
    assert not questions and not human and not errors
    assert extract_pending_questions(spec) == []


@pytest.mark.parametrize("action", ["git push", "upload", "删除生产数据", "read credentials"])
def test_question_classification_does_not_infer_authority_from_keywords(action):
    spec = make_spec(f"[x] [fact] Review proposed action | answer: {action}")
    questions, human, errors = autonomous_question_contract(spec, "")
    assert len(questions) == 1 and questions[0].kind == "fact"
    assert not human and not errors


@pytest.mark.parametrize("kind", ["fact", "reversible"])
def test_open_autonomous_question_routes_to_run_without_a_task_binding(tmp_path, kind):
    root = init_repo(tmp_path)
    tasks = TASKS.replace("[x] Inspect", "[ ] Inspect")
    package = package_fixture(root, f"[ ] [{kind}] Confirm format", tasks)
    result = json.loads(route_for_package(root, SLUG, output_format="json"))
    assert result["stage"] == "run"
    assert result["humanQuestions"] == []
    assert result["autonomousQuestions"] == ["Confirm format"]
    assert result["questionErrors"] == []
    spec = (package / "spec.md").read_text(encoding="utf-8")
    sections, parsed = parse_tasks(tasks)
    status = render_status(SLUG, "Fixture", tasks, spec, None, sections, parsed)
    summary = package_status_summary(SLUG, tasks, spec, None, sections, parsed)
    assert "自主调查：Confirm format" in status
    assert "需要确认需求" not in status
    assert summary["pending"] == "无" and summary["inquiry"] == "Confirm format"


def test_open_decision_routes_to_user_confirmation(tmp_path):
    root = init_repo(tmp_path)
    tasks = TASKS.replace("[x] Inspect", "[ ] Inspect")
    package_fixture(root, "[ ] [decision] Choose tier | reason: preference", tasks)
    result = json.loads(route_for_package(root, SLUG, output_format="json"))
    assert result["stage"] == "status"
    assert result["humanQuestions"] == ["Choose tier"]
    assert result["autonomousQuestions"] == []
    assert "Choose tier" in result["nextAction"]


@pytest.mark.parametrize("question", ["[x] [fact] Confirm format", "[x] [decision] Choose tier | answer: free"])
def test_closed_question_without_required_fields_routes_to_repair(tmp_path, question):
    root = init_repo(tmp_path)
    tasks = TASKS.replace("[x] Inspect", "[ ] Inspect")
    package_fixture(root, question, tasks)
    result = json.loads(route_for_package(root, SLUG, output_format="json"))
    assert result["stage"] == "status"
    assert result["questionErrors"]
    assert "修复调查契约" in result["nextAction"]


@pytest.mark.parametrize("kind", ["fact", "reversible"])
def test_changing_an_answer_does_not_stale_machine_evidence(tmp_path, kind):
    root = init_repo(tmp_path)
    package = package_fixture(root, f"[x] [{kind}] Confirm format | answer: version 2")
    run_recorder(root, SLUG, "--all")
    records = [json.loads(line) for line in (package / RUNS_FILENAME).read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1 and records[0]["exit"] == 0
    assert set(records[0]) == {"task", "cmd", "exit", "duration_s", "stdout_tail_sha256", "tree_fp", "package_id", "at"}
    original = (package / "spec.md").read_text(encoding="utf-8")
    changed = original.replace("answer: version 2", "answer: version 3")
    (package / "spec.md").write_text(changed, encoding="utf-8")
    results = compute_gate_results(changed, TASKS, None, slug=SLUG, root=root, package_dir=package)
    assert results.runs_gate_ok is True
    assert results.question_errors == []
    assert results.overall_ok

"""Questions route to executable inquiry; closure requires bound, fresh proof."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_spec_package import compute_gate_results
from report_spec_package import package_status_summary, parse_tasks, render_multi_status, render_status
from route_spec_package import route_for_package
from spec_package_support import autonomous_question_contract, extract_pending_questions
from spec_verify import RUNS_FILENAME, evaluate_question_evidence, evaluate_runs_gate

from tests.test_pending_question_closure import _make_checklist, _make_spec
from tests.test_spec_verify import init_repo

ROOT = Path(__file__).resolve().parents[1]
SLUG = "2026-11-07_test-autonomous-question"
QUESTION = "[fact] Confirm local format | task: task-inspect | answer: version 2"
TASKS = """- [ ] Inspect format
  - id: task-inspect
  - boundary: local fixture only
  - verify: `python3 -c "from pathlib import Path; assert Path('format.txt').read_text() == '2'"`
"""


def make_spec(question):
    return "<!-- spec-format: 2 -->\n" + _make_spec().replace("- 无\n", f"- {question}\n") + "\n- evidence: runs\n"


def package_fixture(tmp_path, question="[ ] " + QUESTION, tasks=TASKS):
    package = tmp_path / ".spec/specs" / SLUG
    package.mkdir(parents=True)
    (package / "spec.md").write_text(make_spec(question), encoding="utf-8")
    (package / "tasks.md").write_text(tasks, encoding="utf-8")
    (package / "checklist.md").write_text(_make_checklist(), encoding="utf-8")
    (tmp_path / "format.txt").write_text("2", encoding="utf-8")
    return package


def route(root):
    return json.loads(route_for_package(root, SLUG, output_format="json", brief=True))


def test_fact_routes_to_inquiry_without_waiting_for_user(tmp_path):
    package_fixture(tmp_path)
    result = route(tmp_path)
    assert result["stage"] == "run"
    assert "自主调查" in json.dumps(result, ensure_ascii=False)
    assert "需要确认需求" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    "question",
    [
        "[ ] Original unclassified question",
        "[ ] [decision] Choose service tier | reason: preference",
        "[ ] [fact] Confirm format | task: missing",
        "[ ] [reversible] git push results | task: task-inspect",
    ],
)
def test_legacy_decision_and_invalid_inquiry_still_block(tmp_path, question):
    package_fixture(tmp_path, question)
    assert route(tmp_path)["stage"] == "status"


@pytest.mark.parametrize(
    "action",
    [
        "git commit",
        "git push",
        "upload",
        "read credentials",
        "change global config",
        "提交更改",
        "推送分支",
        "上传结果",
        "读取凭据",
        "读取密钥",
        "修改全局配置",
        "部署",
        "删除生产数据",
        "清空用户数据",
        "删除边界外文件",
    ],
)
def test_sensitive_action_cannot_be_relabelled(action):
    spec = make_spec(f"[ ] [fact] {action} | task: task-inspect")
    _questions, human, errors = autonomous_question_contract(spec, TASKS)
    assert human
    assert any("敏感动作" in error for error in errors)
    spec = make_spec("[ ] " + QUESTION)
    _questions, _human, errors = autonomous_question_contract(spec, TASKS.replace("local fixture only", action))
    assert any("敏感动作" in error for error in errors)
    spec = make_spec(f"[ ] [reversible] Select approach | task: task-inspect | choice: {action}")
    _questions, _human, errors = autonomous_question_contract(spec, TASKS)
    assert any("敏感动作" in error for error in errors)


@pytest.mark.parametrize("action", ["PUSH", "`git push`", "上传结果", "删除生产数据"])
@pytest.mark.parametrize("kind,field", [("fact", "answer"), ("reversible", "rationale"), ("reversible", "rollback")])
def test_sensitive_action_is_rejected_in_every_conclusion_field(kind, field, action):
    spec = make_spec(f"[ ] [{kind}] Inspect local format | task: task-inspect | {field}: {action}")
    _questions, human, errors = autonomous_question_contract(spec, TASKS)
    assert human
    assert any("敏感动作" in error for error in errors)


@pytest.mark.parametrize(
    "action", ["删除临时变量", "清理局部变量", "remove temporary variable", "inspect local fixture"]
)
def test_local_reversible_wording_does_not_trigger_permission_guard(action):
    spec = make_spec(f"[ ] [fact] {action} | task: task-inspect")
    questions, human, errors = autonomous_question_contract(spec, TASKS)
    assert len(questions) == 1
    assert not human and not errors


def test_checked_fact_without_machine_evidence_fails(tmp_path):
    package = package_fixture(
        tmp_path, "[x] " + QUESTION + " | evidence: runs:task-inspect", TASKS.replace("[ ]", "[x]")
    )
    results = compute_gate_results(
        (package / "spec.md").read_text(),
        (package / "tasks.md").read_text(),
        _make_checklist(),
        root=tmp_path,
        package_dir=package,
    )
    assert not results.assumptions_ok
    assert any("机器运行证据" in error for error in results.question_errors)


def test_reversible_choice_requires_rationale_and_rollback():
    spec = make_spec(
        "[x] [reversible] Choose parser | task: task-inspect | evidence: runs:task-inspect | choice: stdlib"
    )
    _questions, _human, errors = autonomous_question_contract(spec, TASKS.replace("[ ]", "[x]"))
    assert any("rationale" in error for error in errors)
    assert any("rollback" in error for error in errors)


def test_decision_closure_needs_user_reference():
    spec = make_spec("[x] [decision] Choose service tier | reason: preference | answer: paid | authority: agent:self")
    _questions, _human, errors = autonomous_question_contract(spec, TASKS)
    assert any("authority" in error for error in errors)


@pytest.mark.parametrize("authority", ["user:", "user:TODO", "agent:self"])
def test_decision_requires_meaningful_user_reference(authority):
    spec = make_spec(f"[x] [decision] Choose tier | reason: preference | answer: paid | authority: {authority}")
    _questions, _human, errors = autonomous_question_contract(spec, TASKS)
    assert errors


@pytest.mark.parametrize("question", ["[x] [fact]", "[x] [FACT] Unknown", "[fact] Unknown"])
def test_malformed_classified_question_never_silently_closes(question):
    _questions, _human, errors = autonomous_question_contract(make_spec(question), TASKS)
    assert errors


def test_invalid_inquiry_dependency_blocks(tmp_path):
    package_fixture(tmp_path, tasks=TASKS + "  - depends-on: task-missing\n")
    assert route(tmp_path)["stage"] == "status"


def test_inquiry_is_prioritized_over_unrelated_work(tmp_path):
    other = TASKS.replace("task-inspect", "task-other").replace("Inspect format", "Other work")
    package_fixture(tmp_path, tasks=other + TASKS)
    result = route(tmp_path)
    assert result["currentTask"] == "Inspect format"
    assert result["nextAction"] == "自主调查：Inspect format"
    assert result["humanQuestions"] == []


def test_inquiry_prerequisite_is_not_mislabelled(tmp_path):
    other = TASKS.replace("task-inspect", "task-other").replace("Inspect format", "Prepare fixture")
    package_fixture(tmp_path, tasks=other + TASKS + "  - depends-on: task-other\n")
    result = route(tmp_path)
    assert result["stage"] == "run"
    assert result["nextAction"] == "Prepare fixture"


def test_status_and_multi_status_never_request_human_for_valid_inquiry(tmp_path):
    other = TASKS.replace("task-inspect", "task-other").replace("Inspect format", "Other work")
    tasks_content = "## Work\n" + other + TASKS
    package = package_fixture(tmp_path, tasks=tasks_content)
    spec_content = (package / "spec.md").read_text()
    sections, tasks = parse_tasks(tasks_content)
    status = render_status(SLUG, "Test", tasks_content, spec_content, _make_checklist(), sections, tasks)
    assert "自主调查：Inspect format" in status
    assert "需要确认需求" not in status
    summary = package_status_summary(SLUG, tasks_content, spec_content, _make_checklist(), sections, tasks)
    assert summary["pending"] == "无"
    assert summary["inquiry"] == "Confirm local format"
    multi_status = render_multi_status(tmp_path, tmp_path / ".spec", [SLUG])
    assert "自主调查 Confirm local format" in multi_status
    assert "需要确认需求" not in multi_status


def test_status_reports_invalid_closed_question(tmp_path):
    tasks_content = "## Work\n" + TASKS.replace("[ ]", "[x]")
    package = package_fixture(tmp_path, "[x] " + QUESTION + " | evidence: runs:task-inspect", tasks_content)
    spec_content = (package / "spec.md").read_text()
    sections, tasks = parse_tasks(tasks_content)
    status = render_status(SLUG, "Test", tasks_content, spec_content, _make_checklist(), sections, tasks)
    assert "修复调查契约" in status
    multi_status = render_multi_status(tmp_path, tmp_path / ".spec", [SLUG])
    assert "修复调查契约" in multi_status


def test_conclusion_changed_during_verification_is_not_bound_as_success(tmp_path):
    init_repo(tmp_path)
    command = (
        'python3 -c "from pathlib import Path; '
        f"path = Path('.spec/specs/{SLUG}/spec.md'); "
        "path.write_text(path.read_text().replace('version 2', 'version 3'))\""
    )
    tasks = TASKS.split("  - verify:")[0] + f"  - verify: `{command}`\n"
    package = package_fixture(tmp_path, tasks=tasks)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/spec_verify.py"),
            "run",
            "--root",
            str(tmp_path),
            "--slug",
            SLUG,
            "--task",
            "task-inspect",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "验证期间问题或结论发生变化" in result.stderr
    record = json.loads((package / "runs.jsonl").read_text().splitlines()[-1])
    assert record["exit"] == -1
    assert "question_fps" not in record


@pytest.mark.parametrize("mutation", ["close", "downgrade", "delete"])
@pytest.mark.parametrize("classified", [False, True], ids=["unclassified", "decision"])
def test_verify_cannot_change_decision_questions(tmp_path, mutation, classified):
    init_repo(tmp_path)
    question = "[ ] [decision] Choose service tier | reason: preference" if classified else "[ ] Choose service tier"
    changes = {
        "close": (
            "[x] [decision] Choose service tier | reason: preference | answer: free | authority: user:claimed approval"
        ),
        "downgrade": "[ ] [fact] Choose service tier | task: task-inspect",
        "delete": "无",
    }
    if not classified:
        changes["close"] = "[x] Choose service tier"
    command = (
        'python3 -c "from pathlib import Path; '
        f"path = Path('.spec/specs/{SLUG}/spec.md'); "
        f'path.write_text(path.read_text().replace({question!r}, {changes[mutation]!r}))"'
    )
    tasks = TASKS.split("  - verify:")[0] + f"  - verify: `{command}`\n"
    package = package_fixture(tmp_path, question, tasks)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/spec_verify.py"),
            "run",
            "--root",
            str(tmp_path),
            "--slug",
            SLUG,
            "--task",
            "task-inspect",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "验证期间 decision 问题发生变化" in result.stderr
    record = json.loads((package / "runs.jsonl").read_text().splitlines()[-1])
    assert record["exit"] == -1
    assert "question_fps" not in record
    assert route(tmp_path)["stage"] == "status"


def test_decision_closed_outside_verify_remains_valid(tmp_path):
    init_repo(tmp_path)
    question = "[ ] [decision] Choose service tier | reason: preference"
    package = package_fixture(tmp_path, question)
    closed = (
        "[x] [decision] Choose service tier | reason: preference | answer: free | authority: user:approval before run"
    )
    (package / "spec.md").write_text(make_spec(closed))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/spec_verify.py"),
            "run",
            "--root",
            str(tmp_path),
            "--slug",
            SLUG,
            "--task",
            "task-inspect",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((package / "runs.jsonl").read_text().splitlines()[-1])
    assert record["exit"] == 0
    _questions, human, errors = autonomous_question_contract((package / "spec.md").read_text(), TASKS)
    assert not human and not errors


def test_legacy_string_interface_retains_labels():
    question = "[ ] " + QUESTION
    pending = extract_pending_questions(make_spec(question))
    assert pending == [QUESTION]
    rewritten = make_spec("[ ] " + pending[0])
    questions, human, errors = autonomous_question_contract(rewritten, TASKS)
    assert questions[0].kind == "fact"
    assert not human and not errors


def test_manual_evidence_cannot_be_used_for_autonomous_inquiry(tmp_path):
    package_fixture(tmp_path, tasks=TASKS.split("  - verify:")[0] + "  - verify: manual: trust me\n")
    assert route(tmp_path)["stage"] == "status"


def test_real_run_binds_question_and_conclusion_then_rejects_staleness(tmp_path):
    init_repo(tmp_path)
    package = package_fixture(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/spec_verify.py"),
            "run",
            "--root",
            str(tmp_path),
            "--slug",
            SLUG,
            "--task",
            "task-inspect",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    spec = make_spec("[x] " + QUESTION + " | evidence: runs:task-inspect")
    tasks = TASKS.replace("[ ]", "[x]")
    (package / "spec.md").write_text(spec)
    (package / "tasks.md").write_text(tasks)
    assert evaluate_question_evidence(spec, tasks, package, tmp_path) == []
    assert evaluate_question_evidence(spec.replace("version 2", "version 3"), tasks, package, tmp_path)
    (tmp_path / "format.txt").write_text("3")
    assert any("机器运行证据" in error for error in evaluate_question_evidence(spec, tasks, package, tmp_path))


def _record_bound_run(tmp_path, package):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/spec_verify.py"),
            "run",
            "--root",
            str(tmp_path),
            "--slug",
            SLUG,
            "--task",
            "task-inspect",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    spec = make_spec("[x] " + QUESTION + " | evidence: runs:task-inspect")
    tasks = TASKS.replace("[ ]", "[x]")
    (package / "spec.md").write_text(spec, encoding="utf-8")
    (package / "tasks.md").write_text(tasks, encoding="utf-8")
    return spec, tasks


def _archive(package, tmp_path, container=""):
    archived = tmp_path / ".spec" / "specs" / "archive" / container / SLUG
    archived.parent.mkdir(parents=True, exist_ok=True)
    package.rename(archived)
    return archived


@pytest.mark.parametrize("container", ["", "retired/2026-10-12"])
def test_archived_evidence_keeps_question_binding_without_live_freshness(tmp_path, container):
    init_repo(tmp_path)
    package = package_fixture(tmp_path)
    spec, tasks = _record_bound_run(tmp_path, package)
    assert evaluate_question_evidence(spec, tasks, package, tmp_path) == []

    archived = _archive(package, tmp_path, container)
    # Later work invalidates live freshness, not the archived answer's proof.
    (tmp_path / "format.txt").write_text("9", encoding="utf-8")
    assert evaluate_question_evidence(spec, tasks, archived, None, archived_root=tmp_path) == []
    assert evaluate_question_evidence(spec, tasks, archived, tmp_path)
    assert "未评估" in " ".join(evaluate_question_evidence(spec, tasks, archived, None))
    assert evaluate_runs_gate(spec, tasks, archived, None)[0] is None
    results = compute_gate_results(spec, tasks, _make_checklist(), package_dir=archived, archived_root=tmp_path)
    assert results.question_errors == []
    assert results.runs_gate_ok is None  # Historical run freshness stays frozen.
    assert evaluate_question_evidence(
        spec.replace("version 2", "version 3"), tasks, archived, None, archived_root=tmp_path
    )


@pytest.mark.parametrize(
    "change",
    [
        "exit-nonzero",
        "exit-abnormal",
        "exit-bool",
        "exit-float",
        "missing-run",
        "malformed-runs",
        "wrong-command",
        "changed-helper",
        "missing-identity",
        "wrong-identity",
        "unbound-answer",
        "invalid-verify",
        "duplicate-verify",
    ],
)
def test_archived_question_rejects_unusable_or_unbound_records(tmp_path, change):
    init_repo(tmp_path)
    package = package_fixture(tmp_path)
    spec, tasks = _record_bound_run(tmp_path, package)
    archived = _archive(package, tmp_path)
    runs = archived / RUNS_FILENAME
    record = json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])
    if change.startswith("exit-"):
        record["exit"] = {"exit-nonzero": 1, "exit-abnormal": -1, "exit-bool": False, "exit-float": 0.0}[change]
    elif change == "missing-run":
        record["task"] = "task-other"
    elif change == "wrong-command":
        record["cmd"] = "python3 -c 'print(42)'"
    elif change == "changed-helper":
        (archived / "helper.txt").write_text("new package input", encoding="utf-8")
    elif change == "missing-identity":
        record.pop("package_id")
    elif change == "wrong-identity":
        record["package_id"] = ".spec/specs/another-package"
    elif change == "unbound-answer":
        record.pop("question_fps")
    elif change == "invalid-verify":
        tasks = tasks.split("  - verify:")[0] + "  - verify: prose without a command\n"
    elif change == "duplicate-verify":
        tasks += "  - verify: `python3 -c 'assert True'`\n"
    runs.write_text("{not-json}\n" if change == "malformed-runs" else json.dumps(record) + "\n", encoding="utf-8")
    results = compute_gate_results(spec, tasks, _make_checklist(), package_dir=archived, archived_root=tmp_path)
    assert results.question_errors, change
    assert not results.assumptions_ok


def test_archived_question_cannot_reuse_identity_from_another_specs_root(tmp_path):
    init_repo(tmp_path)
    package = package_fixture(tmp_path)
    spec, tasks = _record_bound_run(tmp_path, package)
    archived = _archive(package, tmp_path)
    copied = tmp_path / "other" / "specs" / "archive" / SLUG
    shutil.copytree(archived, copied)
    assert evaluate_question_evidence(spec, tasks, copied, None, archived_root=tmp_path)


def test_frozen_question_proof_never_accepts_a_live_package(tmp_path):
    init_repo(tmp_path)
    package = package_fixture(tmp_path)
    spec, tasks = _record_bound_run(tmp_path, package)
    assert evaluate_runs_gate(spec, tasks, package, None)[0] is None
    assert evaluate_question_evidence(spec, tasks, package, None, archived_root=tmp_path)


def test_archived_question_cannot_pass_without_per_task_states(tmp_path, monkeypatch):
    import spec_verify

    init_repo(tmp_path)
    package = package_fixture(tmp_path)
    spec, tasks = _record_bound_run(tmp_path, package)
    archived = _archive(package, tmp_path)
    monkeypatch.setattr(spec_verify, "_runs_gate_states", lambda *args, **kwargs: (None, [], "适用外", {}))
    assert evaluate_question_evidence(spec, tasks, archived, None, archived_root=tmp_path)

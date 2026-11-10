from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_complete_spec_package import _COMPLETE_CHECKLIST, _COMPLETE_SPEC_EN

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

SLUG = "2026-09-05_add-end-to-end"


def command(root, *args):
    result = subprocess.run(list(args), cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def attempt(root, *args):
    return subprocess.run(list(args), cwd=root, capture_output=True, text=True)


def cli(root, slug: str, script: str, *extra: str):
    return [sys.executable, str(SCRIPTS / script), "--root", str(root), "--slug", slug, *extra]


def git(root, *args):
    return command(root, "git", *args)


def test_package_lifecycle_check_and_archive(tmp_path):
    """Init → check → complete --archive archives once and keeps gates green."""
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "Fixture")
    (tmp_path / "README").write_text("fixture")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "initial")
    command(
        tmp_path,
        sys.executable,
        str(SCRIPTS / "init_spec_package.py"),
        "--root",
        str(tmp_path),
        "--slug",
        SLUG,
        "--title",
        "Lifecycle",
    )
    package = tmp_path / ".spec/specs" / SLUG
    (package / "spec.md").write_text(_COMPLETE_SPEC_EN + f"\n- Git integration branch：`spec/{SLUG}`\n")
    path = package / "tasks.md"
    path.write_text(
        "- [x] Prepare\n  - id: task-prepare\n  - boundary: inputs\n  - verify: pytest\n"
        "- [x] Review\n  - id: task-review\n  - depends-on: task-prepare\n"
        "  - boundary: findings\n  - verify: pytest\n"
    )
    (package / "checklist.md").write_text(_COMPLETE_CHECKLIST)
    command(tmp_path, sys.executable, str(SCRIPTS / "check_spec_package.py"), "--root", str(tmp_path), "--slug", SLUG)
    command(
        tmp_path,
        sys.executable,
        str(SCRIPTS / "complete_spec_package.py"),
        "--root",
        str(tmp_path),
        "--slug",
        SLUG,
        "--archive",
    )
    assert (package.parent / "archive" / SLUG / "completion-summary.md").is_file()


def test_dependency_cycle_is_visible_to_route_and_stop_guard(tmp_path):
    from tests.test_stop_guard_hook import run_hook, stop_payload

    package = tmp_path / ".spec/specs" / SLUG
    package.mkdir(parents=True)
    (package / "spec.md").write_text(_COMPLETE_SPEC_EN)
    (package / "checklist.md").write_text(_COMPLETE_CHECKLIST)
    (package / "tasks.md").write_text(
        "- [x] A\n  - id: task-a\n  - depends-on: task-b\n  - boundary: a\n  - verify: pytest\n"
        "- [x] B\n  - id: task-b\n  - depends-on: task-a\n  - boundary: b\n  - verify: pytest\n"
    )
    route = json.loads(
        command(
            tmp_path,
            sys.executable,
            str(SCRIPTS / "route_spec_package.py"),
            "--root",
            str(tmp_path),
            "--format",
            "json",
        )
    )
    assert "cycle" in route["dependencyErrors"][0]
    # Dependency errors are advisory for the Stop hook: it only blocks on
    # unfinished tasks or open [decision] questions.
    code, output = run_hook(stop_payload(tmp_path))
    assert code == 0 and output.strip() == ""


def _recovery_repo(tmp_path, slug: str, *, spec_extra: str, tasks_text: str):
    """Temp Git project on the package branch; the fixture plays the model role."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "Fixture")
    (tmp_path / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    (tmp_path / "README").write_text("fixture\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "initial")
    git(tmp_path, "checkout", "-qb", f"spec/{slug}")
    package = tmp_path / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    spec = _COMPLETE_SPEC_EN + f"\n- Git integration branch：`spec/{slug}`\n{spec_extra}"
    (package / "spec.md").write_text(spec, encoding="utf-8")
    (package / "tasks.md").write_text(tasks_text, encoding="utf-8")
    (package / "checklist.md").write_text(_COMPLETE_CHECKLIST, encoding="utf-8")
    return package


def test_failed_evidence_converges_through_writeback_and_run(tmp_path):
    """check -> append one repair task -> route run -> fix -> final tree -> done.

    The repair is a deterministic fixture action, not a live model run.
    """
    slug = "2026-11-09_proof-recovery-loop"
    check_cmd = (
        "python3 -c \"import pathlib; v=pathlib.Path('app.py').read_text().strip(); "
        "raise SystemExit(0 if v == 'VALUE = 1' else 1)\""
    )
    package = _recovery_repo(
        tmp_path,
        slug,
        spec_extra="\n本包启用 evidence: runs 协议。\n",
        tasks_text=f"- [x] Implement value\n  - id: task-value\n  - boundary: app.py\n  - verify: `{check_cmd}`\n",
    )
    verify = [sys.executable, str(SCRIPTS / "spec_verify.py"), "run", "--root", str(tmp_path), "--slug", slug, "--all"]
    checked = cli(tmp_path, slug, "check_spec_package.py", "--format", "json")
    routed = cli(tmp_path, slug, "route_spec_package.py", "--format", "json")

    assert attempt(tmp_path, *verify).returncode != 0
    first_check = json.loads(attempt(tmp_path, *checked).stdout)
    assert first_check["overall"] is False
    assert set(first_check["gateFailures"]) == {"Runs evidence"}
    assert first_check["recovery"] == {"Runs evidence": "run"}

    tasks_path = package / "tasks.md"
    repair = (
        f"- [ ] Repair value\n  - id: task-repair\n  - depends-on: task-value\n"
        f"  - boundary: app.py\n  - verify: `{check_cmd}`\n"
    )
    tasks_path.write_text(tasks_path.read_text(encoding="utf-8") + repair, encoding="utf-8")
    route = json.loads(attempt(tmp_path, *routed).stdout)
    assert route["stage"] == "run"
    assert route["currentTask"] == "Repair value"
    assert route["nextAction"] == "Repair value"

    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert attempt(tmp_path, *verify).returncode == 0
    ticked = tasks_path.read_text(encoding="utf-8").replace("- [ ] Repair value", "- [x] Repair value")
    tasks_path.write_text(ticked, encoding="utf-8")
    assert attempt(tmp_path, *verify).returncode == 0
    final_check = json.loads(attempt(tmp_path, *checked).stdout)
    assert final_check["overall"] is True
    assert final_check["converged"] is True and final_check["gaps"] == []
    assert json.loads(attempt(tmp_path, *routed).stdout)["stage"] == "done"


@pytest.mark.parametrize("old_verify", ["  - verify: passes", "  - verify: `true`\n  - verify: `true`"])
def test_corrected_completed_verify_can_recover_with_retained_history(tmp_path, old_verify):
    """A model-role fixture records the old contract, then re-verifies the corrected task."""
    slug = "2026-11-09_completed-verify-repair"
    package = _recovery_repo(
        tmp_path,
        slug,
        spec_extra="\nevidence: runs\n",
        tasks_text=f"- [x] Initial task\n  - id: task-original\n  - boundary: app.py\n{old_verify}\n",
    )
    check = cli(tmp_path, slug, "check_spec_package.py", "--format", "json")
    verify = [sys.executable, str(SCRIPTS / "spec_verify.py"), "run", "--root", str(tmp_path), "--slug", slug, "--all"]
    initial = json.loads(attempt(tmp_path, *check).stdout)
    assert initial["recovery"]["Runs evidence"] == "update"
    assert attempt(tmp_path, *verify).returncode == 1

    tasks_path = package / "tasks.md"
    old = tasks_path.read_text(encoding="utf-8")
    corrected = old.replace("- [x] Initial task", "- [ ] Initial task").replace(old_verify, "  - verify: `true`")
    history = f"<!-- correction task-original: invalid verify {old_verify!r} -->\n"
    tasks_path.write_text(corrected + "\n" + history)
    spec_path = package / "spec.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8") + "\n- correction: task-original invalid verify -> `true`\n"
    )
    assert attempt(tmp_path, *verify).returncode == 0
    tasks_path.write_text(tasks_path.read_text(encoding="utf-8").replace("- [ ] Initial task", "- [x] Initial task"))
    assert attempt(tmp_path, *verify).returncode == 0
    result = json.loads(attempt(tmp_path, *check).stdout)
    assert result["overall"] is True
    assert history in tasks_path.read_text(encoding="utf-8")
    check_doc = (SCRIPTS.parent / "references/stages/check.md").read_text(encoding="utf-8")
    assert "verify" in check_doc and "evidence" in check_doc


def test_open_decision_keeps_the_authority_boundary(tmp_path):
    slug = "2026-11-09_proof-authority"
    package = _recovery_repo(
        tmp_path,
        slug,
        spec_extra="",
        tasks_text="- [ ] Publish release\n  - id: task-publish\n  - boundary: README\n  - verify: `true`\n",
    )
    spec_path = package / "spec.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace(
            "### 2.3 待确认问题\n- 无\n", "### 2.3 待确认问题\n- [ ] [decision] Publish?\n"
        ),
        encoding="utf-8",
    )
    routed = cli(tmp_path, slug, "route_spec_package.py", "--format", "json")
    checked = cli(tmp_path, slug, "check_spec_package.py", "--format", "json")
    route = json.loads(attempt(tmp_path, *routed).stdout)
    assert route["stage"] == "status"
    assert route["humanQuestions"] == ["Publish?"]
    assert route["nextAction"] == "Confirm requirements: Publish?"
    check = json.loads(attempt(tmp_path, *checked).stdout)
    assert check["recovery"]["Open questions"] == "authority"
    assert check["recovery"]["Open questions"].split("+") == ["authority"]

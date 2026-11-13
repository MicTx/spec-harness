import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ui_review  # noqa: E402

from tests.v3_support import SLUG, cli, make_package, repo, started_package

PNG = b"\x89PNG\r\n\x1a\n" + b"minimal screenshot"
JPEG = b"\xff\xd8\xff" + b"minimal screenshot"


def _ui_package(root: Path, *, extra_scope: str = "web/**", patch: bool = True) -> Path:
    repo(root)
    package = make_package(root)
    spec_path = package / "spec.md"
    spec = spec_path.read_text(encoding="utf-8").replace("- in: app.py", f"- in: {extra_scope}\n- in: app.py")
    spec_path.write_text(spec, encoding="utf-8")
    tasks_path = package / "tasks.md"
    tasks_path.write_text(
        tasks_path.read_text(encoding="utf-8").replace("- boundary: app.py", "- boundary: web/app.py, app.py"),
        encoding="utf-8",
    )
    web = root / "web"
    web.mkdir()
    (web / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "web/app.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "add UI target"], check=True, capture_output=True)
    if patch:
        (web / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    return package


def _finished_ui_package(root: Path) -> Path:
    package = _ui_package(root)
    assert cli(root, "plan-go").returncode == 0
    assert cli(root, "verify", "task-value").returncode == 0
    return package


def _draft(package: Path, root: Path, *, name: str = "flow.png", data: bytes = PNG) -> Path:
    evidence = package / ui_review.EVIDENCE_DIR
    evidence.mkdir(parents=True, exist_ok=True)
    screenshot = evidence / name
    screenshot.write_bytes(data)
    draft = root.parent / f"{root.name}-ui-review-draft.json"
    draft.write_text(
        json.dumps(
            {
                "patterns": ["web/routes/**"],
                "flows": [
                    {
                        "id": "happy-path",
                        "action": "Open the page, click Save, and inspect the result in a real browser",
                        "target": "/settings",
                        "expected": "The saved state is visible and the action completes",
                        "observed": "The saved state appeared after the real click",
                        "result": "pass",
                        "criteria": ["C1"],
                    }
                ],
                "screenshots": [
                    {
                        "path": f"{ui_review.EVIDENCE_DIR}/{name}",
                        "step": "happy-path",
                        "description": "Saved state after the real click",
                    }
                ],
                "findings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return draft


def _write_review(root: Path, draft: Path) -> subprocess.CompletedProcess:
    return cli(root, "ui-review", "--file", str(draft))


def _accepted_finding() -> dict:
    return {
        "id": "F1",
        "issue": "Secondary theme control is hidden at 390px",
        "impact": "Theme switching is harder on small screens",
        "reproduction": "Open /settings at 390px and look for the theme control",
        "solution": "Accept the current release risk and revisit in the next UI package",
        "status": "accepted",
        "step": "happy-path",
        "accepted_decision": "D1",
    }


def test_path_activation_covers_ui_files_directories_and_extra_patterns():
    assert ui_review.is_ui_path("src/App.tsx")
    assert ui_review.is_ui_path("web/app.py")
    assert ui_review.is_ui_path("public/logo.png")
    assert ui_review.is_ui_path("i18n/zh.json")
    assert ui_review.is_ui_path("backend/routes/new.jsx", ["backend/routes/**"])
    assert not ui_review.is_ui_path("backend/api.py")
    assert not ui_review.is_ui_path("../outside.css")


def test_ui_package_check_fails_until_real_operation_evidence_is_written_back(tmp_path):
    package = _finished_ui_package(tmp_path)

    result = cli(tmp_path, "check", "--check-only")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert any("ui-review.json is missing" in error for error in payload["errors"])
    assert payload["ui_review"]["required"] is True
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()

    draft = _draft(package, tmp_path)
    written = _write_review(tmp_path, draft)
    assert written.returncode == 0, written.stderr
    assert (package / ui_review.RECORD_NAME).is_file()
    passed = cli(tmp_path, "check", "--check-only")
    assert passed.returncode == 0, passed.stderr
    assert json.loads(passed.stdout)["passed"] is True


def test_ui_review_auto_delivery_archives_structured_evidence_and_summary(tmp_path):
    package = _finished_ui_package(tmp_path)
    draft = _draft(package, tmp_path)
    assert _write_review(tmp_path, draft).returncode == 0

    result = cli(tmp_path, "check")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["passed"] is True
    archive = tmp_path / ".spec/specs/archive" / SLUG
    record = json.loads((archive / ui_review.RECORD_NAME).read_text(encoding="utf-8"))
    assert record["schema_version"] == ui_review.SCHEMA_VERSION
    assert record["tree_fp"]
    assert record["screenshots"][0]["sha256"]
    assert (archive / ui_review.EVIDENCE_DIR / "flow.png").is_file()
    summary = (archive / "completion-summary.md").read_text(encoding="utf-8")
    assert "## UI Review" in summary and ui_review.RECORD_NAME in summary


def test_non_ui_package_keeps_the_existing_four_step_lifecycle(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    result = cli(tmp_path, "check", "--check-only")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ui_review"]["required"] is False
    assert payload["passed"] is True


def test_check_failure_points_to_the_ui_review_workflow(tmp_path):
    _finished_ui_package(tmp_path)

    result = cli(tmp_path, "next")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["state"] == "stalled"
    assert "UI/UX review" in payload["next"]


@pytest.mark.parametrize("mutator", ["remove-criteria", "empty-flows", "bad-result"])
def test_invalid_flow_is_rejected_before_writing_the_record(tmp_path, mutator):
    package = _finished_ui_package(tmp_path)
    draft = _draft(package, tmp_path)
    payload = json.loads(draft.read_text(encoding="utf-8"))
    if mutator == "remove-criteria":
        payload["flows"][0]["criteria"] = []
    elif mutator == "empty-flows":
        payload["flows"] = []
    else:
        payload["flows"][0]["result"] = "unknown"
    draft.write_text(json.dumps(payload), encoding="utf-8")

    result = _write_review(tmp_path, draft)

    assert result.returncode == 1
    assert not (package / ui_review.RECORD_NAME).exists()


def test_screenshot_must_exist_be_valid_and_match_its_digest(tmp_path):
    package = _finished_ui_package(tmp_path)
    draft = _draft(package, tmp_path, name="flow.jpg", data=JPEG)
    payload = json.loads(draft.read_text(encoding="utf-8"))
    payload["screenshots"][0]["path"] = f"{ui_review.EVIDENCE_DIR}/missing.png"
    draft.write_text(json.dumps(payload), encoding="utf-8")
    assert _write_review(tmp_path, draft).returncode == 1

    draft = _draft(package, tmp_path, name="flow.png", data=b"not a screenshot")
    assert _write_review(tmp_path, draft).returncode == 1

    draft = _draft(package, tmp_path)
    assert _write_review(tmp_path, draft).returncode == 0
    (package / ui_review.EVIDENCE_DIR / "flow.png").write_bytes(b"\x89PNG\r\n\x1a\ntampered")
    gate = cli(tmp_path, "check", "--check-only")
    assert gate.returncode == 1
    assert any("sha256 does not match" in error for error in json.loads(gate.stdout)["errors"])


def test_finding_must_reference_a_verified_fix_or_user_accepted_decision(tmp_path):
    package = _finished_ui_package(tmp_path)
    draft = _draft(package, tmp_path)
    payload = json.loads(draft.read_text(encoding="utf-8"))
    payload["findings"] = [_accepted_finding()]
    payload["findings"][0]["status"] = "fixed"
    payload["findings"][0]["fix_task"] = "task-value"
    payload["findings"][0]["accepted_decision"] = None
    del payload["findings"][0]["step"]
    draft.write_text(json.dumps(payload), encoding="utf-8")
    assert _write_review(tmp_path, draft).returncode == 0

    payload["findings"][0]["status"] = "accepted"
    payload["findings"][0]["fix_task"] = None
    payload["findings"][0]["step"] = "happy-path"
    payload["findings"][0]["accepted_decision"] = "D99"
    draft.write_text(json.dumps(payload), encoding="utf-8")
    assert _write_review(tmp_path, draft).returncode == 1

    assert cli(tmp_path, "decide", "Accept theme risk", "--by", "user current").returncode == 0
    payload["findings"][0]["accepted_decision"] = "D1"
    draft.write_text(json.dumps(payload), encoding="utf-8")
    assert _write_review(tmp_path, draft).returncode == 0
    assert cli(tmp_path, "check", "--check-only").returncode == 0


def test_failed_flow_requires_a_closed_finding(tmp_path):
    package = _finished_ui_package(tmp_path)
    draft = _draft(package, tmp_path)
    payload = json.loads(draft.read_text(encoding="utf-8"))
    payload["flows"].append(
        {
            "id": "theme",
            "action": "Switch theme at mobile width in a real browser",
            "target": "/settings",
            "expected": "Theme changes",
            "observed": "Control is hidden",
            "result": "fail",
            "criteria": ["C1"],
            "finding": "F1",
        }
    )
    payload["screenshots"].append(
        {
            "path": f"{ui_review.EVIDENCE_DIR}/theme.png",
            "step": "theme",
            "description": "Hidden theme control",
        }
    )
    (package / ui_review.EVIDENCE_DIR / "theme.png").write_bytes(PNG)
    draft.write_text(json.dumps(payload), encoding="utf-8")
    first = _write_review(tmp_path, draft)
    assert first.returncode == 1
    assert "no valid finding reference" in first.stderr

    payload["findings"] = [_accepted_finding()]
    payload["findings"][0]["step"] = "theme"
    draft.write_text(json.dumps(payload), encoding="utf-8")
    assert _write_review(tmp_path, draft).returncode == 1
    payload["findings"][0]["accepted_decision"] = "D99"
    draft.write_text(json.dumps(payload), encoding="utf-8")
    assert _write_review(tmp_path, draft).returncode == 1
    assert cli(tmp_path, "decide", "Accept theme risk", "--by", "user current").returncode == 0
    payload["findings"][0]["accepted_decision"] = "D1"
    draft.write_text(json.dumps(payload), encoding="utf-8")
    assert _write_review(tmp_path, draft).returncode == 0


def test_product_tree_change_invalidates_ui_review_until_it_is_rerun(tmp_path):
    package = _finished_ui_package(tmp_path)
    draft = _draft(package, tmp_path)
    assert _write_review(tmp_path, draft).returncode == 0
    (tmp_path / "web" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = cli(tmp_path, "check", "--check-only")

    assert result.returncode == 1
    assert any("stale for the current product tree" in error for error in json.loads(result.stdout)["errors"])
    assert _write_review(tmp_path, draft).returncode == 0
    after = cli(tmp_path, "check", "--check-only")
    assert after.returncode == 1
    assert any("evidence is stale" in error for error in json.loads(after.stdout)["errors"])
    delivered = cli(tmp_path, "check")
    assert delivered.returncode == 0, delivered.stderr


def test_extra_patterns_activate_a_backend_review_without_disabling_default_rules(tmp_path):
    package = _ui_package(tmp_path, extra_scope="backend/**", patch=False)
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "routes.py").write_text("ROUTE = 'x'\n", encoding="utf-8")
    spec = (package / "spec.md").read_text(encoding="utf-8").replace("- in: web/**", "- in: backend/**")
    (package / "spec.md").write_text(spec, encoding="utf-8")
    tasks = (
        (package / "tasks.md")
        .read_text(encoding="utf-8")
        .replace("- boundary: web/app.py, app.py", "- boundary: backend/routes.py, app.py")
    )
    (package / "tasks.md").write_text(tasks, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "backend/routes.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "add backend"], check=True, capture_output=True)
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    probe = cli(tmp_path, "check", "--check-only")
    assert probe.returncode == 0
    assert json.loads(probe.stdout)["ui_review"]["required"] is False
    draft = _draft(package, tmp_path)
    payload = json.loads(draft.read_text(encoding="utf-8"))
    payload["patterns"] = ["backend/routes.py"]
    draft.write_text(json.dumps(payload), encoding="utf-8")
    assert _write_review(tmp_path, draft).returncode == 0
    assert json.loads(cli(tmp_path, "check", "--check-only").stdout)["ui_review"]["required"] is True


def test_archived_packages_without_a_ui_record_are_read_only_compatible(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "done").returncode == 0
    archive = tmp_path / ".spec/specs/archive" / SLUG
    shutil.rmtree(archive / ui_review.EVIDENCE_DIR, ignore_errors=True)

    from check_all import package_errors

    assert package_errors(tmp_path, archive, True, False) == []


def test_archived_ui_record_is_statically_validated_without_tree_fingerprint(tmp_path):
    package = _finished_ui_package(tmp_path)
    draft = _draft(package, tmp_path)
    assert _write_review(tmp_path, draft).returncode == 0
    assert cli(tmp_path, "done").returncode == 0
    archive = tmp_path / ".spec/specs/archive" / SLUG

    from check_all import package_errors

    assert package_errors(tmp_path, archive, True, False) == []
    record_path = archive / ui_review.RECORD_NAME
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["tree_fp"] = "stale"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    assert any("tree_fp" in item for item in package_errors(tmp_path, archive, True, False)) is False

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "claude_stop_guard.py"

CONVERGED_PACKAGE = "2026-09-01_fix-guard-converged"
UNCONVERGED_PACKAGE = "2026-09-02_add-guard-unconverged"

CONVERGED_SPEC = "# Guard Test - 项目范围\n"
CONVERGED_TASKS = """\
## Phase
- [x] Task A
  - boundary: only x
  - verify: passes
- [x] Task B
  - boundary: only y
  - verify: works
"""
CONVERGED_CHECKLIST = """\
## 基础
- [x] Item 1
- [x] Item 2

## 验收证据
- 脚本验证：pytest -q

**验收结果**：通过
"""
UNCONVERGED_TASKS = """\
## Phase
- [x] Task A
  - boundary: only x
  - verify: passes
- [ ] Task B
  - boundary: only y
  - verify: works
"""


def run_hook(payload):
    completed = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.returncode, completed.stdout


def make_package(root: Path, slug: str, *, converged: bool) -> Path:
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True, exist_ok=True)
    spec_content = CONVERGED_SPEC + f"- Git integration branch：`spec/{slug}`\n"
    (package / "spec.md").write_text(spec_content, encoding="utf-8")
    (package / "tasks.md").write_text(CONVERGED_TASKS if converged else UNCONVERGED_TASKS, encoding="utf-8")
    (package / "checklist.md").write_text(CONVERGED_CHECKLIST, encoding="utf-8")
    return package


def stop_payload(cwd):
    return {"session_id": "test-session", "cwd": str(cwd), "hook_event_name": "Stop", "stop_hook_active": False}


def test_unconverged_package_blocks_with_gap_list(tmp_path):
    make_package(tmp_path, UNCONVERGED_PACKAGE, converged=False)
    code, stdout = run_hook(stop_payload(tmp_path))
    assert code == 0
    decision = json.loads(stdout)
    assert decision["decision"] == "block"
    assert UNCONVERGED_PACKAGE in decision["reason"]
    assert "未勾任务 1 项" in decision["reason"]


def test_converged_package_allows_stop(tmp_path):
    make_package(tmp_path, CONVERGED_PACKAGE, converged=True)
    code, stdout = run_hook(stop_payload(tmp_path))
    assert code == 0
    assert stdout.strip() == ""


def test_project_without_spec_tree_allows_stop(tmp_path):
    (tmp_path / "README.md").write_text("not a spec project\n", encoding="utf-8")
    code, stdout = run_hook(stop_payload(tmp_path))
    assert code == 0
    assert stdout.strip() == ""


def test_spec_root_found_from_nested_cwd(tmp_path):
    make_package(tmp_path, UNCONVERGED_PACKAGE, converged=False)
    nested = tmp_path / "packages" / "app"
    nested.mkdir(parents=True)
    code, stdout = run_hook(stop_payload(nested))
    assert code == 0
    assert json.loads(stdout)["decision"] == "block"


def test_non_git_multi_package_stop_guard_fails_closed(tmp_path):
    make_package(tmp_path, "2026-08-01_fix-guard-old", converged=False)
    make_package(tmp_path, UNCONVERGED_PACKAGE, converged=False)
    code, stdout = run_hook(stop_payload(tmp_path))
    decision = json.loads(stdout)
    assert decision["decision"] == "block"
    assert "已隐藏跨包数据" in decision["reason"]
    assert UNCONVERGED_PACKAGE not in decision["reason"]
    assert "2026-08-01" not in decision["reason"]


def test_stop_hook_binds_to_current_integration_branch_not_newest(tmp_path):
    old = "2026-08-01_fix-current-branch"
    newest = "2026-09-09_fix-newest-other-branch"
    old_package = make_package(tmp_path, old, converged=False)
    newest_package = make_package(tmp_path, newest, converged=True)
    (old_package / "spec.md").write_text(CONVERGED_SPEC + f"- Git integration branch：`spec/{old}`\n", encoding="utf-8")
    (newest_package / "spec.md").write_text(
        CONVERGED_SPEC + f"- Git integration branch：`spec/{newest}`\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Spec Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    subprocess.run(["git", "switch", "-qc", f"spec/{old}"], cwd=tmp_path, check=True)
    code, stdout = run_hook(stop_payload(tmp_path))
    decision = json.loads(stdout)
    assert code == 0
    assert old in decision["reason"]
    assert newest not in decision["reason"]


def test_stop_hook_fails_closed_when_branch_does_not_bind_package(tmp_path):
    first = "2026-09-08_fix-first"
    second = "2026-09-09_fix-second"
    for slug in (first, second):
        package = make_package(tmp_path, slug, converged=False)
        (package / "spec.md").write_text(
            CONVERGED_SPEC + f"- Git integration branch：`spec/{slug}`\n", encoding="utf-8"
        )
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    code, stdout = run_hook(stop_payload(tmp_path))
    decision = json.loads(stdout)
    assert code == 0
    assert decision["decision"] == "block"
    assert "已隐藏跨包数据" in decision["reason"]
    assert first not in decision["reason"] and second not in decision["reason"]


def test_archived_packages_are_ignored(tmp_path):
    make_package(tmp_path, CONVERGED_PACKAGE, converged=True)
    archived = tmp_path / ".spec" / "specs" / "archive" / "2026-09-09_fix-guard-archived"
    archived.mkdir(parents=True)
    (archived / "spec.md").write_text(CONVERGED_SPEC, encoding="utf-8")
    (archived / "tasks.md").write_text(UNCONVERGED_TASKS, encoding="utf-8")
    (archived / "checklist.md").write_text(CONVERGED_CHECKLIST, encoding="utf-8")
    code, stdout = run_hook(stop_payload(tmp_path))
    assert code == 0
    assert stdout.strip() == ""


def test_stop_hook_active_suppresses_repeat_block(tmp_path):
    make_package(tmp_path, UNCONVERGED_PACKAGE, converged=False)
    payload = stop_payload(tmp_path)
    payload["stop_hook_active"] = True
    code, stdout = run_hook(payload)
    assert code == 0
    assert stdout.strip() == ""


def test_invalid_stdin_stays_silent(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not-json",
        capture_output=True,
        text=True,
        check=True,
        cwd=str(tmp_path),
    )
    assert completed.stdout.strip() == ""


def test_non_stop_event_is_ignored(tmp_path):
    make_package(tmp_path, UNCONVERGED_PACKAGE, converged=False)
    payload = stop_payload(tmp_path)
    payload["hook_event_name"] = "SubagentStop"
    code, stdout = run_hook(payload)
    assert code == 0
    assert stdout.strip() == ""


def test_legacy_helper_runtime_is_never_read_or_reported(tmp_path):
    make_package(tmp_path, UNCONVERGED_PACKAGE, converged=False)
    legacy = tmp_path / ".agents" / "runtime" / "helpers" / UNCONVERGED_PACKAGE / "run-legacy"
    legacy.mkdir(parents=True)
    (legacy / "request.json").write_text(
        json.dumps({"runId": "run-legacy", "origin": "manual", "role": "reviewer"}), encoding="utf-8"
    )
    (legacy / "state.json").write_text(json.dumps({"runId": "run-legacy", "status": "running"}), encoding="utf-8")
    (legacy / "watchdog.json").write_text(json.dumps({"state": "watching", "health": "damaged"}), encoding="utf-8")
    code, stdout = run_hook(stop_payload(tmp_path))
    decision = json.loads(stdout)
    assert code == 0
    assert decision["decision"] == "block"
    assert "run-legacy" not in decision["reason"]
    assert "助手" not in decision["reason"]

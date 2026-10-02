"""Regressions for release smoke fixtures and completion option precedence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_test_spec_skill import assert_runtime_layout, setup_git_repo  # noqa: E402
from spec_package_support import extract_integration_branch, validate_branch_bound_package  # noqa: E402


@pytest.mark.parametrize("git_repo", [False, True])
@pytest.mark.parametrize("package_state", ["missing", "incomplete", "invalid-encoding"])
def test_conflicting_completion_options_fail_before_package_state(tmp_path, git_repo, package_state):
    slug = "2026-09-05_fix-completion-options"
    if git_repo:
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    package = tmp_path / ".spec" / "specs" / slug
    if package_state != "missing":
        package.mkdir(parents=True)
        (package / "spec.md").write_bytes(b"\xff" if package_state == "invalid-encoding" else b"# Incomplete\n")
        (package / "tasks.md").write_text("- [ ] Task\n", encoding="utf-8")
        (package / "checklist.md").write_text("- [ ] Check\n", encoding="utf-8")
    before = {p.relative_to(package): p.read_bytes() for p in package.glob("*")}
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "complete_spec_package.py"),
            "--root",
            str(tmp_path),
            "--slug",
            slug,
            "--allow-incomplete",
            "--archive",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "cannot be combined with --archive" in result.stderr
    assert result.stdout == ""
    assert {p.relative_to(package): p.read_bytes() for p in package.glob("*")} == before
    assert not (tmp_path / ".spec" / "specs" / "archive").exists()


def test_smoke_git_setup_binds_packages_without_duplicate_branches(tmp_path):
    packages = [
        (".spec", "2026-06-12_smoke-test", "spec/test"),
        (".spec", "2026-06-12_second-pack", "spec/2026-06-12_second-pack"),
        ("spec-state", "2026-06-12_custom-specs", "spec/2026-06-12_custom-specs"),
    ]
    for directory, slug, _ in packages:
        package = tmp_path / directory / "specs" / slug
        package.mkdir(parents=True)
        (package / "spec.md").write_text(
            "# Fixture\n- Git integration branch：`spec/YYYY-MM-DD_<slug>` 或适用外理由\n", encoding="utf-8"
        )
    setup_git_repo(tmp_path)
    branches = []
    for directory, slug, expected in packages:
        content = (tmp_path / directory / "specs" / slug / "spec.md").read_text(encoding="utf-8")
        branch = extract_integration_branch(content)
        assert branch == expected
        branches.append(branch)
        valid, detail = validate_branch_bound_package(tmp_path, slug, content)
        assert valid == (expected == "spec/test"), detail
    assert len(set(branches)) == len(packages)
    for ref in ("main", "spec/test"):
        subprocess.run(["git", "rev-parse", "--verify", ref], cwd=tmp_path, check=True, capture_output=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=tmp_path, check=True, capture_output=True, text=True)
    assert status.stdout == ""


ORCHESTRATION_RUNTIME_FILES = (
    "scripts/route_decision.py",
    "references/orchestration.md",
    "agents/orchestrator.md",
    "agents/planner.md",
)


def _minimal_runtime_tree(root: Path) -> None:
    required = (
        "SKILL.md",
        "install.sh",
        "agents/openai.yaml",
        "agents/orchestrator.md",
        "agents/planner.md",
        "hooks/pre-commit",
        "hooks/pre-push",
        "hooks/spec_disk_truth_gate.py",
        "scripts/safe_open_support.py",
        "scripts/issue_closure_support.py",
        "scripts/init_spec_package.py",
        "scripts/read_version.py",
        "scripts/route_spec_package.py",
        "scripts/route_decision.py",
        "scripts/report_spec_package.py",
        "scripts/check_spec_package.py",
        "scripts/check_all_spec_packages.py",
        "scripts/complete_spec_package.py",
        "scripts/doctor_spec_environment.py",
        "scripts/push_spec_package.py",
        "scripts/smoke_test_spec_skill.py",
        "scripts/spec_package_support.py",
        "scripts/slot_registry.py",
        "references/commands.md",
        "references/output-contracts.md",
        "references/orchestration.md",
    )
    for relative in required:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == "SKILL.md":
            path.write_text("# spec\n", encoding="utf-8")
        elif relative == "scripts/slot_registry.py":
            path.write_text((ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
        else:
            path.write_text("fixture\n", encoding="utf-8")
    for slot_name in ("team-loop", "workflow-runner"):
        slot = root / "slots" / slot_name
        for relative in ("scripts/runner.py", "hooks/guard.py", "README.md", "tests/test_runner.py"):
            path = slot / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture\n", encoding="utf-8")
        (slot / "manifest.json").write_text(
            json.dumps(
                {
                    "name": slot_name,
                    "version": "1.0.0",
                    "summary": "fixture",
                    "scripts": ["scripts/runner.py"],
                    "hooks": {"Stop": ["hooks/guard.py"]},
                    "docs": ["README.md"],
                    "tests": "tests",
                }
            ),
            encoding="utf-8",
        )


@pytest.mark.parametrize("missing", ORCHESTRATION_RUNTIME_FILES)
def test_smoke_runtime_layout_requires_orchestration_assets(tmp_path, missing, monkeypatch):
    _minimal_runtime_tree(tmp_path)
    (tmp_path / missing).unlink()
    # Keep doctor from running against this fixture: layout must fail first.
    monkeypatch.setattr("smoke_test_spec_skill.REPO_ROOT", tmp_path)
    with pytest.raises(AssertionError, match=missing.replace("/", r"[/\\\\]")):
        assert_runtime_layout(tmp_path)


def test_smoke_runtime_layout_requires_builtin_slots(tmp_path, monkeypatch):
    _minimal_runtime_tree(tmp_path)
    (tmp_path / "slots" / "workflow-runner" / "manifest.json").unlink()
    monkeypatch.setattr("smoke_test_spec_skill.REPO_ROOT", tmp_path)
    with pytest.raises(AssertionError, match="missing runtime slots"):
        assert_runtime_layout(tmp_path)

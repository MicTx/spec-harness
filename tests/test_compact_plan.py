"""Minimal plans inherit repository policy without rewriting it."""

import json
from pathlib import Path

import package

from tests.v3_support import SLUG, cli, git, make_package, repo


def _minimal(root, *, override="", verify="true"):
    directory = make_package(root)
    (directory / "spec.md").write_text(
        "# Compact fixture\n<!-- spec-format: 3 -->\n\n"
        "## Goal\n- goal: Keep the fixture working\n- done means: The declared test passes\n"
        "## Scope\n- in: app.py\n## Criteria\n- C1 | Fixture works | task-value\n" + override
    )
    (directory / "tasks.md").write_text(
        f"- [ ] Verify fixture\n  - id: task-value\n  - boundary: app.py\n  - verify: `{verify}`\n"
    )
    return directory


def test_minimal_plan_executes_no_implicit_baseline_or_charter_write(tmp_path):
    repo(tmp_path)
    directory = _minimal(tmp_path, verify="printf x >> .spec/verify-count")
    marker = tmp_path / ".spec" / "verify-count"
    assert cli(tmp_path, "--slug", SLUG, "plan-lint").returncode == 0
    assert cli(tmp_path, "--slug", SLUG, "plan-go").returncode == 0
    assert not marker.exists()
    assert not (tmp_path / ".spec" / "charter.md").exists()
    assert package.package_baseline(directory)["charter"]["V4"] == "commit"
    assert cli(tmp_path, "verify", "--all").returncode == 0
    assert cli(tmp_path, "check").returncode == 0
    assert marker.read_bytes() == b"xx"


def test_protected_go_inherits_primary_charter_and_seals_overrides(tmp_path):
    repo(tmp_path)
    _minimal(tmp_path, override="## Charter\n- V3 verification: project | package override\n")
    policy = tmp_path / ".spec" / "charter.md"
    policy.write_bytes(b"# Keep this text verbatim\r\n- V2 scope: strict\r\n- V3 verification: integration\r\n")
    original = policy.read_bytes()
    git(tmp_path, "checkout", "main")
    result = cli(tmp_path, "--slug", SLUG, "plan-go")
    assert result.returncode == 0, result.stdout + result.stderr
    linked = Path(json.loads(result.stdout)["worktree"])
    directory = package.package_dir(linked, SLUG)
    baseline = package.package_baseline(directory)
    assert baseline["charter"]["V2"] == "strict" and baseline["charter"]["V3"] == "project"
    assert baseline["charter_overrides"] == {"V3": "project"}
    assert policy.read_bytes() == original
    policy.write_text("# Changed repository policy\n- V2 scope: adjacent-fixes\n")
    spec = (directory / "spec.md").read_text()
    assert package.frozen_charter(directory, spec)["V2"] == "strict"
    assert cli(linked, "verify", "--all").returncode == 0
    assert cli(linked, "check").returncode == 0


def test_malformed_inherited_policy_fails_before_commands(tmp_path):
    repo(tmp_path)
    _minimal(tmp_path, verify="touch .spec/not-run")
    policy = tmp_path / ".spec" / "charter.md"
    policy.write_text("- V4 finish: unknown\n")
    result = cli(tmp_path, "--slug", SLUG, "plan-go")
    assert result.returncode == 1
    assert any(gap["rule"] == "PLAN-003" and "V4" in gap["detail"] for gap in json.loads(result.stdout)["gaps"])
    assert not (tmp_path / ".spec" / "not-run").exists()

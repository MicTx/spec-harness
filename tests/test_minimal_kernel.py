"""The shipped kernel keeps proof and rejects retired runtime surfaces."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.v3_support import SLUG, cli, git, make_package, repo

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_removals_and_line_budget():
    from payload_contract import SCRIPT_BLACKLIST

    scripts = ROOT / "scripts"
    lines = sum(
        len(path.read_text(encoding="utf-8").splitlines())
        for path in scripts.glob("*.py")
        if path.name not in SCRIPT_BLACKLIST
    )
    assert lines <= 4500
    assert not (scripts / "ui_review.py").exists()
    assert not (scripts / "check_all.py").exists()
    assert not (ROOT / "hooks" / "pre-commit").exists()
    assert not (ROOT / "hooks" / "pre-push").exists()


def test_removed_cli_features_are_not_routed(tmp_path):
    for command in ("learn", "ui-review", "task-card", "repair"):
        result = cli(tmp_path, command, "--help")
        assert result.returncode == 2 and "invalid choice" in result.stderr
    result = cli(tmp_path, "plan-lint", "--suggest")
    assert result.returncode == 2 and "unrecognized arguments" in result.stderr


def test_frontend_package_still_runs_real_verify_without_ui_artifact(tmp_path):
    repo(tmp_path)
    source = tmp_path / "web" / "app.tsx"
    source.parent.mkdir()
    source.write_text("export const value = 0;\n")
    git(tmp_path, "add", "web/app.tsx")
    git(tmp_path, "commit", "-qm", "frontend fixture")
    directory = make_package(tmp_path, verify="grep -qx 'export const value = 1;' web/app.tsx", expect="red")
    spec, tasks = directory / "spec.md", directory / "tasks.md"
    spec.write_text(spec.read_text().replace("app.py", "web/app.tsx"))
    tasks.write_text(tasks.read_text().replace("boundary: app.py", "boundary: web/app.tsx"))
    assert cli(tmp_path, "plan-go").returncode == 0
    source.write_text("export const value = 1;\n")
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    result = cli(tmp_path, "check")
    assert result.returncode == 0, result.stdout + result.stderr
    archive = directory.parent / "archive" / SLUG
    assert not (archive / "ui-review.json").exists()
    rows = [json.loads(line) for line in (archive / "runs.jsonl").read_text().splitlines()]
    final = next(row for row in reversed(rows) if row.get("kind") == "gate" and row.get("final"))
    assert final["passed"] and final["checks"][0]["exit"] == 0


def test_repair_of_metadata_uses_the_standard_plan(tmp_path):
    repo(tmp_path)
    origin = "2026-01-01_old-fix"
    archive = tmp_path / ".spec" / "specs" / "archive" / origin
    archive.mkdir(parents=True)
    directory = make_package(tmp_path)
    spec = directory / "spec.md"
    spec.write_text(spec.read_text().replace("## Goal\n", f"## Goal\n- kind: repair\n- repair-of: {origin}\n"))
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "check").returncode == 0
    delivered = directory.parent / "archive" / SLUG
    assert f"- repair-of: {origin}" in (delivered / "spec.md").read_text()
    assert archive.is_dir()


@pytest.mark.parametrize("flag", ("--suggest", "--append", "--tdd"))
def test_retired_flags_are_absent_from_help(flag):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "spec.py"), "--help"], capture_output=True, text=True, check=True
    )
    assert flag not in result.stdout

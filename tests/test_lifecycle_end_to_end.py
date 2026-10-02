from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_complete_spec_package import _COMPLETE_CHECKLIST, _COMPLETE_SPEC_EN

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

SLUG = "2026-09-05_add-end-to-end"


def command(root, *args):
    result = subprocess.run(list(args), cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


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
    import json

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
    code, output = run_hook(stop_payload(tmp_path))
    assert code == 0 and "cycle" in json.loads(output)["reason"]

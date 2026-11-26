"""Built-in repair: Spec Git hooks left by versions up to 2.2.0 are retired instead of blocking Git."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import doctor
import gitx
import pytest

from tests.v3_support import ROOT, cli, git, repo, started_package

SESSION_HOOK = ROOT / "hooks" / "spec_hook.py"
FOREIGN = "#!/bin/sh\nexec make lint\n"
# The opening of every rendered 2.2.0 pre-commit/pre-push hook, verbatim apart from the checker path.
HOOK_2_2_0 = """#!/bin/sh
set -eu

repo=$(git rev-parse --show-toplevel)
checker="CHECKER"
if [ -n "${SPEC_CHECK_ALL_SCRIPT:-}" ]; then
  checker="$SPEC_CHECK_ALL_SCRIPT"
  printf '%s\\n' "spec gate: using SPEC_CHECK_ALL_SCRIPT override: $checker" >&2
fi
if [ ! -f "$checker" ]; then
  printf '%s\\n' "error: Spec gate checker not found at: $checker" >&2
  exit 1
fi
exec python3 "$checker" --root "$repo"
"""


def _legacy(hooks: Path, name: str = "pre-commit", checker: str = "/gone/spec/scripts/check_all.py") -> Path:
    """Write a hook as the 2.2.0 installer rendered it; the checker it names no longer ships."""
    hooks.mkdir(parents=True, exist_ok=True)
    target = hooks / name
    target.write_text(HOOK_2_2_0.replace("CHECKER", checker), encoding="utf-8")
    target.chmod(0o755)
    return target


def _resolved(paths: list[str]) -> list[Path]:
    return [Path(path).resolve() for path in paths]


def test_only_spec_owned_hooks_are_found_and_retired(tmp_path):
    repo(tmp_path)
    hooks = tmp_path / ".git" / "hooks"
    legacy = _legacy(hooks)
    original = legacy.read_bytes()
    foreign = hooks / "pre-push"
    foreign.write_text(FOREIGN, encoding="utf-8")

    assert _resolved(gitx.legacy_git_hooks(tmp_path)) == [legacy.resolve()]
    assert legacy.read_bytes() == original

    retired = gitx.legacy_git_hooks(tmp_path, retire=True)

    assert [Path(path).name.split(".spec-retired-")[0] for path in retired] == ["pre-commit"]
    assert Path(retired[0]).read_bytes() == original and not legacy.exists()
    assert foreign.read_text(encoding="utf-8") == FOREIGN
    assert gitx.legacy_git_hooks(tmp_path, retire=True) == []


@pytest.mark.parametrize("relative", [False, True])
def test_retirement_follows_core_hooks_path_from_a_linked_worktree(tmp_path, relative):
    main = tmp_path / "main"
    main.mkdir()
    repo(main)
    shared = main / ".githooks" if relative else tmp_path / "shared-hooks"
    git(main, "config", "core.hooksPath", ".githooks" if relative else str(shared))
    legacy = _legacy(shared, "pre-push")
    root = main
    if not relative:
        root = tmp_path / "linked"
        git(main, "worktree", "add", "-q", "-b", "side", str(root))

    retired = gitx.legacy_git_hooks(root, retire=True)

    assert [path.parent for path in _resolved(retired)] == [shared.resolve()]
    assert not legacy.exists()


def test_committing_lifecycle_retires_a_hook_whose_checker_is_gone(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    hook = _legacy(tmp_path / ".git" / "hooks")
    probe = ["git", "commit", "-q", "--allow-empty", "-m", "probe"]
    blocked = subprocess.run(probe, cwd=tmp_path, capture_output=True, text=True)
    assert blocked.returncode != 0 and "checker not found" in blocked.stderr
    for read_only in (("next",), ("check", "--check-only")):
        cli(tmp_path, *read_only)
    assert hook.exists()

    verified = cli(tmp_path, "verify", "--all")
    delivered = cli(tmp_path, "check")

    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "retired a legacy Spec Git hook" in verified.stderr
    assert delivered.returncode == 0, delivered.stdout + delivered.stderr
    assert not hook.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unreadable_legacy_hook_is_left_alone_and_the_lifecycle_continues(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    hook = _legacy(tmp_path / ".git" / "hooks", "pre-push")
    hook.chmod(0o000)
    try:
        verified = cli(tmp_path, "verify", "--all")
        report = doctor.check(tmp_path, [], fix=True)
    finally:
        hook.chmod(0o755)

    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert hook.exists() and report["retired_git_hooks"] == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_execute_only_hook_that_blocks_git_is_reported_but_never_moved(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    hook = _legacy(tmp_path / ".git" / "hooks")
    hook.chmod(0o111)
    try:
        blocked = subprocess.run(["git", "commit", "--allow-empty", "-m", "x"], cwd=tmp_path, capture_output=True)
        report = doctor.check(tmp_path, [], fix=True)
        verified = cli(tmp_path, "verify", "--all")
    finally:
        hook.chmod(0o755)

    assert blocked.returncode != 0
    assert report["passed"] is False and report["retired_git_hooks"] == []
    assert _resolved(report["legacy_git_hooks"]) == [hook.resolve()] and hook.exists()
    assert "could not be retired; run spec.py doctor" in verified.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_legacy_hook_that_cannot_be_moved_is_reported_not_raised(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    hooks = tmp_path / ".git" / "hooks"
    hook = _legacy(hooks)
    hooks.chmod(0o555)
    body = {"hook_event_name": "SessionStart", "source": "startup", "cwd": str(tmp_path)}
    try:
        report = doctor.check(tmp_path, [], fix=True)
        verified = cli(tmp_path, "verify", "--all")
        start = subprocess.run(
            [sys.executable, str(SESSION_HOOK), "--host", "claude", "start"],
            input=json.dumps(body),
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
    finally:
        hooks.chmod(0o755)

    assert report["passed"] is False and report["retired_git_hooks"] == []
    assert _resolved(report["legacy_git_hooks"]) == [hook.resolve()]
    assert "Errno 13" not in verified.stdout + verified.stderr
    assert verified.returncode != 0 and "could not be retired; run spec.py doctor" in verified.stderr
    assert start.returncode == 0 and "could not retire Git hooks (pre-commit)" in start.stdout
    assert hook.exists()


def test_doctor_reports_then_repairs_legacy_hooks(tmp_path):
    repo(tmp_path)
    hook = _legacy(tmp_path / ".git" / "hooks")

    report = doctor.check(tmp_path, [], fix=False)

    assert report["passed"] is False and _resolved(report["legacy_git_hooks"]) == [hook.resolve()]
    assert hook.exists()
    fixed = doctor.check(tmp_path, [], fix=True)
    assert fixed["passed"] is True and fixed["legacy_git_hooks"] == [] and not hook.exists()
    assert Path(fixed["retired_git_hooks"][0]).name.startswith("pre-commit.spec-retired-")


@pytest.mark.parametrize("state", ["plain", "spec", "active"])
def test_session_start_retires_legacy_hooks_only_in_spec_repositories(tmp_path, state):
    repo(tmp_path)
    if state == "active":
        started_package(tmp_path)
    elif state == "spec":
        (tmp_path / ".spec" / "specs").mkdir(parents=True)
    hook = _legacy(tmp_path / ".git" / "hooks", "pre-push")
    body = {"hook_event_name": "SessionStart", "source": "startup", "cwd": str(tmp_path)}

    result = subprocess.run(
        [sys.executable, str(SESSION_HOOK), "--host", "claude", "start"],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    if state == "plain":
        assert hook.exists() and "retired" not in result.stdout
    else:
        assert not hook.exists()
        assert "retired legacy Git hooks (pre-push" in result.stdout

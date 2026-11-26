"""Blast-radius guards: the session hook must be inert outside Spec repositories
and must not destroy checkpointed work inside them."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.v3_support import cli, make_package, repo, started_package

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "spec_hook.py"

sys.path.insert(0, str(ROOT / "hooks"))

from spec_hook import handle  # noqa: E402

EVENTS = ("start", "prompt", "pre_tool", "stop")

PAYLOADS = {
    "start": {"hook_event_name": "SessionStart", "source": "startup"},
    "prompt": {"hook_event_name": "UserPromptSubmit", "prompt": "hello"},
    "pre_tool": {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}},
    "post_edit": {"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": "a.py"}},
    "pre_compact": {"hook_event_name": "PreCompact", "trigger": "manual"},
    "subagent": {"hook_event_name": "SubagentStart", "agent_type": "task"},
    "stop": {"hook_event_name": "Stop"},
    "end": {"hook_event_name": "SessionEnd", "reason": "clear"},
}

FORBIDDEN_RUNTIME_MODULES = (
    "package",
    "route",
    "memory",
    "charter",
    "gitx",
    "plan",
    "local_evidence",
)


def run_hook(event: str, cwd: Path, payload: dict | None = None, extra_argv: list[str] | None = None):
    body = dict(payload or PAYLOADS[event])
    body["cwd"] = str(cwd)
    argv = [sys.executable, *(extra_argv or []), str(HOOK), "--host", "claude", event]
    return subprocess.run(argv, input=json.dumps(body), capture_output=True, text=True, cwd=cwd)


def plain_repo(root: Path) -> None:
    """A git repository with no Spec footprint at all."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
    (root / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True, capture_output=True)


# --- outside a Spec repository every event is a silent no-op -----------------


def test_every_event_is_silent_in_a_plain_git_repository(tmp_path):
    plain_repo(tmp_path)
    for event in EVENTS:
        result = run_hook(event, tmp_path)
        assert result.returncode == 0, (event, result.stderr)
        assert result.stdout == "", (event, result.stdout)


def test_every_event_is_silent_outside_any_repository(tmp_path):
    for event in EVENTS:
        result = run_hook(event, tmp_path)
        assert result.returncode == 0, (event, result.stderr)
        assert result.stdout == "", (event, result.stdout)


def test_noop_path_never_imports_the_runtime(tmp_path):
    plain_repo(tmp_path)
    result = run_hook("pre_tool", tmp_path, extra_argv=["-X", "importtime"])
    assert result.returncode == 0
    for name in FORBIDDEN_RUNTIME_MODULES:
        assert not any(
            field.strip() == name for field in (line.split("|")[-1] for line in result.stderr.splitlines())
        ), name


def test_submodule_inside_a_spec_repository_is_not_a_spec_repository(tmp_path):
    plain_repo(tmp_path)
    nested = tmp_path / "vendor" / "lib"
    nested.mkdir(parents=True)
    plain_repo(nested)
    for event in ("pre_tool", "stop", "start"):
        result = run_hook(event, nested)
        assert result.returncode == 0
        assert result.stdout == "", (event, result.stdout)


# --- unparsable commands: allow unless raw text names a risky git action -----


def test_unparsable_commands_are_allowed_in_a_plain_repository(tmp_path):
    plain_repo(tmp_path)
    for command in ("echo don't", 'printf "%s" "unclosed', "cat <<'EOF'\nit's fine\nEOF"):
        result = run_hook("pre_tool", tmp_path, {"tool_input": {"command": command}})
        assert result.returncode == 0
        assert result.stdout == "", command


def test_unparsable_commands_are_allowed_in_an_active_spec_repository(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    for command in ("echo don't", 'printf "%s" "unclosed', "cat <<'EOF'\nit's fine\nEOF"):
        result = run_hook("pre_tool", tmp_path, {"tool_input": {"command": command}})
        assert result.returncode == 0
        assert result.stdout == "", command


def test_unparsable_command_running_git_commit_is_still_denied(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    command = "echo don't && git commit -m wip"
    result = run_hook("pre_tool", tmp_path, {"tool_input": {"command": command}})
    assert result.returncode == 0
    assert '"decision": "block"' in result.stdout


# --- bypass flags: denied only inside Spec repositories (F2) -----------------


def test_bypass_flags_are_denied_inside_a_spec_repository(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    for command in ("git commit --no-verify", "git -c core.hooksPath=/dev/null commit -m x"):
        result = handle("pre_tool", {"cwd": str(tmp_path), "command": command})
        assert result.deny, command


def test_bypass_flags_are_denied_without_a_bound_package(tmp_path):
    """F2: any .spec repository denies hook bypass, bound branch or not."""
    repo(tmp_path)
    (tmp_path / ".spec" / "specs").mkdir(parents=True, exist_ok=True)
    for command in ("git commit --no-verify", "git commit -n", "git -c core.hooksPath=/dev/null commit -m x"):
        result = handle("pre_tool", {"cwd": str(tmp_path), "command": command})
        assert result.deny, command


def test_bypass_flags_are_allowed_in_a_plain_repository(tmp_path):
    plain_repo(tmp_path)
    for command in ("git commit --no-verify", "git -c core.hooksPath=/dev/null commit -m x", "git commit -n"):
        result = handle("pre_tool", {"cwd": str(tmp_path), "command": command})
        assert not result.deny, command
        proc = run_hook("pre_tool", tmp_path, {"tool_input": {"command": command}})
        assert proc.stdout == "", command


# --- destructive git forms: denied only while a package binds the branch ------


DESTRUCTIVE = (
    "git reset --hard HEAD~1",
    "git clean -f",
    "git clean -fd",
    "git clean -xdf",
    "git stash",
    "git stash push -m wip",
    "git stash pop",
    "git stash drop",
    "git branch -D feature",
    "git branch -d -f feature",
    "git branch -Df feature",
    "git branch -dF feature",
    "git branch -fD feature",
    "git worktree remove ../other",
    "git -C . worktree remove ../other",
    "git --no-pager worktree remove ../other",
)

SAFE = (
    "git stash list",
    "git stash show",
    "git -C . stash list",
    "git --no-pager stash list",
    "git clean -n",
    "git branch -d feature",
    "git worktree list",
    "git status",
)


def test_destructive_forms_are_denied_while_a_package_is_active(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    for command in DESTRUCTIVE:
        result = handle("pre_tool", {"cwd": str(tmp_path), "command": command})
        assert result.deny, command
        assert "checkpoint" in result.reason or "Spec" in result.reason


def test_safe_forms_stay_allowed_while_a_package_is_active(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    for command in SAFE:
        result = handle("pre_tool", {"cwd": str(tmp_path), "command": command})
        assert not result.deny, command


def test_destructive_forms_are_allowed_without_an_active_package(tmp_path):
    repo(tmp_path)
    (tmp_path / ".spec" / "specs").mkdir(parents=True, exist_ok=True)
    for command in DESTRUCTIVE:
        result = handle("pre_tool", {"cwd": str(tmp_path), "command": command})
        assert not result.deny, command


def test_destructive_forms_are_allowed_in_a_plain_repository(tmp_path):
    plain_repo(tmp_path)
    for command in DESTRUCTIVE:
        result = handle("pre_tool", {"cwd": str(tmp_path), "command": command})
        assert not result.deny, command
        proc = run_hook("pre_tool", tmp_path, {"tool_input": {"command": command}})
        assert proc.stdout == "", command


def test_commit_and_push_stay_denied_while_a_package_is_active(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    for command in ("git commit -m wip", "git push origin main"):
        result = handle("pre_tool", {"cwd": str(tmp_path), "command": command})
        assert result.deny and "Spec owns Git" in result.reason


def test_commit_is_denied_after_the_package_is_archived(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "done").returncode == 0
    result = handle("pre_tool", {"cwd": str(tmp_path), "command": "git push origin main"})
    assert result.deny

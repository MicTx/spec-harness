import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

from spec_disk_truth_gate import commit_changes_index, parse_direct_git

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "spec_disk_truth_gate.py"
CHECK_ALL = ROOT / "scripts" / "check_all_spec_packages.py"


def run_hook(mode: str, cwd: Path, *, env_overrides=None, **payload):
    env = {
        **os.environ,
        "SPEC_CHECK_ALL_SCRIPT": str(CHECK_ALL),
        **(env_overrides or {}),
    }
    completed = subprocess.run(
        [sys.executable, str(HOOK), mode],
        input=json.dumps({"cwd": str(cwd), **payload}),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return json.loads(completed.stdout) if completed.stdout.strip() else None


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Spec Test"], cwd=root, check=True)


def make_incomplete_package(root: Path) -> Path:
    package = root / ".spec" / "specs" / "2026-07-13_fix-hook"
    package.mkdir(parents=True)
    (package / "spec.md").write_text("# Incomplete\n", encoding="utf-8")
    (package / "tasks.md").write_text("- [ ] Task\n", encoding="utf-8")
    (package / "checklist.md").write_text("**验收结果**：待修复\n", encoding="utf-8")
    return package


def test_parse_direct_git_rejects_shell_composition_and_accepts_quoted_semicolon(tmp_path):
    assert parse_direct_git("true && git commit -m test", tmp_path) is None
    assert parse_direct_git("git push origin main > out", tmp_path) is None
    assert parse_direct_git('git commit -m "$(touch out)"', tmp_path) is None
    invocation = parse_direct_git("git commit -m 'a; b'", tmp_path)
    assert invocation is not None
    assert invocation.subcommand == "commit"


def test_parse_direct_git_rejects_config_env_redirects(tmp_path):
    for command in (
        "git --config-env=core.hooksPath=HOOKS commit -m bypass",
        "git --config-env=remote.origin.url=REMOTE push origin main",
    ):
        invocation = parse_direct_git(command, tmp_path)
        assert invocation is not None
        assert invocation.unsafe_global_options is True

    for args in (
        ("-am", "test"),
        ("--all", "-m", "test"),
        ("--only", "file", "-m", "test"),
        ("-m", "test", "--", "file"),
        ("--pathspec-from-file=list",),
    ):
        assert commit_changes_index(args) is True
    assert commit_changes_index(("-m", "a; b")) is False


def test_hook_ignores_git_directory_paths(tmp_path):
    payload = {"tool_input": {"command": "cp hooks/pre-commit /repo/.git/hooks/pre-commit"}}
    assert run_hook("pretool", tmp_path, **payload) is None


def test_hook_blocks_alias_and_repository_redirection(tmp_path):
    init_repo(tmp_path)
    make_incomplete_package(tmp_path)
    subprocess.run(["git", "config", "alias.x", "commit"], cwd=tmp_path, check=True)

    alias = run_hook("pretool", tmp_path, tool_input={"command": "git x -m bypass"})
    redirected = run_hook(
        "pretool",
        tmp_path,
        tool_input={"command": f"git --git-dir={tmp_path / '.git'} -c core.worktree={tmp_path} commit -m bypass"},
    )
    no_verify = run_hook(
        "pretool",
        tmp_path,
        tool_input={"command": "git commit --no-verify -m bypass"},
    )
    escaped = run_hook("pretool", tmp_path, tool_input={"command": "g\\it commit -m bypass"})
    line_continuation = run_hook(
        "pretool",
        tmp_path,
        tool_input={"command": "g\\\nit commit -m bypass"},
    )

    assert "禁止 Git alias" in alias["hookSpecificOutput"]["permissionDecisionReason"]
    assert "禁止 commit/push 使用" in redirected["hookSpecificOutput"]["permissionDecisionReason"]
    assert "禁止 commit/push 使用 `--no-verify`" in no_verify["hookSpecificOutput"]["permissionDecisionReason"]
    assert escaped["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert line_continuation["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_blocks_stale_index_and_implicit_stage(tmp_path):
    init_repo(tmp_path)
    package = make_incomplete_package(tmp_path)
    subprocess.run(["git", "add", ".spec"], cwd=tmp_path, check=True)
    (package / "spec.md").write_text(
        """# Gate - 项目范围
## 1. 问题定义
- **项目目标**：目标
- **目标用户**：用户
- **核心价值**：价值
## 2. 假设与待确认
### 2.1 已确认事实
- 事实
### 2.2 关键假设
- 假设
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不做
## 4. 最小实现路径
- a
- b
- c
""",
        encoding="utf-8",
    )
    (package / "tasks.md").write_text("- [x] Task\n  - boundary: x\n  - verify: x\n", encoding="utf-8")
    (package / "checklist.md").write_text(
        "- [x] Item\n## 验收证据\n- 脚本验证：pytest\n**验收结果**：通过\n", encoding="utf-8"
    )

    stale = run_hook("pretool", tmp_path, tool_input={"command": "git commit -m test"})
    implicit = run_hook("pretool", tmp_path, tool_input={"command": "git commit -am test"})

    assert "Git index snapshot failed" in stale["hookSpecificOutput"]["permissionDecisionReason"]
    assert "请先单独执行 `git add`" in implicit["hookSpecificOutput"]["permissionDecisionReason"]


def test_stop_anchor_supports_custom_root_and_rejects_ambiguity(tmp_path):
    slug = "2026-07-13_fix-custom-stop"
    custom = tmp_path / "custom" / "spec-state" / "specs" / slug
    custom.mkdir(parents=True)
    (custom / "tasks.md").write_text("- [ ] Task\n", encoding="utf-8")
    env = {"SPEC_SPECS_DIRS": "custom/spec-state"}
    nested = tmp_path / "work" / "nested"
    nested.mkdir(parents=True)

    allowed = run_hook(
        "stop",
        nested,
        env_overrides=env,
        last_assistant_message=f"仍有待修复项：{slug}#task_0001。",
    )
    assert allowed is None

    duplicate = tmp_path / ".spec" / "specs" / slug
    duplicate.mkdir(parents=True)
    (duplicate / "tasks.md").write_text("- [ ] Task\n", encoding="utf-8")
    blocked = run_hook(
        "stop",
        nested,
        env_overrides=env,
        last_assistant_message=f"仍有待修复项：{slug}#task_0001。",
    )
    assert blocked["decision"] == "block"
    assert "ambiguous across Spec roots" in blocked["reason"]


def test_stop_requires_non_terminal_issue_to_be_recorded(tmp_path):
    package = make_incomplete_package(tmp_path)
    slug = package.name

    terminal = run_hook("stop", tmp_path, last_assistant_message="No remaining work; release-ready.")
    unrecorded = run_hook("stop", tmp_path, last_assistant_message="仍有待确认项，需要用户输入。")
    fake = run_hook(
        "stop",
        tmp_path,
        last_assistant_message="仍有待修复问题，记录在 2026-07-13_fix-missing#task_0001。",
    )
    recorded = run_hook(
        "stop",
        tmp_path,
        last_assistant_message=f"仍有待确认项，已回写 {slug}#task_0001。",
    )
    mixed = run_hook(
        "stop",
        tmp_path,
        last_assistant_message=(f"仍有待修复项：{slug}#task_0001 和 2026-07-13_fix-missing#task_0001。"),
    )

    assert terminal["decision"] == "block"
    assert unrecorded["decision"] == "block"
    assert "#<task-or-issue-id>" in unrecorded["reason"]
    assert fake["decision"] == "block"
    assert "package not found" in fake["reason"]
    assert mixed["decision"] == "block"
    assert "package not found" in mixed["reason"]
    assert recorded is None

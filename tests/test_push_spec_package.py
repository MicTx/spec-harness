from __future__ import annotations

import http.server
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "push_spec_package.py"
TEST_SPEC_SLUG = "2026-07-13_fix-push-fixture"


def write_archived_spec(repo: Path, slug: str = TEST_SPEC_SLUG) -> Path:
    package = repo / ".spec" / "specs" / "archive" / slug
    package.mkdir(parents=True, exist_ok=True)
    (package / "spec.md").write_text(
        """# Push Fixture - 项目范围
## 1. 问题定义
- **项目目标**：验证 push fixture
- **目标用户**：维护者
- **核心价值**：归属可信
## 2. 假设与待确认
### 2.1 已确认事实
- fixture
### 2.2 关键假设
- standard git
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- production
## 4. 最小实现路径
- archive
- attribute
- verify
""",
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [x] Push fixture\n  - boundary: test repository\n  - verify: push gate\n",
        encoding="utf-8",
    )
    (package / "checklist.md").write_text(
        "- [x] Verified\n## 验收证据\n- 脚本验证：pytest\n**验收结果**：通过\n",
        encoding="utf-8",
    )
    summary = """# Push Fixture - 完成总结
## 交付结论
- 完成
## 假设回顾
- verified
## 交付范围
- delivered
## 简化决策
- simple
## 变更边界
- fixture
## 验证证据
- pytest
## 门禁证据
- passed
## 问题处置
```json
{"version":1,"issues":[]}
```
"""
    (package / "completion-summary.md").write_text(summary, encoding="utf-8")
    return package


def run_push(cwd: Path, *args: str, env: Optional[dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def init_repo(tmp_path: Path, *, active_baseline: str | None = None) -> tuple[Path, str]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Spec Test")
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    if active_baseline:
        package = write_archived_spec(repo, active_baseline)
        active = package.parent.parent / active_baseline
        package.rename(active)
        (active / "completion-summary.md").unlink()
        spec = active / "spec.md"
        spec.write_text(spec.read_text() + "\n- Git integration branch：`spec/test`\n")
        (active / "tasks.md").write_text("- [ ] Independent work\n  - boundary: README.md\n  - verify: test\n")
        (active / "checklist.md").write_text("- [ ] Pending\n")
        git(repo, "add", ".spec")
    git(repo, "commit", "-m", "initial")
    git(repo, "branch", "-M", "main")
    git(repo, "push", "-u", "origin", "main")
    git(repo, "checkout", "-b", "spec/test")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    write_archived_spec(repo)
    git(repo, "add", "feature.txt", ".spec")
    git(repo, "commit", "-m", "feature", "-m", f"Spec: {TEST_SPEC_SLUG}")
    git(repo, "push", "-u", "origin", "spec/test")
    return repo, "spec/test"


def test_push_default_merges_and_deletes_branch(tmp_path):
    repo, branch = init_repo(tmp_path)

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode == 0
    assert "mode: execute" in result.stdout
    assert "Core prechecks" in result.stdout or "Safety" in result.stdout
    assert "hard-block by default" in result.stdout
    assert "--allow-unarchived" in result.stdout
    assert "warn but no longer hard-block" not in result.stdout
    assert git(repo, "branch", "--show-current") == "main"
    assert "spec/test" not in git(repo, "branch", "--list", "spec/test")
    assert "origin/spec/test" not in git(repo, "branch", "-r", "--list", "origin/spec/test")
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "feature\n"


def test_push_accepts_root_argument_from_other_cwd(tmp_path):
    repo, branch = init_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()

    result = run_push(outside, "--root", str(repo), "--branch", branch, "--main-branch", "main")

    assert result.returncode == 0
    assert "mode: execute" in result.stdout
    assert git(repo, "branch", "--show-current") == "main"


def test_push_ignores_external_git_environment_for_root(tmp_path):
    repo, branch = init_repo(tmp_path)
    (tmp_path / "other").mkdir()
    other_repo, _ = init_repo(tmp_path / "other")
    env = os.environ.copy()
    env["GIT_DIR"] = str(other_repo / ".git")
    env["GIT_WORK_TREE"] = str(other_repo)
    env["GIT_INDEX_FILE"] = str(other_repo / ".git" / "index")

    result = run_push(
        tmp_path,
        "--root",
        str(repo),
        "--branch",
        branch,
        "--main-branch",
        "main",
        env=env,
    )

    assert result.returncode == 0
    assert "mode: execute" in result.stdout
    assert f"- branch: {branch}" in result.stdout
    assert git(repo, "branch", "--show-current") == "main"


def test_push_ignores_git_config_remote_override(tmp_path):
    repo, branch = init_repo(tmp_path)
    attacker = tmp_path / "attacker.git"
    subprocess.run(["git", "init", "--bare", str(attacker)], check=True, capture_output=True)
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "remote.origin.url",
            "GIT_CONFIG_VALUE_0": str(attacker),
        }
    )

    result = run_push(repo, "--branch", branch, "--main-branch", "main", env=env)

    assert result.returncode == 0, result.stderr
    assert (
        subprocess.run(
            ["git", "show-ref", "--verify", "refs/heads/main"],
            cwd=attacker,
            capture_output=True,
            text=True,
        ).returncode
        != 0
    )


def test_push_strips_git_dir_and_work_tree_only(tmp_path):
    """GIT_SSH_COMMAND survives while repository/config redirection is stripped.

    Directory/index redirection and GIT_CONFIG_* overrides must not redirect
    security-sensitive push operations; GIT_SSH_COMMAND remains supported.
    """
    repo, branch = init_repo(tmp_path)
    env = os.environ.copy()
    env["GIT_DIR"] = str(repo / ".git")
    env["GIT_WORK_TREE"] = str(repo)
    env["GIT_SSH_COMMAND"] = "echo ssh"
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "core.sshCommand"
    env["GIT_CONFIG_VALUE_0"] = "echo ssh"

    result = run_push(
        tmp_path,
        "--root",
        str(repo),
        "--branch",
        branch,
        "--main-branch",
        "main",
        env=env,
    )

    assert result.returncode == 0
    assert "mode: execute" in result.stdout
    assert f"- branch: {branch}" in result.stdout


def test_push_blocks_unarchived_spec_package_by_default(tmp_path):
    """Unarchived active packages hard-block push unless --allow-unarchived is set."""
    repo, branch = init_repo(tmp_path)
    package = repo / ".spec" / "specs" / "2026-07-13_fix-active"
    package.mkdir(parents=True)
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
        "- [x] Item\n## 验收证据\n- 脚本验证：pytest\n**验收结果**：通过\n",
        encoding="utf-8",
    )

    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "active package")
    git(repo, "push", "origin", branch)

    # Without --allow-unarchived, push must hard-block
    result = run_push(repo, "--branch", branch, "--main-branch", "main")
    assert result.returncode != 0
    assert "unarchived active Spec package" in result.stderr
    # Branch must still exist (push did not proceed)
    assert "spec/test" in git(repo, "branch", "--list", "spec/test")

    # With --allow-unarchived, push proceeds (advisory only)
    result = run_push(repo, "--branch", branch, "--main-branch", "main", "--allow-unarchived")
    assert result.returncode == 0, result.stderr
    assert "--allow-unarchived" in result.stderr or "active Spec package" in result.stderr
    assert git(repo, "branch", "--show-current") == "main"


def test_push_rejects_dirty_tree(tmp_path):
    repo, branch = init_repo(tmp_path)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "working tree is not clean" in result.stderr
    assert "triage required before push" in result.stderr
    assert "if no active Spec package" in result.stderr
    assert "retro-pack" in result.stderr
    assert "never push unscoped dirty without retro-pack" in result.stderr


def test_push_fetches_before_execute_when_remote_tracking_ref_missing(tmp_path):
    repo, branch = init_repo(tmp_path)
    git(repo, "update-ref", "-d", "refs/remotes/origin/spec/test")

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode == 0
    assert "mode: execute" in result.stdout
    assert "origin/spec/test" not in git(repo, "branch", "-r", "--list", "origin/spec/test")


def test_push_rejects_removed_execute_flag(tmp_path):
    repo, branch = init_repo(tmp_path)

    result = run_push(repo, "--branch", branch, "--main-branch", "main", "--execute")

    assert result.returncode != 0
    assert "unrecognized arguments: --execute" in result.stderr


def test_push_rejects_dirty_tree_after_execute_flag_removal(tmp_path):
    repo, branch = init_repo(tmp_path)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "working tree is not clean" in result.stderr
    assert "if an active Spec package exists" in result.stderr
    assert "retro-pack" in result.stderr


def test_push_rejects_main_branch_after_execute_flag_removal(tmp_path):
    repo, _ = init_repo(tmp_path)
    git(repo, "checkout", "main")

    result = run_push(repo, "--branch", "main", "--main-branch", "main")

    assert result.returncode != 0
    assert "refusing to delete protected branch" in result.stderr


def test_push_allows_when_checked_out_on_main_if_branch_flag_set(tmp_path):
    """Checkout on main is fine when --branch names a working branch."""
    repo, branch = init_repo(tmp_path)
    git(repo, "checkout", "main")

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode == 0, result.stderr
    assert git(repo, "branch", "--show-current") == "main"


def test_push_rejects_remote_only_commits(tmp_path):
    repo, branch = init_repo(tmp_path)
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", str(tmp_path / "remote.git"), str(other)],
        check=True,
        capture_output=True,
    )
    git(other, "config", "user.email", "other@example.com")
    git(other, "config", "user.name", "Other User")
    git(other, "checkout", branch)
    (other / "remote-only.txt").write_text("remote only\n", encoding="utf-8")
    git(other, "add", "remote-only.txt")
    git(other, "commit", "-m", "remote only")
    git(other, "push", "origin", branch)

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "local branch does not match remote branch" in result.stderr
    assert "origin/spec/test" in git(repo, "branch", "-r", "--list", "origin/spec/test")


def test_push_merge_conflict_aborts_and_returns_to_original_branch(tmp_path):
    repo, branch = init_repo(tmp_path)
    git(repo, "checkout", "main")
    (repo / "conflict.txt").write_text("main\n", encoding="utf-8")
    git(repo, "add", "conflict.txt")
    git(repo, "commit", "-m", "main conflict")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", branch)
    (repo / "conflict.txt").write_text("feature\n", encoding="utf-8")
    git(repo, "add", "conflict.txt")
    git(repo, "commit", "-m", "feature conflict")
    git(repo, "push", "origin", branch)

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "Automatic merge failed" in result.stderr
    assert git(repo, "branch", "--show-current") == branch
    assert git(repo, "status", "--porcelain") == ""
    assert "origin/spec/test" in git(repo, "branch", "-r", "--list", "origin/spec/test")


def test_push_rejects_invalid_branch_name(tmp_path):
    repo, _ = init_repo(tmp_path)

    result = run_push(repo, "--branch=-bad", "--main-branch", "main")

    assert result.returncode != 0
    assert "invalid branch" in result.stderr


def test_push_rejects_full_ref_branch_name(tmp_path):
    repo, _ = init_repo(tmp_path)

    result = run_push(repo, "--branch", "refs/heads/main", "--main-branch", "main")

    assert result.returncode != 0
    assert "invalid branch" in result.stderr


def commit_dated(cwd: Path, subject: str, footer: str, date: str) -> None:
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = date
    env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        ["git", "commit", "-m", subject, "-m", footer],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def test_push_rejects_non_spec_branch_started_after_enforcement(tmp_path):
    repo, _ = init_repo(tmp_path)
    git(repo, "checkout", "-b", "feature/fresh-work", "main")
    write_archived_spec(repo)
    (repo / "fresh.txt").write_text("fresh\n", encoding="utf-8")
    git(repo, "add", "fresh.txt", ".spec")
    commit_dated(repo, "fresh work", f"Spec: {TEST_SPEC_SLUG}", "2026-08-28T12:00:00+00:00")
    main_before = git(repo, "rev-parse", "refs/heads/main")

    result = run_push(repo, "--branch", "feature/fresh-work", "--main-branch", "main")

    assert result.returncode != 0
    assert "refusing to push non-spec branch" in result.stderr
    assert "git branch -m feature/fresh-work spec/<name>" in result.stderr
    assert "feature/fresh-work" in git(repo, "branch", "--list", "feature/fresh-work")
    assert git(repo, "rev-parse", "refs/heads/main") == main_before


def test_push_allows_legacy_branch_with_pre_enforcement_work(tmp_path):
    repo, _ = init_repo(tmp_path)
    git(repo, "checkout", "-b", "feature/legacy-work", "main")
    write_archived_spec(repo)
    (repo / "legacy.txt").write_text("legacy\n", encoding="utf-8")
    git(repo, "add", "legacy.txt", ".spec")
    commit_dated(repo, "legacy work", f"Spec: {TEST_SPEC_SLUG}", "2026-08-20T12:00:00+00:00")

    result = run_push(repo, "--branch", "feature/legacy-work", "--main-branch", "main")

    assert result.returncode == 0, result.stderr
    assert "legacy branch feature/legacy-work predates" in result.stdout
    assert "rename recommended" in result.stdout
    assert git(repo, "branch", "--show-current") == "main"
    assert "feature/legacy-work" not in git(repo, "branch", "--list")


def test_push_rejects_tag_when_local_branch_missing(tmp_path):
    repo, branch = init_repo(tmp_path)
    git(repo, "checkout", "main")
    git(repo, "branch", "-D", branch)
    git(repo, "tag", branch)
    git(repo, "checkout", "-b", "scratch")

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "required local branch does not exist" in result.stderr
    assert git(repo, "branch", "--show-current") == "scratch"
    assert "origin/spec/test" in git(repo, "branch", "-r", "--list", "origin/spec/test")


def test_push_fast_forwards_local_main_when_remote_advanced(tmp_path):
    repo, branch = init_repo(tmp_path)
    other = tmp_path / "other-main"
    subprocess.run(
        ["git", "clone", str(tmp_path / "remote.git"), str(other)],
        check=True,
        capture_output=True,
    )
    git(other, "config", "user.email", "other@example.com")
    git(other, "config", "user.name", "Other User")
    git(other, "checkout", "main")
    (other / "remote-main.txt").write_text("remote main\n", encoding="utf-8")
    git(other, "add", "remote-main.txt")
    git(other, "commit", "-m", "remote main")
    git(other, "push", "origin", "main")

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode == 0
    assert git(repo, "branch", "--show-current") == "main"
    assert (repo / "remote-main.txt").read_text(encoding="utf-8") == "remote main\n"
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    assert "origin/spec/test" not in git(repo, "branch", "-r", "--list", "origin/spec/test")


def test_push_rejects_local_main_ahead_of_remote(tmp_path):
    repo, branch = init_repo(tmp_path)
    git(repo, "checkout", "main")
    (repo / "local-main.txt").write_text("local main\n", encoding="utf-8")
    git(repo, "add", "local-main.txt")
    git(repo, "commit", "-m", "local main only")
    git(repo, "checkout", branch)

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "local main branch does not fast-forward to remote main branch" in result.stderr
    assert "origin/spec/test" in git(repo, "branch", "-r", "--list", "origin/spec/test")


def test_push_shell_quotes_displayed_commands(tmp_path):
    repo, _ = init_repo(tmp_path)
    branch = "spec/quote;test"
    git(repo, "checkout", "-b", branch)
    (repo / "quoted.txt").write_text("quoted\n", encoding="utf-8")
    git(repo, "add", "quoted.txt")
    git(repo, "commit", "-m", "quoted")
    git(repo, "push", "-u", "origin", branch)

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode == 0
    assert "git checkout main" in result.stdout
    assert "'spec/quote;test'" in result.stdout


def test_push_ignores_path_injected_git_binary(tmp_path):
    repo, branch = init_repo(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\necho fake git should not run >&2\nexit 99\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = run_push(repo, "--branch", branch, "--main-branch", "main", env=env)

    assert result.returncode == 0
    assert "fake git should not run" not in result.stderr
    assert git(repo, "branch", "--show-current") == "main"


def test_push_rejects_remote_main_race_before_branch_delete(tmp_path):
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    initial_main_sha = git(repo, "rev-parse", "refs/heads/main")
    hook = remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read old new ref; do\n"
        '  if [ "$ref" = "refs/heads/main" ]; then\n'
        f"    git update-ref refs/heads/main {initial_main_sha}\n"
        "  fi\n"
        "done\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "remote main branch no longer contains merged branch" in result.stderr
    assert git(repo, "branch", "--show-current") == branch
    assert "origin/spec/test" in git(repo, "branch", "-r", "--list", "origin/spec/test")


def test_push_rejects_broken_git_hooks_warning_and_keeps_branch(tmp_path):
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    hook = remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        "printf '%s\\n' \"Remote repository Git hooks seem to be broken.\n"
        "Please repair server-side hooks, then push a new commit to refresh\n"
        'status." >&2\n'
        "exit 0\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "Remote reported broken repository Git hooks during push" in result.stderr
    assert "Resynchronize or reinstall the repository server-side hooks" in result.stderr
    assert "push a new commit to refresh repository activity" in result.stderr
    assert git(repo, "branch", "--show-current") == branch
    assert "spec/test" in git(repo, "branch", "--list", "spec/test")
    assert "origin/spec/test" in git(repo, "branch", "-r", "--list", "origin/spec/test")


def test_push_rejects_missing_remote_main_branch(tmp_path):
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    subprocess.run(
        ["git", "--git-dir", str(remote), "branch", "-D", "main"],
        check=True,
        capture_output=True,
        text=True,
    )

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "remote main branch not found" in result.stderr


def test_push_rejects_unconfigured_remote(tmp_path):
    repo, branch = init_repo(tmp_path)

    result = run_push(repo, "--branch", branch, "--remote", "upstream")

    assert result.returncode != 0
    assert "remote is not configured" in result.stderr


def test_push_rejects_protected_long_lived_branch(tmp_path):
    repo, _ = init_repo(tmp_path)
    git(repo, "checkout", "-b", "develop")
    (repo / "develop.txt").write_text("develop\n", encoding="utf-8")
    git(repo, "add", "develop.txt")
    git(repo, "commit", "-m", "develop")
    git(repo, "push", "-u", "origin", "develop")

    result = run_push(repo, "--branch", "develop", "--main-branch", "main")

    assert result.returncode != 0
    assert "refusing to delete protected branch" in result.stderr


def test_push_publishes_local_only_branch(tmp_path):
    repo, _ = init_repo(tmp_path)
    git(repo, "checkout", "main")
    git(repo, "checkout", "-b", "spec/local-only")
    (repo / "local.txt").write_text("local\n", encoding="utf-8")
    write_archived_spec(repo)
    git(repo, "add", "local.txt", ".spec")
    git(repo, "commit", "-m", "local only", "-m", f"Spec: {TEST_SPEC_SLUG}")
    # do not push spec/local-only

    result = run_push(repo, "--branch", "spec/local-only", "--main-branch", "main")

    assert result.returncode == 0, result.stderr
    assert "publish working branch first" in result.stdout or "will publish" in result.stdout
    assert git(repo, "branch", "--show-current") == "main"
    assert "spec/local-only" not in git(repo, "branch", "--list")


def test_push_falls_back_to_local_only_when_remote_unreachable(tmp_path):
    repo, branch = init_repo(tmp_path)
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing-remote.git"))

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode == 0, result.stderr
    assert "mode: local-only" in result.stdout
    assert "remote unavailable" in result.stdout
    assert "项目变更已合并到本地" in result.stdout
    assert "变更尚未同步到远端，工作分支已保留" in result.stdout
    assert "网络恢复后同步远端并清理工作分支" in result.stdout
    assert git(repo, "branch", "--show-current") == "main"
    assert "spec/test" in git(repo, "branch", "--list", "spec/test")
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "feature\n"


def test_push_recovers_after_local_only_run_when_remote_returns(tmp_path):
    repo, branch = init_repo(tmp_path)
    remote_url = git(repo, "remote", "get-url", "origin")
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing-remote.git"))

    offline = run_push(repo, "--branch", branch, "--main-branch", "main")
    assert offline.returncode == 0, offline.stderr
    assert "mode: local-only" in offline.stdout
    assert git(repo, "branch", "--show-current") == "main"

    git(repo, "remote", "set-url", "origin", remote_url)
    recovered = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert recovered.returncode == 0, recovered.stderr
    assert "mode: execute" in recovered.stdout
    assert "mode: local-only" not in recovered.stdout
    assert git(repo, "branch", "--show-current") == "main"
    assert "spec/test" not in git(repo, "branch", "--list", "spec/test")
    assert "origin/spec/test" not in git(repo, "branch", "-r", "--list", "origin/spec/test")


def test_remote_unavailable_does_not_treat_repository_not_found_as_network_error():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import push_spec_package
    finally:
        sys.path.pop(0)

    assert not push_spec_package.remote_unavailable_detected(
        "ERROR: repository not found\nfatal: Could not read from remote repository."
    )


def test_remote_default_branch_fail_closed_for_non_network_rejection(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import push_spec_package
    finally:
        sys.path.pop(0)

    completed = subprocess.CompletedProcess(
        args=["git", "ls-remote", "--symref", "origin", "HEAD"],
        returncode=1,
        stdout="",
        stderr="permission denied by policy",
    )
    monkeypatch.setattr(push_spec_package, "git", lambda *args, **kwargs: completed)

    try:
        push_spec_package.remote_default_branch(tmp_path, "origin")
    except push_spec_package.PushError as exc:
        assert "cannot determine remote default branch" in str(exc)
        assert "permission denied by policy" in str(exc)
    else:
        raise AssertionError("remote_default_branch should fail closed")


def test_push_does_not_fallback_for_remote_rejection(tmp_path):
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    hook = remote / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\necho 'permission denied by policy' >&2\nexit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "mode: local-only" not in result.stdout
    assert "permission denied by policy" in result.stderr
    assert git(repo, "branch", "--show-current") == branch
    assert "origin/spec/test" in git(repo, "branch", "-r", "--list", "origin/spec/test")


def test_touched_spec_path_only_accepts_trusted_roots():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from spec_package_support import scope_from_changed_paths
    finally:
        sys.path.pop(0)

    paths = [
        "hardware/specs/draft-a/spec.md",
        ".spec/specs/2026-07-13_fix-a/spec.md",
        ".trae/specs/archive/2026-07-13_fix-b/tasks.md",
        "custom/spec-state/specs/2026-07-13_fix-c/checklist.md",
        ".spec/local/specs/2026-07-13_fix-d/spec.md",
    ]
    slugs = scope_from_changed_paths(paths, ["custom/spec-state", ".spec/local"])
    assert slugs == [
        "2026-07-13_fix-a",
        "2026-07-13_fix-b",
        "2026-07-13_fix-c",
        "2026-07-13_fix-d",
    ]


@pytest.mark.parametrize("operation", ["execute_plan", "execute_local_only_plan", "execute_recovery_plan"])
def test_push_execution_paths_check_clean_tree_before_mutation(tmp_path, monkeypatch, operation):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import push_spec_package as push

    calls = []

    def clean(root):
        calls.append("clean")
        assert root == tmp_path

    monkeypatch.setattr(push, "ensure_clean_tree", clean)

    def fail_run_checked(root, argv, repair=None):
        raise push.PushError("injected failure")

    monkeypatch.setattr(push, "current_branch", lambda root: "spec/test")
    monkeypatch.setattr(push, "git_stdout", lambda *args: "")
    monkeypatch.setattr(push, "run_checked", fail_run_checked)
    monkeypatch.setattr(push, "run_best_effort", lambda root, argv: calls.append(argv))
    args = (
        (tmp_path, "spec/test", "main")
        if operation == "execute_local_only_plan"
        else (tmp_path, "origin", "spec/test", "main", "branch-sha", "main-sha")
    )
    kwargs = {"push_branch_first": False} if operation == "execute_plan" else {}
    with pytest.raises(push.PushError, match="injected failure"):
        getattr(push, operation)(*args, **kwargs)
    assert calls[0] == "clean"
    assert calls[-2:] == [["git", "merge", "--abort"], ["git", "checkout", "spec/test"]]


# --- Gitea hook repair integration -----------------------------------------


def _write_stateful_broken_hook(remote: Path, state_file: Path) -> None:
    """Install a post-receive hook that emits the Gitea broken-hooks warning
    while the state file says "broken", and stays silent once it says "ok"."""
    hook = remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        f'if [ "$(cat "{state_file}")" = "broken" ]; then\n'
        "  printf '%s\\n' \"Remote repository Git hooks seem to be broken.\"\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)


class _GiteaStubHandler(http.server.BaseHTTPRequestHandler):
    """Emulates POST /api/v1/admin/cron/{task}; flips hook state to ok."""

    state_file: Path = Path("/nonexistent")
    requests: list[str] = []
    fail_task: str | None = None

    def do_POST(self) -> None:  # noqa: N802
        task = self.path.rsplit("/", 1)[-1]
        type(self).requests.append(task)
        if task == type(self).fail_task:
            self.send_response(403)
            self.end_headers()
            return
        type(self).state_file.write_text("ok", encoding="utf-8")
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture()
def gitea_stub(tmp_path):
    state_file = tmp_path / "hook-state"
    state_file.write_text("broken", encoding="utf-8")
    _GiteaStubHandler.state_file = state_file
    _GiteaStubHandler.requests = []
    _GiteaStubHandler.fail_task = None
    server = http.server.HTTPServer(("127.0.0.1", 0), _GiteaStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", state_file
    server.shutdown()
    server.server_close()


def test_push_repairs_gitea_hooks_and_reruns_plan(tmp_path, gitea_stub):
    gitea_url, state_file = gitea_stub
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    _write_stateful_broken_hook(remote, state_file)

    result = run_push(
        repo,
        "--branch",
        branch,
        "--main-branch",
        "main",
        "--repair-gitea-hooks",
        "--gitea-url",
        gitea_url,
        "--gitea-token",
        "admin-token",
    )

    assert result.returncode == 0, result.stderr
    assert _GiteaStubHandler.requests == [
        "sync_repo_branches",
        "sync_repo_tags",
        "resync_all_hooks",
    ]
    assert "retrying Spec push plan after Gitea hook repair" in result.stderr
    assert git(repo, "branch", "--show-current") == "main"
    assert "spec/test" not in git(repo, "branch", "--list")
    assert "origin/spec/test" not in git(repo, "branch", "-r", "--list")


def test_push_repair_failure_fails_closed_and_keeps_branch(tmp_path, gitea_stub):
    gitea_url, state_file = gitea_stub
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    _write_stateful_broken_hook(remote, state_file)
    _GiteaStubHandler.fail_task = "sync_repo_tags"

    result = run_push(
        repo,
        "--branch",
        branch,
        "--main-branch",
        "main",
        "--repair-gitea-hooks",
        "--gitea-url",
        gitea_url,
        "--gitea-token",
        "admin-token",
    )

    assert result.returncode != 0
    assert "Gitea hook repair failed" in result.stderr
    assert "403" in result.stderr
    assert git(repo, "branch", "--show-current") == branch
    assert "spec/test" in git(repo, "branch", "--list")
    # No retry happened after the failed repair: only two tasks attempted.
    assert _GiteaStubHandler.requests == ["sync_repo_branches", "sync_repo_tags"]


def test_push_repair_without_token_fails_closed(tmp_path, gitea_stub):
    gitea_url, state_file = gitea_stub
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    _write_stateful_broken_hook(remote, state_file)

    result = run_push(
        repo,
        "--branch",
        branch,
        "--main-branch",
        "main",
        "--repair-gitea-hooks",
        "--gitea-url",
        gitea_url,
    )

    assert result.returncode != 0
    assert "credentials are incomplete" in result.stderr
    assert _GiteaStubHandler.requests == []
    assert git(repo, "branch", "--show-current") == branch


def test_push_without_repair_flag_keeps_legacy_fail_closed(tmp_path, gitea_stub):
    gitea_url, state_file = gitea_stub
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    _write_stateful_broken_hook(remote, state_file)

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "Remote reported broken repository Git hooks during push" in result.stderr
    assert "--repair-gitea-hooks" in result.stderr
    assert _GiteaStubHandler.requests == []
    assert git(repo, "branch", "--show-current") == branch


def test_push_local_only_plan_keeps_legacy_branch_note(tmp_path):
    """Legacy rename advisory must survive the local-only plan path too."""
    repo, branch = init_repo(tmp_path)
    git(repo, "checkout", "-b", "feature/legacy-work", "main")
    write_archived_spec(repo)
    (repo / "legacy.txt").write_text("legacy\n", encoding="utf-8")
    git(repo, "add", "legacy.txt", ".spec")
    commit_dated(repo, "legacy work", f"Spec: {TEST_SPEC_SLUG}", "2026-08-20T12:00:00+00:00")
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing-remote.git"))

    result = run_push(repo, "--branch", "feature/legacy-work", "--main-branch", "main")

    assert result.returncode == 0, result.stderr
    assert "mode: local-only" in result.stdout
    assert "legacy branch feature/legacy-work predates" in result.stdout
    assert "rename recommended" in result.stdout

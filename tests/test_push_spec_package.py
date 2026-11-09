from __future__ import annotations

import os
import subprocess
import sys
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

    assert result.returncode == 0, result.stderr
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
## 6. 成功标准与验证方式
- [x] 门禁通过 -> verify: task_0001
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


def test_push_rejects_non_spec_branch_started_before_enforcement(tmp_path):
    repo, _ = init_repo(tmp_path)
    git(repo, "checkout", "-b", "feature/legacy-work", "main")
    (repo / "legacy.txt").write_text("legacy\n", encoding="utf-8")
    git(repo, "add", "legacy.txt")
    commit_dated(repo, "legacy work", f"Spec: {TEST_SPEC_SLUG}", "2026-08-20T12:00:00+00:00")
    main_before = git(repo, "rev-parse", "refs/heads/main")

    result = run_push(repo, "--branch", "feature/legacy-work", "--main-branch", "main")

    assert result.returncode != 0
    assert "refusing to push non-spec branch" in result.stderr
    assert git(repo, "rev-parse", "refs/heads/main") == main_before
    assert git(repo, "branch", "--show-current") == "feature/legacy-work"


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


@pytest.mark.parametrize("protected_branch", ["develop", "release/1.0", "spec/test"])
def test_protected_main_branch_cannot_be_merge_target(tmp_path, protected_branch):
    repo, branch = init_repo(tmp_path)
    if protected_branch != branch:
        git(repo, "checkout", "-b", protected_branch, "main")
        git(repo, "push", "origin", protected_branch)
        git(repo, "checkout", branch)
    protected_tip = git(repo, "rev-parse", f"refs/heads/{protected_branch}")
    remote_protected_tip = git(repo, "ls-remote", "origin", f"refs/heads/{protected_branch}").split()[0]

    result = run_push(repo, "--branch", branch, "--main-branch", protected_branch)

    assert result.returncode != 0
    assert "refusing to write protected branch" in result.stderr or (
        protected_branch == branch and "refusing to delete protected branch" in result.stderr
    )
    assert git(repo, "rev-parse", f"refs/heads/{protected_branch}") == protected_tip
    assert git(repo, "ls-remote", "origin", f"refs/heads/{protected_branch}").split()[0] == remote_protected_tip
    assert git(repo, "ls-remote", "origin", f"refs/heads/{branch}")


def test_remote_default_branch_is_never_deleted(tmp_path):
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    git(remote, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    branch_tip = git(repo, "rev-parse", f"refs/heads/{branch}")

    result = run_push(repo, "--branch", branch)

    assert result.returncode != 0
    assert "refusing to delete protected branch" in result.stderr
    assert git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0] == branch_tip
    assert git(repo, "branch", "--show-current") == branch


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


def test_push_stops_before_mutations_when_remote_unreachable(tmp_path):
    """An unreachable remote stops the run before any mutation: no local
    merge, no branch deletion — the operator fixes connectivity and reruns."""
    repo, branch = init_repo(tmp_path)
    main_tip = git(repo, "rev-parse", "refs/heads/main")
    branch_tip = git(repo, "rev-parse", f"refs/heads/{branch}")
    remote_main_tip = git(repo, "rev-parse", "refs/remotes/origin/main")
    remote_branch_tip = git(repo, "rev-parse", f"refs/remotes/origin/{branch}")
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing-remote.git"))

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode != 0
    assert "remote unavailable" in result.stderr
    assert git(repo, "branch", "--show-current") == branch
    assert git(repo, "rev-parse", "refs/heads/main") == main_tip
    assert git(repo, "rev-parse", f"refs/heads/{branch}") == branch_tip
    assert git(repo, "rev-parse", "refs/remotes/origin/main") == remote_main_tip
    assert git(repo, "rev-parse", f"refs/remotes/origin/{branch}") == remote_branch_tip
    assert git(repo, "status", "--porcelain") == ""


def test_dry_run_reports_plan_without_local_or_remote_mutations(tmp_path):
    repo, branch = init_repo(tmp_path)
    before = {ref: git(repo, "rev-parse", ref) for ref in ("main", branch, "origin/main", f"origin/{branch}")}
    result = run_push(repo, "--branch", branch, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout.lower()
    assert "git merge" in result.stdout
    assert git(repo, "branch", "--show-current") == branch
    assert {ref: git(repo, "rev-parse", ref) for ref in before} == before
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == before["origin/main"]
    assert git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0] == before[f"origin/{branch}"]


def test_dry_run_reports_plan_when_remote_main_has_unfetched_commits(tmp_path):
    repo, branch = init_repo(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(tmp_path / "remote.git"), str(other)], check=True, capture_output=True)
    git(other, "config", "user.email", "other@example.com")
    git(other, "config", "user.name", "Other User")
    git(other, "checkout", "main")
    (other / "advance.txt").write_text("remote advance\n", encoding="utf-8")
    git(other, "add", "advance.txt")
    git(other, "commit", "-m", "remote advances")
    git(other, "push", "origin", "main")
    before = {ref: git(repo, "rev-parse", ref) for ref in ("main", branch, "origin/main", f"origin/{branch}")}
    fetch_head_path = repo / ".git" / "FETCH_HEAD"
    fetch_head_path.write_text("preserve fetch state\n", encoding="utf-8")
    fetch_head_before = fetch_head_path.read_bytes()

    result = run_push(repo, "--branch", branch, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout.lower()
    assert git(repo, "branch", "--show-current") == branch
    assert {ref: git(repo, "rev-parse", ref) for ref in before} == before
    assert fetch_head_path.read_bytes() == fetch_head_before
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] != before["origin/main"]


def test_dry_run_reports_publish_lease_for_missing_remote_branch(tmp_path):
    repo, _ = init_repo(tmp_path)
    branch = "spec/missing-remotely"
    git(repo, "checkout", "-b", branch)
    branch_tip = git(repo, "rev-parse", f"refs/heads/{branch}")

    result = run_push(repo, "--branch", branch, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert f"--force-with-lease=refs/heads/{branch}: " in result.stdout
    assert f"--force-with-lease=refs/heads/{branch}:{branch_tip}" in result.stdout
    assert f"origin {branch_tip}:refs/heads/{branch}" in result.stdout
    assert git(repo, "ls-remote", "origin", f"refs/heads/{branch}") == ""
    assert git(repo, "branch", "--show-current") == branch


def test_dry_run_reports_locally_ahead_branch_and_exact_deletion_lease(tmp_path):
    repo, branch = init_repo(tmp_path)
    (repo / "extra.txt").write_text("local work\n", encoding="utf-8")
    git(repo, "add", "extra.txt")
    git(repo, "commit", "-m", "local extra")
    local_tip = git(repo, "rev-parse", f"refs/heads/{branch}")
    remote_tip = git(repo, "rev-parse", f"refs/remotes/origin/{branch}")

    result = run_push(repo, "--branch", branch, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert f"--force-with-lease=refs/heads/{branch}:{remote_tip}" in result.stdout
    assert f"origin {local_tip}:refs/heads/{branch}" in result.stdout
    assert f"git merge --no-ff {local_tip}" in result.stdout
    assert f"--force-with-lease=refs/heads/{branch}:{local_tip}" in result.stdout
    assert git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0] == remote_tip
    assert git(repo, "branch", "--show-current") == branch


def test_remote_lease_race_preserves_both_working_branches(tmp_path):
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    git(repo, "checkout", "--detach", "main")
    (repo / "competing.txt").write_text("competing commit\n", encoding="utf-8")
    git(repo, "add", "competing.txt")
    git(repo, "commit", "-qm", "competing")
    competing_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", f"{competing_sha}:refs/heads/competitor")
    git(repo, "checkout", branch)
    hook = remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read old new ref; do\n"
        '  if [ "$ref" = "refs/heads/main" ]; then\n'
        f"    git update-ref refs/heads/{branch} {competing_sha}\n"
        "  fi\n"
        "done\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    result = run_push(repo, "--branch", branch)
    assert result.returncode != 0
    assert git(repo, "show-ref", "--verify", f"refs/heads/{branch}").split()[1] == f"refs/heads/{branch}"
    assert git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0] == competing_sha


def test_remote_push_rejected_even_when_error_mentions_up_to_date(tmp_path):
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nprintf 'Everything up-to-date\\n' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    main_tip = git(repo, "ls-remote", "origin", "refs/heads/main").split()[0]

    result = run_push(repo, "--branch", branch)

    assert result.returncode != 0
    assert "Everything up-to-date" in result.stderr
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == main_tip
    assert git(repo, "ls-remote", "origin", f"refs/heads/{branch}")


@pytest.mark.parametrize("status", ["=\trefs/heads/x:refs/heads/x\t[aktuell]", "fatal: no status line"])
def test_publish_without_porcelain_update_status_fails_closed(tmp_path, monkeypatch, status):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import push_spec_package as push

    result = subprocess.CompletedProcess(["git", "push"], 0, stdout=status, stderr="")
    monkeypatch.setattr(push, "git", lambda *args, **kwargs: result)
    command = ["git", "push", "-u", "--porcelain", "--force-with-lease=refs/heads/x:", "origin", "x:x"]

    with pytest.raises(push.PushError, match="did not update"):
        push.run_checked(tmp_path, command)


@pytest.mark.parametrize(
    "status",
    [
        " \trefs/heads/x:refs/heads/x\t1a..2b",
        "+\trefs/heads/x:refs/heads/x\t1a...2b (forced update)",
        "*\trefs/heads/x:refs/heads/x\t[new branch]",
    ],
)
def test_publish_accepts_porcelain_update_statuses(tmp_path, monkeypatch, status):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import push_spec_package as push

    result = subprocess.CompletedProcess(["git", "push"], 0, stdout=status, stderr="")
    monkeypatch.setattr(push, "git", lambda *args, **kwargs: result)

    push.run_checked(
        tmp_path, ["git", "push", "-u", "--porcelain", "--force-with-lease=refs/heads/x:", "origin", "x:x"]
    )


def test_publish_never_sends_local_ref_drift(tmp_path, monkeypatch):
    """A ref moved after review must not be force-published under the review's lease."""
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import argparse

    import push_spec_package as push

    repo, branch = init_repo(tmp_path)
    (repo / "reviewed.txt").write_text("reviewed\n", encoding="utf-8")
    git(repo, "add", "reviewed.txt")
    git(repo, "commit", "-m", "reviewed work")
    reviewed_sha = git(repo, "rev-parse", f"refs/heads/{branch}")
    remote_branch_tip = git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0]
    remote_main_tip = git(repo, "ls-remote", "origin", "refs/heads/main").split()[0]
    drifted = {}
    run_checked = push.run_checked

    def drift_before_publish(root, command):
        if command[:2] == ["git", "push"] and "--porcelain" in command:
            (repo / "drift.txt").write_text("unreviewed\n", encoding="utf-8")
            git(repo, "add", "drift.txt")
            git(repo, "commit", "-m", "unreviewed drift")
            drifted["sha"] = git(repo, "rev-parse", f"refs/heads/{branch}")
        run_checked(root, command)

    monkeypatch.setattr(push, "run_checked", drift_before_publish)
    monkeypatch.setattr(push, "run_spec_gate", lambda *args, **kwargs: [])
    args = argparse.Namespace(
        remote="origin",
        main_branch="main",
        dry_run=False,
        slugs=[],
        all_packages=False,
        allow_unarchived=True,
        specs_dirs=[],
    )

    with pytest.raises(push.PushError, match="local branch moved"):
        push._run_push_plan(args, repo, branch)

    assert drifted["sha"] != reviewed_sha
    assert remote_branch_tip != reviewed_sha
    assert git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0] == reviewed_sha
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == remote_main_tip
    assert git(repo, "rev-parse", "refs/heads/main") == remote_main_tip
    assert git(repo, "rev-parse", f"refs/heads/{branch}") == drifted["sha"]
    assert git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("same_commit", [False, True])
def test_remote_branch_created_during_publish_is_not_deleted(tmp_path, monkeypatch, same_commit):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import push_spec_package as push

    repo, _ = init_repo(tmp_path)
    branch = "spec/newly-created"
    git(repo, "checkout", "-b", branch)
    local_tip = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--detach", "main")
    (repo / "competing.txt").write_text("concurrent branch\n", encoding="utf-8")
    git(repo, "add", "competing.txt")
    git(repo, "commit", "-m", "competing branch")
    competing_sha = local_tip if same_commit else git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", branch)
    main_tip = git(repo, "ls-remote", "origin", "refs/heads/main").split()[0]
    run_checked = push.run_checked

    def create_remote_branch_at_publish(root, command):
        if command[:3] == ["git", "push", "-u"]:
            git(repo, "push", "origin", f"{competing_sha}:refs/heads/{branch}")
        run_checked(root, command)

    monkeypatch.setattr(push, "run_checked", create_remote_branch_at_publish)
    with pytest.raises(push.PushError):
        push.execute_plan(repo, "origin", branch, "main", None, main_tip, push_branch_first=True, publish_sha=local_tip)

    assert git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0] == competing_sha
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == main_tip
    assert git(repo, "show-ref", "--verify", f"refs/heads/{branch}").split()[0] == local_tip


def test_push_reports_remote_auth_error_without_offline_fallback(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import push_spec_package as push

    result = subprocess.CompletedProcess(
        args=["git", "ls-remote"],
        returncode=128,
        stdout="",
        stderr="ERROR: repository not found\nfatal: Could not read from remote repository.",
    )
    monkeypatch.setattr(push, "git", lambda *args, **kwargs: result)
    with pytest.raises(push.PushError, match="repository not found"):
        push.remote_default_branch(tmp_path, "origin")


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


def test_rejected_main_push_reports_local_merge_state(tmp_path):
    repo, branch = init_repo(tmp_path)
    remote = Path(git(repo, "remote", "get-url", "origin"))
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nprintf 'denied\\n' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    main_tip = git(repo, "rev-parse", "refs/heads/main")
    branch_tip = git(repo, "rev-parse", f"refs/heads/{branch}")

    result = run_push(repo, "--branch", branch)

    assert result.returncode != 0
    assert "denied" in result.stderr
    assert "local main" in result.stderr.lower() and "merge" in result.stderr.lower()
    assert git(repo, "branch", "--show-current") == branch
    assert git(repo, "rev-parse", "refs/heads/main") != main_tip
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == main_tip
    assert git(repo, "rev-parse", f"refs/heads/{branch}") == branch_tip
    assert git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0] == branch_tip
    assert git(repo, "status", "--porcelain") == ""


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


def test_touched_spec_path_exempts_retired_archive_subtree():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from spec_package_support import scope_from_changed_paths
    finally:
        sys.path.pop(0)

    paths = [
        ".spec/specs/archive/retired/2026-09-30/MANIFEST.md",
        ".spec/specs/archive/retired/2026-09-30/docs/guide.md",
        ".trae/specs/archive/retired/2026-09-30/server/run.py",
    ]

    assert scope_from_changed_paths(paths) == []


def test_push_execute_plan_checks_clean_tree_before_mutation(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import push_spec_package as push

    calls = []

    def clean(root):
        calls.append("clean")
        assert root == tmp_path

    monkeypatch.setattr(push, "ensure_clean_tree", clean)

    def fail_run_checked(root, argv):
        raise push.PushError("injected failure")

    monkeypatch.setattr(push, "current_branch", lambda root: "spec/test")
    monkeypatch.setattr(push, "git_stdout", lambda *args: "")
    monkeypatch.setattr(push, "run_checked", fail_run_checked)
    monkeypatch.setattr(push, "run_best_effort", lambda root, argv: calls.append(argv))
    with pytest.raises(push.PushError, match="injected failure"):
        push.execute_plan(
            tmp_path,
            "origin",
            "spec/test",
            "main",
            "branch-sha",
            "main-sha",
            push_branch_first=False,
            publish_sha="branch-sha",
        )
    assert calls[0] == "clean"
    assert calls[-2:] == [["git", "merge", "--abort"], ["git", "checkout", "spec/test"]]


class TestSubprocessBoundedness:
    """F21: every git/subprocess call in the push gate is bounded — a stalled
    network operation fails diagnosably instead of hanging the gate."""

    @pytest.fixture(autouse=True)
    def _module(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import push_spec_package

            self.module = push_spec_package
            yield
        finally:
            sys.path.pop(0)

    def test_git_timeout_maps_to_push_error(self, tmp_path, monkeypatch):
        def hung(args, **kwargs):
            assert "timeout" in kwargs, "git() must pass a timeout"
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])

        monkeypatch.setattr(self.module.subprocess, "run", hung)
        with pytest.raises(self.module.PushError, match="timed out within 300s"):
            self.module.git(tmp_path, "fetch", "origin")

    def test_run_checked_timeout_maps_to_push_error(self, tmp_path, monkeypatch):
        def hung(command, **kwargs):
            assert "timeout" in kwargs
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        monkeypatch.setattr(self.module.subprocess, "run", hung)
        with pytest.raises(self.module.PushError, match="timed out within 300s"):
            self.module.run_checked(tmp_path, ["git", "push", "origin", "main"])

    def test_run_best_effort_timeout_is_swallowed(self, tmp_path, monkeypatch):
        def hung(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        monkeypatch.setattr(self.module.subprocess, "run", hung)
        self.module.run_best_effort(tmp_path, ["git", "push", "--delete", "origin", "x"])  # no raise


# -- F9 (AUD-06): footer-attribution interval warnings (observation period) --

OLD_SPEC_SLUG = "2026-08-01_legacy-audit-fixture"


def _write_summary_with_interval(package: Path, slug: str, base: str, head: str) -> None:
    (package / "completion-summary.md").write_text(
        f"""# {slug} - 完成总结
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
## Git 记录
- 提交：pending local commit
- 提交区间：{base}..{head}
- 推送：not-run
## 问题处置
```json
{{"version":1,"issues":[]}}
```
""",
        encoding="utf-8",
    )


def _footer_push_sandbox(tmp_path: Path) -> tuple[Path, str, str]:
    """Bare remote + clone whose main already archives OLD_SPEC_SLUG.

    Returns (repo, initial_sha, main_tip). The archived summary records a
    提交区间 anchored at the initial commit (base == head == the pre-archive
    tip, both shas known before the archive commit lands — the shape an
    archive recorded at archive time produces on this history).
    """
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Spec Test")
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial")
    ancient = git(repo, "rev-parse", "HEAD")
    package = write_archived_spec(repo, OLD_SPEC_SLUG)
    _write_summary_with_interval(package, OLD_SPEC_SLUG, ancient, ancient)
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "chore(spec): archive old fixture", "-m", f"Spec: {OLD_SPEC_SLUG}")
    git(repo, "branch", "-M", "main")
    git(repo, "push", "-u", "origin", "main")
    return repo, ancient, git(repo, "rev-parse", "HEAD")


def test_push_warns_when_footer_cites_archive_before_window(tmp_path):
    # EXP-04(c) replay: a code-only commit whose Spec footer cites an archived
    # package delivered long before this branch's window. Observation period:
    # the push succeeds and stderr names the evidence triple.
    repo, ancient, main_tip = _footer_push_sandbox(tmp_path)
    git(repo, "checkout", "-b", "spec/sneaky-reuse")
    (repo / "sneaky.py").write_text("import os\nos.system('echo pwned')\n", encoding="utf-8")
    git(repo, "add", "sneaky.py")
    git(repo, "commit", "-m", "feat: innocent change", "-m", f"Spec: {OLD_SPEC_SLUG}")
    result = run_push(repo, "--branch", "spec/sneaky-reuse")
    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "spec gate warning: footer attribution cites an archive delivered before this push window" in combined
    assert f"- slug: {OLD_SPEC_SLUG}" in combined
    assert f"- archive commit range: {ancient}..{ancient}" in combined
    assert f"- push window base: {main_tip}" in combined
    assert "advisory only" in combined


def test_push_rejects_main_already_merged_locally_without_remote_mutation(tmp_path):
    repo, branch = init_repo(tmp_path)
    remote_tip = git(repo, "rev-parse", "origin/main")
    git(repo, "checkout", "main")
    git(repo, "merge", "--no-ff", branch, "-m", "pre-merge locally")
    local_tip = git(repo, "rev-parse", "main")

    result = run_push(repo, "--branch", branch)

    assert result.returncode != 0
    assert "local main branch does not fast-forward" in result.stderr
    assert git(repo, "rev-parse", "main") == local_tip
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == remote_tip
    assert git(repo, "show-ref", "--verify", f"refs/heads/{branch}").split()[1] == f"refs/heads/{branch}"


def test_push_footer_multi_theme_multi_package_delivery_no_warning(tmp_path):
    # Retro-pack / multi-theme shape: every theme package is archived on the
    # branch BEFORE the push (commands.md packing path), so the delivery runs
    # the touched-package channel — several packages, several code commits,
    # zero interval warnings.
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Spec Test")
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial")
    git(repo, "branch", "-M", "main")
    git(repo, "push", "-u", "origin", "main")
    git(repo, "checkout", "-b", "spec/multi-theme")
    for slug in ("2026-08-01_theme-one", "2026-08-02_theme-two"):
        write_archived_spec(repo, slug)
        git(repo, "add", ".spec")
        git(repo, "commit", "-m", f"chore(spec): archive {slug}", "-m", f"Spec: {slug}")
        (repo / f"{slug}.txt").write_text("theme work\n", encoding="utf-8")
        git(repo, "add", f"{slug}.txt")
        git(repo, "commit", "-m", f"feat: {slug}", "-m", f"Spec: {slug}")
    result = run_push(repo, "--branch", "spec/multi-theme")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "spec gate warning" not in result.stdout + result.stderr


def test_push_footer_cites_legacy_archive_without_interval_no_warning(tmp_path):
    # Pre-F9 history: an archive without the interval line stays silent.
    repo, _ancient, _main_tip = _footer_push_sandbox(tmp_path)
    summary_path = repo / ".spec" / "specs" / "archive" / OLD_SPEC_SLUG / "completion-summary.md"
    lines = [
        line for line in summary_path.read_text(encoding="utf-8").splitlines() if not line.startswith("- 提交区间：")
    ]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "chore(spec): drop interval line", "-m", f"Spec: {OLD_SPEC_SLUG}")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "-b", "spec/cite-legacy")
    (repo / "legacy.txt").write_text("cites legacy archive\n", encoding="utf-8")
    git(repo, "add", "legacy.txt")
    git(repo, "commit", "-m", "fix: legacy citation", "-m", f"Spec: {OLD_SPEC_SLUG}")
    result = run_push(repo, "--branch", "spec/cite-legacy")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "spec gate warning" not in result.stdout + result.stderr


def test_push_touched_channel_multi_commit_delivery_no_warning(tmp_path):
    # The touched-package channel (archive + code on the branch) never enters
    # the footer interval judge: same package, multiple commits, no warnings.
    repo, _remote = tmp_path / "repo", None
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Spec Test")
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial")
    git(repo, "branch", "-M", "main")
    git(repo, "push", "-u", "origin", "main")
    git(repo, "checkout", "-b", "spec/multi-commit")
    write_archived_spec(repo)
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "chore(spec): archive fixture", "-m", f"Spec: {TEST_SPEC_SLUG}")
    for name in ("part-one.txt", "part-two.txt"):
        (repo / name).write_text("delivery\n", encoding="utf-8")
        git(repo, "add", name)
        git(repo, "commit", "-m", f"feat: {name}", "-m", f"Spec: {TEST_SPEC_SLUG}")
    result = run_push(repo, "--branch", "spec/multi-commit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "spec gate warning" not in result.stdout + result.stderr


def test_footer_interval_warnings_judge(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_all_spec_packages import footer_interval_warnings, render_footer_interval_warnings

    repo, ancient, main_tip = _footer_push_sandbox(tmp_path)
    git(repo, "checkout", "-qb", "spec/judge")
    (repo / "judge.txt").write_text("judge\n", encoding="utf-8")
    git(repo, "add", "judge.txt")
    git(repo, "commit", "-m", "feat: judge probe", "-m", f"Spec: {OLD_SPEC_SLUG}")
    head = git(repo, "rev-parse", "HEAD")

    # Ancient base strictly older than the window base: warned with evidence.
    warnings = footer_interval_warnings(repo, [OLD_SPEC_SLUG], revision=head, window_base=main_tip, specs_dirs=[])
    assert len(warnings) == 1
    assert warnings[0].slug == OLD_SPEC_SLUG
    assert warnings[0].base == ancient
    assert warnings[0].head == ancient
    rendered = render_footer_interval_warnings(warnings)
    assert OLD_SPEC_SLUG in rendered and ancient in rendered and main_tip in rendered

    # Window base equal to the interval base: this window delivered it.
    assert footer_interval_warnings(repo, [OLD_SPEC_SLUG], revision=head, window_base=ancient, specs_dirs=[]) == []

    # Not yet archived at the revision: lifecycle owned elsewhere, silent.
    missing = footer_interval_warnings(repo, ["2099-01-01_missing"], revision=head, window_base=main_tip, specs_dirs=[])
    assert missing == []

    # Malformed window base: skip honestly instead of guessing.
    assert footer_interval_warnings(repo, [OLD_SPEC_SLUG], revision=head, window_base="not-a-sha", specs_dirs=[]) == []

    # No slugs: nothing to judge.
    assert footer_interval_warnings(repo, [], revision=head, window_base=main_tip, specs_dirs=[]) == []


# -- F11 lite channel: `Spec: none (<reason>)` --


def _none_channel_repo(tmp_path: Path, *, active_package: bool) -> tuple[Path, str]:
    repo, branch = init_repo(tmp_path, active_baseline="2026-07-13_fix-active" if active_package else None)
    git(repo, "checkout", "-B", "spec/lite", "main")
    (repo / "typo.txt").write_text("typo fix\n", encoding="utf-8")
    git(repo, "add", "typo.txt")
    git(repo, "commit", "-m", "fix: trivial typo", "-m", "Spec: none (typo fix with test)")
    return repo, "spec/lite"


def test_push_lite_channel_accepts_none_footer_without_active_package(tmp_path):
    repo, branch = _none_channel_repo(tmp_path, active_package=False)

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "lite-channel attribution" in result.stdout
    assert "none (typo fix with test)" in result.stdout


def test_push_lite_channel_warns_on_missing_reason(tmp_path):
    repo, _ = _none_channel_repo(tmp_path, active_package=False)
    git(repo, "commit", "--allow-empty", "-m", "no reason", "-m", "Spec: none")

    result = run_push(repo, "--branch", "spec/lite", "--main-branch", "main")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "advisory" in result.stderr.lower()
    assert "without" in result.stderr.lower()


def test_push_lite_channel_warns_when_active_package_exists(tmp_path):
    repo, branch = _none_channel_repo(tmp_path, active_package=True)

    result = run_push(repo, "--branch", branch, "--main-branch", "main")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "advisory" in result.stderr.lower()
    assert "2026-07-13_fix-active" in result.stderr


def test_explicit_slug_cannot_be_bypassed_when_no_spec_paths_changed(tmp_path):
    repo, branch = _none_channel_repo(tmp_path, active_package=True)
    remote_main = git(repo, "rev-parse", "origin/main")
    result = run_push(repo, "--branch", branch, "--slug", "2026-07-13_fix-active")
    assert result.returncode != 0
    assert "2026-07-13_fix-active" in result.stderr
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == remote_main
    assert git(repo, "show-ref", "--verify", f"refs/heads/{branch}").split()[1] == f"refs/heads/{branch}"


def test_all_packages_cannot_be_bypassed_when_no_spec_paths_changed(tmp_path):
    repo, branch = _none_channel_repo(tmp_path, active_package=True)
    remote_main = git(repo, "rev-parse", "origin/main")
    result = run_push(repo, "--branch", branch, "--all-packages")
    assert result.returncode != 0
    assert "2026-07-13_fix-active" in result.stderr
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == remote_main
    assert git(repo, "show-ref", "--verify", f"refs/heads/{branch}").split()[1] == f"refs/heads/{branch}"


def test_mixed_footerless_and_touched_package_still_warns(tmp_path):
    repo, branch = init_repo(tmp_path)
    (repo / "extra.txt").write_text("unattributed work\n", encoding="utf-8")
    git(repo, "add", "extra.txt")
    git(repo, "commit", "-m", "work without footer")
    missing = git(repo, "rev-parse", "HEAD")[:12]

    result = run_push(repo, "--branch", branch)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "advisory" in result.stderr.lower()
    assert missing in result.stderr
    assert "changed-package attribution" in result.stdout


def test_unknown_footer_is_advisory_not_a_package_gate(tmp_path):
    repo, _ = init_repo(tmp_path)
    git(repo, "checkout", "-B", "spec/advisory", "main")
    (repo / "change.txt").write_text("unpackaged work\n", encoding="utf-8")
    git(repo, "add", "change.txt")
    git(repo, "commit", "-m", "unpackaged", "-m", "Spec: 2099-01-01_missing")

    result = run_push(repo, "--branch", "spec/advisory", "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2099-01-01_missing" in result.stdout + result.stderr
    assert "advisory" in (result.stdout + result.stderr).lower()


def test_push_lite_channel_mixed_footers_gate_the_slug_commits(tmp_path):
    repo, _ = _none_channel_repo(tmp_path, active_package=False)
    git(repo, "checkout", "-B", "spec/mixed", "main")
    write_archived_spec(repo, TEST_SPEC_SLUG)
    (repo / "mixed.txt").write_text("mixed\n", encoding="utf-8")
    git(repo, "add", "mixed.txt", ".spec")
    git(repo, "commit", "-m", "feat: packaged work", "-m", f"Spec: {TEST_SPEC_SLUG}")
    (repo / "more.txt").write_text("more\n", encoding="utf-8")
    git(repo, "add", "more.txt")
    git(repo, "commit", "-m", "chore: trivial", "-m", "Spec: none (follow-up tweak)")

    result = run_push(repo, "--branch", "spec/mixed", "--main-branch", "main")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "changed-package attribution" in result.stdout or TEST_SPEC_SLUG in result.stdout

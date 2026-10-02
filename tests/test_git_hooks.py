import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_all_spec_packages.py"
PRE_COMMIT = ROOT / "hooks" / "pre-commit"
PRE_PUSH = ROOT / "hooks" / "pre-push"


def git(root: Path, *args: str, check: bool = True):
    env = {
        **os.environ,
        "SPEC_CHECK_ALL_SCRIPT": str(CHECKER),
        "SPEC_PYTHON": os.environ.get("SPEC_PYTHON", "python3"),
    }
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


def init_repo(root: Path) -> None:
    git(root.parent, "init", "-q", str(root))
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Spec Test")


def install_hook(repo: Path, source: Path) -> None:
    target = repo / ".git" / "hooks" / source.name
    content = source.read_text(encoding="utf-8")
    content = content.replace("__SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER__", str(CHECKER.resolve()))
    content = content.replace("__SPEC_PYTHON_PLACEHOLDER__", sys.executable)
    target.write_text(content, encoding="utf-8")
    target.chmod(0o755)


def make_incomplete_package(repo: Path) -> None:
    package = repo / ".spec" / "specs" / "2026-07-13_fix-git-hook"
    package.mkdir(parents=True)
    (package / "spec.md").write_text("# Incomplete\n", encoding="utf-8")
    (package / "tasks.md").write_text("- [ ] Task\n", encoding="utf-8")
    (package / "checklist.md").write_text("**验收结果**：待修复\n", encoding="utf-8")


def make_archived_package(repo: Path, slug: str = "2026-07-13_fix-hook-attribution") -> Path:
    package = repo / ".spec" / "specs" / "archive" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(
        """# Hook - 项目范围
## 1. 问题定义
- **项目目标**：验证 hook
- **目标用户**：维护者
- **核心价值**：归属可信
## 2. 假设与待确认
### 2.1 已确认事实
- fixture
### 2.2 关键假设
- git available
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- production
## 4. 最小实现路径
- archive
- attribute
- push
""",
        encoding="utf-8",
    )
    (package / "tasks.md").write_text("- [x] Verify hook\n  - boundary: fixture\n  - verify: push\n", encoding="utf-8")
    (package / "checklist.md").write_text(
        "- [x] Verified\n## 验收证据\n- 脚本验证：pytest\n**验收结果**：通过\n",
        encoding="utf-8",
    )
    summary = """# Hook - 完成总结
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


def test_installed_hook_ignores_runtime_python_override(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    install_hook(repo, PRE_COMMIT)
    make_incomplete_package(repo)
    (repo / "payload.txt").write_text("payload\n", encoding="utf-8")
    git(repo, "add", ".")

    fake = tmp_path / "fake.py"
    fake.write_text("raise SystemExit(0)\n", encoding="utf-8")
    # Checker overrides are an explicit supported selection mechanism, covered
    # by test_install_git_hooks.py. A rendered interpreter must remain pinned.
    env = dict(os.environ, SPEC_PYTHON=str(fake))
    env.pop("SPEC_CHECK_ALL_SCRIPT", None)
    result = subprocess.run(
        ["git", "commit", "-m", "bypass"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0


def test_pre_commit_blocks_git_alias_and_git_dir_redirection(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    install_hook(repo, PRE_COMMIT)
    make_incomplete_package(repo)
    (repo / "payload.txt").write_text("payload\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "config", "alias.x", "commit")

    alias = git(repo, "x", "-m", "bypass", check=False)
    redirected = subprocess.run(
        [
            "git",
            f"--git-dir={repo / '.git'}",
            "-c",
            f"core.worktree={repo}",
            "commit",
            "-m",
            "bypass",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "SPEC_CHECK_ALL_SCRIPT": str(CHECKER),
            "SPEC_PYTHON": os.environ.get("SPEC_PYTHON", "python3"),
        },
    )

    assert alias.returncode != 0
    assert redirected.returncode != 0
    assert "check gates not passed" in alias.stdout + alias.stderr
    assert "Spec Disk Truth" in redirected.stdout + redirected.stderr
    assert "not a git repository" in redirected.stdout + redirected.stderr
    assert git(repo, "rev-parse", "--verify", "HEAD", check=False).returncode != 0


def test_pre_push_blocks_alias_until_active_package_is_archived(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial")
    git(repo, "remote", "add", "origin", str(remote))
    install_hook(repo, PRE_PUSH)
    make_incomplete_package(repo)
    # Commit the package so touched-package detection sees it in the push diff.
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "add incomplete package")
    git(repo, "config", "alias.p", "push")

    result = git(repo, "p", "origin", "HEAD:main", check=False)

    assert result.returncode != 0
    assert "active package must be archived" in result.stdout + result.stderr
    assert git(remote, "show-ref", "--verify", "refs/heads/main", check=False).returncode != 0


def test_pre_push_blocks_push_without_spec_attribution(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial")
    git(repo, "remote", "add", "origin", str(remote))
    install_hook(repo, PRE_PUSH)

    result = git(repo, "push", "origin", "HEAD:main", check=False)

    assert result.returncode != 0
    assert "has no Spec: <slug> attribution" in result.stdout + result.stderr


def test_pre_push_accepts_archived_spec_attribution(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    repo = tmp_path / "repo"
    init_repo(repo)
    slug = "2026-07-13_fix-hook-attribution"
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    make_archived_package(repo, slug)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial", "-m", f"Spec: {slug}")
    git(repo, "remote", "add", "origin", str(remote))
    install_hook(repo, PRE_PUSH)

    result = git(repo, "push", "origin", "HEAD:main", check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_pre_push_validates_multiple_refs_independently(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main")
    git(repo, "branch", "-M", "main")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()

    refs: list[tuple[str, str]] = []
    for name, slug in (
        ("one", "2026-07-13_fix-multi-one"),
        ("two", "2026-07-13_fix-multi-two"),
    ):
        git(repo, "checkout", "-b", f"feature/{name}", "main")
        make_archived_package(repo, slug)
        (repo / f"{name}.txt").write_text(name, encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", name, "-m", f"Spec: {slug}")
        refs.append((f"refs/heads/feature/{name}", git(repo, "rev-parse", "HEAD").stdout.strip()))
        git(repo, "checkout", "main")

    input_text = "".join(f"{ref} {sha} refs/heads/{ref.rsplit('/', 1)[-1]} {base}\n" for ref, sha in refs)
    result = subprocess.run(
        ["sh", str(PRE_PUSH)],
        cwd=repo,
        input=input_text,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "SPEC_CHECK_ALL_SCRIPT": str(CHECKER),
            "SPEC_PYTHON": os.environ.get("SPEC_PYTHON", "python3"),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_pre_push_fails_closed_when_ref_enumeration_fails(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main")
    local_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    missing_sha = "1" * 40
    input_text = f"refs/heads/main {local_sha} refs/heads/main {missing_sha}\n"

    result = subprocess.run(
        ["sh", str(PRE_PUSH)],
        cwd=repo,
        input=input_text,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "SPEC_CHECK_ALL_SCRIPT": str(CHECKER),
            "SPEC_PYTHON": os.environ.get("SPEC_PYTHON", "python3"),
        },
    )

    assert result.returncode != 0
    assert "cannot diff pushed ref" in result.stderr


def _run_pre_push_hook(repo: Path, input_text: str, **extra_env):
    return subprocess.run(
        ["sh", str(PRE_PUSH)],
        cwd=repo,
        input=input_text,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "SPEC_CHECK_ALL_SCRIPT": str(CHECKER),
            "SPEC_PYTHON": os.environ.get("SPEC_PYTHON", "python3"),
            **extra_env,
        },
    )


def test_pre_push_mixed_refs_still_requires_code_only_footer(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main")
    git(repo, "branch", "-M", "main")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()

    slug = "2026-07-13_fix-mixed-ref"
    git(repo, "checkout", "-b", "feature/spec", "main")
    make_archived_package(repo, slug)
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "spec", "-m", f"Spec: {slug}")
    spec_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "-b", "feature/code", "main")
    (repo / "code.txt").write_text("code\n", encoding="utf-8")
    git(repo, "add", "code.txt")
    git(repo, "commit", "-m", "unattributed code")
    code_sha = git(repo, "rev-parse", "HEAD").stdout.strip()

    input_text = (
        f"refs/heads/feature/spec {spec_sha} refs/heads/spec {base}\n"
        f"refs/heads/feature/code {code_sha} refs/heads/code {base}\n"
    )
    result = _run_pre_push_hook(repo, input_text)

    assert result.returncode != 0
    assert "has no Spec: <slug> attribution" in result.stderr


def test_pre_push_ignores_untrusted_specs_path(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main")
    git(repo, "branch", "-M", "main")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    slug = "2026-07-13_fix-trusted-ref"
    make_archived_package(repo, slug)
    unrelated = repo / "hardware" / "specs" / "draft-a" / "spec.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("not a Development Record\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "hardware specs", "-m", f"Spec: {slug}")
    local_sha = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = _run_pre_push_hook(
        repo,
        f"refs/heads/main {local_sha} refs/heads/main {base}\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_pre_push_accepts_configured_custom_specs_root(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main")
    git(repo, "branch", "-M", "main")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    slug = "2026-07-13_fix-custom-root"
    make_archived_package(repo, slug)
    custom_parent = repo / "custom"
    custom_parent.mkdir()
    (repo / ".spec").rename(custom_parent / "spec-state")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "custom root", "-m", f"Spec: {slug}")
    local_sha = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = _run_pre_push_hook(
        repo,
        f"refs/heads/main {local_sha} refs/heads/main {base}\n",
        SPEC_SPECS_DIRS="custom/spec-state",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_pre_push_legacy_baseline_comes_from_remote_main_not_feature_tip(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main")
    git(repo, "branch", "-M", "main")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")

    git(repo, "checkout", "-b", "feature/legacy")
    slug = "2026-07-13_fix-feature-only-legacy"
    archive = make_archived_package(repo, slug)
    (archive / "completion-summary.md").write_text("# Feature-only legacy\n", encoding="utf-8")
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "feature legacy", "-m", f"Spec: {slug}")
    feature_legacy_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "push", "-u", "origin", "feature/legacy")

    install_hook(repo, PRE_PUSH)
    monkeypatch.setenv("SPEC_LEGACY_BASELINE", feature_legacy_sha)
    (repo / "next.txt").write_text("next\n", encoding="utf-8")
    git(repo, "add", "next.txt")
    git(repo, "commit", "-m", "next", "-m", f"Spec: {slug}")
    result = git(repo, "push", "origin", "feature/legacy", check=False)

    assert result.returncode != 0
    assert "not byte-identical to a baseline legacy summary" in result.stdout + result.stderr


def test_pre_push_all_mode_blocks_uncommitted_active_package(tmp_path):
    """SPEC_PUSH_GATE=all restores legacy all-packages behavior."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial")
    git(repo, "remote", "add", "origin", str(remote))
    install_hook(repo, PRE_PUSH)
    make_incomplete_package(repo)
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "incomplete active package")

    env = {
        **os.environ,
        "SPEC_CHECK_ALL_SCRIPT": str(CHECKER),
        "SPEC_PYTHON": os.environ.get("SPEC_PYTHON", "python3"),
        "SPEC_PUSH_GATE": "all",
    }
    result = subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "active package must be archived" in result.stdout + result.stderr

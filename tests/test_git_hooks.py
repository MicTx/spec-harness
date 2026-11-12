import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_all.py"
PRE_COMMIT = ROOT / "hooks" / "pre-commit"
PRE_PUSH = ROOT / "hooks" / "pre-push"
NETWORK_TIMEOUT = 300
LOCAL_TIMEOUT = 60


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


def render_pre_push_with_timeout(timeout: str) -> Path:
    target = Path(tempfile.mkdtemp(prefix="spec-pre-push-timeout-")) / "pre-push"
    content = PRE_PUSH.read_text(encoding="utf-8")
    content = content.replace("run_git 300", f"run_git {timeout}")
    content = content.replace("run_git 60", f"run_git {timeout}")
    content = content.replace("__SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER__", str(CHECKER.resolve()))
    content = content.replace("__SPEC_PYTHON_PLACEHOLDER__", sys.executable)
    target.write_text(content, encoding="utf-8")
    target.chmod(0o755)
    return target


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
    branch = git(repo, "branch", "--show-current").stdout.strip()
    (package / "spec.md").write_text(
        "# Incomplete\n<!-- spec-format: 3 -->\n"
        "## Goal\n- goal: incomplete\n- literal ask: incomplete\n- done means: verified\n"
        "## Charter\n- V1 priority: quality | charter.md\n- V2 scope: strict | charter.md\n"
        "- V3 verification: package | charter.md\n- V4 finish: commit | charter.md\n"
        "- V5 dependencies: none-new | charter.md\n- V6 compatibility: keep-public-api | charter.md\n"
        "- V7 blocked: park-and-continue | charter.md\n## Scope\n- in: fixture\n- out: unrelated\n"
        "## Assumptions\n## Forks\n## Criteria\n- C1 | Verified | task-fixture\n"
        f"## Decisions\n## Parked\n## Binding\n- branch: `{branch}`\n",
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [ ] Task\n  - id: task-fixture\n  - boundary: fixture\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )


def make_archived_package(repo: Path, slug: str = "2026-07-13_fix-hook-attribution") -> Path:
    package = repo / ".spec" / "specs" / "archive" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(
        "# Hook\n<!-- spec-format: 3 -->\n## Goal\n- goal: verify hooks\n- literal ask: hook\n- done means: verified\n"
        "## Charter\n- V1 priority: quality | charter.md\n- V2 scope: strict | charter.md\n"
        "- V3 verification: package | charter.md\n- V4 finish: commit | charter.md\n"
        "- V5 dependencies: none-new | charter.md\n- V6 compatibility: keep-public-api | charter.md\n"
        "- V7 blocked: park-and-continue | charter.md\n## Scope\n- in: fixture\n- out: unrelated\n"
        "## Assumptions\n## Forks\n## Criteria\n- C1 | Verified | task-fixture\n"
        "## Decisions\n## Parked\n## Binding\n- branch: `spec/fixture`\n",
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [x] Verify hook\n  - id: task-fixture\n  - boundary: fixture\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    (package / "runs.jsonl").write_text('{"task":"task-fixture","exit":0,"cmd":"true"}\n', encoding="utf-8")
    (package / "completion-summary.md").write_text("# Completion Summary\n- Verified fixture.\n", encoding="utf-8")
    return package


def test_push_removes_linked_worktree_for_delivery_branch_worktree_topology(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)

    repo = tmp_path / "repo"
    init_repo(repo)
    git(repo, "checkout", "-b", "main")
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main")

    slug = "2026-01-01_cleanup-worktree"
    git(repo, "checkout", "-b", f"spec/{slug}")
    package = repo / ".spec" / "specs" / "archive" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(
        "# Worktree cleanup\n<!-- spec-format: 3 -->\n"
        "## Goal\n- goal: cleanup\n- literal ask: cleanup\n- done means: pushed\n"
        "## Charter\n- V1 priority: quality | charter.md\n- V2 scope: strict | charter.md\n"
        "- V3 verification: package | charter.md\n- V4 finish: push | charter.md\n"
        "- V5 dependencies: none-new | charter.md\n- V6 compatibility: keep-public-api | charter.md\n"
        "- V7 blocked: park-and-continue | charter.md\n## Scope\n- in: fixture\n- out: unrelated\n"
        "## Assumptions\n## Forks\n## Criteria\n- C1 | Delivered | task-fixture\n"
        "## Decisions\n## Parked\n## Binding\n- branch: `spec/2026-01-01_cleanup-worktree`\n",
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [x] Cleanup worktree\n  - id: task-fixture\n  - boundary: fixture\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "archive", "-m", f"Spec: {slug}")

    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "main", f"spec/{slug}")
    linked = tmp_path / "linked"
    git(repo, "checkout", "main")
    git(repo, "worktree", "add", str(linked), f"spec/{slug}")

    result = push.deliver(linked, slug, True)

    assert result["pushed"] is True
    assert result["worktrees"] == [{"path": str(linked), "removed": True}]
    assert not linked.exists()
    assert "warnings" not in result
    assert git(repo, "branch", "--list", f"spec/{slug}").stdout.strip() == ""
    listed = git(repo, "ls-remote", "--heads", "origin").stdout
    assert f"refs/heads/spec/{slug}" not in listed


def test_push_preflight_rejects_dirty_target_worktree_worktree_topology(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    slug = "2026-01-01_push-archive-resolution"
    repo = _spec_push_repo(tmp_path, slug)
    main_worktree = tmp_path / "main-worktree"
    git(repo, "worktree", "add", str(main_worktree), "main")
    (main_worktree / "dirty.txt").write_text("keep me\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"(?s)target worktree is not clean:.*dirty\.txt"):
        push.preflight(repo, slug, _preflight_spec(slug), ["app.py"])


def test_push_preflight_rejects_duplicate_delivery_worktrees_worktree_topology(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    slug = "2026-01-01_push-archive-resolution"
    repo = _spec_push_repo(tmp_path, slug)
    branch_ref = f"refs/heads/spec/{slug}"
    monkeypatch.setattr(
        push,
        "_worktrees",
        lambda _root: [
            {"path": repo, "branch": branch_ref},
            {"path": tmp_path / "duplicate", "branch": branch_ref},
            {"path": tmp_path / "main-worktree", "branch": "refs/heads/main"},
        ],
    )

    with pytest.raises(ValueError, match="multiple worktrees are bound to delivery branch"):
        push.preflight(repo, slug, _preflight_spec(slug), ["app.py"])


def test_deliver_merge_conflict_is_recoverable_in_linked_target_worktree_topology(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    slug = "2026-01-01_push-archive-resolution"
    repo = _spec_push_repo(tmp_path, slug)
    (repo / "README.md").write_text("spec branch\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "spec readme")
    git(repo, "checkout", "main")
    (repo / "README.md").write_text("main changed\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main readme")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", f"spec/{slug}")
    main_worktree = tmp_path / "main-worktree"
    git(repo, "worktree", "add", str(main_worktree), "main")

    with pytest.raises(subprocess.CalledProcessError):
        push.deliver(repo, slug, True)

    assert git(repo, "branch", "--show-current").stdout.strip() == f"spec/{slug}"
    assert git(main_worktree, "branch", "--show-current").stdout.strip() == "main"
    assert "UU" not in git(main_worktree, "status", "--porcelain").stdout


def test_push_keeps_dirty_linked_worktree_and_reports_cleanup_warning(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)

    repo = tmp_path / "repo"
    init_repo(repo)
    git(repo, "checkout", "-b", "main")
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main")

    slug = "2026-01-01_cleanup-worktree"
    git(repo, "checkout", "-b", f"spec/{slug}")
    package = repo / ".spec" / "specs" / "archive" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(
        "# Worktree cleanup\n<!-- spec-format: 3 -->\n"
        "## Goal\n- goal: cleanup\n- literal ask: cleanup\n- done means: pushed\n"
        "## Charter\n- V1 priority: quality | charter.md\n- V2 scope: strict | charter.md\n"
        "- V3 verification: package | charter.md\n- V4 finish: push | charter.md\n"
        "- V5 dependencies: none-new | charter.md\n- V6 compatibility: keep-public-api | charter.md\n"
        "- V7 blocked: park-and-continue | charter.md\n## Scope\n- in: fixture\n- out: unrelated\n"
        "## Assumptions\n## Forks\n## Criteria\n- C1 | Delivered | task-fixture\n"
        "## Decisions\n## Parked\n## Binding\n- branch: `spec/2026-01-01_cleanup-worktree`\n",
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [x] Cleanup worktree\n  - id: task-fixture\n  - boundary: fixture\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "archive", "-m", f"Spec: {slug}")

    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "main", f"spec/{slug}")
    linked = tmp_path / "linked"
    git(repo, "checkout", "main")
    git(repo, "worktree", "add", str(linked), f"spec/{slug}")
    (linked / "dirty.txt").write_text("keep me\n", encoding="utf-8")

    result = push._remove_linked_worktrees(repo, f"spec/{slug}")

    assert result == [{"path": str(linked), "removed": False, "warning": "worktree has uncommitted changes"}]
    assert linked.exists()
    assert git(repo, "branch", "--list", f"spec/{slug}").stdout.strip().lstrip("+ ") == f"spec/{slug}"
    listed = git(repo, "ls-remote", "--heads", "origin").stdout
    assert f"refs/heads/spec/{slug}" in listed


def _spec_push_repo(tmp_path: Path, slug: str = "2026-01-01_push-archive-resolution") -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)

    repo = tmp_path / "repo"
    init_repo(repo)
    git(repo, "checkout", "-b", "main")
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main")

    git(repo, "checkout", "-b", f"spec/{slug}")
    package = repo / ".spec" / "specs" / "archive" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(
        "# Push archive resolution\n<!-- spec-format: 3 -->\n"
        "## Goal\n- goal: push\n- literal ask: push\n- done means: pushed\n"
        "## Charter\n- V1 priority: quality | charter.md\n- V2 scope: strict | charter.md\n"
        "- V3 verification: package | charter.md\n- V4 finish: push | charter.md\n"
        "- V5 dependencies: none-new | charter.md\n- V6 compatibility: keep-public-api | charter.md\n"
        "- V7 blocked: park-and-continue | charter.md\n## Scope\n- in: fixture\n- out: unrelated\n"
        "## Assumptions\n## Forks\n## Criteria\n- C1 | Delivered | task-fixture\n"
        f"## Decisions\n## Parked\n## Binding\n- branch: `spec/{slug}`\n",
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [x] Push\n  - id: task-fixture\n  - boundary: fixture\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "archive", "-m", f"Spec: {slug}")

    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "main", f"spec/{slug}")
    return repo


def test_spec_push_resolves_archived_package_by_branch(tmp_path):
    slug = "2026-01-01_push-archive-resolution"
    repo = _spec_push_repo(tmp_path, slug)

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "spec.py"), "--root", str(repo), "push"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["pushed"] is True
    assert git(repo, "branch", "--show-current").stdout.strip() == "main"
    assert git(repo, "branch", "--list", f"spec/{slug}").stdout.strip() == ""
    listed = git(repo, "ls-remote", "--heads", "origin").stdout
    assert f"refs/heads/spec/{slug}" not in listed


def test_spec_push_reports_no_archived_package_on_main(tmp_path):
    slug = "2026-01-01_push-archive-resolution"
    repo = _spec_push_repo(tmp_path, slug)
    git(repo, "checkout", "-b", "spec/unbound")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "spec.py"), "--root", str(repo), "push"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "no archived package binds the current branch" in result.stderr


def test_spec_push_reports_multiple_archived_packages(tmp_path):
    slug = "2026-01-01_push-archive-resolution"
    repo = _spec_push_repo(tmp_path, slug)
    other = "2026-01-01_push-archive-resolution-2"
    package = repo / ".spec" / "specs" / "archive" / other
    package.mkdir(parents=True)
    (package / "spec.md").write_text(
        (repo / ".spec" / "specs" / "archive" / slug / "spec.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [x] Push\n  - id: task-fixture\n  - boundary: fixture\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "archive second", "-m", f"Spec: {other}")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "spec.py"), "--root", str(repo), "push"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "multiple archived packages bind the current branch" in result.stderr


def test_deliver_merge_conflict_restores_spec_branch(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    slug = "2026-01-01_push-archive-resolution"
    repo = _spec_push_repo(tmp_path, slug)
    # Diverge main so `merge --no-ff` conflicts on README.md.
    (repo / "README.md").write_text("spec branch\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "spec readme")
    git(repo, "checkout", "main")
    (repo / "README.md").write_text("main changed\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main readme")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", f"spec/{slug}")

    with pytest.raises(subprocess.CalledProcessError):
        push.deliver(repo, slug, True)

    # Recoverable state: back on the spec branch, no merge in progress.
    assert git(repo, "branch", "--show-current").stdout.strip() == f"spec/{slug}"
    assert not (repo / ".git" / "MERGE_HEAD").exists()

    # Resolving on the spec branch (merge main into spec, resolve, commit)
    # makes the retry succeed: main now merges cleanly.
    git(repo, "merge", "main", check=False)
    (repo / "README.md").write_text("merged\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "--no-edit")
    result = push.deliver(repo, slug, True)
    assert result["pushed"] is True


def test_deliver_push_failure_is_rerunnable(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    slug = "2026-01-01_push-archive-resolution"
    repo = _spec_push_repo(tmp_path, slug)
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    with pytest.raises(subprocess.CalledProcessError):
        push.deliver(repo, slug, True)

    # The merge commit landed on main but the push failed; the root worktree is
    # back on the spec branch so the call can be retried.
    assert git(repo, "branch", "--show-current").stdout.strip() == f"spec/{slug}"
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    merged = git(repo, "branch", "--contains", f"spec/{slug}", "--format=%(refname:short)").stdout.split()
    assert "main" in merged

    # A retry finds main already merged, pushes, and cleans up.
    git(repo, "remote", "set-url", "origin", str(tmp_path / "remote.git"))
    result = push.deliver(repo, slug, True)
    assert result["pushed"] is True
    assert git(repo, "branch", "--list", f"spec/{slug}").stdout.strip() == ""


def _preflight_repo(tmp_path: Path, slug: str = "2026-01-01_spec-push-preflight") -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)

    repo = tmp_path / "repo"
    init_repo(repo)
    git(repo, "checkout", "-b", "main")
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main")
    git(repo, "checkout", "-b", f"spec/{slug}")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "main")
    return repo


def _preflight_spec(slug: str) -> str:
    return f"# Push preflight\n<!-- spec-format: 3 -->\n## Binding\n- branch: `spec/{slug}`\n"


def test_push_preflight_passes_on_clean_tree(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    slug = "2026-01-01_spec-push-preflight"
    repo = _preflight_repo(tmp_path, slug)
    push.preflight(repo, slug, _preflight_spec(slug), ["app.py"])


def test_push_preflight_allows_boundary_dirt_inside_scope(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    slug = "2026-01-01_spec-push-preflight"
    repo = _preflight_repo(tmp_path, slug)
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    push.preflight(repo, slug, _preflight_spec(slug), ["app.py"])


def test_push_preflight_rejects_outside_residual_dirt(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    slug = "2026-01-01_spec-push-preflight"
    repo = _preflight_repo(tmp_path, slug)
    (repo / "unrelated.txt").write_text("wip\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside task boundary"):
        push.preflight(repo, slug, _preflight_spec(slug), ["app.py"])


def test_push_preflight_allows_baseline_dirt_inside_boundary(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    slug = "2026-01-01_spec-push-preflight"
    repo = _preflight_repo(tmp_path, slug)
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    push.preflight(repo, slug, _preflight_spec(slug), ["app.py"])


def test_push_preflight_rejects_protected_branch(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import push

    slug = "2026-01-01_spec-push-preflight"
    repo = _preflight_repo(tmp_path, slug)
    git(repo, "checkout", "main")

    with pytest.raises(ValueError, match="requires the package branch"):
        push.preflight(repo, slug, _preflight_spec(slug), ["app.py"])


def test_done_preflight_fails_before_archive_without_remote(tmp_path):
    from tests.v3_support import SLUG, cli, make_package, repo

    repo(tmp_path)
    package = make_package(tmp_path)
    spec_path = package / "spec.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace("V4 finish: commit", "V4 finish: push"),
        encoding="utf-8",
    )
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    result = cli(tmp_path, "done")

    assert result.returncode != 0
    assert "no Git remote" in result.stderr
    assert (tmp_path / ".spec/specs" / SLUG / "spec.md").is_file()
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_done_delivers_baseline_dirt_inside_boundary(tmp_path):
    from tests.v3_support import cli, make_package, repo

    repo(tmp_path)
    make_package(tmp_path)
    # Pre-Go dirt inside the task boundary simulates implementation that
    # started before plan-go recorded the baseline.
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    base = git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    result = cli(tmp_path, "done")

    assert result.returncode == 0, result.stderr
    delivered = git(tmp_path, "rev-list", f"{base}..HEAD", "--", "app.py").stdout.split()
    assert delivered, "baseline change inside the boundary was not delivered"
    residual = [
        line
        for line in git(tmp_path, "status", "--porcelain").stdout.splitlines()
        if not line.endswith(".spec/charter.md")
    ]
    assert residual == []


def test_done_preflight_rejects_baseline_dirt_outside_boundary(tmp_path):
    from tests.v3_support import SLUG, cli, make_package, repo

    repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)
    package = make_package(tmp_path)
    spec_path = package / "spec.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace("V4 finish: commit", "V4 finish: push"),
        encoding="utf-8",
    )
    git(tmp_path, "remote", "add", "origin", str(remote))
    git(tmp_path, "push", "-q", "origin", "HEAD:main")
    (tmp_path / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    result = cli(tmp_path, "done")

    assert result.returncode != 0
    assert "outside task boundary" in result.stderr
    assert "other.py" in result.stderr
    assert (tmp_path / ".spec/specs" / SLUG / "spec.md").is_file()
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_done_delivers_new_boundary_file(tmp_path):
    from tests.v3_support import cli, make_package, repo

    repo(tmp_path)
    package = make_package(tmp_path)
    spec_path = package / "spec.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace("- in: tests/**", "- in: tests/**\n- in: created.py"),
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [ ] Create value file\n  - id: task-value\n  - boundary: new:created.py\n"
        "  - verify: `test -f created.py`\n  - expect: red\n",
        encoding="utf-8",
    )
    base = git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    assert cli(tmp_path, "plan-go").returncode == 0
    # Implementation creates the declared new file inside the boundary.
    (tmp_path / "created.py").write_text("CREATED = True\n", encoding="utf-8")
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    result = cli(tmp_path, "done")

    assert result.returncode == 0, result.stderr
    delivered = git(tmp_path, "rev-list", f"{base}..HEAD", "--", "created.py").stdout.split()
    assert delivered, "created boundary file was not delivered"


def test_done_skips_untracked_ignored_memory_files(tmp_path):
    from tests.v3_support import cli, make_package, repo

    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    result = cli(tmp_path, "done")

    assert result.returncode == 0, result.stderr
    # charter.md/lessons.md are ignored in the fixture like in the real repo:
    # they must not be staged or committed even though they are listed for add.
    committed = git(tmp_path, "show", "--name-only", "--format=", "HEAD").stdout
    assert ".spec/charter.md" not in committed
    assert ".spec/lessons.md" not in committed


def archive_commit_fixture(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    init_repo(repo)
    git(repo, "checkout", "-b", "spec/2026-07-13_fix-git-hook")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")
    install_hook(repo, PRE_COMMIT)
    make_incomplete_package(repo)
    slug = "2026-07-13_fix-hook-attribution"
    make_archived_package(repo, slug)
    return repo, slug


def test_archive_commit_checks_its_archive_not_unrelated_in_progress_work(tmp_path):
    repo, slug = archive_commit_fixture(tmp_path)
    git(repo, "add", ".spec")

    result = git(repo, "commit", "-m", "archive", "-m", f"Spec: {slug}", check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "show", "--format=", "--name-only", "HEAD").stdout.count(slug) >= 1


@pytest.mark.parametrize("invalid", ["archive", "active"])
def test_archive_commit_still_rejects_invalid_archive_or_active_structure(tmp_path, invalid):
    repo, slug = archive_commit_fixture(tmp_path)
    original_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    if invalid == "archive":
        (repo / ".spec/specs/archive" / slug / "completion-summary.md").write_text("", encoding="utf-8")
    else:
        (repo / ".spec/specs/2026-07-13_fix-git-hook/tasks.md").unlink()
    git(repo, "add", ".spec")

    result = git(repo, "commit", "-m", "archive", "-m", f"Spec: {slug}", check=False)

    assert result.returncode != 0
    assert git(repo, "rev-parse", "--verify", "HEAD").stdout.strip() == original_head


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
    assert "result: blocked" in alias.stdout + alias.stderr
    assert "Spec Disk Truth" in redirected.stdout + redirected.stderr
    assert "diff" in redirected.stdout + redirected.stderr
    assert git(repo, "rev-parse", "--verify", "HEAD", check=False).returncode != 0


def test_pre_commit_checks_git_commit_only_transaction_index(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    slug = "2026-07-13_fix-git-hook"
    package = make_archived_package(repo, slug)
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "baseline")
    install_hook(repo, PRE_COMMIT)
    tasks = package / "tasks.md"
    tasks.write_text(tasks.read_text(encoding="utf-8").replace("[x]", "[ ]"), encoding="utf-8")

    result = git(repo, "commit", "--only", "-m", "broken archive", "--", str(tasks), check=False)

    assert result.returncode != 0
    assert "result: blocked" in result.stdout + result.stderr
    status = git(repo, "status", "--short").stdout
    assert " M .spec/specs/archive/2026-07-13_fix-git-hook/tasks.md" in status


def test_pre_push_blocks_incomplete_active_package(tmp_path):
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
    assert "result: blocked" in result.stdout + result.stderr
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


def test_pre_push_git_calls_are_bounded():
    source = PRE_PUSH.read_text(encoding="utf-8")
    assert source.count("run_git 300") >= 3
    assert source.count("run_git 60") >= 7
    direct_git_calls = [
        line
        for line in source.splitlines()
        if not line.startswith("repo=$(git rev-parse")
        if re.search(r"(?<![#'\"])git\s+(ls-remote|fetch|log|rev-list|diff|show|merge-base|update-ref|rev-parse)", line)
    ]
    assert direct_git_calls == []


def test_pre_push_git_timeout_fails_closed(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial")
    git(repo, "branch", "-M", "main")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "payload.txt").write_text("payload\n", encoding="utf-8")
    git(repo, "add", "payload.txt")
    git(repo, "commit", "-m", "payload")
    tip = git(repo, "rev-parse", "HEAD").stdout.strip()
    hook = render_pre_push_with_timeout("0")

    result = subprocess.run(
        ["sh", str(hook)],
        cwd=repo,
        input=f"refs/heads/main {tip} refs/heads/main {base}\n",
        capture_output=True,
        text=True,
        timeout=5,
        env={
            **os.environ,
            "SPEC_CHECK_ALL_SCRIPT": str(CHECKER),
            "SPEC_PYTHON": os.environ.get("SPEC_PYTHON", "python3"),
        },
    )

    assert result.returncode != 0
    assert "timed out after" in result.stderr


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


def test_pre_push_does_not_regrade_archives_outside_remote_diff(tmp_path):
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
    git(repo, "push", "-u", "origin", "feature/legacy")

    install_hook(repo, PRE_PUSH)
    (repo / "next.txt").write_text("next\n", encoding="utf-8")
    git(repo, "add", "next.txt")
    git(repo, "commit", "-m", "next", "-m", f"Spec: {slug}")
    result = git(repo, "push", "origin", "feature/legacy", check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert git(remote, "rev-parse", "refs/heads/feature/legacy").stdout == git(repo, "rev-parse", "HEAD").stdout


def test_pre_push_checks_archives_changed_earlier_in_remote_window(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("main\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "main")
    git(repo, "branch", "-M", "main")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    slug = "2026-07-13_fix-earlier-window"
    archive = make_archived_package(repo, slug)
    (archive / "completion-summary.md").unlink()
    git(repo, "add", ".spec")
    git(repo, "commit", "-m", "archive", "-m", f"Spec: {slug}")
    (repo / "later.txt").write_text("later\n", encoding="utf-8")
    git(repo, "add", "later.txt")
    git(repo, "commit", "-m", "later", "-m", f"Spec: {slug}")
    tip = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = _run_pre_push_hook(repo, f"refs/heads/main {tip} refs/heads/main {base}\n")

    assert result.returncode != 0
    assert "result: blocked" in result.stdout + result.stderr


def test_pre_push_all_mode_blocks_incomplete_active_package(tmp_path):
    """The all-packages channel checks every active package in the pushed snapshot."""
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
    assert "result: blocked" in result.stdout + result.stderr


def test_pre_push_lite_channel_releases_none_only_push(tmp_path):
    """F11: `Spec: none (<reason>)` attributes a trivial unpackaged commit;
    a none-only push skips the package gate and lands on the remote."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial", "-m", "Spec: none (bootstrap)")
    git(repo, "remote", "add", "origin", str(remote))
    install_hook(repo, PRE_PUSH)
    (repo / "typo.txt").write_text("typo\n", encoding="utf-8")
    git(repo, "add", "typo.txt")
    git(repo, "commit", "-m", "fix: trivial typo", "-m", "Spec: none (typo fix covered by tests)")

    result = git(repo, "push", "origin", "HEAD:main", check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert git(remote, "show-ref", "--verify", "refs/heads/main", check=False).returncode == 0


@pytest.mark.parametrize(
    ("footer", "expected_message"),
    [
        ("Spec: none", "requires a non-empty reason"),
        ("Spec: none ()", "requires a non-empty reason"),
        # Reasons with parentheses are deliberately outside the footer
        # grammar: they fall through to the generic attribution rejection.
        ("Spec: none (reason with ) inside)", "attribution"),
    ],
)
def test_pre_push_rejects_malformed_none_footer(tmp_path, footer, expected_message):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial", "-m", "Spec: none (bootstrap)")
    git(repo, "remote", "add", "origin", str(remote))
    install_hook(repo, PRE_PUSH)
    (repo / "typo.txt").write_text("typo\n", encoding="utf-8")
    git(repo, "add", "typo.txt")
    git(repo, "commit", "-m", "fix: trivial typo", "-m", footer)

    result = git(repo, "push", "origin", "HEAD:main", check=False)

    assert result.returncode != 0
    assert expected_message in result.stdout + result.stderr
    assert git(remote, "show-ref", "--verify", "refs/heads/main", check=False).returncode != 0


def _spec_branch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_repo(repo)
    git(repo, "checkout", "-b", "spec/2026-07-13_fix-hook-attribution")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")
    install_hook(repo, PRE_COMMIT)
    return repo


@pytest.mark.parametrize("rel", [".spec/specs/archive/retired/2026-07-13/old-note.md", ".spec/specs/archive/README.md"])
def test_archive_commit_accepts_retired_and_plain_archive_files(tmp_path, rel):
    """organize's retired container and plain files under archive/ are not package slugs."""
    repo = _spec_branch_repo(tmp_path)
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("retired\n", encoding="utf-8")
    git(repo, "add", rel)
    result = git(repo, "commit", "-m", "chore: retire", check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_invalid_archive_slug_error_names_the_path(tmp_path):
    repo = _spec_branch_repo(tmp_path)
    target = repo / ".spec/specs/archive/Bad_Name/spec.md"
    target.parent.mkdir(parents=True)
    target.write_text("# x\n", encoding="utf-8")
    git(repo, "add", ".spec")
    result = git(repo, "commit", "-m", "archive", check=False)
    assert result.returncode != 0
    assert "Bad_Name" in result.stderr, result.stderr


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_a_slug_named_entry_directly_under_archive_still_gets_the_full_gate(tmp_path, kind):
    """A direct archive child named like a package claims to be one: it is gated, not waved through as content."""
    repo = _spec_branch_repo(tmp_path)
    original_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    archive = repo / ".spec/specs/archive"
    archive.mkdir(parents=True)
    entry = archive / "2026-07-13_fix-entry"
    if kind == "file":
        entry.write_text("not a package\n", encoding="utf-8")
    else:
        elsewhere = repo / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "spec.md").write_text("# not a package\n", encoding="utf-8")
        entry.symlink_to("../../../elsewhere")
    git(repo, "add", "-A")
    result = git(repo, "commit", "-m", "archive entry", check=False)
    assert result.returncode != 0, result.stdout + result.stderr
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == original_head

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from init_spec_package import (
    LOCAL_BRANCH_NOTE,
    GitPreflightError,
    create_integration_branch_from_main,
    render_template,
)
from spec_package_support import load_reference_templates, write_text


def test_render_template():
    result = render_template("# [项目名称] - 标题\ncontent", "My Project")
    assert "My Project" in result
    assert "[项目名称]" not in result


def test_init_rejects_integration_branch_already_bound_to_active_package(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    owner = "2026-09-05_fix-owner"
    package = repo / ".spec" / "specs" / owner
    package.mkdir(parents=True)
    shared = "spec/shared-branch"
    (package / "spec.md").write_text(f"# Owner\n- Git integration branch：`{shared}`\n", encoding="utf-8")
    (package / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
    (package / "checklist.md").write_text("# Checklist\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add owner"], cwd=repo, check=True)
    with pytest.raises(GitPreflightError, match="already bound"):
        create_integration_branch_from_main(
            repo,
            "2026-09-05_fix-second",
            "main",
            shared,
        )
    assert git(repo, "branch", "--show-current") == "main"


def test_render_template_records_integration_branch():
    result = render_template(
        "Git integration branch：`spec/YYYY-MM-DD_<slug>` 或适用外理由",
        "My Project",
        "spec/2026-08-07_add-new-branch-preflight",
    )
    assert "`spec/2026-08-07_add-new-branch-preflight`" in result
    assert "spec/YYYY-MM-DD_<slug>" not in result


def test_render_template_records_local_branch_note():
    result = render_template(
        "Git integration branch：`spec/YYYY-MM-DD_<slug>` 或适用外理由",
        "My Project",
        "spec/2026-08-28_fix-spec-logic-integrity",
        LOCAL_BRANCH_NOTE,
    )
    assert "`spec/2026-08-28_fix-spec-logic-integrity`" in result
    assert "本地模式" in result
    assert "从本地主分支创建" in result


def test_render_template_no_placeholder():
    result = render_template("no placeholder here", "Title")
    assert result == "no placeholder here"


def test_init_end_to_end():
    skill_root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir)
        slug = "e2e-init-test"
        pkg_dir = target / ".spec" / "specs" / slug

        templates = load_reference_templates(skill_root)
        pkg_dir.mkdir(parents=True, exist_ok=True)
        for filename, template in templates.items():
            write_text(pkg_dir / filename, render_template(template, "E2E Test"))

        assert (pkg_dir / "spec.md").exists()
        assert (pkg_dir / "tasks.md").exists()
        # v2 packages ship no checklist.md (F11): the template carries only
        # the two init fences.
        assert not (pkg_dir / "checklist.md").exists()
        assert "checklist.md" not in templates

        spec_content = (pkg_dir / "spec.md").read_text(encoding="utf-8")
        assert "E2E Test" in spec_content
        assert "[项目名称]" not in spec_content
        assert "### 5.1 验证策略" in spec_content
        assert "范围级别：package" in spec_content
        assert "evidence: runs" in spec_content
        assert "- [ ] 标准 A 达成 -> verify: task_0001" in spec_content


ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "scripts" / "init_spec_package.py"


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def init_git_repo(path: Path) -> None:
    git(path.parent, "init", "-q", str(path))
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Spec Test")
    (path / "README.md").write_text("main\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")
    git(path, "branch", "-M", "main")


def test_new_preflight_switches_to_main_and_creates_spec_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    git(repo, "checkout", "-b", "feature/existing-work")

    creation = create_integration_branch_from_main(
        repo,
        "2026-08-07_add-new-branch-preflight",
        "main",
        None,
    )

    assert creation.branch == "spec/2026-08-07_add-new-branch-preflight"
    assert creation.mode == "synced"
    assert git(repo, "branch", "--show-current") == creation.branch
    assert git(repo, "merge-base", "main", creation.branch) == git(repo, "rev-parse", "main")


def test_new_preflight_rejects_dirty_tree_before_branching(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    git(repo, "checkout", "-b", "feature/existing-work")
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    try:
        create_integration_branch_from_main(repo, "2026-08-07_add-new-branch-preflight", "main", None)
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover - explicit assertion path
        raise AssertionError("dirty tree should block /spec:new branch preflight")

    assert "working tree is not clean" in message
    assert git(repo, "branch", "--show-current") == "feature/existing-work"
    assert "spec/2026-08-07_add-new-branch-preflight" not in git(repo, "branch", "--list")


def add_unreachable_upstream(repo: Path) -> None:
    remote = repo.parent / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    git(repo, "remote", "set-url", "origin", str(repo.parent / "missing-remote.git"))


def test_new_preflight_uses_local_main_when_upstream_fetch_is_unreachable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    add_unreachable_upstream(repo)
    git(repo, "checkout", "-b", "feature/existing-work")

    creation = create_integration_branch_from_main(
        repo,
        "2026-08-28_fix-spec-logic-integrity",
        "main",
        None,
    )

    assert creation.branch == "spec/2026-08-28_fix-spec-logic-integrity"
    assert creation.mode == "local"
    assert "本地模式" in creation.note
    assert git(repo, "branch", "--show-current") == creation.branch
    assert git(repo, "merge-base", "main", creation.branch) == git(repo, "rev-parse", "main")


def test_new_preflight_still_fails_when_fetch_is_permission_denied(monkeypatch, tmp_path):
    import init_spec_package

    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    add_unreachable_upstream(repo)

    original_git = init_spec_package.git

    def fake_git(root, *args, check=True, disable_hooks=False):
        if args[:1] == ("fetch",):
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=1,
                stdout="",
                stderr="fatal: could not read from remote repository.\npermission denied\n",
            )
        return original_git(root, *args, check=check, disable_hooks=disable_hooks)

    monkeypatch.setattr(init_spec_package, "git", fake_git)

    try:
        create_integration_branch_from_main(
            repo,
            "2026-08-28_fix-spec-logic-integrity",
            "main",
            None,
        )
    except GitPreflightError as exc:
        message = str(exc)
    else:  # pragma: no cover - explicit assertion path
        raise AssertionError("permission denied fetch should block /spec:new")

    assert "failed to fetch upstream for main" in message
    assert "permission denied" in message
    assert git(repo, "branch", "--show-current") == "main"
    assert "spec/2026-08-28_fix-spec-logic-integrity" not in git(repo, "branch", "--list")


def test_new_preflight_still_fails_when_fetch_says_repository_not_found(monkeypatch, tmp_path):
    import init_spec_package

    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    add_unreachable_upstream(repo)

    original_git = init_spec_package.git

    def fake_git(root, *args, check=True, disable_hooks=False):
        if args[:1] == ("fetch",):
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=1,
                stdout="",
                stderr="ERROR: repository not found\nfatal: Could not read from remote repository.\n",
            )
        return original_git(root, *args, check=check, disable_hooks=disable_hooks)

    monkeypatch.setattr(init_spec_package, "git", fake_git)

    try:
        create_integration_branch_from_main(
            repo,
            "2026-08-28_fix-spec-logic-integrity",
            "main",
            None,
        )
    except GitPreflightError as exc:
        message = str(exc)
    else:  # pragma: no cover - explicit assertion path
        raise AssertionError("repository not found should block /spec:new")

    assert "failed to fetch upstream for main" in message
    assert "repository not found" in message.lower()
    assert "spec/2026-08-28_fix-spec-logic-integrity" not in git(repo, "branch", "--list")


def test_init_cli_records_local_mode_when_upstream_is_unreachable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    add_unreachable_upstream(repo)

    result = subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--root",
            str(repo),
            "--slug",
            "2026-08-28_fix-spec-logic-integrity",
            "--title",
            "本地模式建分支",
            "--disable-git-hooks",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "branch: spec/2026-08-28_fix-spec-logic-integrity" in result.stdout
    assert "branch-mode: local" in result.stdout
    assert git(repo, "branch", "--show-current") == "spec/2026-08-28_fix-spec-logic-integrity"
    spec_text = (repo / ".spec" / "specs" / "2026-08-28_fix-spec-logic-integrity" / "spec.md").read_text(
        encoding="utf-8"
    )
    assert "`spec/2026-08-28_fix-spec-logic-integrity`" in spec_text
    assert "本地模式" in spec_text


# --- AUD-07: clean-tree exclusion for active package documents ---


def run_init(root: Path, slug: str):
    return subprocess.run(
        [sys.executable, str(INIT), "--slug", slug, "--title", "Probe", "--root", str(root)],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_ensure_clean_tree_carries_active_package_documents(tmp_path):
    from init_spec_package import GitPreflightError, ensure_clean_tree

    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    package = repo / ".spec" / "specs" / "2026-10-12_first-probe"
    package.mkdir(parents=True)
    for filename in ("spec.md", "tasks.md", "checklist.md"):
        (package / filename).write_text("draft\n", encoding="utf-8")

    # Only the active package's own documents are dirty: carried, not blocking.
    ensure_clean_tree(repo)

    # A rename whose source is a business file still blocks (both sides of a
    # rename must be package documents to be carried).
    (repo / "business.txt").write_text("payload\n", encoding="utf-8")
    git(repo, "add", "business.txt")
    git(
        repo,
        "-c",
        "user.email=spec@example.com",
        "-c",
        "user.name=Spec Test",
        "commit",
        "-m",
        "business",
    )
    git(repo, "mv", "business.txt", ".spec/specs/2026-10-12_first-probe/business.txt")
    try:
        ensure_clean_tree(repo)
    except GitPreflightError as exc:
        assert "working tree is not clean" in str(exc)
        assert "business.txt" in str(exc)
    else:
        raise AssertionError("rename out of a business path must stay blocking")


def test_ensure_clean_tree_still_rejects_archive_dirt(tmp_path):
    from init_spec_package import GitPreflightError, ensure_clean_tree

    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    active = repo / ".spec" / "specs" / "2026-10-12_first-probe"
    active.mkdir(parents=True)
    (active / "spec.md").write_text("draft\n", encoding="utf-8")
    archive = repo / ".spec" / "specs" / "archive" / "2026-01-01_legacy-record"
    archive.mkdir(parents=True)
    (archive / "completion-summary.md").write_text("summary\n", encoding="utf-8")

    try:
        ensure_clean_tree(repo)
    except GitPreflightError as exc:
        assert "archive/2026-01-01_legacy-record" in str(exc)
    else:
        raise AssertionError("archive dirt must stay blocking")


def test_replay_exp04a_second_package_with_uncommitted_docs(tmp_path):
    """EXP-04(a): a previous package's uncommitted documents must not block
    /spec:new from creating the next package."""
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    first = run_init(repo, "2026-10-12_first-probe")
    assert first.returncode == 0, first.stdout + first.stderr
    assert git(repo, "branch", "--show-current") == "spec/2026-10-12_first-probe"
    assert "install the Spec git hooks" in first.stdout

    second = run_init(repo, "2026-10-12_second-probe")
    assert second.returncode == 0, second.stdout + second.stderr
    assert git(repo, "branch", "--show-current") == "spec/2026-10-12_second-probe"
    assert (repo / ".spec" / "specs" / "2026-10-12_second-probe" / "spec.md").is_file()
    assert (repo / ".spec" / "specs" / "2026-10-12_first-probe" / "spec.md").is_file()


def test_replay_exp04a_business_dirt_still_blocks_second_package(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    assert run_init(repo, "2026-10-12_first-probe").returncode == 0
    git(repo, "checkout", "-q", "main")
    (repo / "unrelated.txt").write_text("dirty\n", encoding="utf-8")

    rejected = run_init(repo, "2026-10-12_second-probe")

    assert rejected.returncode != 0
    assert "working tree is not clean" in rejected.stderr
    assert "unrelated.txt" in rejected.stderr
    assert "spec/2026-10-12_second-probe" not in git(repo, "branch", "--list")

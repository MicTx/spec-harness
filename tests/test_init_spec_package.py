import argparse
import json
import os
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
    result = render_template("# [Project Name] - Scope\ncontent", "My Project")
    assert "My Project" in result
    assert "[Project Name]" not in result


def test_render_template_keeps_legacy_chinese_placeholder():
    result = render_template("# [项目名称] - 标题\ncontent", "My Project")
    assert "My Project" in result
    assert "[项目名称]" not in result


def test_init_help_excludes_unreferenced_git_overrides(tmp_path):
    result = subprocess.run([sys.executable, str(INIT), "--help"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0
    for flag in (
        "--root",
        "--slug",
        "--title",
        "--workflow",
        "--force",
        "--branch",
        "--specs-dir",
        "--adopt",
        "--confirm-adopt",
    ):
        assert flag in result.stdout
    for flag in ("--skip-git-branch", "--main-branch", "--disable-git-hooks"):
        assert flag not in result.stdout


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
        "- Git integration branch: `spec/YYYY-MM-DD_<slug>` or not applicable",
        "My Project",
        "spec/2026-08-07_add-public-example-04",
    )
    assert "`spec/2026-08-07_add-public-example-04`" in result
    assert "spec/YYYY-MM-DD_<slug>" not in result


def test_render_template_records_integration_branch_for_legacy_template():
    result = render_template(
        "Git integration branch：`spec/YYYY-MM-DD_<slug>` 或适用外理由",
        "My Project",
        "spec/2026-08-07_add-public-example-04",
    )
    assert "`spec/2026-08-07_add-public-example-04`" in result
    assert "spec/YYYY-MM-DD_<slug>" not in result


def test_render_template_records_local_branch_note():
    result = render_template(
        "- Git integration branch: `spec/YYYY-MM-DD_<slug>` or not applicable",
        "My Project",
        "spec/2026-08-28_fix-public-example-06",
        LOCAL_BRANCH_NOTE,
    )
    assert "`spec/2026-08-28_fix-public-example-06`" in result
    assert "local mode" in result
    assert "created from local main" in result


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
        assert "[Project Name]" not in spec_content
        assert "### 5.1 Verification Strategy" in spec_content
        assert "scope level: package" in spec_content
        assert "evidence: runs" in spec_content
        assert "- [ ] Criterion A met -> verify: task_0001" in spec_content


def test_workflow_selects_the_spec_template():
    skill_root = Path(__file__).resolve().parent.parent
    lite = load_reference_templates(skill_root)
    full = load_reference_templates(skill_root, "full")
    assert set(lite) == set(full) == {"spec.md", "tasks.md"}
    assert "- Workflow: LITE" in lite["spec.md"] and "### 4.1 Option Ladder" not in lite["spec.md"]
    assert "- Workflow: FULL" in full["spec.md"] and "### 4.1 Option Ladder" in full["spec.md"]
    assert lite["tasks.md"] == full["tasks.md"]


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
        "2026-08-07_add-public-example-04",
        "main",
        None,
    )

    assert creation.branch == "spec/2026-08-07_add-public-example-04"
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
        create_integration_branch_from_main(repo, "2026-08-07_add-public-example-04", "main", None)
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover - explicit assertion path
        raise AssertionError("dirty tree should block /spec:new branch preflight")

    assert "working tree is not clean" in message
    assert git(repo, "branch", "--show-current") == "feature/existing-work"
    assert "spec/2026-08-07_add-public-example-04" not in git(repo, "branch", "--list")


def add_unreachable_upstream(repo: Path) -> None:
    remote = repo.parent / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    git(repo, "remote", "set-url", "origin", str(repo.parent / "missing-remote.git"))


def test_new_preflight_fast_forwards_main_before_creating_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-qu", "origin", "main")

    upstream = tmp_path / "upstream"
    git(tmp_path, "clone", "-q", "-b", "main", str(remote), str(upstream))
    git(upstream, "config", "user.email", "test@example.com")
    git(upstream, "config", "user.name", "Spec Test")
    (upstream / "README.md").write_text("upstream change\n", encoding="utf-8")
    git(upstream, "add", "README.md")
    git(upstream, "commit", "-qm", "advance upstream")
    git(upstream, "push", "-q", "origin", "main")
    remote_head = git(upstream, "rev-parse", "HEAD")
    git(repo, "checkout", "-qb", "feature/existing-work")

    creation = create_integration_branch_from_main(repo, "2026-11-09_synced", "main", None)

    assert creation.mode == "synced"
    assert git(repo, "branch", "--show-current") == "spec/2026-11-09_synced"
    assert git(repo, "rev-parse", "main") == remote_head
    assert git(repo, "rev-parse", "HEAD") == remote_head
    assert (repo / "README.md").read_text(encoding="utf-8") == "upstream change\n"


def test_new_preflight_rejects_divergent_main_and_restores_original_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-qu", "origin", "main")
    upstream = tmp_path / "upstream"
    git(tmp_path, "clone", "-q", "-b", "main", str(remote), str(upstream))
    git(upstream, "config", "user.email", "test@example.com")
    git(upstream, "config", "user.name", "Spec Test")
    (upstream / "remote.txt").write_text("upstream\n", encoding="utf-8")
    git(upstream, "add", "remote.txt")
    git(upstream, "commit", "-qm", "remote change")
    git(upstream, "push", "-q", "origin", "main")
    (repo / "local.txt").write_text("local\n", encoding="utf-8")
    git(repo, "add", "local.txt")
    git(repo, "commit", "-qm", "local change")
    local_main = git(repo, "rev-parse", "main")
    git(repo, "checkout", "-qb", "feature/existing-work")
    (repo / ".spec").mkdir()
    (repo / ".spec" / "state.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(GitPreflightError, match="cannot fast-forward"):
        create_integration_branch_from_main(repo, "2026-11-09_divergent", "main", None)

    assert git(repo, "branch", "--show-current") == "feature/existing-work"
    assert git(repo, "rev-parse", "main") == local_main
    assert "spec/2026-11-09_divergent" not in git(repo, "branch", "--list")
    assert (repo / ".spec" / "state.json").read_text(encoding="utf-8") == "{}\n"


def test_new_preflight_uses_local_main_when_upstream_fetch_is_unreachable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    add_unreachable_upstream(repo)
    git(repo, "checkout", "-b", "feature/existing-work")

    creation = create_integration_branch_from_main(
        repo,
        "2026-08-28_fix-public-example-06",
        "main",
        None,
    )

    assert creation.branch == "spec/2026-08-28_fix-public-example-06"
    assert creation.mode == "local"
    assert "local mode" in creation.note
    assert git(repo, "branch", "--show-current") == creation.branch
    assert git(repo, "merge-base", "main", creation.branch) == git(repo, "rev-parse", "main")


def test_new_preflight_still_fails_when_fetch_is_permission_denied(monkeypatch, tmp_path):
    import init_spec_package

    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    add_unreachable_upstream(repo)

    original_git = init_spec_package.gitx.git

    def fake_git(root, *args, check=True, **options):
        if args[:1] == ("fetch",):
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=1,
                stdout="",
                stderr="fatal: could not read from remote repository.\npermission denied\n",
            )
        return original_git(root, *args, check=check, **options)

    monkeypatch.setattr(init_spec_package.gitx, "git", fake_git)

    try:
        create_integration_branch_from_main(
            repo,
            "2026-08-28_fix-public-example-06",
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
    assert "spec/2026-08-28_fix-public-example-06" not in git(repo, "branch", "--list")


def test_new_preflight_still_fails_when_fetch_says_repository_not_found(monkeypatch, tmp_path):
    import init_spec_package

    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    add_unreachable_upstream(repo)

    original_git = init_spec_package.gitx.git

    def fake_git(root, *args, check=True, **options):
        if args[:1] == ("fetch",):
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=1,
                stdout="",
                stderr="ERROR: repository not found\nfatal: Could not read from remote repository.\n",
            )
        return original_git(root, *args, check=check, **options)

    monkeypatch.setattr(init_spec_package.gitx, "git", fake_git)

    try:
        create_integration_branch_from_main(
            repo,
            "2026-08-28_fix-public-example-06",
            "main",
            None,
        )
    except GitPreflightError as exc:
        message = str(exc)
    else:  # pragma: no cover - explicit assertion path
        raise AssertionError("repository not found should block /spec:new")

    assert "failed to fetch upstream for main" in message
    assert "repository not found" in message.lower()
    assert "spec/2026-08-28_fix-public-example-06" not in git(repo, "branch", "--list")


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
            "2026-08-28_fix-public-example-06",
            "--title",
            "本地模式建分支",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "branch: spec/2026-08-28_fix-public-example-06" in result.stdout
    assert "branch-mode: local" in result.stdout
    assert git(repo, "branch", "--show-current") == "spec/2026-08-28_fix-public-example-06"
    spec_text = (repo / ".spec" / "specs" / "2026-08-28_fix-public-example-06" / "spec.md").read_text(encoding="utf-8")
    assert "`spec/2026-08-28_fix-public-example-06`" in spec_text
    assert "local mode" in spec_text


# --- AUD-07: clean-tree exclusion for active package documents ---


def run_init(root: Path, slug: str):
    return subprocess.run(
        [sys.executable, str(INIT), "--slug", slug, "--title", "Probe", "--root", str(root)],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_new_preflight_carries_completely_untracked_spec_state(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / ".spec" / "history").mkdir(parents=True)
    (repo / ".spec" / "history" / "state.json").write_text("{}\n", encoding="utf-8")

    result = run_init(repo, "2026-11-09_untracked-spec")

    assert result.returncode == 0, result.stderr
    assert git(repo, "branch", "--show-current") == "spec/2026-11-09_untracked-spec"
    assert (repo / ".spec" / "history" / "state.json").read_text(encoding="utf-8") == "{}\n"


def test_clean_tree_carries_spec_state_and_rejects_rename_from_business(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    archive = repo / ".spec" / "specs" / "archive" / "2026-01-01_legacy-record"
    archive.mkdir(parents=True)
    (archive / "completion-summary.md").write_text("summary\n", encoding="utf-8")
    (repo / ".spec" / "state.json").write_text("{}\n", encoding="utf-8")
    (repo / "business.txt").write_text("payload\n", encoding="utf-8")
    git(repo, "add", "business.txt")
    git(repo, "commit", "-qm", "business")

    git(repo, "mv", "business.txt", ".spec/state-renamed.txt")
    with pytest.raises(GitPreflightError, match="business.txt"):
        create_integration_branch_from_main(repo, "2026-11-09_clean-tree", "main", None)
    assert git(repo, "branch", "--show-current") == "main"


def test_new_preflight_restores_original_branch_on_fetch_error_with_spec_state(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    add_unreachable_upstream(repo)
    git(repo, "checkout", "-qb", "feature/existing-work")
    (repo / ".spec").mkdir()
    (repo / ".spec" / "state.json").write_text("{}\n", encoding="utf-8")
    # A non-transport fetch failure must leave the caller on their original branch.
    import init_spec_package

    original_git = init_spec_package.gitx.git

    def fake_git(root, *args, **options):
        if args[:1] == ("fetch",):
            return subprocess.CompletedProcess(args=["git", *args], returncode=1, stdout="", stderr="permission denied")
        return original_git(root, *args, **options)

    monkeypatch.setattr(init_spec_package.gitx, "git", fake_git)
    with pytest.raises(GitPreflightError, match="permission denied"):
        create_integration_branch_from_main(repo, "2026-11-09_fetch-error", "main", None)
    assert git(repo, "branch", "--show-current") == "feature/existing-work"
    assert (repo / ".spec" / "state.json").read_text(encoding="utf-8") == "{}\n"


@pytest.mark.parametrize("state_dir", [".trae", "custom-state"])
def test_new_preflight_rejects_non_spec_state(tmp_path, state_dir):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / state_dir).mkdir()
    (repo / state_dir / "state.json").write_text("{}\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--root",
            str(repo),
            "--slug",
            "2026-11-09_other-state",
            "--title",
            "Probe",
            "--specs-dir",
            state_dir,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "working tree is not clean" in result.stderr
    assert state_dir in result.stderr
    assert "action:" in result.stderr
    assert git(repo, "branch", "--show-current") == "main"


def test_adopt_records_dirty_status_without_touching_work_or_lock(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    git(repo, "checkout", "-qb", "spec/in-flight")
    (repo / "README.md").write_text("work in progress\n", encoding="utf-8")
    git(repo, "add", "README.md")
    (repo / "new file.txt").write_text("new work\n", encoding="utf-8")
    lock = repo / ".spec" / ".adopt.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text("do not touch\n", encoding="utf-8")
    head = git(repo, "rev-parse", "HEAD")
    slug = "2026-11-09_adopt-work"
    # Unrelated leftover state from an older implementation is never needed
    # or touched by the simplified adoption path.
    import init_spec_package

    staging = repo / ".spec" / "specs" / f".{slug}.adopt-4321987"
    staging.mkdir(parents=True)
    (staging / "keep.txt").write_text("unrelated\n", encoding="utf-8")
    before = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.rstrip("\n")
    calls = []
    original_git = init_spec_package.gitx.git

    def tracked_git(root, *git_args, **options):
        if git_args[:1] == ("status",):
            calls.append(git_args)
        return original_git(root, *git_args, **options)

    monkeypatch.setattr(init_spec_package.gitx, "git", tracked_git)
    args = argparse.Namespace(
        root=str(repo),
        confirm_adopt=True,
        teammate=["worker-1"],
        writers_paused=True,
        workflow="lite",
    )
    rc = init_spec_package.adopt_in_flight_work(args, slug, "Adopt", repo / ".spec", ROOT)

    assert rc == 0
    assert calls == [("status", "--porcelain", "--untracked-files=all")]
    package = repo / ".spec" / "specs" / slug
    baseline = json.loads((package / "adoption.json").read_text(encoding="utf-8"))
    assert baseline["branch"] == "spec/in-flight"
    assert baseline["head"] == head
    assert baseline["teammates"] == ["worker-1"]
    assert baseline["writers_paused"] is True
    assert baseline["status"] == before
    assert baseline["status"].startswith("M  README.md")
    assert (lock / "owner").read_text(encoding="utf-8") == "do not touch\n"
    assert (staging / "keep.txt").read_text(encoding="utf-8") == "unrelated\n"
    assert (repo / "README.md").read_text(encoding="utf-8") == "work in progress\n"
    assert (repo / "new file.txt").read_text(encoding="utf-8") == "new work\n"
    assert git(repo, "branch", "--show-current") == "spec/in-flight"
    assert git(repo, "rev-parse", "HEAD") == head
    assert "spec/in-flight" in (package / "spec.md").read_text(encoding="utf-8")


def test_adopt_rejects_a_second_active_package_without_modifying_it(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    git(repo, "checkout", "-qb", "spec/in-flight")
    other = repo / ".spec" / "specs" / "2026-11-09_other"
    other.mkdir(parents=True)
    (other / "spec.md").write_text("other spec\n", encoding="utf-8")
    (other / "tasks.md").write_text("other tasks\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--root",
            str(repo),
            "--slug",
            "2026-11-09_takeover",
            "--title",
            "Takeover",
            "--adopt",
            "--confirm-adopt",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ambiguous ownership" in result.stderr
    assert not (repo / ".spec" / "specs" / "2026-11-09_takeover").exists()
    assert (other / "spec.md").read_text(encoding="utf-8") == "other spec\n"
    assert (other / "tasks.md").read_text(encoding="utf-8") == "other tasks\n"


def test_adopt_rejects_other_package_bound_to_current_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    git(repo, "checkout", "-qb", "spec/in-flight")
    other = repo / ".spec" / "specs" / "2026-11-09_other"
    other.mkdir(parents=True)
    (other / "spec.md").write_text("- Git integration branch：`spec/in-flight`\n", encoding="utf-8")
    (other / "tasks.md").write_text("other tasks\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--root",
            str(repo),
            "--slug",
            "2026-11-09_takeover",
            "--title",
            "Takeover",
            "--adopt",
            "--confirm-adopt",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "already bound" in result.stderr
    assert not (repo / ".spec" / "specs" / "2026-11-09_takeover").exists()


@pytest.mark.parametrize("branch", ["main", "develop", "release/stable"])
def test_adopt_rejects_protected_branch_without_writing_package(tmp_path, branch):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    if branch != "main":
        git(repo, "checkout", "-qb", branch)
    result = subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--root",
            str(repo),
            "--slug",
            "2026-11-09_adopt-work",
            "--title",
            "Adopt",
            "--adopt",
            "--confirm-adopt",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "protected" in result.stderr
    assert not (repo / ".spec" / "specs" / "2026-11-09_adopt-work").exists()
    assert git(repo, "branch", "--show-current") == branch


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


@pytest.mark.parametrize("branch", ["main", "develop", "release/stable"])
def test_init_rejects_protected_integration_branch(tmp_path, branch):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    result = subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--root",
            str(repo),
            "--slug",
            "2026-11-09_protected",
            "--title",
            "Probe",
            "--branch",
            branch,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "protected" in result.stderr
    assert git(repo, "branch", "--show-current") == "main"
    assert not (repo / ".spec" / "specs" / "2026-11-09_protected").exists()


def test_init_rejects_existing_integration_branch_before_checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    git(repo, "branch", "spec/existing")
    git(repo, "checkout", "-qb", "feature/work")
    result = subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--root",
            str(repo),
            "--slug",
            "2026-11-09_existing",
            "--title",
            "Probe",
            "--branch",
            "spec/existing",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "integration branch already exists" in result.stderr
    assert git(repo, "branch", "--show-current") == "feature/work"
    assert not (repo / ".spec" / "specs" / "2026-11-09_existing").exists()


def test_init_full_workflow_and_custom_specs_directory(tmp_path):
    target = tmp_path / "project"
    target.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--root",
            str(target),
            "--slug",
            "full-probe",
            "--title",
            "Full Probe",
            "--workflow",
            "full",
            "--specs-dir",
            "project-state",
        ],
        cwd=target,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    package = target / "project-state" / "specs" / "full-probe"
    content = (package / "spec.md").read_text(encoding="utf-8")
    assert "- Workflow: FULL" in content
    assert "Full Probe" in content
    assert (package / "tasks.md").is_file()


def test_init_force_replaces_existing_template_without_git(tmp_path):
    target = tmp_path / "project"
    target.mkdir()
    first = run_init(target, "force-probe")
    assert first.returncode == 0, first.stderr
    spec = target / ".spec" / "specs" / "force-probe" / "spec.md"
    spec.write_text("old content\n", encoding="utf-8")
    refused = run_init(target, "force-probe")
    assert refused.returncode != 0
    assert spec.read_text(encoding="utf-8") == "old content\n"
    result = subprocess.run(
        [sys.executable, str(INIT), "--root", str(target), "--slug", "force-probe", "--title", "New", "--force"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "New" in spec.read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", ["spec.md", "tasks.md", "adoption.json"])
@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_adopt_force_replaces_only_current_package_entry(tmp_path, filename, link_kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    git(repo, "checkout", "-qb", "spec/in-flight")
    slug = "2026-11-09_adopt-linked"
    package = repo / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    historical = package.parent / "archive" / "2026-11-08_old"
    historical.mkdir(parents=True)
    sentinel = historical / filename
    sentinel.write_text("frozen history\n", encoding="utf-8")
    target = package / filename
    if link_kind == "symlink":
        target.symlink_to(sentinel)
    else:
        os.link(sentinel, target)
    result = subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--root",
            str(repo),
            "--slug",
            slug,
            "--title",
            "Takeover",
            "--adopt",
            "--confirm-adopt",
            "--force",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert sentinel.read_text(encoding="utf-8") == "frozen history\n"
    assert not target.is_symlink()
    assert target.stat().st_ino != sentinel.stat().st_ino
    assert target.stat().st_nlink == 1


@pytest.mark.parametrize("adopt", [False, True])
def test_force_refuses_package_directory_link_to_archive(tmp_path, adopt):
    repo = tmp_path / "repo"
    repo.mkdir()
    if adopt:
        init_git_repo(repo)
        git(repo, "checkout", "-qb", "spec/in-flight")
    slug = "2026-11-09_linked-package"
    packages = repo / ".spec" / "specs"
    historical = packages / "archive" / "2026-11-08_old"
    historical.mkdir(parents=True)
    sentinel = historical / "spec.md"
    sentinel.write_text("frozen history\n", encoding="utf-8")
    (packages / slug).symlink_to(historical, target_is_directory=True)
    command = [sys.executable, str(INIT), "--root", str(repo), "--slug", slug, "--title", "Takeover", "--force"]
    result = subprocess.run(command + (["--adopt", "--confirm-adopt"] if adopt else []), capture_output=True, text=True)
    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "frozen history\n"
    assert sorted(path.name for path in historical.iterdir()) == ["spec.md"]


@pytest.mark.parametrize("symlink_name", ["spec.md", "tasks.md", "adoption.json"])
def test_adopt_force_refuses_to_write_outside_spec_root(tmp_path, symlink_name):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    git(repo, "checkout", "-qb", "spec/in-flight")
    slug = "2026-11-09_adopt-symlink"
    package = repo / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("untouched\n", encoding="utf-8")
    (package / symlink_name).symlink_to(sentinel)
    result = subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--root",
            str(repo),
            "--slug",
            slug,
            "--title",
            "Takeover",
            "--adopt",
            "--confirm-adopt",
            "--force",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "untouched\n"


def test_adopt_force_overwrites_existing_template_and_keeps_other_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    git(repo, "checkout", "-qb", "spec/in-flight")
    slug = "2026-11-09_adopt-existing"
    package = repo / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text("# Existing\n- Git integration branch：`spec/in-flight`\n", encoding="utf-8")
    (package / "tasks.md").write_text("old\n", encoding="utf-8")
    (package / "other.txt").write_text("keep\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--root",
            str(repo),
            "--slug",
            slug,
            "--title",
            "Takeover",
            "--adopt",
            "--confirm-adopt",
            "--force",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Takeover" in (package / "spec.md").read_text(encoding="utf-8")
    assert "spec/in-flight" in (package / "spec.md").read_text(encoding="utf-8")
    assert (package / "other.txt").read_text(encoding="utf-8") == "keep\n"


def test_adopt_cli_requires_confirmation_and_preserves_current_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    git(repo, "checkout", "-qb", "spec/in-flight")
    (repo / "README.md").write_text("in flight\n", encoding="utf-8")
    cmd = [
        sys.executable,
        str(INIT),
        "--root",
        str(repo),
        "--slug",
        "2026-11-09_takeover",
        "--title",
        "Takeover",
        "--adopt",
    ]
    refused = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    assert refused.returncode != 0
    assert "--confirm-adopt" in refused.stderr
    package = repo / ".spec" / "specs" / "2026-11-09_takeover"
    assert not package.exists()
    result = subprocess.run(cmd + ["--confirm-adopt"], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "not confirmed paused" in result.stdout
    assert git(repo, "branch", "--show-current") == "spec/in-flight"
    baseline = json.loads((package / "adoption.json").read_text(encoding="utf-8"))
    assert "README.md" in baseline["status"]
    assert baseline["writers_paused"] is False
    assert "spec/in-flight" in (package / "spec.md").read_text(encoding="utf-8")
    assert (repo / "README.md").read_text(encoding="utf-8") == "in flight\n"

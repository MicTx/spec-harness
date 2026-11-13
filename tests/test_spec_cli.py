import json
import subprocess

from tests.v3_support import SLUG, cli, make_package, repo


def test_cli_resolves_root_and_slug_from_nested_directory(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    result = cli(nested, "next")
    assert result.returncode == 0
    assert json.loads(result.stdout)["slug"] == SLUG


def test_user_command_surface():
    from pathlib import Path

    skill = (Path(__file__).parents[1] / "SKILL.md").read_text()
    for stage in ("/spec:plan", "/spec:run", "/spec:check", "/spec:push", "/spec:doctor"):
        assert stage in skill
    for retired in ("/spec:new", "/spec:done", "/spec:goal", "/spec:update", "/spec:organize", "/spec:status"):
        assert retired not in skill


def test_user_commands_and_references_require_real_ui_review():
    from pathlib import Path

    root = Path(__file__).parents[1]
    skill = (root / "SKILL.md").read_text()
    execution = (root / "references/exec.md").read_text()
    installer = (root / "install.sh").read_text()
    assert "UI/UX review" in skill
    assert "## UI/UX Review" in execution
    assert "spec.py ui-review --file" in execution
    assert "ui-review.json" in installer


def test_done_reports_archived_result_and_recovery_when_push_fails(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec_path = package / "spec.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace("V4 finish: commit", "V4 finish: push"),
        encoding="utf-8",
    )
    remote = tmp_path.parent / f"{tmp_path.name}-remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=tmp_path, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=tmp_path, check=True)
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    subprocess.run(
        ["git", "remote", "set-url", "origin", str(tmp_path.parent / f"{tmp_path.name}-missing.git")],
        cwd=tmp_path,
        check=True,
    )

    result = cli(tmp_path, "done")

    assert result.returncode == 1
    assert result.stdout, result.stderr
    payload = json.loads(result.stdout)
    assert payload["archive"].endswith(f".spec/specs/archive/{SLUG}")
    assert payload["finish"] == "push"
    assert payload["push"]["error"]
    assert "delivery incomplete" in result.stderr
    assert "spec.py push" in result.stderr

    subprocess.run(["git", "remote", "set-url", "origin", str(remote)], cwd=tmp_path, check=True)
    retry = cli(tmp_path, "push")
    assert retry.returncode == 0, retry.stderr
    assert json.loads(retry.stdout)["pushed"] is True


def test_done_preflight_rejects_dirty_target_worktree_worktree_topology(tmp_path):
    from tests.v3_support import SLUG, cli, make_package, repo

    repo(tmp_path)
    package = make_package(tmp_path)
    spec_path = package / "spec.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace("V4 finish: commit", "V4 finish: push"),
        encoding="utf-8",
    )
    remote = tmp_path.parent / f"{tmp_path.name}-remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=tmp_path, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=tmp_path, check=True)
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    main_worktree = tmp_path.parent / f"{tmp_path.name}-main-worktree"
    subprocess.run(["git", "worktree", "add", str(main_worktree), "main"], cwd=tmp_path, check=True)
    (main_worktree / "dirty.txt").write_text("keep me\n", encoding="utf-8")

    result = cli(tmp_path, "done")

    assert result.returncode != 0
    assert "target worktree is not clean" in result.stderr
    assert (tmp_path / ".spec/specs" / SLUG / "spec.md").is_file()
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def _git_out(root, *args) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _unbound_package_on_main(tmp_path):
    """Fixture: a package planned on main with pre-existing uncommitted dirt."""
    from tests.v3_support import git

    repo(tmp_path)
    package = make_package(tmp_path)
    spec_path = package / "spec.md"
    spec_path.write_text(spec_path.read_text(encoding="utf-8").split("## Binding")[0].rstrip() + "\n")
    git(tmp_path, "checkout", "main")
    git(tmp_path, "branch", "-d", f"spec/{SLUG}")
    (tmp_path / "ui-dirt.txt").write_text("uncommitted ui work\n", encoding="utf-8")
    return package


def test_plan_go_worktree_binds_linked_and_isolates_main_dirt(tmp_path):
    _unbound_package_on_main(tmp_path)

    result = cli(tmp_path, "--slug", SLUG, "plan-go")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    worktree = tmp_path / ".spec" / "worktrees" / SLUG
    assert payload["branch"] == f"spec/{SLUG}"
    assert payload["worktree"] == str(worktree)
    assert payload["next"]["slug"] == SLUG
    assert _git_out(tmp_path, "branch", "--show-current") == "main"
    assert _git_out(worktree, "branch", "--show-current") == f"spec/{SLUG}"
    assert (worktree / ".spec" / "specs" / SLUG / "spec.md").is_file()
    assert "- frozen:" in (worktree / ".spec" / "specs" / SLUG / "spec.md").read_text(encoding="utf-8")
    assert not (tmp_path / ".spec" / "specs" / SLUG / "spec.md").exists()
    assert (tmp_path / "ui-dirt.txt").is_file()
    assert not (worktree / "ui-dirt.txt").exists()
    status = _git_out(tmp_path, "status", "--porcelain")
    assert "ui-dirt.txt" in status
    assert ".spec/worktrees" not in status
    import gitx

    assert gitx.dirty_paths(worktree) == []


def test_plan_go_worktree_static_gap_leaves_no_worktree(tmp_path):
    _unbound_package_on_main(tmp_path)
    tasks = tmp_path / ".spec" / "specs" / SLUG / "tasks.md"
    tasks.write_text(
        "- [ ] Broken\n  - id: task-x\n  - boundary: missing-xyz\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )

    result = cli(tmp_path, "--slug", SLUG, "plan-go")

    assert result.returncode != 0
    assert not (tmp_path / ".spec" / "worktrees" / SLUG).exists()
    assert _git_out(tmp_path, "branch", "--list", f"spec/{SLUG}") == ""
    assert (tmp_path / ".spec" / "specs" / SLUG / "spec.md").is_file()


def test_plan_go_worktree_verify_gap_retries_from_worktree(tmp_path):
    _unbound_package_on_main(tmp_path)
    tasks = tmp_path / ".spec" / "specs" / SLUG / "tasks.md"
    tasks.write_text(
        "- [ ] Fails\n  - id: task-value\n  - boundary: app.py\n  - verify: `false`\n  - expect: green\n",
        encoding="utf-8",
    )
    worktree = tmp_path / ".spec" / "worktrees" / SLUG

    result = cli(tmp_path, "--slug", SLUG, "plan-go")

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["ready"] is False
    assert payload["worktree"] == str(worktree)
    assert not (tmp_path / ".spec" / "specs" / SLUG / "spec.md").exists()
    wt_tasks = worktree / ".spec" / "specs" / SLUG / "tasks.md"
    wt_tasks.write_text(
        wt_tasks.read_text(encoding="utf-8").replace("`false`", "`true`"),
        encoding="utf-8",
    )
    retry = cli(worktree, "--slug", SLUG, "plan-go")
    assert retry.returncode == 0, retry.stderr
    assert "- frozen:" in (worktree / ".spec" / "specs" / SLUG / "spec.md").read_text(encoding="utf-8")


def test_next_discovers_worktree_bound_packages(tmp_path):
    _unbound_package_on_main(tmp_path)
    assert cli(tmp_path, "--slug", SLUG, "plan-go").returncode == 0

    result = cli(tmp_path, "next")

    assert result.returncode != 0
    assert SLUG in result.stderr
    assert ".spec/worktrees" in result.stderr


def test_next_discovers_local_unbound_packages(tmp_path):
    _unbound_package_on_main(tmp_path)

    result = cli(tmp_path, "next")

    assert result.returncode != 0
    assert SLUG in result.stderr
    assert "local packages" in result.stderr

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
    for stage in ("/spec:plan", "/spec:run", "/spec:repair", "/spec:check", "/spec:push", "/spec:doctor"):
        assert stage in skill
    for retired in ("/spec:new", "/spec:done", "/spec:goal", "/spec:update", "/spec:organize", "/spec:status"):
        assert retired not in skill


def test_user_commands_and_references_do_not_require_a_ui_review():
    from pathlib import Path

    root = Path(__file__).parents[1]
    skill = (root / "SKILL.md").read_text()
    execution = (root / "references/exec.md").read_text()
    installer = (root / "install.sh").read_text()
    assert "UI/UX review" not in skill
    assert "## UI/UX Review" not in execution
    assert "spec.py ui-review --file" not in execution
    assert "ui-review.json" not in installer


def test_done_preserves_active_package_when_remote_is_unavailable(tmp_path):
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
    assert any(item["code"] == "handoff-blocked" for item in payload["findings"])
    assert payload["next"]["decisions"][0]["id"] == "P0"
    assert (tmp_path / ".spec/specs" / SLUG).is_dir()
    assert "remote HEAD is unavailable" in result.stderr

    subprocess.run(["git", "remote", "set-url", "origin", str(remote)], cwd=tmp_path, check=True)
    retry = cli(tmp_path, "check")
    assert retry.returncode == 0, retry.stderr
    assert json.loads(retry.stdout)["push"]["pushed"] is True


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


def test_waiting_for_handoff_still_reports_until_a_new_user_choice(tmp_path):
    repo(tmp_path)
    directory = make_package(tmp_path)
    spec = directory / "spec.md"
    spec.write_text(spec.read_text().replace("V4 finish: commit", "V4 finish: push"))
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "check").returncode == 1
    assert cli(tmp_path, "decide", "--resolve", "P0", "--choice", "wait", "--by", "user waits", "Wait").returncode == 0
    probe = cli(tmp_path, "check", "--check-only")
    assert probe.returncode == 0
    paused = json.loads(cli(tmp_path, "next").stdout)
    assert paused["state"] == "parked-only" and not paused["decisions"]
    result = cli(tmp_path, "check")
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["next"]["state"] == "parked-only"
    assert not payload["next"]["decisions"]
    assert payload["next"]["parked"][0]["status"] == "waiting"
    assert "no Git remote" in result.stderr


def test_parked_choice_is_linked_and_wait_does_not_finish_or_reask(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    parked = cli(tmp_path, "decide", "Keep the optional follow-up?", "--park")
    assert parked.returncode == 0
    parked_id = json.loads(parked.stdout)["id"]
    denied = cli(tmp_path, "decide", "--resolve", parked_id, "--choice", "wait", "No user authority")
    assert denied.returncode == 1
    waiting = cli(tmp_path, "decide", "--resolve", parked_id, "--choice", "wait", "--by", "user waiting", "Later")
    assert waiting.returncode == 0, waiting.stderr
    check = json.loads(cli(tmp_path, "check").stdout)
    assert not check["passed"] and not check["next"]["decisions"]
    assert check["parked"][0]["status"] == "waiting"
    closed = cli(tmp_path, "decide", "--resolve", parked_id, "--choice", "resolve", "--by", "user approved", "Close it")
    assert closed.returncode == 0, closed.stderr
    assert cli(tmp_path, "check").returncode == 0
    summary = (tmp_path / ".spec/specs/archive" / SLUG / "completion-summary.md").read_text()
    assert "Close it" in summary and parked_id in summary


def test_unavailable_handoff_offers_wait_or_authorized_local_commit(tmp_path):
    repo(tmp_path)
    directory = make_package(tmp_path)
    spec = directory / "spec.md"
    spec.write_text(spec.read_text().replace("V4 finish: commit", "V4 finish: push"), encoding="utf-8")
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    main = _git_out(tmp_path, "rev-parse", "main")
    check = cli(tmp_path, "check")
    assert check.returncode == 1
    card = json.loads(check.stdout)["next"]["decisions"][0]
    assert {row["id"] for row in card["options"]} == {"wait", "local-commit"}
    resolution = ("decide", "--resolve", card["id"], "--choice", "local-commit", "--by", "user approved", "Keep local")
    app = tmp_path / "app.py"
    app.write_text("VALUE = 1\n")
    assert cli(tmp_path, *resolution).returncode == 1  # The card is for a different tree.
    app.write_text("VALUE = 0\n")
    decision = cli(tmp_path, *resolution)
    assert decision.returncode == 0, decision.stderr
    delivered = json.loads(cli(tmp_path, "check").stdout)
    assert delivered["passed"] and delivered["finish"] == "commit" and delivered["requested_finish"] == "push"
    assert _git_out(tmp_path, "rev-parse", "main") == main
    assert not (tmp_path / ".spec/charter.md").exists()
    assert json.loads(cli(tmp_path, "next").stdout)["state"] == "delivered"
    assert "local commit only" in cli(tmp_path, "push").stderr


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

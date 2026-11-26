"""The small-fix path retains proof without moving or staging the change."""

import json
import shlex
import sys
from pathlib import Path

import gitx
import pytest

from tests.v3_support import cli, git, repo

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

from spec_hook import handle  # noqa: E402


def _main(root):
    repo(root)
    git(root, "checkout", "main")


def _start(root, *paths, verify="grep -qx 'VALUE = 1' app.py"):
    flags = tuple(item for name in (paths or ("app.py",)) for item in ("--path", name))
    return cli(root, "quick", "Fix the fixture", "--done", "VALUE is corrected", *flags, "--verify", verify)


def _ok(result):
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_quick_runs_one_check_on_main_and_leaves_the_index_untouched(tmp_path):
    _main(tmp_path)
    head = gitx.head(tmp_path)
    index = (tmp_path / ".git" / "index").read_bytes()
    worktrees = gitx.worktrees(tmp_path)
    branches = gitx.stdout(tmp_path, "for-each-ref", "refs/heads")
    counter = tmp_path.parent / f"{tmp_path.name}-count"
    quick = _ok(_start(tmp_path, verify=f"printf x >> {shlex.quote(str(counter))}; grep -qx 'VALUE = 1' app.py"))
    assert quick["branch"] == "main"
    assert not counter.exists() and gitx.dirty_paths(tmp_path, exclude=()) == []
    assert not (tmp_path / ".spec" / "charter.md").exists()
    assert _ok(cli(tmp_path, "next"))["slug"] == quick["slug"]
    assert handle("pre_tool", {"cwd": str(tmp_path), "command": "git commit -m work"}).deny
    assert cli(tmp_path, "verify", "--all").returncode != 0

    (tmp_path / "app.py").write_text("VALUE = 1\n")
    finished = _ok(cli(tmp_path, "check"))

    assert finished["finish"] == "worktree" and finished["next"]["state"] == "delivered"
    assert counter.read_text() == "x" and gitx.head(tmp_path) == head
    assert (tmp_path / ".git" / "index").read_bytes() == index
    assert gitx.worktrees(tmp_path) == worktrees and gitx.stdout(tmp_path, "for-each-ref", "refs/heads") == branches
    assert gitx.dirty_paths(tmp_path, exclude=()) == ["app.py"]
    assert _ok(cli(tmp_path, "next"))["state"] == "delivered"
    assert not handle("pre_tool", {"cwd": str(tmp_path), "command": "git commit -m work"}).deny
    assert cli(tmp_path, "push", "--slug", quick["slug"]).returncode != 0


@pytest.mark.parametrize("changed", ["outside", "staged", "head", "verify"])
def test_quick_blocks_drift_without_executing_or_absorbing_it(tmp_path, changed):
    _main(tmp_path)
    counter = tmp_path.parent / f"{tmp_path.name}-count"
    quick = _ok(_start(tmp_path, verify=f"touch {shlex.quote(str(counter))}"))
    directory = tmp_path / ".spec" / "specs" / quick["slug"]
    source = tmp_path / "app.py"
    source.write_text("VALUE = 1\n")
    if changed == "outside":
        (tmp_path / "unrelated.txt").write_text("user data\n")
    elif changed == "staged":
        git(tmp_path, "add", "app.py")
    elif changed == "head":
        git(tmp_path, "commit", "--allow-empty", "-qm", "fixture advances head")
    else:
        card = directory / "tasks.md"
        card.write_text(card.read_text().replace("touch ", "echo changed > "))
    result = cli(tmp_path, "check")
    assert result.returncode != 0 and not counter.exists()
    assert source.read_text() == "VALUE = 1\n" and directory.is_dir()


def test_quick_fails_without_a_fix_and_rechecks_after_real_failure(tmp_path):
    _main(tmp_path)
    _ok(_start(tmp_path))
    assert cli(tmp_path, "check").returncode != 0
    (tmp_path / "app.py").write_text("VALUE = 2\n")
    assert cli(tmp_path, "check").returncode != 0
    assert _ok(cli(tmp_path, "next"))["state"] == "check-failed"
    (tmp_path / "app.py").write_text("VALUE = 1\n")
    assert _ok(cli(tmp_path, "check"))["finish"] == "worktree"


@pytest.mark.parametrize("paths", [("src/*.py",), ("../file",), (".env",), ("web/app.tsx",), ("app.py", "a", "b")])
def test_quick_rejects_nontrivial_scope_before_creating_evidence(tmp_path, paths):
    _main(tmp_path)
    assert _start(tmp_path, *paths).returncode != 0
    assert not (tmp_path / ".spec").exists()


def test_quick_never_adopts_preexisting_work_in_the_checkout(tmp_path):
    _main(tmp_path)
    app = tmp_path / "app.py"
    app.write_text("VALUE = 42\n")
    assert _start(tmp_path).returncode != 0
    assert app.read_text() == "VALUE = 42\n" and not (tmp_path / ".spec").exists()


def test_quick_does_not_turn_project_delivery_policy_into_git_authority(tmp_path):
    _main(tmp_path)
    head = gitx.head(tmp_path)
    policy = tmp_path / ".spec" / "charter.md"
    policy.parent.mkdir()
    policy.write_text("- V4 finish: push\n")
    _ok(_start(tmp_path))
    (tmp_path / "app.py").write_text("VALUE = 1\n")
    assert _ok(cli(tmp_path, "check"))["finish"] == "worktree"
    assert policy.read_text() == "- V4 finish: push\n" and gitx.head(tmp_path) == head


def test_concurrent_quick_names_are_exactly_ignored_and_never_clobbered(tmp_path):
    _main(tmp_path)
    slug = "2026-11-25_quick-first"
    quick = _ok(cli(tmp_path, "--slug", slug, "quick", "Fix the value", "--path", "app.py", "--verify", "true"))
    assert quick["slug"] == slug
    duplicate = cli(tmp_path, "--slug", slug, "quick", "Other goal", "--path", "app.py", "--verify", "true")
    assert duplicate.returncode != 0
    lookalike = tmp_path / ".spec" / "specs" / "2026-11-25_quick-first-extra" / "spec.md"
    lookalike.parent.mkdir(parents=True)
    lookalike.write_text("user-owned\n")
    assert gitx.git(tmp_path, "check-ignore", "-q", str(lookalike), check=False).returncode == 1
    assert lookalike.read_text() == "user-owned\n"


def test_new_standard_plan_cannot_freeze_on_an_active_quick_branch(tmp_path):
    from tests.v3_support import make_package

    _main(tmp_path)
    _ok(_start(tmp_path))
    draft = make_package(tmp_path)
    spec = draft / "spec.md"
    spec.write_text(spec.read_text().split("## Binding")[0])
    result = cli(tmp_path, "--slug", draft.name, "plan-go")
    assert result.returncode != 0 and "already binds active package" in result.stderr
    assert "- frozen:" not in spec.read_text()

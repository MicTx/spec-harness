"""Delivery Git edges: remote transport environment and Spec-owned target dirt.

Remote Git calls must keep the user's transport settings (GIT_SSH_COMMAND,
GIT_SSH, SSH_ASKPASS, GIT_ASKPASS) while local calls keep stripping them, and
files Spec itself wrote in the target worktree (.spec/charter.md and friends)
must not block a merge/push delivery as "target dirt".
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import subprocess as real_subprocess  # noqa: E402

import gitx  # noqa: E402
import push  # noqa: E402

from tests.v3_support import SLUG, cli, git  # noqa: E402


def _git_out(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _plain_repo_on_main(root: Path) -> None:
    """A repository without local-evidence ignore rules, checked out on main.

    tests/v3_support.repo() writes a .gitignore covering .spec/charter.md,
    which hides the defect this file exercises, so this fixture inits its own
    repository instead.
    """
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")


def _package(root: Path, slug: str, finish: str) -> Path:
    directory = root / ".spec" / "specs" / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spec.md").write_text(
        f"""# Fixture {slug}
<!-- spec-format: 3 -->
## Goal
- goal: Change the fixture value
- literal ask: Test delivery git edges
- done means: The command passes
## Charter
- V1 priority: quality > maintainability > speed > cost | card
- V2 scope: adjacent-fixes | charter.md
- V3 verification: package | charter.md
- V4 finish: {finish} | charter.md
- V5 dependencies: none-new | charter.md
- V6 compatibility: break-ok | charter.md
- V7 blocked: park-and-continue | charter.md
## Scope
- in: app.py
- out: publication
## Assumptions
## Forks
## Criteria
- C1 | Fixture works | task-value
## Decisions
## Parked
""",
        encoding="utf-8",
    )
    (directory / "tasks.md").write_text(
        "- [ ] Change value\n  - id: task-value\n  - boundary: app.py\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    return directory


def _bare_remote(root: Path, remote: Path) -> Path:
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "origin", "main")
    return remote


def _start_package(root: Path, slug: str, finish: str) -> Path:
    """Plan on the protected main branch and start execution in a worktree."""
    _package(root, slug, finish)
    result = cli(root, "--slug", slug, "plan-go")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ready"] is True
    worktree = root / ".spec" / "worktrees" / slug
    assert worktree.is_dir()
    assert _git_out(worktree, "branch", "--show-current") == f"spec/{slug}"
    verified = cli(worktree, "verify", "task-value")
    assert verified.returncode == 0, verified.stderr
    return worktree


def test_remote_env_preserves_transport_variables(monkeypatch):
    """Remote Git keeps the user's SSH/askpass transport; local Git strips it."""
    for name in ("GIT_SSH_COMMAND", "GIT_SSH", "SSH_ASKPASS", "GIT_ASKPASS", "GIT_SSH_VARIANT"):
        monkeypatch.setenv(name, f"/opt/transport/{name.lower()}")
    monkeypatch.setenv("GIT_DIR", "/foreign/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/foreign")
    monkeypatch.setenv("GIT_INDEX_FILE", "/foreign/index")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hooksPath=/evil'")

    local = gitx.clean_env()
    for name in ("GIT_SSH_COMMAND", "GIT_SSH", "SSH_ASKPASS", "GIT_ASKPASS", "GIT_SSH_VARIANT"):
        assert name not in local, f"local env must strip {name}"
    assert "GIT_DIR" not in local
    assert "GIT_WORK_TREE" not in local
    assert "GIT_INDEX_FILE" not in local
    assert not any(key.startswith("GIT_CONFIG_") for key in local)
    assert local["GIT_OPTIONAL_LOCKS"] == "0"

    remote = gitx.clean_env(remote=True)
    for name in ("GIT_SSH_COMMAND", "GIT_SSH", "SSH_ASKPASS", "GIT_ASKPASS", "GIT_SSH_VARIANT"):
        assert remote[name] == f"/opt/transport/{name.lower()}", f"remote env must keep {name}"
    # Transport variables may ride along, but repository-context and config
    # injection variables stay stripped even for remote calls: a parent
    # `git -c core.hooksPath=...` must not disable hooks during the handoff.
    assert "GIT_DIR" not in remote
    assert "GIT_WORK_TREE" not in remote
    assert "GIT_INDEX_FILE" not in remote
    assert "GIT_CONFIG_COUNT" not in remote
    assert "GIT_CONFIG_PARAMETERS" not in remote
    assert remote["GIT_OPTIONAL_LOCKS"] == "0"


def test_git_remote_flag_selects_remote_env(tmp_path, monkeypatch):
    """git(..., remote=True) requests the transport-preserving environment."""
    monkeypatch.setenv("GIT_SSH_COMMAND", "/opt/transport/ssh -i /opt/transport/key")
    captured = {}
    real_run = real_subprocess.run

    def spy(command, **kwargs):
        captured["env"] = kwargs.get("env")
        return real_run(command, **kwargs)

    monkeypatch.setattr(gitx.subprocess, "run", spy)
    # `init` is local plumbing but it lets us observe the env git() builds.
    gitx.git(tmp_path, "init", "-q", remote=True)
    assert captured["env"]["GIT_SSH_COMMAND"] == "/opt/transport/ssh -i /opt/transport/key"

    gitx.git(tmp_path, "rev-parse", "--git-dir", check=False)
    assert "GIT_SSH_COMMAND" not in captured["env"]


def test_push_module_marks_every_remote_call():
    """Every transport-touching call in push.py opts into remote mode."""
    source = (SCRIPTS / "push.py").read_text(encoding="utf-8")
    remote_verbs = 0
    for line in source.splitlines():
        stripped = line.strip()
        if "gitx.git(" not in stripped:
            continue
        if '"ls-remote"' in stripped or '"push"' in stripped:
            assert "remote=True" in stripped, f"remote call is not marked: {stripped}"
            remote_verbs += 1
        else:
            assert "remote=True" not in stripped, f"local call marked remote: {stripped}"
    # ls-remote x2, push x1, push --delete x1.
    assert remote_verbs == 4


def test_merge_delivery_ignores_spec_state_in_target(tmp_path):
    """A V4 merge leaves the target's independent charter untouched."""
    _plain_repo_on_main(tmp_path)
    _bare_remote(tmp_path, tmp_path.parent / f"{tmp_path.name}-remote.git")
    policy = tmp_path / ".spec" / "charter.md"
    policy.parent.mkdir()
    policy.write_text("- V4 finish: commit\n")
    worktree = _start_package(tmp_path, SLUG, finish="merge")
    assert policy.read_text() == "- V4 finish: commit\n"
    assert ".spec/charter.md" in gitx.dirty_paths(tmp_path, exclude=())

    result = cli(worktree, "check")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["finish"] == "merge"
    assert payload["push"]["merged"] is True
    assert payload["push"]["pushed"] is False
    assert not any("not clean" in error for error in payload["errors"])
    assert _git_out(tmp_path, "branch", "--show-current") == "main"
    assert (tmp_path / ".spec" / "specs" / "archive" / SLUG / "spec.md").is_file()


def test_push_delivery_reaches_bare_remote(tmp_path):
    """A V4 push delivery merges into main and updates the remote head."""
    _plain_repo_on_main(tmp_path)
    remote = _bare_remote(tmp_path, tmp_path.parent / f"{tmp_path.name}-remote.git")
    worktree = _start_package(tmp_path, SLUG, finish="push")

    result = cli(worktree, "check")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["finish"] == "push"
    assert payload["push"]["pushed"] is True
    remote_head = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_head == payload["push"]["head"]
    assert _git_out(tmp_path, "branch", "--list", f"spec/{SLUG}") == ""


def test_foreign_dirt_in_target_still_blocks(tmp_path):
    """Spec-owned dirt is tolerated; user files in the target still block."""
    _plain_repo_on_main(tmp_path)
    _bare_remote(tmp_path, tmp_path.parent / f"{tmp_path.name}-remote.git")
    worktree = _start_package(tmp_path, SLUG, finish="merge")
    (tmp_path / "foreign-dirt.txt").write_text("not spec's file\n", encoding="utf-8")

    result = cli(worktree, "check")

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert any(item["code"] == "handoff-blocked" and "not clean" in item["detail"] for item in payload["findings"]), (
        payload
    )
    assert "foreign-dirt.txt" in result.stderr or "foreign-dirt.txt" in json.dumps(payload)
    # Nothing was delivered: the package stays active in the worktree.
    assert (worktree / ".spec" / "specs" / SLUG / "spec.md").is_file()
    assert not (worktree / ".spec" / "specs" / "archive" / SLUG).exists()


def test_delivery_range_touching_spec_state_still_blocks(tmp_path):
    """A delivery branch that modifies the Spec-state path keeps the block.

    Whitelisting the path must not let a merge silently overwrite the user's
    copy: if the incoming range changes .spec/charter.md the target dirt is
    real and the handoff stays blocked.
    """
    _plain_repo_on_main(tmp_path)
    _bare_remote(tmp_path, tmp_path.parent / f"{tmp_path.name}-remote.git")
    worktree = _start_package(tmp_path, SLUG, finish="merge")
    # The package branch itself changes the Spec-owned path in the target.
    (worktree / ".spec" / "charter.md").write_text("# branch charter\n", encoding="utf-8")
    git(worktree, "add", ".spec/charter.md")
    git(worktree, "commit", "-qm", "branch-owned charter")
    (tmp_path / ".spec" / "charter.md").write_text("# divergent local copy\n", encoding="utf-8")

    result = cli(worktree, "check")

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert any(
        item["code"] == "handoff-blocked" and ".spec/charter.md" in item["detail"] for item in payload["findings"]
    ), payload


def test_preflight_reports_spec_dirt_only_after_foreign_dirt(tmp_path):
    """Direct preflight check: Spec-state paths are filtered, foreign dirt stays."""
    _plain_repo_on_main(tmp_path)
    _bare_remote(tmp_path, tmp_path.parent / f"{tmp_path.name}-remote.git")
    worktree = _start_package(tmp_path, SLUG, finish="merge")
    (tmp_path / ".spec" / "cache").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".spec" / "cache" / "blob").write_text("x", encoding="utf-8")

    # Only Spec-owned dirt: no block.
    push.preflight(worktree, SLUG, (worktree / ".spec" / "specs" / SLUG / "spec.md").read_text(), ["app.py"])

    (tmp_path / "user.txt").write_text("mine\n", encoding="utf-8")
    with pytest.raises(push.HandoffBlocked) as excinfo:
        push.preflight(worktree, SLUG, (worktree / ".spec" / "specs" / SLUG / "spec.md").read_text(), ["app.py"])
    assert excinfo.value.subject == "target-dirty"
    assert "user.txt" in str(excinfo.value)
    assert ".spec/cache" not in str(excinfo.value)

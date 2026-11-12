import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from tests.v3_support import SLUG, cli, git, repo  # noqa: E402


def _git_out(root, *args) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo_on_main(tmp_path: Path) -> None:
    repo(tmp_path)
    git(tmp_path, "checkout", "main")
    git(tmp_path, "branch", "-d", f"spec/{SLUG}")


def _package(root: Path, slug: str, finish: str = "commit") -> Path:
    directory = root / ".spec" / "specs" / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spec.md").write_text(
        f"""# Fixture {slug}
<!-- spec-format: 3 -->
## Goal
- goal: Change the fixture value
- literal ask: Test v3
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


def _remote(tmp_path: Path) -> Path:
    remote = tmp_path.parent / f"{tmp_path.name}-remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=tmp_path, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=tmp_path, check=True)
    return remote


def test_two_packages_execute_in_separate_worktrees(tmp_path):
    _repo_on_main(tmp_path)
    slug_b = "2026-01-02_second"
    _package(tmp_path, SLUG)
    _package(tmp_path, slug_b)

    assert cli(tmp_path, "--slug", SLUG, "plan-go").returncode == 0
    assert cli(tmp_path, "--slug", slug_b, "plan-go").returncode == 0

    worktree_a = tmp_path / ".spec" / "worktrees" / SLUG
    worktree_b = tmp_path / ".spec" / "worktrees" / slug_b
    assert _git_out(worktree_a, "branch", "--show-current") == f"spec/{SLUG}"
    assert _git_out(worktree_b, "branch", "--show-current") == f"spec/{slug_b}"
    assert _git_out(tmp_path, "branch", "--show-current") == "main"

    assert cli(worktree_a, "verify", "task-value").returncode == 0
    tasks_b = worktree_b / ".spec" / "specs" / slug_b / "tasks.md"
    assert "[x]" in (worktree_a / ".spec" / "specs" / SLUG / "tasks.md").read_text(encoding="utf-8")
    assert "[x]" not in tasks_b.read_text(encoding="utf-8")

    assert cli(worktree_b, "verify", "task-value").returncode == 0
    assert "[x]" in tasks_b.read_text(encoding="utf-8")
    assert _git_out(tmp_path, "branch", "--show-current") == "main"


def test_delivery_from_worktree_merges_pushes_and_cleans(tmp_path):
    _repo_on_main(tmp_path)
    _package(tmp_path, SLUG, finish="push")
    _remote(tmp_path)
    assert cli(tmp_path, "--slug", SLUG, "plan-go").returncode == 0
    worktree = tmp_path / ".spec" / "worktrees" / SLUG
    assert cli(worktree, "verify", "task-value").returncode == 0

    result = cli(worktree, "done")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["finish"] == "push"
    assert payload["push"]["pushed"] is True
    assert not worktree.exists()
    assert _git_out(tmp_path, "branch", "--list", f"spec/{SLUG}") == ""
    assert (tmp_path / ".spec" / "specs" / "archive" / SLUG / "spec.md").is_file()
    listed = _git_out(tmp_path, "ls-remote", "--heads", "origin")
    assert f"spec/{SLUG}" not in listed


def test_done_rejects_dirty_primary_worktree(tmp_path):
    _repo_on_main(tmp_path)
    _package(tmp_path, SLUG, finish="push")
    _remote(tmp_path)
    assert cli(tmp_path, "--slug", SLUG, "plan-go").returncode == 0
    worktree = tmp_path / ".spec" / "worktrees" / SLUG
    assert cli(worktree, "verify", "task-value").returncode == 0
    (tmp_path / "ui-dirt.txt").write_text("keep me\n", encoding="utf-8")

    result = cli(worktree, "done")

    assert result.returncode != 0
    assert "target worktree is not clean" in result.stderr
    assert (worktree / ".spec" / "specs" / SLUG / "spec.md").is_file()
    assert not (worktree / ".spec" / "specs" / "archive" / SLUG).exists()

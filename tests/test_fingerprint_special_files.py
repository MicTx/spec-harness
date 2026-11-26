"""Tree fingerprints must survive submodules, nested repositories and special files.

C2: a superproject containing an initialized submodule, an untracked nested
repository and a FIFO still goes through plan-lint, plan-go, verify and check
delivery, and the snapshot observes submodule state changes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import package
import pytest
from gitx import stdout

from tests.v3_support import SLUG, cli, git, make_package, repo, started_package

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"


def _source_repo(path: Path) -> None:
    """A standalone repository used as a submodule source."""
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "fixture@example.invalid")
    git(path, "config", "user.name", "Fixture")
    (path / "lib.py").write_text("LIB = 1\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-qm", "lib init")


def _special_tree(root: Path) -> None:
    """Populate a repository with a submodule, an untracked nested repo and a FIFO."""
    source = root.parent / f"{root.name}-subsrc"
    _source_repo(source)
    git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "sub")
    nested = root / "nested"
    nested.mkdir()
    git(nested, "init", "-q", "-b", "main")
    git(nested, "config", "user.email", "fixture@example.invalid")
    git(nested, "config", "user.name", "Fixture")
    (nested / "inner.txt").write_text("inner\n", encoding="utf-8")
    git(nested, "add", ".")
    git(nested, "commit", "-qm", "nested init")
    fifo = root / "pipe"
    try:
        os.mkfifo(fifo)
    except (AttributeError, OSError):
        pass  # platform without FIFO support; the directory cases still run


def _records(pkg: Path) -> list[dict]:
    return [json.loads(line) for line in (pkg / "runs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def test_full_lifecycle_with_special_files(tmp_path):
    repo(tmp_path)
    _special_tree(tmp_path)
    git(tmp_path, "add", ".gitmodules")
    git(tmp_path, "commit", "-qm", "add submodule pointer")
    make_package(tmp_path)

    lint = cli(tmp_path, "plan-lint")
    assert lint.returncode == 0, lint.stderr

    started = cli(tmp_path, "plan-go")
    assert started.returncode == 0, started.stderr

    verified = cli(tmp_path, "verify", "--all")
    assert verified.returncode == 0, verified.stderr

    checked = cli(tmp_path, "check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    payload = json.loads(checked.stdout)
    assert payload["passed"] is True
    assert payload["finish"] == "commit"
    assert (tmp_path / ".spec/specs/archive" / SLUG / "completion-summary.md").is_file()
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout
    assert f"Spec: {SLUG}" in log


@pytest.mark.parametrize("kind", ["head-move", "uncommitted-edit"])
def test_submodule_state_changes_the_live_snapshot(tmp_path, kind):
    repo(tmp_path)
    _special_tree(tmp_path)
    git(tmp_path, "add", ".gitmodules")
    git(tmp_path, "commit", "-qm", "add submodule pointer")
    started_package(tmp_path)
    sub = tmp_path / "sub"
    base_fp = package.tree_fingerprint(tmp_path)

    assert cli(tmp_path, "verify", "task-value").returncode == 0
    after_verify = package.tree_fingerprint(tmp_path)
    assert after_verify == base_fp

    if kind == "head-move":
        # A new commit inside the submodule moves its HEAD.
        (sub / "lib.py").write_text("LIB = 2\n", encoding="utf-8")
        git(sub, "add", "lib.py")
        git(sub, "commit", "-qm", "lib bump")
    else:
        # An uncommitted edit inside the submodule's own working tree.
        (sub / "lib.py").write_text("LIB = 99\n", encoding="utf-8")

    changed = package.tree_fingerprint(tmp_path)
    assert changed != base_fp

    probe = cli(tmp_path, "check", "--check-only")
    assert probe.returncode == 0
    payload = json.loads(probe.stdout)
    assert payload["verification"] == "pending" and payload["tree_fp"] == changed

    # Reverting the submodule restores the original fingerprint.
    if kind == "head-move":
        git(sub, "reset", "-q", "--hard", "HEAD~1")
    else:
        (sub / "lib.py").write_text("LIB = 1\n", encoding="utf-8")
    assert package.tree_fingerprint(tmp_path) == base_fp


def test_uninitialized_submodule_gitlink_change(tmp_path):
    """A gitlink with an empty checkout dir does not crash; pointer moves still rehash."""
    repo(tmp_path)
    source = tmp_path.parent / f"{tmp_path.name}-subsrc"
    _source_repo(source)
    # Register the gitlink without checking out the submodule contents.
    git(tmp_path, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "sub")
    git(tmp_path, "add", ".gitmodules")
    git(tmp_path, "commit", "-qm", "pointer")
    subdir = tmp_path / "sub"
    # Empty the checkout so it is a directory without a .git — the uninitialized case.
    for child in list(subdir.iterdir()):
        if child.name != ".git":
            child.unlink()
    gitfile = subdir / ".git"
    if gitfile.exists():
        gitfile.unlink()
    # Re-register a bare empty directory to simulate deinit.
    assert subdir.is_dir()

    first = package.tree_fingerprint(tmp_path)
    assert first  # no crash

    # Move the gitlink pointer in the index to a different commit.
    git(source, "checkout", "-q", "-b", "other")
    (source / "lib.py").write_text("LIB = 2\n", encoding="utf-8")
    git(source, "add", "lib.py")
    git(source, "commit", "-qm", "lib bump")
    new_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True
    ).stdout.strip()
    git(tmp_path, "update-index", "--cacheinfo", f"160000,{new_head},sub")
    second = package.tree_fingerprint(tmp_path)
    assert second != first


def test_fifo_fingerprint_completes(tmp_path):
    """A FIFO in the tree must not block the fingerprint (opening one would hang)."""
    repo(tmp_path)
    fifo = tmp_path / "pipe"
    try:
        os.mkfifo(fifo)
    except (AttributeError, OSError):
        pytest.skip("platform does not support FIFOs")
    make_package(tmp_path)
    # Run the fingerprint in a subprocess so a hang cannot freeze the suite.
    code = (
        "import sys; sys.path.insert(0, {!r}); from pathlib import Path; import package; "
        "print(package.tree_fingerprint(Path({!r})))".format(str(SCRIPTS), str(tmp_path))
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    digest = result.stdout.strip()
    assert digest == stdout(tmp_path, "rev-parse", "HEAD^{tree}")
    # Also prove the CLI path does not hang on plan-lint.
    lint = cli(tmp_path, "plan-lint")
    assert lint.returncode == 0, lint.stderr


def test_ordinary_tree_uses_the_git_id(tmp_path):
    """Git's own ID includes the executable and symlink modes."""
    repo(tmp_path)
    (tmp_path / "a.txt").write_text("A\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B\n", encoding="utf-8")
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "c.txt").write_text("C\n", encoding="utf-8")
    try:
        os.symlink("a.txt", tmp_path / "link.txt")
    except OSError:
        pytest.skip("platform does not support symlinks")
    exe = tmp_path / "run.sh"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "pin tree")

    assert package.tree_fingerprint(tmp_path) == stdout(tmp_path, "rev-parse", "HEAD^{tree}")

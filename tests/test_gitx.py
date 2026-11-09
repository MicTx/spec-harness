"""Shared Git commands operate on the requested project, never inherited indexes."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gitx  # noqa: E402


def test_git_commands_and_dirty_paths(tmp_path):
    gitx.git(tmp_path, "init", "-q", "-b", "spec/test")
    gitx.git(tmp_path, "config", "user.name", "Fixture")
    gitx.git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "first.txt").write_text("first", encoding="utf-8")
    gitx.git(tmp_path, "add", ".")
    gitx.git(tmp_path, "commit", "-qm", "initial")
    assert gitx.branch(tmp_path) == "spec/test"
    assert gitx.toplevel(tmp_path) == tmp_path.resolve()
    assert len(gitx.head(tmp_path)) == 40
    (tmp_path / "first.txt").write_text("changed", encoding="utf-8")
    (tmp_path / "space name.txt").write_text("new", encoding="utf-8")
    (tmp_path / ".spec").mkdir()
    (tmp_path / ".spec" / "state").write_text("local", encoding="utf-8")
    assert gitx.dirty_paths(tmp_path) == ["first.txt", "space name.txt"]


def test_inherited_git_environment_does_not_select_another_repository(tmp_path, monkeypatch):
    original = dict(os.environ)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        monkeypatch.setenv(name, str(tmp_path / "foreign"))
    cleaned = gitx.clean_env()
    assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"} & cleaned.keys()
    assert os.environ != original  # Reading a clean copy must not mutate the caller.
    gitx.git(tmp_path, "init", "-q")
    assert gitx.toplevel(tmp_path) == tmp_path.resolve()


def test_unchecked_failure_is_returned_and_checked_failure_raises(tmp_path):
    assert gitx.git(tmp_path, "rev-parse", "HEAD", check=False).returncode != 0
    with pytest.raises(Exception):
        gitx.git(tmp_path, "rev-parse", "HEAD")

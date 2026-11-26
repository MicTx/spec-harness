"""Git tree identity uses a private index and hashes only changed content."""

from pathlib import Path

import gitx
import package

from tests.v3_support import git, make_package, repo


def test_clean_product_id_is_the_git_tree_even_with_package_files(tmp_path):
    repo(tmp_path)
    contract = tmp_path / ".spec" / "architecture" / "contracts.json"
    contract.parent.mkdir(parents=True)
    contract.write_text('{"version": 1}\n')
    git(tmp_path, "add", str(contract.relative_to(tmp_path)))
    git(tmp_path, "commit", "-qm", "contract")
    expected = gitx.stdout(tmp_path, "rev-parse", "HEAD^{tree}")
    directory = make_package(tmp_path)
    git(tmp_path, "add", str(directory.relative_to(tmp_path)))
    assert package.tree_fingerprint(tmp_path) == expected


def test_working_tree_id_leaves_current_index_and_git_objects_unchanged(tmp_path):
    repo(tmp_path)
    source = tmp_path / "app.py"
    source.write_text("VALUE = 'staged'\n")
    git(tmp_path, "add", "app.py")
    index = (tmp_path / ".git" / "index").read_bytes()
    source.write_text("VALUE = 'working'\n")
    extra = tmp_path / "[fresh] file.txt"
    extra.write_text("only the private object store may hold these contents\n")
    object_id = gitx.stdout(tmp_path, "hash-object", str(extra))

    tree = package.tree_fingerprint(tmp_path)

    assert (tmp_path / ".git" / "index").read_bytes() == index
    assert gitx.git(tmp_path, "cat-file", "-e", object_id, check=False).returncode != 0
    git(tmp_path, "--literal-pathspecs", "add", "app.py", extra.name)
    assert tree == gitx.stdout(tmp_path, "write-tree")


def test_only_dirty_product_paths_are_staged_in_the_snapshot(tmp_path, monkeypatch):
    repo(tmp_path)
    (tmp_path / "unchanged.txt").write_text("unchanged\n")
    git(tmp_path, "add", "unchanged.txt")
    git(tmp_path, "commit", "-qm", "unrelated source")
    (tmp_path / "app.py").write_text("VALUE = 12\n")
    make_package(tmp_path)
    commands = []
    real = gitx.git

    def capture(root, *args, **kwargs):
        if "add" in args:
            commands.append(kwargs.get("input"))
        return real(root, *args, **kwargs)

    monkeypatch.setattr(gitx, "git", capture)
    assert package.tree_fingerprint(tmp_path) != gitx.stdout(tmp_path, "rev-parse", "HEAD^{tree}")
    assert commands == [b"app.py\0"]


def test_product_tree_is_not_hidden_by_materialized_skip_worktree(tmp_path):
    repo(tmp_path)
    git(tmp_path, "update-index", "--skip-worktree", "app.py")
    before = package.tree_fingerprint(tmp_path)
    (tmp_path / "app.py").write_text("VALUE = 12\n")
    assert package.tree_fingerprint(tmp_path) != before
    assert gitx.stdout(tmp_path, "ls-files", "-v", "app.py") == "S app.py"


def test_private_copy_preserves_racy_git_check_timestamp(tmp_path):
    repo(tmp_path)
    stamp = (tmp_path / ".git" / "index").stat().st_mtime_ns
    with gitx.temporary_index(tmp_path) as env:
        assert Path(env["GIT_INDEX_FILE"]).stat().st_mtime_ns == stamp

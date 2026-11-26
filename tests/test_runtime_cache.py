"""Pin the runtime hot-path caches: parse caches, fingerprint freshness, archive index, batched git."""

from __future__ import annotations

import package

from tests.v3_support import SLUG, repo, started_package


def test_pkg_cache_runs_parse_invalidation(tmp_path):
    repo(tmp_path)
    pkg = started_package(tmp_path)
    package.append_run(pkg, {"kind": "run", "task": "task-value", "exit": 0})
    assert any(record.get("task") == "task-value" for record in package.runs(pkg))
    package.append_run(pkg, {"kind": "run", "task": "task-value", "exit": 1})
    assert package.latest_runs(pkg)["task-value"]["exit"] == 1


def test_pkg_cache_read_package_snapshot(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    directory, spec, _tasks = package.read_package(tmp_path, SLUG)
    (directory / "spec.md").write_text(spec + "\n- extra: 1\n", encoding="utf-8")
    assert package.read_package(tmp_path, SLUG)[1] != spec


def test_pkg_cache_fingerprint_content_freshness(tmp_path, monkeypatch):
    repo(tmp_path)
    calls = {"n": 0}
    real = package.refresh_product_tree

    def counted(root, *args):
        calls["n"] += 1
        return real(root, *args)

    monkeypatch.setattr(package, "refresh_product_tree", counted)
    first = package.tree_fingerprint(tmp_path)
    assert package.tree_fingerprint(tmp_path) == first
    # Each call reflects its own tree snapshot; a cross-call status memo must
    # not hide a rewrite whose porcelain status entry is unchanged.
    assert calls["n"] == 2
    (tmp_path / "untracked.txt").write_text("change\n", encoding="utf-8")
    changed = package.tree_fingerprint(tmp_path)
    assert changed != first
    assert calls["n"] == 3
    (tmp_path / "untracked.txt").write_text("change again\n", encoding="utf-8")
    assert package.tree_fingerprint(tmp_path) != changed
    assert calls["n"] == 4


def test_gate_shares_one_hash(tmp_path, monkeypatch):
    import gate

    repo(tmp_path)
    started_package(tmp_path)
    calls = {"n": 0}
    real = package.refresh_product_tree

    def counted(root, *args):
        calls["n"] += 1
        return real(root, *args)

    monkeypatch.setattr(package, "refresh_product_tree", counted)
    gate.compute(tmp_path, SLUG)
    assert calls["n"] == 1


def test_archive_index(tmp_path, monkeypatch):
    repo(tmp_path)
    archive = tmp_path / ".spec/specs/archive"
    archive.mkdir(parents=True)
    for slug, branch in (("2026-01-01_a", "spec/old-a"), ("2026-01-02_b", "spec/old-b")):
        (archive / slug).mkdir()
        (archive / slug / "spec.md").write_text(f"## Binding\n- branch: `{branch}`\n", encoding="utf-8")
        (archive / slug / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
    scans = {"n": 0}
    real = package._scan_archived_branches

    def counted(root):
        scans["n"] += 1
        return real(root)

    monkeypatch.setattr(package, "_scan_archived_branches", counted)
    index = package.archived_branch_index(tmp_path)
    assert index == {"2026-01-01_a": "spec/old-a", "2026-01-02_b": "spec/old-b"}
    assert package.archived_branch_index(tmp_path)["2026-01-02_b"] == "spec/old-b"
    assert scans["n"] == 1
    assert (tmp_path / ".spec" / "cache" / "archive-index.json").is_file()


def test_git_add_batching(tmp_path, monkeypatch):
    import gitx

    repo(tmp_path)
    started_package(tmp_path)
    extra = tmp_path / "extra"
    extra.mkdir()
    for index in range(3):
        (extra / f"f{index}.txt").write_text("x\n", encoding="utf-8")
    ignore_calls = {"n": 0}
    real = gitx.git

    def counted(root, *args, **kwargs):
        if "check-ignore" in args:
            ignore_calls["n"] += 1
        return real(root, *args, **kwargs)

    monkeypatch.setattr(gitx, "git", counted)
    package.git_add_paths(tmp_path, ["extra/**"])
    assert ignore_calls["n"] <= 1
    staged = gitx.git(tmp_path, "diff", "--cached", "--name-only").stdout.split()
    assert sorted(staged) == ["extra/f0.txt", "extra/f1.txt", "extra/f2.txt"]

"""Safe adoption of in-flight work (task-team-adoption).

Real temporary Git repositories. Adoption is an explicit, separate path from
`/spec:new`: it binds a package to the CURRENT non-protected branch and records
the dirty baseline, without ever switching branch, stashing, committing,
resetting, or touching a teammate's files. Anything unprovable is refused with
zero writes. The default `new` gate stays strict (pinned in test_init_spec_package).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "init_spec_package.py"
SLUG = "2026-10-23_add-export-suite"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def adopt(repo, *extra, slug=SLUG, confirm=True):
    args = [sys.executable, str(SCRIPT), "--root", str(repo), "--slug", slug, "--title", "Export suite", "--adopt"]
    if confirm:
        args += ["--confirm-adopt"]
    args += list(extra)
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "config", "commit.gpgsign", "false")
    (root / "app.py").write_text("print('v1')\n")
    (root / "util.py").write_text("x = 1\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "init")
    git(root, "checkout", "-q", "-b", "feature/export")
    return root


def dirty(repo):
    (repo / "app.py").write_text("print('teammate edit')\n")
    (repo / "staged.py").write_text("y = 2\n")
    git(repo, "add", "staged.py")
    (repo / "untracked.txt").write_text("scratch\n")


def worktree_bytes(repo):
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in sorted(repo.rglob("*"))
        if path.is_file() and ".git" not in path.parts and ".spec" not in path.parts
    }


def snapshot(repo):
    return {
        "branch": git(repo, "branch", "--show-current").strip(),
        "head": git(repo, "rev-parse", "HEAD").strip(),
        "index": git(repo, "diff", "--cached", "--binary"),
        "stash": git(repo, "stash", "list"),
        "files": worktree_bytes(repo),
        "branches": git(repo, "branch", "--list"),
    }


def test_adoption_preserves_branch_index_and_dirty_bytes(repo):
    dirty(repo)
    before = snapshot(repo)
    completed = adopt(repo)
    assert completed.returncode == 0, completed.stderr
    assert snapshot(repo) == before, "adoption must not move or rewrite anything outside .spec"
    assert (repo / ".spec/specs" / SLUG / "spec.md").is_file()
    assert (repo / ".spec/specs" / SLUG / "tasks.md").is_file()


def test_adopted_package_binds_the_current_branch(repo):
    dirty(repo)
    adopt(repo)
    spec = (repo / ".spec/specs" / SLUG / "spec.md").read_text()
    assert "Git integration branch：`feature/export`" in spec


def test_baseline_is_recorded_with_hashes_not_contents(repo):
    dirty(repo)
    adopt(repo)
    baseline = json.loads((repo / ".spec/specs" / SLUG / "adoption.json").read_text())
    assert baseline["branch"] == "feature/export"
    assert baseline["head"] == git(repo, "rev-parse", "HEAD").strip()
    paths = {entry["path"] for entry in baseline["dirty"]}
    assert {"app.py", "staged.py", "untracked.txt"} <= paths
    raw = (repo / ".spec/specs" / SLUG / "adoption.json").read_text()
    assert "teammate edit" not in raw and "scratch" not in raw
    expected = hashlib.sha256((repo / "untracked.txt").read_bytes()).hexdigest()
    assert next(e for e in baseline["dirty"] if e["path"] == "untracked.txt")["sha256"] == expected


def test_default_new_still_refuses_a_dirty_tree(repo):
    dirty(repo)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), "--slug", SLUG, "--title", "x"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode != 0
    assert "working tree is not clean" in completed.stderr
    assert not (repo / ".spec/specs" / SLUG).exists()


def test_adoption_requires_explicit_confirmation(repo):
    dirty(repo)
    before = snapshot(repo)
    completed = adopt(repo, confirm=False)
    assert completed.returncode != 0
    assert "--confirm-adopt" in completed.stderr
    assert snapshot(repo) == before
    assert not (repo / ".spec").exists()


@pytest.mark.parametrize("branch", ["main", "master", "release/1.0", "hotfix/x", "develop"])
def test_protected_branches_are_refused(repo, branch):
    dirty(repo)
    git(repo, "stash", "-q", "-u")
    git(repo, "checkout", "-q", "-B", branch)
    git(repo, "stash", "pop", "-q")
    before = snapshot(repo)
    completed = adopt(repo)
    assert completed.returncode != 0
    assert "protected" in completed.stderr
    assert snapshot(repo) == before
    assert not (repo / ".spec").exists()


def test_detached_head_is_refused(repo):
    dirty(repo)
    git(repo, "checkout", "-q", "--detach")
    completed = adopt(repo)
    assert completed.returncode != 0 and "branch" in completed.stderr
    assert not (repo / ".spec").exists()


def test_branch_already_bound_to_another_package_is_refused(repo):
    other = repo / ".spec/specs/2026-10-22_add-other"
    other.mkdir(parents=True)
    (other / "spec.md").write_text("# t\n- Git integration branch：`feature/export`\n")
    (other / "tasks.md").write_text("# t\n")
    git(repo, "add", ".spec")
    git(repo, "commit", "-qm", "other package")
    dirty(repo)
    completed = adopt(repo)
    assert completed.returncode != 0 and "already bound" in completed.stderr
    assert not (repo / ".spec/specs" / SLUG).exists()


def test_ambiguous_ownership_with_foreign_active_package_is_refused(repo):
    other = repo / ".spec/specs/2026-10-22_add-other"
    other.mkdir(parents=True)
    (other / "spec.md").write_text("# t\n- Git integration branch：`spec/2026-10-22_add-other`\n")
    (other / "tasks.md").write_text("# t\n")
    git(repo, "add", ".spec")
    git(repo, "commit", "-qm", "other package")
    dirty(repo)
    completed = adopt(repo)
    assert completed.returncode != 0 and "active package" in completed.stderr
    assert not (repo / ".spec/specs" / SLUG).exists()


def test_existing_package_slug_is_never_overwritten(repo):
    dirty(repo)
    assert adopt(repo).returncode == 0
    first = (repo / ".spec/specs" / SLUG / "spec.md").read_bytes()
    completed = adopt(repo)
    assert completed.returncode != 0
    assert (repo / ".spec/specs" / SLUG / "spec.md").read_bytes() == first


def test_concurrent_adoptions_create_exactly_one_package(repo):
    dirty(repo)
    results = []

    def run(slug):
        results.append(adopt(repo, slug=slug).returncode)

    slugs = ["2026-10-23_add-first-run", "2026-10-23_add-second-run"]
    threads = [threading.Thread(target=run, args=(slug,)) for slug in slugs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    packages = [p for p in (repo / ".spec/specs").iterdir() if p.is_dir() and p.name != "archive"]
    assert len(packages) == 1, [p.name for p in packages]
    assert sorted(results) == [0, 1]
    assert not list((repo / ".spec/specs").glob("**/*.tmp"))


def test_adoption_does_not_modify_teammate_owned_files(repo):
    dirty(repo)
    teammate = {name: (repo / name).read_bytes() for name in ("app.py", "staged.py", "untracked.txt")}
    adopt(repo)
    for name, content in teammate.items():
        assert (repo / name).read_bytes() == content


def test_adoption_makes_no_git_mutation(repo):
    dirty(repo)
    reflog_before = git(repo, "reflog")
    adopt(repo)
    assert git(repo, "reflog") == reflog_before
    assert git(repo, "stash", "list") == ""
    assert git(repo, "log", "--oneline").count("\n") == 1


def test_non_git_directory_is_refused(tmp_path):
    completed = adopt(tmp_path)
    assert completed.returncode != 0 and "git" in completed.stderr.lower()
    assert not (tmp_path / ".spec").exists()


def test_existing_teammate_identities_are_referenced_not_recreated(repo):
    dirty(repo)
    completed = adopt(repo, "--teammate", "impl-1", "--teammate", "impl-2")
    assert completed.returncode == 0, completed.stderr
    baseline = json.loads((repo / ".spec/specs" / SLUG / "adoption.json").read_text())
    assert baseline["teammates"] == ["impl-1", "impl-2"]
    assert baseline["coordinator"] == "main-session"


def test_teammate_names_are_validated(repo):
    dirty(repo)
    completed = adopt(repo, "--teammate", "../../etc/passwd")
    assert completed.returncode != 0
    assert not (repo / ".spec/specs" / SLUG).exists()


def test_running_writers_are_not_assumed_stopped(repo):
    """Without an explicit confirmation of a pause/handoff the output must not claim safety."""
    dirty(repo)
    completed = adopt(repo, "--teammate", "impl-1")
    assert completed.returncode == 0
    out = completed.stdout.lower()
    assert "pause" in out or "handoff" in out
    assert "safely adopted" not in out
    baseline = json.loads((repo / ".spec/specs" / SLUG / "adoption.json").read_text())
    assert baseline["writers_paused"] is False


def test_confirmed_pause_is_recorded_as_a_claim_not_a_fact(repo):
    dirty(repo)
    completed = adopt(repo, "--teammate", "impl-1", "--writers-paused")
    assert completed.returncode == 0
    baseline = json.loads((repo / ".spec/specs" / SLUG / "adoption.json").read_text())
    assert baseline["writers_paused"] is True and baseline["writers_paused_source"] == "operator-attested"


# --- the lock heals: a killed adopter must not block every later adoption ------------


def plant_lock(repo, pid):
    lock = repo / ".spec" / ".adopt.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text(str(pid), encoding="utf-8")
    return lock


def dead_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_a_dead_holders_lock_is_reclaimed(repo):
    dirty(repo)
    plant_lock(repo, dead_pid())
    completed = adopt(repo)
    assert completed.returncode == 0, completed.stderr
    assert (repo / ".spec/specs" / SLUG / "spec.md").is_file()
    assert not (repo / ".spec" / ".adopt.lock").exists()


def test_a_live_holders_lock_is_respected(repo):
    import os

    dirty(repo)
    plant_lock(repo, os.getpid())
    before = snapshot(repo)
    completed = adopt(repo)
    assert completed.returncode != 0 and "in progress" in completed.stderr
    assert snapshot(repo) == before
    assert not (repo / ".spec/specs" / SLUG).exists()
    assert (repo / ".spec" / ".adopt.lock").exists(), "a live holder's lock must not be removed"


def test_a_lock_with_no_owner_record_is_not_silently_stolen(repo):
    dirty(repo)
    lock = repo / ".spec" / ".adopt.lock"
    lock.mkdir(parents=True)
    completed = adopt(repo)
    assert completed.returncode != 0 and "in progress" in completed.stderr
    assert lock.exists()


def test_the_lock_is_released_after_a_refusal(repo):
    dirty(repo)
    git(repo, "checkout", "-q", "-B", "main")
    assert adopt(repo).returncode != 0
    assert not (repo / ".spec" / ".adopt.lock").exists()


# --- review findings, each reproduced before being fixed -----------------------------


def test_adoption_does_not_rewrite_the_git_index_file(repo):
    """`git status` may refresh and rewrite .git/index; adoption must not touch it."""
    import os
    import time

    dirty(repo)
    index = repo / ".git" / "index"
    time.sleep(0.05)
    os.utime(repo / "app.py", None)  # stale stat info: a plain `git status` would refresh the index
    before_bytes, before_mtime = index.read_bytes(), index.stat().st_mtime_ns
    assert adopt(repo).returncode == 0
    assert index.read_bytes() == before_bytes and index.stat().st_mtime_ns == before_mtime


def test_adoption_still_works_while_an_index_lock_is_held_by_a_teammate(repo):
    dirty(repo)
    (repo / ".git" / "index.lock").write_text("")
    completed = adopt(repo)
    assert completed.returncode == 0, completed.stderr
    assert (repo / ".git" / "index.lock").exists(), "a teammate's lock must survive"


def test_a_staged_rename_into_business_code_is_recorded_in_the_baseline(repo):
    (repo / ".spec").mkdir()
    (repo / ".spec" / "note.md").write_text("doc\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "note")
    git(repo, "mv", ".spec/note.md", "moved.py")
    assert adopt(repo).returncode == 0
    baseline = json.loads((repo / ".spec/specs" / SLUG / "adoption.json").read_text())
    assert "moved.py" in {entry["path"] for entry in baseline["dirty"]}


# --- review: the lock must not stay stuck after a crash, nor be stolen from a new holder ---


def test_an_ownerless_lock_older_than_the_grace_window_is_reclaimed(repo):
    import os
    import time

    dirty(repo)
    lock = repo / ".spec" / ".adopt.lock"
    lock.mkdir(parents=True)  # crash between mkdir and writing the owner record
    old = time.time() - 600
    os.utime(lock, (old, old))
    completed = adopt(repo)
    assert completed.returncode == 0, completed.stderr
    assert (repo / ".spec/specs" / SLUG / "spec.md").is_file()


def test_a_fresh_ownerless_lock_is_not_stolen(repo):
    dirty(repo)
    lock = repo / ".spec" / ".adopt.lock"
    lock.mkdir(parents=True)  # just created: its holder may be about to write the owner record
    completed = adopt(repo)
    assert completed.returncode != 0 and "in progress" in completed.stderr
    assert lock.exists()


def test_reclaiming_never_removes_a_lock_that_changed_hands(repo, monkeypatch):
    """A dead holder's lock is replaced between our read and our removal: we must not delete the new one."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("init_spec_package_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    spec.loader.exec_module(module)

    lock = repo / ".spec" / ".adopt.lock"
    lock.mkdir(parents=True)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (lock / "owner").write_text(str(dead.pid), encoding="utf-8")

    real_alive = module._pid_alive

    def swap_in_a_live_holder(pid):
        alive = real_alive(pid)
        # between "read the dead owner" and "remove it", another adopter takes the lock
        (lock / "owner").write_text(str(__import__("os").getpid()), encoding="utf-8")
        return alive

    monkeypatch.setattr(module, "_pid_alive", swap_in_a_live_holder)
    with pytest.raises(module.AdoptionError):
        module._acquire_adoption_lock(lock)
    assert (lock / "owner").read_text(encoding="utf-8") == str(__import__("os").getpid()), (
        "a live holder's lock was deleted"
    )


def test_a_crash_leftover_staging_directory_does_not_block_the_next_adoption(repo):
    dirty(repo)
    specs = repo / ".spec" / "specs"
    specs.mkdir(parents=True)
    leftover = specs / f".{SLUG}.adopt-12345"
    (leftover / "sub").mkdir(parents=True)
    (leftover / "sub" / "x").write_text("junk")
    completed = adopt(repo)
    assert completed.returncode == 0, completed.stderr
    assert (specs / SLUG / "spec.md").is_file()


def test_a_huge_dirty_file_is_recorded_without_hashing_its_whole_content(repo):
    dirty(repo)
    big = repo / "big.bin"
    with big.open("wb") as handle:
        handle.truncate(300 * 1024 * 1024)  # sparse: cheap to create, expensive to read if we tried
    completed = adopt(repo)
    assert completed.returncode == 0, completed.stderr
    baseline = json.loads((repo / ".spec/specs" / SLUG / "adoption.json").read_text())
    entry = next(e for e in baseline["dirty"] if e["path"] == "big.bin")
    assert entry["sha256"] == "" and entry.get("skipped") == "too_large"


# --- a path git reports in a non-UTF-8 encoding must not crash the baseline ----------


def _git_returning(monkeypatch, module, payload: bytes):
    """Stand in for git so the test does not depend on a filesystem that can hold such a name."""
    real = subprocess.run

    def fake(args, *a, **kw):
        if isinstance(args, (list, tuple)) and "status" in args:
            return subprocess.CompletedProcess(args, 0, stdout=payload, stderr=b"")
        return real(args, *a, **kw)

    monkeypatch.setattr(module.subprocess, "run", fake)


def test_baseline_survives_a_non_utf8_path(repo, monkeypatch):
    import importlib.util

    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("init_pkg_nonutf8", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _git_returning(monkeypatch, module, b"?? bad\xff.txt\0?? ok.txt\0")
    entries = module._dirty_baseline(repo)
    assert {e["path"] for e in entries} >= {"ok.txt"} and len(entries) == 2
    assert all(isinstance(e["path"], str) for e in entries)
    json.dumps(entries)  # the record must be serialisable


def test_dirty_check_treats_unreadable_output_as_dirty_not_a_crash(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import assess_spec_activation as policy

    real = subprocess.run

    def fake(args, *a, **kw):
        if isinstance(args, (list, tuple)) and "status" in args:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return real(args, *a, **kw)

    monkeypatch.setattr(policy.subprocess, "run", fake)
    assert policy._worktree_dirty(tmp_path) is True

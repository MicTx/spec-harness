"""Snapshot ownership, two-file transaction fences, and conflict-safe rollback."""

import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_issue_writeback as writeback  # noqa: E402

from tests.test_check_issue_writeback import (  # noqa: E402
    SLUG,
    SPEC,
    TASKS,
    _apply,
    _make_package,
    _plan,
    _seed_plan,
    _snapshot,
    _write_gaps,
)


def apply_in_process(root, plan):
    return writeback.main(["apply", "--root", str(root), "--slug", SLUG, "--plan", str(plan)])


@pytest.mark.parametrize("name", ["spec.md", "tasks.md"])
def test_stale_byte_snapshot_never_overwrites_changes(tmp_path, name):
    package = _make_package(tmp_path)
    plan, payload = _seed_plan(tmp_path, package, ["fix a gap"])
    assert payload["version"] == 2
    assert payload["snapshot"]["files"][name]["sha256"] == hashlib.sha256((package / name).read_bytes()).hexdigest()
    with (package / name).open("ab") as stream:
        stream.write(b"\nconcurrent edit\n")
    before = _snapshot(package)
    result = _apply(tmp_path, plan)
    assert result.returncode == 2 and "snapshot" in result.stderr
    assert _snapshot(package) == before
    assert not (package / writeback.TRANSACTION_NAME).exists()


def test_copied_tree_cannot_replay_foreign_snapshot(tmp_path):
    root = tmp_path / "first"
    package = _make_package(root)
    plan, _ = _seed_plan(root, package, ["fix a gap"])
    second = tmp_path / "copied"
    shutil.copytree(root, second)
    copied_package = second / ".spec/specs" / SLUG
    before = _snapshot(copied_package)
    result = _apply(second, second / plan.name)
    assert result.returncode == 2 and "foreign" in result.stderr
    assert _snapshot(copied_package) == before


def test_same_path_replaced_directory_cannot_replay_snapshot(tmp_path):
    package = _make_package(tmp_path)
    plan, _ = _seed_plan(tmp_path, package, ["fix a gap"])
    old = package.with_name("old")
    package.rename(old)
    shutil.copytree(old, package)
    before = _snapshot(package)
    result = _apply(tmp_path, plan)
    assert result.returncode == 2 and "snapshot" in result.stderr
    assert _snapshot(package) == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory"])
def test_unsafe_documents_are_refused_without_touching_sentinel(tmp_path, kind):
    package = _make_package(tmp_path)
    plan, _ = _seed_plan(tmp_path, package, ["fix a gap"])
    sentinel = tmp_path / "sentinel"
    sentinel.write_text(TASKS)
    target = package / "tasks.md"
    target.unlink()
    if kind == "symlink":
        target.symlink_to(sentinel)
    elif kind == "hardlink":
        os.link(sentinel, target)
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        target.mkdir()
    original_spec = (package / "spec.md").read_bytes()
    result = _apply(tmp_path, plan)
    assert result.returncode == 2 and "Traceback" not in result.stderr
    assert sentinel.read_text() == TASKS
    assert (package / "spec.md").read_bytes() == original_spec
    assert not (package / writeback.TRANSACTION_NAME).exists()


def test_symlink_package_ancestor_is_refused(tmp_path):
    package = _make_package(tmp_path)
    plan, _ = _seed_plan(tmp_path, package, ["fix a gap"])
    specs = package.parent
    real = specs.with_name("real-specs")
    specs.rename(real)
    specs.symlink_to(real, target_is_directory=True)
    before = _snapshot(package)
    result = _apply(tmp_path, plan)
    assert result.returncode == 2
    assert _snapshot(package) == before


def test_live_flock_is_not_unlinked_or_bypassed(tmp_path):
    if writeback.fcntl is None:
        pytest.skip("flock unavailable")
    package = _make_package(tmp_path)
    plan, _ = _seed_plan(tmp_path, package, ["fix a gap"])
    lock = package / writeback.LOCK_NAME
    with lock.open("a+b") as stream:
        writeback.fcntl.flock(stream, writeback.fcntl.LOCK_EX | writeback.fcntl.LOCK_NB)
        inode = lock.stat().st_ino
        before = _snapshot(package)
        result = _apply(tmp_path, plan)
        assert result.returncode == 2 and "locked" in result.stderr
        assert lock.stat().st_ino == inode
        assert _snapshot(package) == before
    assert _apply(tmp_path, plan).returncode == 0


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_lock_is_refused(tmp_path, kind):
    package = _make_package(tmp_path)
    plan, _ = _seed_plan(tmp_path, package, ["fix a gap"])
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"never truncate")
    lock = package / writeback.LOCK_NAME
    if kind == "symlink":
        lock.symlink_to(sentinel)
    elif kind == "hardlink":
        os.link(sentinel, lock)
    else:
        os.mkfifo(lock)
    before = _snapshot(package)
    result = _apply(tmp_path, plan)
    assert result.returncode == 2 and "Traceback" not in result.stderr
    assert _snapshot(package) == before and sentinel.read_bytes() == b"never truncate"


def test_second_replace_failure_rolls_back_pair_and_preserves_mode(tmp_path, monkeypatch):
    package = _make_package(tmp_path)
    for name in writeback.DOCUMENT_NAMES:
        (package / name).chmod(0o600)
    plan, _ = _seed_plan(tmp_path, package, ["fix a gap"])
    before = _snapshot(package)
    real_replace, failed = writeback.os.replace, False

    def fail_once(source, destination, **kwargs):
        nonlocal failed
        if destination == "spec.md" and not failed:
            failed = True
            raise OSError("injected second replace failure")
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(writeback.os, "replace", fail_once)
    assert apply_in_process(tmp_path, plan) == 2
    assert failed and _snapshot(package) == before
    assert not (package / writeback.TRANSACTION_NAME).exists()
    assert not list(package.glob(".writeback-*.tmp"))
    assert all(stat.S_IMODE((package / name).stat().st_mode) == 0o600 for name in writeback.DOCUMENT_NAMES)


@pytest.mark.parametrize("conflict_kind", ["bytes", "same-bytes-new-inode"])
def test_postrename_conflict_is_never_claimed_for_rollback(tmp_path, monkeypatch, conflict_kind):
    package = _make_package(tmp_path)
    plan, _ = _seed_plan(tmp_path, package, ["fix a gap"])
    before = _snapshot(package)
    real_replace = writeback.os.replace
    conflicting = None

    def replace_then_conflict(source, destination, **kwargs):
        nonlocal conflicting
        real_replace(source, destination, **kwargs)
        if destination == "tasks.md" and conflicting is None:
            target = package / "tasks.md"
            if conflict_kind == "bytes":
                target.write_bytes(b"outside writer owns this content\n")
            else:
                foreign = package / "foreign-document"
                foreign.write_bytes(target.read_bytes())
                real_replace(foreign, target)
            conflicting = target.read_bytes()

    monkeypatch.setattr(writeback.os, "replace", replace_then_conflict)
    assert apply_in_process(tmp_path, plan) == 2
    assert conflicting is not None
    assert (package / "tasks.md").read_bytes() == conflicting
    assert (package / "spec.md").read_bytes() == before["spec.md"]
    assert (package / writeback.TRANSACTION_NAME).is_file()


def test_oversized_update_is_refused_before_replacement(tmp_path):
    package = _make_package(tmp_path)
    before = _snapshot(package)
    with writeback._package_handle(tmp_path, package) as descriptor:
        expected = writeback._read_document(descriptor, "tasks.md")[1]
        with pytest.raises(writeback.WritebackError, match="update exceeds"):
            writeback._replace_document(descriptor, "tasks.md", b"x" * (writeback.MAX_FILE_BYTES + 1), expected)
    assert _snapshot(package) == before
    assert not list(package.glob(".writeback-*.tmp"))


def test_crash_mixed_state_retains_durable_marker_and_refuses_further_writes(tmp_path, monkeypatch):
    package = _make_package(tmp_path)
    plan, _ = _seed_plan(tmp_path, package, ["fix a gap"])
    before = _snapshot(package)
    replace = writeback._replace_document

    def crash_after_first(*args, **kwargs):
        replace(*args, **kwargs)
        raise KeyboardInterrupt("simulated hard interruption after first durable rename")

    with monkeypatch.context() as patch:
        patch.setattr(writeback, "_replace_document", crash_after_first)
        with pytest.raises(KeyboardInterrupt):
            apply_in_process(tmp_path, plan)
    marker_path = package / writeback.TRANSACTION_NAME
    marker = json.loads(marker_path.read_text())
    assert (package / "spec.md").read_bytes() == before["spec.md"]
    assert (package / "tasks.md").read_bytes() != before["tasks.md"]
    assert stat.S_IMODE(marker_path.stat().st_mode) == 0o600
    backup = Path(marker["backups"])
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    assert all((backup / name).read_bytes() == before[name] for name in writeback.DOCUMENT_NAMES)
    assert all(stat.S_IMODE((backup / name).stat().st_mode) == 0o600 for name in writeback.DOCUMENT_NAMES)
    mixed = _snapshot(package)
    for result in (_apply(tmp_path, plan), _plan(tmp_path, _write_gaps(tmp_path, []))):
        assert result.returncode == 2 and "unfinished writeback transaction" in result.stderr
    assert _snapshot(package) == mixed
    # Only an explicit operator restoration of BOTH files plus marker removal
    # re-enables planning. The command performs no hidden roll-forward/kill.
    for name in writeback.DOCUMENT_NAMES:
        (package / name).write_bytes((backup / name).read_bytes())
    marker_path.unlink()
    new_plan, _ = _seed_plan(tmp_path, package, ["fix a gap"])
    assert _apply(tmp_path, new_plan).returncode == 0


def test_rollback_never_overwrites_uncooperative_postwrite_edit(tmp_path, monkeypatch):
    package = _make_package(tmp_path)
    plan, _ = _seed_plan(tmp_path, package, ["fix a gap"])
    conflict = b"outside writer owns this content\n"

    def gate(*_args):
        (package / "tasks.md").write_bytes(conflict)
        raise writeback.WritebackError("injected concurrent edit")

    monkeypatch.setattr(writeback, "_gate_converged", gate)
    assert apply_in_process(tmp_path, plan) == 2
    assert (package / "tasks.md").read_bytes() == conflict
    assert (package / writeback.TRANSACTION_NAME).is_file()
    result = _apply(tmp_path, plan)
    assert result.returncode == 2 and "unfinished" in result.stderr


def test_marker_blocks_even_when_both_documents_happen_to_match(tmp_path):
    package = _make_package(tmp_path)
    plan, _ = _seed_plan(tmp_path, package, ["fix a gap"])
    (package / writeback.TRANSACTION_NAME).write_text("partial or corrupt marker")
    before = _snapshot(package)
    result = _apply(tmp_path, plan)
    assert result.returncode == 2 and "unfinished" in result.stderr
    assert _snapshot(package) == before


@pytest.mark.parametrize(
    "gaps,next_action", [([], "check-green"), (["same pending gap"], "run"), (["new gap"], "escalate")]
)
def test_round_cap_only_limits_new_writeback(tmp_path, gaps, next_action):
    base = writeback.weak_task_id("same pending gap")
    rows = "\n".join(
        f"| {i} | check 回写钩子 plan/apply | FAIL | new output: fixture (2026-01-01) |" for i in range(1, 4)
    )
    spec = SPEC.replace(
        "- none.", "| round | approach | result | new information |\n| --- | --- | --- | --- |\n" + rows
    )
    package = _make_package(tmp_path, spec, TASKS + f"\n- [ ] pending\n  - id: {base}\n")
    before = _snapshot(package)
    result = _plan(tmp_path, _write_gaps(tmp_path, gaps))
    assert result.returncode == (3 if next_action == "escalate" else 0), result.stderr
    assert json.loads(result.stdout)["next"] == next_action
    assert _snapshot(package) == before


def test_version_one_plan_is_explicitly_rejected(tmp_path):
    package = _make_package(tmp_path)
    plan, payload = _seed_plan(tmp_path, package, ["fix a gap"])
    payload["version"] = 1
    plan.write_text(json.dumps(payload))
    before = _snapshot(package)
    result = _apply(tmp_path, plan)
    assert result.returncode == 2 and "regenerate old plans" in result.stderr
    assert _snapshot(package) == before

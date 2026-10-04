# tests/test_update_checkpoint_support.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for update-checkpoint primitives.

Covers the contract surface required by the update stage: normal
begin->complete, simulated interruption (begin without complete), rollback
snapshot restore, complete-time consistency refusal (checkpoint stays), and
explicit reporting for corrupted checkpoints.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import update_checkpoint_support as checkpoint_module  # noqa: E402
from update_checkpoint_support import (  # noqa: E402
    TRIAD_FILENAMES,
    CheckpointConsistencyError,
    CheckpointCorruptedError,
    CheckpointError,
    CheckpointExistsError,
    CheckpointMissingError,
    begin_update_checkpoint,
    checkpoint_path,
    complete_update_checkpoint,
    detect_unresolved_checkpoints,
    detect_update_checkpoint,
    inspect_update_checkpoint,
    rollback_update_checkpoint,
)

SLUG = "2026-06-12_add-push-stage"


def _write_text(path: Path, content: str) -> None:
    # newline="" keeps \r\n byte-exact; Path.write_text lacks the flag on 3.9.
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def _sample_triad(marker: str) -> dict[str, str]:
    return {
        "spec.md": f"# {marker} - 项目范围\n\n- Git integration branch: `spec/demo`\n",
        "tasks.md": f"# {marker} 任务\n\n- [ ] task one\n- [ ] task two\n",
        "checklist.md": f"# {marker} checklist\n\n**验收结果**：通过\n",
    }


def _write_triad(package: Path, triad: dict[str, str]) -> None:
    for name, content in triad.items():
        _write_text(package / name, content)


def _make_package(root: Path, slug: str = SLUG, *, triad: dict[str, str] | None = None) -> Path:
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True, exist_ok=True)
    _write_triad(package, triad if triad is not None else _sample_triad("initial"))
    return package


def _tamper_checkpoint(package: Path, mutate) -> None:
    path = checkpoint_path(package)
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    _write_text(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")


@pytest.fixture()
def package_dir(tmp_path: Path) -> Path:
    return _make_package(tmp_path)


def test_begin_records_intent_timestamp_and_triad_snapshot(package_dir: Path) -> None:
    triad = _sample_triad("initial")
    checkpoint = begin_update_checkpoint(package_dir, "追加数据导出任务")

    document = json.loads(checkpoint_path(package_dir).read_text(encoding="utf-8"))
    assert set(document) == {"version", "package", "intent", "createdAt", "snapshot", "checksum"}
    assert document["version"] == 1
    assert document["package"] == package_dir.name
    assert document["intent"] == "追加数据导出任务"
    created = datetime.fromisoformat(document["createdAt"])
    assert created.tzinfo is not None and created.utcoffset() is not None
    assert document["snapshot"] == triad
    assert checkpoint.snapshot == triad


def test_begin_then_complete_clears_checkpoint(package_dir: Path) -> None:
    begin_update_checkpoint(package_dir, "追加数据导出任务")
    _write_text(package_dir / "tasks.md", "# updated tasks\n\n- [ ] task one\n- [ ] new task\n")
    assert detect_update_checkpoint(package_dir).state == "active"

    completed = complete_update_checkpoint(package_dir, expected_intent="追加数据导出任务")

    assert completed.intent == "追加数据导出任务"
    assert not checkpoint_path(package_dir).exists()
    assert detect_update_checkpoint(package_dir).state == "none"
    # complete clears the checkpoint; it never reverts the applied update.
    assert "new task" in (package_dir / "tasks.md").read_text(encoding="utf-8")


def test_begin_without_complete_is_detectable_interruption(package_dir: Path) -> None:
    begin_update_checkpoint(package_dir, "调整范围")

    status = detect_update_checkpoint(package_dir)
    assert status.state == "active"
    assert status.exists is True
    assert status.interrupted is True
    assert status.checkpoint is not None
    assert status.checkpoint.intent == "调整范围"

    inspected = inspect_update_checkpoint(package_dir)
    assert inspected.intent == "调整范围"
    assert set(inspected.snapshot) == set(TRIAD_FILENAMES)
    # No complete happened: the checkpoint file must still be on disk.
    assert checkpoint_path(package_dir).is_file()


def test_rollback_restores_modified_and_deleted_members(package_dir: Path) -> None:
    original = _sample_triad("original")
    _write_triad(package_dir, original)
    begin_update_checkpoint(package_dir, "重排任务")
    _write_text(package_dir / "spec.md", "# half-updated\n")
    (package_dir / "tasks.md").unlink()
    _write_text(package_dir / "checklist.md", "")

    restored = rollback_update_checkpoint(package_dir)

    assert restored.intent == "重排任务"
    for name, content in original.items():
        assert (package_dir / name).read_bytes() == content.encode("utf-8")
    assert not checkpoint_path(package_dir).exists()
    assert detect_update_checkpoint(package_dir).state == "none"


def test_rollback_restores_crlf_bytes_exactly(package_dir: Path) -> None:
    triad = _sample_triad("initial")
    triad["spec.md"] = "# 范围\r\n\r\nWindows 风格行尾\r\n必须保留\r\n"
    _write_triad(package_dir, triad)
    begin_update_checkpoint(package_dir, "追加任务")
    _write_text(package_dir / "spec.md", "# rewritten with \n only\n")

    rollback_update_checkpoint(package_dir)

    assert (package_dir / "spec.md").read_bytes() == triad["spec.md"].encode("utf-8")


def test_complete_refuses_to_clear_tampered_checkpoint(package_dir: Path) -> None:
    begin_update_checkpoint(package_dir, "追加任务")
    _tamper_checkpoint(package_dir, lambda document: document["snapshot"].update({"tasks.md": "# forged\n"}))

    with pytest.raises(CheckpointCorruptedError, match="checksum"):
        complete_update_checkpoint(package_dir)

    # Refusal must leave the checkpoint on disk instead of clearing it.
    assert checkpoint_path(package_dir).is_file()
    assert detect_update_checkpoint(package_dir).state == "corrupted"


def test_complete_refuses_on_intent_mismatch(package_dir: Path) -> None:
    begin_update_checkpoint(package_dir, "追加任务")

    with pytest.raises(CheckpointConsistencyError, match="intent"):
        complete_update_checkpoint(package_dir, expected_intent="另一个更新")

    assert checkpoint_path(package_dir).is_file()
    assert detect_update_checkpoint(package_dir).state == "active"


def test_complete_refuses_when_triad_broken_after_update(package_dir: Path) -> None:
    begin_update_checkpoint(package_dir, "追加任务")
    (package_dir / "checklist.md").unlink()

    with pytest.raises(CheckpointConsistencyError, match="checklist.md"):
        complete_update_checkpoint(package_dir)

    assert checkpoint_path(package_dir).is_file()
    # Retained checkpoint keeps rollback available as the recovery path.
    rollback_update_checkpoint(package_dir)
    assert (package_dir / "checklist.md").read_text(encoding="utf-8")


def test_unparsable_checkpoint_fails_explicitly(package_dir: Path) -> None:
    _write_text(checkpoint_path(package_dir), "{ not json")

    status = detect_update_checkpoint(package_dir)
    assert status.state == "corrupted"
    assert status.interrupted is True
    assert status.error  # explicit reason, never a silent pass
    with pytest.raises(CheckpointCorruptedError):
        complete_update_checkpoint(package_dir)
    with pytest.raises(CheckpointCorruptedError):
        rollback_update_checkpoint(package_dir)
    assert checkpoint_path(package_dir).is_file()


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("wrong-version", lambda document: document.update(version=2)),
        ("missing-intent", lambda document: document.pop("intent")),
        ("blank-intent", lambda document: document.update(intent="   ")),
        ("missing-created-at", lambda document: document.pop("createdAt")),
        ("naive-created-at", lambda document: document.update(createdAt="2026-06-12T10:00:00")),
        ("snapshot-not-object", lambda document: document.update(snapshot=["spec.md"])),
        ("missing-snapshot-member", lambda document: document["snapshot"].pop("tasks.md")),
        ("extra-snapshot-member", lambda document: document["snapshot"].update({"notes.md": "x"})),
        ("non-string-snapshot-value", lambda document: document["snapshot"].update({"spec.md": 123})),
        ("missing-checksum", lambda document: document.pop("checksum")),
        ("non-string-checksum", lambda document: document.update(checksum=123)),
        ("checksum-mismatch", lambda document: document.update(intent="forged intent")),
    ],
)
def test_corrupted_checkpoint_fails_explicitly(package_dir: Path, label: str, mutate) -> None:
    begin_update_checkpoint(package_dir, "追加任务")
    _tamper_checkpoint(package_dir, mutate)

    status = detect_update_checkpoint(package_dir)
    assert status.state == "corrupted", status.error
    assert status.error
    with pytest.raises(CheckpointCorruptedError):
        inspect_update_checkpoint(package_dir)
    with pytest.raises(CheckpointCorruptedError):
        rollback_update_checkpoint(package_dir)
    with pytest.raises(CheckpointExistsError):
        begin_update_checkpoint(package_dir, "retry intent")
    assert checkpoint_path(package_dir).is_file()


def test_begin_refuses_to_clobber_unresolved_checkpoint(package_dir: Path) -> None:
    begin_update_checkpoint(package_dir, "first intent")

    with pytest.raises(CheckpointExistsError):
        begin_update_checkpoint(package_dir, "second intent")

    assert inspect_update_checkpoint(package_dir).intent == "first intent"


def test_begin_refuses_incomplete_triad(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    _write_text(package / "spec.md", "# spec\n")

    with pytest.raises(CheckpointError, match="tasks.md"):
        begin_update_checkpoint(package, "追加任务")

    # A failed begin must never leave a partial checkpoint behind.
    assert not checkpoint_path(package).exists()


def test_begin_rejects_blank_intent(package_dir: Path) -> None:
    with pytest.raises(ValueError):
        begin_update_checkpoint(package_dir, "   ")
    assert not checkpoint_path(package_dir).exists()


def test_missing_checkpoint_operations_fail_explicitly(package_dir: Path) -> None:
    with pytest.raises(CheckpointMissingError):
        complete_update_checkpoint(package_dir)
    with pytest.raises(CheckpointMissingError):
        rollback_update_checkpoint(package_dir)
    with pytest.raises(CheckpointMissingError):
        inspect_update_checkpoint(package_dir)
    assert detect_update_checkpoint(package_dir).state == "none"


def test_second_complete_after_complete_fails(package_dir: Path) -> None:
    begin_update_checkpoint(package_dir, "追加任务")
    complete_update_checkpoint(package_dir)
    with pytest.raises(CheckpointMissingError):
        complete_update_checkpoint(package_dir)


def test_begin_again_allowed_after_rollback(package_dir: Path) -> None:
    begin_update_checkpoint(package_dir, "first")
    rollback_update_checkpoint(package_dir)

    second = begin_update_checkpoint(package_dir, "second")

    assert second.intent == "second"
    assert detect_update_checkpoint(package_dir).state == "active"


def test_checkpoint_moved_between_packages_is_refused(tmp_path: Path) -> None:
    source = _make_package(tmp_path, slug="2026-06-11_source-package")
    target = _make_package(tmp_path, slug="2026-06-12_target-package")
    begin_update_checkpoint(source, "追加任务")
    checkpoint_path(source).replace(checkpoint_path(target))

    with pytest.raises(CheckpointConsistencyError, match="moved between packages"):
        complete_update_checkpoint(target)
    with pytest.raises(CheckpointConsistencyError, match="moved between packages"):
        rollback_update_checkpoint(target)
    assert checkpoint_path(target).is_file()
    assert detect_update_checkpoint(source).state == "none"


def test_detect_unresolved_checkpoints_only_reports_interrupted(tmp_path: Path) -> None:
    clean = _make_package(tmp_path, slug="2026-06-11_clean-package")
    interrupted = _make_package(tmp_path, slug="2026-06-12_add-push-stage")
    begin_update_checkpoint(interrupted, "追加任务")

    found = detect_unresolved_checkpoints(tmp_path / ".spec" / "specs")

    assert set(found) == {"2026-06-12_add-push-stage"}
    assert found["2026-06-12_add-push-stage"].state == "active"
    assert detect_update_checkpoint(clean).state == "none"


def test_detect_treats_missing_directory_as_clean(tmp_path: Path) -> None:
    status = detect_update_checkpoint(tmp_path / "missing" / "pkg")
    assert status.state == "none"
    assert status.interrupted is False
    assert detect_unresolved_checkpoints(tmp_path / "missing") == {}


def test_begin_refuses_symlinked_triad_member(package_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    _write_text(outside, "# outside\n")
    (package_dir / "spec.md").unlink()
    (package_dir / "spec.md").symlink_to(outside)

    with pytest.raises(CheckpointError, match="spec.md"):
        begin_update_checkpoint(package_dir, "追加任务")

    assert not checkpoint_path(package_dir).exists()


def test_symlinked_checkpoint_file_is_reported_corrupted(package_dir: Path, tmp_path: Path) -> None:
    begin_update_checkpoint(package_dir, "追加任务")
    real = checkpoint_path(package_dir)
    moved = tmp_path / "moved-checkpoint.json"
    real.replace(moved)
    real.symlink_to(moved)

    status = detect_update_checkpoint(package_dir)

    assert status.state == "corrupted"
    assert status.error
    with pytest.raises(CheckpointCorruptedError):
        inspect_update_checkpoint(package_dir)


@pytest.mark.skipif(os.name != "posix", reason="permission bits are POSIX semantics")
def test_checkpoint_file_is_private(package_dir: Path) -> None:
    begin_update_checkpoint(package_dir, "追加任务")
    assert checkpoint_path(package_dir).stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "posix", reason="permission bits are POSIX semantics")
def test_rollback_preserves_existing_member_mode(package_dir: Path) -> None:
    target = package_dir / "tasks.md"
    target.chmod(0o644)
    begin_update_checkpoint(package_dir, "追加任务")
    _write_text(target, "# replaced\n")

    rollback_update_checkpoint(package_dir)

    assert target.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("damage", ["deep-json", "invalid-utf8", "intent", "snapshot"])
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
def test_malformed_checkpoint_is_corrupted_without_mutation(package_dir: Path, damage: str, surrogate: str) -> None:
    begin_update_checkpoint(package_dir, "追加任务")
    path = checkpoint_path(package_dir)
    if damage == "deep-json":
        path.write_bytes(("[" * 1100 + "0" + "]" * 1100).encode("ascii"))
    elif damage == "invalid-utf8":
        path.write_bytes(b"\xff")
    else:
        document = json.loads(path.read_text(encoding="utf-8"))
        if damage == "intent":
            document["intent"] = surrogate
        else:
            document["snapshot"]["tasks.md"] = surrogate
        # Escaped lone surrogates are legal UTF-8 bytes but not valid snapshot text.
        path.write_text(json.dumps(document, ensure_ascii=True), encoding="utf-8")
    _write_text(package_dir / "spec.md", "# half-applied update\n")
    before = {p.name: p.read_bytes() for p in package_dir.iterdir()}

    status = detect_update_checkpoint(package_dir)

    assert status.state == "corrupted"
    assert status.exists and status.interrupted
    assert status.checkpoint is None and status.error
    status.error.encode("utf-8")
    assert detect_unresolved_checkpoints(package_dir.parent)[package_dir.name].state == "corrupted"
    for operation in (inspect_update_checkpoint, complete_update_checkpoint, rollback_update_checkpoint):
        with pytest.raises(CheckpointCorruptedError):
            operation(package_dir)
        assert {p.name: p.read_bytes() for p in package_dir.iterdir()} == before
    with pytest.raises(CheckpointExistsError):
        begin_update_checkpoint(package_dir, "retry")
    assert {p.name: p.read_bytes() for p in package_dir.iterdir()} == before


@pytest.mark.parametrize("operation", [complete_update_checkpoint, rollback_update_checkpoint])
@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_valid_unicode_checkpoint_round_trips(package_dir: Path, operation, ensure_ascii: bool) -> None:
    text = "中文 café e\u0301 🌞 \U0010ffff\r\n"
    triad = {name: text for name in TRIAD_FILENAMES}
    _write_triad(package_dir, triad)
    original = begin_update_checkpoint(package_dir, text)
    path = checkpoint_path(package_dir)
    path.write_text(json.dumps(original.as_dict(), ensure_ascii=ensure_ascii), encoding="utf-8")
    before = path.read_bytes()

    assert detect_update_checkpoint(package_dir).state == "active"
    assert inspect_update_checkpoint(package_dir) == original
    assert path.read_bytes() == before
    _write_text(package_dir / "tasks.md", "updated\n")
    assert operation(package_dir) == original
    assert not path.exists()
    expected = text if operation is rollback_update_checkpoint else "updated\n"
    assert (package_dir / "tasks.md").read_bytes() == expected.encode("utf-8")


def test_json_integer_decode_value_error_is_corrupted(package_dir: Path) -> None:
    limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
    if not limit:
        pytest.skip("interpreter has no JSON integer digit limit")
    path = checkpoint_path(package_dir)
    path.write_text("1" * (limit + 1), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        json.loads(before)
    assert detect_update_checkpoint(package_dir).state == "corrupted"
    with pytest.raises(CheckpointCorruptedError):
        inspect_update_checkpoint(package_dir)
    assert path.read_bytes() == before


@pytest.mark.parametrize("seam", ["loads", "_checkpoint_from_payload", "_digest_payload"])
@pytest.mark.parametrize("error_type", [ValueError, RecursionError, UnicodeError])
def test_load_validation_errors_are_normalized(package_dir: Path, monkeypatch, seam: str, error_type) -> None:
    begin_update_checkpoint(package_dir, "valid intent")
    before = checkpoint_path(package_dir).read_bytes()

    def fail(*args, **kwargs):
        raise error_type("invalid checkpoint data")

    target = checkpoint_module.json if seam == "loads" else checkpoint_module
    monkeypatch.setattr(target, seam, fail)
    assert detect_update_checkpoint(package_dir).state == "corrupted"
    with pytest.raises(CheckpointCorruptedError):
        inspect_update_checkpoint(package_dir)
    assert checkpoint_path(package_dir).read_bytes() == before


@pytest.mark.parametrize("error_type", [BaseException, KeyboardInterrupt, SystemExit])
def test_probe_does_not_swallow_process_control(package_dir: Path, monkeypatch, error_type) -> None:
    begin_update_checkpoint(package_dir, "valid intent")

    def interrupt(*args, **kwargs):
        raise error_type("interrupted")

    monkeypatch.setattr(checkpoint_module.json, "loads", interrupt)
    with pytest.raises(error_type):
        detect_update_checkpoint(package_dir)


def test_begin_invalid_unicode_is_caller_error_not_corruption(package_dir: Path) -> None:
    with pytest.raises(UnicodeEncodeError):
        begin_update_checkpoint(package_dir, "\ud800")
    assert not checkpoint_path(package_dir).exists()


@pytest.mark.parametrize("operation", [begin_update_checkpoint, rollback_update_checkpoint])
def test_real_write_failure_is_not_checkpoint_corruption(package_dir: Path, monkeypatch, operation) -> None:
    if operation is rollback_update_checkpoint:
        begin_update_checkpoint(package_dir, "valid intent")
        _write_text(package_dir / "tasks.md", "half-applied\n")
    before = {p.name: p.read_bytes() for p in package_dir.iterdir()}

    def fail_write(*args, **kwargs):
        raise OSError("disk write failed")

    monkeypatch.setattr(checkpoint_module, "_write_file_atomic", fail_write)
    with pytest.raises(OSError, match="disk write failed"):
        if operation is begin_update_checkpoint:
            operation(package_dir, "valid intent")
        else:
            operation(package_dir)
    assert {p.name: p.read_bytes() for p in package_dir.iterdir()} == before

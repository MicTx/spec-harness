# tests/test_update_checkpoint_support.py
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Read-only recognition tests for historical update-checkpoint files.

The write mechanism (begin/rollback/complete snapshot lifecycle) is retired —
rollback safety is carried by Git WIP commits. These tests lock the surviving
contract: historical ``update-checkpoint.json`` files are detected and
validated (never mutated), corrupted records fail explicitly, and the module
exposes no snapshot/restore/write symbols at all.
"""

from __future__ import annotations

import hashlib
import json
import sys
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
    CheckpointMissingError,
    checkpoint_path,
    detect_unresolved_checkpoints,
    detect_update_checkpoint,
    inspect_update_checkpoint,
)

SLUG = "2026-06-12_add-push-stage"
CREATED_AT = "2026-06-12T10:00:00+00:00"

# Every symbol of the retired snapshot/restore/write path. The module must
# not re-grow any of them: Git WIP is the only recovery mechanism now.
RETIRED_WRITE_SYMBOLS = (
    "begin_update_checkpoint",
    "complete_update_checkpoint",
    "rollback_update_checkpoint",
    "CheckpointExistsError",
    "_read_triad_member",
    "_write_checkpoint",
    "_write_file_atomic",
    "_remove_checkpoint",
    "_fsync_directory",
    "DEFAULT_MEMBER_MODE",
    "CHECKPOINT_MODE",
    "MAX_SNAPSHOT_FILE_BYTES",
)


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


def _checkpoint_document(
    package: str,
    intent: str,
    snapshot: dict[str, str],
    *,
    created_at: str = CREATED_AT,
) -> dict:
    """Build an on-disk-format checkpoint document, checksum computed exactly
    like the retired writer did (locks the historical format contract)."""
    payload = {
        "version": 1,
        "package": package,
        "intent": intent,
        "createdAt": created_at,
        "snapshot": dict(snapshot),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["checksum"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _write_historical_checkpoint(package: Path, intent: str = "追加数据导出任务") -> None:
    """Leave behind a checkpoint file exactly as the retired mechanism did."""
    document = _checkpoint_document(package.name, intent, _sample_triad("snapshot"))
    _write_text(checkpoint_path(package), json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def _tamper_checkpoint(package: Path, mutate) -> None:
    path = checkpoint_path(package)
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    _write_text(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")


@pytest.fixture()
def package_dir(tmp_path: Path) -> Path:
    return _make_package(tmp_path)


def test_module_exposes_no_write_path_symbols() -> None:
    for symbol in RETIRED_WRITE_SYMBOLS:
        assert not hasattr(checkpoint_module, symbol), f"retired write symbol survived: {symbol}"


def test_historical_checkpoint_is_detected_as_active(package_dir: Path) -> None:
    _write_historical_checkpoint(package_dir, "调整范围")

    status = detect_update_checkpoint(package_dir)
    assert status.state == "active"
    assert status.exists is True
    assert status.interrupted is True
    assert status.checkpoint is not None
    assert status.checkpoint.intent == "调整范围"
    assert status.checkpoint.version == 1
    assert status.checkpoint.package == package_dir.name
    assert status.checkpoint.created_at == CREATED_AT
    assert set(status.checkpoint.snapshot) == set(TRIAD_FILENAMES)

    inspected = inspect_update_checkpoint(package_dir)
    assert inspected.intent == "调整范围"
    # Detection is read-only: the file stays byte-identical on disk.
    assert checkpoint_path(package_dir).is_file()


def test_detection_never_mutates_package_state(package_dir: Path) -> None:
    _write_historical_checkpoint(package_dir, "追加任务")
    _write_text(package_dir / "spec.md", "# half-applied update\n")
    _write_text(checkpoint_path(package_dir), "{ not json")
    before = {p.name: p.read_bytes() for p in package_dir.iterdir()}

    detect_update_checkpoint(package_dir)
    detect_unresolved_checkpoints(package_dir.parent)
    with pytest.raises(CheckpointCorruptedError):
        inspect_update_checkpoint(package_dir)

    assert {p.name: p.read_bytes() for p in package_dir.iterdir()} == before


def _corrupt(package: Path) -> Path:
    _write_text(checkpoint_path(package), "{ not json")
    return package


def test_clean_package_reports_none(package_dir: Path) -> None:
    status = detect_update_checkpoint(package_dir)
    assert status.state == "none"
    assert status.exists is False
    assert status.interrupted is False
    with pytest.raises(CheckpointMissingError):
        inspect_update_checkpoint(package_dir)


def test_unparsable_checkpoint_fails_explicitly(package_dir: Path) -> None:
    _corrupt(package_dir)

    status = detect_update_checkpoint(package_dir)
    assert status.state == "corrupted"
    assert status.interrupted is True
    assert status.error  # explicit reason, never a silent pass
    with pytest.raises(CheckpointCorruptedError):
        inspect_update_checkpoint(package_dir)
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
    _write_historical_checkpoint(package_dir, "追加任务")
    _tamper_checkpoint(package_dir, mutate)

    status = detect_update_checkpoint(package_dir)
    assert status.state == "corrupted", status.error
    assert status.error
    with pytest.raises(CheckpointCorruptedError):
        inspect_update_checkpoint(package_dir)
    assert checkpoint_path(package_dir).is_file()


def test_v2_snapshot_without_checklist_is_recognized(tmp_path: Path) -> None:
    """F11-era packages may hold spec.md + tasks.md snapshots only."""
    package = tmp_path / "pkg-v2"
    package.mkdir()
    _write_text(package / "spec.md", "# spec\n")
    _write_text(package / "tasks.md", "# tasks\n")
    document = _checkpoint_document(
        package.name,
        "v2 更新",
        {"spec.md": "# spec\n", "tasks.md": "# tasks\n"},
    )
    _write_text(checkpoint_path(package), json.dumps(document, ensure_ascii=False, indent=2) + "\n")

    status = detect_update_checkpoint(package)
    assert status.state == "active"
    assert set(status.checkpoint.snapshot) == {"spec.md", "tasks.md"}


def test_checkpoint_moved_between_packages_is_refused(tmp_path: Path) -> None:
    source = _make_package(tmp_path, slug="2026-06-11_source-package")
    target = _make_package(tmp_path, slug="2026-06-12_target-package")
    _write_historical_checkpoint(source, "追加任务")
    checkpoint_path(source).replace(checkpoint_path(target))

    with pytest.raises(CheckpointConsistencyError, match="moved between packages"):
        inspect_update_checkpoint(target)
    assert checkpoint_path(target).is_file()
    assert detect_update_checkpoint(source).state == "none"


def test_detect_unresolved_checkpoints_only_reports_interrupted(tmp_path: Path) -> None:
    clean = _make_package(tmp_path, slug="2026-06-11_clean-package")
    interrupted = _make_package(tmp_path, slug="2026-06-12_add-push-stage")
    _write_historical_checkpoint(interrupted, "追加任务")

    found = detect_unresolved_checkpoints(tmp_path / ".spec" / "specs")

    assert set(found) == {"2026-06-12_add-push-stage"}
    assert found["2026-06-12_add-push-stage"].state == "active"
    assert detect_update_checkpoint(clean).state == "none"


def test_detect_treats_missing_directory_as_clean(tmp_path: Path) -> None:
    status = detect_update_checkpoint(tmp_path / "missing" / "pkg")
    assert status.state == "none"
    assert status.interrupted is False
    assert detect_unresolved_checkpoints(tmp_path / "missing") == {}


def test_symlinked_checkpoint_file_is_reported_corrupted(package_dir: Path, tmp_path: Path) -> None:
    _write_historical_checkpoint(package_dir, "追加任务")
    real = checkpoint_path(package_dir)
    moved = tmp_path / "moved-checkpoint.json"
    real.replace(moved)
    real.symlink_to(moved)

    status = detect_update_checkpoint(package_dir)

    assert status.state == "corrupted"
    assert status.error
    with pytest.raises(CheckpointCorruptedError):
        inspect_update_checkpoint(package_dir)


@pytest.mark.parametrize("damage", ["deep-json", "invalid-utf8", "intent", "snapshot"])
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
def test_malformed_checkpoint_is_corrupted_without_mutation(package_dir: Path, damage: str, surrogate: str) -> None:
    _write_historical_checkpoint(package_dir, "追加任务")
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
    with pytest.raises(CheckpointCorruptedError):
        inspect_update_checkpoint(package_dir)
    assert {p.name: p.read_bytes() for p in package_dir.iterdir()} == before


def test_valid_unicode_checkpoint_is_recognized(package_dir: Path) -> None:
    text = "中文 café e\u0301 🌞 \U0010ffff\r\n"
    document = _checkpoint_document(package_dir.name, text, {name: text for name in TRIAD_FILENAMES})
    path = checkpoint_path(package_dir)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()

    status = detect_update_checkpoint(package_dir)

    assert status.state == "active"
    assert status.checkpoint == inspect_update_checkpoint(package_dir)
    assert status.checkpoint.intent == text
    assert path.read_bytes() == before


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
    _write_historical_checkpoint(package_dir, "valid intent")
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
    _write_historical_checkpoint(package_dir, "valid intent")

    def interrupt(*args, **kwargs):
        raise error_type("interrupted")

    monkeypatch.setattr(checkpoint_module.json, "loads", interrupt)
    with pytest.raises(error_type):
        detect_update_checkpoint(package_dir)


def test_checkpoint_error_family_remains_importable() -> None:
    """Consumers (route/check/report) catch the read-side family; the base and
    its read-side subclasses stay part of the public contract."""
    assert issubclass(CheckpointMissingError, CheckpointError)
    assert issubclass(CheckpointCorruptedError, CheckpointError)
    assert issubclass(CheckpointConsistencyError, CheckpointError)

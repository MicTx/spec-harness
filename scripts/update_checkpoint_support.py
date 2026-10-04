# scripts/update_checkpoint_support.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Update-stage checkpoint primitives for task packages.

An update mutation rewrites the ``spec.md`` / ``tasks.md`` / ``checklist.md``
triad in place; an interruption mid-update would otherwise leave the package
half-applied with no recovery point. ``begin_update_checkpoint`` snapshots the
triad into ``update-checkpoint.json`` inside the package directory;
``complete_update_checkpoint`` clears it only after re-validating checkpoint
integrity and post-update triad readability; ``rollback_update_checkpoint``
restores the recorded snapshot; ``detect`` / ``inspect`` surface interrupted
(begin without complete) or corrupted checkpoints so update flows recover
explicitly instead of silently losing state.

Single-writer contract: the update flow owns the package directory while a
checkpoint is open; concurrent ``begin`` is a caller-level error, not
enforced here. Only ``safe_open_support`` is imported while triad reads go
through the shared O_NOFOLLOW regular-file contract.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from safe_open_support import SafeOpenError, open_regular_read

CHECKPOINT_FILENAME = "update-checkpoint.json"
TRIAD_FILENAMES = ("spec.md", "tasks.md", "checklist.md")
CHECKPOINT_SCHEMA_VERSION = 1

# Per-member bound mirrors spec_package_support.MAX_SPEC_FILE_BYTES (kept
# literal so this module only depends on safe_open_support).
MAX_SNAPSHOT_FILE_BYTES = 2 * 1024 * 1024
# begin bounds every member, so the whole-document bound only rejects absurd
# files; JSON escaping can expand text several-fold, hence the loose value.
MAX_CHECKPOINT_FILE_BYTES = 64 * 1024 * 1024
# Mode applied to restored triad members that did not exist before rollback.
DEFAULT_MEMBER_MODE = 0o644
# Checkpoint files are private coordination state, never shared artifacts.
CHECKPOINT_MODE = 0o600


class CheckpointError(Exception):
    """Base class for update-checkpoint failures."""


class CheckpointExistsError(CheckpointError):
    """begin refused: an unresolved checkpoint already exists."""


class CheckpointMissingError(CheckpointError):
    """A checkpoint was required but is absent."""


class CheckpointCorruptedError(CheckpointError):
    """The checkpoint file exists but fails structural or integrity validation."""


class CheckpointConsistencyError(CheckpointError):
    """complete/rollback-time state does not match expectations; mutation refused."""


@dataclass(frozen=True)
class UpdateCheckpoint:
    """Validated checkpoint record: intent, timestamp, and triad snapshot."""

    version: int
    package: str
    intent: str
    created_at: str
    snapshot: dict[str, str]

    def as_dict(self) -> dict:
        """Stored document form, including the recomputed integrity checksum."""
        payload = {
            "version": self.version,
            "package": self.package,
            "intent": self.intent,
            "createdAt": self.created_at,
            "snapshot": dict(self.snapshot),
        }
        return {**payload, "checksum": _digest_payload(payload)}


@dataclass(frozen=True)
class CheckpointStatus:
    """Tri-state probe result for one package directory.

    ``state`` is one of:
      - ``"none"``: no checkpoint file; no update in flight.
      - ``"active"``: valid checkpoint; an update began and was not completed.
      - ``"corrupted"``: checkpoint present but unparseable or failing
        integrity validation; explicit manual recovery is required.
    """

    state: str
    path: Path
    checkpoint: UpdateCheckpoint | None = None
    error: str = ""

    @property
    def exists(self) -> bool:
        return self.state != "none"

    @property
    def interrupted(self) -> bool:
        return self.state in ("active", "corrupted")


def checkpoint_path(package_dir: Path) -> Path:
    return Path(package_dir) / CHECKPOINT_FILENAME


def _digest_payload(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _preview(value: object, limit: int = 80) -> str:
    """Short repr for diagnostics; corrupted payloads must not flood errors."""
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _read_triad_member(path: Path) -> str:
    """Read one triad member as UTF-8 text, byte-faithful across newlines."""
    try:
        with open_regular_read(path, max_bytes=MAX_SNAPSHOT_FILE_BYTES) as descriptor:
            with os.fdopen(descriptor, "r", encoding="utf-8", newline="", closefd=False) as handle:
                data = handle.read(MAX_SNAPSHOT_FILE_BYTES + 1)
    except SafeOpenError as exc:
        raise CheckpointError(f"cannot read {path.name}: {exc}") from exc
    except UnicodeError as exc:
        raise CheckpointError(f"{path.name} is not valid UTF-8 text: {exc}") from exc
    if len(data) > MAX_SNAPSHOT_FILE_BYTES:
        raise CheckpointError(f"{path.name} exceeds {MAX_SNAPSHOT_FILE_BYTES} bytes")
    return data


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory fsync so renames/unlinks survive a crash."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_file_atomic(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Replace ``path`` with ``data`` via an fsync'd temp file and os.replace.

    ``mode=None`` preserves an existing target's permission bits (rollback
    fidelity for triad members that already exist) and falls back to
    ``DEFAULT_MEMBER_MODE`` for newly created files; callers that own a
    private artifact pass an explicit mode.
    """
    directory = path.parent
    target_mode = mode
    if target_mode is None:
        try:
            target_mode = path.stat().st_mode & 0o777
        except OSError:
            target_mode = DEFAULT_MEMBER_MODE
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), target_mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    _fsync_directory(directory)


def _read_checkpoint_payload(path: Path) -> dict:
    """Parse the checkpoint file, refusing anything but a readable JSON object."""
    try:
        with open_regular_read(path, max_bytes=MAX_CHECKPOINT_FILE_BYTES) as descriptor:
            with os.fdopen(descriptor, "r", encoding="utf-8", newline="", closefd=False) as handle:
                raw = handle.read()
    except SafeOpenError as exc:
        if exc.reason == "missing":
            raise CheckpointMissingError(f"update checkpoint missing: {path}") from exc
        raise CheckpointCorruptedError(f"cannot safely read update checkpoint {path}: {exc}") from exc
    except UnicodeError as exc:
        raise CheckpointCorruptedError(f"update checkpoint is not valid UTF-8: {path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CheckpointCorruptedError(f"update checkpoint is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CheckpointCorruptedError(f"update checkpoint must be a JSON object: {path}: {_preview(payload)}")
    return payload


def _checkpoint_from_payload(payload: dict) -> UpdateCheckpoint:
    """Validate structure and integrity; any defect is explicit corruption."""
    version = payload.get("version")
    if isinstance(version, bool) or version != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointCorruptedError(
            f"unsupported update checkpoint version: {_preview(version)} (expected {CHECKPOINT_SCHEMA_VERSION})"
        )
    package = payload.get("package")
    if not isinstance(package, str) or not package:
        raise CheckpointCorruptedError(f"checkpoint field 'package' must be a non-empty string: {_preview(package)}")
    intent = payload.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        raise CheckpointCorruptedError(f"checkpoint field 'intent' must be a non-empty string: {_preview(intent)}")
    created_at = payload.get("createdAt")
    if not isinstance(created_at, str) or not created_at:
        raise CheckpointCorruptedError(
            f"checkpoint field 'createdAt' must be a non-empty string: {_preview(created_at)}"
        )
    try:
        parsed = datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise CheckpointCorruptedError(
            f"checkpoint field 'createdAt' is not an ISO-8601 timestamp: {_preview(created_at)}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CheckpointCorruptedError(f"checkpoint field 'createdAt' must be timezone-aware: {_preview(created_at)}")
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        raise CheckpointCorruptedError(f"checkpoint field 'snapshot' must be a JSON object: {_preview(snapshot)}")
    if set(snapshot) != set(TRIAD_FILENAMES):
        raise CheckpointCorruptedError(
            f"checkpoint 'snapshot' must map exactly the triad {TRIAD_FILENAMES}, got keys {sorted(snapshot)}"
        )
    for name, content in snapshot.items():
        if not isinstance(content, str):
            raise CheckpointCorruptedError(f"checkpoint snapshot member {name!r} must be a string: {_preview(content)}")
    checksum = payload.get("checksum")
    if not isinstance(checksum, str) or not checksum.isascii():
        raise CheckpointCorruptedError(f"checkpoint field 'checksum' must be an ASCII string: {_preview(checksum)}")
    expected_checksum = _digest_payload({key: value for key, value in payload.items() if key != "checksum"})
    if not hmac.compare_digest(checksum, expected_checksum):
        raise CheckpointCorruptedError(
            "update checkpoint checksum mismatch: the record was modified or torn; manual recovery required"
        )
    return UpdateCheckpoint(
        version=CHECKPOINT_SCHEMA_VERSION,
        package=package,
        intent=intent,
        created_at=created_at,
        snapshot={name: snapshot[name] for name in TRIAD_FILENAMES},
    )


def _load_status(package_dir: Path) -> CheckpointStatus:
    path = checkpoint_path(package_dir)
    try:
        try:
            checkpoint = _checkpoint_from_payload(_read_checkpoint_payload(path))
        except (ValueError, RecursionError) as exc:
            # Only untrusted load/validation failures are corruption. ValueError
            # includes UnicodeError and JSON decoder limits; canonical checksum
            # encoding and structural diagnostics can also reject parsed data.
            # Keep caller validation and all write paths outside this boundary.
            raise CheckpointCorruptedError(f"invalid update checkpoint data: {path}: {exc}") from exc
    except CheckpointMissingError:
        return CheckpointStatus("none", path)
    except CheckpointCorruptedError as exc:
        return CheckpointStatus("corrupted", path, None, str(exc))
    return CheckpointStatus("active", path, checkpoint)


def _write_checkpoint(package_dir: Path, checkpoint: UpdateCheckpoint) -> None:
    document = json.dumps(checkpoint.as_dict(), ensure_ascii=False, indent=2) + "\n"
    _write_file_atomic(checkpoint_path(package_dir), document.encode("utf-8"), mode=CHECKPOINT_MODE)


def _remove_checkpoint(package_dir: Path) -> None:
    path = checkpoint_path(package_dir)
    try:
        path.unlink()
    except FileNotFoundError as exc:
        raise CheckpointMissingError(f"update checkpoint already removed: {path}") from exc
    _fsync_directory(package_dir)


def _require_active_checkpoint(package_dir: Path, action: str) -> UpdateCheckpoint:
    status = _load_status(package_dir)
    if status.state == "none":
        raise CheckpointMissingError(f"no update checkpoint to {action}: {status.path}")
    if status.state == "corrupted":
        raise CheckpointCorruptedError(f"cannot {action} corrupted update checkpoint: {status.error}")
    checkpoint = status.checkpoint
    if checkpoint.package != package_dir.name:
        raise CheckpointConsistencyError(
            f"update checkpoint belongs to package {checkpoint.package!r}, not {package_dir.name!r}; "
            f"refusing to {action} a checkpoint moved between packages"
        )
    return checkpoint


def begin_update_checkpoint(package_dir: Path, intent: str) -> UpdateCheckpoint:
    """Snapshot the triad and atomically write the package checkpoint.

    Refuses to clobber an existing checkpoint (active or corrupted): an
    interrupted update must be completed or rolled back first. The checkpoint
    is written only after every member snapshots cleanly, so a failed begin
    never leaves a partial checkpoint behind.
    """
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("intent must be a non-empty string")
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        raise CheckpointError(f"task package directory not found: {package_dir}")
    status = _load_status(package_dir)
    if status.state == "active":
        raise CheckpointExistsError(
            f"unresolved update checkpoint already exists (intent: {status.checkpoint.intent!r}); "
            "complete or rollback before beginning another update"
        )
    if status.state == "corrupted":
        raise CheckpointExistsError(f"corrupted update checkpoint at {status.path}: {status.error}")
    snapshot = {name: _read_triad_member(package_dir / name) for name in TRIAD_FILENAMES}
    checkpoint = UpdateCheckpoint(
        version=CHECKPOINT_SCHEMA_VERSION,
        package=package_dir.name,
        intent=intent,
        created_at=datetime.now(timezone.utc).isoformat(),
        snapshot=snapshot,
    )
    _write_checkpoint(package_dir, checkpoint)
    return checkpoint


def complete_update_checkpoint(package_dir: Path, *, expected_intent: str | None = None) -> UpdateCheckpoint:
    """Clear the checkpoint after consistency checks; refusal keeps it intact.

    Verified before clearing: the checkpoint is structurally valid with an
    intact checksum, it belongs to this package, its intent matches
    ``expected_intent`` when given, and every triad member is currently a
    readable regular file (the update did not leave a broken package). Any
    failure raises and leaves the checkpoint in place so rollback stays
    available; complete never reverts the applied update.
    """
    if expected_intent is not None and (not isinstance(expected_intent, str) or not expected_intent.strip()):
        raise ValueError("expected_intent must be a non-empty string when provided")
    package_dir = Path(package_dir)
    checkpoint = _require_active_checkpoint(package_dir, "complete")
    if expected_intent is not None and checkpoint.intent != expected_intent:
        raise CheckpointConsistencyError(
            f"checkpoint intent {checkpoint.intent!r} does not match expected intent {expected_intent!r}; "
            "refusing to clear"
        )
    for name in TRIAD_FILENAMES:
        try:
            _read_triad_member(package_dir / name)
        except CheckpointError as exc:
            raise CheckpointConsistencyError(f"post-update triad check failed; refusing to clear: {exc}") from exc
    _remove_checkpoint(package_dir)
    return checkpoint


def rollback_update_checkpoint(package_dir: Path) -> UpdateCheckpoint:
    """Restore the triad from the snapshot, then remove the checkpoint.

    The checkpoint is removed only after every member is restored, so a failed
    rollback leaves the interruption detectable and retryable.
    """
    package_dir = Path(package_dir)
    checkpoint = _require_active_checkpoint(package_dir, "roll back")
    for name in TRIAD_FILENAMES:
        _write_file_atomic(package_dir / name, checkpoint.snapshot[name].encode("utf-8"))
    _remove_checkpoint(package_dir)
    return checkpoint


def inspect_update_checkpoint(package_dir: Path) -> UpdateCheckpoint:
    """Strict read: return the checkpoint or raise missing/corrupted explicitly."""
    return _require_active_checkpoint(Path(package_dir), "inspect")


def detect_update_checkpoint(package_dir: Path) -> CheckpointStatus:
    """Non-raising tri-state probe for interrupted updates in one package."""
    return _load_status(Path(package_dir))


def detect_unresolved_checkpoints(specs_dir: Path) -> dict[str, CheckpointStatus]:
    """Map slug -> status for every immediate package holding a checkpoint.

    Symlinked children are skipped, mirroring active-package enumeration; a
    clean package directory yields an empty mapping.
    """
    specs_dir = Path(specs_dir)
    if not specs_dir.is_dir():
        return {}
    found: dict[str, CheckpointStatus] = {}
    for child in sorted(specs_dir.iterdir()):
        if child.is_symlink() or not child.is_dir():
            continue
        status = _load_status(child)
        if status.state != "none":
            found[child.name] = status
    return found

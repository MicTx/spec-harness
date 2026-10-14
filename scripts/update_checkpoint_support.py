#!/usr/bin/env python3
# scripts/update_checkpoint_support.py
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Read-only recognition of historical update-stage checkpoints.

The self-managed checkpoint mechanism (triad snapshot into
``update-checkpoint.json``, begin/rollback/complete lifecycle) was retired in
favor of Git WIP commits: an update session commits a WIP recovery point on
the integration branch before rewriting the triad, and rollback is
``git reset --hard`` / ``git revert`` against that history. This module no
longer snapshots, restores, or writes anything — it only recognizes and
validates checkpoint files left behind by packages updated under the retired
mechanism, so historical half-applied states stay visible instead of becoming
silent dead corners.

``detect_update_checkpoint`` / ``detect_unresolved_checkpoints`` probe without
raising (tri-state: none / active / corrupted);
``inspect_update_checkpoint`` is the strict read that returns the validated
record or raises ``CheckpointMissingError`` / ``CheckpointCorruptedError`` /
``CheckpointConsistencyError``. Only ``safe_open_support`` is imported; triad
reads go through the shared O_NOFOLLOW regular-file contract.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from safe_open_support import SafeOpenError, open_regular_read

CHECKPOINT_FILENAME = "update-checkpoint.json"
TRIAD_FILENAMES = ("spec.md", "tasks.md", "checklist.md")
# checklist.md was optional for v2 packages (F11): a historical snapshot may
# hold only the members that existed, always including this required core.
TRIAD_REQUIRED_CORE = ("spec.md", "tasks.md")
CHECKPOINT_SCHEMA_VERSION = 1

# Recognition bound mirrors spec_package_support.MAX_SPEC_FILE_BYTES (kept
# literal so this module only depends on safe_open_support). Checkpoint files
# were private coordination state, never shared artifacts.
MAX_CHECKPOINT_FILE_BYTES = 64 * 1024 * 1024


class CheckpointError(Exception):
    """Base class for update-checkpoint failures."""


class CheckpointMissingError(CheckpointError):
    """A checkpoint was required but is absent."""


class CheckpointCorruptedError(CheckpointError):
    """The checkpoint file exists but fails structural or integrity validation."""


class CheckpointConsistencyError(CheckpointError):
    """Recorded state does not match expectations; the read is refused."""


@dataclass(frozen=True)
class UpdateCheckpoint:
    """Validated checkpoint record: intent, timestamp, and triad snapshot."""

    version: int
    package: str
    intent: str
    created_at: str
    snapshot: dict[str, str]


@dataclass(frozen=True)
class CheckpointStatus:
    """Tri-state probe result for one package directory.

    ``state`` is one of:
      - ``"none"``: no checkpoint file; no historical update in flight.
      - ``"active"``: valid checkpoint; an update began and was never resolved
        under the retired mechanism. Git history is the recovery point.
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
    if not set(TRIAD_REQUIRED_CORE) <= set(snapshot) or not set(snapshot) <= set(TRIAD_FILENAMES):
        raise CheckpointCorruptedError(
            f"checkpoint 'snapshot' must map the triad members that exist "
            f"(required core {TRIAD_REQUIRED_CORE}, optional {TRIAD_FILENAMES}), got keys {sorted(snapshot)}"
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
        snapshot={name: snapshot[name] for name in TRIAD_FILENAMES if name in snapshot},
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
            # Caller validation stays outside this boundary.
            raise CheckpointCorruptedError(f"invalid update checkpoint data: {path}: {exc}") from exc
    except CheckpointMissingError:
        return CheckpointStatus("none", path)
    except CheckpointCorruptedError as exc:
        return CheckpointStatus("corrupted", path, None, str(exc))
    return CheckpointStatus("active", path, checkpoint)


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

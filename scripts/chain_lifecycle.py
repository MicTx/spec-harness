#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Strict recorded-scope proof and explicit completed-chain closeout.

Internal support for the two spawn CLIs, not a new user stage. A close uses
those CLIs' mutex, never kills a worker, and never implicitly changes scope.
The immutable snapshot precedes the durable audit barrier and summary marker.
An interrupted close leaves the old state retryable; corrupt-summary recovery
sees the barrier, not a completed chain's former spawn counter.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict

import chain_spawn_support as support


def _safe_path(root: Path, path: Path) -> Path:
    """Refuse linked ancestors too, including links that resolve inside root."""
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise support.ChainSpawnError(f"chain path escapes project root: {path}") from exc
    current = root
    for part in parts:
        if part in ("..", "."):
            raise support.ChainSpawnError(f"ambiguous chain path: {path}")
        current = current / part
        if current.is_symlink():
            raise support.ChainSpawnError(f"symlink in chain path: {current}")
    return path


def recorded_plan(root: Path, state: Dict[str, object], *, require_fingerprint: bool = True) -> Dict[str, object]:
    """Strict proof for exactly the recorded scope, never a fresh global scan.

    Lazy import keeps the shared support independent of the CLI import graph.
    Every recorded document must still exist and contain features; a partially
    unreadable cluster cannot become a smaller, supposedly completed cluster.
    """
    from autorun_spawn import count_features

    names = state.get("plan_docs")
    frozen = state.get("feature_fingerprint")
    if not isinstance(names, list) or not names or any(not isinstance(name, str) or not name.strip() for name in names):
        raise support.ChainSpawnError("chain has no valid recorded plan_docs; cannot prove its scope")
    if len(set(names)) != len(names):
        raise support.ChainSpawnError("duplicate recorded plan_docs")
    if require_fingerprint and (not isinstance(frozen, str) or not frozen):
        raise support.ChainSpawnError("chain has no feature_fingerprint; cannot prove its scope")
    paths = []
    snapshots = []
    reports = []
    totals = {"checked": 0, "unchecked": 0, "total": 0}
    for name in names:
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise support.ChainSpawnError(f"recorded plan path escapes project root: {name}")
        path = _safe_path(root, root / name)
        if path in paths:
            raise support.ChainSpawnError("duplicate normalized recorded plan path")
        try:
            raw = path.read_bytes()
            counts = count_features(raw.decode("utf-8"))
        except (OSError, UnicodeError) as exc:
            raise support.ChainSpawnError(f"recorded plan unreadable: {name}: {exc}") from exc
        if not counts["total"]:
            raise support.ChainSpawnError(f"recorded plan has no features: {name}")
        paths.append(path)
        snapshots.append(raw)
        reports.append({"path": name, **counts, "sha256": hashlib.sha256(raw).hexdigest()})
        for key in totals:
            totals[key] += int(counts[key])
    try:
        current = support.feature_fingerprint(paths, root)
        stable = all(path.read_bytes() == raw for path, raw in zip(paths, snapshots))
    except (OSError, UnicodeError) as exc:
        raise support.ChainSpawnError(f"recorded plan changed during inspection: {exc}") from exc
    if not stable or (frozen is not None and current != frozen):
        raise support.ChainSpawnError("recorded planning features changed; refusing completion or close")
    return {"project": str(root), "docs": reports, "totals": totals, "complete": totals["unchecked"] == 0}


def _append_closure(path: Path, marker: Dict[str, object]) -> None:
    """Required (not best-effort) audit barrier, tolerant of a partial tail."""
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell():
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.write(b"\n")
        handle.write((json.dumps(marker, sort_keys=True) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def close_chain(root: Path, chain: str, *, dry_run: bool = False) -> Dict[str, object]:
    """Close a completed recorded chain; no force/reset or new-scope override.

    Archive files stay tracked under .spec/<chain>/history. Runtime spawns
    remain append-only, including a closed marker so recovery cannot resurrect
    the old counter. Idempotent retries also cover partial audit/summary writes.
    The flock serializes CLI transactions, not the whole interactive session.
    """
    root = root.resolve()
    summary = _safe_path(root, support.chain_dir(root, chain))
    runtime = _safe_path(root, support.chain_runtime_dir(root, chain))
    state_path = _safe_path(root, summary / support.CHAIN_STATE_FILE)
    audit_path = _safe_path(root, runtime / support.CHAIN_SPAWNS_FILE)
    history = _safe_path(root, summary / "history")
    _safe_path(root, runtime / support.CHAIN_LOCK_FILE)
    try:
        with support.single_chain_lock(runtime, chain):
            if not state_path.exists():
                return {"status": "not_started", "chain": chain}
            raw = state_path.read_text(encoding="utf-8")
            state = json.loads(raw)
            if not isinstance(state, dict):
                raise support.ChainSpawnError("chain state is not a JSON object")
            if support.closed_chain_marker(state):
                return {"status": "already_closed", "chain": chain, "archive": state.get("archive")}
            proof = recorded_plan(root, state)
            if not proof["complete"]:
                raise support.ChainSpawnError("recorded chain has unfinished features; refusing close")
            packages = _safe_path(root, root / ".spec/specs")
            if packages.exists() and any(p.is_dir() and p.name != "archive" for p in packages.iterdir()):
                raise support.ChainSpawnError("active package exists; finish/archive it before closing the chain")
            counter_key = "pass" if chain == "autoplan" else "round"
            counter = state.get(counter_key)
            if isinstance(counter, bool) or not isinstance(counter, int) or counter < 1:
                raise support.ChainSpawnError(f"invalid chain {counter_key}; refusing close")
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            tail = support.read_audit_tail(runtime)
            if audit_path.exists() and tail is None:
                raise support.ChainSpawnError("chain audit unreadable; refusing close")
            if tail is not None:
                if tail.get("lifecycle") == "closed":
                    matches = tail.get("previous_digest") == digest
                else:
                    matches = all(
                        tail.get(key) == state.get(key)
                        for key in (counter_key, "feature_fingerprint", "plan_docs", "model", "host")
                    )
                if not matches:
                    raise support.ChainSpawnError("chain summary and audit diverged; inspect/recover before close")
            archive = _safe_path(root, history / f"{digest}.json")
            payload = {
                "status": "would_close",
                "chain": chain,
                "archive": str(archive.relative_to(root)),
                "checked": proof["totals"]["checked"],
            }
            if dry_run:
                return payload
            snapshot = {
                "previous_state": state,
                "previous_state_text": raw,
                "proof": proof,
                "closed_at": support.utc_now(),
            }
            if archive.exists():
                existing = json.loads(archive.read_text(encoding="utf-8"))
                if (
                    not isinstance(existing, dict)
                    or existing.get("previous_state_text") != raw
                    or existing.get("proof") != proof
                ):
                    raise support.ChainSpawnError("close archive collision; refusing overwrite")
                snapshot = existing
            else:
                _safe_path(root, archive.with_name(f".{archive.name}.tmp"))
                support.atomic_write(archive, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
            marker = {
                "lifecycle": "closed",
                "chain": chain,
                "archive": payload["archive"],
                "closed_at": snapshot["closed_at"],
                "previous_digest": digest,
            }
            # Commit order matters: an audit failure must not clear active state.
            _append_closure(audit_path, marker)
            _safe_path(root, state_path.with_name(f".{state_path.name}.tmp"))
            support.atomic_write(state_path, json.dumps(marker, indent=2, sort_keys=True) + "\n")
            return {**payload, "status": "closed"}
    except (OSError, ValueError, UnicodeError) as exc:
        raise support.ChainSpawnError(f"cannot close {chain} chain: {exc}") from exc


def add_close_parser(subparsers) -> None:
    parser = subparsers.add_parser("close", help="internally archive a proven-completed recorded chain")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true", help="validate only; preserve active state and history")
    parser.add_argument("--format", choices=("text", "json"), default="text")


def render_close(payload: Dict[str, object]) -> str:
    return "chain: {}\nclose: {}\narchive: {}".format(
        payload["chain"], payload["status"], payload.get("archive", "none")
    )

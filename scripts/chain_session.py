#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import chain_spawn_support as support
from chain_spawn_support import ChainSpawnError

SURFACES = ("cli", "ide-plugin", "desktop", "unknown")
EXECUTIONS = ("auto", "current", "fresh")
SCHEMA_VERSION = 3
SUPPORTED_CURRENT_SCHEMAS = (2, 3)
FRESH_WORKERS = ("codex", "pi", "claude")
CHAINS = ("autoplan", "autorun")
CURRENT_REQUIREMENTS = ["workspace-read", "workspace-write", "python>=3.9", "git"]

CLIENT_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
OWNER_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
CHAIN_ID_RE = OWNER_RE
STEP_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
SKIP_LINK_PREFIXES = ("http://", "https://", "mailto:", "#", "/")
MAX_PAUSE_REASON = 400
PASS_KINDS = ("framework", "detail", "review")
LIFECYCLES = ("active", "ready", "complete")
REVIEW_MODES = ("main", "independent")
STEP_OUTCOMES = ("passed", "revise")
STEP_STATUSES = ("active", "paused", "complete")
STEP_KINDS = {"autoplan": PASS_KINDS, "autorun": ("package",)}

_atomic_write = support.atomic_write


def _resolve_root(raw_root: str) -> Path:
    return support.resolve_root(raw_root)


def _safe_path(boundary: Path, path: Path) -> Path:
    if boundary.is_symlink():
        raise ChainSpawnError(f"chain path boundary is a symlink: {boundary}")
    try:
        parts = path.relative_to(boundary).parts
    except ValueError as exc:
        raise ChainSpawnError(f"chain path escapes its boundary: {path}") from exc
    current = boundary
    for part in parts:
        if part in ("..", "."):
            raise ChainSpawnError(f"ambiguous chain path: {path}")
        current = current / part
        if current.is_symlink():
            raise ChainSpawnError(f"symlink in chain path: {current}")
    return path


def _valid_rel(rel) -> bool:
    if not isinstance(rel, str) or not rel or "\\" in rel or "\x00" in rel:
        return False
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        return False
    return os.path.normpath(rel) == rel


def _safe_plan_path(root: Path, rel) -> Path:
    if not _valid_rel(rel):
        raise ChainSpawnError(f"unsafe recorded path: {rel!r}")
    return _safe_path(root, root / rel)


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _hash_owner(owner: str) -> str:
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()


def _valid_client(client) -> bool:
    return isinstance(client, str) and CLIENT_RE.fullmatch(client) is not None and client.strip(".") != ""


def _valid_chain_id(value) -> bool:
    return isinstance(value, str) and CHAIN_ID_RE.fullmatch(value) is not None


def _validate_chain_id(value) -> str:
    if not _valid_chain_id(value):
        raise ChainSpawnError("invalid --chain-id (nonempty ASCII, <=128, letters/digits/._:-)")
    return value


def _validate_owner(owner) -> str:
    if not isinstance(owner, str) or OWNER_RE.fullmatch(owner) is None:
        raise ChainSpawnError("invalid --owner (expected a nonempty ASCII id: letters, digits, ._:-)")
    return owner


def route_payload(
    client: str = "unknown",
    surface: str = "unknown",
    execution: str = "auto",
    worker=None,
):
    if surface not in SURFACES or execution not in EXECUTIONS:
        raise ChainSpawnError("invalid client surface or execution strategy")
    if not _valid_client(client):
        raise ChainSpawnError("invalid client identifier (nonempty ASCII, <=64, letters/digits/-_.)")
    mode = execution
    if mode == "auto":
        mode = "fresh" if surface == "cli" and client in FRESH_WORKERS else "current"
    if mode == "current" and worker is not None:
        raise ChainSpawnError("--worker requires --execution fresh")
    selected = (worker or client) if mode == "fresh" else None
    if mode == "fresh" and selected not in FRESH_WORKERS:
        raise ChainSpawnError("no verified fresh-session adapter; use current or explicitly select --worker")
    return {
        "execution_mode": mode,
        "client": client,
        "surface": surface,
        "worker": selected,
        "requirements": list(CURRENT_REQUIREMENTS),
        "native_subagents": "optional",
    }


def _summary_path(root: Path, chain: str) -> Path:
    return support.chain_dir(root, chain) / support.CHAIN_STATE_FILE


def _journal_path(root: Path, chain: str) -> Path:
    return support.chain_runtime_dir(root, chain) / support.CHAIN_SPAWNS_FILE


def _counter_key(chain: str) -> Tuple[str, str]:
    return ("pass", "max_passes") if chain == "autoplan" else ("round", "max_rounds")


def _read_summary(root: Path, chain: str) -> Tuple[Optional[Dict[str, object]], str]:
    path = _safe_path(root, _summary_path(root, chain))
    if not path.is_file():
        return None, "corrupt" if path.exists() else "missing"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "corrupt"
    if not isinstance(parsed, dict):
        return None, "corrupt"
    version = parsed.get("schema_version")
    if version is not None and (type(version) is not int or version not in SUPPORTED_CURRENT_SCHEMAS):
        return parsed, "unknown"
    if parsed.get("lifecycle") == "closed":
        try:
            if version in SUPPORTED_CURRENT_SCHEMAS:
                error = _closed_state_error(parsed, chain)
                return parsed, "corrupt" if error else "closed"
            support.closed_chain_marker(parsed)
            return parsed, "closed"
        except ChainSpawnError:
            return parsed, "corrupt"
    if version is not None:
        if parsed.get("execution_mode") != "current":
            return parsed, "unknown"
        return (parsed, "current") if _state_error(parsed, chain) is None else (parsed, "corrupt")
    if parsed.get("execution_mode") == "current":
        return parsed, "unknown"
    return parsed, "legacy"


def _closed_state_error(state, chain):
    if (
        type(state.get("schema_version")) is not int
        or state["schema_version"] not in SUPPORTED_CURRENT_SCHEMAS
        or state.get("execution_mode") != "current"
        or state.get("chain") != chain
        or not _valid_chain_id(state.get("chain_id"))
        or type(state.get("revision")) is not int
        or state["revision"] < 2
    ):
        return "invalid current closure identity or revision"
    try:
        if not support.closed_chain_marker(state):
            return "invalid current closure marker"
    except ChainSpawnError as exc:
        return str(exc)
    return None


def _proof_error(proof: Dict[str, object]) -> Optional[str]:
    for key in ("doc_digests", "archive_hashes"):
        value = proof.get(key)
        if value is not None and (
            not isinstance(value, dict)
            or any(
                not _valid_rel(name) or not isinstance(digest, str) or not HEX64_RE.fullmatch(digest)
                for name, digest in value.items()
            )
        ):
            return f"invalid proof {key}"
    if proof.get("review_mode") is not None and proof["review_mode"] not in REVIEW_MODES:
        return "invalid proof review_mode"
    for key in ("review_note", "archive", "slug", "gate"):
        if proof.get(key) is not None and not isinstance(proof[key], str):
            return f"invalid proof {key}"
    return None


def _step_error(step: Dict[str, object], chain: str) -> Optional[str]:
    if not isinstance(step, dict):
        return "invalid step"
    if not isinstance(step.get("id"), str) or not step["id"] or not STEP_ID_RE.fullmatch(step["id"]):
        return "invalid step id"
    if step.get("kind") not in STEP_KINDS[chain]:
        return "invalid step kind"
    if step.get("status") not in STEP_STATUSES:
        return "invalid step status"
    if step.get("outcome") is not None and step["outcome"] not in STEP_OUTCOMES:
        return "invalid step outcome"
    for key in ("target", "slug"):
        if step.get(key) is not None and not isinstance(step[key], str):
            return f"invalid step {key}"
    if step["kind"] == "detail":
        if not _valid_rel(step.get("target")) or step.get("slug") is not None:
            return "invalid detail assignment"
    elif step["kind"] == "package":
        from spec_package_support import is_development_record_slug

        if not isinstance(step.get("slug"), str) or not is_development_record_slug(step["slug"]):
            return "invalid package slug"
        if step.get("target") is not None:
            return "invalid package target"
    elif step.get("target") is not None or step.get("slug") is not None:
        return "invalid planning assignment"
    if step["status"] == "complete":
        if step.get("outcome") not in STEP_OUTCOMES:
            return "completed step has no outcome"
        if step["outcome"] == "revise" and step["kind"] != "review":
            return "only review can request revision"
    elif step.get("outcome") is not None:
        return "unfinished step has an outcome"
    if step.get("pause_reason") is not None and not isinstance(step["pause_reason"], str):
        return "invalid pause_reason"
    if step.get("proof") is not None:
        if not isinstance(step["proof"], dict):
            return "invalid step proof"
        return _proof_error(step["proof"])
    return None


def _state_error(state: Dict[str, object], chain: str) -> Optional[str]:
    if (
        type(state.get("schema_version")) is not int
        or state["schema_version"] not in SUPPORTED_CURRENT_SCHEMAS
        or state.get("execution_mode") != "current"
    ):
        return "unsupported schema/execution marker"
    if state.get("chain") != chain:
        return "chain mismatch"
    if not _valid_chain_id(state.get("chain_id")):
        return "invalid chain_id"
    revision = state.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        return "invalid revision"
    if not _valid_client(state.get("host")):
        return "invalid host"
    if state.get("client_surface") not in SURFACES:
        return "invalid client_surface"
    docs = state.get("plan_docs")
    if not isinstance(docs, list) or not docs or any(not _valid_rel(doc) for doc in docs):
        return "invalid plan_docs"
    if len(set(docs)) != len(docs):
        return "duplicate plan_docs"
    counter_key, cap_key = _counter_key(chain)
    counter = state.get(counter_key)
    if isinstance(counter, bool) or not isinstance(counter, int) or counter < 1:
        return f"invalid {counter_key}"
    cap = state.get(cap_key)
    if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 1):
        return f"invalid {cap_key}"
    fingerprint = state.get("feature_fingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or not HEX64_RE.fullmatch(fingerprint)):
        return "invalid feature_fingerprint"
    step = state.get("step")
    err = _step_error(step, chain)
    if err is not None:
        return err
    completed = state.get("completed_step_ids")
    if (
        not isinstance(completed, list)
        or any(not isinstance(item, str) or not item for item in completed)
        or len(set(completed)) != len(completed)
    ):
        return "invalid completed_step_ids"
    if state.get("lifecycle") not in LIFECYCLES:
        return "invalid lifecycle"
    if not isinstance(state.get("updated_at"), str) or not state["updated_at"]:
        return "invalid updated_at"
    if state.get("model") is not None:
        return "invalid model"
    for key in ("model_control", "permission_effective"):
        if state.get(key) != "host":
            return f"invalid {key}"
    if state["lifecycle"] == "ready":
        proof = step.get("proof") or {}
        if (
            chain != "autoplan"
            or step["kind"] != "review"
            or step["status"] != "complete"
            or step["outcome"] != "passed"
            or not proof.get("doc_digests")
            or proof.get("gate") != "passed"
            or proof.get("review_mode") not in REVIEW_MODES
            or not isinstance(proof.get("review_note"), str)
            or not proof["review_note"].strip()
        ):
            return "ready state lacks a passed review proof"
    if state["lifecycle"] == "complete":
        if (
            chain != "autorun"
            or step["kind"] != "package"
            or step["status"] != "complete"
            or step["outcome"] != "passed"
            or not (step.get("proof") or {}).get("archive_hashes")
        ):
            return "complete state lacks an accepted archive proof"
    return None


def _current_row_error(row: Dict[str, object], chain: str) -> Optional[str]:
    state = row.get("state")
    if not isinstance(state, dict):
        return "state is not an object"
    owner_hash = row.get("owner_hash")
    if owner_hash is not None and (not isinstance(owner_hash, str) or not HEX64_RE.fullmatch(owner_hash)):
        return "invalid owner_hash"
    operation_id = row.get("operation_id")
    if not isinstance(operation_id, str) or not HEX64_RE.fullmatch(operation_id):
        return "invalid operation_id"
    if type(state.get("schema_version")) is not int or state["schema_version"] not in SUPPORTED_CURRENT_SCHEMAS:
        return "unsupported schema/execution marker"
    if state.get("execution_mode") != "current":
        return "unsupported schema/execution marker"
    if state.get("chain") != chain:
        return "chain mismatch"
    if state.get("lifecycle") == "closed":
        return _closed_state_error(state, chain)
    return _state_error(state, chain)


def _closure_covers(summary, marker) -> bool:
    if not isinstance(summary, dict) or not isinstance(marker, dict):
        return False
    if type(summary.get("revision")) is not int or type(marker.get("revision")) is not int:
        return False
    digest = hashlib.sha256((json.dumps(summary, indent=2, sort_keys=True) + "\n").encode()).hexdigest()
    return (
        summary.get("chain_id") == marker.get("chain_id")
        and summary["revision"] + 1 == marker["revision"]
        and digest == marker.get("previous_digest")
    )


def _read_journal(runtime_dir: Path, chain: str) -> Dict[str, object]:
    scan: Dict[str, object] = {
        "epoch": [],
        "barrier": None,
        "closed_row": None,
        "last_current_row": None,
        "saw_current_row": False,
        "error": None,
        "torn": False,
        "journal_digest": None,
        "tail_offset": None,
        "known_chain_ids": set(),
    }
    path = _safe_path(runtime_dir.parent.parent, runtime_dir / support.CHAIN_SPAWNS_FILE)
    if not path.is_file():
        if path.exists():
            scan["error"] = "journal is not a regular file"
        return scan
    try:
        raw = path.read_bytes()
        lines = raw.decode("utf-8").splitlines()
    except (OSError, UnicodeError):
        scan["error"] = "journal unreadable"
        return scan
    scan["journal_digest"] = hashlib.sha256(raw).hexdigest()
    nonempty = [index for index, line in enumerate(lines) if line.strip()]
    last_index = nonempty[-1] if nonempty else -1
    for line in lines:
        try:
            item = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(item, dict) and (
            item.get("event") == "current_transition"
            or isinstance(item.get("state"), dict)
            and item["state"].get("execution_mode") == "current"
        ):
            scan["saw_current_row"] = True
    epoch: List[Dict[str, object]] = []
    epoch_chain_id: Optional[str] = None
    previous_revision = 0
    closed_chain_id: Optional[str] = None
    barrier: Optional[str] = None
    closed_row: Optional[Dict[str, object]] = None
    max_current_schema = 0
    for index in nonempty:
        stripped = lines[index].strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            if index == last_index and not raw.endswith(b"\n"):
                scan["torn"] = True
                scan["tail_offset"] = raw.rfind(b"\n") + 1
                break
            scan["error"] = f"malformed journal record at line {index + 1}"
            return scan
        if not isinstance(parsed, dict):
            scan["error"] = f"malformed journal record at line {index + 1}"
            return scan
        if parsed.get("event") != "current_transition":
            if scan["saw_current_row"] and (
                parsed.get("event") is not None
                or not (
                    type(parsed.get("round")) is int
                    or type(parsed.get("pass")) is int
                    or parsed.get("lifecycle") == "closed"
                )
            ):
                scan["error"] = f"unrecognized journal record at line {index + 1}"
                return scan
            barrier = "legacy"
            epoch = []
            epoch_chain_id = None
            previous_revision = 0
            closed_chain_id = None
            closed_row = None
            continue
        scan["saw_current_row"] = True
        err = _current_row_error(parsed, chain)
        if err is not None:
            scan["error"] = f"invalid current journal row at line {index + 1}: {err}"
            return scan
        state = parsed["state"]
        if state["schema_version"] < max_current_schema:
            scan["error"] = f"current schema downgrade at line {index + 1}"
            return scan
        max_current_schema = state["schema_version"]
        stored_id = state.get("chain_id")
        if _valid_chain_id(stored_id):
            scan["known_chain_ids"].add(stored_id)
        if state.get("lifecycle") == "closed":
            if epoch and state.get("chain_id") != epoch_chain_id:
                scan["error"] = f"closed marker at line {index + 1} names a different chain_id"
                return scan
            if epoch and state["revision"] != previous_revision + 1:
                scan["error"] = f"non-sequential closure revision at line {index + 1}"
                return scan
            barrier = "closed"
            closed_row = parsed
            closed_chain_id = state.get("chain_id")
            epoch = []
            epoch_chain_id = None
            previous_revision = 0
            continue
        if not epoch:
            if closed_chain_id is not None and state.get("chain_id") == closed_chain_id:
                scan["error"] = f"current epoch at line {index + 1} continues a closed chain"
                return scan
            if barrier == "closed" and state["revision"] != 1:
                scan["error"] = f"new chain at line {index + 1} must start at revision 1"
                return scan
            epoch_chain_id = state.get("chain_id")
        else:
            if state.get("chain_id") != epoch_chain_id:
                scan["error"] = f"mixed chain ids in current epoch at line {index + 1}"
                return scan
            if state.get("revision") != previous_revision + 1:
                scan["error"] = f"non-sequential revision at line {index + 1}"
                return scan
        epoch.append(parsed)
        previous_revision = state["revision"]
    scan["epoch"] = epoch
    scan["barrier"] = barrier
    scan["closed_row"] = closed_row
    scan["last_current_row"] = epoch[-1] if epoch else closed_row
    return scan


def _effective_view(root: Path, chain: str) -> Dict[str, object]:
    _safe_path(root, root / ".git")
    summary, kind = _read_summary(root, chain)
    scan = _read_journal(support.chain_runtime_dir(root, chain), chain)
    epoch = scan["epoch"]
    view: Dict[str, object] = {
        "summary": summary,
        "summary_kind": kind,
        "rows": epoch,
        "scan": scan,
        "last_current_row": scan["last_current_row"],
        "owner_hash": epoch[-1].get("owner_hash") if epoch else None,
        "journal_error": scan["error"],
        "torn": scan["torn"],
        "state": None,
        "source": "none",
        "diverged": None,
        "closed": False,
        "closed_marker": None,
        "legacy": False,
    }
    if kind == "unknown":
        view["diverged"] = "unsupported or foreign state version"
        view["source"] = "diverged"
        return view
    if not scan["saw_current_row"] and not _current_marker(summary) and kind == "legacy":
        view["legacy"] = True
        view["state"] = summary
        view["source"] = "legacy"
        return view
    if scan["error"] is not None:
        view["diverged"] = f"chain journal malformed: {scan['error']}"
        view["source"] = "diverged"
        if kind == "current":
            view["state"] = summary
        return view
    if epoch:
        journal_state = epoch[-1]["state"]
        if kind == "current":
            if summary.get("chain_id") != journal_state.get("chain_id"):
                prior_close = scan["closed_row"]
                if prior_close is not None and _closure_covers(summary, prior_close["state"]):
                    view["state"] = journal_state
                    view["source"] = "journal"
                    return view
                view["diverged"] = "summary and journal record different chain ids"
                view["state"] = summary
                view["source"] = "diverged"
                return view
            journal_rev = journal_state.get("revision")
            summary_rev = summary.get("revision")
            if isinstance(journal_rev, int) and isinstance(summary_rev, int):
                if journal_rev > summary_rev:
                    view["state"] = journal_state
                    view["source"] = "journal"
                    return view
                if journal_rev < summary_rev:
                    view["diverged"] = "summary is newer than the durable journal"
                    view["state"] = summary
                    view["source"] = "diverged"
                    return view
            if journal_state != summary:
                view["diverged"] = "summary differs from the recorded journal state"
                view["state"] = summary
                view["source"] = "diverged"
                return view
            view["state"] = summary
            view["source"] = "summary"
            return view
        if kind == "closed":
            if summary.get("chain_id") == journal_state.get("chain_id"):
                view["diverged"] = "a closed marker claims the live chain epoch"
                view["source"] = "diverged"
                return view
            view["state"] = journal_state
            view["source"] = "journal"
            return view
        if kind in ("missing", "corrupt"):
            view["state"] = journal_state
            view["source"] = "journal"
            return view
        view["diverged"] = "summary contradicts the committed current journal"
        view["source"] = "diverged"
        return view
    if scan["barrier"] == "closed":
        view["closed_marker"] = scan["closed_row"]["state"]
        if kind == "legacy":
            view["legacy"] = True
            view["state"] = summary
            view["source"] = "legacy"
            return view
        if kind == "current" and not _closure_covers(summary, view["closed_marker"]):
            view["state"] = summary
            view["diverged"] = "current summary contradicts the committed closure"
            view["source"] = "diverged"
            return view
        view["closed"] = True
        view["source"] = "closed"
        return view
    if scan["barrier"] == "legacy":
        if kind == "current":
            view["diverged"] = "current summary contradicts a later legacy chain tail"
            view["state"] = summary
            view["source"] = "diverged"
            return view
        if kind == "legacy":
            view["legacy"] = True
            view["state"] = summary
            view["source"] = "legacy"
            return view
        if kind == "closed":
            view["closed"] = True
            view["closed_marker"] = summary
            view["source"] = "closed"
            return view
        if kind == "corrupt":
            view["diverged"] = "chain summary is corrupt"
            view["source"] = "diverged"
            return view
        if kind == "unknown":
            view["diverged"] = "unsupported or foreign state version"
            view["source"] = "diverged"
        return view
    if kind == "current":
        view["state"] = summary
        view["source"] = "summary-only"
        return view
    if kind == "closed":
        view["closed"] = True
        view["closed_marker"] = summary
        view["source"] = "closed"
        return view
    if kind == "legacy":
        view["legacy"] = True
        view["state"] = summary
        view["source"] = "legacy"
        return view
    if kind == "corrupt":
        view["diverged"] = "chain summary is corrupt and no journal state exists"
        view["source"] = "diverged"
        return view
    if kind == "unknown":
        view["diverged"] = "unsupported or foreign state version"
        view["source"] = "diverged"
    return view


def has_current_state(root: Path, chain: str) -> bool:
    view = _effective_view(root, chain)
    state = view["state"]
    return isinstance(state, dict) and state.get("execution_mode") == "current" and state.get("lifecycle") in LIFECYCLES


def _current_marker(summary) -> bool:
    return isinstance(summary, dict) and (
        summary.get("execution_mode") == "current" or summary.get("schema_version") in SUPPORTED_CURRENT_SCHEMAS
    )


def refuse_current_spawn(root: Path, chain: str) -> None:
    view = _effective_view(root, chain)
    if (
        view["summary_kind"] != "unknown"
        and not _current_marker(view["summary"])
        and not view["scan"]["saw_current_row"]
    ):
        return
    if view["legacy"]:
        return
    if view["closed"]:
        if view["summary"] != view["closed_marker"]:
            raise ChainSpawnError(
                "current closure is durable but its summary needs repair; retry chain_session.py close"
            )
        return
    state = view["state"]
    if isinstance(state, dict) and state.get("execution_mode") == "current" and state.get("lifecycle") in LIFECYCLES:
        raise ChainSpawnError(
            f"an active current-session {chain} chain exists "
            f"(revision {state.get('revision')}, lifecycle {state.get('lifecycle')}); "
            f"continue it with scripts/chain_session.py or close it before spawning a fresh session"
        )
    if view["diverged"]:
        raise ChainSpawnError(
            f"current-session {chain} state is diverged or unreadable ({view['diverged']}); "
            "inspect it with scripts/chain_session.py status before spawning a fresh session"
        )
    if _current_marker(view["summary"]):
        raise ChainSpawnError(
            f"a current-session {chain} marker exists but is unreadable or foreign; "
            "inspect it with scripts/chain_session.py status before spawning a fresh session"
        )


def current_status_view(root: Path, chain: str, owner: Optional[str] = None) -> Optional[Dict[str, object]]:
    view = _effective_view(root, chain)
    state = view["state"]
    if isinstance(state, dict) and state.get("execution_mode") == "current" and state.get("lifecycle") in LIFECYCLES:
        return status_payload(root, chain, owner)
    if _current_marker(view["summary"]):
        return status_payload(root, chain, owner)
    if view["closed"] and view["scan"]["saw_current_row"]:
        return status_payload(root, chain, owner)
    if view["summary_kind"] == "unknown":
        return status_payload(root, chain, owner)
    if view["scan"]["error"] is not None and view["scan"]["saw_current_row"]:
        return status_payload(root, chain, owner)
    return None


def _plan_counts(root: Path, state: Dict[str, object]) -> Optional[Dict[str, int]]:
    try:
        from autorun_spawn import count_features
    except ImportError as exc:
        raise ChainSpawnError(f"plan helpers unavailable: {exc}") from exc
    totals = {"checked": 0, "unchecked": 0, "total": 0}
    for rel in state.get("plan_docs") or []:
        try:
            path = _safe_plan_path(root, rel)
            counts = count_features(path.read_text(encoding="utf-8"))
        except (ChainSpawnError, OSError, UnicodeDecodeError):
            return None
        for key in totals:
            totals[key] += int(counts[key])
    return totals


def _autoplan_ready_check(root: Path, state: Dict[str, object]) -> Tuple[bool, Optional[str]]:
    step = state.get("step") or {}
    proof = step.get("proof") or {}
    digests = proof.get("doc_digests")
    if not isinstance(digests, dict) or not digests:
        return False, "no recorded review digests"
    for rel, digest in digests.items():
        try:
            path = _safe_plan_path(root, rel)
        except ChainSpawnError as exc:
            return False, str(exc)
        try:
            current = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            return False, f"reviewed document missing: {rel} ({exc})"
        if current != digest:
            return False, f"reviewed document changed: {rel}"
    try:
        from autoplan_gate import gate_payload

        gate = gate_payload(root, ",".join(str(rel) for rel in state.get("plan_docs") or []))
    except ChainSpawnError as exc:
        return False, f"gate failed: {exc}"
    if not gate.get("ready"):
        failed = [check["id"] for check in gate.get("checks", []) if check.get("status") == "fail"]
        return False, "gate not ready: " + ", ".join(failed)
    return True, None


def _archive_integrity(root: Path, state: Dict[str, object]) -> Tuple[bool, Optional[str]]:
    step = state.get("step") or {}
    proof = step.get("proof") or {}
    hashes = proof.get("archive_hashes")
    slug = step.get("slug")
    if not hashes or not slug:
        return True, None
    archive = _safe_path(root, root / ".spec" / "specs" / "archive" / str(slug))
    for name, digest in hashes.items():
        if not isinstance(name, str) or "/" in name or ".." in Path(name).parts or not name:
            return False, f"unsafe recorded archive name: {name!r}"
        try:
            current = hashlib.sha256(_safe_path(archive, archive / name).read_bytes()).hexdigest()
        except OSError as exc:
            return False, f"archived file missing: {name} ({exc})"
        if current != digest:
            return False, f"archived file changed: {name}"
    return True, None


def _active_package_names(root: Path) -> List[str]:
    specs_dir = _safe_path(root, root / ".spec" / "specs")
    if not specs_dir.is_dir():
        return []
    try:
        entries = list(specs_dir.iterdir())
        if any(path.is_symlink() for path in entries):
            raise ChainSpawnError("symlink in the active-package directory")
        return sorted(path.name for path in entries if path.is_dir() and path.name != "archive")
    except OSError as exc:
        raise ChainSpawnError(f"cannot inspect .spec/specs: {exc}") from exc


def _complete_block_reason(root: Path, chain: str, state: Dict[str, object]) -> Optional[str]:
    if chain != "autorun":
        return None
    step = state.get("step") or {}
    if (
        step.get("kind") != "package"
        or step.get("status") != "complete"
        or step.get("outcome") != "passed"
        or not (step.get("proof") or {}).get("archive_hashes")
    ):
        return "no accepted archive proof for the completed round"
    try:
        if _active_package_names(root):
            return "active packages remain"
        if not _recorded_complete(root, state):
            return "recorded scope still has unchecked features"
        intact, reason = _archive_integrity(root, state)
        if not intact:
            return reason
    except ChainSpawnError as exc:
        return str(exc)
    return None


def _scope_block_reason(root: Path, chain: str, state: Dict[str, object]) -> Optional[str]:
    step = state.get("step") or {}
    if chain == "autoplan" and step.get("kind") == "framework" and step.get("status") in ("active", "paused"):
        return None
    recorded = state.get("feature_fingerprint")
    if not isinstance(recorded, str) or not recorded:
        return None
    try:
        current = _fingerprint(root, [str(rel) for rel in state.get("plan_docs") or []])
    except ChainSpawnError as exc:
        return str(exc)
    if current != recorded:
        return "recorded planning features changed"
    return None


def status_payload(root: Path, chain: str, owner: Optional[str] = None) -> Dict[str, object]:
    root = root.resolve()
    view = _effective_view(root, chain)
    if view["summary_kind"] == "unknown" and not view["rows"]:
        raise ChainSpawnError(
            "chain state uses an unsupported version or foreign marker; refusing to interpret it (fail closed)"
        )
    payload: Dict[str, object] = {
        "project": str(root),
        "chain": chain,
        "execution_mode": "current",
        "schema_version": SCHEMA_VERSION,
        "started": False,
        "source": view["source"],
    }
    if view["legacy"] or view["summary_kind"] == "legacy":
        payload["legacy"] = True
        payload["next_action"] = "legacy"
    if view["closed"]:
        payload["closed"] = True
        marker = view["closed_marker"]
        if isinstance(marker, dict):
            payload["chain_id"] = marker.get("chain_id")
            payload["revision"] = marker.get("revision")
            if type(marker.get("schema_version")) is int:
                payload["schema_version"] = marker["schema_version"]
        payload["next_action"] = "start"
    if view["diverged"]:
        payload["diverged"] = view["diverged"]
        payload["next_action"] = "state_diverged"
    state = view["state"]
    if isinstance(state, dict) and type(state.get("schema_version")) is int:
        payload["schema_version"] = state["schema_version"]
    if not (
        isinstance(state, dict) and state.get("execution_mode") == "current" and state.get("lifecycle") in LIFECYCLES
    ):
        payload.setdefault("next_action", "start")
        return payload
    counter_key, cap_key = _counter_key(chain)
    payload.update(
        {
            "started": True,
            "lifecycle": state.get("lifecycle"),
            "revision": state.get("revision"),
            "chain_id": state.get("chain_id"),
            "host": state.get("host"),
            "client_surface": state.get("client_surface"),
            "step": state.get("step"),
            "completed_step_ids": state.get("completed_step_ids"),
            "counter": state.get(counter_key),
            "cap": state.get(cap_key),
            "plan_docs": state.get("plan_docs"),
            "owner_known": view["owner_hash"] is not None,
            "summary_kind": view["summary_kind"],
        }
    )
    if owner is not None:
        _validate_owner(owner)
        payload["owner_match"] = None if view["owner_hash"] is None else _hash_owner(owner) == view["owner_hash"]
    if view["diverged"]:
        payload["next_action"] = "state_diverged"
        return payload
    counts = _plan_counts(root, state)
    if counts is not None:
        payload["plan"] = counts
    step = state.get("step") or {}
    lifecycle = state.get("lifecycle")
    try:
        if lifecycle == "ready":
            ok, reason = _autoplan_ready_check(root, state)
            payload["ready_valid"] = ok
            if reason:
                payload["ready_reason"] = reason
            payload["next_action"] = "stop_ready" if ok else "blocked"
        elif lifecycle == "complete":
            reason = _complete_block_reason(root, chain, state)
            if reason:
                payload["block_reason"] = reason
            payload["next_action"] = "stop_complete" if reason is None else "blocked"
        else:
            scope_reason = _scope_block_reason(root, chain, state)
            if scope_reason:
                payload["block_reason"] = scope_reason
                payload["next_action"] = "blocked"
            elif step.get("status") == "paused":
                payload["next_action"] = "resume" if view["owner_hash"] else "takeover"
            elif step.get("status") == "active":
                payload["next_action"] = (
                    "continue" if view["owner_hash"] and payload.get("owner_match") is not False else "takeover"
                )
            elif step.get("status") == "complete":
                cap = state.get(cap_key)
                if view["owner_hash"] is None or payload.get("owner_match") is False:
                    payload["next_action"] = "takeover"
                elif cap is not None and int(state[counter_key]) >= int(cap):
                    payload["next_action"] = "cap"
                else:
                    payload["next_action"] = "begin"
            else:
                payload["next_action"] = "blocked"
        if chain == "autorun":
            intact, reason = _archive_integrity(root, state)
            payload["archive_intact"] = intact
            if reason:
                payload["archive_reason"] = reason
    except ChainSpawnError as exc:
        payload["next_action"] = "blocked"
        payload["block_reason"] = str(exc)
    return payload


def render_status(payload: Dict[str, object]) -> str:
    lines = [f"project: {payload['project']}", f"chain: {payload['chain']}", "execution: current"]
    if payload.get("legacy"):
        lines.append("note: a legacy fresh-session summary exists")
    if not payload.get("started"):
        lines.append("state: not started")
        if payload.get("diverged"):
            lines.append(f"diverged: {payload['diverged']}")
        lines.append(f"next: {payload.get('next_action')}")
        return "\n".join(lines)
    counter_name = "pass" if payload["chain"] == "autoplan" else "round"
    cap = payload.get("cap")
    lines.append(f"lifecycle: {payload.get('lifecycle')}")
    lines.append(f"revision: {payload.get('revision')}")
    lines.append(f"{counter_name}: {payload.get('counter')} (cap {cap if cap is not None else 'none'})")
    step = payload.get("step") or {}
    if step:
        lines.append(f"step: {step.get('id')} [{step.get('status')}] kind={step.get('kind')}")
    if payload.get("plan"):
        plan = payload["plan"]
        lines.append(f"plan: {plan['checked']}/{plan['total']} features checked ({plan['unchecked']} unchecked)")
    if payload.get("diverged"):
        lines.append(f"diverged: {payload['diverged']}")
    if "ready_valid" in payload:
        lines.append(f"ready-valid: {payload['ready_valid']} {payload.get('ready_reason') or ''}".rstrip())
    if payload.get("archive_reason"):
        lines.append(f"archive: {payload['archive_reason']}")
    if payload.get("block_reason"):
        lines.append(f"blocked: {payload['block_reason']}")
    lines.append(f"next: {payload.get('next_action')}")
    lines.append(f"source: {payload.get('source')}")
    return "\n".join(lines)


def _require_mutation_platform() -> None:
    name = platform.system()
    if name == "Windows" or name.startswith(("MSYS", "MINGW", "CYGWIN")):
        raise ChainSpawnError(
            "native Windows current-session execution is not supported; "
            "run the skill inside WSL2 (Linux environment) instead"
        )


def _require_git_workspace(root: Path) -> Path:
    overrides = [name for name in support.GIT_REPOSITORY_ENV if name in os.environ]
    if overrides:
        raise ChainSpawnError(
            "current-session mutations require a filesystem-bound Git workspace; "
            "unset repository overrides: " + ", ".join(overrides)
        )

    def probe(*rev_args: str):
        try:
            return subprocess.run(
                ["git", "-C", str(root), "rev-parse", *rev_args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env=support.chain_git_probe_env(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ChainSpawnError(f"cannot resolve the Git workspace: {exc}") from exc

    top = probe("--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        raise ChainSpawnError(
            "current-session mutations require a real Git work tree "
            "(a linked-worktree .git file is supported; a bare repository or fake .git is not)"
        )
    top_path = Path(top.stdout.strip()).resolve()
    if top_path != root:
        raise ChainSpawnError(f"--root must be the Git worktree root {top_path}, not a subdirectory of it")
    probe_dir = probe("--absolute-git-dir")
    if probe_dir.returncode != 0 or not probe_dir.stdout.strip():
        raise ChainSpawnError("cannot resolve the Git dir for current-session state")
    git_dir = Path(probe_dir.stdout.strip()).resolve()
    if not git_dir.is_dir():
        raise ChainSpawnError(f"resolved Git dir does not exist: {git_dir}")
    if git_dir != support.chain_runtime_root(root).parent.resolve():
        raise ChainSpawnError("resolved Git dir does not match the workspace runtime root")
    return git_dir


def _operation_id(inputs: Dict[str, object]) -> str:
    normalized = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _append_journal_row(path: Path, row: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell():
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.write(b"\n")
        handle.write((json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def _repair_torn_journal(journal: Path, scan: Dict[str, object]) -> None:
    if not scan["torn"]:
        return
    raw = journal.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != scan["journal_digest"]:
        raise ChainSpawnError("journal changed during tail recovery; re-read status")
    backup = journal.with_name(f"spawns.torn-{digest}.jsonl")
    boundary = journal.parent.parent.parent
    for path in (backup, backup.with_name(f".{backup.name}.tmp"), journal.with_name(f".{journal.name}.tmp")):
        _safe_path(boundary, path)
    if backup.exists():
        if backup.read_bytes() != raw:
            raise ChainSpawnError("torn-journal recovery backup conflicts with the original bytes")
    else:
        _atomic_write(backup, raw.decode("utf-8"))
    _atomic_write(journal, raw[: scan["tail_offset"]].decode("utf-8"))


def _find_operation(view, operation_id):
    row = view.get("last_current_row")
    if row is None or row.get("operation_id") != operation_id:
        return None
    state = view.get("state")
    if state is not None and row["state"]["chain_id"] != state["chain_id"]:
        return None
    return row


def _receipt_from_row(row: Dict[str, object], chain: str, verb: str, operation_id: str) -> Dict[str, object]:
    state = row.get("state") or {}
    return {
        "chain": chain,
        "verb": verb,
        "operation_id": operation_id,
        "duplicate": True,
        "dry_run": False,
        "revision": state.get("revision"),
        "state": state,
        "status": "already_closed" if support.closed_chain_marker(state) else "recorded",
    }


def _repair_summary(root: Path, chain: str, view: Dict[str, object]) -> None:
    target = None
    if view["source"] == "journal" and isinstance(view["state"], dict):
        target = view["state"]
    elif view["closed"] and isinstance(view["closed_marker"], dict):
        target = view["closed_marker"]
    if target is None or view["summary"] == target:
        return
    try:
        _atomic_write(_summary_path(root, chain), json.dumps(target, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        raise ChainSpawnError(
            "transition is already committed to the journal, but summary repair failed; "
            f"fix storage and retry the identical operation: {exc}"
        ) from exc


def _expect_revision(state: Dict[str, object], expected: int) -> None:
    actual = state.get("revision")
    if actual != expected:
        raise ChainSpawnError(
            f"stale revision: expected {expected}, recorded revision is {actual}; "
            "re-read status and retry with the current revision"
        )


def _require_local_owner(view: Dict[str, object], owner: str) -> str:
    caller = _hash_owner(owner)
    known = view["owner_hash"]
    if known is None:
        raise ChainSpawnError(
            "this chain has no local owner record (foreign or summary-only state); "
            "run resume --takeover to establish local ownership first"
        )
    if caller != known:
        raise ChainSpawnError(
            "--owner does not match the recorded chain owner; "
            "use resume --takeover only after confirming the former session stopped"
        )
    return caller


def _guard_mutable_view(view: Dict[str, object]) -> Dict[str, object]:
    if view["legacy"]:
        raise ChainSpawnError(
            "an active legacy fresh-session chain exists; finish/close it with the spawn scripts "
            "before starting a current-session chain (no automatic conversion)"
        )
    if view["diverged"]:
        raise ChainSpawnError(f"current chain state diverged: {view['diverged']} (fail closed)")
    state = view["state"]
    if not isinstance(state, dict):
        raise ChainSpawnError("no current-session chain state to mutate")
    return state


def _older_current_schema(doc) -> bool:
    version = doc.get("schema_version") if isinstance(doc, dict) else None
    return type(version) is int and version in SUPPORTED_CURRENT_SCHEMAS and version < SCHEMA_VERSION


def _enforce_chain_identity(view: Dict[str, object], verb: str, chain_id: str) -> None:
    if view["legacy"]:
        raise ChainSpawnError(
            "an active legacy fresh-session chain exists; finish/close it with the spawn scripts "
            "before starting a current-session chain (no automatic conversion)"
        )
    state = view["state"]
    if isinstance(state, dict) and state.get("execution_mode") == "current" and state.get("lifecycle") in LIFECYCLES:
        if chain_id != state.get("chain_id"):
            raise ChainSpawnError(
                "--chain-id refers to a different generation; this request was not applied. "
                "Inspect the intended chain before issuing new work; never replace a stale request's chain-id."
            )
        return
    marker = view["closed_marker"]
    marker_id = marker.get("chain_id") if isinstance(marker, dict) else None
    if verb == "begin":
        if (marker_id is not None and chain_id == marker_id) or chain_id in view["scan"]["known_chain_ids"]:
            raise ChainSpawnError(
                "--chain-id was already used by an earlier generation; do not replay completed work. "
                "Only an explicitly new chain may use a freshly generated id."
            )
        return
    if not view["closed"]:
        return
    if not _valid_chain_id(marker_id):
        raise ChainSpawnError(
            "this chain closed before caller-chosen chain ids existed; "
            "manage it with the legacy spawn/lifecycle tools rather than inventing an id"
        )
    if chain_id != marker_id:
        raise ChainSpawnError(
            "--chain-id does not identify the recorded closure; this request was not applied. "
            "Do not retarget a stale close request to another chain."
        )


def _verify_terminal_claim(root: Path, chain: str, doc: Dict[str, object]) -> None:
    lifecycle = doc.get("lifecycle")
    if lifecycle == "ready" and chain == "autoplan":
        ok, reason = _autoplan_ready_check(root, doc)
        if not ok:
            raise ChainSpawnError(
                f"the recorded ready claim no longer verifies ({reason}); run status for recovery guidance"
            )
    elif lifecycle == "complete" and chain == "autorun":
        reason = _complete_block_reason(root, chain, doc)
        if reason:
            raise ChainSpawnError(
                f"the recorded complete claim no longer verifies ({reason}); run status for recovery guidance"
            )
    elif lifecycle == "active":
        step = doc.get("step") or {}
        if step.get("status") in ("active", "paused"):
            _resume_scope_check(root, chain, doc, step)
        elif chain == "autorun":
            _require_fingerprint_match(root, doc)
            intact, reason = _archive_integrity(root, doc)
            if not intact:
                raise ChainSpawnError(f"archived evidence changed: {reason}")


def _transact(
    root: Path,
    chain: str,
    verb: str,
    args,
    inputs: Dict[str, object],
    transform,
) -> Dict[str, object]:
    _require_mutation_platform()
    git_dir = _require_git_workspace(root)
    spec_dir = _safe_path(root, root / ".spec")
    summary_dir = _safe_path(root, spec_dir / chain)
    state_path = _safe_path(root, summary_dir / support.CHAIN_STATE_FILE)
    _safe_path(root, state_path.with_name(f".{state_path.name}.tmp"))
    _safe_path(root, summary_dir / "history")
    runtime_root = _safe_path(git_dir, git_dir / "spec-chain")
    runtime_dir = _safe_path(git_dir, runtime_root / chain)
    journal = _safe_path(git_dir, runtime_dir / support.CHAIN_SPAWNS_FILE)
    _safe_path(git_dir, runtime_dir / support.CHAIN_LOCK_FILE)
    operation_id = _operation_id(inputs)
    dry_run = bool(getattr(args, "dry_run", False))

    def run() -> Dict[str, object]:
        view = _effective_view(root, chain)
        if view["diverged"]:
            raise ChainSpawnError(f"current chain state diverged: {view['diverged']}")
        _enforce_chain_identity(view, verb, args.chain_id)
        if verb == "close" and view["closed"]:
            marker = view["closed_marker"]
            if not dry_run and view["summary"] != marker:
                _repair_torn_journal(journal, view["scan"])
                _repair_summary(root, chain, view)
            return {
                "chain": chain,
                "verb": verb,
                "status": "already_closed",
                "duplicate": True,
                "dry_run": dry_run,
                "revision": marker.get("revision"),
                "state": marker,
            }
        prior = _find_operation(view, operation_id)
        if prior is not None:
            _verify_terminal_claim(root, chain, prior.get("state") or {})
            if not dry_run:
                _repair_torn_journal(journal, view["scan"])
                _repair_summary(root, chain, view)
            receipt = _receipt_from_row(prior, chain, verb, operation_id)
            receipt["dry_run"] = dry_run
            return receipt
        doc, owner_hash, extra = transform(view, args)
        if doc is None:
            base = extra.get("state")
            if verb == "begin" and _older_current_schema(base):
                _verify_terminal_claim(root, chain, base)
                doc = dict(base)
                doc["schema_version"] = SCHEMA_VERSION
                doc["revision"] = int(base["revision"]) + 1
                doc["updated_at"] = support.utc_now()
                extra = {key: value for key, value in extra.items() if key not in ("state", "revision")}
                extra["upgraded_from"] = base["schema_version"]
            else:
                _verify_terminal_claim(root, chain, base or {})
                if not dry_run:
                    _repair_torn_journal(journal, view["scan"])
                return {"chain": chain, "verb": verb, "duplicate": True, "dry_run": dry_run, **extra}
        if _older_current_schema(doc):
            extra = {**extra, "upgraded_from": doc["schema_version"]}
            doc["schema_version"] = SCHEMA_VERSION
        if dry_run:
            return {
                "chain": chain,
                "verb": verb,
                "operation_id": operation_id,
                "duplicate": False,
                "dry_run": True,
                "revision": doc.get("revision"),
                "state": doc,
                **extra,
            }
        row = {
            "event": "current_transition",
            "state": doc,
            "owner_hash": owner_hash,
            "operation_id": operation_id,
        }
        _repair_torn_journal(journal, view["scan"])
        try:
            _append_journal_row(journal, row)
        except OSError as exc:
            raise ChainSpawnError(
                "durable journal append raised an error and the commit status is uncertain "
                "(the row may or may not have landed); inspect status, then retry the "
                f"identical operation: {exc}"
            ) from exc
        try:
            _atomic_write(state_path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
        except OSError as exc:
            raise ChainSpawnError(
                f"transition committed to the journal at revision {doc.get('revision')} "
                f"but the summary write failed; retry the identical operation or inspect status: {exc}"
            ) from exc
        return {
            "chain": chain,
            "verb": verb,
            "operation_id": operation_id,
            "duplicate": False,
            "dry_run": False,
            "revision": doc.get("revision"),
            "state": doc,
            **extra,
        }

    if dry_run:
        return run()
    with support.single_chain_lock(runtime_dir, chain, error=ChainSpawnError, record_holder=False):
        return run()


def _root_rel_arg(root: Path, raw: str, option: str) -> Tuple[str, Path]:
    if not raw or "\x00" in raw or "\\" in raw:
        raise ChainSpawnError(f"invalid {option} path")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        try:
            rel = candidate.relative_to(root)
        except ValueError as exc:
            raise ChainSpawnError(f"{option} path escapes the project root: {raw}") from exc
        raw_parts = rel.parts
    else:
        raw_parts = candidate.parts
    path = root
    for part in raw_parts:
        path = path.parent if part == ".." else path / part
        if not support.under_root(root, path):
            raise ChainSpawnError(f"{option} path escapes the project root: {raw}")
        _safe_path(root, path)
    rel = path.relative_to(root)
    _safe_path(root, path)
    resolved = path.resolve()
    if not support.under_root(root, resolved):
        raise ChainSpawnError(f"{option} path escapes the project root: {raw}")
    return rel.as_posix(), resolved


def _resolve_autoplan_plan(root: Path, explicit: Optional[str]) -> List[str]:
    if explicit:
        rels: List[str] = []
        for raw in explicit.split(","):
            raw = raw.strip()
            if not raw:
                continue
            rel, resolved = _root_rel_arg(root, raw, "--plan")
            if not resolved.is_file():
                raise ChainSpawnError(f"--plan path does not exist: {raw}")
            rels.append(rel)
        if not rels:
            raise ChainSpawnError("--plan had no usable entries")
        if len(set(rels)) != len(rels):
            raise ChainSpawnError("--plan lists the same document more than once")
        return rels
    from autoplan_spawn import DEFAULT_MASTER_DOC

    rel, resolved = _root_rel_arg(root, DEFAULT_MASTER_DOC, "master")
    if not resolved.is_file():
        raise ChainSpawnError(
            f"framework pass requires the seeded master document {DEFAULT_MASTER_DOC} "
            "(or pass --plan naming the cluster's master document)"
        )
    return [rel]


def _resolve_autorun_plan(root: Path, explicit: Optional[str]) -> List[str]:
    from autorun_spawn import count_features, discover_plan_docs

    if explicit:
        rels: List[str] = []
        for raw in explicit.split(","):
            raw = raw.strip()
            if not raw:
                continue
            rel, resolved = _root_rel_arg(root, raw, "--plan")
            if not resolved.is_file():
                raise ChainSpawnError(f"--plan path does not exist: {raw}")
            try:
                if not int(count_features(resolved.read_text(encoding="utf-8"))["total"]):
                    raise ChainSpawnError(f"selected planning document has no feature checkboxes: {raw}")
                rels.append(rel)
            except (OSError, UnicodeDecodeError) as exc:
                raise ChainSpawnError(f"--plan path unreadable: {raw}: {exc}") from exc
        if len(set(rels)) != len(rels):
            raise ChainSpawnError("--plan lists the same document more than once")
        if not rels:
            raise ChainSpawnError("no qualifying planning document found; write feature checkboxes or pass --plan")
        return rels
    docs, _scanned = discover_plan_docs(root, None)
    rels = []
    for doc in docs:
        try:
            rel = _rel(root, _safe_path(root, doc))
        except (ChainSpawnError, ValueError) as exc:
            raise ChainSpawnError(f"planning candidate is unsafe: {doc}") from exc
        rels.append(rel)
    if not rels:
        raise ChainSpawnError("no qualifying planning document found; write feature checkboxes or pass --plan")
    return rels


def _master_targets(root: Path, master_rel: str) -> List[str]:
    master = _safe_plan_path(root, master_rel)
    if not master.is_file():
        raise ChainSpawnError(f"master document missing: {master_rel}")
    try:
        text = master.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ChainSpawnError(f"master document unreadable: {exc}") from exc
    targets: List[str] = []
    for raw in MD_LINK.findall(text):
        if raw.startswith(SKIP_LINK_PREFIXES):
            continue
        target = raw.partition("#")[0]
        if not target or not target.lower().endswith(".md"):
            continue
        rel, _ = _root_rel_arg(root, (Path(master_rel).parent / target).as_posix(), "master link")
        if rel not in targets:
            targets.append(rel)
    return targets


def _resolve_detail_target(root: Path, masters: List[str], raw_target: str) -> str:
    if not raw_target or Path(raw_target).is_absolute():
        raise ChainSpawnError("--target must be a root-relative .md link from the master document")
    normalized, _ = _root_rel_arg(root, raw_target, "--target")
    parts = Path(normalized).parts
    if not normalized.lower().endswith(".md"):
        raise ChainSpawnError(f"--target must be a markdown document: {raw_target}")
    if "archive" in parts:
        raise ChainSpawnError(f"--target must not point into an archive: {raw_target}")
    _safe_path(root, root / normalized)
    if not support.under_root(root, (root / normalized).resolve()):
        raise ChainSpawnError(f"--target escapes the project root: {raw_target}")
    targets: List[str] = []
    for master_rel in masters:
        for rel in _master_targets(root, master_rel):
            if rel not in targets:
                targets.append(rel)
    if normalized not in targets:
        raise ChainSpawnError(f"--target {normalized} is not linked from the selected master(s)")
    return normalized


def _fingerprint(root: Path, plan_docs: List[str]) -> Optional[str]:
    try:
        from autorun_spawn import count_features
    except ImportError as exc:
        raise ChainSpawnError(f"plan helpers unavailable: {exc}") from exc
    paths = []
    total = 0
    for rel in plan_docs:
        path = _safe_plan_path(root, rel)
        if not path.is_file():
            raise ChainSpawnError(f"plan document missing: {rel}")
        try:
            total += int(count_features(path.read_text(encoding="utf-8"))["total"])
        except (OSError, UnicodeDecodeError) as exc:
            raise ChainSpawnError(f"plan document unreadable: {rel}: {exc}") from exc
        paths.append(path)
    if not total:
        return None
    return support.feature_fingerprint(paths, root)


def _require_fingerprint_match(root: Path, state: Dict[str, object]) -> None:
    recorded = state.get("feature_fingerprint")
    if not isinstance(recorded, str) or not recorded:
        raise ChainSpawnError("chain has no frozen feature fingerprint; finish the framework pass first")
    current = _fingerprint(root, [str(rel) for rel in state["plan_docs"]])
    if current != recorded:
        raise ChainSpawnError(
            "recorded planning features changed; a scope revision requires an explicitly "
            "named framework pass, not silent drift"
        )


def _master_has_goal_and_features(root: Path, master_rel: str) -> Tuple[bool, bool]:
    from autoplan_gate import _has_goal_section
    from autorun_spawn import count_features

    path = _safe_plan_path(root, master_rel)
    if not path.is_file():
        raise ChainSpawnError(f"master document missing: {master_rel}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ChainSpawnError(f"master document unreadable: {exc}") from exc
    return _has_goal_section(text), int(count_features(text)["total"]) > 0


def _linked_detail_docs(root: Path, state: Dict[str, object]) -> List[str]:
    from chain_recovery import PLANNING_INDEX_DOC

    plan_docs = [str(rel) for rel in state.get("plan_docs") or []]
    if not plan_docs:
        raise ChainSpawnError("no recorded master document")
    docs: List[str] = []
    for master_rel in plan_docs:
        for rel in _master_targets(root, master_rel):
            if rel == master_rel or rel in plan_docs or rel == PLANNING_INDEX_DOC:
                continue
            if "archive" in Path(rel).parts:
                continue
            if rel not in docs:
                docs.append(rel)
    return docs


def _detail_complete(root: Path, rel: str) -> bool:
    from chain_recovery import detail_doc_complete

    _safe_plan_path(root, rel)
    return detail_doc_complete(root, rel)


def _autorun_package_checks(root: Path, slug: str) -> None:
    import spec_package_support as pkg

    specs_dir = _safe_path(root, root / ".spec" / "specs")
    active = _active_package_names(root)
    if len(active) > 1:
        raise ChainSpawnError(f"multiple active packages exist ({', '.join(sorted(active))}); disambiguate first")
    if slug not in active:
        raise ChainSpawnError(f"package {slug} is not the one active Development Record under .spec/specs/")
    package = specs_dir / slug
    spec_path = package / "spec.md"
    try:
        content = pkg.read_regular_text(spec_path)
    except ValueError as exc:
        raise ChainSpawnError(f"package spec unreadable: {exc}") from exc
    ok, reason = pkg.validate_branch_bound_package(root, slug, content, allow_non_git=False)
    if not ok:
        raise ChainSpawnError(f"branch binding failed: {reason}")


def _autorun_finish_proof(root: Path, state: Dict[str, object], slug: str) -> Dict[str, object]:
    import check_spec_package
    import complete_spec_package
    import issue_closure_support
    import spec_package_support as pkg

    archive = _safe_path(root, root / ".spec" / "specs" / "archive" / slug)
    if archive.is_symlink() or not archive.is_dir():
        raise ChainSpawnError(
            f"recorded package {slug} must be archived under .spec/specs/archive/ before finish "
            "(run the done stage first)"
        )
    if (root / ".spec" / "specs" / slug).exists():
        raise ChainSpawnError(f"package {slug} is still active; archive it before finish")
    try:
        complete_spec_package.validate_package_tree(archive)
    except ValueError as exc:
        raise ChainSpawnError(f"archived package is unsafe: {exc}") from exc
    try:
        spec_content = pkg.read_regular_text(archive / "spec.md")
        tasks_content = pkg.read_regular_text(archive / "tasks.md")
    except ValueError as exc:
        raise ChainSpawnError(f"archived package triad unreadable: {exc}") from exc
    bound, reason = pkg.validate_branch_bound_package(root, slug, spec_content, allow_non_git=False)
    if not bound:
        raise ChainSpawnError(f"branch binding failed: {reason}")
    checklist_path = archive / "checklist.md"
    checklist_content = None
    if checklist_path.exists():
        try:
            checklist_content = pkg.read_regular_text(checklist_path)
        except ValueError as exc:
            raise ChainSpawnError(f"archived checklist unreadable: {exc}") from exc
    if not check_spec_package.overall_check_passed(
        spec_content, tasks_content, checklist_content, slug=slug, root=root, package_dir=archive
    ):
        raise ChainSpawnError("archived package does not pass the acceptance gates")
    physical = check_spec_package.package_physical_state_errors(archive)
    if physical:
        raise ChainSpawnError("archived package physical state invalid: " + "; ".join(physical))
    summary_path = archive / "completion-summary.md"
    try:
        summary_content = pkg.read_regular_text(summary_path)
    except ValueError as exc:
        raise ChainSpawnError(f"completion summary unreadable: {exc}") from exc
    failures = issue_closure_support.validate_completion_summary(
        summary_content,
        specs_root=root / ".spec",
        current_slug=slug,
        current_tasks_content=tasks_content,
    )
    if failures:
        raise ChainSpawnError("completion summary invalid: " + "; ".join(failures))
    hashes: Dict[str, str] = {}
    for path in sorted(archive.iterdir()):
        if path.is_file() and not path.is_symlink():
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"archive": _rel(root, archive), "archive_hashes": hashes, "slug": slug}


def _recorded_complete(root: Path, state: Dict[str, object]) -> bool:
    from chain_lifecycle import recorded_plan

    proof = recorded_plan(root, state)
    return bool(proof["complete"])


def _same_assignment(existing: Dict[str, object], *, kind: str, target, slug, plan_docs: List[str], state) -> bool:
    if existing.get("kind") != kind or existing.get("target") != target or existing.get("slug") != slug:
        return False
    return plan_docs == list(state.get("plan_docs") or [])


def _resolve_begin_params(root: Path, chain: str, args, state: Optional[Dict[str, object]]):
    if chain == "autoplan":
        if not args.kind:
            raise ChainSpawnError("autoplan begin requires --kind framework|detail|review")
        if args.slug:
            raise ChainSpawnError("--slug is autorun-only")
        if state is not None and not args.plan:
            plan_docs = [str(rel) for rel in state.get("plan_docs") or []]
        else:
            plan_docs = _resolve_autoplan_plan(root, args.plan)
        if state is not None and plan_docs != list(state.get("plan_docs") or []):
            raise ChainSpawnError(
                "--plan does not match the recorded plan_docs of this chain; scope changes need a new chain after close"
            )
        kind = args.kind
        target = None
        if state is None and kind != "framework":
            raise ChainSpawnError("the first current-session pass must be --kind framework")
        if kind == "detail":
            if not args.target:
                raise ChainSpawnError("detail begin requires --target <linked .md doc>")
            target = _resolve_detail_target(root, plan_docs, args.target)
        elif args.target:
            raise ChainSpawnError("--target only applies to --kind detail")
        if state is not None:
            if kind in ("detail", "review"):
                _require_fingerprint_match(root, state)
            if kind == "review":
                details = _linked_detail_docs(root, state)
                missing = [rel for rel in details if not _detail_complete(root, rel)]
                if missing:
                    raise ChainSpawnError(
                        "review requires all linked detail docs to exist with five complete sections; "
                        "incomplete: " + ", ".join(missing)
                    )
        fingerprint = _fingerprint(root, plan_docs)
        return plan_docs, kind, target, None, fingerprint
    if args.kind or args.target:
        raise ChainSpawnError("--kind/--target are autoplan-only")
    if not args.slug:
        raise ChainSpawnError("autorun begin requires --slug <Development Record>")
    import spec_package_support as pkg

    if not pkg.is_development_record_slug(args.slug):
        raise ChainSpawnError(f"--slug must be a Development Record slug (YYYY-MM-DD_<verb>-...): {args.slug}")
    if state is not None and not args.plan:
        plan_docs = [str(rel) for rel in state.get("plan_docs") or []]
    else:
        plan_docs = _resolve_autorun_plan(root, args.plan)
    if state is not None and plan_docs != list(state.get("plan_docs") or []):
        raise ChainSpawnError("--plan does not match the recorded scope of this chain")
    if state is not None:
        _require_fingerprint_match(root, state)
    _autorun_package_checks(root, args.slug)
    fingerprint = _fingerprint(root, plan_docs)
    if not fingerprint:
        raise ChainSpawnError("recorded plan scope has no feature checkboxes; nothing to bind")
    return plan_docs, "package", None, args.slug, fingerprint


def _light_begin_params(root: Path, chain: str, args, state: Dict[str, object]):
    kind = args.kind if chain == "autoplan" else "package"
    target = os.path.normpath(args.target) if args.target else None
    slug = args.slug
    if args.plan:
        resolver = _resolve_autoplan_plan if chain == "autoplan" else _resolve_autorun_plan
        plan_docs = resolver(root, args.plan)
    else:
        plan_docs = [str(rel) for rel in state.get("plan_docs") or []]
    return kind, target, slug, plan_docs


def _cap_for(args, chain: str) -> Optional[int]:
    option = "max-passes" if chain == "autoplan" else "max-rounds"
    if args.unbounded:
        return None
    cap = args.max_passes if chain == "autoplan" else args.max_rounds
    if cap is not None:
        return support.validate_cap(cap, option)
    return None if chain == "autoplan" else support.DEFAULT_AUTORUN_MAX_ROUNDS


def _check_cap_flags(args, chain: str) -> None:
    if chain == "autoplan" and args.max_rounds is not None:
        raise ChainSpawnError("--max-rounds is the autorun cap flag; autoplan uses --max-passes or --unbounded")
    if chain == "autorun" and args.max_passes is not None:
        raise ChainSpawnError("--max-passes is the autoplan cap flag; autorun uses --max-rounds or --unbounded")


def _apply_begin(view: Dict[str, object], args, root: Path, chain: str):
    caller = _hash_owner(args.owner)
    if not STEP_ID_RE.fullmatch(args.step_id or ""):
        raise ChainSpawnError("--step-id must be nonempty, whitespace-free, <=128 chars")
    if not _valid_client(args.client):
        raise ChainSpawnError("invalid --client identifier")
    if args.surface not in SURFACES:
        raise ChainSpawnError("invalid --surface")
    _check_cap_flags(args, chain)
    state = view["state"]
    counter_key, cap_key = _counter_key(chain)
    if state is None:
        if view["legacy"]:
            raise ChainSpawnError(
                "an active legacy fresh-session chain exists; finish/close it with the spawn scripts first"
            )
        if view["diverged"]:
            raise ChainSpawnError(f"current chain state diverged: {view['diverged']}")
        if args.expected_revision != 0:
            raise ChainSpawnError("a new chain starts at revision 0; use --expected-revision 0")
        plan_docs, kind, target, slug, fingerprint = _resolve_begin_params(root, chain, args, None)
        step = {
            "id": args.step_id,
            "kind": kind,
            "target": target,
            "slug": slug,
            "status": "active",
            "outcome": None,
        }
        doc = {
            "schema_version": SCHEMA_VERSION,
            "execution_mode": "current",
            "chain": chain,
            "chain_id": args.chain_id,
            "revision": 1,
            "host": args.client,
            "client_surface": args.surface,
            "plan_docs": plan_docs,
            counter_key: 1,
            cap_key: _cap_for(args, chain),
            "feature_fingerprint": fingerprint,
            "step": step,
            "completed_step_ids": [],
            "lifecycle": "active",
            "updated_at": support.utc_now(),
            "model": None,
            "model_control": "host",
            "permission_effective": "host",
        }
        return doc, caller, {"step": step}
    state = _guard_mutable_view(view)
    _require_local_owner(view, args.owner)
    _expect_revision(state, args.expected_revision)
    if state.get("host") != args.client or state.get("client_surface") != args.surface:
        raise ChainSpawnError(
            "--client/--surface do not match the recorded chain identity; a host change needs a new chain after close"
        )
    step = state.get("step") or {}
    completed = [str(item) for item in state.get("completed_step_ids") or []]
    if args.step_id == step.get("id"):
        light_kind, light_target, light_slug, light_docs = _light_begin_params(root, chain, args, state)
        if _same_assignment(
            step,
            kind=light_kind,
            target=light_target,
            slug=light_slug,
            plan_docs=light_docs,
            state=state,
        ):
            return None, caller, {"step": step, "state": state, "revision": state.get("revision")}
        raise ChainSpawnError(f"step-id {args.step_id} is recorded for different work; choose a new step-id")
    if args.step_id in completed:
        raise ChainSpawnError(f"step-id {args.step_id} already completed; choose a new step-id")
    if step.get("status") not in (None, "complete"):
        raise ChainSpawnError(
            f"previous step {step.get('id')} is {step.get('status')}; finish or resume it before a new begin"
        )
    replanning = chain == "autoplan" and state.get("lifecycle") == "ready" and args.kind == "framework"
    if state.get("lifecycle") in ("ready", "complete") and not replanning:
        raise ChainSpawnError(f"chain is {state['lifecycle']}; close it before starting a new chain")
    plan_docs, kind, target, slug, fingerprint = _resolve_begin_params(root, chain, args, state)
    next_unit = int(state[counter_key]) + 1
    explicit = bool(args.unbounded or args.max_passes is not None or args.max_rounds is not None)
    requested_cap = _cap_for(args, chain) if explicit else state.get(cap_key)
    if requested_cap is not None and next_unit > requested_cap:
        raise ChainSpawnError(
            f"{counter_key} cap reached ({requested_cap}); finish/resume the admitted unit or close the chain"
        )
    new_step = {
        "id": args.step_id,
        "kind": kind,
        "target": target,
        "slug": slug,
        "status": "active",
        "outcome": None,
    }
    doc = dict(state)
    doc[counter_key] = next_unit
    doc[cap_key] = requested_cap
    doc["step"] = new_step
    doc["lifecycle"] = "active"
    doc["completed_step_ids"] = completed + ([str(step["id"])] if step.get("id") else [])
    doc["feature_fingerprint"] = fingerprint if kind == "framework" else state.get("feature_fingerprint")
    doc["revision"] = int(state["revision"]) + 1
    doc["updated_at"] = support.utc_now()
    return doc, caller, {"step": new_step}


def _apply_finish(view: Dict[str, object], args, root: Path, chain: str):
    state = _guard_mutable_view(view)
    caller = _require_local_owner(view, args.owner)
    _expect_revision(state, args.expected_revision)
    step = state.get("step")
    if not isinstance(step, dict) or step.get("status") != "active":
        raise ChainSpawnError("no active step to finish (paused steps need resume first)")
    kind = step.get("kind")
    outcome = args.outcome
    review_flag_used = args.review_mode is not None or args.review_note is not None
    if outcome == "revise" and not (chain == "autoplan" and kind == "review"):
        raise ChainSpawnError("--outcome revise only applies to an autoplan review step")
    if kind == "review":
        if args.review_mode is None:
            raise ChainSpawnError("review finish requires --review-mode main|independent")
        if not args.review_note or not args.review_note.strip():
            raise ChainSpawnError("review finish requires a nonempty --review-note")
    elif review_flag_used:
        raise ChainSpawnError("--review-mode/--review-note only apply to a review step")
    proof: Dict[str, object] = {}
    if chain == "autoplan":
        plan_docs = [str(rel) for rel in state.get("plan_docs") or []]
        if kind == "framework":
            for master_rel in plan_docs:
                has_goal, has_features = _master_has_goal_and_features(root, master_rel)
                if not has_goal:
                    raise ChainSpawnError(
                        f"master {master_rel} has no non-empty Goal section; expansion is not complete"
                    )
                if not has_features:
                    raise ChainSpawnError(
                        f"master {master_rel} still has no feature checkboxes; expand the framework before finish"
                    )
        elif kind == "detail":
            _require_fingerprint_match(root, state)
            target = step.get("target")
            if not target:
                raise ChainSpawnError("detail step records no target")
            rel = _resolve_detail_target(root, plan_docs, str(target))
            if not _detail_complete(root, rel):
                raise ChainSpawnError(f"detail document lacks the five required sections: {rel}")
        elif kind == "review":
            _require_fingerprint_match(root, state)
            details = _linked_detail_docs(root, state)
            if outcome == "passed":
                incomplete = [rel for rel in details if not _detail_complete(root, rel)]
                if incomplete:
                    raise ChainSpawnError(
                        "linked detail documents no longer satisfy the five-section contract: " + ", ".join(incomplete)
                    )
                from autoplan_gate import gate_payload

                gate = gate_payload(root, ",".join(plan_docs))
                if not gate.get("ready"):
                    failed = [c["id"] for c in gate.get("checks", []) if c.get("status") == "fail"]
                    raise ChainSpawnError("gate not ready: " + ", ".join(failed))
            digests: Dict[str, str] = {}
            for rel in plan_docs + details:
                if rel in digests:
                    continue
                path = _safe_plan_path(root, rel)
                try:
                    digests[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError as exc:
                    raise ChainSpawnError(f"reviewed document unreadable: {rel}: {exc}") from exc
            proof = {
                "doc_digests": digests,
                "review_mode": args.review_mode,
                "review_note": args.review_note.strip(),
            }
            if outcome == "passed":
                proof["gate"] = "passed"
    else:
        slug = str(step.get("slug") or "")
        proof = _autorun_finish_proof(root, state, slug)
    new_step = dict(step)
    new_step["status"] = "complete"
    new_step["outcome"] = outcome
    if proof:
        new_step["proof"] = proof
    doc = dict(state)
    doc["step"] = new_step
    if chain == "autoplan" and kind == "review" and outcome == "passed":
        doc["lifecycle"] = "ready"
    if chain == "autorun" and _recorded_complete(root, state):
        if not _active_package_names(root):
            doc["lifecycle"] = "complete"
    doc["revision"] = int(state["revision"]) + 1
    doc["updated_at"] = support.utc_now()
    if chain == "autoplan" and kind == "framework":
        doc["feature_fingerprint"] = _fingerprint(root, plan_docs)
    return doc, caller, {"step": new_step}


def _apply_pause(view: Dict[str, object], args, chain: str):
    state = _guard_mutable_view(view)
    caller = _require_local_owner(view, args.owner)
    _expect_revision(state, args.expected_revision)
    step = state.get("step")
    if not isinstance(step, dict) or step.get("status") != "active":
        raise ChainSpawnError("no active step to pause")
    new_step = dict(step)
    new_step["status"] = "paused"
    new_step["pause_reason"] = args.reason.strip()
    doc = dict(state)
    doc["step"] = new_step
    doc["revision"] = int(state["revision"]) + 1
    doc["updated_at"] = support.utc_now()
    return doc, caller, {"step": new_step}


def _resume_scope_check(root: Path, chain: str, state: Dict[str, object], step: Dict[str, object]) -> None:
    kind = step.get("kind")
    if chain == "autorun":
        _require_fingerprint_match(root, state)
        slug = step["slug"]
        active = _active_package_names(root)
        if slug in active:
            _autorun_package_checks(root, slug)
        else:
            if active:
                raise ChainSpawnError("another package is active; cannot resume the archived assignment")
            import spec_package_support as pkg

            path = _safe_path(root, root / ".spec/specs/archive" / slug / "spec.md")
            content = pkg.read_regular_text(path)
            bound, reason = pkg.validate_branch_bound_package(root, slug, content, allow_non_git=False)
            if not bound:
                raise ChainSpawnError(f"branch binding failed: {reason}")
    elif kind == "detail":
        _require_fingerprint_match(root, state)
        plan_docs = [str(rel) for rel in state.get("plan_docs") or []]
        _resolve_detail_target(root, plan_docs, str(step.get("target") or ""))
    elif kind == "review":
        _require_fingerprint_match(root, state)
    else:
        for rel in state["plan_docs"]:
            if not _safe_plan_path(root, rel).is_file():
                raise ChainSpawnError(f"master document missing: {rel}")


def _apply_resume(view: Dict[str, object], args, root: Path, chain: str):
    state = _guard_mutable_view(view)
    _expect_revision(state, args.expected_revision)
    caller = _hash_owner(args.owner)
    known = view["owner_hash"]
    step = state.get("step")
    lifecycle = state.get("lifecycle")
    takeover = False
    if lifecycle == "active" and isinstance(step, dict) and step.get("status") in ("active", "paused"):
        _resume_scope_check(root, chain, state, step)
        if known is None:
            if not args.takeover:
                raise ChainSpawnError(
                    "no local owner record; resume requires --takeover to establish local ownership "
                    "(confirms the former session stopped)"
                )
            new_owner = caller
            takeover = True
        elif step.get("status") == "paused":
            new_owner = caller
            takeover = caller != known
        elif caller == known:
            new_owner = known
        elif not args.takeover:
            raise ChainSpawnError(
                "step is active under another owner; resume requires --takeover "
                "after confirming the former session stopped"
            )
        else:
            new_owner = caller
            takeover = True
        new_step = dict(step)
        new_step["status"] = "active"
    else:
        if known is not None and not args.takeover:
            if not isinstance(step, dict) or step.get("status") in ("active", "paused"):
                raise ChainSpawnError(f"chain lifecycle is {lifecycle}; nothing to resume")
            raise ChainSpawnError("completed steps advance with begin; nothing to resume")
        if not args.takeover:
            raise ChainSpawnError(
                "no local owner record; resume requires --takeover to establish local ownership "
                "(confirms the former session stopped)"
            )
        new_step = dict(step) if isinstance(step, dict) else step
        new_owner = caller
        takeover = True
        _verify_terminal_claim(root, chain, state)
    doc = dict(state)
    doc["step"] = new_step
    doc["revision"] = int(state["revision"]) + 1
    doc["updated_at"] = support.utc_now()
    return doc, new_owner, {"step": new_step, "takeover": takeover}


def _apply_close(view: Dict[str, object], args, root: Path, chain: str, dry_run: bool):
    state = _guard_mutable_view(view)
    caller = _require_local_owner(view, args.owner)
    _expect_revision(state, args.expected_revision)
    step = state.get("step") or {}
    if chain == "autoplan":
        if state.get("lifecycle") != "ready":
            raise ChainSpawnError(
                f"autoplan close requires a proven ready chain (lifecycle is {state.get('lifecycle')})"
            )
        ok, reason = _autoplan_ready_check(root, state)
        if not ok:
            raise ChainSpawnError(f"readiness no longer valid: {reason}")
    else:
        if step.get("status") != "complete" or step.get("outcome") != "passed":
            raise ChainSpawnError(
                "autorun close requires a completed passed package step; finish the recorded round before closing"
            )
        proof = step.get("proof") or {}
        if not proof.get("archive_hashes"):
            raise ChainSpawnError("autorun close requires a recorded archive proof; finish the round first")
        if not _recorded_complete(root, state):
            raise ChainSpawnError("recorded scope has unchecked features; refusing close")
        active = _active_package_names(root)
        if active:
            raise ChainSpawnError(f"active package(s) remain: {', '.join(active)}")
        intact, reason = _archive_integrity(root, state)
        if not intact:
            raise ChainSpawnError(f"archived evidence changed since finish: {reason}")
    summary_text = json.dumps(state, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(summary_text.encode("utf-8")).hexdigest()
    history_dir = _safe_path(root, support.chain_dir(root, chain) / "history")
    archive_path = _safe_path(root, history_dir / f"{digest}.json")
    _safe_path(root, archive_path.with_name(f".{archive_path.name}.tmp"))
    closed_at: Optional[str] = None
    if archive_path.exists():
        try:
            existing = json.loads(archive_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ChainSpawnError(f"close archive unreadable: {exc}") from exc
        if not isinstance(existing, dict) or existing.get("previous_state_text") != summary_text:
            raise ChainSpawnError("close archive collision; refusing overwrite")
        closed_at = existing.get("closed_at")
        if not isinstance(closed_at, str) or not closed_at:
            raise ChainSpawnError("close archive is missing its closed_at; inspect history before retry")
    if closed_at is None:
        closed_at = support.utc_now()
        snapshot = {
            "previous_state": state,
            "previous_state_text": summary_text,
            "closed_at": closed_at,
            "execution_mode": "current",
        }
        if not dry_run:
            _atomic_write(archive_path, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    marker = {
        "lifecycle": "closed",
        "chain": chain,
        "archive": _rel(root, archive_path),
        "closed_at": closed_at,
        "previous_digest": digest,
        "schema_version": SCHEMA_VERSION,
        "execution_mode": "current",
        "chain_id": state.get("chain_id"),
        "revision": int(state["revision"]) + 1,
    }
    return marker, caller, {"status": "closed", "archive": marker["archive"]}


def _nonnegative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer: {text!r}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("expected-revision must be >= 0")
    return value


def _add_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("text", "json"), default="text")


def _add_mutation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chain", choices=CHAINS, required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--expected-revision", type=_nonnegative_int, required=True)
    parser.add_argument("--chain-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    _add_format(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chain_session.py",
        description="Current-session continuation protocol for /spec:autoplan and /spec:autorun.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    route = sub.add_parser("route", help="resolve client/surface to an execution mode (read-only)")
    route.add_argument("--chain", choices=CHAINS, required=True)
    route.add_argument("--client", default="unknown")
    route.add_argument("--surface", default="unknown")
    route.add_argument("--execution", default="auto")
    route.add_argument("--worker", default=None)
    route.add_argument("--root", default=None)
    _add_format(route)

    status = sub.add_parser("status", help="read the current-session checkpoint (read-only)")
    status.add_argument("--chain", choices=CHAINS, required=True)
    status.add_argument("--root", default=".")
    status.add_argument("--owner", default=None)
    _add_format(status)

    begin = sub.add_parser("begin", help="start the next pass/round step in the current session")
    _add_mutation_args(begin)
    begin.add_argument("--step-id", required=True)
    begin.add_argument("--client", required=True)
    begin.add_argument("--surface", required=True)
    begin.add_argument("--plan", default=None)
    begin.add_argument("--kind", choices=PASS_KINDS, default=None)
    begin.add_argument("--target", default=None)
    begin.add_argument("--slug", default=None)
    begin.add_argument("--max-passes", type=int, default=None)
    begin.add_argument("--max-rounds", type=int, default=None)
    begin.add_argument("--unbounded", action="store_true")

    finish = sub.add_parser("finish", help="attest the active step after doing the work")
    _add_mutation_args(finish)
    finish.add_argument("--outcome", choices=("passed", "revise"), default="passed")
    finish.add_argument("--review-mode", choices=REVIEW_MODES, default=None)
    finish.add_argument("--review-note", default=None)

    pause = sub.add_parser("pause", help="persist a controlled stop for the active step")
    _add_mutation_args(pause)
    pause.add_argument("--reason", required=True)

    resume = sub.add_parser("resume", help="resume a paused step or take over after takeover confirmation")
    _add_mutation_args(resume)
    resume.add_argument("--takeover", action="store_true")

    close = sub.add_parser("close", help="archive a proven ready/complete current chain")
    _add_mutation_args(close)
    return parser


def _inputs(chain: str, verb: str, args) -> Dict[str, object]:
    fields = {
        "chain": chain,
        "verb": verb,
        "owner": args.owner,
        "expected_revision": args.expected_revision,
        "chain_id": args.chain_id,
    }
    for name in (
        "step_id",
        "client",
        "surface",
        "plan",
        "kind",
        "target",
        "slug",
        "max_passes",
        "max_rounds",
        "unbounded",
        "outcome",
        "review_mode",
        "review_note",
        "reason",
        "takeover",
    ):
        if hasattr(args, name):
            fields[name] = getattr(args, name)
    return fields


def _render_receipt(payload: Dict[str, object]) -> str:
    lines = [f"chain: {payload['chain']}", f"verb: {payload['verb']}"]
    if payload.get("status"):
        lines.append(f"status: {payload['status']}")
    if payload.get("revision") is not None:
        lines.append(f"revision: {payload['revision']}")
    if payload.get("duplicate"):
        lines.append("duplicate: yes (existing assignment)")
    if payload.get("dry_run"):
        lines.append("dry-run: yes (nothing written)")
    if payload.get("archive"):
        lines.append(f"archive: {payload['archive']}")
    step = payload.get("step")
    if isinstance(step, dict):
        lines.append(f"step: {step.get('id')} [{step.get('status')}]")
    state = payload.get("state")
    if isinstance(state, dict) and state.get("lifecycle"):
        lines.append(f"lifecycle: {state['lifecycle']}")
    return "\n".join(lines)


def _render_route(payload: Dict[str, object]) -> str:
    worker = payload.get("worker") or "none"
    return "\n".join(
        [
            f"chain: {payload.get('chain', '-')}",
            f"execution: {payload['execution_mode']}",
            f"client: {payload['client']}",
            f"surface: {payload['surface']}",
            f"worker: {worker}",
            "requirements: " + ", ".join(payload["requirements"]),
            "native-subagents: " + str(payload["native_subagents"]),
        ]
    )


def _emit(payload: Dict[str, object], fmt: str, render) -> None:
    if fmt == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(payload))


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "route":
            payload = route_payload(args.client, args.surface, args.execution, args.worker)
            payload["chain"] = args.chain
            _emit(payload, args.format, _render_route)
            return 0
        if args.subcommand == "status":
            root = _resolve_root(args.root)
            _emit(status_payload(root, args.chain, args.owner), args.format, render_status)
            return 0
        root = _resolve_root(args.root)
        _validate_owner(args.owner)
        _validate_chain_id(args.chain_id)
        chain = args.chain
        if args.subcommand == "begin":
            if args.unbounded and (args.max_passes is not None or args.max_rounds is not None):
                parser.error("--unbounded is mutually exclusive with --max-passes/--max-rounds")
            _check_cap_flags(args, chain)
            _cap_for(args, chain)
            payload = _transact(
                root,
                chain,
                "begin",
                args,
                _inputs(chain, "begin", args),
                lambda view, a: _apply_begin(view, a, root, chain),
            )
            _emit(payload, args.format, _render_receipt)
            return 0
        if args.subcommand == "finish":
            payload = _transact(
                root,
                chain,
                "finish",
                args,
                _inputs(chain, "finish", args),
                lambda view, a: _apply_finish(view, a, root, chain),
            )
            _emit(payload, args.format, _render_receipt)
            return 0
        if args.subcommand == "pause":
            reason = (args.reason or "").strip()
            if not reason or len(reason) > MAX_PAUSE_REASON:
                parser.error("--reason must be nonempty bounded text (<=400 chars)")
            payload = _transact(
                root,
                chain,
                "pause",
                args,
                _inputs(chain, "pause", args),
                lambda view, a: _apply_pause(view, a, chain),
            )
            _emit(payload, args.format, _render_receipt)
            return 0
        if args.subcommand == "resume":
            payload = _transact(
                root,
                chain,
                "resume",
                args,
                _inputs(chain, "resume", args),
                lambda view, a: _apply_resume(view, a, root, chain),
            )
            _emit(payload, args.format, _render_receipt)
            return 0
        if args.subcommand == "close":
            payload = _transact(
                root,
                chain,
                "close",
                args,
                _inputs(chain, "close", args),
                lambda view, a: _apply_close(view, a, root, chain, bool(args.dry_run)),
            )
            _emit(payload, args.format, _render_receipt)
            return 0
        parser.error(f"unknown subcommand: {args.subcommand}")
    except ChainSpawnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: I/O failure: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

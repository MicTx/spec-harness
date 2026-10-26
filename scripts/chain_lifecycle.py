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
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional

import chain_spawn_support as support


def _safe_path(root: Path, path: Path) -> Path:
    """Refuse linked ancestors too, including links that resolve inside root."""
    if root.is_symlink():
        raise support.ChainSpawnError(f"chain path boundary is a symlink: {root}")
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


def _git(root: Path, *args: str) -> bytes:
    """Read local objects without inherited repository overrides or replacements."""
    from check_all_spec_packages import clean_git_env, trusted_git

    env = {key: value for key, value in clean_git_env().items() if not key.startswith("GIT_")}
    try:
        result = subprocess.run(
            [trusted_git(), "--no-replace-objects", "--literal-pathspecs", "-C", str(root), *args],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise support.ChainSpawnError("cannot read legacy Git evidence") from exc
    if result.returncode:
        raise support.ChainSpawnError("legacy Git evidence missing, dirty, or ancestry unproved")
    return result.stdout


def _commit_oid(root: Path, value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", value):
        raise support.ChainSpawnError("legacy evidence requires explicit commit OIDs")
    return _git(root, "rev-parse", "--verify", "--end-of-options", value + "^{commit}").decode().strip()


def _git_document(root: Path, commit: str, name: str) -> Dict[str, str]:
    entry = _git(root, "ls-tree", "-z", commit, "--", name).decode("utf-8")
    records = entry.rstrip("\0").split("\0")
    if len(records) != 1 or "\t" not in records[0]:
        raise support.ChainSpawnError(f"historical document missing: {name}")
    metadata, found = records[0].split("\t", 1)
    fields = metadata.split()
    if len(fields) != 3 or fields[0] not in ("100644", "100755") or fields[1] != "blob" or found != name:
        raise support.ChainSpawnError(f"historical document is not a regular file: {name}")
    text = _git(root, "cat-file", "blob", fields[2]).decode("utf-8")
    return {"blob": fields[2], "text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _durable_state(text: str) -> Dict[str, object]:
    state = json.loads(text)
    if not isinstance(state, dict):
        raise support.ChainSpawnError("historical chain state is not an object")
    return {key: value for key, value in state.items() if key not in support.VOLATILE_STATE_KEYS}


def legacy_recorded_plan(
    root: Path, chain: str, state: Dict[str, object], scope_ref: str, completion_ref: str
) -> Dict[str, object]:
    """Prove a legacy scope from two committed snapshots, never backfill its state.

    The scope anchor is the last committed change to this chain's state. Only
    checkbox completion may change feature identities between the two anchors.
    Later project additions are retained and inspected, not credited to the chain.
    """
    from autorun_spawn import count_features

    if "feature_fingerprint" in state:
        raise support.ChainSpawnError("legacy evidence is only for states without feature_fingerprint")
    current = recorded_plan(root, state, require_fingerprint=False)
    if not current["complete"]:
        raise support.ChainSpawnError("live recorded plan has unfinished features; refusing legacy close")
    if Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise support.ChainSpawnError("legacy evidence root must be the Git repository root")
    head = _git(root, "rev-parse", "HEAD^{commit}").decode().strip()
    scope = _commit_oid(root, scope_ref)
    completion = _commit_oid(root, completion_ref)
    _git(root, "merge-base", "--is-ancestor", scope, completion)
    _git(root, "merge-base", "--is-ancestor", completion, head)
    state_name = f".spec/{chain}/{support.CHAIN_STATE_FILE}"
    last_change = _git(root, "log", "-1", "--format=%H", head, "--", state_name).decode().strip()
    if last_change != scope:
        raise support.ChainSpawnError("legacy scope must be the last committed chain-state change")
    expected_state = {key: value for key, value in state.items() if key not in support.VOLATILE_STATE_KEYS}
    state_documents = {}
    for label, oid in (("scope", scope), ("completion", completion), ("head", head)):
        document = _git_document(root, oid, state_name)
        if _durable_state(document["text"]) != expected_state:
            raise support.ChainSpawnError("legacy chain identity changed; refusing historical close")
        state_documents[label] = document
    _git(root, "diff", "--cached", "--quiet", head, "--", state_name, *state["plan_docs"])
    documents = []
    reports = []
    totals = {"checked": 0, "unchecked": 0, "total": 0}
    digest = hashlib.sha256()
    for name in state["plan_docs"]:
        original = _git_document(root, scope, name)
        finished = _git_document(root, completion, name)
        live_committed = _git_document(root, head, name)
        if (root / name).read_bytes() != live_committed["text"].encode("utf-8"):
            raise support.ChainSpawnError(
                f"live recorded plan differs from its Git blob: {name}; "
                "uncommitted changes or checkout EOL/filter conversions are unsupported"
            )
        features = support.feature_lines(original["text"])
        if not features or len(features) != len(set(features)):
            raise support.ChainSpawnError(f"legacy scope has empty or duplicate features: {name}")
        if features != support.feature_lines(finished["text"]):
            raise support.ChainSpawnError(f"legacy feature identities changed: {name}")
        counts = count_features(finished["text"])
        if counts["unchecked"] or counts["total"] != len(features):
            raise support.ChainSpawnError(f"historical completion is incomplete: {name}")
        for item in features:
            digest.update(name.encode("utf-8") + b"\0" + item.encode("utf-8") + b"\0")
        reports.append({"path": name, **counts, "sha256": finished["sha256"]})
        for key in totals:
            totals[key] += counts[key]
        documents.append(
            {
                "path": name,
                "scope_blob": original["blob"],
                "scope_text": original["text"],
                "completion_blob": finished["blob"],
                "completion_text": finished["text"],
            }
        )
    if (
        _git(root, "rev-parse", "HEAD^{commit}").decode().strip() != head
        or recorded_plan(root, state, require_fingerprint=False) != current
    ):
        raise support.ChainSpawnError("legacy evidence changed during inspection")
    return {
        "project": str(root),
        "docs": reports,
        "totals": totals,
        "complete": True,
        "legacy_evidence": {
            "scope_commit": scope,
            "completion_commit": completion,
            "head_commit": head,
            "state_documents": state_documents,
            "documents": documents,
            "reconstructed_scope_fingerprint": digest.hexdigest(),
            "current_plan": current,
        },
    }


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


def independent_successor_proof(
    root: Path, chain: str, state: Dict[str, object], slug: str, active: list
) -> Dict[str, object]:
    """Prove an active package is new work, never the closing chain's own package.

    The chain state records no package membership, so the parameter itself proves
    nothing. What this CAN show: the package was added after both anchors and is not
    a rename or copy of earlier work. What it CANNOT show: that a package whose
    content was wholly rewritten under a new slug is unrelated to the chain's work.
    That residual gap is inherent to Git history (an operator who deliberately
    replaces a package's entire content defeats any content heuristic), so the
    flag is an explicit, auditable operator assertion backed by this evidence,
    not an automatic proof of independence. Independence is shown by Git history only: the package was added after
    both the chain's final committed state and the commit that last touched its
    recorded plan documents, so the recorded scope was already settled when the
    package appeared. Anything unproven refuses, writing nothing.
    """
    from spec_package_support import is_development_record_slug

    if not isinstance(slug, str) or "/" in slug or not is_development_record_slug(slug):
        raise support.ChainSpawnError("independent package must be a Development Record slug")
    if active != [slug]:
        raise support.ChainSpawnError("independent package must be the only active package")
    package = _safe_path(root, root / ".spec/specs" / slug / "spec.md")
    if package.is_symlink() or not package.is_file():
        raise support.ChainSpawnError("independent package has no regular spec.md")
    if Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise support.ChainSpawnError("independent package evidence root must be the Git repository root")
    state_name = f".spec/{chain}/{support.CHAIN_STATE_FILE}"
    plan_names = list(state.get("plan_docs") or [])
    package_name = f".spec/specs/{slug}/spec.md"
    # The proven artifacts (chain state, recorded plans) must equal HEAD; the package
    # itself only has to be committed, since its later edits do not change its origin.
    _git(root, "diff", "--quiet", "HEAD", "--", state_name, *plan_names)
    _git(root, "cat-file", "-e", f"HEAD:{package_name}")
    # Detect renames and copies: a package merely moved under a new slug is the same work.
    added = _git(
        root,
        "log",
        "-M",
        "-C",
        "--topo-order",
        "--reverse",
        "--diff-filter=ACR",
        "--format=%H",
        "HEAD",
        "--",
        package_name,
    )
    added_commits = added.decode().split()
    if not added_commits:
        raise support.ChainSpawnError("independent package has no committed history")
    package_commit = added_commits[0]
    # The first appearance must be a pure addition. If git sees it as a rename or copy of a path that
    # already existed at or before an anchor, this is the closing chain's own package under a new slug.
    origin = _git(
        root, "diff-tree", "-M", "-C", "-r", "--no-commit-id", "--name-status", "--diff-filter=RC", package_commit, "--"
    ).decode()
    for line in origin.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[2].startswith(".spec/specs/") and fields[2].endswith("/spec.md"):
            if fields[2] == package_name and fields[1] != package_name:
                raise support.ChainSpawnError("independent package is a rename or copy of earlier work, not new work")
    state_commit = _git(root, "log", "-1", "--format=%H", "HEAD", "--", state_name).decode().strip()
    if not state_commit:
        raise support.ChainSpawnError("chain state has no committed history")
    anchors = {"state": state_commit}
    for name in plan_names:
        anchors[name] = _git(root, "log", "-1", "--format=%H", "HEAD", "--", name).decode().strip()
    for label, anchor in anchors.items():
        if not anchor or anchor == package_commit:
            raise support.ChainSpawnError(f"independent package is not strictly newer than {label}")
        _git(root, "merge-base", "--is-ancestor", anchor, package_commit)
    return {"slug": slug, "added_commit": package_commit, "state_commit": state_commit, "plan_commits": anchors}


def close_chain(
    root: Path,
    chain: str,
    *,
    dry_run: bool = False,
    legacy_scope_commit: Optional[str] = None,
    legacy_completion_commit: Optional[str] = None,
    independent_package: Optional[str] = None,
) -> Dict[str, object]:
    """Close a completed recorded chain; no force/reset or new-scope override.

    Archive files stay tracked under .spec/<chain>/history. Runtime spawns
    remain append-only, including a closed marker so recovery cannot resurrect
    the old counter. Idempotent retries also cover partial audit/summary writes.
    The flock serializes CLI transactions, not the whole interactive session.
    """
    if (legacy_scope_commit is None) != (legacy_completion_commit is None):
        raise support.ChainSpawnError("legacy scope and completion commits must be supplied together")
    root = root.resolve()
    summary = _safe_path(root, support.chain_dir(root, chain))
    git_dir = support.chain_runtime_root(root).parent
    runtime_root = _safe_path(git_dir, support.chain_runtime_root(root))
    runtime = _safe_path(git_dir, support.chain_runtime_dir(root, chain))
    state_path = _safe_path(root, summary / support.CHAIN_STATE_FILE)
    audit_path = _safe_path(runtime_root, runtime / support.CHAIN_SPAWNS_FILE)
    history = _safe_path(root, summary / "history")
    _safe_path(runtime_root, runtime / support.CHAIN_LOCK_FILE)
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
            if state.get("execution_mode") == "current" or (
                isinstance(state.get("schema_version"), int)
                and not isinstance(state.get("schema_version"), bool)
                and state["schema_version"] >= 2
            ):
                raise support.ChainSpawnError(
                    "current-session chain checkpoint is active; close it with "
                    "scripts/chain_session.py close --owner <id> --expected-revision <n>"
                )
            proof = (
                legacy_recorded_plan(root, chain, state, legacy_scope_commit, legacy_completion_commit)
                if legacy_scope_commit is not None and legacy_completion_commit is not None
                else recorded_plan(root, state)
            )
            if not proof["complete"]:
                raise support.ChainSpawnError("recorded chain has unfinished features; refusing close")
            packages = _safe_path(root, root / ".spec/specs")
            active = (
                sorted(p.name for p in packages.iterdir() if p.is_dir() and p.name != "archive")
                if packages.exists()
                else []
            )
            if independent_package is None:
                if active:
                    raise support.ChainSpawnError("active package exists; finish/archive it before closing the chain")
            else:
                proof = {
                    **proof,
                    "independent_package": independent_successor_proof(root, chain, state, independent_package, active),
                }
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
                if matches and legacy_scope_commit is not None and tail.get("lifecycle") != "closed":
                    audit_identity = {
                        key: value for key, value in tail.items() if key not in support.VOLATILE_STATE_KEYS
                    }
                    if "spawned_at" in audit_identity:
                        audit_identity["updated_at"] = audit_identity.pop("spawned_at")
                    matches = audit_identity == {
                        key: value for key, value in state.items() if key not in support.VOLATILE_STATE_KEYS
                    }
                if not matches:
                    raise support.ChainSpawnError("chain summary and audit diverged; inspect/recover before close")
            archive = _safe_path(root, history / f"{digest}.json")
            payload = {
                "status": "would_close",
                "chain": chain,
                "archive": str(archive.relative_to(root)),
                "checked": proof["totals"]["checked"],
            }
            if state_path.read_text(encoding="utf-8") != raw:
                raise support.ChainSpawnError("chain state changed during inspection")
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
    parser.add_argument("--legacy-scope-commit", help="explicit legacy evidence: last committed chain-state change")
    parser.add_argument("--legacy-completion-commit", help="paired legacy evidence: same features, all checked")
    parser.add_argument(
        "--independent-package",
        help="slug of the single active package proven by Git history to postdate this chain's final state and plan",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")


def render_close(payload: Dict[str, object]) -> str:
    return "chain: {}\nclose: {}\narchive: {}".format(
        payload["chain"], payload["status"], payload.get("archive", "none")
    )

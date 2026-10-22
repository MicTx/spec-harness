#!/usr/bin/env python3
# scripts/autoplan_spawn.py
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Serial pass-chain spawn for ``/spec:autoplan``.

Autoplan plans and reviews through a serial fresh-session chain with the
same spawn discipline as ``/spec:autorun``: one pass = one full agent
session in its own macOS Terminal.app window or Linux/WSL2 tmux session
(attachable TUI, session persisted, never a print/exec headless mode),
strictly sequential, fail fast. No
pass runs as an in-process subagent of the spawning session (recorded in
the ``2026-10-07_add-autoplan-pass-chain`` Development Record).

Subcommands:

  - ``spawn``   open the next pass window after guards. ``--next`` names
                the pass kind (``framework`` / ``detail`` / ``review``);
                ``detail`` additionally requires ``--target <doc>`` (the
                pass's one assigned document, may be a new file, must
                stay under the project root). ``framework`` requires the
                seeded master document (first ``--plan`` entry, default
                ``.spec/plans/README.md``) to exist — evidence that the
                interrogation phase recorded its findings first. Guards
                are mechanical only (pass cap, single-chain lock, path
                bounds, seed presence); cluster qualification is the
                gate's contract in ``autoplan_gate.py``, not this
                script's;
  - ``status``  render the current pass-chain state: chain fields, the lock
                probe (shared with the autorun round spawn), the most recent
                spawn refusal from the events tail, the F4 resume decision
                from ``chain_recovery.decide``, and the detail documents with
                on-disk existence plus the five-section completeness flag.

Exit codes are part of the contract:

  - ``0`` success;
  - ``1`` operational refusal or failure (lock held, pass cap reached,
         missing seed document, missing ``--target`` for a detail pass,
         target escaping the project root, no worker host available,
         session backend failure, unreadable chain state);
  - ``2`` usage error (argparse).

Contract output goes to stdout (text, or JSON with ``--format json``);
diagnostics go to stderr. ``spawn`` writes only under ``.spec/autoplan/``:
``chain.json`` (latest pass-chain state), ``spawns.jsonl`` (append-only
audit), ``events.jsonl`` (append-only events: ``spawn_refusal`` from this
script's refusal exit and ``recycle_close`` from the detached close helper),
and the transient ``chain.lock`` — never under ``.spec/autorun/``.

The chain-state mechanics — constants, the state read/write, the lock, the
audit streams, and the Terminal.app window open/lookup/close automation —
live in ``scripts/chain_spawn_support.py`` (the F5 shared module), imported
here by name. The two spawn steps — the autorun round spawn in
``scripts/autorun_spawn.py`` and this autoplan pass spawn — plus the dead
chain-window sweep (``scripts/sweep_chain_windows.py``, F18) are the only
three callers authorized to automate Terminal.app (the spawns inherit the
recorded overturn of the 2026-09-03 no-AppleScript policy in the
``2026-10-06_add-autorun-command`` Development Record; the sweep extends
that authorization in the ``2026-10-08_sweep-dead-chain-windows``
Development Record; no other caller may reuse it). This module still imports the planning-document and worker-host
helpers from ``autorun_spawn`` — that surface is public and single-sourced
there (F5's boundary: shared mechanics in the support module, domain
helpers in the script that owns them; the F14 worker-argv render moved to
the shared module with its model-identity surface).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import chain_lifecycle  # noqa: E402
import chain_spawn_support as chain_support  # noqa: E402  # type: ignore
from autorun_spawn import (  # noqa: E402  # type: ignore  # noqa: E402  # type: ignore
    PLAN_ARCHIVE_DIR_NAME,
    PLAN_ROOT,
    NoPlanError,
    discover_plan_docs,
    plan_args_for_prompt,
    plan_candidates,
    plan_payload,
    render_last_refusal,
    render_lock,
    render_not_started,
    render_resume,
    resolve_permission_mode,
    resolve_root,
    resolve_worker_host,
)
from chain_recovery import (  # noqa: E402  # type: ignore
    append_recovery_event,
    decide,
    detail_doc_complete,
    read_audit_tail,
    recovery_event_recorded,
)
from chain_spawn_support import (  # noqa: E402  # type: ignore
    CHAIN_EVENTS_FILE,
    CHAIN_LOCK_FILE,
    CHAIN_SPAWNS_FILE,
    CHAIN_STATE_FILE,
    PERMISSION_MODES,
    WORKER_START_TIMEOUT_SECONDS,
    ChainSpawnError,
    append_audit,
    append_refusal_event,
    apply_window_geometry,
    build_terminal_command,
    build_worker_command,  # F14: shared render surface lives in the support module
    close_spawned_window_quietly,
    feature_fingerprint,
    format_chain_counter,
    model_injection_level,
    next_index_within_cap,
    parse_spawn_result,
    read_last_refusal,
    read_state,
    read_state_file,
    read_window_geometry,
    resolve_effective_cap,
    run_terminal_open,
    session_tty,
    single_chain_lock,
    under_root,
    utc_now,
    validate_cap,
    worker_pids_on_tty,
    worker_running_on_tty,
)

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

EXIT_OK = 0
EXIT_FAILURE = 1

PASS_KINDS: Tuple[str, ...] = ("framework", "detail", "review")
DEFAULT_MASTER_DOC = ".spec/plans/README.md"
CHAIN_DIR_NAME = ".spec/autoplan"


class AutoplanSpawnError(ChainSpawnError):
    """Operational refusal for the autoplan pass chain.

    Subclass of the shared ``ChainSpawnError`` base (re-exported by both
    chain scripts) — not of ``AutorunError`` — so the two chains' audit
    ``type`` values stay distinct while one ``main()`` catch handles both.
    """


def _chain_dir(root: Path) -> Path:
    return root / CHAIN_DIR_NAME


def _runtime_dir(root: Path) -> Path:
    """The per-machine runtime tree: lock, audit, events, volatile state."""
    return chain_support.chain_runtime_dir(root, "autoplan")


def _read_chain_state(root: Path) -> Optional[Dict[str, object]]:
    return read_state_file(
        _chain_dir(root) / CHAIN_STATE_FILE,
        label="pass-chain state",
        error=AutoplanSpawnError,
    )


def _resolve_root(raw_root: str) -> Path:
    try:
        return resolve_root(raw_root)
    except ChainSpawnError as exc:
        raise AutoplanSpawnError(str(exc)) from exc


def master_doc_path(root: Path, explicit: Optional[str]) -> Path:
    """Resolve the master document: first ``--plan`` entry, else ``.spec/plans/README.md``."""
    if explicit:
        first = explicit.split(",")[0].strip()
        candidate = Path(first).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        return candidate.resolve()
    return (root / DEFAULT_MASTER_DOC).resolve()


def _master_link_docs(root: Path, master_path: Path) -> List[str]:
    """Root-relative ``.spec/plans/**`` documents linked from the master document.

    A cluster master (``<slug>/master.md``) links its phase detail documents
    as siblings; the index seeds and single-round plans link across the
    planning root. Links resolving under the canonical planning root qualify
    (top-level files and cluster members); the archive and external links
    are history, not detail documents, and stay out of the list.
    """
    if not master_path.is_file():
        return []
    try:
        text = master_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    master_dir = master_path.parent
    found: List[str] = []
    for target in MARKDOWN_LINK.findall(text):
        clean = target.split("#", 1)[0].strip()
        if not clean or clean.startswith(("http://", "https://", "mailto:")):
            continue
        resolved = (master_dir / clean).resolve()
        if not under_root(root, resolved):
            continue
        rel = str(resolved.relative_to(root))
        # under the canonical planning root, at cluster depth or shallower,
        # never inside the archive directory
        if not rel.startswith(PLAN_ROOT + "/") or not rel.endswith(".md"):
            continue
        below_root = rel[len(PLAN_ROOT) + 1 :]
        if PLAN_ARCHIVE_DIR_NAME in below_root.split("/")[:-1]:
            continue
        if len(below_root.split("/")) > 2:
            continue
        if rel not in found:
            found.append(rel)
    return found


def _detail_docs(root: Path, state: Optional[Dict[str, object]]) -> List[Dict[str, object]]:
    """Detail/planning documents with on-disk existence for the status render.

    Sources: the last spawn's recorded ``plan_docs``, the phase documents the
    master plan links, and — when neither gives anything — the canonical
    ``.spec/plans/`` scan. F2 exposes ``path``/``exists`` only; the completeness
    heuristic and the divergence re-export belong to F4's classifier.
    """
    recorded: List[str] = []
    if state is not None:
        plan_docs = state.get("plan_docs")
        if isinstance(plan_docs, list):
            recorded = [item for item in plan_docs if isinstance(item, str)]
    paths: List[str] = []
    for raw in recorded:
        if raw and raw not in paths:
            paths.append(raw)
    master_rel = recorded[0] if recorded else DEFAULT_MASTER_DOC
    for raw in _master_link_docs(root, root / master_rel):
        if raw not in paths:
            paths.append(raw)
    if not paths:
        for candidate in plan_candidates(root):
            paths.append(str(candidate.relative_to(root)) if under_root(root, candidate) else str(candidate))
    return [{"path": raw, "exists": (root / raw).is_file()} for raw in paths]


def resolve_target(root: Path, target: Optional[str], kind: str) -> Optional[str]:
    """Validate the detail pass's assigned document; may be new, must stay under root."""
    if kind != "detail":
        return None
    if not target:
        raise AutoplanSpawnError(
            "--next detail requires --target <document> naming this pass's assigned detail document"
        )
    candidate = Path(target).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if not under_root(root, candidate):
        raise AutoplanSpawnError(f"--target escapes the project root: {target}")
    return str(candidate.relative_to(root)) if under_root(root, candidate) else str(candidate)


def build_pass_prompt(host: str, plan_args: List[str], max_passes: Optional[int], unbounded: bool = False) -> str:
    """Build the autoplan continuation prompt injected into the pass session.

    The cap decision travels with the prompt (F1): a bounded chain forwards
    its effective cap — explicit or the restored default — and an explicitly
    unbounded chain forwards ``--unbounded``; without the forwarding an
    unbounded chain would silently re-acquire the default cap on the next
    pass and truncate mid-cluster.
    """
    if max_passes is not None:
        tail_parts = ["continue"] + plan_args + [f"--max-passes {max_passes}"]
    elif unbounded:
        tail_parts = ["continue"] + plan_args + ["--unbounded"]
    else:
        tail_parts = ["continue"] + list(plan_args)
    tail = " ".join(tail_parts)
    if host == "claude":
        return f"/spec:autoplan {tail}".strip()
    return f"$spec autoplan {tail}".strip()


# --- F14 model identity: lock resolution --------------------------------------
#
# The mirror of the autorun wiring (same spawn discipline, same lock
# semantics): a chain without a ``model`` key locks on its first F14 spawn
# (the incoming identity, or null when no flags were given); afterwards the
# identity is immutable — any component mismatch refuses the spawn through
# the existing ChainSpawnError rejection path (exit 1 + spawn_refusal event
# + stderr carrying both values), and refusals write no state. This chain
# has no --command bypass, so every spawn resolves a worker host and the
# resolved-host form applies unconditionally.


def _format_model_identity(identity: Optional[Dict[str, str]]) -> str:
    """Compact identity render for refusal texts: ``null`` or sorted JSON."""
    if not identity:
        return "null"
    return json.dumps(identity, sort_keys=True)


def _render_identity_component(value: object) -> str:
    """One identity component for a refusal text; ``absent`` when missing."""
    return "absent" if value is None else repr(value)


def _identity_lock_guidance() -> str:
    """The shared refusal tail: how to legitimately change identity."""
    return (
        "the chain model is immutable; finish this chain and start a new one "
        "(or have the maintainer clear the chain state) to change identity"
    )


def _resolve_model_identity(args: argparse.Namespace, state: Dict[str, object]) -> Optional[Dict[str, str]]:
    """F14: resolve ``--model``/``--reasoning`` against the pass-chain lock.

    Returns the identity this pass carries (``None`` = worker default): the
    incoming identity when flags are given, else the locked value. A chain
    without a ``model`` key (pre-F14 or fresh) locks on this spawn. Any
    component mismatch against an existing lock refuses the spawn — the id,
    the reasoning, or null-then-value. The resolved-host form is judged at
    the spawn site, right after the worker host resolves.
    """
    incoming: Optional[Dict[str, str]] = None
    if args.model is not None or args.reasoning is not None:
        if args.model is None:
            raise AutoplanSpawnError("--reasoning requires --model: the model id is the identity's primary component")
        if not args.model.strip():
            raise AutoplanSpawnError(
                "--model must be a non-empty model id (whitespace-only given) — refusing the spawn"
            )
        if args.reasoning is not None and not args.reasoning.strip():
            raise AutoplanSpawnError(
                "--reasoning must be a non-empty level when given (whitespace-only given) — refusing the spawn"
            )
        incoming = {"id": args.model}
        if args.reasoning is not None:
            incoming["reasoning"] = args.reasoning

    if "model" not in state:
        # No lock yet (pre-F14 chains and fresh chains): this spawn locks —
        # the incoming identity, or null when no flags were given.
        return incoming

    locked = state.get("model")
    if locked is not None and not isinstance(locked, dict):
        raise AutoplanSpawnError(
            "pass-chain state has a malformed model identity lock: {!r} "
            "(expected {{id, reasoning?}} or null) — refusing the spawn".format(locked)
        )
    if incoming is not None:
        if locked is None:
            raise AutoplanSpawnError(
                "model identity conflict: the chain locked model: null but this spawn carries "
                f"{_format_model_identity(incoming)} — {_identity_lock_guidance()}"
            )
        if incoming.get("id") != locked.get("id"):
            raise AutoplanSpawnError(
                "model identity conflict: locked id {} vs incoming id {} — {}".format(
                    _render_identity_component(locked.get("id")),
                    _render_identity_component(incoming.get("id")),
                    _identity_lock_guidance(),
                )
            )
        if incoming.get("reasoning") != locked.get("reasoning"):
            raise AutoplanSpawnError(
                "model identity conflict: locked reasoning {} vs incoming reasoning {} — {}".format(
                    _render_identity_component(locked.get("reasoning")),
                    _render_identity_component(incoming.get("reasoning")),
                    _identity_lock_guidance(),
                )
            )
    return locked if isinstance(locked, dict) else None


def _verify_worker_start(
    worker_name: str,
    new_window_id: Optional[int],
    new_tty: Optional[str],
    *,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, object]:
    """F17 worker-start gate via the shared implementation, with this
    module's seams: tests patch ``autoplan_spawn.worker_running_on_tty`` and
    ``autoplan_spawn.WORKER_START_TIMEOUT_SECONDS``; the wrapper binds both
    module globals at call time."""
    if timeout_seconds is None:
        timeout_seconds = WORKER_START_TIMEOUT_SECONDS
    return chain_support.verify_worker_start(
        worker_name,
        new_window_id,
        new_tty,
        timeout_seconds=timeout_seconds,
        worker_check=worker_running_on_tty,
    )


def _recover_front_worker_window(worker_name: str) -> Tuple[Optional[int], Optional[str]]:
    """Front-window recovery via the shared implementation, with this
    module's seam: tests patch ``autoplan_spawn.worker_running_on_tty`` —
    only a front window verifiably running OUR worker is accepted as the
    spawn target."""
    return chain_support.recover_front_worker_window(worker_name, worker_check=worker_running_on_tty)


def _close_refused_window(
    new_window_id: Optional[int],
    new_tty: Optional[str],
    worker_name: str,
    *,
    chain: str,
    events_path: Path,
    pass_index: Optional[int] = None,
) -> Dict[str, object]:
    """Refusal close via the shared implementation, with this module's
    seam: tests patch ``autoplan_spawn.worker_pids_on_tty``
    (交接未确认 = 不留进程)."""
    return chain_support.close_refused_window(
        new_window_id,
        new_tty,
        worker_name,
        chain=chain,
        events_path=events_path,
        pass_index=pass_index,
        pid_lookup=worker_pids_on_tty,
    )


def _render_status_model(model: object) -> str:
    """The F14 status line: the locked identity plus the latest injection."""
    if not isinstance(model, dict):
        return "model: none (worker default)"
    id_value = model.get("id")
    if not isinstance(id_value, str) or not id_value:
        return "model: none (worker default)"
    line = f"model: {id_value}"
    reasoning = model.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        line += f" [{reasoning}]"
    injection = model.get("injection")
    if isinstance(injection, str) and injection:
        line += f" (injection: {injection})"
    return line


def spawn_payload(root: Path, args: argparse.Namespace) -> Dict[str, object]:
    """Run the pass guards, open the pass window, and record chain state."""
    from chain_session import refuse_current_spawn

    refuse_current_spawn(root, "autoplan")
    backend = chain_support.resolve_terminal_backend(getattr(args, "terminal", "auto"))
    kind: str = args.next
    target = resolve_target(root, args.target, kind)

    # Validate --plan entries (existence + root bounds) and gather the
    # qualifying docs for the record; explicit paths qualify as given.
    plan_docs, _ = discover_plan_docs(root, args.plan)
    doc_paths = [str(doc.relative_to(root)) if under_root(root, doc) else str(doc) for doc in plan_docs]

    if kind == "framework":
        master = master_doc_path(root, args.plan)
        if not master.is_file():
            raise AutoplanSpawnError(
                "framework pass requires the seeded master document "
                f"{master} — run the interrogation phase first (it seeds or updates "
                "the master with confirmed facts / assumptions / open questions), "
                "or pass --plan naming the cluster's master document"
            )

    # F16 step 3: legacy runtime files (and summary volatile fields) migrate
    # to .git/spec-chain/ before any read or lock — the rename preserves the
    # inode, so even a racing pre-migration pass stays flock-excluded.
    chain_support.migrate_legacy_chain_state(root, "autoplan")
    chain_dir = _chain_dir(root)
    runtime_dir = _runtime_dir(root)
    with single_chain_lock(
        runtime_dir,
        "autoplan",
        error=AutoplanSpawnError,
        busy_message=(f"another autoplan pass chain holds {CHAIN_LOCK_FILE}; parallel chains are refused"),
    ):
        state = _read_chain_state(root) or {}
        if getattr(args, "terminal", "auto") == "auto" and state.get("terminal_backend") == "tmux":
            backend = "tmux"
        # F1 (AUD-01) feature-set fingerprint: freeze the planning features
        # on the first spawn and refuse any later pass whose plan grew or was
        # edited behind the chain's back. Checking a feature off is legitimate
        # progress and leaves the fingerprint unchanged (the hash covers
        # feature texts, not checkbox states); a chain without the field
        # (pre-F1) freezes on this pass.
        fingerprint = feature_fingerprint(plan_docs, root)
        frozen = state.get("feature_fingerprint")
        if isinstance(frozen, str) and frozen != fingerprint:
            raise AutoplanSpawnError(
                "planning features changed since this pass chain started "
                f"(frozen {frozen[:12]}, current {fingerprint[:12]}): a feature was added, "
                "removed, or reworded mid-chain — finish this chain and start a new one, or "
                "restore the planning documents; refusing the spawn"
            )
        # 2026-10-19 user decision: autoplan passes are uncapped without a
        # flag — --max-passes > --unbounded > no cap. The strict int gate and
        # the next==cap boundary stay in the shared guard helpers (F12); the
        # effective cap is forwarded to the next pass's prompt so the
        # decision is self-carrying.
        max_passes = resolve_effective_cap(
            args.max_passes,
            getattr(args, "unbounded", False),
            None,
            "max-passes",
            error=AutoplanSpawnError,
        )
        if max_passes is not None:
            validate_cap(max_passes, "max-passes")
        next_pass = next_index_within_cap(
            state.get("pass"),
            max_passes,
            label="pass",
            option="max-passes",
            non_integer_template="pass-chain state has a non-integer pass index: {!r}",
            error=AutoplanSpawnError,
        )

        # F14: flags -> lock -> injection. The effective identity doubles as
        # the post-spawn lock value: on every accepted spawn it equals the
        # existing lock (matched component-wise) or establishes it. No bypass
        # exists on this chain, so the resolved-host form applies to every
        # identity-carrying spawn.
        effective_identity = _resolve_model_identity(args, state)

        # F1 (AUD-01): the permission decision is per-spawn (no lock); the
        # requested mode (default inherit) and the probe-resolved effective
        # mode both record for audit.
        permission_mode = getattr(args, "permission_mode", "inherit") or "inherit"
        permission_effective = resolve_permission_mode(permission_mode)

        host, host_source = resolve_worker_host(args.host)
        # F14: a non-null identity is namespace-bound to its recorded host
        # (a pi "provider/model" id means nothing to codex), so a resolved
        # host change refuses the spawn.
        state_host = state.get("host")
        if effective_identity and isinstance(state_host, str) and state_host != host:
            raise AutoplanSpawnError(
                f"model identity conflict: resolved host {host!r} differs from the chain host "
                f"{state_host!r} while the model identity "
                f"{_format_model_identity(effective_identity)} is non-null — model ids are "
                "host-namespace-specific; refusing the spawn"
            )
        plan_args = plan_args_for_prompt(args.plan)
        prompt = build_pass_prompt(host, plan_args, max_passes, unbounded=bool(getattr(args, "unbounded", False)))
        worker_command = build_worker_command(
            host, root, prompt, effective_identity, permission_mode=permission_effective
        )
        worker_name = worker_command[0]
        injection_level = model_injection_level(effective_identity, host)
        # F17 geometry: the new window inherits this session's window place
        parent_tty = session_tty() if backend == "terminal" else None
        osascript_argv, shell_command = (
            build_terminal_command(root, worker_command) if backend == "terminal" else ([], "")
        )

        record = {
            "pass": next_pass,
            "kind": kind,
            "target": target,
            "host": host,
            "host_source": host_source,
            "plan_docs": doc_paths,
            "max_passes": max_passes,
            "feature_fingerprint": fingerprint,
            "permission_mode": permission_mode,
            "permission_effective": permission_effective,
            "model": effective_identity,
            "model_injection": injection_level,
            "spawned_at": utc_now(),
            "shell_command": shell_command,
            "prev_tty": parent_tty,
        }

        if backend == "tmux":
            return chain_support.spawn_tmux_payload(root, "autoplan", worker_command, record, dry_run=args.dry_run)

        if args.dry_run:
            return {
                "dry_run": True,
                "osascript": osascript_argv,
                "window_recycle": {"status": "planned"},
                **record,
            }

        # F17: one bounded retry on a transiently busy Terminal, then the
        # existing refusal path (message texts preserved verbatim).
        completed = run_terminal_open(osascript_argv, error=AutoplanSpawnError, label="the pass Terminal window")
        new_window_id, new_tty = parse_spawn_result(completed.stdout)
        if new_window_id is None or new_tty is None:
            # 2026-10-09: an unconfirmable handoff must not leave the worker
            # either — recover the just-opened window by identity interlock
            # (the front window counts only when it verifiably runs OUR
            # worker) and close it synchronously before refusing.
            recovered_id, recovered_tty = _recover_front_worker_window(worker_name)
            _close_refused_window(
                recovered_id,
                recovered_tty,
                worker_name,
                chain="autoplan",
                events_path=runtime_dir / CHAIN_EVENTS_FILE,
                pass_index=next_pass,
            )
            # An unconfirmable handoff must not strand the chain: the pass is
            # not recorded, so a re-run resumes exactly here.
            raise AutoplanSpawnError(
                "osascript reply missing the window id or tty: {!r} — cannot confirm the handoff; "
                "refusing to record the pass".format(completed.stdout.strip())
            )
        try:
            if parent_tty is not None:
                # F17 geometry: stack the new window where this session's window
                # sits (no parent window / failed read simply keeps the default)
                parent_geometry = read_window_geometry(parent_tty)
                if parent_geometry is not None:
                    apply_window_geometry(new_window_id, parent_geometry)
            # F17 spawn gate: a window whose worker died instantly is closed
            # quietly and refused with zero state written — instead of silently
            # recording a pass that never runs. A busy-but-unobserved tab is a
            # late start: proceed.
            start_verdict = _verify_worker_start(worker_name, new_window_id, new_tty)
            if start_verdict["status"] == "dead-tab":
                close_spawned_window_quietly(new_window_id)
                raise AutoplanSpawnError(
                    "next-pass {} — the spawned Terminal window was closed; refusing to record the pass".format(
                        start_verdict["reason"]
                    )
                )
            record["window_recycle"] = chain_support.recycle_previous_window(
                prev_tty=record["prev_tty"],
                new_window_id=new_window_id,
                new_tty=new_tty,
                worker_name=worker_name,
                chain="autoplan",
                pass_index=next_pass,
                events_path=runtime_dir / CHAIN_EVENTS_FILE,
            )

            state = dict(record)
            # F14: the per-spawn injection level is an audit fact (spawns row
            # only); the chain state carries the identity lock, which the
            # effective identity already equals on every accepted spawn.
            state.pop("model_injection")
            state["updated_at"] = state.pop("spawned_at")
            # F16 step 3: split write — volatile.json in the runtime tree,
            # the volatile-free summary in the tracked tree.
            chain_support.write_chain_state(chain_dir, runtime_dir, state)
        except BaseException:
            # 交接未确认 = 不留进程 (2026-10-09): any failure between the
            # confirmed window open and the recorded state leaves an alive
            # worker the chain does not know about — a duplicate executor on
            # re-run. Close the just-opened window before the failure
            # propagates (idempotent: an already-closed window reports
            # window-gone). append_audit stays outside the guard: once the
            # state is written the handoff is confirmed and the window is
            # the chain's legitimate worker.
            _close_refused_window(
                new_window_id,
                new_tty,
                worker_name,
                chain="autoplan",
                events_path=runtime_dir / CHAIN_EVENTS_FILE,
                pass_index=next_pass,
            )
            raise
        append_audit(runtime_dir / CHAIN_SPAWNS_FILE, record)
        return {"dry_run": False, "terminal": completed.stdout.strip(), **record}


def status_payload(root: Path) -> Dict[str, object]:
    from chain_session import current_status_view

    current = current_status_view(root, "autoplan")
    if current is not None:
        return current
    # F16 step 3: migrate before reading so both layouts render identically.
    chain_support.migrate_legacy_chain_state(root, "autoplan")
    chain_dir = _chain_dir(root)
    runtime_dir = _runtime_dir(root)
    state, read_status = read_state(chain_dir, runtime_dir, chain="autoplan")
    payload: Dict[str, object] = {
        "project": str(root),
        "chain_state_file": str(chain_dir / CHAIN_STATE_FILE),
        "chain_runtime_dir": str(runtime_dir),
        "state_status": read_status,
    }
    if state is None:
        payload["started"] = False
    else:
        payload["started"] = True
        payload.update(state)
    # F16 step 3: the spawn-moment facts (tty, recycle plan) render from the
    # runtime volatile file, with the same defaults the summary read applied
    # when it was a summary field. Read-only overlay — never written back.
    volatile = chain_support.read_volatile_state(runtime_dir)
    payload["prev_tty"] = volatile.get("prev_tty")
    payload["window_recycle"] = volatile.get("window_recycle", {})
    # F14: the derived status model — the identity lock plus the latest
    # injection level from the audit tail when one exists. The injection
    # key is conditional, so a lock without spawns rows (old chains, the
    # F13 unknown-key passthrough sample) keeps its exact shape.
    audit_tail = read_audit_tail(runtime_dir)
    model_lock = state.get("model") if state is not None else None
    if isinstance(model_lock, dict) and model_lock:
        derived_model: Dict[str, object] = dict(model_lock)
        latest_injection = audit_tail.get("model_injection") if isinstance(audit_tail, dict) else None
        if isinstance(latest_injection, str) and latest_injection:
            derived_model["injection"] = latest_injection
        payload["model"] = derived_model
    else:
        payload["model"] = None
    payload["lock"] = chain_support.lock_holder(runtime_dir)
    total_refusals, last_refusal = read_last_refusal(runtime_dir / CHAIN_EVENTS_FILE)
    payload["refusals_recorded"] = total_refusals
    payload["last_refusal"] = last_refusal
    detail_docs = _detail_docs(root, state)
    for entry in detail_docs:
        # F4: the five-section completeness heuristic feeds the decision
        # table; a missing document is never complete.
        entry["complete"] = bool(entry.get("exists")) and detail_doc_complete(root, str(entry["path"]))
    payload["detail_docs"] = detail_docs
    # Tolerant plan counts for the decision table: the recorded cluster
    # documents when the state has them, else the canonical scan; a missing
    # or empty cluster means (0, 0) — nothing to consume, no NoPlanError.
    recorded: List[str] = []
    if state is not None and isinstance(state.get("plan_docs"), list):
        recorded = [item for item in state["plan_docs"] if isinstance(item, str)]  # type: ignore[union-attr]
    plan_checked = 0
    plan_unchecked = 0
    scope_error = None
    try:
        plan = (
            chain_lifecycle.recorded_plan(root, state, require_fingerprint=False)
            if state is not None and "plan_docs" in state
            else plan_payload(root, None)
        )
        totals = plan["totals"]
        plan_checked = int(totals["checked"])
        plan_unchecked = int(totals["unchecked"])
    except NoPlanError:
        pass
    except ChainSpawnError as exc:
        scope_error = str(exc)
    master_rel = recorded[0] if recorded else DEFAULT_MASTER_DOC
    master_links = _master_link_docs(root, root / master_rel)
    decision = decide(
        "autoplan",
        state,
        read_status,
        plan_checked=plan_checked,
        plan_unchecked=plan_unchecked,
        audit_tail=audit_tail,
        master_links=master_links,
        detail_docs=detail_docs,
        master_doc=master_rel,
    )
    if decision.action == "state_diverged":
        # The divergence is audited once until the state changes; decide stays
        # pure, so the audit append lives here in the wiring.
        diverged_target = decision.detail.get("target")
        if not recovery_event_recorded(runtime_dir / CHAIN_EVENTS_FILE, "state_diverged", diverged_target):
            append_recovery_event(
                runtime_dir,
                "autoplan",
                "state_diverged",
                {"target": diverged_target, "master_links": master_links},
            )
    payload["resume"] = {"action": decision.action, "reason": decision.reason, "detail": decision.detail}
    if scope_error:
        payload["resume"] = {"action": "state_diverged", "reason": scope_error, "detail": {}}
    return payload


def _render_spawn(payload: Dict[str, object]) -> str:
    lines = [
        "pass: {}".format(format_chain_counter(payload["pass"], payload.get("max_passes"))),
        "kind: {}".format(payload["kind"]),
        "host: {}".format(payload["host"]),
    ]
    permission_mode = payload.get("permission_mode")
    if isinstance(permission_mode, str):
        effective = payload.get("permission_effective")
        suffix = " -> {}".format(effective) if isinstance(effective, str) and effective != permission_mode else ""
        lines.append("permission: {}{}".format(permission_mode, suffix))
    if payload.get("target"):
        lines.append("target: {}".format(payload["target"]))
    lines.append("shell_command: {}".format(payload["shell_command"]))
    if payload.get("attach_command"):
        lines.append("attach: {}".format(payload["attach_command"]))
    recycle = payload.get("window_recycle")
    if isinstance(recycle, dict):
        if recycle.get("status") == "scheduled":
            lines.append("close: window {} in {}s".format(recycle["window_id"], recycle["delay_seconds"]))
        elif recycle.get("status") == "planned":
            lines.append("close: planned (previous window closes after the pass worker is confirmed running)")
        else:
            lines.append("close: skipped ({})".format(recycle.get("reason", "unknown")))
    if payload.get("dry_run"):
        lines.append(
            "dry-run: {} not executed".format("tmux" if payload.get("terminal_backend") == "tmux" else "osascript")
        )
    else:
        lines.append("terminal: {}".format(payload.get("terminal", "")))
    return "\n".join(lines)


def _render_detail_docs(detail_docs: object) -> str:
    items = [doc for doc in detail_docs if isinstance(doc, dict)] if isinstance(detail_docs, list) else []
    rendered = ", ".join(
        "{} ({})".format(doc.get("path"), "exists" if doc.get("exists") else "missing") for doc in items
    )
    return "detail docs: {}".format(rendered) if rendered else "detail docs: none"


def _render_status(payload: Dict[str, object]) -> str:
    if payload.get("execution_mode") == "current":
        from chain_session import render_status as render_current_status

        return render_current_status(payload)
    lines = [f"project: {payload['project']}", render_lock(payload.get("lock"))]
    if not payload.get("started"):
        if payload.get("state_status") == "corrupt":
            lines.append("pass chain: state unreadable (corrupt; see resume)")
        else:
            lines.append(render_not_started("pass chain: not started", payload))
    else:
        lines.append("pass: {}".format(payload.get("pass")))
        lines.append("kind: {}".format(payload.get("kind")))
        if payload.get("target"):
            lines.append("target: {}".format(payload.get("target")))
        lines.append("host: {}".format(payload.get("host")))
        lines.append(_render_status_model(payload.get("model")))
        permission_mode = payload.get("permission_mode")
        if isinstance(permission_mode, str):
            effective = payload.get("permission_effective")
            suffix = " -> {}".format(effective) if isinstance(effective, str) and effective != permission_mode else ""
            lines.append("permission: {}{}".format(permission_mode, suffix))
        lines.append("updated_at: {}".format(payload.get("updated_at")))
        if payload.get("attach_command"):
            lines.append("attach: {}".format(payload["attach_command"]))
        if payload.get("recovered_from"):
            lines.append("state: recovered from {}".format(payload["recovered_from"]))
    lines.append(render_last_refusal(payload.get("last_refusal")))
    lines.append(render_resume(payload.get("resume")))
    lines.append(_render_detail_docs(payload.get("detail_docs")))
    return "\n".join(lines)


def _emit(payload: Dict[str, object], render, use_json: bool) -> None:
    if use_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(payload))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoplan_spawn.py",
        description="Serial pass-chain spawn for /spec:autoplan (same spawn discipline as /spec:autorun).",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    chain_lifecycle.add_close_parser(subparsers)

    spawn_parser = subparsers.add_parser("spawn", help="open the next planning-pass interactive session after guards")
    spawn_parser.add_argument("--root", default=".", help="project root (default: current directory)")
    spawn_parser.add_argument(
        "--next",
        dest="next",
        choices=PASS_KINDS,
        required=True,
        help="pass kind to hand the next session (framework / detail / review)",
    )
    spawn_parser.add_argument(
        "--target",
        help="assigned document for a detail pass (may be a new file; must stay under the project root)",
    )
    spawn_parser.add_argument(
        "--plan",
        help="comma-separated planning document paths (default: scan the canonical .spec/plans/ root)",
    )
    spawn_parser.add_argument(
        "--host",
        choices=("codex", "pi", "claude"),
        help=(
            "worker host (default: SPEC_AUTORUN_HOST, else the detected current-session "
            "host, else PATH order codex/pi/claude)"
        ),
    )
    spawn_parser.add_argument(
        "--model",
        help=(
            "model identity for the chain: the full model id (multi-provider hosts embed the "
            "provider in the id); the source is only this flag and the chain state — no env "
            "fallback"
        ),
    )
    spawn_parser.add_argument(
        "--reasoning",
        help=(
            "reasoning/thinking level of the model identity, passed through to the worker CLI "
            "as-is (requires --model; no cross-host vocabulary normalization)"
        ),
    )
    spawn_parser.add_argument(
        "--max-passes",
        dest="max_passes",
        type=int,
        default=None,
        help=(
            "explicit pass cap for this chain (the caller's own limit; no flag means no cap); "
            "an explicit value is forwarded to the next pass's prompt"
        ),
    )
    spawn_parser.add_argument(
        "--unbounded",
        dest="unbounded",
        action="store_true",
        help=(
            "explicitly remove the pass cap (now also the no-flag default; kept as a "
            "compatibility entry and for explicit forwarding; mutually exclusive with --max-passes)"
        ),
    )
    spawn_parser.add_argument(
        "--permission-mode",
        dest="permission_mode",
        choices=PERMISSION_MODES,
        default="inherit",
        help=(
            "chain permission mode (default: inherit — the worker inherits the parent "
            "session's escalation, probed via the host-marker channel; undetectable means "
            "no escalation. ask renders no escalation flag; bypass renders the host's "
            "escalation flag and is recorded in the chain state)"
        ),
    )
    spawn_parser.add_argument(
        "--terminal",
        choices=("auto", "terminal", "tmux"),
        default="auto",
        help="session backend: macOS Terminal.app or Linux/WSL2 tmux (default: auto)",
    )
    spawn_parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="print the selected backend command without executing or recording",
    )
    spawn_parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="output format (default: text)"
    )

    status_parser = subparsers.add_parser("status", help="render the current pass-chain state")
    status_parser.add_argument("--root", default=".", help="project root (default: current directory)")
    status_parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="output format (default: text)"
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root: Optional[Path] = None
    try:
        root = _resolve_root(args.root)
        if args.subcommand == "close":
            _emit(
                chain_lifecycle.close_chain(
                    root,
                    "autoplan",
                    dry_run=args.dry_run,
                    legacy_scope_commit=args.legacy_scope_commit,
                    legacy_completion_commit=args.legacy_completion_commit,
                ),
                chain_lifecycle.render_close,
                args.format == "json",
            )
        elif args.subcommand == "spawn":
            if args.max_passes is not None:
                # an explicit cap is validated here, before any lock/state
                # touch; the default cap and the --unbounded interaction
                # resolve inside the payload (refusal event recorded)
                validate_cap(args.max_passes, "max-passes")
            _emit(spawn_payload(root, args), _render_spawn, args.format == "json")
        else:
            _emit(status_payload(root), _render_status, args.format == "json")
    except ChainSpawnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.subcommand == "spawn" and root is not None:
            append_refusal_event(_runtime_dir(root), "autoplan", exc)
        return EXIT_FAILURE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

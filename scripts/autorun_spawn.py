#!/usr/bin/env python3
# scripts/autorun_spawn.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Autorun chain facts and next-round spawn for ``/spec:autorun``.

Subcommands:

  - ``plan``    discover the project planning documents and count feature
                checkboxes (the machine truth for chain termination);
  - ``spawn``   open a new Terminal.app window in the project path running
                the worker CLI in a full interactive session with the
                autorun prompt injected; once the next-round worker is
                confirmed running on the new window's tty, schedule the
                close of the previous round's Terminal window (window
                recycling, fail-open),
                after enforcing the round cap, the single-chain lock, and
                the next-round package presence;
  - ``status``  render the current chain state: chain fields, the lock probe
                (free / held by holder / unknown / stale), the most recent
                spawn refusal from the events tail, and the derived keys
                (the F4 resume decision from ``chain_recovery.decide``, plus
                active package progress).

Exit codes are part of the contract:

  - ``0`` success;
  - ``1`` operational refusal or failure (lock held, round cap reached,
         no worker host available, no next-round package, osascript
         failure, unreadable chain state);
  - ``2`` usage error (argparse);
  - ``3`` no qualifying planning document (fail closed: the user must
         write project-level planning first).

Contract output goes to stdout (text, or JSON with ``--format json``);
diagnostics go to stderr. ``spawn`` writes only under ``.spec/autorun/``:
``chain.json`` (latest chain state), ``spawns.jsonl`` (append-only audit),
``events.jsonl`` (append-only events: ``spawn_refusal`` from this script's
refusal exit and ``recycle_close`` from the detached close helper), and the
transient ``chain.lock`` (the flock mutex plus one advisory holder line per
acquisition).

The chain-state read/write, lock, audit, and Terminal recycle mechanics live
in ``scripts/chain_spawn_support.py`` (the F5 shared module, imported here by
name — one implementation, two authorized Terminal-automation callers: this
script's autorun round spawn and the autoplan pass spawn in
``scripts/autoplan_spawn.py``, both inheriting the recorded overturn of the
2026-09-03 no-AppleScript policy in the ``2026-10-06_add-autorun-command``
Development Record; no other caller may reuse it).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import chain_spawn_support as chain_support  # noqa: E402  # type: ignore

# F4 recovery semantics: the 13-row resume decision table (the tolerant state
# read lives in the shared module; chain_recovery imports nothing from here).
from chain_recovery import decide, read_audit_tail  # noqa: E402  # type: ignore
from chain_spawn_support import (  # noqa: E402  # type: ignore
    CHAIN_EVENTS_FILE,
    CHAIN_LOCK_FILE,
    CHAIN_SPAWNS_FILE,
    CHAIN_STATE_FILE,
    DEFAULT_MAX_ROUNDS,
    OSASCRIPT_TIMEOUT_SECONDS,  # noqa: F401  # re-export: autoplan tests import it from here
    WORKER_START_TIMEOUT_SECONDS,
    ChainSpawnError,
    adaptive_max_rounds,
    append_audit,
    append_refusal_event,
    apply_window_geometry,
    atomic_write,
    build_terminal_command,
    build_window_lookup_applescript,  # noqa: F401  # re-export: chain tests use the module attr
    build_worker_command,  # F14: shared render surface; tests + autoplan still import it from here
    close_spawned_window_quietly,
    controlling_tty,
    effective_max_cap,
    model_injection_level,
    next_index_within_cap,
    parse_spawn_result,
    parse_window_lookup,  # noqa: F401  # re-export: chain tests import it from here
    read_last_refusal,
    read_state,
    read_window_geometry,  # noqa: F401  # re-export: chain tests import it from here
    resolve_root,
    run_terminal_open,
    schedule_window_close,
    under_root,
    utc_now,
    validate_cap,
    worker_pids_on_tty,
    worker_running_on_tty,
)
from chain_spawn_support import pid_alive as _pid_alive  # seam binding: tests patch this name

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NO_PLAN = 3

WORKER_HOSTS: Tuple[str, ...] = ("codex", "pi", "claude")
# Env markers each host CLI sets in its tool subprocesses. Markers inherit
# down the shell chain (a codex session launched from a pi shell carries both
# PI_CODING_AGENT and CODEX_SANDBOX), so multiple markers are disambiguated
# by the nearest-ancestor walk in detect_session_host.
HOST_ENV_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("codex", "CODEX_SANDBOX"),
    ("pi", "PI_CODING_AGENT"),
    ("claude", "CLAUDECODE"),
)
CHAIN_DIR_NAME = ".spec/autorun"

FEATURE_CHECKBOX = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s*(\S.*)$")
# One canonical planning root: every planning document lives under ``plans/``
# (master ``plans/README.md``, phase details ``plans/NN-<slug>.md``, plans
# aligned with a Development Record ``plans/<YYYY-MM-DD>_<verb>-<object>.md``).
# ``--plan`` stays the explicit escape hatch for a document kept elsewhere.
PLAN_ROOT = "plans"
PLAN_CANDIDATE_GLOB = "plans/*.md"


class AutorunError(ChainSpawnError):
    """Operational refusal or failure with a user-facing message.

    Subclass of the shared ``ChainSpawnError`` base (both chain scripts
    re-export that one base) so a single catch handles both chains while
    this script's audit ``type`` stays ``AutorunError``.
    """


class NoPlanError(AutorunError):
    """No qualifying planning document was found (exit 3)."""


def _resolve_root(raw_root: str) -> Path:
    try:
        return resolve_root(raw_root)
    except ChainSpawnError as exc:
        raise AutorunError(str(exc)) from exc


def plan_candidates(root: Path) -> List[Path]:
    """Canonical ``plans/*.md`` scan (the planning root is a project fact)."""
    return sorted(root.glob(PLAN_CANDIDATE_GLOB))


def count_features(text: str) -> Dict[str, object]:
    """Count markdown feature checkboxes: ``- [ ]`` unchecked, ``- [x]`` checked."""
    checked = 0
    unchecked = 0
    unchecked_items: List[str] = []
    for line in text.splitlines():
        match = FEATURE_CHECKBOX.match(line)
        if not match:
            continue
        if match.group(1) == " ":
            unchecked += 1
            unchecked_items.append(match.group(2).strip())
        else:
            checked += 1
    return {
        "checked": checked,
        "unchecked": unchecked,
        "total": checked + unchecked,
        "unchecked_items": unchecked_items,
    }


def discover_plan_docs(root: Path, explicit: Optional[str]) -> Tuple[List[Path], List[str]]:
    """Return (qualifying docs, scanned candidates) for the planning-document scan.

    ``explicit`` is a comma-separated list of paths (relative to root or
    absolute); each must exist and stay under root. Without it, the canonical
    planning root ``plans/`` is scanned. A candidate qualifies only when it
    contains at least one feature checkbox.
    """
    scanned: List[str] = []
    qualifying: List[Path] = []

    if explicit:
        for raw in explicit.split(","):
            raw = raw.strip()
            if not raw:
                continue
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            candidate = candidate.resolve()
            scanned.append(str(candidate))
            if not under_root(root, candidate):
                raise AutorunError(f"--plan path escapes the project root: {raw}")
            if not candidate.is_file():
                raise AutorunError(f"--plan path does not exist: {raw}")
            qualifying.append(candidate)
    else:
        for candidate in plan_candidates(root):
            scanned.append(str(candidate))
            if candidate.is_file():
                qualifying.append(candidate)

    docs: List[Path] = []
    for candidate in qualifying:
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"skipping unreadable planning candidate: {candidate}: {exc}", file=sys.stderr)
            continue
        if count_features(text)["total"]:
            docs.append(candidate)
    return docs, scanned


def plan_payload(root: Path, explicit: Optional[str]) -> Dict[str, object]:
    docs, scanned = discover_plan_docs(root, explicit)
    doc_reports: List[Dict[str, object]] = []
    totals = {"checked": 0, "unchecked": 0, "total": 0}
    for doc in docs:
        counts = count_features(doc.read_text(encoding="utf-8"))
        doc_reports.append(
            {
                "path": str(doc.relative_to(root)) if under_root(root, doc) else str(doc),
                **counts,
            }
        )
        for key in totals:
            totals[key] += counts[key]  # type: ignore[operator]
    if not doc_reports:
        raise NoPlanError(
            f"no qualifying planning document found under {PLAN_ROOT}/; scanned: "
            + (", ".join(scanned) if scanned else "(none)")
            + f" — write project-level planning (feature checkboxes) in {PLAN_ROOT}/README.md "
            "or pass --plan <doc>"
        )
    return {
        "project": str(root),
        "docs": doc_reports,
        "totals": totals,
        "complete": totals["unchecked"] == 0,
    }


def _chain_dir(root: Path) -> Path:
    return root / CHAIN_DIR_NAME


def _read_chain_state(root: Path) -> Optional[Dict[str, object]]:
    return chain_support.read_state_file(
        _chain_dir(root) / CHAIN_STATE_FILE,
        label="chain state",
        error=AutorunError,
    )


def _active_package_present(root: Path) -> bool:
    specs_dir = root / ".spec" / "specs"
    if not specs_dir.is_dir():
        return False
    for entry in sorted(specs_dir.iterdir()):
        if entry.name == "archive" or not entry.is_dir():
            continue
        if (entry / "spec.md").is_file():
            return True
    return False


def _active_package_facts(root: Path) -> Tuple[Optional[Dict[str, object]], List[Dict[str, object]]]:
    """Active (unarchived) packages with task progress from ``tasks.md``.

    Returns ``(active_package, active_packages)``: the single entry when
    exactly one package is active (else ``None``), plus the parallel list the
    multi-package case is rendered from (F4's disambiguation consumes it).
    """
    specs_dir = root / ".spec" / "specs"
    entries: List[Dict[str, object]] = []
    if specs_dir.is_dir():
        for entry in sorted(specs_dir.iterdir()):
            if entry.name == "archive" or not entry.is_dir():
                continue
            if not (entry / "spec.md").is_file():
                continue
            checked = 0
            total = 0
            tasks_file = entry / "tasks.md"
            if tasks_file.is_file():
                try:
                    counts = count_features(tasks_file.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    counts = None
                if counts:
                    checked = int(counts["checked"])
                    total = int(counts["total"])
            entries.append({"slug": entry.name, "checked": checked, "total": total})
    return (entries[0] if len(entries) == 1 else None), entries


def _nearest_ancestor_host(candidates: Tuple[str, ...]) -> Optional[str]:
    """Walk the parent-process chain and return the nearest ancestor running
    one of ``candidates`` (the innermost running CLI comes first).

    The walk stops at the first hit — nearest wins, so there is nothing to
    gain from climbing further — and returns ``None`` when no ancestor
    matches or the chain cannot be read. Callers treat ``None`` as "no
    evidence" and never guess a host from it.
    """
    pid = os.getpid()
    for _ in range(64):
        try:
            probe = subprocess.run(
                ["ps", "-o", "ppid=,command=", "-p", str(pid)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=chain_support.PS_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            break
        line = probe.stdout.strip()
        if not line:
            break
        parts = line.split(None, 1)
        if len(parts) < 2:
            break
        # Scan this level's command before climbing further: the walk breaks
        # when the chain reaches launchd, and that outermost command still
        # counts as evidence.
        for token in parts[1].split():
            name = token.rstrip("/").rsplit("/", 1)[-1]
            if name in candidates:
                return name
        try:
            pid = int(parts[0])
        except ValueError:
            break
        if pid <= 1:
            break
    return None


def _marked_hosts(env: Optional[Dict[str, str]] = None) -> List[str]:
    """Hosts whose env marker is set, in ``HOST_ENV_MARKERS`` order."""
    env = os.environ if env is None else env
    return [host for host, marker in HOST_ENV_MARKERS if env.get(marker)]


def detect_session_host(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Detect the host CLI running the current session, if identifiable.

    A single env marker wins; multiple markers (shell inheritance) resolve
    to the nearest ancestor actually running one of the marked hosts.
    Returns ``None`` when nothing identifies a host — callers fall back to
    PATH order instead of guessing.
    """
    marked = _marked_hosts(env)
    if len(marked) == 1:
        return marked[0]
    if len(marked) > 1:
        return _nearest_ancestor_host(tuple(marked))
    return None


def resolve_worker_host(explicit: Optional[str]) -> Tuple[str, str]:
    """Resolve the worker host and its provenance.

    Order: ``--host`` > ``SPEC_AUTORUN_HOST`` > detected current-session host
    (same-host chain continuation) > PATH order. Same-host continuation is
    the robust default: a pi main session spawns a pi worker, not whatever
    happens to sit first on PATH.
    """
    host = explicit or os.environ.get("SPEC_AUTORUN_HOST") or ""
    if host:
        if host not in WORKER_HOSTS:
            raise AutorunError(f"unknown worker host: {host} (expected one of {WORKER_HOSTS})")
        return host, "flag" if explicit else "env"
    marked = _marked_hosts()
    detected = detect_session_host()
    if detected and shutil.which(detected):
        return detected, "session"
    for candidate in WORKER_HOSTS:
        if shutil.which(candidate):
            if marked:
                # Markers were present but did not resolve to a usable host,
                # so this spawn continues on a different CLI than the
                # session's. Recorded as host_source=path, but never silent.
                print(
                    "warning: session host markers {} did not resolve to an installed host; "
                    "falling back to PATH order ({})".format("/".join(marked), candidate),
                    file=sys.stderr,
                )
            return candidate, "path"
    raise AutorunError(f"no worker host available on PATH (tried {WORKER_HOSTS})")


def plan_args_for_prompt(plan: Optional[str]) -> List[str]:
    """Forward the ``--plan`` selection into the next round's prompt.

    The prompt is re-parsed as arguments by the next session, so the value is
    shell-quoted: without it a planning path containing a space arrives as
    ``--plan docs/plan`` plus a stray word and the next round silently loses
    the document.
    """
    return ["--plan " + shlex.quote(plan)] if plan else []


def build_prompt(host: str, plan_args: List[str], max_rounds: int) -> str:
    """Build the autorun prompt injected into the next-round session."""
    tail = " ".join(plan_args + [f"--max-rounds {max_rounds}"])
    if host == "claude":
        return f"/spec:autorun {tail}".strip()
    return f"$spec autorun {tail}".strip()


def build_close_argv(
    window_id: int,
    delay_seconds: int,
    close_wait_seconds: int,
    *,
    chain: str,
    round_index: Optional[int] = None,
    pass_index: Optional[int] = None,
    prev_tty: str,
    events_path: Path,
    session_pids: Optional[List[int]] = None,
    worker_name: str = "",
) -> List[str]:
    """Close-helper argv via the shared implementation, raising this
    script's own class on invalid arguments (the seam the chain tests use)."""
    return chain_support.build_close_argv(
        window_id,
        delay_seconds,
        close_wait_seconds,
        chain=chain,
        round_index=round_index,
        pass_index=pass_index,
        prev_tty=prev_tty,
        events_path=events_path,
        session_pids=session_pids,
        worker_name=worker_name,
        error=AutorunError,
    )


def session_tty() -> Optional[str]:
    """Session tty via the shared resolution, with this module's patch seam.

    The chain test files monkeypatch ``autorun_spawn.controlling_tty``; the
    wrapper binds that module global at call time, so the seam keeps working
    while the implementation lives once in the shared module.
    """
    return chain_support.session_tty(controlling=controlling_tty)


def probe_lock(chain_dir: Path) -> Dict[str, object]:
    """Lock probe via the shared implementation, with this module's seam.

    Same patch-seam discipline as ``session_tty``: tests replace
    ``autorun_spawn._pid_alive``, so the wrapper passes this module's binding.
    """
    return chain_support.lock_holder(chain_dir, pid_alive_check=_pid_alive)


def verify_worker_start(
    worker_name: str,
    new_window_id: Optional[int],
    new_tty: Optional[str],
    *,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, object]:
    """F17 worker-start gate via the shared implementation, with this module's
    seam: tests patch ``autorun_spawn.worker_running_on_tty`` and
    ``autorun_spawn.WORKER_START_TIMEOUT_SECONDS``; the wrapper binds both
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


def recycle_previous_window(
    prev_tty: Optional[str],
    new_window_id: Optional[int],
    new_tty: Optional[str],
    worker_name: str,
    *,
    chain: str,
    events_path: Path,
    round_index: Optional[int] = None,
    pass_index: Optional[int] = None,
    delay_seconds: Optional[int] = None,
    close_wait_seconds: Optional[int] = None,
) -> Dict[str, object]:
    """Window recycle via the shared implementation, with this module's seams.

    Tests patch ``autorun_spawn.worker_pids_on_tty`` and
    ``autorun_spawn.schedule_window_close``; the wrapper binds both module
    globals at call time and injects them into the shared implementation.
    """
    return chain_support.recycle_previous_window(
        prev_tty,
        new_window_id,
        new_tty,
        worker_name,
        chain=chain,
        events_path=events_path,
        round_index=round_index,
        pass_index=pass_index,
        delay_seconds=delay_seconds,
        close_wait_seconds=close_wait_seconds,
        pid_lookup=worker_pids_on_tty,
        schedule_close=schedule_window_close,
    )


# --- F14 model identity: lock resolution --------------------------------------
#
# The render surface (worker argv, host flag mapping, injection level) lives
# in chain_spawn_support; this is the autorun wiring of flags -> lock ->
# injection. The lock semantics: a chain without a ``model`` key locks on its
# first F14 spawn (the incoming identity, or null when no flags were given);
# afterwards the identity is immutable — any component mismatch refuses the
# spawn through the existing ChainSpawnError rejection path (exit 1 +
# spawn_refusal event + stderr carrying both the locked and the incoming
# value), and refusals write no state.


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
    """F14: resolve ``--model``/``--reasoning`` against the chain identity lock.

    Returns the identity this round carries (``None`` = worker default): the
    incoming identity when flags are given, else the locked value. A chain
    without a ``model`` key (pre-F14 or fresh) locks on this spawn. Any
    component mismatch against an existing lock refuses the spawn — the id,
    the reasoning, or null-then-value. The resolved-host form is judged at
    the spawn site, where the worker host is known (a bypass has none).
    """
    incoming: Optional[Dict[str, str]] = None
    if args.model is not None or args.reasoning is not None:
        if args.model is None:
            raise AutorunError("--reasoning requires --model: the model id is the identity's primary component")
        if not args.model.strip():
            raise AutorunError("--model must be a non-empty model id (whitespace-only given) — refusing the spawn")
        if args.reasoning is not None and not args.reasoning.strip():
            raise AutorunError(
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
        raise AutorunError(
            "chain state has a malformed model identity lock: {!r} "
            "(expected {{id, reasoning?}} or null) — refusing the spawn".format(locked)
        )
    if incoming is not None:
        if locked is None:
            raise AutorunError(
                "model identity conflict: the chain locked model: null but this spawn carries "
                f"{_format_model_identity(incoming)} — {_identity_lock_guidance()}"
            )
        if incoming.get("id") != locked.get("id"):
            raise AutorunError(
                "model identity conflict: locked id {} vs incoming id {} — {}".format(
                    _render_identity_component(locked.get("id")),
                    _render_identity_component(incoming.get("id")),
                    _identity_lock_guidance(),
                )
            )
        if incoming.get("reasoning") != locked.get("reasoning"):
            raise AutorunError(
                "model identity conflict: locked reasoning {} vs incoming reasoning {} — {}".format(
                    _render_identity_component(locked.get("reasoning")),
                    _render_identity_component(incoming.get("reasoning")),
                    _identity_lock_guidance(),
                )
            )
    return locked if isinstance(locked, dict) else None


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
    """Run the spawn guards, open the Terminal window, and record chain state."""
    plan = plan_payload(root, args.plan)
    if plan["complete"]:
        raise AutorunError("planning documents report every feature checked; the chain is complete, nothing to spawn")
    if not _active_package_present(root):
        raise AutorunError(
            "no active task package under .spec/specs/ — spawn expects the next-round "
            "package created by /spec:new first; refusing to spawn"
        )

    with chain_support.single_chain_lock(
        _chain_dir(root),
        "autorun",
        error=AutorunError,
        busy_message=(f"another autorun chain holds {CHAIN_LOCK_FILE} for this project; parallel chains are refused"),
    ):
        state = _read_chain_state(root) or {}
        # F17 adaptive cap: an explicit flag wins exactly; without one the cap
        # follows the workload — recorded cap floors the adaptive estimate
        # (features + rework headroom), so the chain never dies at the cap
        # with features remaining and a manual continuation never refuses on
        # the way down. The strict int gate and the next==cap boundary stay
        # in the shared guard helper (F12).
        unchecked = int(plan["totals"]["unchecked"])  # type: ignore[index]
        max_rounds = effective_max_cap(args.max_rounds, state.get("max_rounds"), adaptive_max_rounds(unchecked))
        validate_cap(max_rounds, "max-rounds")
        next_round = next_index_within_cap(
            state.get("round"),
            max_rounds,
            label="round",
            option="max-rounds",
            non_integer_template="chain state has a non-integer round: {!r}",
            error=AutorunError,
        )

        # F14: flags -> lock -> injection. The effective identity doubles as
        # the post-spawn lock value: on every accepted spawn it equals the
        # existing lock (matched component-wise) or establishes it.
        effective_identity = _resolve_model_identity(args, state)

        if args.command:
            host = "custom"
            host_source = "custom"
            worker_command: List[str] = []
            worker_name = ""
            # bypass: the caller owns the full command, so the identity is
            # recorded but never injected (the custom level says exactly that)
            injection_level = "custom"
        else:
            host, host_source = resolve_worker_host(args.host)
            # F14: a non-null identity is namespace-bound to its recorded host
            # (a pi "provider/model" id means nothing to codex), so a resolved
            # host change refuses the spawn; a bypass has no resolved host and
            # is exempt (its state host is "custom", which any later host
            # spawn with an identity then correctly refuses against).
            state_host = state.get("host")
            if effective_identity and isinstance(state_host, str) and state_host != host:
                raise AutorunError(
                    f"model identity conflict: resolved host {host!r} differs from the chain "
                    f"host {state_host!r} while the model identity "
                    f"{_format_model_identity(effective_identity)} is non-null — model ids are "
                    "host-namespace-specific; refusing the spawn"
                )
            plan_args = plan_args_for_prompt(args.plan)
            prompt = build_prompt(host, plan_args, max_rounds)
            worker_command = build_worker_command(host, root, prompt, effective_identity)
            worker_name = worker_command[0]
            injection_level = model_injection_level(effective_identity, host)
        # F17 geometry: the new window inherits this session's window place
        parent_tty = session_tty()
        osascript_argv, shell_command = build_terminal_command(root, worker_command, args.command)

        record = {
            "round": next_round,
            "host": host,
            "host_source": host_source,
            "plan_docs": [doc["path"] for doc in plan["docs"]],  # type: ignore[index]
            "max_rounds": max_rounds,
            "model": effective_identity,
            "model_injection": injection_level,
            "spawned_at": utc_now(),
            "shell_command": shell_command,
            "prev_tty": parent_tty,
        }

        if args.dry_run:
            return {
                "dry_run": True,
                "osascript": osascript_argv,
                "window_recycle": {"status": "planned"},
                **record,
            }

        # F17: one bounded retry on a transiently busy Terminal, then the
        # existing refusal path (message texts preserved verbatim).
        completed = run_terminal_open(osascript_argv, error=AutorunError, label="the Terminal window")
        new_window_id, new_tty = parse_spawn_result(completed.stdout)
        if new_window_id is None or new_tty is None:
            # An unconfirmable handoff must not strand the chain: the round
            # is not recorded, so a re-run resumes exactly here.
            raise AutorunError(
                "osascript reply missing the window id or tty: {!r} — cannot confirm the handoff; "
                "refusing to record the round".format(completed.stdout.strip())
            )
        if parent_tty is not None:
            # F17 geometry: stack the new window where this session's window
            # sits (no parent window / failed read simply keeps the default)
            parent_geometry = read_window_geometry(parent_tty)
            if parent_geometry is not None:
                apply_window_geometry(new_window_id, parent_geometry)
        # F17 spawn gate: a window whose worker died instantly (codex's
        # probabilistic startup failure) is closed quietly and refused with
        # zero state written — instead of silently recording a round that
        # never runs. A busy-but-unobserved tab is a late start: proceed.
        start_verdict = verify_worker_start(worker_name, new_window_id, new_tty)
        if start_verdict["status"] == "dead-tab":
            close_spawned_window_quietly(new_window_id)
            raise AutorunError(
                "next-round {} — the spawned Terminal window was closed; refusing to record the round".format(
                    start_verdict["reason"]
                )
            )
        record["window_recycle"] = recycle_previous_window(
            prev_tty=record["prev_tty"],
            new_window_id=new_window_id,
            new_tty=new_tty,
            worker_name=worker_name,
            chain="autorun",
            round_index=next_round,
            events_path=_chain_dir(root) / CHAIN_EVENTS_FILE,
        )

        state_file = _chain_dir(root) / CHAIN_STATE_FILE
        state = dict(record)
        # F14: the per-spawn injection level is an audit fact (spawns row
        # only); the chain state carries the identity lock, which the
        # effective identity already equals on every accepted spawn.
        state.pop("model_injection")
        state["updated_at"] = state.pop("spawned_at")
        atomic_write(state_file, json.dumps(state, indent=2, sort_keys=True) + "\n")
        append_audit(_chain_dir(root) / CHAIN_SPAWNS_FILE, record)
        return {"dry_run": False, "terminal": completed.stdout.strip(), **record}


def status_payload(root: Path) -> Dict[str, object]:
    chain_dir = _chain_dir(root)
    state, read_status = read_state(chain_dir, chain="autorun")
    payload: Dict[str, object] = {
        "project": str(root),
        "chain_state_file": str(chain_dir / CHAIN_STATE_FILE),
        "state_status": read_status,
    }
    if state is None:
        payload["started"] = False
    else:
        payload["started"] = True
        payload.update(state)
    # F14: the derived status model — the identity lock plus the latest
    # injection level from the audit tail when one exists. The injection
    # key is conditional, so a lock without spawns rows (old chains, the
    # F13 unknown-key passthrough sample) keeps its exact shape.
    audit_tail = read_audit_tail(chain_dir)
    model_lock = state.get("model") if state is not None else None
    if isinstance(model_lock, dict) and model_lock:
        derived_model: Dict[str, object] = dict(model_lock)
        latest_injection = audit_tail.get("model_injection") if isinstance(audit_tail, dict) else None
        if isinstance(latest_injection, str) and latest_injection:
            derived_model["injection"] = latest_injection
        payload["model"] = derived_model
    else:
        payload["model"] = None
    payload["lock"] = probe_lock(chain_dir)
    total_refusals, last_refusal = read_last_refusal(chain_dir / CHAIN_EVENTS_FILE)
    payload["refusals_recorded"] = total_refusals
    payload["last_refusal"] = last_refusal
    # Plan counts are tolerant for status (a read-only render): no qualifying
    # document means (0, 0) and the decision table says nothing to consume.
    plan_checked = 0
    plan_unchecked = 0
    try:
        plan = plan_payload(root, None)
        payload["plan"] = plan
        totals = plan["totals"]  # type: ignore[index]
        plan_checked = int(totals["checked"])  # type: ignore[index]
        plan_unchecked = int(totals["unchecked"])  # type: ignore[index]
    except NoPlanError as exc:
        payload["plan"] = {"error": str(exc)}
    active_package, active_packages = _active_package_facts(root)
    payload["active_package"] = active_package
    if len(active_packages) > 1:
        payload["active_packages"] = active_packages
    decision = decide(
        "autorun",
        state,
        read_status,
        plan_checked=plan_checked,
        plan_unchecked=plan_unchecked,
        audit_tail=audit_tail,
        active_packages=active_packages,
    )
    payload["resume"] = {"action": decision.action, "reason": decision.reason, "detail": decision.detail}
    return payload


def _render_plan(payload: Dict[str, object]) -> str:
    lines = [f"project: {payload['project']}"]
    totals = payload["totals"]
    for doc in payload["docs"]:  # type: ignore[union-attr]
        lines.append(
            "- {}: {}/{} checked".format(doc["path"], doc["checked"], doc["total"])  # type: ignore[index]
        )
    lines.append(
        "features: {}/{} checked ({} unchecked)".format(
            totals["checked"],
            totals["total"],
            totals["unchecked"],  # type: ignore[index]
        )
    )
    lines.append("complete: {}".format("yes" if payload["complete"] else "no"))
    return "\n".join(lines)


def _render_spawn(payload: Dict[str, object]) -> str:
    lines = ["round: {}/{}".format(payload["round"], payload["max_rounds"]), "host: {}".format(payload["host"])]
    lines.append("shell_command: {}".format(payload["shell_command"]))
    recycle = payload.get("window_recycle")
    if isinstance(recycle, dict):
        if recycle.get("status") == "scheduled":
            lines.append("close: window {} in {}s".format(recycle["window_id"], recycle["delay_seconds"]))
        elif recycle.get("status") == "planned":
            lines.append("close: planned (previous window closes after the next round is confirmed running)")
        else:
            lines.append("close: skipped ({})".format(recycle.get("reason", "unknown")))
    if payload.get("dry_run"):
        lines.append("dry-run: osascript not executed")
    else:
        lines.append("terminal: {}".format(payload.get("terminal", "")))
    return "\n".join(lines)


def render_lock(lock: object) -> str:
    """Render the lock probe: free / held by pid N (command) since at / unknown."""
    if not isinstance(lock, dict):
        return "lock: unknown (probe failed)"
    if lock.get("status") == "free":
        return "lock: free"
    if lock.get("status") == "held":
        if lock.get("holder") == "pid":
            return "lock: held by pid {} ({}) since {}".format(
                lock.get("pid"), lock.get("command", "unknown"), lock.get("since", "unknown")
            )
        if lock.get("holder") == "stale":
            return "lock: held (stale content)"
        return "lock: held (holder unknown)"
    return "lock: unknown (probe failed)"


def render_last_refusal(last_refusal: object) -> str:
    if not isinstance(last_refusal, dict) or not last_refusal.get("message"):
        return "last_refusal: none"
    return "last_refusal: {} (type={}, at={})".format(
        last_refusal.get("message"),
        last_refusal.get("type", "unknown"),
        last_refusal.get("at", "unknown"),
    )


def render_not_started(label: str, payload: Dict[str, object]) -> str:
    """``label`` plus the refusal count when failures were recorded before
    any chain state existed (the refusal-without-chain.json case)."""
    count = payload.get("refusals_recorded", 0)
    if not isinstance(count, int) or count <= 0:
        return label
    noun = "refusal" if count == 1 else "refusals"
    last = payload.get("last_refusal")
    message = ""
    if isinstance(last, dict) and last.get("message"):
        message = ": {}".format(last["message"])
    return "{} ({} {} recorded{})".format(label, count, noun, message)


def _render_active_package(payload: Dict[str, object]) -> str:
    packages = payload.get("active_packages")
    if isinstance(packages, list) and len(packages) > 1:
        return "active packages: " + ", ".join(
            "{} ({}/{} tasks)".format(item.get("slug"), item.get("checked"), item.get("total")) for item in packages
        )
    active = payload.get("active_package")
    if isinstance(active, dict):
        return "active package: {} ({}/{} tasks)".format(active.get("slug"), active.get("checked"), active.get("total"))
    return "active package: none"


def render_resume(resume: object) -> str:
    """One resume line from the F4 decision: ``resume: <action> (<reason>)``."""
    if not isinstance(resume, dict) or not resume.get("action"):
        return "resume: none"
    return "resume: {} ({})".format(resume.get("action"), resume.get("reason", ""))


def _render_status(payload: Dict[str, object]) -> str:
    lines = [f"project: {payload['project']}", render_lock(payload.get("lock"))]
    if not payload.get("started"):
        if payload.get("state_status") == "corrupt":
            lines.append("chain: state unreadable (corrupt; see resume)")
        else:
            lines.append(render_not_started("chain: not started", payload))
    else:
        lines.append("round: {}".format(payload.get("round")))
        lines.append("host: {}".format(payload.get("host")))
        lines.append(_render_status_model(payload.get("model")))
        lines.append("updated_at: {}".format(payload.get("updated_at")))
        if payload.get("recovered_from"):
            lines.append("state: recovered from {}".format(payload["recovered_from"]))
    lines.append(render_last_refusal(payload.get("last_refusal")))
    lines.append(render_resume(payload.get("resume")))
    lines.append(_render_active_package(payload))
    plan = payload.get("plan")
    if isinstance(plan, dict):
        if "error" in plan:
            lines.append("plan: {}".format(plan["error"]))
        else:
            lines.append(
                "plan: {}/{} checked, complete={}".format(
                    plan["totals"]["checked"],
                    plan["totals"]["total"],
                    plan["complete"],  # type: ignore[index]
                )
            )
    return "\n".join(lines)


def _emit(payload: Dict[str, object], render, use_json: bool) -> None:
    if use_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(payload))


def _add_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=".", help="project root (default: current directory)")
    parser.add_argument(
        "--plan",
        help="comma-separated planning document paths (default: scan the canonical plans/ root)",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: text)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autorun_spawn.py",
        description="Autorun chain facts and next-round spawn for /spec:autorun.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    plan_parser = subparsers.add_parser("plan", help="discover planning documents and count feature checkboxes")
    _add_plan_args(plan_parser)

    spawn_parser = subparsers.add_parser("spawn", help="open the next-round Terminal window after guards")
    _add_plan_args(spawn_parser)
    spawn_parser.add_argument(
        "--host",
        choices=WORKER_HOSTS,
        help="worker host (default: SPEC_AUTORUN_HOST, else detected session host, else PATH order codex/pi/claude)",
    )
    spawn_parser.add_argument(
        "--command",
        help="raw shell command override run in the window after cd <root> (host recorded as custom)",
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
        "--max-rounds",
        type=int,
        default=None,
        help=(
            "round cap (default: adaptive — at least {}, grows with the unchecked feature count "
            "to features + 25% + 3)".format(DEFAULT_MAX_ROUNDS)
        ),
    )
    spawn_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the osascript command without executing or recording",
    )

    status_parser = subparsers.add_parser("status", help="render the current chain state")
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
        if args.subcommand == "plan":
            _emit(plan_payload(root, args.plan), _render_plan, args.format == "json")
        elif args.subcommand == "spawn":
            if args.max_rounds is not None:
                # F17: the adaptive default resolves inside the spawn (it needs
                # the plan totals and the recorded cap); an explicit flag is
                # validated here exactly as before.
                validate_cap(args.max_rounds, "max-rounds")
            _emit(spawn_payload(root, args), _render_spawn, args.format == "json")
        else:
            _emit(status_payload(root), _render_status, args.format == "json")
    except NoPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.subcommand == "spawn" and root is not None:
            append_refusal_event(_chain_dir(root), "autorun", exc)
        return EXIT_NO_PLAN
    except ChainSpawnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.subcommand == "spawn" and root is not None:
            append_refusal_event(_chain_dir(root), "autorun", exc)
        return EXIT_FAILURE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

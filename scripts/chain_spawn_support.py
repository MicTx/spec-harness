#!/usr/bin/env python3
# scripts/chain_spawn_support.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared chain-state module for the autorun/autoplan spawn scripts (plans/01 F5).

Import-only surface — no CLI, no new execution face. ``scripts/autorun_spawn.py``
and ``scripts/autoplan_spawn.py`` keep their own command surfaces, guards, and
renders; this module is the one implementation of the pieces both chains
duplicated (plans/01 F5 inventory):

  - constants and time: the chain guard table (F12 single home) — the
    round/pass caps, the osascript/ps timeouts, the worker-confirmation
    bound and poll, the close delay/poll/wait bounds, the chain file names,
    ``utc_now``;
  - paths and state: ``chain_dir`` / ``resolve_root`` / ``under_root`` /
    ``atomic_write``, the strict state read mechanism, and the tolerant
    ``read_state`` (the F4 four-status read with the corrupt-state rebuild —
    the recovery *decisions* stay in ``scripts/chain_recovery.py``);
  - lock: ``single_chain_lock`` (flock mutex + one advisory holder line per
    acquisition) and ``lock_holder`` (the status-side probe);
  - audit: ``append_audit`` (``spawns.jsonl``), ``append_event`` /
    ``append_refusal_event`` / ``append_recovery_event`` (``events.jsonl``,
    anti-glue tail guard) and the events-tail reading helpers;
  - model identity (F14): ``build_worker_command`` (the worker argv render
    with the optional identity injection), the per-host CLI flag mapping
    ``HOST_MODEL_FLAGS``, and the ``model_injection_level`` judgment — the
    render surface only; the identity lock/propagation/refusal semantics
    stay in each spawn script;
  - tty and recycle: ``session_tty``, ``worker_running_on_tty``, the
    Terminal window lookup/parse builders, the bounded-wait close helper
    build/schedule pair, and ``recycle_previous_window``;
  - error base: ``ChainSpawnError`` — both chain scripts' operational error
    classes derive from (and re-export) this one class.

The Terminal.app automation pieces here are scope-limited to exactly two
spawn steps — the autorun round spawn (``scripts/autorun_spawn.py``) and the
autoplan pass spawn (``scripts/autoplan_spawn.py``) — both inheriting the
recorded overturn of the 2026-09-03 no-AppleScript policy in the
``2026-10-06_add-autorun-command`` Development Record; no other caller may
reuse them.

Collaborator seams: several functions accept injectable collaborators
(``controlling``, ``worker_check``, ``schedule_close``, ``pid_alive``,
``error``). The chain scripts bind their thin wrappers to their own module
globals, which is what the chain test files monkeypatch — the seam keeps the
patch points alive while the implementation lives here once.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
import shlex
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

# Guard table (F12 unified home): every chain boundary constant lives here
# and nowhere else — the two spawn scripts and chain_recovery import them.
OSASCRIPT_TIMEOUT_SECONDS = 30
PS_TIMEOUT_SECONDS = 5
WORKER_START_TIMEOUT_SECONDS = 60
WORKER_START_POLL_SECONDS = 1.0
CLOSE_DELAY_SECONDS = 3
CLOSE_POLL_INTERVAL_SECONDS = 2
CLOSE_WAIT_EXIT_SECONDS = 120
DEFAULT_MAX_ROUNDS = 20
DEFAULT_MAX_PASSES = 12

AUTORUN_CHAIN_DIR = ".spec/autorun"
AUTOPLAN_CHAIN_DIR = ".spec/autoplan"
CHAIN_STATE_FILE = "chain.json"
CHAIN_SPAWNS_FILE = "spawns.jsonl"
CHAIN_LOCK_FILE = "chain.lock"
CHAIN_EVENTS_FILE = "events.jsonl"
# Trailing events lines scanned for ``last_refusal`` (recent failures only).
EVENTS_TAIL_LINES = 20

# Read statuses of the tolerant ``read_state`` (F4 contract).
READ_OK = "ok"
READ_MISSING = "missing"
READ_RECOVERED = "recovered"
READ_CORRUPT = "corrupt"


class ChainSpawnError(Exception):
    """Operational refusal or failure shared by both chain scripts.

    ``autorun_spawn.AutorunError`` and ``autoplan_spawn.AutoplanSpawnError``
    derive from this class so one ``main()`` catch handles both chains'
    operational failures while each script's audit ``type`` stays its own
    class name.
    """


def validate_cap(value: int, option: str) -> int:
    """Refuse a non-positive ``--{option}`` cap value (F12 guard helper).

    One check for both chains' ``--max-rounds`` / ``--max-passes``: a cap
    below 1 would make every spawn refuse, so it fails fast with the
    verbatim CLI text. ``value`` comes from argparse (``type=int``); the
    base class is raised because this is a CLI-argument refusal, not a
    chain-specific operational error.
    """
    if value < 1:
        raise ChainSpawnError(f"--{option} must be >= 1")
    return value


def next_index_within_cap(
    state_value: Any,
    cap: int,
    *,
    label: str,
    option: str,
    non_integer_template: str,
    error: Callable[..., Exception] = ChainSpawnError,
) -> int:
    """Derive the next chain counter from state, refusing past-cap moves.

    F12 guard helper — the single decision for both chains' cap semantics:

    - missing state (``None``) → the chain starts at 1;
    - a value that is not a plain ``int`` (``bool``, float, string, …) →
      refused as non-integer — the old ``int()`` casts silently accepted
      ``"4"`` / ``4.0`` / ``True`` and truncated ``4.5``; the strict gate
      keeps chain counters honest in state files a human may have edited;
    - ``next == cap`` allows the last spawn; only ``next > cap`` refuses —
      a new chain always gets its full cap;
    - negative ints keep the pre-F12 behavior (no extra validation): the
      arithmetic stands, the spawn proceeds with the derived index.

    ``non_integer_template`` carries each chain's verbatim refusal text
    (``{!r}``-formatted with the raw state value); ``error`` is the
    caller's exception factory so each chain's audit ``type`` stays its
    own class name.
    """
    if state_value is None:
        return 1
    if isinstance(state_value, bool) or not isinstance(state_value, int):
        raise error(non_integer_template.format(state_value))
    next_index = state_value + 1
    if next_index > cap:
        raise error(f"{label} cap reached: next {label} {next_index} exceeds --{option} {cap}")
    return next_index


def utc_now() -> str:
    """One clock shape for every chain timestamp (ISO-8601Z)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- paths and state -------------------------------------------------------


def chain_dir(root: Path, chain: str) -> Path:
    """The runtime state directory of one chain under ``root``."""
    if chain == "autorun":
        return root / AUTORUN_CHAIN_DIR
    if chain == "autoplan":
        return root / AUTOPLAN_CHAIN_DIR
    raise ChainSpawnError(f"unknown chain: {chain!r} (expected 'autorun' or 'autoplan')")


def resolve_root(raw_root: str) -> Path:
    root = Path(raw_root).expanduser().resolve()
    if not root.is_dir():
        raise ChainSpawnError(f"project root is not a directory: {root}")
    return root


def under_root(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_state_file(state_file: Path, *, label: str, error: Callable[[str], Exception]) -> Optional[Dict[str, object]]:
    """Strict single-file state read: ``None`` when missing, raise otherwise.

    The two spawn flows refuse on an unreadable state (recovery is the
    status flow's job), so the mechanism is shared here and each script
    supplies its own error class and message label.
    """
    if not state_file.is_file():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise error(f"{label} unreadable ({state_file}): {exc}") from exc
    if not isinstance(data, dict):
        raise error(f"{label} is not a JSON object: {state_file}")
    return data


def _normalize_state(state: Dict[str, object]) -> Dict[str, object]:
    """In-memory defaults for old-format states; never written back (except rebuild)."""
    normalized = dict(state)
    if not isinstance(normalized.get("window_recycle"), dict):
        normalized["window_recycle"] = {}
    if not isinstance(normalized.get("host"), str):
        normalized["host"] = "unknown"
    return normalized


def last_audit_record(path: Path) -> Tuple[Optional[Dict[str, object]], int]:
    """Last parseable JSON-object line of a JSONL file, plus its 1-based number.

    Trailing partial (unparseable) lines are skipped — a half-written crash
    tail must not hide the last complete record. Returns ``(None, 0)`` when
    the file is missing, unreadable, or holds no parseable object line.
    """
    if not path.is_file():
        return None, 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, 0
    for index in range(len(lines) - 1, -1, -1):
        stripped = lines[index].strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            print(
                f"warning: skipping unparseable tail line in {path.name} (line {index + 1})",
                file=sys.stderr,
            )
            continue
        if isinstance(parsed, dict):
            return parsed, index + 1
    return None, 0


def read_audit_tail(chain_dir: Path) -> Optional[Dict[str, object]]:
    """The last complete ``spawns.jsonl`` record, or ``None`` (partial lines skipped)."""
    record, _ = last_audit_record(chain_dir / CHAIN_SPAWNS_FILE)
    return record


def read_state(chain_dir: Path, chain: str = "unknown") -> Tuple[Optional[Dict[str, object]], str]:
    """Tolerant ``chain.json`` read returning ``(state, read_status)``.

    Statuses: ``ok`` (parsed), ``missing`` (no file — a fresh chain, even when
    audit lines exist), ``recovered`` (corrupt file rebuilt from the last
    parseable ``spawns.jsonl`` line; the rebuild is written back atomically
    with ``recovered_from`` plus a ``recovery`` event), ``corrupt`` (file
    present but unreadable and the audit cannot rebuild it — manual
    inspection; the file is left exactly as found).
    """
    state_file = chain_dir / CHAIN_STATE_FILE
    if not state_file.is_file():
        return None, READ_MISSING
    data: Optional[Dict[str, object]] = None
    try:
        parsed = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            data = parsed
    except (OSError, json.JSONDecodeError):
        data = None
    if data is not None:
        return _normalize_state(data), READ_OK

    record, line_number = last_audit_record(chain_dir / CHAIN_SPAWNS_FILE)
    if record is None:
        return None, READ_CORRUPT

    # Rebuild from the audit tail: atomic write-back plus the recovery audit.
    # The audit row converts to state form first: F14's per-spawn injection
    # level is a spawns-row-only fact and never enters chain.json (the lock
    # rides along; old rows predate both keys and stay legacy-shaped).
    recovered = _normalize_state(record)
    recovered.pop("model_injection", None)
    recovered["recovered_from"] = f"{CHAIN_SPAWNS_FILE} line {line_number}"
    atomic_write(state_file, json.dumps(recovered, indent=2, sort_keys=True) + "\n")
    append_recovery_event(
        chain_dir,
        chain,
        "recovered_state",
        {"source": f"{CHAIN_SPAWNS_FILE} line {line_number}", "state_file": CHAIN_STATE_FILE},
    )
    return recovered, READ_RECOVERED


# --- lock ------------------------------------------------------------------


def write_lock_holder(lock_handle, chain: str) -> None:
    """Append one advisory holder line to the already-flocked lock file.

    flock is the mutex; the content is advice for ``status``. The line is
    never cleared on release (clearing opens a release race), so readers take
    the last parseable line and cross-check pid liveness. A write failure
    degrades to a stderr note and never breaks the spawn.
    """
    line = json.dumps(
        {
            "pid": os.getpid(),
            "command": " ".join(sys.argv),
            "at": utc_now(),
            "chain": chain,
        },
        sort_keys=True,
    )
    try:
        lock_handle.write(line + "\n")
        lock_handle.flush()
    except OSError as exc:
        print(f"warning: could not record lock holder info: {exc}", file=sys.stderr)


@contextmanager
def single_chain_lock(
    chain_dir: Path,
    chain: str,
    *,
    error: Callable[[str], Exception] = ChainSpawnError,
    busy_message: Optional[str] = None,
) -> Iterator[Any]:
    """Hold the chain's flock for the wrapped block, recording the holder.

    flock ``LOCK_EX | LOCK_NB`` is the mutex (parallel chains are refused,
    never queued); one advisory holder line is appended per acquisition. The
    caller supplies its own error factory so the refusal carries the
    script's class name and wording into the audit stream.
    """
    chain_dir.mkdir(parents=True, exist_ok=True)
    lock_path = chain_dir / CHAIN_LOCK_FILE
    if busy_message is None:
        busy_message = f"another {chain} chain holds {CHAIN_LOCK_FILE}; parallel chains are refused"
    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise error(busy_message) from exc
        write_lock_holder(lock_handle, chain)
        try:
            yield lock_handle
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def pid_alive(pid: int) -> Optional[bool]:
    """Whether ``pid`` names a live process; ``None`` when unverifiable."""
    try:
        probe = subprocess.run(
            ["ps", "-p", str(pid), "-o", "pid="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return probe.returncode == 0 and bool(probe.stdout.strip())


def _last_lock_holder_line(lock_path: Path) -> Optional[Dict[str, object]]:
    """The last parseable JSON holder line in the lock file, or ``None``."""
    try:
        text = lock_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    holder: Optional[Dict[str, object]] = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            holder = parsed
    return holder


def lock_holder(
    chain_dir: Path, *, pid_alive_check: Optional[Callable[[int], Optional[bool]]] = None
) -> Dict[str, object]:
    """Probe the chain lock for the status render.

    Mirrors the spawn's own acquisition (``LOCK_EX | LOCK_NB`` on a brief
    descriptor, released immediately), so the probe never keeps the mutex and
    never blocks a concurrent spawn beyond the lock's own semantics. When
    blocked, the advisory holder line decides the render: a live pid gives
    ``held by pid N (command) since at``; missing content, a dead pid, or an
    unverifiable one degrade to holder-unknown / stale-content. flock remains
    the only source of mutual exclusion.
    """
    if pid_alive_check is None:
        pid_alive_check = pid_alive
    lock_path = chain_dir / CHAIN_LOCK_FILE
    if not lock_path.is_file():
        return {"status": "free"}
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError:
        return {"status": "unknown"}
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = _last_lock_holder_line(lock_path)
            if not holder:
                return {"status": "held", "holder": "unknown"}
            pid = holder.get("pid")
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                return {"status": "held", "holder": "unknown"}
            alive = pid_alive_check(pid)
            if alive is None:
                return {"status": "held", "holder": "unknown"}
            if not alive:
                return {"status": "held", "holder": "stale", "pid": pid}
            payload: Dict[str, object] = {"status": "held", "holder": "pid", "pid": pid}
            if isinstance(holder.get("command"), str):
                payload["command"] = holder["command"]
            if isinstance(holder.get("at"), str):
                payload["since"] = holder["at"]
            return payload
        except OSError:
            return {"status": "unknown"}
        fcntl.flock(fd, fcntl.LOCK_UN)
        return {"status": "free"}
    finally:
        os.close(fd)


# --- audit -----------------------------------------------------------------


def append_audit(path: Path, record: Dict[str, object]) -> None:
    """Append one JSON line to the spawn audit (``spawns.jsonl``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as audit:
        audit.write(json.dumps(record, sort_keys=True) + "\n")


def append_event(events_path: Path, event: Dict[str, object], *, warn_template: Optional[str] = None) -> None:
    """Best-effort JSON event append with the anti-glue tail guard.

    Never glues onto an unterminated tail (a partial crash write, or a
    pre-2026-10-07 helper record with no trailing newline); a recording
    failure only prints a stderr note and never re-raises. ``warn_template``
    lets each caller keep its historical stderr wording.
    """
    if warn_template is None:
        warn_template = "could not record chain event: {}"
    try:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with open(events_path, "a+", encoding="utf-8") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size:
                handle.seek(size - 1)
                if handle.read(1) != "\n":
                    handle.write("\n")
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError as exc:
        print(warn_template.format(exc), file=sys.stderr)


def append_refusal_event(chain_dir: Path, chain: str, exc: Exception) -> None:
    """Best-effort ``spawn_refusal`` event append for one refused spawn.

    The events stream is the failure audit; a recording failure only prints a
    stderr note and changes neither the exit code nor the original error.
    """
    append_event(
        chain_dir / CHAIN_EVENTS_FILE,
        {
            "kind": "spawn_refusal",
            "at": utc_now(),
            "chain": chain,
            "type": type(exc).__name__,
            "message": str(exc),
        },
        warn_template="warning: could not record spawn refusal event: {}",
    )


def append_recovery_event(chain_dir: Path, chain: str, action: str, detail: Dict[str, object]) -> None:
    """Best-effort ``kind=recovery`` audit append (recovered_state / state_diverged)."""
    append_event(
        chain_dir / CHAIN_EVENTS_FILE,
        {"kind": "recovery", "at": utc_now(), "chain": chain, "action": action, "detail": detail},
    )


def read_event_records(events_path: Path, *, warn: bool = True) -> List[Dict[str, object]]:
    """Parsed event lines, skipping unparseable (partial) tails.

    ``warn`` prints one stderr note per skipped line (the status render wants
    the note; dedup scans stay quiet).
    """
    if not events_path.is_file():
        return []
    try:
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        if warn:
            print(f"warning: events file unreadable: {exc}", file=sys.stderr)
        return []
    records: List[Dict[str, object]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            if warn:
                print("warning: skipping unparseable events line in status output", file=sys.stderr)
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def read_last_refusal(
    events_path: Path, *, tail_lines: int = EVENTS_TAIL_LINES
) -> Tuple[int, Optional[Dict[str, object]]]:
    """Return ``(total spawn_refusals, last refusal within the tail window)``.

    ``last_refusal`` comes from the final ``tail_lines`` lines only and is
    filtered to ``kind=spawn_refusal``, so ``recycle_close`` / ``recovery``
    events never cover it; the count spans the whole file.
    """
    if not events_path.is_file():
        return 0, None
    try:
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"warning: events file unreadable: {exc}", file=sys.stderr)
        return 0, None
    refusals: List[Tuple[int, Dict[str, object]]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            print("warning: skipping unparseable events line in status output", file=sys.stderr)
            continue
        if isinstance(parsed, dict) and parsed.get("kind") == "spawn_refusal":
            refusals.append((index, parsed))
    cutoff = len(lines) - tail_lines
    last: Optional[Dict[str, object]] = None
    for index, parsed in reversed(refusals):
        if index >= cutoff:
            last = {
                "kind": parsed.get("kind", "spawn_refusal"),
                "at": parsed.get("at"),
                "type": parsed.get("type"),
                "message": parsed.get("message"),
            }
            break
    return len(refusals), last


def recovery_event_recorded(events_path: Path, action: str, target: Optional[str]) -> bool:
    """True when an identical recovery event is already on the audit stream.

    Keeps a persistent ``state_diverged`` from appending one audit line per
    ``status`` call: the divergence is recorded once until the state changes.
    """
    for parsed in read_event_records(events_path, warn=False):
        if parsed.get("kind") != "recovery" or parsed.get("action") != action:
            continue
        recorded = parsed.get("detail")
        recorded_target = recorded.get("target") if isinstance(recorded, dict) else None
        if recorded_target == target:
            return True
    return False


# --- model identity (F14) --------------------------------------------------
#
# The chain model-identity render surface, shared by both spawn scripts:
# the per-host flag mapping, the argv render, and the injection-level
# judgment. The lock/propagation/refusal semantics stay in each script's
# spawn wiring (this module renders; the scripts decide).

# Per-host CLI flags for the identity components, finalized by --help
# evidence against each installed host CLI (F14 checklist): pi exposes
# --model/--thinking, codex exposes --model plus the -c config channel
# (model_reasoning_effort=<level>, the same channel the sandbox exemption
# uses), claude exposes --model/--effort. Each entry is
# ``(prefix_tokens, value_template)`` rendered as prefix_tokens +
# [value_template.format(value)] right after the worker binary; ``None``
# means the host CLI has no flag for that component (partial injection).
# The tests render through this table instead of hardcoding flag literals,
# so a host CLI flag rename is a one-line fix here.
HOST_MODEL_FLAGS: Dict[str, Dict[str, Optional[Tuple[Tuple[str, ...], str]]]] = {
    "pi": {
        "id": (("--model",), "{}"),
        "reasoning": (("--thinking",), "{}"),
    },
    "codex": {
        "id": (("--model",), "{}"),
        "reasoning": (("-c",), "model_reasoning_effort={}"),
    },
    "claude": {
        "id": (("--model",), "{}"),
        "reasoning": (("--effort",), "{}"),
    },
}

# Identity components in render order; keys of the identity dict and of each
# HOST_MODEL_FLAGS per-host entry.
MODEL_IDENTITY_COMPONENTS: Tuple[str, ...] = ("id", "reasoning")


def build_model_flag_argv(host: str, identity: Dict[str, str]) -> Optional[List[str]]:
    """Render an identity's non-empty components into worker flag argv.

    Returns the tokens to insert right after the worker binary, or ``None``
    when nothing renders (empty identity, unknown host, or no matching
    flags). The full-vs-partial distinction is
    :func:`model_injection_level`'s judgment; this returns what renders.
    """
    mapping = HOST_MODEL_FLAGS.get(host)
    if not mapping:
        return None
    argv: List[str] = []
    for component in MODEL_IDENTITY_COMPONENTS:
        value = identity.get(component)
        if not value:
            continue
        spec = mapping.get(component)
        if spec is None:
            continue
        prefix, template = spec
        argv.extend(prefix)
        argv.append(template.format(value))
    return argv or None


def model_injection_level(identity: Optional[Dict[str, str]], host: str) -> str:
    """Injection level for a host spawn carrying ``identity``.

    ``full`` — every non-empty identity component rendered into the argv;
    ``partial`` — some rendered, some not (host lacks a flag for one);
    ``none`` — no identity (worker default) or nothing renderable. The
    fourth vocabulary value ``custom`` is the ``--command`` bypass fact and
    is written by the spawn wiring, never by this host-path judgment.
    """
    if not identity:
        return "none"
    mapping = HOST_MODEL_FLAGS.get(host)
    if not mapping:
        return "none"
    components = [component for component in MODEL_IDENTITY_COMPONENTS if identity.get(component)]
    if not components:
        return "none"
    injected = [component for component in components if mapping.get(component) is not None]
    if not injected:
        return "none"
    return "full" if len(injected) == len(components) else "partial"


def build_worker_command(
    host: str, root: Path, prompt: str, model_identity: Optional[Dict[str, str]] = None
) -> List[str]:
    """Build the interactive worker argv for the next-round session.

    Every host runs its CLI's normal interactive session (visible TUI,
    session persisted), not a print/exec mode; the prompt is the initial
    message. Recorded overturn of the headless worker choice in the
    ``2026-10-06_add-autorun-command`` Development Record. Shared by both
    spawn scripts since F14 (the render surface lives here once; the
    identity lock/propagation/refusal semantics stay in each script).

    F14: ``model_identity`` (``{id, reasoning?}`` or ``None`` for the worker
    default) renders through the shared host flag mapping right after the
    worker binary; ``None`` keeps the argv byte-identical to the pre-F14
    form (the compatibility red line).
    """
    if host == "codex":
        # codex >=0.160 seatbelt denies .git writes under workspace-write, so
        # `git add`/`git commit` die with EPERM at the round's commit stage
        # (verified 2026-10-07 against codex-cli 0.160.1). Carve the repo's
        # .git back into the writable roots; everything else stays sandboxed.
        git_root = json.dumps(str(root / ".git"))
        argv = [
            "codex",
            "--cd",
            str(root),
            "--sandbox",
            "workspace-write",
            "-c",
            "sandbox_workspace_write.network_access=true",
            "-c",
            f"sandbox_workspace_write.writable_roots=[{git_root}]",
            "--",
            prompt,
        ]
    elif host == "pi":
        argv = ["pi", "--mode", "text", "--", prompt]
    elif host == "claude":
        argv = ["claude", "--dangerously-skip-permissions", prompt]
    else:
        raise ChainSpawnError(f"unknown worker host: {host}")
    if model_identity:
        model_argv = build_model_flag_argv(host, model_identity)
        if model_argv:
            argv[1:1] = model_argv
    return argv


# --- tty and window recycle ------------------------------------------------


def controlling_tty() -> Optional[str]:
    """Return the controlling terminal of this process, or ``None``."""
    try:
        fd = os.open("/dev/tty", os.O_RDONLY)
    except OSError:
        return None
    try:
        return os.ttyname(fd)
    except OSError:
        return None
    finally:
        os.close(fd)


def nearest_ancestor_tty() -> Optional[str]:
    """Walk the parent-process chain and return ``/dev/<tty>`` for the
    nearest ancestor whose controlling tty is real.

    Agent tool subprocesses have no controlling terminal of their own
    (``/dev/tty`` fails with ``Device not configured``), but two or three
    levels up the chain the host shell / ``login`` inside the Terminal tab
    still names the window's tty. The walk stops at the first ancestor with
    a usable tty and returns ``None`` when the chain cannot be read or no
    ancestor has one — callers keep the existing skip reason on ``None``.
    """
    pid = os.getpid()
    for _ in range(64):
        try:
            probe = subprocess.run(
                ["ps", "-o", "ppid=,tty=,command=", "-p", str(pid)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=PS_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            break
        line = probe.stdout.strip()
        if not line:
            break
        parts = line.split(None, 2)
        if len(parts) < 2:
            break
        tty = parts[1].strip()
        if tty and tty != "??":
            return f"/dev/{tty}"
        try:
            pid = int(parts[0])
        except ValueError:
            break
        if pid <= 1:
            break
    return None


def session_tty(
    controlling: Optional[Callable[[], Optional[str]]] = None,
) -> Optional[str]:
    """Return the session's Terminal tty.

    The controlling terminal when this process has one *and it resolves to a
    real device path*; otherwise the nearest ancestor's tty. Two real-world
    gaps: agent tool subprocesses routinely lack ``/dev/tty`` entirely, and
    macOS answers ``ttyname()`` for a ``/dev/tty`` descriptor with the
    literal ``/dev/tty`` (verified 2026-10-07 in a Terminal tab) — either way
    the literal never matches a window's tty, so both fall through to the
    ``ps``-based resolution (own pid first, then ancestors). ``controlling``
    lets the calling script inject its own patchable binding.
    """
    if controlling is None:
        controlling = controlling_tty
    tty = controlling()
    if tty and tty != "/dev/tty":
        return tty
    return nearest_ancestor_tty()


def worker_running_on_tty(worker: str, tty: str) -> bool:
    """Check whether a ``worker`` process runs on the given Terminal tty.

    Matches the command line, not ``comm``: the hosts are interpreter-hosted
    (codex is a ``#!/usr/bin/env node`` script, the pi launcher a ``#!/bin/sh``
    shim), so ``comm`` reports ``node`` / ``/bin/sh`` and the host name never
    appears there — verified 2026-10-07 on a pty, where a node-shebang CLI
    reads as ``ttysNNN  node`` under ``ps -o tty=,comm=`` and as
    ``ttysNNN  node /path/to/cli ...`` under ``ps -o tty=,command=``.
    """
    try:
        completed = subprocess.run(
            ["ps", "-axo", "tty=,command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    tty_name = tty.rsplit("/", 1)[-1]
    for line in completed.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2 or fields[0].strip() != tty_name:
            continue
        # Only the leading tokens carry the host identity: token[0] for a
        # native binary (``pi`` / ``claude``), token[1] for an interpreter
        # running a shebang script (``/bin/sh /usr/local/bin/pi``,
        # ``node …/codex``). A bare word further right (``sh -c 'echo pi'``,
        # ``tail -f logs/pi``, ``vim pi``) is unrelated command data.
        tokens = fields[1].split()
        if not tokens:
            continue
        if os.path.basename(tokens[0].rstrip("/")) == worker:
            return True
        if len(tokens) > 1 and "/" in tokens[1] and os.path.basename(tokens[1].rstrip("/")) == worker:
            return True
    return False


def escape_applescript(text: str) -> str:
    """Escape a string for an AppleScript double-quoted literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _window_lookup_lines(tty_expression: str) -> List[str]:
    """AppleScript lines filling the lookup variables for the tab whose tty
    equals ``tty_expression`` (a quoted literal or a variable): ``match`` is
    the hosting window id (``""`` when none), ``matchTabs`` its tab count and
    ``matchBusy`` whether that tab still runs a process other than the shell."""
    return [
        '\tset match to ""',
        "\tset matchTabs to 0",
        "\tset matchBusy to false",
        "\trepeat with w in windows",
        "\t\trepeat with t in tabs of w",
        "\t\t\tif tty of t is {} then".format(tty_expression),
        "\t\t\t\tset match to (id of w as string)",
        "\t\t\t\tset matchTabs to (count of tabs of w)",
        "\t\t\t\tset matchBusy to (busy of t)",
        "\t\t\t\texit repeat",
        "\t\t\tend if",
        "\t\tend repeat",
        '\t\tif match is not "" then exit repeat',
        "\tend repeat",
    ]


def build_terminal_command(
    root: Path, worker_command: List[str], custom_command: Optional[str] = None
) -> Tuple[List[str], str]:
    """Build the osascript argv opening a Terminal window at ``root``.

    Returns ``(osascript argv, shell command)``. The shell command is
    ``cd <root> && <worker argv>`` — or ``cd <root> && <custom command>``
    when a raw ``--command`` override is given. The AppleScript runs the
    command in a new tab and replies ``"<window id> <tab tty>"`` on
    stdout (the window is found by matching the spawned tab's tty, so no
    front-window race), so the spawn can verify the next round and
    recycle windows.
    """
    if custom_command is not None:
        tail = custom_command
    else:
        tail = " ".join(shlex.quote(part) for part in worker_command)
    shell_command = "cd {} && {}".format(shlex.quote(str(root)), tail)
    applescript = "\n".join(
        [
            'tell application "Terminal"',
            '\tset spawnedTab to do script "{}"'.format(escape_applescript(shell_command)),
            "\tset spawnedTty to tty of spawnedTab",
            *_window_lookup_lines("spawnedTty"),
            '\treturn match & " " & spawnedTty',
            "end tell",
        ]
    )
    return ["osascript", "-e", applescript], shell_command


def parse_spawn_result(stdout: str) -> Tuple[Optional[int], Optional[str]]:
    """Parse the spawn osascript reply ``"<window id> <tab tty>"``."""
    parts = stdout.strip().split()
    if len(parts) != 2:
        return None, None
    raw_id, tty = parts
    try:
        window_id = int(raw_id)
    except ValueError:
        return None, None
    if not tty.startswith("/dev/"):
        return None, None
    return window_id, tty


def build_window_lookup_applescript(tty: str) -> str:
    """Build the AppleScript reporting the window hosting ``tty``.

    Replies ``"<window id> <tab count> <busy>"`` (``" 0 false"`` when no
    window hosts the tty), so the caller learns the window and whether closing
    it would take unrelated tabs — or a still-running session — with it.
    """
    return "\n".join(
        [
            'tell application "Terminal"',
            *_window_lookup_lines('"{}"'.format(escape_applescript(tty))),
            '\treturn match & " " & (matchTabs as string) & " " & (matchBusy as string)',
            "end tell",
        ]
    )


def parse_window_lookup(stdout: str) -> Tuple[Optional[int], int, bool]:
    """Parse the window-lookup reply into ``(window id, tab count, busy)``.

    An empty, malformed, or tab-less reply comes back as ``(None, 0, False)``:
    no window hosts the tty, so the caller skips the recycling.
    """
    parts = stdout.strip().split()
    if len(parts) != 3:
        return None, 0, False
    try:
        window_id = int(parts[0])
        tab_count = int(parts[1])
    except ValueError:
        return None, 0, False
    busy = parts[2] == "true"
    if tab_count < 1:
        return None, 0, False
    return window_id, tab_count, busy


def build_close_applescript(
    window_id: int,
    close_wait_seconds: int,
    *,
    poll_seconds: float = CLOSE_POLL_INTERVAL_SECONDS,
) -> str:
    """Build the AppleScript closing the previous round's Terminal window
    once its session exits, waiting up to ``close_wait_seconds``.

    Guards from real-device findings (2026-10-07): Terminal's AppleScript
    cannot close an individual tab, and closing a window whose tab still runs
    a process raises Terminal's cancel/terminate sheet instead of closing. So
    the script polls every ``delay {poll_seconds}``: it closes only a window
    that still exists, still holds exactly one tab, and whose session has
    exited. A window that vanished reports ``window-gone``, a tab count other
    than one reports ``multi-tab``, and an exhausted wait reports
    ``busy-timeout`` (the window stays open — fail-open).
    """
    return "\n".join(
        [
            'tell application "Terminal"',
            "\tset waited to 0",
            "\trepeat",
            f'\t\tif not (exists window id {window_id}) then return "window-gone"',
            f"\t\tset tabCount to (count of tabs of window id {window_id})",
            '\t\tif tabCount is 0 then return "window-gone"',
            '\t\tif tabCount is not 1 then return "multi-tab"',
            f"\t\tif not (busy of tab 1 of window id {window_id}) then",
            f"\t\t\tclose window id {window_id}",
            '\t\t\treturn "closed"',
            "\t\tend if",
            f'\t\tif waited >= {close_wait_seconds} then return "busy-timeout"',
            f"\t\tdelay {poll_seconds}",
            f"\t\tset waited to waited + {poll_seconds}",
            "\tend repeat",
            "end tell",
        ]
    )


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
    poll_seconds: float = CLOSE_POLL_INTERVAL_SECONDS,
    error: Callable[..., Exception] = ChainSpawnError,
) -> List[str]:
    """Build the detached argv closing a Terminal window after a delay.

    The helper sleeps ``delay_seconds``, runs the bounded close AppleScript,
    and appends one ``recycle_close`` JSON line to ``events_path`` — result
    from the fixed vocabulary (``closed`` / ``multi-tab`` / ``window-gone`` /
    ``busy-timeout``; anything else, including an osascript failure, records
    ``osascript-error``) plus the chain context (``chain`` and ``round`` or
    ``pass``, ``window_id``, ``prev_tty``, ``waited_ms``) so attempts and
    outcomes can be joined per window. The append is fire-and-forget: a
    failure to write never blocks or re-raises (stdout/stderr are discarded
    by the caller).
    """
    if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0:
        raise error(f"window id must be a positive integer: {window_id!r}")
    if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, int) or delay_seconds < 0:
        raise error(f"close delay must be >= 0: {delay_seconds!r}")
    if isinstance(close_wait_seconds, bool) or not isinstance(close_wait_seconds, int) or close_wait_seconds < 0:
        raise error(f"close wait must be >= 0: {close_wait_seconds!r}")
    if chain not in ("autorun", "autoplan"):
        raise error(f"chain must be 'autorun' or 'autoplan': {chain!r}")
    if (round_index is None) == (pass_index is None):
        raise error("exactly one of round_index / pass_index is required")
    context_name = "round" if round_index is not None else "pass"
    context_value = round_index if round_index is not None else pass_index
    if isinstance(context_value, bool) or not isinstance(context_value, int) or context_value < 1:
        raise error(f"{context_name} index must be a positive integer: {context_value!r}")
    if not isinstance(prev_tty, str) or not prev_tty:
        raise error(f"prev_tty is required: {prev_tty!r}")
    events_path = Path(events_path)
    if not events_path.is_absolute():
        raise error(f"events path must be absolute: {events_path}")
    literal = json.dumps(
        {
            "kind": "recycle_close",
            "at": "__AT__",
            "chain": chain,
            context_name: context_value,
            "window_id": window_id,
            "prev_tty": prev_tty,
            "result": "__RESULT__",
            "waited_ms": "__WAITED_MS__",
        }
    )
    line_template = literal.replace('"__AT__"', '"%s"').replace('"__RESULT__"', '"%s"').replace('"__WAITED_MS__"', "%s")
    script = (
        "start=$(date +%s)\n"
        "sleep {delay}\n"
        'out=$(osascript -e {applescript} 2>/dev/null) || out=""\n'
        'case "$out" in\n'
        '  closed|multi-tab|window-gone|busy-timeout) result="$out" ;;\n'
        '  *) result="osascript-error" ;;\n'
        "esac\n"
        "end=$(date +%s)\n"
        "now=$(date -u +%Y-%m-%dT%H:%M:%SZ)\n"
        "waited_ms=$(( (end - start) * 1000 ))\n"
        # The template carries its own trailing newline: without it two
        # consecutive append records glue into one unparseable line (found
        # 2026-10-07 in the live events stream — rounds 2/3 close records).
        'printf \'{template}\\n\' "$now" "$result" "$waited_ms" >> {events} 2>/dev/null'
    ).format(
        delay=delay_seconds,
        applescript=shlex.quote(build_close_applescript(window_id, close_wait_seconds, poll_seconds=poll_seconds)),
        template=line_template,
        events=shlex.quote(str(events_path)),
    )
    return ["/bin/sh", "-c", script]


def schedule_window_close(
    window_id: int,
    delay_seconds: int,
    close_wait_seconds: int,
    *,
    chain: str,
    round_index: Optional[int] = None,
    pass_index: Optional[int] = None,
    prev_tty: str,
    events_path: Path,
    poll_seconds: float = CLOSE_POLL_INTERVAL_SECONDS,
) -> None:
    """Launch the detached close helper; it outlives this process group."""
    subprocess.Popen(
        build_close_argv(
            window_id,
            delay_seconds,
            close_wait_seconds,
            chain=chain,
            round_index=round_index,
            pass_index=pass_index,
            prev_tty=prev_tty,
            events_path=events_path,
            poll_seconds=poll_seconds,
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
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
    timeout_seconds: Optional[int] = None,
    delay_seconds: Optional[int] = None,
    close_wait_seconds: Optional[int] = None,
    worker_check: Optional[Callable[[str, str], bool]] = None,
    schedule_close: Optional[Callable[..., None]] = None,
) -> Dict[str, object]:
    """Confirm the next round and schedule the previous window's close.

    Fail-open: any skip condition leaves the previous window open and
    returns the recorded reason; the chain continues in the new window.
    A one-tab previous window is scheduled for the detached close helper,
    which waits (bounded by ``close_wait_seconds``) for the previous session
    to exit before closing: Terminal cannot close one tab of a window, and
    closing a tab whose process still runs raises its cancel/terminate
    sheet — so the helper polls instead of giving up on the first busy
    observation. The helper appends its outcome to ``events_path``
    (fire-and-forget, append failures are ignored).

    ``worker_check`` / ``schedule_close`` inject the caller's patchable
    collaborators (the chain scripts bind them to their own module globals
    so their test seams keep working).
    """
    if worker_check is None:
        worker_check = worker_running_on_tty
    if schedule_close is None:
        schedule_close = schedule_window_close
    if timeout_seconds is None:
        timeout_seconds = WORKER_START_TIMEOUT_SECONDS
    if delay_seconds is None:
        delay_seconds = CLOSE_DELAY_SECONDS
    if close_wait_seconds is None:
        close_wait_seconds = CLOSE_WAIT_EXIT_SECONDS
    if prev_tty is None:
        return {"status": "skipped", "reason": "spawning session has no controlling Terminal"}
    if new_window_id is None or new_tty is None:
        return {"status": "skipped", "reason": "spawned window id or tty unavailable from osascript"}
    if worker_name:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if worker_check(worker_name, new_tty):
                break
            time.sleep(WORKER_START_POLL_SECONDS)
        else:
            return {
                "status": "skipped",
                "reason": "worker {} not observed on {} within {}s".format(worker_name, new_tty, timeout_seconds),
            }
    lookup_argv = ["osascript", "-e", build_window_lookup_applescript(prev_tty)]
    try:
        lookup = subprocess.run(
            lookup_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=OSASCRIPT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "skipped",
            "reason": "previous window lookup did not answer within {}s".format(OSASCRIPT_TIMEOUT_SECONDS),
        }
    prev_window_id, prev_tab_count, _busy = (
        parse_window_lookup(lookup.stdout) if lookup.returncode == 0 else (None, 0, False)
    )
    if prev_window_id is None:
        return {"status": "skipped", "reason": "no Terminal window hosts {}".format(prev_tty)}
    if prev_window_id == new_window_id:
        return {"status": "skipped", "reason": "previous window is the spawned window"}
    if prev_tab_count > 1:
        return {
            "status": "skipped",
            "reason": (
                "previous window hosts {} tabs and Terminal cannot close one tab; leaving it open".format(
                    prev_tab_count
                )
            ),
        }
    # A still-running session no longer skips the recycle: the close helper
    # waits (bounded) for it to exit — the normal case, since the spawning
    # session is almost always still finishing its round summary right now.
    schedule_close(
        prev_window_id,
        delay_seconds,
        close_wait_seconds,
        chain=chain,
        round_index=round_index,
        pass_index=pass_index,
        prev_tty=prev_tty,
        events_path=events_path,
    )
    return {
        "status": "scheduled",
        "window_id": prev_window_id,
        "delay_seconds": delay_seconds,
        "close_wait_seconds": close_wait_seconds,
    }

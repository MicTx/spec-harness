#!/usr/bin/env python3
# scripts/chain_spawn_support.py
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared chain-state module for the autorun/autoplan spawn scripts (plans/01 F5).

Import-only surface — no CLI, no new execution face. ``scripts/autorun_spawn.py``
and ``scripts/autoplan_spawn.py`` keep their own command surfaces, guards, and
renders; this module is the one implementation of the pieces both chains
duplicated (plans/01 F5 inventory):

  - constants and time: the chain guard table (F12 single home) — the
    opt-in round/pass cap helpers, the osascript/ps timeouts, the
    worker-confirmation bound and poll, the close delay/poll/wait bounds,
    the chain file names, ``utc_now``;
  - paths and state: ``chain_dir`` (tracked summary) / ``chain_runtime_dir``
    (the ``.git/spec-chain/<chain>/`` per-machine runtime root, F16 step 3) /
    ``resolve_root`` / ``under_root`` / ``atomic_write``, the strict state
    read mechanism, the tolerant ``read_state`` (the F4 four-status read
    with the corrupt-state rebuild — the recovery *decisions* stay in
    ``scripts/chain_recovery.py``), and the summary/volatile split
    (``split_volatile_state`` / ``write_chain_state`` /
    ``read_volatile_state``): the tracked summary never carries the
    volatile spawn-moment keys, they live in the runtime tree;
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
  - portable sessions: automatic macOS Terminal.app / Linux and Windows WSL2
    tmux selection, private tmux launch/rollback/state transaction; native
    Windows receives explicit WSL2 guidance without an import-time failure;
  - tty and recycle: ``session_tty``, ``worker_running_on_tty``, the
    Terminal window lookup/parse builders, the bounded-wait close helper
    build/schedule pair, and ``recycle_previous_window``;
  - error base: ``ChainSpawnError`` — both chain scripts' operational error
    classes derive from (and re-export) this one class.

The Terminal.app automation pieces here are scope-limited to exactly three
authorized steps — the autorun round spawn (``scripts/autorun_spawn.py``),
the autoplan pass spawn (``scripts/autoplan_spawn.py``), and the dead
chain-window sweep (``scripts/sweep_chain_windows.py``, F18) — the first
two inheriting the recorded overturn of the 2026-09-03 no-AppleScript policy
in the ``2026-10-06_add-autorun-command`` Development Record, the sweep
extending that authorization in the ``2026-10-08_sweep-dead-chain-windows``
Development Record; no other caller may reuse them.

Collaborator seams: several functions accept injectable collaborators
(``controlling``, ``worker_check``, ``schedule_close``, ``pid_alive``,
``error``). The chain scripts bind their thin wrappers to their own module
globals, which is what the chain test files monkeypatch — the seam keeps the
patch points alive while the implementation lives here once.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # Native Windows: help/status work; spawn explains the WSL2 route.
    fcntl = None  # type: ignore[assignment]

# Guard table (F12 unified home): every chain boundary constant lives here
# and nowhere else — the two spawn scripts and chain_recovery import them.
OSASCRIPT_TIMEOUT_SECONDS = 30
PS_TIMEOUT_SECONDS = 5
WORKER_START_TIMEOUT_SECONDS = 60
WORKER_START_POLL_SECONDS = 1.0
CLOSE_DELAY_SECONDS = 3
CLOSE_POLL_INTERVAL_SECONDS = 2
CLOSE_WAIT_EXIT_SECONDS = 120
# F17 session-recycle escalation: after the handoff is confirmed and the
# delay has let the round summary land, a lingering interactive session is
# abandoned — natural-exit grace first, then SIGTERM, then SIGKILL.
CLOSE_NATURAL_GRACE_SECONDS = 10
CLOSE_TERM_WAIT_SECONDS = 15
# F17 spawn robustness: one bounded retry when the Terminal open osascript
# fails transiently (Terminal busy mid-animation), and its backoff.
TERMINAL_OPEN_ATTEMPTS = 2
TERMINAL_OPEN_RETRY_BACKOFF_SECONDS = 2
# 2026-10-09 refusal close: a spawn refused after its window opened closes
# that window synchronously (交接未确认 = 不留进程). The budget reuses the
# F19 escalated close argv with no scheduling delay; the outer bound covers
# the helper's waits plus its osascript calls.
REFUSAL_CLOSE_WAIT_SECONDS = 30
REFUSAL_CLOSE_HELPER_TIMEOUT_SECONDS = 100
# Chain caps (F1, AUD-01): autorun rounds keep the restored default of 5;
# autoplan passes are uncapped without a flag (2026-10-19 user decision) —
# an explicit ``--max-rounds`` / ``--max-passes`` is the caller's own limit,
# and ``--unbounded`` stays as an explicit compatibility entry. The strict
# validation and the next==cap boundary judgment (F12) stay unchanged. The
# effective cap resolves per spawn (flag > --unbounded > default) and
# travels to the next round's prompt, so a chain's cap decision is
# self-carrying.
DEFAULT_AUTORUN_MAX_ROUNDS = 5

# F1 (AUD-01) chain permission modes. ``ask`` renders no escalation flag on
# any host; ``bypass`` renders the host's escalation flag (claude's
# ``--dangerously-skip-permissions``). ``inherit`` is a spawn-script
# decision — probe the parent session — resolved to ask/bypass before the
# argv renders; the render surface only knows the two effective modes.
PERMISSION_MODES: Tuple[str, ...] = ("inherit", "ask", "bypass")
WORKER_PERMISSION_MODES: Tuple[str, ...] = ("ask", "bypass")
CLAUDE_BYPASS_FLAG = "--dangerously-skip-permissions"

AUTORUN_CHAIN_DIR = ".spec/autorun"
AUTOPLAN_CHAIN_DIR = ".spec/autoplan"
CHAIN_STATE_FILE = "chain.json"
CHAIN_SPAWNS_FILE = "spawns.jsonl"
CHAIN_LOCK_FILE = "chain.lock"
CHAIN_EVENTS_FILE = "events.jsonl"
# F16 step 3 (AUD-14/AUD-18): per-machine runtime state lives under
# ``.git/spec-chain/<chain>/`` — inside the Git directory, so untracked by
# construction and machine-local without any .gitignore entry. The tracked
# tree keeps only the summary (``.spec/<chain>/chain.json``, volatile-free),
# so a round's plan-sync commit no longer carries ttys, window ids, or pids.
CHAIN_RUNTIME_ROOT = Path(".git") / "spec-chain"
VOLATILE_STATE_FILE = "volatile.json"
# Keys describing one machine's spawn moment (the spawning tty, the window
# recycle plan). They live in volatile.json and in the audit rows; the
# tracked summary never carries them.
VOLATILE_STATE_KEYS: Tuple[str, ...] = ("prev_tty", "window_recycle")
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


def resolve_effective_cap(
    explicit: Optional[int],
    unbounded: bool,
    default: Optional[int],
    option: str,
    error: Callable[..., Exception] = ChainSpawnError,
) -> Optional[int]:
    """F1: resolve the effective chain cap from the spawn flags.

    Precedence: an explicit ``--{option}`` value wins; ``--unbounded``
    removes the cap (None); otherwise the restored default applies. Giving
    both flags at once is refused — the intent is ambiguous and the chain
    record must not carry a cap the caller did not actually choose.
    """
    if explicit is not None and unbounded:
        raise error(f"--unbounded and --{option} are mutually exclusive; pick one cap decision")
    if unbounded:
        return None
    return explicit if explicit is not None else default


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
    - ``cap is None`` → the chain is unbounded: the counter advances with no
      refusal ever (the no-flag default since the F17 adaptive caps were
      removed);
    - ``next == cap`` allows the last spawn; only ``next > cap`` refuses —
      a capped chain always gets its full cap;
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
    if cap is not None and next_index > cap:
        raise error(f"{label} cap reached: next {label} {next_index} exceeds --{option} {cap}")
    return next_index


def format_chain_counter(counter: object, cap: object) -> str:
    """Render one chain counter for the spawn text output.

    ``n/cap`` when the chain carries an explicit cap, ``n (no cap)`` when it
    is unbounded (``cap`` absent or null). The JSON payload keeps the raw
    ``max_rounds``/``max_passes`` value; this is the human-facing render.
    """
    if isinstance(cap, int) and not isinstance(cap, bool):
        return f"{counter}/{cap}"
    return f"{counter} (no cap)"


def utc_now() -> str:
    """One clock shape for every chain timestamp (ISO-8601Z)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- paths and state -------------------------------------------------------


def chain_dir(root: Path, chain: str) -> Path:
    """The tracked summary directory of one chain under ``root``."""
    if chain == "autorun":
        return root / AUTORUN_CHAIN_DIR
    if chain == "autoplan":
        return root / AUTOPLAN_CHAIN_DIR
    raise ChainSpawnError(f"unknown chain: {chain!r} (expected 'autorun' or 'autoplan')")


GIT_REPOSITORY_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")


def chain_git_probe_env() -> Dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in GIT_REPOSITORY_ENV}


def _linked_git_dir(root: Path) -> Optional[Path]:
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=chain_git_probe_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode != 0 or not probe.stdout.strip():
        return None
    candidate = Path(probe.stdout.strip())
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    return candidate


def _git_common_dir(root: Path) -> Path:
    # Linked worktrees keep objects/refs in the main repo's common dir, so
    # sandbox carve-outs must target it rather than the worktree's .git file.
    if (root / ".git").is_dir():
        return root / ".git"
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=chain_git_probe_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return root / ".git"
    if probe.returncode != 0 or not probe.stdout.strip():
        return root / ".git"
    return Path(probe.stdout.strip())


def chain_runtime_root(root: Path) -> Path:
    if (root / ".git").is_file():
        resolved = _linked_git_dir(root)
        if resolved is not None:
            return resolved / "spec-chain"
    return root / CHAIN_RUNTIME_ROOT


def chain_runtime_dir(root: Path, chain: str) -> Path:
    """The per-machine runtime directory of one chain (lock, logs, volatile).

    Lives under ``.git/spec-chain/`` so Git never tracks it and a worktree
    clone starts fresh; the summary stays tracked under ``.spec/<chain>/``.
    """
    if chain not in ("autorun", "autoplan"):
        raise ChainSpawnError(f"unknown chain: {chain!r} (expected 'autorun' or 'autoplan')")
    return chain_runtime_root(root) / chain


def split_volatile_state(state: Dict[str, object]) -> Tuple[Dict[str, object], Dict[str, object]]:
    """Split one state record into (tracked summary fields, volatile fields).

    The audit row keeps the full record; only the summary write strips the
    volatile keys, and volatile.json carries exactly those keys.
    """
    volatile = {key: state[key] for key in VOLATILE_STATE_KEYS if key in state}
    summary = {key: value for key, value in state.items() if key not in VOLATILE_STATE_KEYS}
    return summary, volatile


def write_chain_state(summary_dir: Path, runtime_dir: Path, state: Dict[str, object]) -> None:
    """Atomic split write: volatile.json first, then the tracked summary.

    Volatile first so a crash between the two writes leaves the summary as
    the previous commit point; the next spawn overwrites both anyway. The
    summary never carries the volatile keys, so it stays byte-stable across
    machine-side churn and safe for the round's plan-sync commit.
    """
    summary, volatile = split_volatile_state(state)
    atomic_write(runtime_dir / VOLATILE_STATE_FILE, json.dumps(volatile, indent=2, sort_keys=True) + "\n")
    atomic_write(summary_dir / CHAIN_STATE_FILE, json.dumps(summary, indent=2, sort_keys=True) + "\n")


def read_volatile_state(runtime_dir: Path) -> Dict[str, object]:
    """Tolerant volatile read: ``{}`` when missing or unreadable.

    The volatile file is machine-side render fuel (status text, window
    sweep), never a gate input — degrade to defaults instead of failing the
    read, and let the next spawn overwrite whatever is corrupt.
    """
    path = runtime_dir / VOLATILE_STATE_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: volatile state unreadable ({path}): {exc}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(f"warning: volatile state is not a JSON object: {path}", file=sys.stderr)
        return {}
    return data


def migrate_legacy_chain_state(root: Path, chain: str) -> Dict[str, object]:
    """One idempotent legacy→runtime migration pass for one chain (F16 step 3).

    Legacy layout (pre-split): ``chain.lock`` / ``spawns.jsonl`` /
    ``events.jsonl`` beside the summary under ``.spec/<chain>/``, and the
    summary itself carrying the volatile keys. New layout: those files live
    under ``.git/spec-chain/<chain>/`` and the summary is volatile-free.

    Every move is an atomic rename on the same filesystem — a reader or
    writer holding an open descriptor follows the inode, so even a racing
    pre-migration process stays mutually excluded through the flock. A
    failure (target unwritable, cross-device, whatever) leaves the legacy
    bytes exactly as found, is reported in the return value, and never
    raises; the next status/spawn entry re-runs the pass. Called before any
    state read in ``status`` and ``spawn``, so both layouts render
    identically and the tracked tree converges to summary-only.
    """
    summary_dir = chain_dir(root, chain)
    runtime_dir = chain_runtime_dir(root, chain)
    moved: List[str] = []
    skipped: List[Dict[str, str]] = []
    for name in (CHAIN_LOCK_FILE, CHAIN_SPAWNS_FILE, CHAIN_EVENTS_FILE):
        legacy = summary_dir / name
        if not legacy.is_file():
            continue
        target = runtime_dir / name
        if target.exists():
            # Both positions populated means a partial earlier migration met
            # fresh runtime writes; merging append-only logs is not worth the
            # corruption risk — keep the legacy bytes and report the skip.
            skipped.append({"file": name, "reason": "target already exists"})
            continue
        try:
            runtime_dir.mkdir(parents=True, exist_ok=True)
            os.replace(legacy, target)
            moved.append(name)
        except OSError as exc:
            skipped.append({"file": name, "reason": str(exc)})

    # Strip volatile keys the tracked summary may still carry (legacy writer
    # or a partial earlier strip): first migration writes them to
    # volatile.json, then rewrites the summary without them. An existing
    # volatile.json is authoritative and stays; the stale summary copies are
    # dropped either way. Atomic writes on both files keep every
    # intermediate state readable.
    volatile_stripped = False
    state_file = summary_dir / CHAIN_STATE_FILE
    if state_file.is_file():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            legacy_volatile = {key: data[key] for key in VOLATILE_STATE_KEYS if key in data}
            if legacy_volatile:
                volatile_path = runtime_dir / VOLATILE_STATE_FILE
                if not volatile_path.exists():
                    try:
                        atomic_write(volatile_path, json.dumps(legacy_volatile, indent=2, sort_keys=True) + "\n")
                    except OSError as exc:
                        skipped.append({"file": VOLATILE_STATE_FILE, "reason": str(exc)})
                        legacy_volatile = {}
                if legacy_volatile:
                    for key in VOLATILE_STATE_KEYS:
                        data.pop(key, None)
                    try:
                        atomic_write(state_file, json.dumps(data, indent=2, sort_keys=True) + "\n")
                        volatile_stripped = True
                    except OSError as exc:
                        skipped.append({"file": CHAIN_STATE_FILE, "reason": str(exc)})

    report: Dict[str, object] = {
        "chain": chain,
        "moved": moved,
        "skipped": skipped,
        "volatile_stripped": volatile_stripped,
    }
    if moved or volatile_stripped:
        append_event(
            runtime_dir / CHAIN_EVENTS_FILE,
            {"kind": "state_migrated", "at": utc_now(), "chain": chain, "detail": report},
        )
    return report


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


def closed_chain_marker(state: Dict[str, object]) -> bool:
    """Recognize only a well-shaped close receipt, never a bare reset flag."""
    if state.get("lifecycle") != "closed":
        return False
    chain = state.get("chain")
    digest = state.get("previous_digest")
    if (
        chain not in ("autoplan", "autorun")
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or state.get("archive") != f".spec/{chain}/history/{digest}.json"
        or not isinstance(state.get("closed_at"), str)
        or not state["closed_at"]
    ):
        raise ChainSpawnError("invalid closed chain marker; inspect its archive/audit before restarting")
    return True


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
    try:
        return None if closed_chain_marker(data) else data
    except ChainSpawnError as exc:
        raise error(str(exc)) from exc


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


def read_audit_tail(runtime_dir: Path) -> Optional[Dict[str, object]]:
    """The last complete ``spawns.jsonl`` record, or ``None`` (partial lines skipped)."""
    record, _ = last_audit_record(runtime_dir / CHAIN_SPAWNS_FILE)
    return record


def read_state(summary_dir: Path, runtime_dir: Path, chain: str = "unknown") -> Tuple[Optional[Dict[str, object]], str]:
    """Tolerant chain-state read returning ``(state, read_status)``.

    Reads the tracked summary from ``summary_dir``; the recovery path (and
    every event append) works against ``runtime_dir``. Statuses: ``ok``
    (parsed), ``missing`` (no file — a fresh chain, even when
    audit lines exist), ``recovered`` (corrupt file rebuilt from the last
    parseable ``spawns.jsonl`` line; the rebuild is written back atomically
    with ``recovered_from`` plus a ``recovery`` event), ``corrupt`` (file
    present but unreadable and the audit cannot rebuild it — manual
    inspection; the file is left exactly as found). The rebuilt state is
    split: volatile fields land in volatile.json, the summary stays
    volatile-free.
    """
    state_file = summary_dir / CHAIN_STATE_FILE
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
        try:
            if closed_chain_marker(data):
                return None, READ_MISSING
            return _normalize_state(data), READ_OK
        except ChainSpawnError:
            pass  # A malformed close receipt is corrupt, never a fresh chain.

    record, line_number = last_audit_record(runtime_dir / CHAIN_SPAWNS_FILE)
    if record is not None and record.get("event") == "current_transition":
        return None, READ_CORRUPT
    if record is None:
        return None, READ_CORRUPT
    try:
        closed_chain_marker(record)
    except ChainSpawnError:
        return None, READ_CORRUPT

    # Rebuild from the audit tail: atomic write-back plus the recovery audit.
    # The audit row converts to state form first: F14's per-spawn injection
    # level is a spawns-row-only fact and never enters chain.json (the lock
    # rides along; old rows predate both keys and stay legacy-shaped). The
    # volatile fields split out to volatile.json — the rebuild is a new-layout
    # write, so the summary comes out volatile-free even from a legacy row.
    recovered = _normalize_state(record)
    recovered.pop("model_injection", None)
    recovered["recovered_from"] = f"{CHAIN_SPAWNS_FILE} line {line_number}"
    write_chain_state(summary_dir, runtime_dir, recovered)
    append_recovery_event(
        runtime_dir,
        chain,
        "recovered_state",
        {"source": f"{CHAIN_SPAWNS_FILE} line {line_number}", "state_file": CHAIN_STATE_FILE},
    )
    if closed_chain_marker(recovered):
        return None, READ_MISSING
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
    record_holder: bool = True,
) -> Iterator[Any]:
    """Hold the chain's flock for the wrapped block, recording the holder.

    flock ``LOCK_EX | LOCK_NB`` is the mutex (parallel chains are refused,
    never queued); one advisory holder line is appended per acquisition. The
    caller supplies its own error factory so the refusal carries the
    script's class name and wording into the audit stream.
    """
    if fcntl is None:
        raise error(WINDOWS_SESSION_GUIDANCE)
    chain_dir.mkdir(parents=True, exist_ok=True)
    lock_path = chain_dir / CHAIN_LOCK_FILE
    if busy_message is None:
        busy_message = f"another {chain} chain holds {CHAIN_LOCK_FILE}; parallel chains are refused"
    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise error(busy_message) from exc
        if record_holder:
            write_lock_holder(lock_handle, chain)
        try:
            yield lock_handle
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def pid_alive(pid: int) -> Optional[bool]:
    """Whether ``pid`` names a live process; ``None`` when unverifiable.

    POSIX probes with ``os.kill(pid, 0)`` — no external ``ps``: minimal
    images (the self-hosted release runner's busybox ``ps`` rejects
    ``-p <pid>``) must not degrade a live lock holder to "stale".
    Non-POSIX platforms keep the ``ps`` probe as the unverifiable fallback.
    Zombies report alive under both probes; reaped pids report dead.
    """
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None
        return True
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
    if fcntl is None:
        return {"status": "unknown", "reason": WINDOWS_SESSION_GUIDANCE}
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
    host: str,
    root: Path,
    prompt: str,
    model_identity: Optional[Dict[str, str]] = None,
    permission_mode: str = "ask",
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

    F1 (AUD-01): ``permission_mode`` carries the *effective* mode — the
    spawn script resolves ``inherit`` by probing the parent session before
    it renders. ``ask`` (the default) renders no escalation flag on any
    host; ``bypass`` renders the host's escalation flag — today only claude
    has one (``--dangerously-skip-permissions``); pi and codex accept the
    mode for the audit record with no argv change. The pre-F1 argv always
    escalated claude workers; the restored default deliberately does not.
    """
    if permission_mode not in WORKER_PERMISSION_MODES:
        raise ChainSpawnError(
            f"unknown worker permission mode: {permission_mode!r} (expected one of {WORKER_PERMISSION_MODES})"
        )
    if host == "codex":
        # codex >=0.160 seatbelt denies .git writes under workspace-write, so
        # `git add`/`git commit` die with EPERM at the round's commit stage
        # (verified 2026-10-07 against codex-cli 0.160.1). Carve the repo's
        # .git back into the writable roots; everything else stays sandboxed.
        git_root = json.dumps(str(_git_common_dir(root)))
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
        argv = ["claude"]
        if permission_mode == "bypass":
            argv.append(CLAUDE_BYPASS_FLAG)
        argv.append(prompt)
    else:
        raise ChainSpawnError(f"unknown worker host: {host}")
    if model_identity:
        model_argv = build_model_flag_argv(host, model_identity)
        if model_argv:
            argv[1:1] = model_argv
    return argv


# --- F1 feature-set fingerprint (AUD-01) ----------------------------------------
#
# A chain must not spin on a plan that grew behind its back: every spawn
# freezes an ordered hash of the planning documents' feature checkboxes and
# the next spawn refuses when the fingerprint moved. The hash covers the
# feature *texts* in document order — checking a box off is legitimate
# progress and leaves the fingerprint unchanged; adding, removing, or
# rewording a feature changes it and refuses the spawn through the existing
# refusal path (no state written).

FEATURE_CHECKBOX_LINE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s*(\S.*)$")


def feature_lines(text: str) -> List[str]:
    """Feature checkbox item texts in document order (state-independent)."""
    items: List[str] = []
    for line in text.splitlines():
        match = FEATURE_CHECKBOX_LINE.match(line)
        if match:
            items.append(match.group(2).strip())
    return items


def feature_fingerprint(docs: List[Path], root: Optional[Path] = None) -> str:
    """Ordered sha256 over ``(doc path, feature text)`` pairs for ``docs``.

    ``root`` relativizes each doc path when it stays under the root, so the
    fingerprint survives a checkout at another absolute location. Doc order
    is the caller's deterministic discovery order (sorted); the hash mixes
    each path with a NUL separator so no concatenation ambiguity exists.
    Unreadable docs contribute their path with no items — a planning doc a
    chain depends on must not silently vanish from the fingerprint.
    """
    digest = hashlib.sha256()
    for doc in docs:
        rel = str(doc.relative_to(root)) if root is not None and under_root(root, doc) else str(doc)
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for item in feature_lines(text):
            digest.update(rel.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(item.encode("utf-8"))
            digest.update(b"\x00")
    return digest.hexdigest()


# --- portable session backend ----------------------------------------------

WINDOWS_SESSION_GUIDANCE = (
    "Native Windows session spawning is not supported. Use WSL2: run Python, "
    "the worker CLI and Spec Harness inside the same WSL distribution, install tmux "
    "(Ubuntu/Debian: sudo apt-get install tmux), then rerun the command there. "
    "PowerShell / cmd / Git Bash are not WSL2."
)
TMUX_TIMEOUT_SECONDS = 10
TMUX_START_GRACE_SECONDS = 0.5
TMUX_STOP_GRACE_SECONDS = 1.0
TMUX_STOP_POLL_SECONDS = 0.05


def resolve_terminal_backend(requested: str = "auto") -> str:
    """Choose a real interactive terminal; never fall back to a headless worker."""
    system = platform.system()
    if system == "Windows" or system.startswith(("MSYS", "MINGW", "CYGWIN")):
        raise ChainSpawnError(WINDOWS_SESSION_GUIDANCE)
    if requested not in ("auto", "terminal", "tmux"):
        raise ChainSpawnError(f"unknown terminal backend: {requested!r}")
    if requested == "terminal":
        if system != "Darwin":
            raise ChainSpawnError("the terminal backend requires macOS Terminal.app; use --terminal tmux")
        return "terminal"
    if requested == "tmux" or system == "Linux":
        return "tmux"
    if system == "Darwin":
        return "terminal"
    raise ChainSpawnError(f"unsupported session platform: {system}; use Linux/WSL2 with tmux or macOS")


def build_tmux_command(
    root: Path, worker_command: List[str], chain: str, custom_command: Optional[str] = None
) -> Tuple[List[str], str, str]:
    """Build a private tmux server/session with an isolated, inherited environment.

    One socket per spawn avoids stale PATH/auth environment from a pre-existing
    tmux server, user hooks/configuration, and accidental targeting of user panes.
    A multi-argument shell command (tmux >=3.2) explicitly uses POSIX sh, not the
    user's possibly non-POSIX login shell. No command is interpreted twice.
    """
    if chain not in ("autoplan", "autorun"):
        raise ChainSpawnError(f"unknown chain: {chain!r}")
    name = f"spec-{chain}-{uuid.uuid4().hex}"
    command = custom_command if custom_command is not None else "exec " + shlex.join(worker_command)
    argv = [
        "tmux",
        "-L",
        name,
        "-f",
        os.devnull,
        "new-session",
        "-d",
        "-s",
        name,
        "-c",
        str(root),
        "-x",
        "120",
        "-y",
        "40",
        "/bin/sh",
        "-c",
        command,
    ]
    return argv, command, name


def _tmux_run(argv: List[str]):
    return subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=TMUX_TIMEOUT_SECONDS,
    )


def open_tmux_session(argv: List[str], name: str) -> None:
    """Start a detached but attachable TUI; refuse an immediately exited worker."""
    if shutil.which("tmux") is None:
        raise ChainSpawnError(
            "tmux is required for Linux/WSL2 interactive sessions; install tmux >=3.2 "
            "(Ubuntu/Debian: sudo apt-get install tmux; Fedora: sudo dnf install tmux) "
            "and rerun. No graphical desktop is required."
        )
    if shutil.which("ps") is None:
        raise ChainSpawnError("ps is required to verify tmux rollback; install procps and rerun")
    try:
        completed = _tmux_run(argv)
        if completed.returncode:
            raise ChainSpawnError(f"tmux failed to start the session: {completed.stderr.strip()}")
        time.sleep(TMUX_START_GRACE_SECONDS)
        probe = _tmux_run(
            [
                "tmux",
                "-L",
                name,
                "list-panes",
                "-t",
                "=" + name,
                "-F",
                "#{pane_dead}|#{pane_pid}",
            ]
        )
        fields = probe.stdout.strip().split("|")
        if probe.returncode or len(fields) != 2 or fields[0] != "0" or not fields[1].isdigit():
            raise ChainSpawnError("tmux worker exited or its live pane could not be confirmed; refusing the handoff")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ChainSpawnError(f"tmux session launch failed: {exc}") from exc


def _tmux_group_running(group: int) -> bool:
    """Verify live members of the captured pane group, ignoring exited zombies.

    killpg(..., 0) also sees zombies, so it cannot prove that a worker is still
    executing. Session membership is rechecked before each escalation to avoid
    signaling a process group that no longer belongs to the captured pane.
    """
    probe = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,stat="],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=PS_TIMEOUT_SECONDS,
    )
    if probe.returncode or not probe.stdout.strip():
        raise ChainSpawnError("cannot verify tmux process-group exit: ps failed or returned no processes")
    running = False
    for line in probe.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            raise ChainSpawnError("cannot verify tmux process-group exit: malformed ps output")
        if int(fields[1]) != group or fields[2].startswith(("Z", "X")):
            continue
        try:
            if os.getsid(int(fields[0])) != group:
                raise ChainSpawnError("tmux process-group identity changed; refusing to signal")
        except ProcessLookupError:
            continue  # Exited between the process table snapshot and getsid.
        running = True
    return running


def _stop_tmux_group(pid: int) -> None:
    """Stop a verified pane's dedicated group before destroying its identity anchor."""
    if pid <= 1 or pid == os.getpgrp() or os.getpgid(pid) != pid or os.getsid(pid) != pid:
        raise ChainSpawnError("tmux pane is not an isolated process group; refusing to signal")
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not _tmux_group_running(pid):
            return
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + TMUX_STOP_GRACE_SECONDS
        while True:
            if not _tmux_group_running(pid):
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(TMUX_STOP_POLL_SECONDS)
    raise ChainSpawnError("tmux worker group remains running after SIGKILL; cleanup not confirmed")


def _tmux_session_absent(result) -> bool:
    return bool(result.returncode) and any(
        phrase in result.stderr.lower() for phrase in ("no server running", "no such file", "can't find session")
    )


def close_tmux_session(name: str) -> Dict[str, str]:
    """Stop this private pane's worker group, then close only its managed session.

    Destroying a pane merely sends SIGHUP, which workers can ignore. Capture the
    pane owner while the private server can still identify it, verify POSIX
    session/group isolation, and bound TERM/KILL plus actual exit observation.
    Unknown identity or unavailable observation must never report success.
    """
    if not re.fullmatch(r"spec-(autoplan|autorun)-[0-9a-f]{32}", name):
        return {"status": "skipped", "reason": "not a managed tmux session name"}
    try:
        pane = _tmux_run(["tmux", "-L", name, "list-panes", "-t", "=" + name, "-F", "#{pane_pid}"])
        if _tmux_session_absent(pane):
            return {"status": "gone"}
        owner = pane.stdout.strip()
        if pane.returncode or not owner.isdigit():
            raise ChainSpawnError("cannot identify the private tmux pane owner; cleanup not confirmed")
        _stop_tmux_group(int(owner))
        result = _tmux_run(["tmux", "-L", name, "kill-session", "-t", "=" + name])
        # Worker exit normally removes the last session/server automatically.
        if result.returncode == 0 or _tmux_session_absent(result):
            return {"status": "closed"}
        return {"status": "failed", "reason": result.stderr.strip()}
    except (ChainSpawnError, OSError, subprocess.SubprocessError) as exc:
        return {"status": "failed", "reason": str(exc)}


def spawn_tmux_payload(
    root: Path,
    chain: str,
    worker_command: List[str],
    record: Dict[str, object],
    *,
    dry_run: bool,
    custom_command: Optional[str] = None,
) -> Dict[str, object]:
    """Shared transaction under the caller's lock: open -> check -> state -> audit.

    State-write success confirms the handoff. Any earlier failure rolls back
    only the new session; audit failure after commit must never kill its worker.
    The old terminal is never recycled on this backend.
    """
    argv, command, name = build_tmux_command(root, worker_command, chain, custom_command)
    attach = shlex.join(["tmux", "-L", name, "attach-session", "-t", "=" + name])
    record = {
        **record,
        "terminal_backend": "tmux",
        "tmux_session": name,
        "shell_command": command,
        "prev_tty": None,
        "attach_command": attach,
        "window_recycle": {"status": "skipped", "reason": "tmux backend preserves the previous session"},
    }
    if dry_run:
        return {"dry_run": True, "launch_argv": argv, **record}
    summary_dir = chain_dir(root, chain)
    runtime_dir = chain_runtime_dir(root, chain)
    try:
        open_tmux_session(argv, name)
        state = dict(record)
        state.pop("model_injection")
        state["updated_at"] = state.pop("spawned_at")
        write_chain_state(summary_dir, runtime_dir, state)
    except BaseException as exc:
        cleanup = close_tmux_session(name)
        append_event(
            runtime_dir / CHAIN_EVENTS_FILE,
            {
                "kind": "refusal_close",
                "chain": chain,
                "at": utc_now(),
                "tmux_session": name,
                "cleanup": cleanup,
            },
        )
        if cleanup["status"] == "failed":
            print(f"warning: tmux rollback failed for {name}: {cleanup.get('reason')}", file=sys.stderr)
        if isinstance(exc, (OSError, subprocess.SubprocessError)):
            raise ChainSpawnError(f"tmux handoff failed: {exc}") from exc
        raise
    try:
        append_audit(runtime_dir / CHAIN_SPAWNS_FILE, record)
    except OSError as exc:
        raise ChainSpawnError(
            f"tmux handoff committed but spawn audit failed: {exc}; inspect status before retrying"
        ) from exc
    return {"dry_run": False, "terminal": name, **record}


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
        if tty and tty not in ("?", "??", "-"):
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


def worker_pids_on_tty(worker: str, tty: str) -> List[int]:
    """Pids of the ``worker`` processes running on the given Terminal tty.

    Same token matching as :func:`worker_running_on_tty` (which stays
    untouched — its tests pin the ``tty=,command=`` call shape), but with the
    pid column: the session-recycle escalation targets exactly these
    processes by pid, never by tty (the close helper and the spawning
    python both hang off the same tty and must survive the kill).
    """
    try:
        completed = subprocess.run(
            ["ps", "-axo", "tty=,pid=,command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    tty_name = tty.rsplit("/", 1)[-1]
    pids: List[int] = []
    for line in completed.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3 or fields[0].strip() != tty_name:
            continue
        tokens = fields[2].split()
        if not tokens:
            continue
        if os.path.basename(tokens[0].rstrip("/")) == worker or (
            len(tokens) > 1 and "/" in tokens[1] and os.path.basename(tokens[1].rstrip("/")) == worker
        ):
            try:
                pid = int(fields[1])
            except ValueError:
                continue
            if pid > 0 and pid not in pids:
                pids.append(pid)
    return pids


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
        "\tset matchRef to missing value",
        "\tset matchTabs to 0",
        "\tset matchBusy to false",
        "\trepeat with w in windows",
        "\t\trepeat with t in tabs of w",
        "\t\t\tif tty of t is {} then".format(tty_expression),
        "\t\t\t\tset match to (id of w as string)",
        "\t\t\t\tset matchRef to w",
        "\t\t\t\tset matchTabs to (count of tabs of w)",
        "\t\t\t\tset matchBusy to (busy of t)",
        "\t\t\t\texit repeat",
        "\t\t\tend if",
        "\t\tend repeat",
        '\t\tif match is not "" then exit repeat',
        "\tend repeat",
    ]


def build_terminal_command(
    root: Path,
    worker_command: List[str],
    custom_command: Optional[str] = None,
) -> Tuple[List[str], str]:
    """Build the osascript argv opening a Terminal window at ``root``.

    Returns ``(osascript argv, shell command)``. The shell command is
    ``cd <root> && <worker argv>`` — or ``cd <root> && <custom command>``
    when a raw ``--command`` override is given. The AppleScript runs the
    command in a new tab and replies ``"<window id> <tab tty>"`` on
    stdout (the window is found by matching the spawned tab's tty, so no
    front-window race), so the spawn can verify the next round and
    recycle windows. F17 geometry inheritance (the spawned window taking
    the parent's place) is applied from Python after this reply — see
    :func:`read_window_geometry` / :func:`apply_window_geometry` (an
    in-script ``set`` races Terminal's own cascade placement, and
    Terminal's ``bounds`` get/set pair is Y-asymmetric; setting by window
    id from a second call sticks).
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


def read_window_geometry(tty: str) -> Optional[Tuple[int, int, int, int]]:
    """The ``(x, y, width, height)`` of the Terminal window hosting ``tty``.

    ``None`` when no Terminal window hosts the tty (headless host,
    non-Terminal parent) — the caller then skips geometry inheritance.
    Uses the symmetric ``position``/``size`` pair; Terminal's ``bounds``
    get/set pair drifts a constant on Y (verified on-device).
    """
    lines = [
        'tell application "Terminal"',
        *_window_lookup_lines('"{}"'.format(escape_applescript(tty))),
        '\tif match is not "" then',
        "\t\tset {x, y} to position of matchRef",
        "\t\tset {w, h} to size of matchRef",
        '\t\treturn (x as string) & "," & (y as string) & "," & (w as string) & "," & (h as string)',
        "\tend if",
        '\treturn ""',
        "end tell",
    ]
    try:
        completed = subprocess.run(
            ["osascript", "-e", "\n".join(lines)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=OSASCRIPT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    parts = completed.stdout.strip().split(",")
    if len(parts) != 4:
        return None
    try:
        values = tuple(int(part.strip()) for part in parts)
    except ValueError:
        return None
    return values  # type: ignore[return-value]


def apply_window_geometry(window_id: int, geometry: Tuple[int, int, int, int]) -> None:
    """Place ``window_id`` at ``geometry`` — best effort, errors swallowed.

    Terminal applies its own cascade placement asynchronously after
    ``do script``, so a single early set can be overwritten; the set runs
    twice with a short gap (verified on-device: setting by window id from
    a second call sticks).
    """
    if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0:
        return
    x, y, width, height = geometry
    script = (
        'tell application "Terminal"\n'
        "\tset position of window id {id} to {{{x}, {y}}}\n"
        "\tset size of window id {id} to {{{w}, {h}}}\n"
        "end tell"
    ).format(id=window_id, x=x, y=y, w=width, h=height)
    for _attempt in (0, 1):
        try:
            subprocess.run(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=OSASCRIPT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            return
        time.sleep(0.2)


# --- F18: dead chain-window sweep ---------------------------------------------
#
# Chain membership is proven by recorded ttys, not by screen scraping: a
# do-script tab returns to an interactive shell prompt once its command
# finishes (the ``[Process completed]`` marker only appears when the login
# shell itself exits), so a dead chain window is visually indistinguishable
# from the user's own idle prompt. Every window the chains spawned is,
# however, recorded as ``prev_tty`` in a spawns row — the sweep closes
# single-tab idle windows whose tty is in that recorded set (minus the
# session running the sweep). The one window never recorded is the
# chain's last spawn (its tty lands in the next row only); that is the
# window the user most recently touched, so leaving it is the safe side.


def recorded_chain_ttys(chain_dirs: List[Path]) -> List[str]:
    """Every tty the chains' spawns rows recorded (``prev_tty`` values).

    Each row's ``prev_tty`` is the tty of the window that spawned — i.e.,
    every chain window except the newest spawn. Tolerant: missing or
    unparseable rows contribute nothing.
    """
    ttys: List[str] = []
    for chain_dir in chain_dirs:
        spawns_file = chain_dir / CHAIN_SPAWNS_FILE
        if not spawns_file.is_file():
            continue
        try:
            lines = spawns_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                tty = row.get("prev_tty")
                if isinstance(tty, str) and tty.startswith("/dev/ttys") and tty not in ttys:
                    ttys.append(tty)
    return ttys


def find_dead_chain_windows(recorded_ttys: List[str], *, exclude_tty: Optional[str] = None) -> List[Dict[str, object]]:
    """Idle single-tab Terminal windows whose tty the chains recorded.

    Qualification is conjunctive: exactly one tab (Terminal cannot close one
    tab of a multi-tab window), no tab busy (no live process — the crashed
    round's session ended), and the tab's tty is in ``recorded_ttys``
    (chain-owned by record, never the user's own window). ``exclude_tty``
    protects the session running the sweep from closing its own window.
    One bounded osascript enumerates ``id|tabCount|tty|anyBusy`` lines; any
    failure returns ``[]`` (fail-open: report nothing rather than guess).
    """
    if not recorded_ttys:
        return []
    script = "\n".join(
        [
            'tell application "Terminal"',
            '\tset out to ""',
            "\trepeat with w in windows",
            "\t\tset tid to id of w as string",
            "\t\tset tc to count of tabs of w",
            "\t\tif tc is 0 then",
            '\t\t\tset out to out & tid & "|0||true" & linefeed',
            "\t\telse",
            "\t\t\tset anyBusy to false",
            "\t\t\tset wTty to tty of (tab 1 of w)",
            "\t\t\trepeat with t in tabs of w",
            "\t\t\t\tif (busy of t) then set anyBusy to true",
            "\t\t\tend repeat",
            '\t\t\tset out to out & tid & "|" & (tc as string) & "|" & wTty & "|" & (anyBusy as string) & linefeed',
            "\t\tend if",
            "\tend repeat",
            "\treturn out",
            "end tell",
        ]
    )
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=OSASCRIPT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    recorded_set = set(recorded_ttys)
    dead: List[Dict[str, object]] = []
    for line in completed.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 4:
            continue
        window_id_s, tab_count_s, tty_s, busy_s = parts
        try:
            window_id = int(window_id_s)
            tabs = int(tab_count_s)
        except ValueError:
            continue
        if window_id <= 0 or tabs != 1 or busy_s != "false":
            continue
        if tty_s not in recorded_set or tty_s == exclude_tty:
            continue
        dead.append({"window_id": window_id, "tty": tty_s})
    return dead


def close_terminal_windows(window_ids: List[int]) -> Dict[str, List[int]]:
    """Close Terminal windows by id, tolerating per-window failures.

    An idle single-tab window closes silently (no confirmation sheet — the
    sweep only passes ids :func:`find_dead_chain_windows` qualified). A
    window that vanished or refused lands in ``skipped``; the sweep never
    aborts on one window.
    """
    closed: List[int] = []
    skipped: List[int] = []
    for window_id in window_ids:
        # Guarded close, never a bare one: a window that turned busy after
        # the enumeration (TOCTOU) must never raise Terminal's
        # cancel/terminate sheet — the zero-wait guard closes only a window
        # that still exists, holds one tab, and is free; every other
        # outcome is a conservative skip (F19).
        try:
            result = subprocess.run(
                ["osascript", "-e", build_close_applescript(window_id, 0)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=OSASCRIPT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            skipped.append(window_id)
            continue
        if result.returncode == 0 and result.stdout.strip() == "closed":
            closed.append(window_id)
        else:
            skipped.append(window_id)
    return {"closed": closed, "skipped": skipped}


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


# --- F17 spawn robustness ----------------------------------------------------


def run_terminal_open(osascript_argv: List[str], *, error: Callable[[str], Exception], label: str):
    """Run the window-open osascript with one bounded retry.

    A transiently busy Terminal (mid-animation, mid-close) must not kill the
    round: one retry after a short backoff, then the existing refusal. The
    per-chain message texts are preserved verbatim through ``label`` ("the
    Terminal window" / "the pass Terminal window").
    """
    last_detail = ""
    for attempt in range(TERMINAL_OPEN_ATTEMPTS):
        try:
            completed = subprocess.run(
                osascript_argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=OSASCRIPT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            last_detail = "osascript did not answer within {}s opening {}".format(OSASCRIPT_TIMEOUT_SECONDS, label)
        else:
            if completed.returncode == 0:
                return completed
            last_detail = completed.stderr.strip() or completed.stdout.strip()
        if attempt + 1 < TERMINAL_OPEN_ATTEMPTS:
            time.sleep(TERMINAL_OPEN_RETRY_BACKOFF_SECONDS)
    raise error(f"osascript failed to open {label}: {last_detail}")


def verify_worker_start(
    worker_name: str,
    new_window_id: Optional[int],
    new_tty: Optional[str],
    *,
    timeout_seconds: Optional[int] = None,
    poll_seconds: float = WORKER_START_POLL_SECONDS,
    worker_check: Optional[Callable[[str, str], bool]] = None,
) -> Dict[str, object]:
    """F17: confirm the next-round session actually started.

    Verdicts: ``running`` (worker observed on the new tty), ``late-start``
    (not observed within the bound but the new tab is busy — something is
    running, keep today's fail-open), ``dead-tab`` (not observed and the tab
    is idle — the worker died instantly; the caller closes the dead window
    and refuses without recording state). An empty ``worker_name`` (the
    ``--command`` bypass) is trusted without verification.
    """
    if worker_check is None:
        worker_check = worker_running_on_tty
    if timeout_seconds is None:
        timeout_seconds = WORKER_START_TIMEOUT_SECONDS
    if not worker_name:
        return {"status": "running", "reason": "raw command bypass trusted without worker verification"}
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if worker_check(worker_name, new_tty or ""):
            return {"status": "running"}
        time.sleep(poll_seconds)
    # Not observed: discriminate a dead tab (idle) from a late start (busy)
    lookup_argv = ["osascript", "-e", build_window_lookup_applescript(new_tty or "")]
    try:
        lookup = subprocess.run(
            lookup_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=OSASCRIPT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"status": "late-start", "reason": "window lookup did not answer"}
    _window_id, _tab_count, busy = parse_window_lookup(lookup.stdout) if lookup.returncode == 0 else (None, 0, False)
    if busy:
        return {
            "status": "late-start",
            "reason": "worker {} not observed on {} within {}s but the tab is busy".format(
                worker_name, new_tty, timeout_seconds
            ),
        }
    return {
        "status": "dead-tab",
        "reason": "worker {} never started on {} within {}s (tab idle)".format(worker_name, new_tty, timeout_seconds),
    }


def close_spawned_window_quietly(window_id: Optional[int]) -> None:
    """Best-effort cleanup of a spawned window whose session died.

    The zero-wait close AppleScript closes an idle single-tab window
    silently and reports busy for anything still running — fire-and-forget,
    outcome ignored (the accompanying refusal event is the audit record)."""
    if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0:
        return
    try:
        subprocess.run(
            ["osascript", "-e", build_close_applescript(window_id, 0)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=OSASCRIPT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return


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


_WORKER_NAME_TOKEN = re.compile(r"^[A-Za-z0-9._+-]+$")


def _session_kill_lines(session_pids: List[int], signal_name: str, worker_name: str) -> str:
    """Best-effort sh lines signaling the lingering session's process groups.

    Targets the pgid of each recorded worker pid — never the tty (the close
    helper and the spawning python both hang off it and must survive). Each
    pid is identity-checked first (F19): the pid was captured ~13s before
    the signal fires, and a reused pid must never pass — the ps command
    line has to name the worker with a token boundary (``*codex *`` or
    ``*/codex`` — ``pip3``-style substrings do not match). A worker name
    that is not a plain token (never the case for the three hosts; the
    bypass carries no name) skips the escalation entirely — conservative.
    """
    if not session_pids or not worker_name or not _WORKER_NAME_TOKEN.match(worker_name):
        return ""
    pid_list = " ".join(str(pid) for pid in session_pids)
    return (
        "for pid in {pids}; do "
        'cmd=$(ps -o command= -p "$pid" 2>/dev/null); '
        # the space-bearing alternative must stay quoted: macOS /bin/sh is
        # bash 3.2, whose case parser rejects an unquoted ``*w *`` pattern
        # outright (verified on-device — the whole helper would die at parse)
        'case "$cmd" in *"{worker} "*|*/{worker}) '
        'pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d " "); '
        '[ -n "$pgid" ] && kill -{signal} -"$pgid" 2>/dev/null || true ;; '
        "esac; "
        "done\n"
    ).format(pids=pid_list, signal=signal_name, worker=worker_name)


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
    session_pids: Optional[List[int]] = None,
    worker_name: str = "",
    event_kind: str = "recycle_close",
    error: Callable[..., Exception] = ChainSpawnError,
) -> List[str]:
    """Build the detached argv recycling a Terminal window after a handoff.

    F17 three-phase escalation (the interactive worker TUI finishes its
    turn and then idles forever, so waiting for a natural exit alone left
    every window open — 10 busy-timeout vs 1 closed in the live events
    stream): (1) natural-exit grace; (2) SIGTERM to the recorded session
    process groups; (3) SIGKILL, then the remaining wait budget. Each phase
    runs the bounded close AppleScript — it closes only a window that still
    exists, holds exactly one tab, and whose session has exited, so the
    cancel/terminate sheet never appears. The helper appends one JSON line
    of ``event_kind`` (``recycle_close`` for the previous window after a
    confirmed handoff, ``refusal_close`` for the just-opened window of a
    refused spawn — 2026-10-09) to ``events_path`` — result from the fixed
    vocabulary (``closed`` / ``multi-tab`` / ``window-gone`` / ``busy-timeout``;
    anything else records ``osascript-error``) plus the chain context. The
    append is fire-and-forget: a failure to write never blocks or re-raises.
    """
    if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0:
        raise error(f"window id must be a positive integer: {window_id!r}")
    if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, int) or delay_seconds < 0:
        raise error(f"close delay must be >= 0: {delay_seconds!r}")
    if isinstance(close_wait_seconds, bool) or not isinstance(close_wait_seconds, int) or close_wait_seconds < 0:
        raise error(f"close wait must be >= 0: {close_wait_seconds!r}")
    if chain not in ("autorun", "autoplan"):
        raise error(f"chain must be 'autorun' or 'autoplan': {chain!r}")
    if event_kind not in ("recycle_close", "refusal_close"):
        raise error(f"event kind must be 'recycle_close' or 'refusal_close': {event_kind!r}")
    if (round_index is None) == (pass_index is None):
        raise error("exactly one of round_index / pass_index is required")
    context_name = "round" if round_index is not None else "pass"
    context_value = round_index if round_index is not None else pass_index
    if isinstance(context_value, bool) or not isinstance(context_value, int) or context_value < 1:
        raise error(f"{context_name} index must be a positive integer: {context_value!r}")
    if not isinstance(prev_tty, str) or not prev_tty:
        raise error(f"prev_tty is required: {prev_tty!r}")
    clean_pids: List[int] = []
    for pid in session_pids or []:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise error(f"session pids must be positive integers: {pid!r}")
        if pid not in clean_pids:
            clean_pids.append(pid)
    clean_pids.sort()
    events_path = Path(events_path)
    if not events_path.is_absolute():
        raise error(f"events path must be absolute: {events_path}")
    grace_wait = min(CLOSE_NATURAL_GRACE_SECONDS, close_wait_seconds)
    term_wait = min(CLOSE_TERM_WAIT_SECONDS, max(0, close_wait_seconds - grace_wait))
    final_wait = max(0, close_wait_seconds - grace_wait - term_wait)
    literal = json.dumps(
        {
            "kind": event_kind,
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
        # phase 1: natural-exit grace (a bypass `sleep N` command closes here)
        'out=$(osascript -e {grace_as} 2>/dev/null) || out=""\n'
        'case "$out" in closed|multi-tab|window-gone) result="$out" ;; *)\n'
        # phase 2: the session lingers past the grace — SIGTERM its groups
        "{term_kill}"
        'out=$(osascript -e {term_as} 2>/dev/null) || out=""\n'
        'case "$out" in closed|multi-tab|window-gone) result="$out" ;; *)\n'
        # phase 3: SIGKILL, then the remaining wait budget
        "{final_kill}"
        'out=$(osascript -e {final_as} 2>/dev/null) || out=""\n'
        'case "$out" in closed|multi-tab|window-gone|busy-timeout) result="$out" ;; *) '
        'result="osascript-error" ;; esac\n'
        " ;; esac\n"
        " ;; esac\n"
        "end=$(date +%s)\n"
        "now=$(date -u +%Y-%m-%dT%H:%M:%SZ)\n"
        "waited_ms=$(( (end - start) * 1000 ))\n"
        # The template carries its own trailing newline: without it two
        # consecutive append records glue into one unparseable line (found
        # 2026-10-07 in the live events stream — rounds 2/3 close records).
        'printf \'{template}\\n\' "$now" "$result" "$waited_ms" >> {events} 2>/dev/null'
    ).format(
        delay=delay_seconds,
        grace_as=shlex.quote(build_close_applescript(window_id, grace_wait, poll_seconds=poll_seconds)),
        term_kill=_session_kill_lines(clean_pids, "TERM", worker_name),
        term_as=shlex.quote(build_close_applescript(window_id, term_wait, poll_seconds=poll_seconds)),
        final_kill=_session_kill_lines(clean_pids, "KILL", worker_name),
        final_as=shlex.quote(build_close_applescript(window_id, final_wait, poll_seconds=poll_seconds)),
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
    session_pids: Optional[List[int]] = None,
    worker_name: str = "",
    event_kind: str = "recycle_close",
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
            session_pids=session_pids,
            worker_name=worker_name,
            event_kind=event_kind,
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def recover_front_worker_window(
    worker_name: str,
    *,
    worker_check: Optional[Callable[[str, str], bool]] = None,
) -> Tuple[Optional[int], Optional[str]]:
    """Best-effort identity for a spawned window whose open reply was unparseable.

    The just-opened window is Terminal's front window by construction, but a
    race (the user switching windows inside the spawn) could surface any
    window — so the recovery is only accepted when the front window's
    selected tab verifiably runs OUR worker (identity interlock via
    ``worker_check``). A user's window, or any other command, never matches
    and the recovery returns nothing; the caller then refuses without a
    close target (honest limitation, recorded as a skipped close).
    """
    if worker_check is None:
        worker_check = worker_running_on_tty
    if not worker_name:
        return None, None
    script = (
        'tell application "Terminal"\n'
        "\ttry\n"
        "\t\tset w to front window\n"
        '\t\treturn (id of w as text) & " " & (tty of selected tab of w)\n'
        "\ton error\n"
        '\t\treturn ""\n'
        "\tend try\n"
        "end tell"
    )
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=OSASCRIPT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    if completed.returncode != 0:
        return None, None
    parts = completed.stdout.strip().split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].startswith("/dev/"):
        return None, None
    window_id, tty = int(parts[0]), parts[1]
    if not worker_check(worker_name, tty):
        return None, None
    return window_id, tty


def close_refused_window(
    new_window_id: Optional[int],
    new_tty: Optional[str],
    worker_name: str,
    *,
    chain: str,
    events_path: Path,
    round_index: Optional[int] = None,
    pass_index: Optional[int] = None,
    pid_lookup: Optional[Callable[[str, str], List[int]]] = None,
    run_helper: Optional[Callable[[List[str]], None]] = None,
) -> Dict[str, object]:
    """Synchronously close the window a refused spawn just opened.

    交接未确认 = 不留进程 (2026-10-09): a refusal after the window opened
    leaves an alive worker running the injected next-round prompt while the
    round itself is never recorded — a duplicate executor on re-run. This
    helper closes that window before the refusal propagates: the F19
    escalated close argv (natural-exit grace, then SIGTERM, then SIGKILL to
    the identity-checked session process groups, then the guard-shaped
    window close) runs synchronously and bounded, with no scheduling delay,
    and appends one ``refusal_close`` event (fire-and-forget). A ``--command``
    bypass carries no worker name, so nothing is identity-killable — the
    close then only reaps a naturally-exited tab and otherwise times out
    fail-open, exactly like the recycle path for unnamed sessions. A close
    failure never masks the refusal it serves.
    """
    if pid_lookup is None:
        pid_lookup = worker_pids_on_tty
    if run_helper is None:

        def _run_helper(argv: List[str]) -> None:
            try:
                subprocess.run(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=REFUSAL_CLOSE_HELPER_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.SubprocessError):
                return

        run_helper = _run_helper
    if new_window_id is None or new_tty is None:
        return {"status": "skipped", "reason": "spawned window id or tty unavailable from osascript"}
    session_pids = pid_lookup(worker_name, new_tty) if worker_name else []
    try:
        argv = build_close_argv(
            new_window_id,
            0,
            REFUSAL_CLOSE_WAIT_SECONDS,
            chain=chain,
            round_index=round_index,
            pass_index=pass_index,
            prev_tty=new_tty,
            events_path=events_path,
            session_pids=session_pids,
            worker_name=worker_name,
            event_kind="refusal_close",
        )
    except ChainSpawnError:
        return {"status": "skipped", "reason": "close argv validation refused the parameters"}
    run_helper(argv)
    return {"status": "close-command-sent", "window_id": new_window_id, "session_pids": session_pids}


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
    pid_lookup: Optional[Callable[[str, str], List[int]]] = None,
    schedule_close: Optional[Callable[..., None]] = None,
) -> Dict[str, object]:
    """Recycle the previous round's window: confirm the new window is distinct,
    then schedule the escalated close for the previous one.

    The next-round worker verification moved to :func:`verify_worker_start`
    (F17): it is a spawn gate, not a recycle concern. Fail-open: any skip
    condition leaves the previous window open and returns the recorded
    reason. A one-tab previous window is scheduled for the detached close
    helper with the session pids recorded now (the lingering interactive
    session is SIGTERM/SIGKILL-escalated there — see ``build_close_argv``).
    Multi-tab windows stay untouched: Terminal cannot close one tab, and the
    escalation must not kill a session in a window it cannot close.

    ``pid_lookup`` / ``schedule_close`` inject the caller's patchable
    collaborators (the chain scripts bind them to their own module globals
    so their test seams keep working).
    """
    if pid_lookup is None:
        pid_lookup = worker_pids_on_tty
    if schedule_close is None:
        schedule_close = schedule_window_close
    if delay_seconds is None:
        delay_seconds = CLOSE_DELAY_SECONDS
    if close_wait_seconds is None:
        close_wait_seconds = CLOSE_WAIT_EXIT_SECONDS
    if prev_tty is None:
        return {"status": "skipped", "reason": "spawning session has no controlling Terminal"}
    if new_window_id is None or new_tty is None:
        return {"status": "skipped", "reason": "spawned window id or tty unavailable from osascript"}
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
    session_pids = pid_lookup(worker_name, prev_tty) if worker_name else []
    schedule_close(
        prev_window_id,
        delay_seconds,
        close_wait_seconds,
        chain=chain,
        round_index=round_index,
        pass_index=pass_index,
        prev_tty=prev_tty,
        events_path=events_path,
        session_pids=session_pids,
        worker_name=worker_name,
    )
    return {
        "status": "scheduled",
        "window_id": prev_window_id,
        "delay_seconds": delay_seconds,
        "close_wait_seconds": close_wait_seconds,
    }

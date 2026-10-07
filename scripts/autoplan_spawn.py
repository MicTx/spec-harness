#!/usr/bin/env python3
# scripts/autoplan_spawn.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Serial pass-chain spawn for ``/spec:autoplan``.

Autoplan plans and reviews through a serial fresh-session chain with the
same spawn discipline as ``/spec:autorun``: one pass = one full agent
session in its own Terminal.app window (visible TUI, session persisted,
never a print/exec headless mode), strictly sequential, fail fast. No
pass runs as an in-process subagent of the spawning session (recorded in
the ``2026-10-07_add-autoplan-pass-chain`` Development Record).

Subcommands:

  - ``spawn``   open the next pass window after guards. ``--next`` names
                the pass kind (``framework`` / ``detail`` / ``review``);
                ``detail`` additionally requires ``--target <doc>`` (the
                pass's one assigned document, may be a new file, must
                stay under the project root). ``framework`` requires the
                seeded master document (first ``--plan`` entry, default
                ``.spec/plan.md``) to exist — evidence that the
                interrogation phase recorded its findings first. Guards
                are mechanical only (pass cap, single-chain lock, path
                bounds, seed presence); cluster qualification is the
                gate's contract in ``autoplan_gate.py``, not this
                script's;
  - ``status``  render the current pass-chain state.

Exit codes are part of the contract:

  - ``0`` success;
  - ``1`` operational refusal or failure (lock held, pass cap reached,
         missing seed document, missing ``--target`` for a detail pass,
         target escaping the project root, no worker host available,
         osascript failure, unreadable chain state);
  - ``2`` usage error (argparse).

Contract output goes to stdout (text, or JSON with ``--format json``);
diagnostics go to stderr. ``spawn`` writes only under ``.spec/autoplan/``:
``chain.json`` (latest pass-chain state), ``spawns.jsonl`` (append-only
audit), and the transient ``chain.lock`` — never under ``.spec/autorun/``.

The Terminal.app automation is implemented by ``autorun_spawn.py`` and
reused here by import: the two spawn steps — the autorun round spawn and
this autoplan pass spawn — are the only two callers authorized to
automate Terminal.app (both inherit the recorded overturn of the
2026-09-03 no-AppleScript policy in the ``2026-10-06_add-autorun-command``
Development Record).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Shared implementation with the autorun round spawn: one spawn mechanism,
# two authorized callers (see module docstring). The underscore helpers are
# private to the scripts package, not to a single file.
from autorun_spawn import (  # noqa: E402  # type: ignore
    AutorunError,
    _atomic_write,
    _resolve_root,
    _under_root,
    _utc_now,
    build_terminal_command,
    build_worker_command,
    controlling_tty,
    discover_plan_docs,
    parse_spawn_result,
    recycle_previous_window,
    resolve_worker_host,
)

EXIT_OK = 0
EXIT_FAILURE = 1

PASS_KINDS: Tuple[str, ...] = ("framework", "detail", "review")
DEFAULT_MASTER_DOC = ".spec/plan.md"
DEFAULT_MAX_PASSES = 12
CHAIN_DIR_NAME = ".spec/autoplan"
CHAIN_STATE_FILE = "chain.json"
CHAIN_SPAWNS_FILE = "spawns.jsonl"
CHAIN_LOCK_FILE = "chain.lock"


class AutoplanSpawnError(AutorunError):
    """Operational refusal for the autoplan pass chain (same exit class as autorun)."""


def _chain_dir(root: Path) -> Path:
    return root / CHAIN_DIR_NAME


def _read_chain_state(root: Path) -> Optional[Dict[str, object]]:
    state_file = _chain_dir(root) / CHAIN_STATE_FILE
    if not state_file.is_file():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutoplanSpawnError(f"pass-chain state unreadable ({state_file}): {exc}") from exc
    if not isinstance(data, dict):
        raise AutoplanSpawnError(f"pass-chain state is not a JSON object: {state_file}")
    return data


def master_doc_path(root: Path, explicit: Optional[str]) -> Path:
    """Resolve the master document: first ``--plan`` entry, else ``.spec/plan.md``."""
    if explicit:
        first = explicit.split(",")[0].strip()
        candidate = Path(first).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        return candidate.resolve()
    return (root / DEFAULT_MASTER_DOC).resolve()


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
    if not _under_root(root, candidate):
        raise AutoplanSpawnError(f"--target escapes the project root: {target}")
    return str(candidate.relative_to(root)) if _under_root(root, candidate) else str(candidate)


def build_pass_prompt(host: str, plan_args: List[str], max_passes: int) -> str:
    """Build the autoplan continuation prompt injected into the pass session."""
    tail = " ".join(["continue"] + plan_args + [f"--max-passes {max_passes}"])
    if host == "claude":
        return f"/spec:autoplan {tail}".strip()
    return f"$spec autoplan {tail}".strip()


def spawn_payload(root: Path, args: argparse.Namespace) -> Dict[str, object]:
    """Run the pass guards, open the pass window, and record chain state."""
    kind: str = args.next
    target = resolve_target(root, args.target, kind)

    # Validate --plan entries (existence + root bounds) and gather the
    # qualifying docs for the record; explicit paths qualify as given.
    plan_docs, _ = discover_plan_docs(root, args.plan)
    doc_paths = [str(doc.relative_to(root)) if _under_root(root, doc) else str(doc) for doc in plan_docs]

    if kind == "framework":
        master = master_doc_path(root, args.plan)
        if not master.is_file():
            raise AutoplanSpawnError(
                "framework pass requires the seeded master document "
                f"{master} — run the interrogation phase first (it seeds or updates "
                "the master with confirmed facts / assumptions / open questions), "
                "or pass --plan naming the cluster's master document"
            )

    chain_dir = _chain_dir(root)
    chain_dir.mkdir(parents=True, exist_ok=True)
    lock_path = chain_dir / CHAIN_LOCK_FILE
    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AutoplanSpawnError(
                f"another autoplan pass chain holds {CHAIN_LOCK_FILE}; parallel chains are refused"
            ) from exc

        state = _read_chain_state(root) or {}
        last_pass = state.get("pass")
        try:
            last_pass = int(last_pass) if last_pass is not None else 0
        except (TypeError, ValueError) as exc:
            raise AutoplanSpawnError(f"pass-chain state has a non-integer pass index: {last_pass!r}") from exc
        next_pass = last_pass + 1
        if next_pass > args.max_passes:
            raise AutoplanSpawnError(f"pass cap reached: next pass {next_pass} exceeds --max-passes {args.max_passes}")

        host, host_source = resolve_worker_host(args.host)
        plan_args: List[str] = []
        if args.plan:
            plan_args.append(f"--plan {args.plan}")
        prompt = build_pass_prompt(host, plan_args, args.max_passes)
        worker_command = build_worker_command(host, root, prompt)
        worker_name = worker_command[0]
        osascript_argv, shell_command = build_terminal_command(root, worker_command)

        record = {
            "pass": next_pass,
            "kind": kind,
            "target": target,
            "host": host,
            "host_source": host_source,
            "plan_docs": doc_paths,
            "max_passes": args.max_passes,
            "spawned_at": _utc_now(),
            "shell_command": shell_command,
            "prev_tty": controlling_tty(),
        }

        if args.dry_run:
            return {
                "dry_run": True,
                "osascript": osascript_argv,
                "window_recycle": {"status": "planned"},
                **record,
            }

        completed = subprocess.run(
            osascript_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise AutoplanSpawnError(f"osascript failed to open the pass Terminal window: {detail}")
        new_window_id, new_tty = parse_spawn_result(completed.stdout)
        record["window_recycle"] = recycle_previous_window(
            prev_tty=record["prev_tty"],
            new_window_id=new_window_id,
            new_tty=new_tty,
            worker_name=worker_name,
        )

        state_file = chain_dir / CHAIN_STATE_FILE
        state = dict(record)
        state["updated_at"] = state.pop("spawned_at")
        _atomic_write(state_file, json.dumps(state, indent=2, sort_keys=True) + "\n")
        with open(chain_dir / CHAIN_SPAWNS_FILE, "a", encoding="utf-8") as audit:
            audit.write(json.dumps(record, sort_keys=True) + "\n")
        return {"dry_run": False, "terminal": completed.stdout.strip(), **record}


def status_payload(root: Path) -> Dict[str, object]:
    state = _read_chain_state(root)
    payload: Dict[str, object] = {
        "project": str(root),
        "chain_state_file": str(_chain_dir(root) / CHAIN_STATE_FILE),
    }
    if state is None:
        payload["started"] = False
    else:
        payload["started"] = True
        payload.update(state)
    return payload


def _render_spawn(payload: Dict[str, object]) -> str:
    lines = [
        "pass: {}/{}".format(payload["pass"], payload["max_passes"]),
        "kind: {}".format(payload["kind"]),
        "host: {}".format(payload["host"]),
    ]
    if payload.get("target"):
        lines.append("target: {}".format(payload["target"]))
    lines.append("shell_command: {}".format(payload["shell_command"]))
    recycle = payload.get("window_recycle")
    if isinstance(recycle, dict):
        if recycle.get("status") == "scheduled":
            lines.append("close: window {} in {}s".format(recycle["window_id"], recycle["delay_seconds"]))
        elif recycle.get("status") == "planned":
            lines.append("close: planned (previous window closes after the pass worker is confirmed running)")
        else:
            lines.append("close: skipped ({})".format(recycle.get("reason", "unknown")))
    if payload.get("dry_run"):
        lines.append("dry-run: osascript not executed")
    else:
        lines.append("terminal: {}".format(payload.get("terminal", "")))
    return "\n".join(lines)


def _render_status(payload: Dict[str, object]) -> str:
    lines = [f"project: {payload['project']}"]
    if not payload.get("started"):
        lines.append("pass chain: not started")
    else:
        lines.append("pass: {}".format(payload.get("pass")))
        lines.append("kind: {}".format(payload.get("kind")))
        if payload.get("target"):
            lines.append("target: {}".format(payload.get("target")))
        lines.append("host: {}".format(payload.get("host")))
        lines.append("updated_at: {}".format(payload.get("updated_at")))
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

    spawn_parser = subparsers.add_parser("spawn", help="open the next planning-pass Terminal window after guards")
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
        help="comma-separated planning document paths (default: scan conventional candidates)",
    )
    spawn_parser.add_argument(
        "--host",
        choices=("codex", "pi", "claude"),
        help="worker host (default: SPEC_AUTORUN_HOST or PATH order codex/pi/claude)",
    )
    spawn_parser.add_argument(
        "--max-passes",
        dest="max_passes",
        type=int,
        default=DEFAULT_MAX_PASSES,
        help=f"pass cap (default: {DEFAULT_MAX_PASSES})",
    )
    spawn_parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="print the osascript command without executing or recording",
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
    try:
        root = _resolve_root(args.root)
        if args.subcommand == "spawn":
            if args.max_passes < 1:
                raise AutoplanSpawnError("--max-passes must be >= 1")
            _emit(spawn_payload(root, args), _render_spawn, args.format == "json")
        else:
            _emit(status_payload(root), _render_status, args.format == "json")
    except AutorunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# scripts/autorun_spawn.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Autorun chain facts and next-round spawn for ``/spec:autorun``.

Subcommands:

  - ``plan``    discover the project planning documents and count feature
                checkboxes (the machine truth for chain termination);
  - ``spawn``   open a new Terminal.app window in the project path running
                the worker CLI headless with the autorun prompt injected,
                after enforcing the round cap, the single-chain lock, and
                the next-round package presence;
  - ``status``  render the current chain state.

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
and the transient ``chain.lock``. The Terminal.app automation is
scope-limited to this spawn step (recorded overturn of the 2026-09-03
no-AppleScript policy in the ``2026-10-06_add-autorun-command``
Development Record); no other caller may reuse it.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NO_PLAN = 3

WORKER_HOSTS: Tuple[str, ...] = ("codex", "pi", "claude")
DEFAULT_MAX_ROUNDS = 20
CHAIN_DIR_NAME = ".spec/autorun"
CHAIN_STATE_FILE = "chain.json"
CHAIN_SPAWNS_FILE = "spawns.jsonl"
CHAIN_LOCK_FILE = "chain.lock"

FEATURE_CHECKBOX = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s*(\S.*)$")
PLAN_CANDIDATES: Tuple[str, ...] = (
    ".spec/plan.md",
    "PLAN.md",
    "ROADMAP.md",
    "PRD.md",
    "docs/plan.md",
    "docs/roadmap.md",
    "docs/prd.md",
)
PLAN_CANDIDATE_GLOBS: Tuple[str, ...] = ("docs/plans/*.md", "docs/design/*.md")


class AutorunError(Exception):
    """Operational refusal or failure with a user-facing message."""


class NoPlanError(AutorunError):
    """No qualifying planning document was found (exit 3)."""


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_root(raw_root: str) -> Path:
    root = Path(raw_root).expanduser().resolve()
    if not root.is_dir():
        raise AutorunError(f"project root is not a directory: {root}")
    return root


def _under_root(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _candidate_paths(root: Path) -> List[Path]:
    candidates: List[Path] = [root / name for name in PLAN_CANDIDATES]
    for pattern in PLAN_CANDIDATE_GLOBS:
        candidates.extend(sorted(root.glob(pattern)))
    return candidates


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
    absolute); each must exist and stay under root. Without it, the
    conventional candidates are scanned. A candidate qualifies only when
    it contains at least one feature checkbox.
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
            if not _under_root(root, candidate):
                raise AutorunError(f"--plan path escapes the project root: {raw}")
            if not candidate.is_file():
                raise AutorunError(f"--plan path does not exist: {raw}")
            qualifying.append(candidate)
    else:
        for candidate in _candidate_paths(root):
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
                "path": str(doc.relative_to(root)) if _under_root(root, doc) else str(doc),
                **counts,
            }
        )
        for key in totals:
            totals[key] += counts[key]  # type: ignore[operator]
    if not doc_reports:
        raise NoPlanError(
            "no qualifying planning document found; scanned: "
            + (", ".join(scanned) if scanned else "(none)")
            + " — write project-level planning (feature checkboxes) before autorun"
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
    state_file = _chain_dir(root) / CHAIN_STATE_FILE
    if not state_file.is_file():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutorunError(f"chain state unreadable ({state_file}): {exc}") from exc
    if not isinstance(data, dict):
        raise AutorunError(f"chain state is not a JSON object: {state_file}")
    return data


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


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


def resolve_worker_host(explicit: Optional[str]) -> str:
    """Resolve the worker host: ``--host`` > ``SPEC_AUTORUN_HOST`` > PATH order."""
    host = explicit or os.environ.get("SPEC_AUTORUN_HOST") or ""
    if host:
        if host not in WORKER_HOSTS:
            raise AutorunError(f"unknown worker host: {host} (expected one of {WORKER_HOSTS})")
        return host
    for candidate in WORKER_HOSTS:
        if shutil.which(candidate):
            return candidate
    raise AutorunError(f"no worker host available on PATH (tried {WORKER_HOSTS})")


def build_prompt(host: str, plan_args: List[str], max_rounds: int) -> str:
    """Build the autorun prompt injected into the next-round session."""
    tail = " ".join(plan_args + [f"--max-rounds {max_rounds}"])
    if host == "claude":
        return f"/spec:autorun {tail}".strip()
    return f"$spec autorun {tail}".strip()


def build_worker_command(host: str, root: Path, prompt: str) -> List[str]:
    """Build the headless worker argv for the next-round session."""
    if host == "codex":
        return [
            "codex",
            "exec",
            "--cd",
            str(root),
            "--sandbox",
            "workspace-write",
            "-c",
            "sandbox_workspace_write.network_access=true",
            "--",
            prompt,
        ]
    if host == "pi":
        return ["pi", "-p", "--no-session", "--mode", "text", "--", prompt]
    if host == "claude":
        return ["claude", "-p", "--dangerously-skip-permissions", prompt]
    raise AutorunError(f"unknown worker host: {host}")


def escape_applescript(text: str) -> str:
    """Escape a string for an AppleScript double-quoted literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def build_terminal_command(
    root: Path, worker_command: List[str], custom_command: Optional[str] = None
) -> Tuple[List[str], str]:
    """Build the osascript argv opening a Terminal window at ``root``.

    Returns ``(osascript argv, shell command)``. The shell command is
    ``cd <root> && <worker argv>`` — or ``cd <root> && <custom command>``
    when a raw ``--command`` override is given; AppleScript sees it as one
    escaped string literal.
    """
    if custom_command is not None:
        tail = custom_command
    else:
        tail = " ".join(shlex.quote(part) for part in worker_command)
    shell_command = "cd {} && {}".format(shlex.quote(str(root)), tail)
    applescript = 'tell application "Terminal" to do script "{}"'.format(escape_applescript(shell_command))
    return ["osascript", "-e", applescript], shell_command


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

    chain_dir = _chain_dir(root)
    chain_dir.mkdir(parents=True, exist_ok=True)
    lock_path = chain_dir / CHAIN_LOCK_FILE
    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AutorunError(
                f"another autorun chain holds {CHAIN_LOCK_FILE} for this project; parallel chains are refused"
            ) from exc

        state = _read_chain_state(root) or {}
        last_round = state.get("round")
        try:
            last_round = int(last_round) if last_round is not None else 0
        except (TypeError, ValueError) as exc:
            raise AutorunError(f"chain state has a non-integer round: {last_round!r}") from exc
        next_round = last_round + 1
        if next_round > args.max_rounds:
            raise AutorunError(f"round cap reached: next round {next_round} exceeds --max-rounds {args.max_rounds}")

        if args.command:
            host = "custom"
            worker_command: List[str] = []
        else:
            host = resolve_worker_host(args.host)
            plan_args: List[str] = []
            if args.plan:
                plan_args.append(f"--plan {args.plan}")
            prompt = build_prompt(host, plan_args, args.max_rounds)
            worker_command = build_worker_command(host, root, prompt)
        osascript_argv, shell_command = build_terminal_command(root, worker_command, args.command)

        record = {
            "round": next_round,
            "host": host,
            "plan_docs": [doc["path"] for doc in plan["docs"]],  # type: ignore[index]
            "max_rounds": args.max_rounds,
            "spawned_at": _utc_now(),
            "shell_command": shell_command,
        }

        if args.dry_run:
            return {
                "dry_run": True,
                "osascript": osascript_argv,
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
            raise AutorunError(f"osascript failed to open the Terminal window: {detail}")

        state_file = chain_dir / CHAIN_STATE_FILE
        state = dict(record)
        state["updated_at"] = state.pop("spawned_at")
        _atomic_write(state_file, json.dumps(state, indent=2, sort_keys=True) + "\n")
        with open(chain_dir / CHAIN_SPAWNS_FILE, "a", encoding="utf-8") as audit:
            audit.write(json.dumps(record, sort_keys=True) + "\n")
        return {"dry_run": False, "terminal": completed.stdout.strip(), **record}


def status_payload(root: Path) -> Dict[str, object]:
    state = _read_chain_state(root)
    payload: Dict[str, object] = {"project": str(root), "chain_state_file": str(_chain_dir(root) / CHAIN_STATE_FILE)}
    if state is None:
        payload["started"] = False
    else:
        payload["started"] = True
        payload.update(state)
    try:
        payload["plan"] = plan_payload(root, None)
    except NoPlanError as exc:
        payload["plan"] = {"error": str(exc)}
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
    if payload.get("dry_run"):
        lines.append("dry-run: osascript not executed")
    else:
        lines.append("terminal: {}".format(payload.get("terminal", "")))
    return "\n".join(lines)


def _render_status(payload: Dict[str, object]) -> str:
    lines = [f"project: {payload['project']}"]
    if not payload.get("started"):
        lines.append("chain: not started")
    else:
        lines.append("round: {}".format(payload.get("round")))
        lines.append("host: {}".format(payload.get("host")))
        lines.append("updated_at: {}".format(payload.get("updated_at")))
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
        help="comma-separated planning document paths (default: scan conventional candidates)",
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
        help="worker host (default: SPEC_AUTORUN_HOST or PATH order codex/pi/claude)",
    )
    spawn_parser.add_argument(
        "--command",
        help="raw shell command override run in the window after cd <root> (host recorded as custom)",
    )
    spawn_parser.add_argument(
        "--max-rounds",
        type=int,
        default=DEFAULT_MAX_ROUNDS,
        help=f"round cap (default: {DEFAULT_MAX_ROUNDS})",
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
    try:
        root = _resolve_root(args.root)
        if args.subcommand == "plan":
            _emit(plan_payload(root, args.plan), _render_plan, args.format == "json")
        elif args.subcommand == "spawn":
            if args.max_rounds < 1:
                raise AutorunError("--max-rounds must be >= 1")
            _emit(spawn_payload(root, args), _render_spawn, args.format == "json")
        else:
            _emit(status_payload(root), _render_status, args.format == "json")
    except NoPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_PLAN
    except AutorunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

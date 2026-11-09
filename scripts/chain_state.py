#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Minimal chain state for /spec:autoplan and /spec:autorun (current session only).

The task packages stay the work truth. This script only records where a chain
is: which plan it follows, which step or package it is on, and how many rounds
it has spent. State is one JSON file, ``.spec/<chain>/chain.json``; Git is the
history. There is no owner protocol, journal, lock or terminal spawning: one
session drives one chain, and a new session resumes from disk.

Subcommands:
  plan    list qualifying planning documents and feature counts (exit 3: none)
  status  print the chain state
  begin   start or resume a step (autorun: one package = one round)
  finish  mark the current step done; autorun completes when every feature is checked
  pause   record why the chain stopped
  close   move the state to .spec/<chain>/history/ (fresh start next time)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_STOP = 3

CHAINS = ("autoplan", "autorun")
DEFAULT_MAX_ROUNDS = {"autoplan": None, "autorun": 5}
STATE_FILE = "chain.json"
STATE_FORMAT = "chain-state/1"

PLAN_ROOT = ".spec/plans"
PLAN_ARCHIVE_DIR_NAME = "archive"
PLAN_CANDIDATE_GLOBS = (".spec/plans/*.md", ".spec/plans/*/master.md")
FEATURE_CHECKBOX = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s*(\S.*)$")


class ChainError(Exception):
    """User-facing refusal (exit 1)."""


class NoPlanError(ChainError):
    """No qualifying planning document (exit 3)."""


# --------------------------------------------------------------------------- #
# planning documents
# --------------------------------------------------------------------------- #


def under_root(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def plan_candidates(root: Path) -> list[Path]:
    """Top-level ``.spec/plans/*.md`` plus each cluster's ``master.md``; ``archive/`` is never scanned."""
    found = {
        path
        for pattern in PLAN_CANDIDATE_GLOBS
        for path in root.glob(pattern)
        if path.is_file() and path.parent.name != PLAN_ARCHIVE_DIR_NAME
    }
    return sorted(found)


def count_features(text: str) -> dict[str, object]:
    checked, unchecked_items, items = 0, [], []
    for line in text.splitlines():
        match = FEATURE_CHECKBOX.match(line)
        if not match:
            continue
        items.append(match.group(2).strip())
        if match.group(1) == " ":
            unchecked_items.append(match.group(2).strip())
        else:
            checked += 1
    return {
        "checked": checked,
        "unchecked": len(unchecked_items),
        "total": len(items),
        "unchecked_items": unchecked_items,
        "items": items,
    }


def discover_plan_docs(root: Path, explicit: str | None) -> tuple[list[Path], list[str]]:
    """(docs with at least one feature checkbox, scanned candidates)."""
    candidates: list[Path] = []
    if explicit:
        for raw in (part.strip() for part in explicit.split(",")):
            if not raw:
                continue
            path = Path(raw).expanduser()
            path = (path if path.is_absolute() else root / path).resolve()
            if not under_root(root, path):
                raise ChainError(f"--plan path escapes the project root: {raw}")
            if not path.is_file():
                raise ChainError(f"--plan path does not exist: {raw}")
            candidates.append(path)
    else:
        candidates = plan_candidates(root)
    docs = [path for path in candidates if count_features(path.read_text(encoding="utf-8"))["total"]]
    return docs, [str(path) for path in candidates]


def plan_payload(root: Path, explicit: str | None) -> dict[str, object]:
    docs, scanned = discover_plan_docs(root, explicit)
    if not docs:
        raise NoPlanError(
            f"no planning document with feature checkboxes under {PLAN_ROOT}/; scanned: "
            + (", ".join(scanned) or "(none)")
        )
    reports, totals, items = [], {"checked": 0, "unchecked": 0, "total": 0}, []
    for doc in docs:
        counts = count_features(doc.read_text(encoding="utf-8"))
        items.extend(counts.pop("items"))  # type: ignore[arg-type]
        reports.append({"path": doc.relative_to(root).as_posix(), **counts})
        for key in totals:
            totals[key] += counts[key]  # type: ignore[operator]
    scope = hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()[:16]
    return {"docs": reports, **totals, "complete": totals["unchecked"] == 0, "scope": scope}


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #


def state_path(root: Path, chain: str) -> Path:
    return root / ".spec" / chain / STATE_FILE


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(root: Path, chain: str) -> dict[str, object] | None:
    path = state_path(root, chain)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChainError(f"{path} is unreadable ({exc}); fix it with git or delete it to start over") from exc
    if not isinstance(state, dict) or state.get("format") != STATE_FORMAT:
        raise ChainError(
            f"{path} was written by an older chain implementation (removed in 0.27); "
            "delete it (git keeps the history) and begin again"
        )
    return state


def save_state(root: Path, chain: str, state: dict[str, object]) -> None:
    path = state_path(root, chain)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now()
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def require_state(root: Path, chain: str) -> dict[str, object]:
    state = load_state(root, chain)
    if state is None:
        raise ChainError(f"no {chain} chain; start one with `chain_state.py begin --chain {chain}`")
    return state


def cmd_begin(root: Path, args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    state = load_state(root, args.chain)
    if state is None or state.get("status") == "complete":
        plan = plan_payload(root, args.plan)
        cap = None if args.unbounded else (args.max_rounds or DEFAULT_MAX_ROUNDS[args.chain])
        state = {
            "format": STATE_FORMAT,
            "chain": args.chain,
            "plan": [doc["path"] for doc in plan["docs"]],  # type: ignore[index]
            "scope": plan["scope"],
            "round": 0,
            "max_rounds": cap,
            "status": "active",
            "step": None,
            "started_at": now(),
        }
    elif args.max_rounds or args.unbounded:
        state["max_rounds"] = None if args.unbounded else args.max_rounds
    if state.get("step") != args.step:
        cap = state.get("max_rounds")
        if isinstance(cap, int) and int(state["round"]) >= cap:  # type: ignore[arg-type]
            save_state(root, args.chain, {**state, "status": "paused", "reason": "round cap reached"})
            return EXIT_STOP, {**state, "status": "paused", "reason": f"round cap {cap} reached"}
        state["round"] = int(state["round"]) + 1  # type: ignore[arg-type]
        state["step"] = args.step
    state["status"] = "active"
    state.pop("reason", None)
    save_state(root, args.chain, state)
    return EXIT_OK, state


def cmd_finish(root: Path, args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    state = require_state(root, args.chain)
    plan = plan_payload(root, ",".join(state["plan"]))  # type: ignore[arg-type]
    state["last_finished"] = state.get("step")
    state["status"] = "complete" if args.chain == "autorun" and plan["complete"] else "active"
    save_state(root, args.chain, state)
    return EXIT_OK, {**state, "plan_status": plan}


def cmd_pause(root: Path, args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    state = require_state(root, args.chain)
    state.update(status="paused", reason=args.reason)
    save_state(root, args.chain, state)
    return EXIT_OK, state


def cmd_close(root: Path, args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    state = require_state(root, args.chain)
    history = state_path(root, args.chain).parent / "history"
    history.mkdir(exist_ok=True)
    target = history / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    state_path(root, args.chain).replace(target)
    return EXIT_OK, {**state, "closed_to": target.relative_to(root).as_posix()}


def cmd_status(root: Path, args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    state = load_state(root, args.chain)
    if state is None:
        return EXIT_OK, {"chain": args.chain, "status": "none"}
    try:
        plan = plan_payload(root, ",".join(state["plan"]))  # type: ignore[arg-type]
    except ChainError as exc:
        return EXIT_OK, {**state, "plan_error": str(exc)}
    return EXIT_OK, {**state, "plan_status": plan, "scope_changed": plan["scope"] != state.get("scope")}


def cmd_plan(root: Path, args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    return EXIT_OK, plan_payload(root, args.plan)


COMMANDS = {
    "plan": cmd_plan,
    "status": cmd_status,
    "begin": cmd_begin,
    "finish": cmd_finish,
    "pause": cmd_pause,
    "close": cmd_close,
}


def render_text(payload: dict[str, object]) -> str:
    if "docs" in payload:
        lines = [f"{doc['path']}: {doc['checked']}/{doc['total']} 已完成" for doc in payload["docs"]]  # type: ignore[union-attr]
        return "\n".join([*lines, f"合计 {payload['checked']}/{payload['total']}"])
    keys = ("chain", "status", "step", "round", "max_rounds", "reason", "scope_changed", "closed_to", "plan_error")
    return "\n".join(f"{key}: {payload[key]}" for key in keys if payload.get(key) is not None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chain_state", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        child = sub.add_parser(name)
        child.add_argument("--root", default=".")
        child.add_argument("--format", choices=("text", "json"), default="text")
        if name != "plan":
            child.add_argument("--chain", choices=CHAINS, required=True)
        if name in ("plan", "begin"):
            child.add_argument("--plan", default=None, help="comma-separated planning documents")
        if name == "begin":
            child.add_argument("--step", required=True, help="autoplan: framework-1, detail-2, ...; autorun: slug")
            cap = child.add_mutually_exclusive_group()
            cap.add_argument("--max-rounds", type=int, default=None)
            cap.add_argument("--unbounded", action="store_true")
        if name == "pause":
            child.add_argument("--reason", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    try:
        code, payload = COMMANDS[args.command](root, args)
    except NoPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_STOP
    except ChainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    print(json.dumps(payload, ensure_ascii=False) if args.format == "json" else render_text(payload))
    return code


if __name__ == "__main__":
    sys.exit(main())

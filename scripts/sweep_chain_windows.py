#!/usr/bin/env python3
# scripts/sweep_chain_windows.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sweep dead chain windows: Terminal windows a chain spawned whose session
ended and nobody recycled (a crashed round, a refused spawn, a killed
session — windows off the recycle path).

Chain membership is proven by recorded ttys: every window the chains
spawned appears as ``prev_tty`` in a spawns row, so the sweep closes
single-tab idle windows whose tty is in that recorded set (minus the
session running the sweep). Live sessions (busy), multi-tab windows, and
the user's own windows (never recorded) are untouched by construction.
``--dry-run`` lists what would close; the real run closes silently (idle
single-tab windows never raise the confirmation sheet) and reports
closed/skipped.

This is the third authorized Terminal.app automation step (F18, recorded in
the ``2026-10-08_sweep-dead-chain-windows`` Development Record); the sweep
is manual by design — an automatic hook could destroy a round summary the
user is still reading.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chain_spawn_support as support  # noqa: E402  # type: ignore


def _chain_dirs(root: Path) -> List[Path]:
    return [root / ".spec" / "autorun", root / ".spec" / "autoplan"]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sweep_chain_windows.py",
        description="Close dead chain windows (single-tab, idle, tty recorded by a chain spawns row).",
    )
    parser.add_argument(
        "--root", default=".", help="project root holding .spec/*/spawns.jsonl (default: current directory)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list the windows that would close without touching them",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: text)")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"error: project root is not a directory: {root}", file=sys.stderr)
        return 1
    recorded = support.recorded_chain_ttys(_chain_dirs(root))
    dead = support.find_dead_chain_windows(recorded, exclude_tty=support.session_tty())
    if args.dry_run:
        payload = {"dry_run": True, "recorded_ttys": len(recorded), "dead_windows": dead}
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("recorded chain ttys: {}".format(len(recorded)))
            print("dead chain windows: {}".format(len(dead)))
            for entry in dead:
                print("- window {} on {} (idle, chain-recorded)".format(entry["window_id"], entry["tty"]))
            print("dry-run: nothing closed")
        return 0

    result = support.close_terminal_windows([int(entry["window_id"]) for entry in dead])
    payload = {
        "dry_run": False,
        "recorded_ttys": len(recorded),
        "found": len(dead),
        "closed": result["closed"],
        "skipped": result["skipped"],
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("recorded chain ttys: {}".format(len(recorded)))
        print("found: {}".format(len(dead)))
        closed = result["closed"]
        skipped = result["skipped"]
        print("closed: {}".format(len(closed)) + (" ({})".format(", ".join(map(str, closed))) if closed else ""))
        print("skipped: {}".format(len(skipped)) + (" ({})".format(", ".join(map(str, skipped))) if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

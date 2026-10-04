#!/usr/bin/env python3
# scripts/update_checkpoint.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recovery CLI for update-stage checkpoints.

Wraps ``update_checkpoint_support`` primitives into the operator-facing
commands for an interrupted update mutation (begin without complete):

  - ``status``   probe one package (``--slug``) or every active package for
                 an unresolved ``update-checkpoint.json``;
  - ``rollback`` restore the triad snapshot and clear the checkpoint;
  - ``complete`` verify the applied update, then clear the checkpoint.

Exit codes are part of the contract so callers can probe without parsing:

  - ``0`` success; for ``status`` additionally "no unresolved checkpoint";
  - ``1`` operational refusal or failure (missing/corrupted checkpoint,
         consistency refusal, unreadable package, invalid slug);
  - ``2`` usage error (argparse);
  - ``3`` ``status`` found an active checkpoint (interrupted update pending
         rollback or complete);
  - ``4`` ``status`` found a corrupted checkpoint (manual recovery required;
         reported as the most severe state when several packages match).

Every mutating command is per-package (``--slug`` is required); the CLI never
scans-and-rewrites in bulk. Failures print ``error: ...`` to stderr and leave
package state untouched except for the documented rollback/complete effects.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from spec_package_support import add_specs_dir_arg, resolve_specs_child, resolve_specs_root, validate_slug
from update_checkpoint_support import (
    CheckpointError,
    CheckpointStatus,
    complete_update_checkpoint,
    detect_unresolved_checkpoints,
    detect_update_checkpoint,
    rollback_update_checkpoint,
)

EXIT_OK = 0
EXIT_FAILURE = 1
# EXIT_USAGE (2) is argparse's own convention; nothing returns it explicitly.
EXIT_STATUS_ACTIVE = 3
EXIT_STATUS_CORRUPTED = 4

STATUS_EXIT_CODES = {"none": EXIT_OK, "active": EXIT_STATUS_ACTIVE, "corrupted": EXIT_STATUS_CORRUPTED}

EPILOG = """\
exit codes:
  0  success; status: no unresolved update checkpoint
  1  operational failure or refusal (details on stderr)
  2  usage error
  3  status: active checkpoint found (interrupted update)
  4  status: corrupted checkpoint found (manual recovery required)
"""


def _add_shared_positional_args(parser: argparse.ArgumentParser) -> None:
    """Accept --root/--specs-dir after the subcommand too.

    SUPPRESS keeps a subparser from clobbering values the top-level parser
    already stored, while still parsing the flags when they come after the
    subcommand name (the repo's flag-at-the-end invocation style).
    """
    parser.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--specs-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect and resolve interrupted update-stage checkpoints.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root where the specs directory exists (default: current directory)",
    )
    add_specs_dir_arg(parser)
    subcommands = parser.add_subparsers(dest="command", required=True, metavar="{status,rollback,complete}")

    status_parser = subcommands.add_parser(
        "status",
        help="report unresolved update checkpoints; exit 3 = active, 4 = corrupted",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    status_parser.add_argument("--slug", help="Probe a single task package; omit to scan all packages")
    _add_shared_positional_args(status_parser)

    rollback_parser = subcommands.add_parser(
        "rollback",
        help="restore the triad snapshot, then clear the checkpoint",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rollback_parser.add_argument("--slug", required=True, help="Task package slug to roll back")
    _add_shared_positional_args(rollback_parser)

    complete_parser = subcommands.add_parser(
        "complete",
        help="verify the applied update, then clear the checkpoint",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    complete_parser.add_argument("--slug", required=True, help="Task package slug whose update is confirmed")
    complete_parser.add_argument(
        "--expect-intent",
        help="Refuse to clear unless the recorded checkpoint intent matches this text",
    )
    _add_shared_positional_args(complete_parser)
    return parser.parse_args(argv)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return EXIT_FAILURE


def _status_line(slug: str, status: CheckpointStatus) -> str:
    if status.state == "active" and status.checkpoint is not None:
        return (
            f"{slug}：存在未完成 update checkpoint"
            f"（意图：{status.checkpoint.intent}；开始于：{status.checkpoint.created_at}）"
        )
    if status.state == "corrupted":
        return f"{slug}：存在未完成 update checkpoint，且记录已损坏，需人工恢复（{status.error}）"
    return f"{slug}：无未完成 update checkpoint"


def _resolve_package_dir(args: argparse.Namespace, specs_dir: Path) -> tuple[str | None, Path | None, int | None]:
    """Validate ``--slug`` and locate the package; ``None`` parts mean failure."""
    try:
        slug = validate_slug(args.slug.strip())
        package_dir = specs_dir / slug
        if package_dir.is_symlink():
            raise ValueError(f"task package directory must not be a symlink: {package_dir}")
        package_dir = resolve_specs_child(specs_dir, slug)
        if not package_dir.is_dir():
            raise ValueError(f"task package not found: {package_dir}")
    except (ValueError, OSError, RuntimeError) as exc:
        return None, None, _fail(str(exc))
    return slug, package_dir, None


def _cmd_status(args: argparse.Namespace, specs_dir: Path) -> int:
    if args.slug:
        slug, package_dir, failure = _resolve_package_dir(args, specs_dir)
        if failure is not None or slug is None or package_dir is None:
            return failure if failure is not None else EXIT_FAILURE
        status = detect_update_checkpoint(package_dir)
        print(_status_line(slug, status))
        return STATUS_EXIT_CODES[status.state]

    found = detect_unresolved_checkpoints(specs_dir)
    if not found:
        print("未发现未完成 update checkpoint")
        return EXIT_OK
    worst = EXIT_OK
    for slug in sorted(found):
        status = found[slug]
        print(_status_line(slug, status))
        worst = max(worst, STATUS_EXIT_CODES[status.state])
    return worst


def _cmd_resolve(args: argparse.Namespace, specs_dir: Path) -> int:
    slug, package_dir, failure = _resolve_package_dir(args, specs_dir)
    if failure is not None or slug is None or package_dir is None:
        return failure if failure is not None else EXIT_FAILURE
    try:
        if args.command == "rollback":
            checkpoint = rollback_update_checkpoint(package_dir)
            print(f"{slug}：update checkpoint 已回滚（意图：{checkpoint.intent}），三件套已恢复到更新前快照")
        else:
            checkpoint = complete_update_checkpoint(package_dir, expected_intent=args.expect_intent)
            print(f"{slug}：update checkpoint 已确认完成（意图：{checkpoint.intent}），检查点已清除，更新内容保留")
    except (CheckpointError, ValueError, OSError) as exc:
        return _fail(str(exc))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    try:
        specs_root = resolve_specs_root(root, args.specs_dir)
    except ValueError as exc:
        return _fail(str(exc))
    try:
        specs_dir = resolve_specs_child(specs_root, "specs")
    except ValueError as exc:
        return _fail(str(exc))

    if args.command == "status":
        return _cmd_status(args, specs_dir)
    return _cmd_resolve(args, specs_dir)


if __name__ == "__main__":
    sys.exit(main())

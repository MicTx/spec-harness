#!/usr/bin/env python3
# scripts/update_checkpoint.py
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Recognition CLI for historical update-stage checkpoints.

The self-managed checkpoint mechanism (triad snapshot + begin/rollback/
complete lifecycle) is retired: update rollback safety is now carried by Git
WIP commits on the integration branch. This CLI is read-only —
``status`` probes one package (``--slug``) or every active package for an
unresolved historical ``update-checkpoint.json`` left behind by the retired
mechanism. The retired write subcommands (``begin`` / ``rollback`` /
``complete``) are still accepted by the parser only to refuse them with the
Git replacement guidance, so muscle-memory paths get migration instructions
instead of a silent disappearance.

Exit codes are part of the contract so callers can probe without parsing:

  - ``0`` success; for ``status`` additionally "no unresolved checkpoint";
  - ``1`` operational refusal or failure (unreadable package, invalid slug);
  - ``2`` usage error (argparse); retired write subcommands also exit ``2``
    after printing the Git replacement guidance;
  - ``3`` ``status`` found an active checkpoint (interrupted update pending
    recovery via Git history, then file deletion);
  - ``4`` ``status`` found a corrupted checkpoint (manual recovery required;
         reported as the most severe state when several packages match).

Failures print ``error: ...`` to stderr and never mutate package state.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from spec_package_support import add_specs_dir_arg, resolve_specs_child, resolve_specs_root, validate_slug
from update_checkpoint_support import CheckpointStatus, detect_unresolved_checkpoints, detect_update_checkpoint

EXIT_OK = 0
EXIT_FAILURE = 1
# EXIT_USAGE (2) is argparse's own convention; retired write subcommands
# reuse it because they are no longer valid usage, guidance included.
EXIT_STATUS_ACTIVE = 3
EXIT_STATUS_CORRUPTED = 4

STATUS_EXIT_CODES = {"none": EXIT_OK, "active": EXIT_STATUS_ACTIVE, "corrupted": EXIT_STATUS_CORRUPTED}

RETIRED_SUBCOMMANDS = ("begin", "rollback", "complete")

GIT_REPLACEMENT_GUIDANCE = """\
替代流程（Git WIP 承担回滚保障）：
  1) 更新前，在集成分支提交恢复点：
       git add -A && git commit -m "Spec-WIP: <slug> update checkpoint"
  2) 更新中断需要回滚时：
       git reset --hard <WIP 提交 sha>   （集成分支未推送时）
       git revert <WIP 提交 sha>         （已推送时）
  3) 历史遗留的 update-checkpoint.json：按上述流程用 Git 历史确认/恢复
     三件套后，直接删除该文件，再用 status 复核。
"""

RETIRED_NOTICE = "此子命令已退役：update 回滚保障改由 Git WIP 提交承担，CLI 不再写任何状态。\n\n"

EPILOG = f"""\
exit codes:
  0  success; status: no unresolved update checkpoint
  1  operational failure or refusal (details on stderr)
  2  usage error; retired subcommands ({", ".join(RETIRED_SUBCOMMANDS)}) also
     exit 2 after printing the Git WIP replacement guidance
  3  status: active checkpoint found (interrupted update)
  4  status: corrupted checkpoint found (manual recovery required)
"""

RETIRED_EPILOG = RETIRED_NOTICE + GIT_REPLACEMENT_GUIDANCE + "\n" + EPILOG


def _add_shared_positional_args(parser: argparse.ArgumentParser) -> None:
    """Accept --root/--specs-dir after the subcommand too.

    SUPPRESS keeps a subparser from clobbering values the top-level parser
    already stored, while still parsing the flags when they come after the
    subcommand name (the repo's flag-at-the-end invocation style).
    """
    parser.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--specs-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def _add_retired_parser(subcommands, name: str, old_help: str) -> None:
    """Register a retired write subcommand that only refuses with guidance."""
    parser = subcommands.add_parser(
        name,
        help=f"(retired) {old_help}; exits 2 with the Git WIP replacement guidance",
        epilog=RETIRED_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    if name in ("begin", "rollback", "complete"):
        parser.add_argument("--slug", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    if name == "begin":
        parser.add_argument("--intent", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    if name == "complete":
        parser.add_argument("--expect-intent", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    _add_shared_positional_args(parser)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recognize historical update-stage checkpoints (read-only; rollback safety is Git WIP).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root where the specs directory exists (default: current directory)",
    )
    add_specs_dir_arg(parser)
    subcommands = parser.add_subparsers(dest="command", required=True, metavar="{status,begin,rollback,complete}")

    status_parser = subcommands.add_parser(
        "status",
        help="report unresolved update checkpoints; exit 3 = active, 4 = corrupted",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    status_parser.add_argument("--slug", help="Probe a single task package; omit to scan all packages")
    _add_shared_positional_args(status_parser)

    _add_retired_parser(subcommands, "begin", "snapshot the triad, then record a new checkpoint")
    _add_retired_parser(subcommands, "rollback", "restore the triad snapshot, then clear the checkpoint")
    _add_retired_parser(subcommands, "complete", "verify the applied update, then clear the checkpoint")
    return parser.parse_args(argv)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return EXIT_FAILURE


def _fail_retired(command: str) -> int:
    print(
        f"error: `{command}` 已退役：update 的回滚保障改由 Git WIP 提交承担，不再写 update-checkpoint.json。",
        file=sys.stderr,
    )
    print(GIT_REPLACEMENT_GUIDANCE, file=sys.stderr, end="")
    return 2


def _status_line(slug: str, status: CheckpointStatus) -> str:
    if status.state == "active" and status.checkpoint is not None:
        return (
            f"{slug}：存在未完成 update checkpoint（历史遗留，机制已退役）"
            f"（意图：{status.checkpoint.intent}；开始于：{status.checkpoint.created_at}）；"
            "恢复方式：按 Git 历史确认/恢复三件套后删除该文件"
        )
    if status.state == "corrupted":
        return f"{slug}：存在未完成 update checkpoint，且记录已损坏，需按 Git 历史人工恢复（{status.error}）"
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
    return _fail_retired(args.command)


if __name__ == "__main__":
    sys.exit(main())

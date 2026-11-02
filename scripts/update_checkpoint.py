#!/usr/bin/env python3
# scripts/update_checkpoint.py
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Recognition CLI for historical update-stage checkpoints.

The self-managed checkpoint mechanism (triad snapshot + begin/rollback/
complete lifecycle) is retired: update rollback safety uses a private,
path-scoped pre-update copy without staging or committing. This CLI is read-only —
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
    after printing the path-scoped recovery guidance;
  - ``3`` ``status`` found an active checkpoint (interrupted update pending
    reviewed recovery or keep-new, then checkpoint removal);
  - ``4`` ``status`` found a corrupted checkpoint (manual recovery required;
         reported as the most severe state when several packages match).

Failures print ``error: ...`` to stderr and never mutate package state.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from spec_package_support import add_specs_dir_arg, resolve_specs_child, resolve_specs_root, validate_slug
from update_checkpoint_support import (
    CheckpointStatus,
    checkpoint_display_text,
    detect_unresolved_checkpoints,
    detect_update_checkpoint,
)

EXIT_OK = 0
EXIT_FAILURE = 1
# EXIT_USAGE (2) is argparse's own convention; retired write subcommands
# reuse it because they are no longer valid usage, guidance included.
EXIT_STATUS_ACTIVE = 3
EXIT_STATUS_CORRUPTED = 4

STATUS_EXIT_CODES = {"none": EXIT_OK, "active": EXIT_STATUS_ACTIVE, "corrupted": EXIT_STATUS_CORRUPTED}

RETIRED_SUBCOMMANDS = ("begin", "rollback", "complete")

RECOVERY_GUIDANCE = """\
安全替代流程（普通和已接管的包相同）：
  1) 先协调受影响的精确路径，备份其更新前工作区内容、权限和存在状态到
     私有的仓库外位置；不要擅自暂存、提交或更改索引。
  2) 中断时核对更新后文件、已暂存和未暂存差异；只恢复确定属于本次更新
     且没有并发改动的路径或片段，不能确认时保留内容并人工协调。
  3) 历史 checkpoint 的有效 snapshot 是更新前实际文本，可能未入 Git；
     损坏记录不可直接信任。先备份、审阅并确认保留或恢复，再移除该文件；
     索引另行检查，不要擅自改动。完整操作见 references/stages/update.md。
"""

RETIRED_NOTICE = "此子命令已退役：CLI 不再写任何状态；请使用路径范围内的安全恢复流程。\n\n"

EPILOG = f"""\
exit codes:
  0  success; status: no unresolved update checkpoint
  1  operational failure or refusal (details on stderr)
  2  usage error; retired subcommands ({", ".join(RETIRED_SUBCOMMANDS)}) also
     exit 2 after printing the path-scoped recovery guidance
  3  status: active checkpoint found (interrupted update)
  4  status: corrupted checkpoint found (manual recovery required)
"""

RETIRED_EPILOG = RETIRED_NOTICE + RECOVERY_GUIDANCE + "\n" + EPILOG


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
        help=f"(retired) {old_help}; exits 2 with path-scoped recovery guidance",
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
        description="Recognize historical update-stage checkpoints (read-only; path-scoped recovery).",
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
        f"error: `{command}` 已退役：不再写 update-checkpoint.json，请使用路径范围内的安全恢复流程。",
        file=sys.stderr,
    )
    print(RECOVERY_GUIDANCE, file=sys.stderr, end="")
    return 2


def _status_line(slug: str, status: CheckpointStatus) -> str:
    slug = checkpoint_display_text(slug)
    if status.state == "active" and status.checkpoint is not None:
        return (
            f"{slug}：存在未完成 update checkpoint（历史遗留，机制已退役）"
            f"（意图：{checkpoint_display_text(status.checkpoint.intent)}；"
            f"开始于：{checkpoint_display_text(status.checkpoint.created_at)}）；"
            "先备份并审阅有效 snapshot，确认保留或路径级恢复后删除该文件；见 references/stages/update.md"
        )
    if status.state == "corrupted":
        return (
            f"{slug}：存在未完成 update checkpoint，且记录已损坏，须保留并人工核对"
            f"可信备份或 Git 历史（{checkpoint_display_text(status.error)}）"
        )
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

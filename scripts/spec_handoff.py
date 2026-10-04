#!/usr/bin/env python3
# scripts/spec_handoff.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Session-boundary handoff CLI for the optional package ``handoff.md``.

Wraps ``handoff_support`` primitives into the operator-facing commands:

  - ``update``   regenerate the snapshot and append one entry (pause /
                 takeover / lane-end / escalation); creates the file on
                 first use;
  - ``show``     render the snapshot, the latest (or ``--entry N``) entry,
                 and the freshness verdict for a resuming collaborator;
  - ``validate`` re-run the structural validation used by the check gate.

Exit codes are part of the contract:

  - ``0`` success;
  - ``1`` operational refusal or failure (missing package, unreadable file,
         invalid vocabulary, structural validation failure);
  - ``2`` usage error (argparse).

Contract output goes to stdout (markdown, or JSON with ``--format json``);
diagnostics go to stderr and leave package state untouched. ``update`` writes
exactly one file: ``handoff.md`` inside the package directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from handoff_support import (
    COLLAB_MODES,
    ENTRY_EVENTS,
    ENTRY_STATUSES,
    HandoffError,
    append_entry,
    freshness,
    handoff_brief,
    parse_handoff,
    read_handoff,
    validate_handoff,
)
from spec_package_support import (
    add_specs_dir_arg,
    read_regular_text,
    resolve_specs_child,
    resolve_specs_root,
    validate_slug,
)

EXIT_OK = 0
EXIT_FAILURE = 1

EPILOG = f"""\
collab modes:  {"/".join(COLLAB_MODES)}
events:        {"/".join(ENTRY_EVENTS)}
statuses:      {"/".join(ENTRY_STATUSES)}
exit codes:
  0  success
  1  operational failure or refusal (details on stderr)
  2  usage error
"""


def _add_shared_args(
    parser: argparse.ArgumentParser,
    *,
    positional_friendly: bool = False,
) -> None:
    if positional_friendly:
        # SUPPRESS keeps a subparser from clobbering top-level values while
        # still accepting the flags after the subcommand (the repo's
        # flag-at-the-end invocation style).
        parser.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        parser.add_argument("--specs-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        parser.add_argument("--slug", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return
    parser.add_argument(
        "--root",
        default=".",
        help="Project root containing the .spec directory (default: current directory)",
    )
    add_specs_dir_arg(parser)
    parser.add_argument(
        "--slug",
        required=True,
        help="Development Record slug owning the handoff document",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Maintain the session-boundary handoff document of a task package.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root containing the .spec directory (default: current directory)",
    )
    add_specs_dir_arg(parser)
    parser.add_argument("--slug", help="Development Record slug owning the handoff document")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update = subparsers.add_parser(
        "update",
        help="Regenerate the snapshot and append one entry",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_shared_args(update, positional_friendly=True)
    update.add_argument(
        "--collab",
        choices=COLLAB_MODES,
        help="Collaboration mode (default: carry over from the latest entry, else solo)",
    )
    update.add_argument(
        "--event",
        choices=tuple(event for event in ENTRY_EVENTS if event != "close"),
        default="pause",
        help="Boundary event that triggers this entry (default: pause); close is reserved for done --archive",
    )
    update.add_argument("--actor", help="Who is handing off (default: carry over, else main)")
    update.add_argument(
        "--status",
        choices=ENTRY_STATUSES,
        help="Entry status (default: blocked-on-human for escalation, else in-progress)",
    )
    update.add_argument(
        "--note",
        default="",
        help="One-line context for the 上下文 slot (at most 4000 characters; longer narrative belongs in the slots)",
    )

    show = subparsers.add_parser(
        "show",
        help="Render the snapshot and an entry for the resuming side",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_shared_args(show, positional_friendly=True)
    show.add_argument("--entry", type=int, help="Show this entry number instead of the latest")
    show.add_argument("--format", choices=("markdown", "json"), default="markdown")

    validate = subparsers.add_parser(
        "validate",
        help="Validate handoff structure (used by the check gate)",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_shared_args(validate, positional_friendly=True)

    return parser.parse_args(argv)


def _load_package(root: Path, args: argparse.Namespace, *, allow_archived: bool = False) -> tuple:
    """Resolve slug/paths and read the triad; shared by all subcommands.

    Read-only subcommands fall back to the archive location so a frozen
    handoff stays inspectable after ``done --archive``; ``update`` never
    resolves into the archive (a closed log must not grow).
    """
    try:
        slug = validate_slug(args.slug.strip())
    except ValueError as exc:
        raise HandoffError(str(exc)) from exc
    try:
        specs_root = resolve_specs_root(root, args.specs_dir)
        active_dir = resolve_specs_child(specs_root, "specs", slug)
        archived_dir = resolve_specs_child(specs_root, "specs", "archive", slug)
    except ValueError as exc:
        raise HandoffError(str(exc)) from exc
    package_dir = active_dir
    if allow_archived and not active_dir.is_dir() and archived_dir.is_dir():
        package_dir = archived_dir
    spec_path = package_dir / "spec.md"
    tasks_path = package_dir / "tasks.md"
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise HandoffError(f"task package not found or not a directory: {package_dir}")
    for path in (spec_path, tasks_path):
        if not path.is_file():
            raise HandoffError(f"required file missing: {path}")
    try:
        spec_content = read_regular_text(spec_path)
        tasks_content = read_regular_text(tasks_path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise HandoffError(f"invalid task package file: {exc}") from exc
    return slug, package_dir, spec_content, tasks_content


def _command_update(root: Path, args: argparse.Namespace) -> int:
    slug, package_dir, spec_content, tasks_content = _load_package(root, args)
    if len(args.note) > 4000:
        print(
            "error: --note exceeds 4000 characters; write longer narrative directly into the entry slots",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    try:
        number = append_entry(
            package_dir,
            root,
            slug,
            tasks_content,
            spec_content,
            event=args.event,
            collab=args.collab,
            actor=args.actor,
            status=args.status,
            note=args.note,
        )
    except HandoffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"handoff updated: entry {number} appended ({args.event}); snapshot regenerated")
    return EXIT_OK


def _command_show(root: Path, args: argparse.Namespace) -> int:
    slug, package_dir, _spec_content, tasks_content = _load_package(root, args, allow_archived=True)
    content = read_handoff(package_dir)
    if content is None:
        print(f"error: no handoff document in package: {package_dir}", file=sys.stderr)
        return EXIT_FAILURE
    doc = parse_handoff(content)
    if not doc.entries:
        print("error: handoff document has no entries", file=sys.stderr)
        return EXIT_FAILURE
    label, detail = freshness(doc, root, tasks_content)
    entry = doc.latest
    if args.entry is not None:
        entry = next((item for item in doc.entries if item.number == args.entry), None)
        if entry is None:
            print(f"error: entry not found: {args.entry}", file=sys.stderr)
            return EXIT_FAILURE
    if args.format == "json":
        payload = {
            "slug": slug,
            "snapshot": doc.snapshot,
            "freshness": label,
            "freshnessDetail": detail,
            "entry": {
                "number": entry.number,
                "timestamp": entry.timestamp,
                **{key: entry.fields.get(key, "") for key in ("event", "collab", "actor", "status")},
            },
        }
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK
    heading = (
        f"### [{entry.timestamp}]"
        f" event={entry.fields.get('event', '')}"
        f" collab={entry.fields.get('collab', '')}"
        f" actor={entry.fields.get('actor', '')}"
    )
    lines = [
        f"# handoff - {slug}",
        "",
        f"- 新鲜度：{label}（{detail}）",
        f"- 条目数：{len(doc.entries)}",
        "",
        heading,
        "",
        "```yaml",
        *(f"{key}: {entry.fields.get(key, '')}" for key in ("entry", "event", "collab", "actor", "status")),
        "```",
    ]
    for name, body in entry.sections.items():
        lines.extend(["", f"#### {name}", body or "（空）"])
    print("\n".join(lines))
    return EXIT_OK


def _command_validate(root: Path, args: argparse.Namespace) -> int:
    _slug, package_dir, _spec_content, _tasks_content = _load_package(root, args, allow_archived=True)
    content = read_handoff(package_dir)
    if content is None:
        print("error: no handoff document in package; nothing to validate", file=sys.stderr)
        return EXIT_FAILURE
    errors = validate_handoff(content)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    brief = handoff_brief(package_dir, root, _tasks_content)
    print(f"handoff valid: {brief or 'structure ok'}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not getattr(args, "slug", None):
        print("error: --slug is required", file=sys.stderr)
        return EXIT_FAILURE
    root = Path(args.root).resolve()
    handlers = {
        "update": _command_update,
        "show": _command_show,
        "validate": _command_validate,
    }
    try:
        return handlers[args.command](root, args)
    except HandoffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())

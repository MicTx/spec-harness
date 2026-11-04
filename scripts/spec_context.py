#!/usr/bin/env python3
# scripts/spec_context.py
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Internal CLI for cross-agent session context discovery and bounded reads.

  - ``discover`` emits a factual candidate list (schema_version 1) for the
    workspace, including none/partial outcomes; exit 0.
  - ``read`` exports sanitized context for one scoped session id resolved
    anew through discovery; refused/unavailable/unknown id exits 1 with a
    classified diagnostic on stderr.

Usage errors exit 2. Output is untrusted history marked context-only; the
CLI never makes resume/new task decisions and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys

sys.dont_write_bytecode = True

import session_adapters as adapters  # noqa: E402
import session_context_support as support  # noqa: E402

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spec_context",
        description="Read-only cross-agent session context discovery and export.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover", help="list scoped session candidates")
    discover.add_argument("--root", default=".")
    discover.add_argument("--home", default=None)
    discover.add_argument("--client", default="unknown")
    discover.add_argument("--current-session", default=None)
    discover.add_argument("--limit", type=int, default=adapters.DEFAULT_CANDIDATE_LIMIT)
    discover.add_argument("--format", choices=("json", "markdown"), default="json")

    read = sub.add_parser("read", help="export one scoped session's context")
    read.add_argument("--root", default=".")
    read.add_argument("--home", default=None)
    read.add_argument("--agent", required=True, choices=adapters.AGENTS)
    read.add_argument("--session", required=True)
    read.add_argument("--max-messages", type=int, default=adapters.DEFAULT_MESSAGE_LIMIT)
    read.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def _markdown_discover(payload: dict) -> str:
    ws = payload["workspace"]
    lines = [
        "# Session context discovery",
        "",
        f"- workspace: {ws['root']}",
        f"- git: {ws['git']} branch: {ws['branch']} head: {ws['head']}",
        f"- status: {payload['status']} complete: {payload['complete']}",
        "",
        "## Sources",
    ]
    for source in payload["sources"]:
        lines.append(f"- {source['agent']}: {source['status']} ({source['reason_code']}) complete={source['complete']}")
    lines.append("")
    lines.append("## Candidates")
    for cand in payload["candidates"]:
        title = cand["title"] or ""
        lines.append(
            f"- [{cand['agent']}] {cand['session_id']} {title}"
            f" updated={cand['updated_at']} relation={cand['relation']} has_user={cand['has_user']}"
            f" complete={cand['complete']}"
        )
        lines.append(
            f"  path={cand.get('project_path')} association={cand.get('association')}"
            f"/{cand.get('association_confidence')} format={cand.get('source_format')}"
        )
    return "\n".join(lines) + "\n"


def _markdown_read(payload: dict) -> str:
    lines = [
        "# Session context",
        "",
        f"- workspace: {payload['workspace']['root']}",
        f"- session: {payload['candidate']['agent']}/{payload['candidate']['session_id']}",
        f"- freshness: {payload['freshness']} authority: {payload['authority']}",
        f"- complete: {payload['complete']} truncation: {payload['truncation']}",
        f"- truncation_reasons: {', '.join(payload.get('truncation_reasons') or []) or 'none'}",
        f"- project_path: {payload['candidate'].get('project_path')}"
        f" format: {payload['candidate'].get('source_format')}",
        f"- advisories: {', '.join(payload['advisories'])}",
        "",
        "## Messages",
    ]
    for message in payload["messages"]:
        lines.append(f"- ({message['role']}) [{message['record_id']}] {message['text']}")
    return "\n".join(lines) + "\n"


def _emit(payload: dict, fmt: str, renderer) -> None:
    if fmt == "markdown":
        sys.stdout.write(renderer(payload))
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")


def _diagnose(payload: dict) -> str:
    error = payload.get("error") or {}
    return f"error: {error.get('reason_code', payload.get('status'))}: {error.get('message', '')}"


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "read" and not adapters.valid_session_id(args.session):
        parser.error("--session must be a bounded ASCII session id")
    try:
        if args.command == "discover":
            payload = support.discover(
                args.root,
                home=args.home,
                client=args.client,
                current_session=args.current_session,
                limit=args.limit,
            )
            _emit(payload, args.format, _markdown_discover)
            return EXIT_OK
        payload = support.read(
            args.root,
            agent=args.agent,
            session_id=args.session,
            home=args.home,
            max_messages=args.max_messages,
        )
    except support.SessionContextError as exc:
        sys.stderr.write(f"error: {exc.reason_code}: {exc}\n")
        return EXIT_FAILURE
    if payload["status"] != "ok":
        if args.format == "json":
            _emit(payload, "json", None)
        sys.stderr.write(_diagnose(payload) + "\n")
        return EXIT_FAILURE
    _emit(payload, args.format, _markdown_read)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# scripts/autoplan_gate.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Autoplan cluster facts and readiness gate for ``/spec:autoplan``.

Subcommands:

  - ``discover``  scan the project for the planning-document cluster and
                  report per-document checkbox facts plus the run mode
                  (project start vs phase boundary);
  - ``gate``      enforce the structural readiness invariants the cluster
                  must satisfy before ``/spec:autorun`` can consume it.

Exit codes are part of the contract:

  - ``0`` success (for ``gate``: the cluster is autorun-ready);
  - ``1`` operational refusal or failure (bad root, unreadable documents,
         broken ``--plan`` paths);
  - ``2`` usage error (argparse);
  - ``3`` the cluster is not autorun-ready (fail closed: no qualifying
         planning document, every feature already checked, unresolvable
         detail references, or no goal statement).

Contract output goes to stdout (text, or JSON with ``--format json``);
diagnostics go to stderr. The gate reads only and never writes project
state; planning-document discovery and checkbox counting are reused from
``autorun_spawn.py`` so both commands share one truth. Semantic
consistency (business/data/flow contradictions across documents) belongs
to the reviewer sidecar pass of ``/spec:autoplan``, not this script.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from autorun_spawn import (
    PLAN_ARCHIVE_DIR_NAME,
    PLAN_ROOT,
    AutorunError,
    NoPlanError,
    count_features,
    discover_plan_docs,
)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NOT_READY = 3
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
ANY_HEADING = re.compile(r"^#{1,6}\s")
GOAL_HEADING = re.compile(r"^#{1,6}\s*[^\n]*(?:goal|目标)", re.IGNORECASE)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
SKIP_LINK_PREFIXES = ("http://", "https://", "mailto:", "#", "/")


class AutoplanError(AutorunError):
    """Operational refusal or failure with a user-facing message."""


class NotReadyError(AutoplanError):
    """The planning cluster is not autorun-ready (exit 3)."""


def _resolve_root(raw_root: str) -> Path:
    root = Path(raw_root).expanduser().resolve()
    if not root.is_dir():
        raise AutoplanError(f"project root is not a directory: {root}")
    return root


def _under_root(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)) if _under_root(root, path) else str(path)


def _slugify_heading(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text)
    return text


def _heading_slugs(text: str) -> Set[str]:
    slugs: Set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            slugs.add(_slugify_heading(match.group(1)))
    return slugs


def _has_goal_section(text: str) -> bool:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not GOAL_HEADING.match(line):
            continue
        body: List[str] = []
        for follow in lines[index + 1 :]:
            if ANY_HEADING.match(follow):
                break
            body.append(follow.strip())
        if any(body):
            return True
    return False


def _reference_facts(root: Path, docs: List[Path]) -> Tuple[List[Dict[str, str]], Set[str]]:
    """Return (broken markdown detail references, referenced relative paths).

    Only relative ``.md`` links are validated: images and absolute URLs are
    skipped, and a reference resolves only when the target file exists under
    the project root and the fragment (when present) matches a slugified
    heading of the target.
    """
    broken: List[Dict[str, str]] = []
    referenced: Set[str] = set()
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for raw in MD_LINK.findall(text):
            if raw.startswith(SKIP_LINK_PREFIXES):
                continue
            target, _, fragment = raw.partition("#")
            if not target or not target.lower().endswith(".md"):
                continue
            candidate = (doc.parent / target).resolve()
            if not _under_root(root, candidate):
                broken.append({"source": _rel(root, doc), "target": raw, "reason": "escapes the project root"})
                continue
            if not candidate.is_file():
                broken.append({"source": _rel(root, doc), "target": raw, "reason": "target file does not exist"})
                continue
            referenced.add(candidate.relative_to(root).as_posix())
            if fragment and fragment not in _heading_slugs(candidate.read_text(encoding="utf-8")):
                broken.append({"source": _rel(root, doc), "target": raw, "reason": "anchor does not match any heading"})
    return broken, referenced


def _orphan_details(root: Path, docs: List[Path], referenced: Set[str]) -> List[str]:
    """Live detail documents under the planning root that no payload doc links.

    Candidates are every markdown file under ``.spec/plans/`` except the
    index seed (``README.md``): cluster members (``<slug>/NN-<slug>.md``)
    and top-level single-round plans alike. The archive directory is
    history, not detail, and is skipped wholesale.
    """
    orphans: List[str] = []
    plan_root = root / PLAN_ROOT
    if not plan_root.is_dir():
        return orphans
    for path in sorted(plan_root.rglob("*.md")):
        rel_path = path.relative_to(root).as_posix()
        below_root = rel_path[len(PLAN_ROOT) + 1 :]
        segments = below_root.split("/")
        if PLAN_ARCHIVE_DIR_NAME in segments[:-1]:
            continue
        if below_root == "README.md":
            continue
        if path in docs:
            continue
        if rel_path not in referenced:
            orphans.append(rel_path)
    return orphans


def discover_payload(root: Path, explicit: Optional[str]) -> Dict[str, object]:
    docs, scanned = discover_plan_docs(root, explicit)
    doc_reports: List[Dict[str, object]] = []
    totals = {"checked": 0, "unchecked": 0, "total": 0}
    for doc in docs:
        counts = count_features(doc.read_text(encoding="utf-8"))
        doc_reports.append({"path": _rel(root, doc), **counts})
        for key in totals:
            totals[key] += counts[key]  # type: ignore[operator]
    return {
        "project": str(root),
        "run_mode": "phase-boundary" if doc_reports else "project-start",
        "docs": doc_reports,
        "totals": totals,
        "scanned": scanned,
    }


def gate_payload(root: Path, explicit: Optional[str]) -> Dict[str, object]:
    docs, _scanned = discover_plan_docs(root, explicit)
    checks: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    totals = {"checked": 0, "unchecked": 0, "total": 0}
    doc_reports: List[Dict[str, object]] = []
    for doc in docs:
        counts = count_features(doc.read_text(encoding="utf-8"))
        doc_reports.append({"path": _rel(root, doc), **counts})
        for key in totals:
            totals[key] += counts[key]  # type: ignore[operator]

    if not docs:
        checks.append({"id": "cluster-present", "status": "fail", "detail": "no qualifying planning document"})
    else:
        checks.append(
            {"id": "cluster-present", "status": "pass", "detail": f"{len(docs)} qualifying planning document(s)"}
        )
        unchecked = totals["unchecked"]
        checks.append(
            {
                "id": "has-unchecked-features",
                "status": "pass" if unchecked else "fail",
                "detail": f"{unchecked} unchecked feature(s)"
                if unchecked
                else "every feature checkbox is already checked",
            }
        )
        broken, referenced = _reference_facts(root, docs)
        if broken:
            detail = "; ".join(f"{item['source']} -> {item['target']} ({item['reason']})" for item in broken)
            checks.append({"id": "detail-references-resolve", "status": "fail", "detail": detail})
        else:
            checks.append(
                {
                    "id": "detail-references-resolve",
                    "status": "pass",
                    "detail": "all markdown detail references resolve",
                }
            )
        has_goal = any(_has_goal_section(doc.read_text(encoding="utf-8")) for doc in docs)
        checks.append(
            {
                "id": "goal-section-present",
                "status": "pass" if has_goal else "fail",
                "detail": (
                    "a qualifying document declares a non-empty goal"
                    if has_goal
                    else "no qualifying document has a non-empty Goal/目标 section"
                ),
            }
        )
        for rel_path in _orphan_details(root, docs, referenced):
            warnings.append({"id": "orphan-detail-doc", "path": rel_path})

    ready = all(check["status"] == "pass" for check in checks)
    return {
        "project": str(root),
        "ready": ready,
        "checks": checks,
        "warnings": warnings,
        "docs": doc_reports,
        "totals": totals,
    }


def _render_discover(payload: Dict[str, object]) -> str:
    lines = [f"project: {payload['project']}", f"run-mode: {payload['run_mode']}"]
    for doc in payload["docs"]:  # type: ignore[union-attr]
        lines.append("- {}: {}/{} checked".format(doc["path"], doc["checked"], doc["total"]))  # type: ignore[index]
    totals = payload["totals"]
    if totals["total"]:  # type: ignore[index]
        lines.append(
            "features: {}/{} checked ({} unchecked)".format(
                totals["checked"],
                totals["total"],
                totals["unchecked"],  # type: ignore[index]
            )
        )
    else:
        lines.append("cluster: none found (project-start run)")
    return "\n".join(lines)


def _render_gate(payload: Dict[str, object]) -> str:
    totals = payload["totals"]
    lines = [f"project: {payload['project']}", "ready: {}".format("yes" if payload["ready"] else "no")]
    for check in payload["checks"]:  # type: ignore[union-attr]
        lines.append("- {}: {} ({})".format(check["id"], check["status"], check["detail"]))
    for warning in payload["warnings"]:  # type: ignore[union-attr]
        lines.append("- warning {}: not referenced by any planning document".format(warning["path"]))
    if totals["total"]:  # type: ignore[index]
        lines.append(
            "features: {}/{} checked ({} unchecked)".format(
                totals["checked"],
                totals["total"],
                totals["unchecked"],  # type: ignore[index]
            )
        )
    return "\n".join(lines)


def _emit(payload: Dict[str, object], render, use_json: bool) -> None:
    if use_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(payload))


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=".", help="project root (default: current directory)")
    parser.add_argument(
        "--plan",
        help="comma-separated planning document paths (default: scan the canonical .spec/plans/ root)",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: text)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoplan_gate.py",
        description="Autoplan cluster facts and readiness gate for /spec:autoplan.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    discover_parser = subparsers.add_parser("discover", help="scan the planning cluster and report checkbox facts")
    _add_args(discover_parser)

    gate_parser = subparsers.add_parser("gate", help="enforce the structural readiness invariants of the cluster")
    _add_args(gate_parser)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = _resolve_root(args.root)
        if args.subcommand == "discover":
            _emit(discover_payload(root, args.plan), _render_discover, args.format == "json")
        else:
            payload = gate_payload(root, args.plan)
            use_json = args.format == "json"
            if not payload["ready"]:
                failed = [check["id"] for check in payload["checks"] if check["status"] == "fail"]  # type: ignore[union-attr]
                _emit(payload, _render_gate, use_json)
                raise NotReadyError("cluster not autorun-ready: " + ", ".join(failed))
            _emit(payload, _render_gate, use_json)
    except NotReadyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_READY
    except NoPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_READY
    except AutorunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

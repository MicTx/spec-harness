"""Final-tree verification, archive, summary and script-owned delivery commit."""

from __future__ import annotations

import shutil
from pathlib import Path

import evidence
import gate
import gitx
from memory import lesson_proposals, lessons_adjust
from package import (
    _baseline_dirty,
    _unstage_baseline_dirty,
    git_add_paths,
    now,
    parse_charter,
    parse_ledger,
    parse_tasks,
    read_package,
)


def build_summary(root: Path, slug: str) -> str:
    package, spec, tasks = read_package(root, slug)
    records = parse_tasks(tasks)
    decisions = parse_ledger(spec, "Decisions", "D")
    parked = parse_ledger(spec, "Parked", "P")
    emergent = [record.title for record in records if record.origin == "emergent"]
    proposals = lesson_proposals(root)
    lines = [
        f"# {slug} - Completion Summary",
        "",
        "## Delivery",
        *[f"- {record.title}" for record in records if record.checked],
        "",
        "## Evidence",
        "- Final-tree verifies passed; see runs.jsonl.",
        "",
        "## Decisions",
    ]
    lines.extend(f"- {item['id']} | {item['text']}" for item in decisions)
    lines.extend(
        [
            "",
            "## Parked",
            *(f"- {item['id']} | {item['text']}" for item in parked),
            "",
            "## Emergent tasks",
            *(f"- {item}" for item in emergent),
            "",
            "## Lessons",
            "- See .spec/lessons.md for retained project lessons.",
            *(f"- Promote: {line}" for line in proposals),
            f"- Archived: {now()}",
            "",
            "## Git",
            f"- Commit interval: {gitx.git(root, 'rev-parse', 'HEAD').stdout.strip()}",
        ]
    )
    return "\n".join(lines) + "\n"


def finish(root: Path, slug: str, lesson_used: list[str] | None = None, lesson_wrong: list[str] | None = None) -> dict:
    evidence.run_tasks(root, slug, final=True)
    result = gate.compute(root, slug, final=True)
    if not result["passed"]:
        raise ValueError(
            "delivery gate failed: " + "; ".join(result["errors"] + [item["id"] for item in result["parked"]])
        )
    package, spec, _tasks = read_package(root, slug)
    records = parse_tasks(_tasks)
    patterns = [pattern for record in records for pattern in record.globs]
    baseline = _baseline_dirty(root, package)
    (package / "completion-summary.md").write_text(build_summary(root, slug), encoding="utf-8")
    destination = package.parent / "archive" / slug
    if destination.exists():
        raise ValueError(f"archive exists: {destination}")
    shutil.move(str(package), str(destination))
    # The package may already be tracked by a wip checkpoint commit; a plain
    # `git add` never records its deletion, leaving the active spec visible to
    # --index hook checks on a git-less checkout-index tree.
    gitx.git(root, "rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", f".spec/specs/{slug}", check=False)
    lessons_adjust(root, lesson_used or [], lesson_wrong or [])
    paths = [pattern.removeprefix("new:") for pattern in patterns]
    paths.extend(
        [
            f".spec/specs/archive/{slug}",
            ".spec/charter.md",
            ".spec/lessons.md",
        ]
    )
    git_add_paths(root, paths)
    _unstage_baseline_dirty(root, baseline)
    if gitx.git(root, "diff", "--cached", "--quiet", check=False).returncode == 0:
        raise ValueError("nothing to deliver after final verification")
    gitx.git(root, "commit", "-m", f"feat(spec): deliver {slug}\n\nSpec: {slug}")
    values = parse_charter(spec)
    return {"slug": slug, "archive": str(destination), "head": gitx.head(root), "finish": values.get("V4", "commit")}

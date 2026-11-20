"""Final-tree verification, archive, summary and script-owned delivery commit."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import evidence
import gate
import gitx
import local_evidence
import push
import ui_review
from memory import lessons_by_id, validate_enforce
from package import (
    _baseline_dirty,
    _unstage_baseline_dirty,
    append_run,
    git_add_paths,
    now,
    parse_charter,
    parse_ledger,
    parse_tasks,
    read_package,
    runs,
)

ENFORCE_RE = re.compile(r"(?:^| \| )enforce: (.+)$")


def recorded_lessons(package: Path) -> list[dict]:
    """Return this package's `kind: learn` rows in append order."""
    return [
        record for record in runs(package) if record.get("kind") == "learn" and isinstance(record.get("lesson"), str)
    ]


def lesson_ids_for(package: Path, lesson_used: list[str] | None) -> list[str]:
    """Union the recorded L-ids with --lesson-used, refusing a dropped lesson (F2)."""
    recorded = [record["lesson"] for record in recorded_lessons(package)]
    used = list(dict.fromkeys(lesson_used or []))
    missing = [lesson for lesson in recorded if lesson not in used]
    if missing:
        raise ValueError(
            "this package recorded lessons "
            + ", ".join(recorded)
            + "; --lesson-used must name every recorded lesson (missing: "
            + ", ".join(missing)
            + ")"
        )
    return list(dict.fromkeys([*recorded, *used]))


def lesson_lines(root: Path, package: Path, lesson_ids: list[str]) -> list[str]:
    """Render archive rows from the live ledger, falling back to the learn record."""
    ledger = lessons_by_id(root)
    recorded = {record["lesson"]: record for record in recorded_lessons(package)}
    lines = []
    for lesson in lesson_ids:
        line = ledger.get(lesson)
        enforce = (recorded.get(lesson) or {}).get("enforce") or ""
        if line is None:
            if not enforce:
                raise ValueError(f"unknown lesson id: {lesson}")
            line = f"{lesson} | enforce: {enforce}"
        elif enforce and not ENFORCE_RE.search(line):
            line = f"{line} | enforce: {enforce}"
        lines.append(line)
    return lines


def lesson_archive(root: Path, package: Path, lesson_used: list[str] | None) -> tuple[list[str], list[str]]:
    """Validate the F2 coverage rule and every enforce target before archiving."""
    lesson_ids = lesson_ids_for(package, lesson_used)
    lines = lesson_lines(root, package, lesson_ids)
    for line in lines:
        match = ENFORCE_RE.search(line)
        if match:
            validate_enforce(root, match.group(1).strip(), package)
    return lesson_ids, lines


def build_summary(root: Path, slug: str, lesson_ids: list[str]) -> str:
    package, spec, tasks = read_package(root, slug)
    records = parse_tasks(tasks)
    decisions = parse_ledger(spec, "Decisions", "D")
    parked = parse_ledger(spec, "Parked", "P")
    emergent = [record.title for record in records if record.origin == "emergent"]
    lessons = lesson_lines(root, package, lesson_ids)
    lines = [
        f"# {slug} - Completion Summary",
        "",
        "## Delivery",
        *[f"- {record.title}" for record in records if record.checked],
        "",
        "## Evidence",
        "- Final-tree verifies passed; see runs.jsonl.",
        "",
        "## UI Review",
        *ui_review.summary_lines(root, slug),
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
            *([f"- {line}" for line in lessons] or ["- No retained lessons."]),
            f"- Archived: {now()}",
            "",
            "## Git",
            f"- Commit interval: {gitx.git(root, 'rev-parse', 'HEAD').stdout.strip()}",
        ]
    )
    return "\n".join(lines) + "\n"


def finish(root: Path, slug: str, lesson_used: list[str] | None = None) -> dict:
    package, _spec, _tasks = read_package(root, slug)
    # F2 and the enforce targets are checked before the final re-verify so a
    # dangling lesson cannot ride a green tree into the archive.
    lesson_ids, _lines = lesson_archive(root, package, lesson_used)
    evidence.run_tasks(root, slug, final=True)
    result = gate.compute(root, slug, final=True)
    if not result["passed"]:
        raise ValueError(
            "delivery gate failed: " + "; ".join(result["errors"] + [item["id"] for item in result["parked"]])
        )
    package, spec, _tasks = read_package(root, slug)
    # The delivery gate passed on the final tree; record that fact before the
    # package leaves the active tree so the archived ledger ends green instead
    # of showing only pre-refresh stale-evidence failures.
    append_run(
        package,
        {
            "kind": "gate",
            "passed": True,
            "errors": [],
            "parked": [],
            "final": True,
            "ts": now(),
        },
    )
    records = parse_tasks(_tasks)
    patterns = [pattern for record in records for pattern in record.globs]
    baseline = _baseline_dirty(root, package)
    values = parse_charter(spec)
    if values.get("V4", "commit") in {"merge", "push"}:
        push.preflight(root, slug, spec, patterns)
    local = local_evidence.is_local(root)
    if local:
        local_evidence.preflight_archive(root, slug)
        local_evidence.ensure(root)
        # Re-probe after ensure: a clone inherits local mode from .gitignore
        # without a marker, and linked worktrees must not fall back to tracked
        # evidence just because no marker existed at entry.
        local = local_evidence.is_local(root)
    if local:
        # Retire tracked records before moving the package; errors leave the
        # active package in place, rather than stranding it in the archive.
        local_evidence.untrack(root)
    (package / "completion-summary.md").write_text(build_summary(root, slug, lesson_ids), encoding="utf-8")
    destination = package.parent / "archive" / slug
    if destination.exists():
        raise ValueError(f"archive exists: {destination}")
    shutil.move(str(package), str(destination))
    if not local:
        # The package may already be tracked by a wip checkpoint commit; a plain
        # `git add` never records its deletion, leaving the active spec visible to
        # --index hook checks on a git-less checkout-index tree.
        gitx.git(root, "rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", f".spec/specs/{slug}", check=False)
        # The package move deletes the active directory from both the worktree and
        # the index. Stage the spec tree as a unit so Git records the deletions and
        # archive additions together. Preflight already rejected unrelated dirt.
        gitx.git(root, "add", "-A", "--", ".spec/specs")
    paths = [pattern.removeprefix("new:") for pattern in patterns]
    paths.extend(
        [
            f".spec/specs/archive/{slug}",
            ".spec/charter.md",
        ]
    )
    remaining = sorted(
        {
            path.removeprefix("new:")
            for path in paths
            if not path.removeprefix("new:").startswith(".spec/specs/") and (root / path.removeprefix("new:")).exists()
        }
    )
    if local:
        git_add_paths(root, list(patterns))
    elif remaining:
        gitx.git(root, "add", "-A", "--", *remaining, check=False)
    _unstage_baseline_dirty(root, baseline, tuple(patterns))
    empty = gitx.git(root, "diff", "--cached", "--quiet", check=False).returncode == 0
    if empty and not local:
        raise ValueError("nothing to deliver after final verification")
    extra = ["--allow-empty"] if empty else []
    try:
        gitx.git(root, "commit", *extra, "-m", f"feat(spec): deliver {slug}\n\nSpec: {slug}")
    except Exception:
        if local:
            shutil.move(str(destination), str(package))
        raise
    result = {"slug": slug, "archive": str(destination), "head": gitx.head(root), "finish": values.get("V4", "commit")}
    if local:
        append_run(destination, {"kind": "delivery", "head": result["head"], "ts": now()})
        result["local_archive"] = local_evidence.sync_archive(root, destination, slug)
    return result

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
    append_run,
    checkpoint_paths,
    effective_finish,
    gate_actions,
    gate_recovery,
    git_add_paths,
    latest_gate,
    now,
    park_decisions,
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
    recorded = [record["lesson"] for record in recorded_lessons(package)]
    return list(dict.fromkeys([*recorded, *(lesson_used or [])]))


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


def build_summary(root: Path, slug: str, lesson_ids: list[str], result: dict) -> str:
    package, spec, tasks = read_package(root, slug)
    records = parse_tasks(tasks)
    decisions = parse_ledger(spec, "Decisions", "D")
    parked = parse_ledger(spec, "Parked", "P")
    decisions += [
        dict(id=row["id"], text=f"{row['choice']}: {row['reason']} | by: {row['by']}")
        for row in park_decisions(package).values()
    ]
    emergent = [record.title for record in records if record.origin == "emergent"]
    lessons = lesson_lines(root, package, lesson_ids)
    lines = [
        f"# {slug} - Completion Summary",
        "",
        "## Delivery",
        f"- Authorized finish: {result['finish']} (requested: {result['requested_finish']}).",
        *[f"- {record.title}" for record in records if record.checked],
        "",
        "## Evidence",
        "- Final-tree verifies passed; see runs.jsonl.",
        "",
        "## UI Review",
        *ui_review.summary_lines(result["ui_review"]),
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


def finish(root: Path, slug: str, lesson_used: list[str] | None = None, *, retry: bool = False) -> dict:
    package, spec, tasks = read_package(root, slug)
    previous = gate_actions(latest_gate(package, execution=True))
    result = gate.compute(root, slug)
    primary = next((item for item in previous if item["code"] != "recovery-exhausted"), None)
    if primary and gate_recovery(package, primary)["exhausted"]:
        gate.add(
            result,
            gate.finding(
                "recovery-exhausted", "recovery budget exhausted", primary["subject"], action="inspect-delivery"
            ),
        )
    gate.record(root, slug, result)
    if not gate.can_finish(result, retry=retry):
        return result
    lesson_ids, _ = lesson_archive(root, package, lesson_used)
    patterns = tuple(pattern for task in parse_tasks(tasks) for pattern in task.globs)
    delivery, requested = effective_finish(package, spec)
    evidence.run_tasks(root, slug, final=True)
    result = gate.compute(root, slug, final=True)
    if result["passed"] and delivery in {"merge", "push"}:
        try:
            push.preflight(root, slug, spec, list(patterns))
        except push.HandoffBlocked as exc:
            gate.add(
                result,
                gate.finding(
                    "handoff-blocked", str(exc), exc.subject, owner="user", action="choose-handoff", requested=requested
                ),
            )
    gate.record(root, slug, result, final=True)
    if not result["passed"]:
        return result
    _, local = checkpoint_paths(root, slug, patterns)
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
    result.update(finish=delivery, requested_finish=requested)
    (package / "completion-summary.md").write_text(build_summary(root, slug, lesson_ids, result), encoding="utf-8")
    destination = package.parent / "archive" / slug
    if destination.exists():
        raise ValueError(f"archive exists: {destination}")
    shutil.move(str(package), str(destination))
    try:
        paths = list(patterns)
        if not local:
            paths += [f".spec/specs/{slug}", f".spec/specs/archive/{slug}", ".spec/charter.md"]
        git_add_paths(root, paths)
        gitx.git(root, "commit", "--allow-empty", "-m", f"feat(spec): deliver {slug}\n\nSpec: {slug}")
    except Exception:
        shutil.move(str(destination), str(package))
        raise
    result.update(slug=slug, archive=str(destination), head=gitx.head(root))
    if local:
        append_run(destination, {"kind": "delivery", "head": result["head"], "ts": now()})
        result["local_archive"] = local_evidence.sync_archive(root, destination, slug)
    return result

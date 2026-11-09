#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Conservatively migrate active v1 packages to v2; dry-run unless --apply.

Checklist items must match a uniquely named task or an existing task-bound
criterion. Unmappable packages are left untouched for manual migration.
"""

from __future__ import annotations

import argparse
import difflib
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from spec_package_support import (
    SPEC_FORMAT_MARKER,
    SpecControlError,
    TaskRecord,
    active_package_slugs,
    add_specs_dir_arg,
    checklist_complete,
    parse_task_records,
    resolve_specs_child,
    resolve_specs_root,
    section_checkboxes_by_prefix,
)
from spec_verify import classify_verify, duplicate_verify_error

FORMAT_MARKER = "<!-- spec-format: 2 -->"
HEADING_SIX = re.compile(r"(?m)^## 6\.[^\r\n]*(?:\r?\n|$)")
NEXT_HEADING = re.compile(r"(?m)^#{1,2} ")
CHECKBOX = re.compile(r"^\s*-\s\[([^\]])\]\s+(.+?)\s*$")
TASK_REFERENCE = re.compile(r"\s*->\s*verify:\s*(task_\d{4,}|task-[a-z0-9][a-z0-9-]{0,58})\s*$")


@dataclass(frozen=True)
class Criterion:
    title: str
    task_id: str
    checked: bool


@dataclass(frozen=True)
class Migration:
    before: str
    after: str
    checklist: str | None


def _section_six(spec: str) -> tuple[int, int] | None:
    headings = list(HEADING_SIX.finditer(spec))
    if len(headings) > 1:
        raise ValueError("multiple §6 headings; resolve manually")
    if not headings:
        return None
    start = headings[0].start()
    next_heading = NEXT_HEADING.search(spec, headings[0].end())
    return start, next_heading.start() if next_heading else len(spec)


def _usable_task(task: TaskRecord) -> bool:
    if duplicate_verify_error(task.id, task.block_text):
        return False
    return task.executable and bool(classify_verify(task.verify))


def _criteria(spec: str, tasks: list[TaskRecord]) -> list[Criterion]:
    section = _section_six(spec)
    if section is None:
        return []
    known = {task.id: task for task in tasks}
    criteria: list[Criterion] = []
    for line in spec[section[0] : section[1]].splitlines():
        checkbox = CHECKBOX.match(line)
        if not checkbox:
            continue
        reference = TASK_REFERENCE.search(checkbox.group(2))
        if not reference or reference.group(1) not in known:
            raise ValueError("existing §6 checkbox lacks a valid task reference")
        task = known[reference.group(1)]
        if not _usable_task(task):
            raise ValueError(f"{task.id} has no usable boundary/verify")
        title = checkbox.group(2)[: reference.start()].strip()
        criteria.append(Criterion(title, task.id, checkbox.group(1).lower() == "x"))
    return criteria


def _legacy_items(checklist: str) -> list[tuple[str, bool]]:
    return [
        (match.group(2).strip(), match.group(1).lower() == "x")
        for line in checklist.splitlines()
        if (match := CHECKBOX.match(line))
    ]


def _map_to_task(title: str, tasks: list[TaskRecord]) -> TaskRecord:
    reference = TASK_REFERENCE.search(title)
    if reference:
        candidates = [task for task in tasks if task.id == reference.group(1)]
    else:
        candidates = [task for task in tasks if task.title == title]
    if len(candidates) != 1 or not _usable_task(candidates[0]):
        raise ValueError(f"checklist item has no unambiguous task/verify: {title}")
    return candidates[0]


def _map_to_existing_criterion(title: str, criteria: list[Criterion]) -> Criterion:
    reference = TASK_REFERENCE.search(title)
    if reference:
        label = title[: reference.start()].strip()
        candidates = [item for item in criteria if item.task_id == reference.group(1) and item.title == label]
    else:
        candidates = [item for item in criteria if item.title == title]
    if len(candidates) != 1:
        raise ValueError(f"checklist item has no equivalent existing §6 criterion: {title}")
    return candidates[0]


def _new_criteria(checklist: str, tasks: list[TaskRecord]) -> list[Criterion]:
    items = _legacy_items(checklist)
    if not items:
        raise ValueError("checklist has no acceptance items to migrate")
    legacy_passed = checklist_complete(checklist)
    result: list[Criterion] = []
    for title, checked in items:
        task = _map_to_task(title, tasks)
        reference = TASK_REFERENCE.search(title)
        clean_title = title[: reference.start()].strip() if reference else title
        result.append(Criterion(clean_title, task.id, checked and task.checked and legacy_passed))
    return result


def _check_existing(criteria: list[Criterion], checklist: str, tasks: list[TaskRecord]) -> None:
    items = _legacy_items(checklist)
    if not items:
        raise ValueError("checklist has no acceptance items to compare")
    legacy_passed = checklist_complete(checklist)
    known = {task.id: task for task in tasks}
    for title, checked in items:
        criterion = _map_to_existing_criterion(title, criteria)
        if criterion.checked and not (checked and legacy_passed and known[criterion.task_id].checked):
            raise ValueError("existing §6 would mark unfinished checklist acceptance as complete")


def _add_format_marker(spec: str) -> str:
    markers = list(SPEC_FORMAT_MARKER.finditer(spec))
    if len(markers) > 1:
        raise ValueError("multiple format markers; resolve manually")
    if markers:
        marker = markers[0]
        return spec[: marker.start()] + FORMAT_MARKER + spec[marker.end() :]
    title = re.search(r"(?m)^# [^\r\n]*(?:\r?\n|$)", spec)
    if not title:
        return FORMAT_MARKER + "\n\n" + spec
    return spec[: title.end()].rstrip("\r\n") + "\n\n" + FORMAT_MARKER + "\n" + spec[title.end() :]


def _add_criteria(spec: str, criteria: list[Criterion]) -> str:
    section = _section_six(spec)
    lines = "".join(f"- [{'x' if item.checked else ' '}] {item.title} -> verify: {item.task_id}\n" for item in criteria)
    if section:
        header_end = HEADING_SIX.match(spec, section[0]).end()  # type: ignore[union-attr]
        return spec[:header_end] + lines + "\n" + spec[header_end:]
    section_seven = re.search(r"(?m)^## 7\.", spec)
    position = section_seven.start() if section_seven else len(spec)
    prefix = spec[:position].rstrip("\r\n")
    suffix = spec[position:]
    return prefix + "\n\n## 6. Success Criteria and Verification\n" + lines + "\n" + suffix


def _append_checklist_history(spec: str, checklist: str) -> str:
    # A block quote preserves all checklist evidence without creating new §6 items
    # or making headings inside the historical text active package sections.
    quoted = "\n".join("> " + line if line else ">" for line in checklist.splitlines())
    return spec.rstrip("\r\n") + "\n\n## Original checklist.md Before Migration\n\n" + quoted + "\n"


def _check_owned_files(package: Path) -> None:
    # Following an active package's link could rewrite an archived document.
    for name in ("spec.md", "tasks.md", "checklist.md"):
        path = package / name
        try:
            attributes = path.lstat()
        except FileNotFoundError:
            if name == "checklist.md":
                continue
            raise ValueError(f"required package file missing: {name}") from None
        if not stat.S_ISREG(attributes.st_mode) or attributes.st_nlink != 1:
            raise ValueError(f"{name} must be a regular file without links")


def plan_migration(package: Path) -> Migration | None:
    _check_owned_files(package)
    spec = (package / "spec.md").read_text(encoding="utf-8")
    markers = list(SPEC_FORMAT_MARKER.finditer(spec))
    if len(markers) > 1:
        raise ValueError("multiple format markers; resolve manually")
    checklist_path = package / "checklist.md"
    if markers and int(markers[0].group(1)) > 2:
        return None
    if markers and int(markers[0].group(1)) == 2 and not checklist_path.exists():
        return None
    tasks = parse_task_records((package / "tasks.md").read_text(encoding="utf-8"))
    checklist = checklist_path.read_text(encoding="utf-8") if checklist_path.exists() else None
    criteria = _criteria(spec, tasks)
    if not criteria:
        if checklist is None:
            raise ValueError("no task-bound §6 criteria or legacy checklist")
        criteria = _new_criteria(checklist, tasks)
        spec_after = _add_criteria(spec, criteria)
    else:
        if checklist is not None:
            _check_existing(criteria, checklist, tasks)
        spec_after = spec
    spec_after = _add_format_marker(spec_after)
    if checklist is not None:
        spec_after = _append_checklist_history(spec_after, checklist)
    if section_checkboxes_by_prefix(spec_after, "## 6.")[0] < 1:
        raise ValueError("migration did not create acceptance criteria")
    return Migration(spec, spec_after, checklist)


def _show_diff(slug: str, migration: Migration) -> None:
    print(f"\n{slug}:")
    diff = difflib.unified_diff(
        migration.before.splitlines(keepends=True),
        migration.after.splitlines(keepends=True),
        fromfile=f"{slug}/spec.md",
        tofile=f"{slug}/spec.md",
    )
    print("".join(diff), end="")
    if migration.checklist is not None:
        print(f"  delete {slug}/checklist.md (contents preserved in spec.md)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Project root (default: current directory)")
    add_specs_dir_arg(parser)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Show changes without writing (default)")
    mode.add_argument("--apply", action="store_true", help="Write migrated packages and remove migrated checklists")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = Path(args.root).resolve()
        specs_root = resolve_specs_root(root, args.specs_dir)
        if "archive" in specs_root.resolve().relative_to(root).parts:
            raise ValueError("--specs-dir must not be inside an archive directory")
        specs_dir = resolve_specs_child(specs_root, "specs")
        slugs = active_package_slugs(specs_dir)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    failed = False
    for slug in slugs:
        try:
            package = resolve_specs_child(specs_dir, slug)
            migration = plan_migration(package)
            if migration is None:
                print(f"{slug}: v2 or newer; unchanged")
                continue
            _show_diff(slug, migration)
            if args.apply:
                (package / "spec.md").write_text(migration.after, encoding="utf-8")
                if migration.checklist is not None:
                    (package / "checklist.md").unlink()
                print(f"{slug}: applied")
            else:
                print(f"{slug}: dry-run; unchanged")
        except (OSError, UnicodeError, ValueError, SpecControlError) as exc:
            failed = True
            print(f"{slug}: skipped; {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

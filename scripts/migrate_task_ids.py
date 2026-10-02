#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""Preview or atomically migrate an active package to stable task identifiers."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import tempfile
from pathlib import Path

from spec_package_support import (
    SpecControlError,
    dependency_errors,
    parse_task_records,
    read_regular_text,
    resolve_active_slug,
    resolve_specs_child,
    resolve_specs_root,
)


def migration(content: str) -> tuple[str, dict[str, str]]:
    records = parse_task_records(content)
    errors = dependency_errors(records)
    if errors:
        raise SpecControlError("; ".join(errors))
    used = {record.id for record in records}
    mapping = {}
    for record in records:
        target = record.id
        if target.startswith("task_"):
            target = "task-" + target.removeprefix("task_")
            while target in used:
                target += "-m"
            used.add(target)
        mapping[record.id] = target
    lines = content.splitlines(keepends=True)
    output = []
    index = 0
    for line in lines:
        if re.match(r"^\s*-\s\[.\]\s+", line):
            record = records[index]
            index += 1
            if record.id.startswith("task_"):
                ending = "\r\n" if line.endswith("\r\n") else "\n"
                output.extend([line if line.endswith("\n") else line + ending, f"  - id: {mapping[record.id]}{ending}"])
                continue
        match = re.match(r"^(\s*- depends-on:\s*)([^\r\n]*)(\r?\n)?$", line)
        if match:
            refs = [mapping[item.strip()] for item in match.group(2).split(",")]
            line = match.group(1) + ", ".join(refs) + (match.group(3) or "")
        output.append(line)
    result = "".join(output)
    parse_task_records(result)
    return result, mapping


def migrate(root: Path, slug: str, *, apply: bool = False, specs_dir: str = ".spec") -> dict:
    path = resolve_specs_child(resolve_specs_root(root, specs_dir), "specs", slug, "tasks.md")
    original = read_regular_text(path)
    updated, mapping = migration(original)
    if apply:
        resolve_active_slug(root, specs_dir, slug)
        if read_regular_text(path) != original:
            raise SpecControlError("tasks changed during migration")
        if original != updated:
            fd, temporary = tempfile.mkstemp(prefix=".task-ids-", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                    stream.write(updated)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, path.stat().st_mode & 0o777)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
    return {
        "applied": apply,
        "changed": original != updated,
        "mapping": mapping,
        "diff": "".join(
            difflib.unified_diff(
                original.splitlines(True), updated.splitlines(True), fromfile="tasks.md", tofile="tasks.md"
            )
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--specs-dir", default=".spec")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        root = Path(args.root).resolve()
        slug = resolve_active_slug(root, args.specs_dir, args.slug)
        print(json.dumps(migrate(root, slug, apply=args.apply, specs_dir=args.specs_dir), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, SpecControlError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

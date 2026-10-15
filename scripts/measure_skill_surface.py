#!/usr/bin/env python3
"""Measure the SKILL constant-load surface in four fixed dimensions.

One run emits a single JSON document (stdout; diagnostics go to stderr) with
the same byte/char/line accounting for every surface, so before/after
comparisons stay honest:

- metadata:   SKILL.md YAML frontmatter (including ``---`` fences) — the part
              host skill lists always load.
- skill:      the whole SKILL.md — loaded once the skill triggers.
- stages:     every ``references/stages/*.md`` — read on demand per stage.
- templates:  init fences from ``references/templates.md`` — instantiated per
              new package. Fence extraction reuses
              ``spec_package_support.load_reference_templates``; this script
              deliberately owns no second parser.

Byte/char/line counts are static-size proxies only: they never claim host
tokens or latency.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from spec_package_support import load_reference_templates  # noqa: E402

FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.S)

SURFACE_DEFINITIONS = {
    "metadata": "SKILL.md frontmatter (with --- fences) always loaded by host skill lists",
    "skill": "SKILL.md whole file loaded when the skill triggers",
    "stages": "references/stages/*.md read on demand per stage",
    "templates": "init fences from references/templates.md instantiated per new package",
}


class MeasurementError(Exception):
    """A broken measurement assumption (missing file, absent fence, …)."""


def _account(text: str) -> dict[str, int]:
    """Static size accounting for one text unit, byte order included."""
    return {
        "bytes": len(text.encode("utf-8")),
        "chars": len(text),
        "lines": len(text.splitlines()),
    }


def _read(root: Path, relative: str) -> str:
    path = root / relative
    if not path.is_file():
        raise MeasurementError(f"missing file: {path}")
    return path.read_text(encoding="utf-8")


def measure_metadata(root: Path) -> dict[str, int]:
    match = FRONTMATTER_PATTERN.match(_read(root, "SKILL.md"))
    if not match:
        raise MeasurementError("SKILL.md has no YAML frontmatter block")
    return _account(match.group(0))


def measure_skill(root: Path) -> dict[str, int]:
    return _account(_read(root, "SKILL.md"))


def stage_files(root: Path) -> list[Path]:
    stages_dir = root / "references" / "stages"
    files = sorted(stages_dir.glob("*.md")) if stages_dir.is_dir() else []
    if not files:
        raise MeasurementError(f"no stage pages found under {stages_dir}")
    return files


def measure_stages(root: Path) -> tuple[dict[str, int], list[dict[str, object]]]:
    files = stage_files(root)
    per_stage: list[dict[str, object]] = []
    total = {"bytes": 0, "chars": 0, "lines": 0}
    for path in files:
        acc = _account(path.read_text(encoding="utf-8"))
        per_stage.append({"file": path.name, "bytes": acc["bytes"]})
        for key in total:
            total[key] += acc[key]
    return total, per_stage


def measure_templates(root: Path) -> tuple[dict[str, int], list[dict[str, object]]]:
    try:
        templates = load_reference_templates(root)
    except (OSError, ValueError) as exc:
        raise MeasurementError(f"template fence extraction failed: {exc}") from exc
    fences: list[dict[str, object]] = []
    total = {"bytes": 0, "chars": 0, "lines": 0}
    for doc in sorted(templates):
        acc = _account(templates[doc])
        fences.append({"doc": doc, "bytes": acc["bytes"], "lines": acc["lines"]})
        for key in total:
            total[key] += acc[key]
    if not fences:
        raise MeasurementError("no init fences parsed from references/templates.md")
    return total, fences


def measure(root: Path) -> dict[str, object]:
    metadata = measure_metadata(root)
    skill = measure_skill(root)
    stages, per_stage = measure_stages(root)
    templates, fences = measure_templates(root)

    surfaces = [
        {"name": name, "definition": SURFACE_DEFINITIONS[name], **values}
        for name, values in (
            ("metadata", metadata),
            ("skill", skill),
            ("stages", stages),
            ("templates", templates),
        )
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "surfaces": surfaces,
        "per_stage": per_stage,
        "fences": fences,
    }


def render_table(report: dict[str, object]) -> str:
    lines = [f"skill surface @ {report['root']}", ""]
    lines.append(f"{'surface':<12}{'bytes':>9}{'chars':>9}{'lines':>8}")
    for surface in report["surfaces"]:
        lines.append(f"{surface['name']:<12}{surface['bytes']:>9}{surface['chars']:>9}{surface['lines']:>8}")
    lines.append("")
    lines.append("stages (on demand):")
    for entry in report["per_stage"]:
        lines.append(f"  {entry['file']:<28}{entry['bytes']:>7} B")
    total_stage = sum(int(entry["bytes"]) for entry in report["per_stage"])
    lines.append(f"  {'total':<28}{total_stage:>7} B")
    lines.append("")
    lines.append("templates (instantiated fences):")
    for entry in report["fences"]:
        lines.append(f"  {entry['doc']:<28}{entry['bytes']:>7} B / {entry['lines']:>3} lines")
    total_fence = sum(int(entry["bytes"]) for entry in report["fences"])
    lines.append(f"  {'total':<28}{total_fence:>7} B")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the SKILL constant-load surface (four fixed dimensions).")
    parser.add_argument("--root", default=".", help="project root holding SKILL.md and references/ (default: .)")
    parser.add_argument("--json", action="store_true", help="print the single JSON report on stdout instead of a table")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        report = measure(root)
    except MeasurementError as exc:
        print(f"measure_skill_surface: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

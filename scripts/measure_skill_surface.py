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

Optional read-chain attribution (``--read-chain-manifest``): a fixture
contract describing per-stage information tasks as read events over bounded
line ranges. The manifest is a *measurement input*, never a runtime decision
table; this mode adds one ``read_chains`` block and changes none of the four
surfaces above. Comparisons use the stable projection (drop ``generated_at``
and the absolute ``root``); raw digests pin input versions so drift fails
loudly instead of silently re-baselining.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from spec_package_support import load_reference_templates  # noqa: E402
from safe_open_support import SafeOpenError, open_regular_read  # noqa: E402

FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.S)

SURFACE_DEFINITIONS = {
    "metadata": "SKILL.md frontmatter (with --- fences) always loaded by host skill lists",
    "skill": "SKILL.md whole file loaded when the skill triggers",
    "stages": "references/stages/*.md read on demand per stage",
    "templates": "init fences from references/templates.md instantiated per new package",
}

# --- read-chain attribution constants (fixture contract v1) -----------------------
READ_CHAIN_VERSION = 1
MANIFEST_MAX_BYTES = 1024 * 1024
INPUT_MAX_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 4096
EVENT_CONSUMERS = frozenset({"model", "script"})
EVENT_LOAD_CLASSES = frozenset({"always_loaded", "trigger_loaded", "conditional"})


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
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise MeasurementError(f"file is not valid UTF-8: {path}: {exc}") from exc


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
        acc = _account(_read(root, str(path.relative_to(root))))
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


# --- read-chain attribution mode --------------------------------------------------

def _safe_relative(rel: str) -> Path:
    """Validate a manifest-declared relative path; refuse absolutes, `..`, escapes."""
    if not isinstance(rel, str) or not rel or rel.startswith("/"):
        raise MeasurementError(f"manifest source path must be relative and non-empty: {rel!r}")
    parts = Path(rel).parts
    if any(part in {"..", ""} for part in parts) or Path(rel).is_absolute():
        raise MeasurementError(f"manifest source path escapes the root: {rel!r}")
    return Path(*parts)


def _read_bytes_pinned(root: Path, rel: str, max_bytes: int) -> bytes:
    """Read one input file under the safe-open contract (no-follow, regular, bounded)."""
    path = root / _safe_relative(rel)
    try:
        with open_regular_read(path, max_bytes=max_bytes) as fd:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise MeasurementError(
                        f"input exceeds budget of {max_bytes} bytes while reading: {rel}")
                chunks.append(chunk)
            return b"".join(chunks)
    except SafeOpenError as exc:
        reason = {"missing": "missing", "untrusted": "untrusted path (symlink/non-regular/oversize)",
                  "unsupported": "platform lacks O_NOFOLLOW; safe reads unsupported"}[exc.reason]
        raise MeasurementError(f"cannot safely read manifest input {rel}: {reason}") from exc


def _decode_input(rel: str, raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MeasurementError(f"manifest input is not valid UTF-8: {rel}: {exc}") from exc


def _line_bytes(text: str) -> list[int]:
    """Per-line UTF-8 byte sizes, trailing newline included (frozen accounting)."""
    return [len(line.encode("utf-8")) for line in text.splitlines(keepends=True)]


def _range_bytes(line_sizes: list[int], ranges) -> int:
    return sum(sum(line_sizes[start - 1:end]) for start, end in ranges)


def _merge_intervals(ranges: list[list[int]]) -> list[list[int]]:
    """Union of 1-indexed inclusive line intervals (line-disjoint merge)."""
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _load_manifest(manifest_path: Path) -> tuple[dict, str]:
    """Load and structurally validate the manifest; return (manifest, raw_sha256).

    The manifest file itself is an explicitly provided measurement input: an
    absolute path is used as-is, a relative one resolves against the root.
    Every *source* it declares stays strictly root-bounded (see _safe_relative).
    """
    try:
        with open_regular_read(manifest_path, max_bytes=MANIFEST_MAX_BYTES) as fd:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > MANIFEST_MAX_BYTES:
                    raise MeasurementError(
                        f"read-chain manifest exceeds the {MANIFEST_MAX_BYTES} byte budget")
                chunks.append(chunk)
            raw = b"".join(chunks)
    except SafeOpenError as exc:
        raise MeasurementError(
            f"cannot safely open read-chain manifest {manifest_path}: {exc.reason}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeasurementError(f"read-chain manifest is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise MeasurementError("read-chain manifest must be a JSON object")
    if manifest.get("schema_version") != READ_CHAIN_VERSION:
        raise MeasurementError(
            f"unsupported read-chain manifest schema_version: "
            f"{manifest.get('schema_version')!r} (expected {READ_CHAIN_VERSION})")
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise MeasurementError("read-chain manifest must carry a non-empty scenarios list")
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise MeasurementError("read-chain manifest must declare its sources digest map")
    seen_ids: set[str] = set()
    total_events = 0
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not isinstance(scenario.get("id"), str):
            raise MeasurementError("every scenario needs a string id")
        if scenario["id"] in seen_ids:
            raise MeasurementError(f"duplicate scenario id: {scenario['id']}")
        seen_ids.add(scenario["id"])
        events = scenario.get("events")
        if not isinstance(events, list) or not events:
            raise MeasurementError(f"scenario {scenario['id']}: events must be a non-empty list")
        seen_events: set[str] = set()
        for event in events:
            total_events += 1
            if total_events > MAX_EVENTS:
                raise MeasurementError(
                    f"manifest exceeds the event budget of {MAX_EVENTS} events")
            if not isinstance(event, dict) or not isinstance(event.get("id"), str):
                raise MeasurementError(f"scenario {scenario['id']}: every event needs a string id")
            if event["id"] in seen_events:
                raise MeasurementError(
                    f"scenario {scenario['id']}: duplicate event id {event['id']}")
            seen_events.add(event["id"])
            if event.get("consumer") not in EVENT_CONSUMERS:
                raise MeasurementError(
                    f"scenario {scenario['id']}/{event['id']}: consumer must be one of "
                    f"{sorted(EVENT_CONSUMERS)}, got {event.get('consumer')!r}")
            if event.get("load_class") not in EVENT_LOAD_CLASSES:
                raise MeasurementError(
                    f"scenario {scenario['id']}/{event['id']}: load_class must be one of "
                    f"{sorted(EVENT_LOAD_CLASSES)}, got {event.get('load_class')!r}")
            if event.get("source") not in sources:
                raise MeasurementError(
                    f"scenario {scenario['id']}/{event['id']}: source "
                    f"{event.get('source')!r} missing from the manifest sources map")
            ranges = event.get("ranges")
            if ranges != "whole" and not isinstance(ranges, list):
                raise MeasurementError(
                    f"scenario {scenario['id']}/{event['id']}: ranges must be 'whole' "
                    f"or a list of [start, end] pairs")
            call_id = event.get("call_id")
            if not isinstance(call_id, str) or not call_id.strip():
                raise MeasurementError(
                    f"scenario {scenario['id']}/{event['id']}: call_id must be a non-empty string")
            for text_field in ("reason", "contract_anchor"):
                if not isinstance(event.get(text_field), str) or not event[text_field].strip():
                    raise MeasurementError(
                        f"scenario {scenario['id']}/{event['id']}: {text_field} must be a "
                        f"non-empty string")
            repeat = event.get("repeat", 1)
            if not isinstance(repeat, int) or isinstance(repeat, bool) or repeat < 1:
                raise MeasurementError(
                    f"scenario {scenario['id']}/{event['id']}: repeat must be an integer >= 1")
    return manifest, digest


def measure_read_chains(root: Path, manifest: dict, manifest_sha256: str) -> dict[str, object]:
    """Compute per-scenario read attribution from a validated manifest.

    Snapshots every declared source once (raw sha256 pins the version), then
    derives: model event bytes (repeat expanded — every injection bills), the
    unique byte union per source (controllable corpus face), script read bytes,
    and planned/modelled tool-call and response proxies. Coverage is judged
    from each event's declared ``expected_keys``; a partial scenario is a
    reported state (``complete: false``), never an error and never hidden.
    """
    sources: dict[str, str] = manifest["sources"]
    inputs: list[dict[str, object]] = []
    snapshots: dict[str, tuple[str, list[int], str]] = {}  # rel -> (text, line bytes, digest)
    for rel in sorted(sources):
        declared = sources[rel]
        if not isinstance(declared, str) or not re.fullmatch(r"[0-9a-f]{64}", declared or ""):
            raise MeasurementError(f"sources['{rel}']: digest must be a 64-hex sha256")
        raw = _read_bytes_pinned(root, rel, INPUT_MAX_BYTES)
        actual = hashlib.sha256(raw).hexdigest()
        if actual != declared:
            raise MeasurementError(
                f"input drift: {rel} no longer matches the manifest digest "
                f"(declared {declared[:12]}…, actual {actual[:12]}…); recalibrate the "
                f"manifest only if the change is intentional")
        text = _decode_input(rel, raw)
        snapshots[rel] = (text, _line_bytes(text), actual)
        acc = _account(text)
        inputs.append({"path": rel, "raw_sha256": actual,
                       "bytes": acc["bytes"], "chars": acc["chars"], "lines": acc["lines"]})

    scenario_reports: list[dict[str, object]] = []
    summary = {"total_scenarios": 0, "complete_scenarios": 0, "read_events": 0,
               "model_event_bytes": 0, "model_unique_bytes": 0, "script_read_bytes": 0}
    for scenario in sorted(manifest["scenarios"], key=lambda s: s["id"]):
        model_event_bytes = 0
        script_read_bytes = 0
        read_events = 0
        model_calls: set[str] = set()
        union_per_source: dict[str, list[list[int]]] = {}
        covered_keys: set[str] = set()
        for event in scenario["events"]:
            text, line_sizes, _ = snapshots[event["source"]]
            ranges = event["ranges"]
            if ranges == "whole":
                ranges = [[1, len(line_sizes)]] if line_sizes else []
            validated: list[list[int]] = []
            previous_end = 0
            for pair in ranges:
                if (not isinstance(pair, list) or len(pair) != 2
                        or not all(isinstance(v, int) and not isinstance(v, bool) for v in pair)):
                    raise MeasurementError(
                        f"scenario {scenario['id']}/{event['id']}: range entries must be "
                        f"[start, end] integer pairs")
                start, end = pair
                if start < 1 or start > end or end > len(line_sizes):
                    raise MeasurementError(
                        f"scenario {scenario['id']}/{event['id']}: range [{start}, {end}] "
                        f"out of bounds for {event['source']} ({len(line_sizes)} lines)")
                if start <= previous_end:
                    raise MeasurementError(
                        f"scenario {scenario['id']}/{event['id']}: ranges must be ordered "
                        f"and non-overlapping in {event['source']}")
                previous_end = end
                validated.append([start, end])
            repeat = event.get("repeat", 1)
            read_events += repeat
            event_bytes = _range_bytes(line_sizes, validated)
            if event["consumer"] == "model":
                model_event_bytes += event_bytes * repeat
                model_calls.add(event["call_id"])
                union_per_source.setdefault(event["source"], []).extend(validated)
            else:
                script_read_bytes += event_bytes * repeat
            covered_keys.update(event.get("expected_keys", []))
        model_unique_bytes = 0
        for source_rel, intervals in union_per_source.items():
            line_sizes = snapshots[source_rel][1]
            model_unique_bytes += _range_bytes(line_sizes, _merge_intervals(intervals))
        for entry in scenario.get("context_available", []):
            rel, declared = entry["source"], entry["sha256"]
            if rel not in snapshots:
                raise MeasurementError(
                    f"scenario {scenario['id']}: context source {rel!r} missing from sources")
            _, _, actual = snapshots[rel]
            if declared != actual:
                raise MeasurementError(
                    f"scenario {scenario['id']}: context digest for {rel} does not match "
                    f"the current file — the warm/resumed premise is stale")
        missing = [key for key in scenario.get("expected", []) if key not in covered_keys]
        scenario_reports.append({
            "id": scenario["id"], "stage": scenario.get("stage"),
            "profile": scenario.get("profile"),
            "package_format": scenario.get("package_format"),
            "session_mode": scenario.get("session_mode"),
            "task_class": scenario.get("task_class"),
            "read_events": read_events,
            "model_events": sum(e.get("repeat", 1) for e in scenario["events"]
                                if e["consumer"] == "model"),
            "script_events": sum(e.get("repeat", 1) for e in scenario["events"]
                                 if e["consumer"] == "script"),
            "model_event_bytes": model_event_bytes,
            "model_unique_bytes": model_unique_bytes,
            "script_read_bytes": script_read_bytes,
            "planned_deterministic_tool_calls": len(model_calls),
            "planned_response_bytes": model_event_bytes,
            "modelled": True,
            "coverage": "complete" if not missing else "partial",
            "missing_expected": missing,
        })
        summary["total_scenarios"] += 1
        summary["complete_scenarios"] += 0 if missing else 1
        summary["read_events"] += read_events
        summary["model_event_bytes"] += model_event_bytes
        summary["model_unique_bytes"] += model_unique_bytes
        summary["script_read_bytes"] += script_read_bytes

    return {
        "version": READ_CHAIN_VERSION,
        "manifest_sha256": manifest_sha256,
        "inputs": inputs,
        "scenarios": scenario_reports,
        "summary": summary,
        "complete": summary["complete_scenarios"] == summary["total_scenarios"],
    }


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
    chains = report.get("read_chains")
    if chains:
        lines.append("")
        lines.append(f"read chains (manifest {chains['manifest_sha256'][:12]}\u2026, "
                     f"complete={chains['complete']}):")
        lines.append(f"  {'scenario':<24}{'events':>7}{'model B':>10}{'unique B':>10}"
                     f"{'script B':>10}{'calls':>7}  coverage")
        for entry in chains["scenarios"]:
            lines.append(
                f"  {entry['id']:<24}{entry['read_events']:>7}{entry['model_event_bytes']:>10}"
                f"{entry['model_unique_bytes']:>10}{entry['script_read_bytes']:>10}"
                f"{entry['planned_deterministic_tool_calls']:>7}  {entry['coverage']}")
        lines.append(f"  {'total':<24}{chains['summary']['read_events']:>7}"
                     f"{chains['summary']['model_event_bytes']:>10}"
                     f"{chains['summary']['model_unique_bytes']:>10}"
                     f"{chains['summary']['script_read_bytes']:>10}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the SKILL constant-load surface (four fixed dimensions).")
    parser.add_argument("--root", default=".", help="project root holding SKILL.md and references/ (default: .)")
    parser.add_argument("--json", action="store_true", help="print the single JSON report on stdout instead of a table")
    parser.add_argument("--read-chain-manifest", default=None, metavar="PATH",
                        help="optional read-chain attribution input (fixture contract v1); "
                             "relative paths resolve against --root; adds one read_chains "
                             "block and never changes the four surfaces")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        report = measure(root)
        if args.read_chain_manifest is not None:
            manifest_path = Path(args.read_chain_manifest)
            if not manifest_path.is_absolute():
                manifest_path = root / manifest_path
            manifest, digest = _load_manifest(manifest_path)
            report["read_chains"] = measure_read_chains(root, manifest, digest)
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

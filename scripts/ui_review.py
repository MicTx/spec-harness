"""Real UI/UX operation evidence for v3 delivery packages."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

import gitx
import package

SCHEMA_VERSION = 1
RECORD_NAME = "ui-review.json"
EVIDENCE_DIR = "evidence"
FRONTEND_EXTENSIONS = frozenset(
    {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".sass", ".less", ".html", ".htm", ".svg"}
)
UI_DIRECTORY_MARKERS = frozenset(
    {"app", "components", "frontend", "pages", "public", "static", "styles", "templates", "ui", "views", "web"}
)
UI_DIRECTORY_EXTENSIONS = FRONTEND_EXTENSIONS | frozenset(
    {
        ".ts",
        ".js",
        ".mjs",
        ".cjs",
        ".py",
        ".json",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".woff",
        ".woff2",
        ".ttf",
        ".ico",
    }
)
STATIC_DIRECTORY_MARKERS = frozenset({"assets", "css", "i18n", "locales", "public", "static", "styles", "themes"})
STATIC_ASSET_EXTENSIONS = frozenset(
    {".json", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".woff", ".woff2", ".ttf", ".ico"}
)
SCREENSHOT_FORMATS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg"}
SCREENSHOT_HASH = re.compile(r"^[0-9a-f]{64}$")
FLOW_RESULTS = frozenset({"pass", "fail"})
FINDING_STATUSES = frozenset({"fixed", "accepted"})
USER_DECISION = re.compile(r"by:\s*user\s+\S")
TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "generated_at", "tree_fp", "patterns", "flows", "screenshots", "findings"}
)
FLOW_KEYS = frozenset({"id", "action", "target", "expected", "observed", "result", "criteria", "finding"})
SCREENSHOT_KEYS = frozenset({"path", "sha256", "format", "step", "description"})
FINDING_KEYS = frozenset(
    {"id", "issue", "impact", "reproduction", "solution", "status", "step", "fix_task", "accepted_decision"}
)


def _clean_path(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or "\\" in text or text.startswith("/"):
        return ""
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return ""
    return "/".join(parts)


def _safe_relative(value: object, prefix: str | None = None, suffixes: set[str] | None = None) -> str:
    clean = _clean_path(value)
    if not clean:
        return ""
    if prefix and not clean.startswith(prefix + "/"):
        return ""
    if suffixes and PurePosixPath(clean).suffix.lower() not in suffixes:
        return ""
    return clean


def _safe_pattern(value: object) -> str:
    clean = _clean_path(value)
    if not clean or clean.startswith("new:"):
        return ""
    parts = clean.split("/")
    if ".spec" in parts or any(part == ".." for part in parts):
        return ""
    return clean.lower()


def _pattern_parts(pattern: str) -> list[str]:
    clean = pattern.removeprefix("new:").replace("\\", "/").lower()
    return [part for part in clean.split("/") if part and part != "."]


def _pattern_is_ui(pattern: str) -> bool:
    clean = pattern.removeprefix("new:").replace("\\", "/").lower()
    if not clean:
        return False
    if any(clean.endswith(extension) for extension in FRONTEND_EXTENSIONS):
        return True
    parts = _pattern_parts(clean)
    suffix = PurePosixPath(clean).suffix.lower()
    if any(part in UI_DIRECTORY_MARKERS for part in parts) and (not suffix or suffix in UI_DIRECTORY_EXTENSIONS):
        return True
    if any(part in UI_DIRECTORY_MARKERS for part in parts) and any(char in clean for char in "*?["):
        return True
    if any(part in STATIC_DIRECTORY_MARKERS for part in parts) and (
        not suffix or suffix in STATIC_ASSET_EXTENSIONS or suffix in UI_DIRECTORY_EXTENSIONS
    ):
        return True
    return False


def is_ui_path(path: str, extra_patterns: tuple[str, ...] | list[str] = ()) -> bool:
    clean = _clean_path(path).lower()
    if not clean:
        return False
    for pattern in extra_patterns:
        safe = _safe_pattern(pattern)
        if safe and package.path_matches(clean, safe):
            return True
    return _pattern_is_ui(clean)


def _detect_screenshot_format(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return ""


def _text_field(record: dict, key: str, label: str, errors: list[str]) -> None:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}.{key} must be a non-empty string")


def _unknown_fields(record: dict, allowed: frozenset[str], label: str, errors: list[str]) -> None:
    unknown = sorted(set(record) - allowed)
    if unknown:
        errors.append(f"{label} has unknown fields: {', '.join(unknown)}")


def record_patterns(record: dict) -> tuple[list[str], list[str]]:
    raw = record.get("patterns", [])
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["patterns must be a list"]
    patterns: list[str] = []
    errors: list[str] = []
    for index, item in enumerate(raw):
        safe = _safe_pattern(item)
        if not safe:
            errors.append(f"patterns[{index}] is not a safe relative glob")
        else:
            patterns.append(safe)
    return patterns, errors


def load_record(directory: Path) -> tuple[dict, list[str]]:
    path = directory / RECORD_NAME
    if not path.exists():
        return {}, ["ui-review.json is missing"]
    if path.is_symlink():
        return {}, ["ui-review.json must not be a symlink"]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"ui-review.json is unreadable: {exc}"]
    if not isinstance(value, dict):
        return {}, ["ui-review.json must contain a JSON object"]
    return value, []


def activation(
    root: Path,
    directory: Path,
    spec: str,
    tasks: str,
    extra_patterns: tuple[str, ...] | list[str] = (),
) -> dict:
    scope = package.scope_patterns(spec)
    try:
        boundaries = [pattern for task in package.parse_tasks(tasks) for pattern in task.globs]
    except ValueError:
        boundaries = []
    declared = [pattern.removeprefix("new:").lower() for pattern in [*scope, *boundaries]]
    safe_patterns = tuple(_safe_pattern(pattern) for pattern in extra_patterns)
    safe_patterns = tuple(pattern for pattern in safe_patterns if pattern)
    scope_ui = [pattern for pattern in scope if _pattern_is_ui(pattern)]
    boundary_ui = [pattern for pattern in boundaries if _pattern_is_ui(pattern)]
    extra_ui = [
        extra
        for extra in safe_patterns
        if any(package.path_matches(extra, declared_pattern) for declared_pattern in declared)
    ]
    baseline = package.package_baseline(directory)
    base = baseline.get("head") or gitx.head(root)
    preexisting = set(baseline.get("dirty", []))
    try:
        changes = package.changed_paths(root, base)
    except (OSError, ValueError):
        changes = []
    changed_ui = [
        path
        for path in changes
        if not path.startswith(".spec/") and path not in preexisting and is_ui_path(path, safe_patterns)
    ]
    return {
        "required": bool(scope_ui or boundary_ui or extra_ui or changed_ui),
        "scope": scope_ui,
        "boundaries": boundary_ui,
        "extra": extra_ui,
        "changed": changed_ui,
        "patterns": list(safe_patterns),
    }


def _validate_flows(record: dict, spec: str, errors: list[str]) -> tuple[set[str], set[str], set[str], dict[str, str]]:
    flows = record.get("flows")
    if not isinstance(flows, list) or not flows:
        errors.append("flows must be a non-empty list")
        return set(), set(), set(), {}
    known_criteria = {key for key, _text, _task in package.criteria(spec)}
    if not known_criteria:
        errors.append("spec has no Criteria for UI flows to cover")
    flow_ids: set[str] = set()
    covered: set[str] = set()
    failed: set[str] = set()
    finding_refs: dict[str, str] = {}
    for index, flow in enumerate(flows):
        label = f"flows[{index}]"
        if not isinstance(flow, dict):
            errors.append(f"{label} must be an object")
            continue
        _unknown_fields(flow, FLOW_KEYS, label, errors)
        flow_id = flow.get("id")
        if not isinstance(flow_id, str) or not flow_id.strip():
            errors.append(f"{label}.id must be a non-empty string")
        elif flow_id in flow_ids:
            errors.append(f"{label}.id is duplicated: {flow_id}")
        else:
            flow_ids.add(flow_id)
        for field_name in ("action", "target", "expected", "observed"):
            _text_field(flow, field_name, label, errors)
        result = flow.get("result")
        if result not in FLOW_RESULTS:
            errors.append(f"{label}.result must be pass or fail")
        criteria = flow.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            errors.append(f"{label}.criteria must be a non-empty list")
            criteria = []
        else:
            for item in criteria:
                if not isinstance(item, str) or item not in known_criteria:
                    errors.append(f"{label}.criteria has unknown criterion: {item}")
        if result == "pass":
            covered.update(item for item in criteria if isinstance(item, str))
        finding = flow.get("finding")
        if result == "fail":
            if isinstance(flow_id, str) and flow_id:
                failed.add(flow_id)
            if not isinstance(finding, str) or not finding.strip():
                errors.append(f"{label}.finding is required for a failed flow")
            elif isinstance(flow_id, str) and flow_id:
                finding_refs[flow_id] = finding.strip()
        elif finding not in (None, ""):
            errors.append(f"{label}.finding is only valid for result fail")
    missing = sorted(known_criteria - covered)
    if missing:
        errors.append("criteria not covered by a passing UI flow: " + ", ".join(missing))
    return flow_ids, failed, covered, finding_refs


def _validate_screenshots(record: dict, directory: Path, flow_ids: set[str], errors: list[str]) -> set[str]:
    screenshots = record.get("screenshots")
    if not isinstance(screenshots, list) or not screenshots:
        errors.append("screenshots must be a non-empty list")
        return set()
    evidence_root = (directory / EVIDENCE_DIR).resolve()
    seen_paths: set[str] = set()
    steps: set[str] = set()
    for index, screenshot in enumerate(screenshots):
        label = f"screenshots[{index}]"
        if not isinstance(screenshot, dict):
            errors.append(f"{label} must be an object")
            continue
        _unknown_fields(screenshot, SCREENSHOT_KEYS, label, errors)
        relative = _safe_relative(screenshot.get("path"), prefix=EVIDENCE_DIR, suffixes=set(SCREENSHOT_FORMATS))
        if not relative:
            errors.append(f"{label}.path must be a PNG or JPEG file under {EVIDENCE_DIR}/")
            continue
        if relative in seen_paths:
            errors.append(f"{label}.path is duplicated: {relative}")
        seen_paths.add(relative)
        step = screenshot.get("step")
        if not isinstance(step, str) or step not in flow_ids:
            errors.append(f"{label}.step must reference an existing flow id")
        else:
            steps.add(step)
        description = screenshot.get("description")
        if description is not None and not isinstance(description, str):
            errors.append(f"{label}.description must be a string")
        target = directory / relative
        if target.is_symlink() or not target.is_file():
            errors.append(f"{label}.path does not resolve to a regular file: {relative}")
            continue
        if not target.resolve().is_relative_to(evidence_root):
            errors.append(f"{label}.path escapes the evidence directory: {relative}")
            continue
        data = target.read_bytes()
        expected = SCREENSHOT_FORMATS[PurePosixPath(relative).suffix.lower()]
        detected = _detect_screenshot_format(data)
        if not data or detected != expected:
            errors.append(f"{label}.path is not a valid {expected.upper()} screenshot: {relative}")
        digest = screenshot.get("sha256")
        if not isinstance(digest, str) or not SCREENSHOT_HASH.fullmatch(digest):
            errors.append(f"{label}.sha256 must be a lowercase SHA-256 hex digest")
        elif digest != hashlib.sha256(data).hexdigest():
            errors.append(f"{label}.sha256 does not match the screenshot: {relative}")
        if screenshot.get("format") != expected:
            errors.append(f"{label}.format must be {expected}")
    missing_steps = sorted(flow_ids - steps)
    if missing_steps:
        errors.append("flow steps without screenshots: " + ", ".join(missing_steps))
    return steps


def _validate_findings(
    record: dict,
    directory: Path,
    spec: str,
    tasks: str,
    flow_ids: set[str],
    failed_steps: set[str],
    finding_refs: dict[str, str],
    errors: list[str],
) -> dict[str, dict]:
    findings = record.get("findings", [])
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        return {}
    try:
        task_map = {task.id: task for task in package.parse_tasks(tasks)}
    except ValueError:
        task_map = {}
    latest = package.latest_runs(directory)
    decisions = {item["id"]: item["text"] for item in package.parse_ledger(spec, "Decisions", "D")}
    by_id: dict[str, dict] = {}
    finding_steps: set[str] = set()
    for index, finding in enumerate(findings):
        label = f"findings[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{label} must be an object")
            continue
        _unknown_fields(finding, FINDING_KEYS, label, errors)
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or not finding_id.strip():
            errors.append(f"{label}.id must be a non-empty string")
        elif finding_id in by_id:
            errors.append(f"{label}.id is duplicated: {finding_id}")
        if isinstance(finding_id, str) and finding_id.strip():
            by_id.setdefault(finding_id, finding)
        for field_name in ("issue", "impact", "reproduction", "solution"):
            _text_field(finding, field_name, label, errors)
        step = finding.get("step")
        if step is not None:
            if not isinstance(step, str) or step not in flow_ids:
                errors.append(f"{label}.step must reference an existing flow id")
            else:
                finding_steps.add(step)
        status = finding.get("status")
        if status not in FINDING_STATUSES:
            errors.append(f"{label}.status must be fixed or accepted")
            continue
        if not isinstance(finding_id, str) or not finding_id.strip():
            continue
        fix_task = finding.get("fix_task")
        accepted_decision = finding.get("accepted_decision")
        if status == "fixed":
            if accepted_decision not in (None, ""):
                errors.append(f"{label}.accepted_decision is not valid for a fixed finding")
            task = task_map.get(fix_task) if isinstance(fix_task, str) else None
            if task is None:
                errors.append(f"{label}.fix_task must reference an existing task")
            elif not task.checked:
                errors.append(f"{label}.fix_task is not checked: {fix_task}")
            elif latest.get(fix_task, {}).get("exit") != 0:
                errors.append(f"{label}.fix_task has no passing evidence: {fix_task}")
        else:
            if fix_task not in (None, ""):
                errors.append(f"{label}.fix_task is not valid for an accepted finding")
            text = ""
            if isinstance(accepted_decision, str) and re.fullmatch(r"D\d+", accepted_decision):
                text = decisions.get(accepted_decision, "")
            if not text:
                errors.append(f"{label}.accepted_decision must reference a Decisions row")
            elif not USER_DECISION.search(text):
                errors.append(f"{label}.accepted_decision must cite by: user <reference>")
    for step in sorted(failed_steps):
        finding_id = finding_refs.get(step, "")
        if not finding_id or finding_id not in by_id:
            errors.append(f"failed flow {step} has no valid finding reference")
            continue
        if step not in finding_steps:
            errors.append(f"failed flow {step} has no finding")
        elif by_id[finding_id].get("step") != step:
            errors.append(f"finding {finding_id} does not reference failed flow {step}")
    return by_id


def validate_record(
    directory: Path,
    spec: str,
    tasks: str,
    record: dict,
    tree_fp: str | None = None,
    tree_required: bool = True,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["record must be a JSON object"]
    _unknown_fields(record, TOP_LEVEL_KEYS, "ui-review.json", errors)
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    fingerprint = record.get("tree_fp")
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        errors.append("tree_fp must be a non-empty string")
    elif tree_required and tree_fp is None:
        errors.append("UI review tree fingerprint cannot be verified")
    elif tree_required and fingerprint != tree_fp:
        errors.append("UI review is stale for the current product tree")
    generated_at = record.get("generated_at")
    if generated_at is not None and (not isinstance(generated_at, str) or not generated_at.strip()):
        errors.append("generated_at must be a non-empty string")
    _patterns, pattern_errors = record_patterns(record)
    errors.extend(pattern_errors)
    flow_ids, failed_steps, _covered, finding_refs = _validate_flows(record, spec, errors)
    _validate_screenshots(record, directory, flow_ids, errors)
    _validate_findings(record, directory, spec, tasks, flow_ids, failed_steps, finding_refs, errors)
    return errors


def review_status(
    root: Path,
    slug: str,
    spec: str | None = None,
    tree_fp: str | None = None,
    tree_required: bool = True,
) -> dict:
    directory, spec_text, tasks_text = package.read_package(root, slug)
    spec_text = spec or spec_text
    record, load_errors = load_record(directory)
    patterns, pattern_errors = record_patterns(record)
    status = activation(root, directory, spec_text, tasks_text, patterns)
    result = {
        "required": status["required"],
        "activation": status,
        "errors": [],
        "flows": len(record.get("flows", [])) if isinstance(record.get("flows", []), list) else 0,
        "screenshots": len(record.get("screenshots", [])) if isinstance(record.get("screenshots", []), list) else 0,
        "findings": len(record.get("findings", [])) if isinstance(record.get("findings", []), list) else 0,
    }
    if not status["required"]:
        result["ignored_errors"] = load_errors + pattern_errors
        return result
    if load_errors:
        result["errors"] = load_errors
        return result
    result["errors"] = pattern_errors + validate_record(
        directory,
        spec_text,
        tasks_text,
        record,
        tree_fp=tree_fp,
        tree_required=tree_required,
    )
    return result


def write_record(root: Path, slug: str, source: Path) -> dict:
    directory, spec, tasks = package.require_execution_started(root, slug)
    if not source.is_absolute():
        source = root / source
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read UI review draft: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("UI review draft must contain a JSON object")
    normalized = dict(payload)
    screenshots = normalized.get("screenshots")
    if not isinstance(screenshots, list):
        raise ValueError("UI review screenshots must be a list")
    evidence_root = (directory / EVIDENCE_DIR).resolve()
    rewritten = []
    for screenshot in screenshots:
        if not isinstance(screenshot, dict):
            raise ValueError("each UI review screenshot must be an object")
        relative = _safe_relative(screenshot.get("path"), prefix=EVIDENCE_DIR, suffixes=set(SCREENSHOT_FORMATS))
        if not relative:
            raise ValueError(f"invalid UI screenshot path: {screenshot.get('path')}")
        target = directory / relative
        if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(evidence_root):
            raise ValueError(f"UI screenshot is missing or unsafe: {relative}")
        data = target.read_bytes()
        expected = SCREENSHOT_FORMATS[PurePosixPath(relative).suffix.lower()]
        if not data or _detect_screenshot_format(data) != expected:
            raise ValueError(f"UI screenshot is not a valid {expected.upper()} file: {relative}")
        entry = dict(screenshot)
        entry["path"] = relative
        entry["sha256"] = hashlib.sha256(data).hexdigest()
        entry["format"] = expected
        rewritten.append(entry)
    normalized["screenshots"] = rewritten
    normalized["schema_version"] = SCHEMA_VERSION
    normalized["generated_at"] = package.now()
    fingerprint = package.tree_fingerprint(root)
    normalized["tree_fp"] = fingerprint
    errors = validate_record(directory, spec, tasks, normalized, tree_fp=fingerprint, tree_required=True)
    if errors:
        raise ValueError("UI review is invalid: " + "; ".join(errors))
    (directory / RECORD_NAME).write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "slug": slug,
        "record": RECORD_NAME,
        "tree_fp": fingerprint,
        "flows": len(normalized.get("flows", [])),
        "screenshots": len(rewritten),
        "findings": len(normalized.get("findings", [])),
        "ready": True,
    }


def archived_errors(directory: Path, spec: str, tasks: str) -> list[str]:
    if not (directory / RECORD_NAME).exists():
        return []
    record, load_errors = load_record(directory)
    if load_errors:
        return [f"ui-review.json: {item}" for item in load_errors]
    errors = validate_record(directory, spec, tasks, record, tree_required=False)
    return [f"ui-review.json: {item}" for item in errors]


def summary_lines(root: Path, slug: str) -> list[str]:
    status = review_status(root, slug, tree_required=False)
    if not status["required"]:
        return ["- Not required: no UI surface was changed."]
    directory, _spec, _tasks = package.read_package(root, slug)
    record, load_errors = load_record(directory)
    if load_errors:
        return [f"- Invalid: {item}" for item in load_errors]
    return [
        f"- Required: {status['flows']} real flows, {status['screenshots']} screenshots, "
        f"{status['findings']} closed findings; see {RECORD_NAME} and {EVIDENCE_DIR}/.",
    ]

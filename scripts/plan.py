"""Thick planning: deterministic lint, Go binding and execution amendments."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import charter
import gitx
from memory import lessons_top
from package import (
    SLUG_RE,
    append_run,
    criteria,
    dependency_errors,
    extract_branch,
    now,
    package_baseline,
    package_kind,
    parse_charter,
    parse_forks,
    parse_tasks,
    path_matches,
    read_package,
    repair_origin,
    scope_patterns,
    section,
    specs_root,
)
from runtime_budget import project_verify_timeout

IRREVERSIBLE_GLOBS = (
    "migrations",
    ".github/workflows",
    "pyproject.toml",
    "package.json",
    "requirements",
    "uv.lock",
    "poetry.lock",
    "package-lock.json",
    "secret",
    ".env",
    "credential",
    "release/",
)
VALID_ORIGINS = {"planned", "emergent", "support"}


def _gap(rule: str, detail: str, ask: str, task: str | None = None) -> dict:
    return {"rule": rule, **({"task": task} if task else {}), "detail": detail, "ask": ask}


def _literal_prefix(pattern: str) -> str:
    clean = pattern.removeprefix("new:").rstrip("/")
    positions = [clean.find(char) for char in "*?[" if char in clean]
    cut = min(positions) if positions else len(clean)
    return clean[:cut].rstrip("/")


def _scope_covers(pattern: str, scopes: list[str]) -> bool:
    """Best-effort deterministic containment check for glob boundaries."""
    clean = pattern.removeprefix("new:").rstrip("/")
    if not any(char in clean for char in "*?["):
        return any(path_matches(clean, scope) for scope in scopes)
    prefix = _literal_prefix(clean)
    boundary_parts = clean.split("/")
    for scope in scopes:
        base = scope.removeprefix("new:").rstrip("/")
        if base == clean:
            return True
        if not any(char in base for char in "*?["):
            if prefix == base or prefix.startswith(base + "/"):
                return True
        elif base.endswith("/**"):
            root = base[:-3].rstrip("/")
            if prefix == root or prefix.startswith(root + "/"):
                return True
        elif "**" not in clean and "**" not in base:
            scope_parts = base.split("/")
            if len(scope_parts) == len(boundary_parts) and all(
                _component_covers(scope_part, boundary_part)
                for scope_part, boundary_part in zip(scope_parts, boundary_parts)
            ):
                return True
    return False


def _component_covers(scope: str, boundary: str) -> bool:
    if scope == boundary or scope == "*":
        return True
    return not any(char in boundary for char in "*?[") and bool(fnmatch.fnmatchcase(boundary, scope))


def _verify(root: Path, command: str) -> tuple[int, float, str]:
    started = time.monotonic()
    timeout = project_verify_timeout()
    try:
        completed = subprocess.run(command, shell=True, cwd=root, capture_output=True, timeout=timeout)
        return completed.returncode, round(time.monotonic() - started, 3), ""
    except subprocess.TimeoutExpired:
        return 124, round(time.monotonic() - started, 3), f"verify timed out after {timeout}s"


def _charter_gaps(spec: str) -> list[dict]:
    values = parse_charter(spec)
    gaps = []
    for index, allowed in charter.ALLOWED.items():
        value = values.get(index, "")
        if not value:
            gaps.append(_gap("PLAN-004", f"missing charter {index}", "Fill the charter through a planning card."))
        elif value not in allowed:
            # PLAN-011: closed-vocabulary drift gets its own rule code so enum
            # inconsistencies are distinguishable from generic format gaps.
            gaps.append(
                _gap(
                    "PLAN-011",
                    f"unknown charter value {index}={value}; use one of: " + ", ".join(sorted(allowed)),
                    "Pick a value from the charter vocabulary.",
                )
            )
    priority = values.get("V1", "")
    if not priority or len([part for part in priority.split(">") if part.strip()]) < 2:
        gaps.append(_gap("PLAN-004", "V1 priority has no ordering", "Write at least two ordered priorities."))
    return gaps


def _suggestions(records: list) -> list[dict]:
    """Read-only refinement proposals for known red/green planning traps."""
    result = []
    for task in records:
        if task.expect != "red" or not task.command:
            continue
        proposed = ""
        for pattern in task.globs:
            if pattern.startswith("new:"):
                path = pattern[4:]
                if path in task.command and "--collect-only" not in task.command:
                    proposed = f"python3 -m pytest {path} --collect-only -q"
                    break
        shared = any(
            other.expect == "green" and other.command == task.command and task.id in other.depends_on
            for other in records
        )
        if shared:
            if not proposed and task.command.startswith("python3 -m pytest "):
                proposed = task.command + " --collect-only -q"
            result.append(
                {
                    "task": task.id,
                    "rule": "SUGGEST-001",
                    "detail": "red card shares the green card verify; split into a collection proof",
                    "proposed_verify": proposed,
                }
            )
        elif proposed:
            result.append(
                {
                    "task": task.id,
                    "rule": "SUGGEST-002",
                    "detail": "red verify runs a not-yet-created new: path; use a collection proof",
                    "proposed_verify": proposed,
                }
            )
    return result


def lint(
    root: Path,
    slug: str,
    static: bool = False,
    record: bool = False,
    *,
    contents: tuple[str, str] | None = None,
    suggest: bool = False,
) -> dict:
    """Validate disk truth, or a read-only candidate before a planning edit."""
    if contents is not None and (not static or record):
        raise ValueError("candidate contents require static=True and record=False")
    package, spec, tasks = read_package(root, slug)
    if contents is not None:
        spec, tasks = contents
    gaps: list[dict] = []
    if "spec-format: 3" not in spec:
        return {
            "ready": False,
            "gaps": [
                _gap(
                    "PLAN-004",
                    "package is not spec-format: 3",
                    "Use the v3 format; migration is not supported.",
                )
            ],
        }
    gaps.extend(_charter_gaps(spec))
    scope = scope_patterns(spec)
    if not scope:
        gaps.append(_gap("PLAN-003", "Scope.in is empty", "List the paths this package may change."))
    if not any(line.startswith("goal:") for line in section(spec, "Goal")):
        gaps.append(_gap("PLAN-004", "Goal.goal is missing", "State the observable outcome."))
    if not any(line.startswith("done means:") for line in section(spec, "Goal")):
        gaps.append(_gap("PLAN-004", "Goal.done means is missing", "State how completion is observed."))
    if package_kind(spec) == "repair":
        # Repair packages keep thin planning but must stay anchored to a real
        # archived delivery so the full gate can audit spec consistency.
        origin = repair_origin(spec)
        if not origin:
            gaps.append(
                _gap(
                    "PLAN-004",
                    "kind: repair requires repair-of: <archived-slug>",
                    "Name the archived package this repair refers to.",
                )
            )
        elif not SLUG_RE.fullmatch(origin) or not (specs_root(root) / "archive" / origin).is_dir():
            gaps.append(
                _gap(
                    "PLAN-004",
                    f"repair-of must name an existing archived package: {origin}",
                    "Deliver or archive the origin first, or fix the slug.",
                )
            )

    try:
        records = parse_tasks(tasks)
    except ValueError as exc:
        # PLAN-013: a missing id becomes a positional task_NNNN fallback, which
        # parse_tasks rejects; surface it as the stable-semantic-id rule.
        positional = re.search(r"invalid task id: (task_\d{4})", str(exc))
        if positional:
            return {
                "ready": False,
                "gaps": [
                    _gap(
                        "PLAN-013",
                        f"positional fallback id {positional.group(1)} is not a stable semantic slug",
                        "Give every task an explicit id that survives edits and reordering.",
                    )
                ],
            }
        return {"ready": False, "gaps": [_gap("PLAN-004", str(exc), "Give every task one unique stable id.")]}
    if not records:
        gaps.append(_gap("PLAN-004", "no tasks", "Write executable task cards."))
    gaps.extend(_gap("PLAN-005", error, "Close the task dependency graph.") for error in dependency_errors(records))
    # PLAN-012: every task reference anywhere in spec.md must resolve in
    # tasks.md, so edits cannot silently renumber or drop a referenced id.
    known_ids = {record.id for record in records}
    for token in sorted(set(re.findall(r"\btask-[a-z0-9][a-z0-9-]*", spec))):
        if token not in known_ids:
            gaps.append(
                _gap(
                    "PLAN-012",
                    f"spec.md references unknown task {token}",
                    "Point the reference at an existing task id or add the task.",
                )
            )
    known = {task.id for task in records}
    criterion_lines = criteria(spec)
    if not criterion_lines:
        gaps.append(_gap("PLAN-004", "no Criteria", "Add at least one observable criterion."))
    criteria_tasks = [task_id for _key, _text, task_id in criterion_lines]
    for task_id in criteria_tasks:
        if task_id not in known:
            gaps.append(_gap("PLAN-004", f"unknown criterion task {task_id}", "Point every criterion at a task."))
    durations: dict[str, float] = {}
    tracked_files = gitx.git(root, "ls-files", "-z", text=False).stdout.split(b"\0")
    tracked_paths = [os.fsdecode(path) for path in tracked_files if path]
    for task in records:
        if task.origin not in VALID_ORIGINS:
            gaps.append(
                _gap("PLAN-004", f"invalid origin {task.origin}", "Use planned, emergent, or support.", task.id)
            )
        if not task.globs:
            gaps.append(_gap("PLAN-002", "empty boundary", "List the exact files this task may change.", task.id))
        for pattern in task.globs:
            clean = pattern.removeprefix("new:")
            matches = [path for path in tracked_paths if path_matches(path, clean)]
            if not pattern.startswith("new:") and not matches:
                gaps.append(
                    _gap(
                        "PLAN-002",
                        f"{clean} matches 0 tracked files",
                        "Point at a real tracked path or prefix new:.",
                        task.id,
                    )
                )
            if scope and not _scope_covers(pattern, scope):
                gaps.append(
                    _gap("PLAN-003", f"{clean} outside Scope.in", "Amend scope or move the task boundary.", task.id)
                )
            token = next((item for item in IRREVERSIBLE_GLOBS if item in clean), "")
            if token:
                has_fork = any(token in fork["text"] or clean in fork["text"] for fork in parse_forks(spec))
                if not has_fork:
                    gaps.append(
                        _gap(
                            "PLAN-009",
                            f"{clean} is an irreversible surface with no matching fork",
                            "Add an F with the user's pre-authorization.",
                            task.id,
                        )
                    )
        if task.id not in criteria_tasks and task.origin != "support":
            gaps.append(
                _gap(
                    "PLAN-004",
                    "task not covered by Criteria",
                    "Reference the task from a criterion or mark origin: support.",
                    task.id,
                )
            )
        if not re.search(r"(?m)^\s*-\s*expect:\s*(red|green)\s*$", task.block):
            gaps.append(
                _gap("PLAN-001", "missing expect: red|green", "Record whether the baseline should fail.", task.id)
            )
        if not task.command:
            gaps.append(
                _gap("PLAN-001", "missing executable verify", "Provide one backticked repeatable command.", task.id)
            )
        elif not static:
            code, duration, detail = _verify(root, task.command)
            durations[task.id] = duration
            expected = task.expect or "green"
            if (expected == "red" and code == 0) or (expected == "green" and code != 0):
                gaps.append(
                    _gap(
                        "PLAN-001",
                        detail or f"expect {expected}, exit {code}",
                        "Make the verify discriminate the intended change or fix its baseline.",
                        task.id,
                    )
                )
            if duration > 120:
                gaps.append(
                    _gap(
                        "PLAN-010",
                        f"verify took {duration}s",
                        "Use a narrower command for a faster feedback loop.",
                        task.id,
                    )
                )
            if record:
                append_run(
                    package,
                    {
                        "kind": "baseline",
                        "task": task.id,
                        "cmd": task.command,
                        "exit": code,
                        "expect": expected,
                        "duration": duration,
                        "ts": now(),
                    },
                )
    for task in records:
        for other in records:
            if task.id == other.id:
                continue
            for pattern in other.globs:
                if pattern.startswith("new:") and pattern[4:] in task.command and other.id not in task.depends_on:
                    gaps.append(
                        _gap(
                            "PLAN-005",
                            f"{task.id} reads {other.id}'s new file before it exists",
                            "Add depends-on for the producer task.",
                            task.id,
                        )
                    )
    for line in section(spec, "Assumptions"):
        if "CONFIRMED" in line and "probe:" not in line:
            gaps.append(_gap("PLAN-006", line[:100], "Add the read-only probe that confirmed this assumption."))
        elif "UNVERIFIED" in line and "if false:" not in line:
            gaps.append(_gap("PLAN-006", line[:100], "Name the fork or task used if this assumption is false."))
        elif "CONFIRMED" not in line and "UNVERIFIED" not in line:
            gaps.append(_gap("PLAN-006", line[:100], "Mark the assumption CONFIRMED with a probe or UNVERIFIED."))
    for marker in ("[fact]", "[decision]", "[reversible]"):
        if marker in spec or marker in tasks:
            gaps.append(_gap("PLAN-007", marker, "Resolve the fact now or turn it into a probe task."))
    for fork in parse_forks(spec):
        if not fork["pick"] or not fork["by"]:
            gaps.append(_gap("PLAN-008", fork["id"], "Choose an option and cite its authority."))
        if "不可逆" in fork["reversal"] or "irreversible" in fork["reversal"].lower():
            if not fork["by"].startswith("user"):
                gaps.append(_gap("PLAN-008", fork["id"], "Irreversible choices require user authority."))
        elif fork["by"] and not re.fullmatch(r"(V[1-7]|F\d+|user .+)", fork["by"]):
            gaps.append(_gap("PLAN-008", fork["id"], "Cite V<n>, F<n>, or user <reference>."))
    payload = {
        "ready": not any(gap["rule"] != "PLAN-010" for gap in gaps),
        "gaps": gaps,
        "durations": durations,
        "lessons": lessons_top(root, [pattern for task in records for pattern in task.globs]),
    }
    if suggest:
        payload["suggestions"] = _suggestions(records)
    return payload


def _exclude_worktrees(root: Path) -> None:
    """Hide `.spec/worktrees/` from dirty checks in every worktree.

    `info/exclude` lives in the common Git dir, so one entry covers the main
    worktree and all linked worktrees without touching the repo's `.gitignore`.
    """
    common = gitx.git(root, "rev-parse", "--git-common-dir", check=False).stdout.strip()
    if not common:
        return
    common_path = Path(common)
    if not common_path.is_absolute():
        base = gitx.toplevel(root) or root
        common_path = base / common_path
    exclude = common_path / "info" / "exclude"
    line = ".spec/worktrees/"
    try:
        existing = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
        if line in existing:
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except OSError:
        return


def _spec_worktree(root: Path, slug: str, branch: str) -> Path:
    """Create (or reuse) the linked worktree that executes the package."""
    target = root / ".spec" / "worktrees" / slug
    _exclude_worktrees(root)
    registered = {entry["path"].resolve(): entry["branch"] for entry in gitx.worktrees(root)}
    existing = registered.get(target.resolve())
    if target.exists() or existing:
        if existing != f"refs/heads/{branch}":
            raise ValueError(f"worktree path is not bound to {branch}: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    result = gitx.git(root, "worktree", "add", str(target), "-b", branch, check=False)
    if result.returncode != 0 and "already exists" in result.stderr:
        result = gitx.git(root, "worktree", "add", str(target), branch, check=False)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "git worktree add failed")
    return target


def go(root: Path, slug: str) -> dict:
    package, spec, _tasks = read_package(root, slug)
    if package_baseline(package):
        raise ValueError("package is already frozen by plan-go")
    if re.search(r"(?m)^- frozen:", spec):
        raise ValueError("package already carries a frozen marker")
    current = gitx.branch(root)
    if not current:
        raise ValueError("plan-go requires a named branch")
    bound_match = re.search(r"(?m)^- branch:", spec)
    bound = extract_branch(spec) if bound_match else ""
    if bound and current != bound:
        raise ValueError(f"package is bound to {bound}, current branch is {current}")
    branch = bound or current
    worktree: Path | None = None
    if not bound and (current in {"main", "master", "develop"} or current.startswith("release/")):
        branch = f"spec/{slug}"
        gate = lint(root, slug, static=True)
        if not gate["ready"]:
            return gate
        worktree = _spec_worktree(root, slug, branch)
        destination = worktree / ".spec" / "specs" / slug
        if destination.exists():
            raise ValueError(f"worktree package already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(package), str(destination))
        root, package = worktree, destination
        current = branch
    result = lint(root, slug, record=True)
    if not result["ready"]:
        return {**result, **({"worktree": str(worktree)} if worktree else {})}
    if not bound and branch != current:
        gitx.git(root, "checkout", "-b", branch)
    if not bound:
        (package / "spec.md").write_text(
            spec.rstrip() + f"\n\n## Binding\n- branch: `{branch}`\n- frozen: {now()}\n", encoding="utf-8"
        )
    else:
        frozen = re.sub(
            r"(?m)^(- branch:.*)$",
            rf"\1\n- frozen: {now()}",
            spec,
            count=1,
        )
        (package / "spec.md").write_text(frozen, encoding="utf-8")
    charter.merge(root, parse_charter(spec))
    dirty = gitx.dirty_paths(root, exclude=(".spec/",))
    # Plan summary snapshot: lets revision_summary diff task ids and line
    # counts exactly even though package files may not be Git-tracked yet.
    frozen_spec = (package / "spec.md").read_text(encoding="utf-8")
    frozen_tasks = (package / "tasks.md").read_text(encoding="utf-8")
    append_run(
        package,
        {
            "kind": "baseline",
            "task": "package",
            "head": gitx.head(root),
            "dirty": dirty,
            "task_ids": [record.id for record in parse_tasks(frozen_tasks)],
            "spec_lines": len(frozen_spec.splitlines()),
            "tasks_lines": len(frozen_tasks.splitlines()),
            "spec_fp": hashlib.sha256(frozen_spec.encode()).hexdigest(),
            "tasks_fp": hashlib.sha256(frozen_tasks.encode()).hexdigest(),
            "ts": now(),
        },
    )
    result = {"ready": True, "slug": slug, "branch": branch}
    if worktree is not None:
        result["worktree"] = str(worktree)
    return result


def amend(root: Path, slug: str, task_text: str) -> dict:
    package, _spec, tasks = read_package(root, slug)
    if charter.load(root).get("V2", "strict") == "strict":
        raise ValueError("charter V2=strict does not allow emergent tasks")
    addition = task_text.strip()
    if not re.search(r"(?m)^\s*-\s*origin:", addition):
        addition += "\n  - origin: emergent"
    (package / "tasks.md").write_text(tasks.rstrip() + "\n" + addition + "\n", encoding="utf-8")
    return lint(root, slug)

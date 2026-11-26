"""Thick planning: deterministic lint, Go binding and execution amendments."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path, PurePosixPath

import charter
import gitx
from local_evidence import ignore_package
from package import (
    SLUG_RE,
    TASK_RE,
    amend_authorized,
    append_run,
    criteria,
    dependency_errors,
    extract_branch,
    frozen_charter,
    goal_fingerprint,
    now,
    package_baseline,
    package_dir,
    package_kind,
    parse_charter,
    parse_forks,
    parse_tasks,
    path_matches,
    read_package,
    repair_origin,
    require_execution_started,
    runs,
    scope_patterns,
    seal_freeze,
    section,
    specs_root,
    staged_product_tree,
    tree_fingerprint,
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


def _normalise_command_path(token: str) -> str:
    """Reduce one verify token to the repo path it names, or "" for non-paths."""
    value = token.strip().strip("'\"")
    if "=" in value and value.split("=", 1)[0].startswith("--"):
        value = value.split("=", 1)[1]
    value = value.removeprefix("./")
    value = value.split("::", 1)[0] if "::" in value else value
    if not value or value.startswith("-"):
        return ""
    return os.path.normpath(value)


def _command_path_tokens(command: str) -> set[str]:
    """Whole path tokens a verify command reads; substrings never count."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    return {path for path in (_normalise_command_path(token) for token in tokens) if path}


def _verify(root: Path, command: str, package: Path | None = None) -> tuple[int, float, str]:
    """Reuse a passing record only for the exact command and current tree."""
    if package is not None:
        fingerprint = tree_fingerprint(root)
        for record in runs(package):
            if (
                record.get("kind") in ("run", "baseline")
                and record.get("cmd") == command
                and record.get("exit") == 0
                and record.get("tree_fp") == fingerprint
            ):
                return 0, 0.0, ""
    started = time.monotonic()
    timeout = project_verify_timeout()
    try:
        completed = subprocess.run(command, shell=True, cwd=root, capture_output=True, timeout=timeout)
        return completed.returncode, round(time.monotonic() - started, 3), ""
    except subprocess.TimeoutExpired:
        return 124, round(time.monotonic() - started, 3), f"verify timed out after {timeout}s"


def _charter_gaps(values: dict) -> list[dict]:
    gaps = []
    for index, allowed in charter.ALLOWED.items():
        value = values.get(index, "")
        if value not in allowed:
            gaps.append(
                _gap(
                    "PLAN-003",
                    f"unknown charter value {index}={value}; use one of: " + ", ".join(sorted(allowed)),
                    "Pick a value from the charter vocabulary.",
                )
            )
    priority = values.get("V1", "")
    if not priority or len([part for part in priority.split(">") if part.strip()]) < 2:
        gaps.append(_gap("PLAN-003", "V1 priority has no ordering", "Order at least two priorities."))
    return gaps


def lint(
    root: Path,
    slug: str,
    static: bool = False,
    record: bool = False,
    *,
    contents: tuple[str, str] | None = None,
    policy: dict | None = None,
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
    policy = policy or package_baseline(package).get("charter") or charter.resolve(root, spec)
    gaps.extend(_charter_gaps(policy))
    scope = scope_patterns(spec)
    if not scope:
        gaps.append(_gap("PLAN-002", "Scope.in is empty", "List the paths this package may change."))
    goal = dict(line.split(":", 1) for line in section(spec, "Goal") if ":" in line)
    for name in ("goal", "done means"):
        if not goal.get(name, "").strip():
            gaps.append(_gap("PLAN-004", f"Goal.{name} is missing", "State the observable outcome."))
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
        return {"ready": False, "gaps": [_gap("PLAN-004", str(exc), "Give every task one unique stable id.")]}
    if not records:
        gaps.append(_gap("PLAN-004", "no tasks", "Write executable task cards."))
    gaps.extend(_gap("PLAN-005", error, "Close the task dependency graph.") for error in dependency_errors(records))
    known_ids = {record.id for record in records}
    for token in sorted(set(re.findall(r"\btask-[a-z0-9][a-z0-9-]*", spec))):
        if token not in known_ids:
            gaps.append(
                _gap(
                    "PLAN-004",
                    f"spec.md references unknown task {token}",
                    "Point the reference at an existing task id or add the task.",
                )
            )
    criterion_lines = criteria(spec)
    criterion_ids = {key for key, _, _ in criterion_lines}
    if (
        not criterion_lines
        or len(criterion_ids) != len(criterion_lines)
        or any(not key or not text for key, text, _ in criterion_lines)
    ):
        gaps.append(_gap("PLAN-004", "Criteria are missing, empty or duplicated", "Use unique observable criteria."))
    criteria_tasks = [task_id for _key, _text, task_id in criterion_lines]
    for task_id in criteria_tasks:
        if task_id not in known_ids:
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
                    _gap("PLAN-002", f"{clean} outside Scope.in", "Amend scope or move the task boundary.", task.id)
                )
            token = next((item for item in IRREVERSIBLE_GLOBS if item in clean), "")
            if token:
                has_fork = any(token in fork["text"] or clean in fork["text"] for fork in parse_forks(spec))
                if not has_fork:
                    gaps.append(
                        _gap(
                            "PLAN-003",
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
        if re.search(r"(?m)^\s*-\s*expect:", task.block) and task.expect not in {"red", "green"}:
            gaps.append(_gap("PLAN-001", "invalid expect", "Use red or green, or omit the baseline probe.", task.id))
        if not task.command or task.verify != f"`{task.command}`":
            gaps.append(_gap("PLAN-001", "invalid verify", "Provide exactly one backticked command.", task.id))
    for task in records:
        tokens = _command_path_tokens(task.command)
        for other in records:
            if task.id == other.id:
                continue
            for pattern in other.globs:
                if (
                    pattern.startswith("new:")
                    and _normalise_command_path(pattern[4:]) in tokens
                    and other.id not in task.depends_on
                ):
                    gaps.append(
                        _gap(
                            "PLAN-005",
                            f"{task.id} reads {other.id}'s new file before it exists",
                            "Add depends-on for the producer task.",
                            task.id,
                        )
                    )
    for fork in parse_forks(spec):
        if not fork["pick"] or not fork["by"]:
            gaps.append(_gap("PLAN-003", fork["id"], "Choose an option and cite its authority."))
        elif "不可逆" in fork["reversal"] or "irreversible" in fork["reversal"].lower():
            if not re.fullmatch(r"user .+", fork["by"]):
                gaps.append(_gap("PLAN-003", fork["id"], "Irreversible choices require user authority."))
        elif not re.fullmatch(r"(V[1-7]|F\d+|user .+)", fork["by"]):
            gaps.append(_gap("PLAN-003", fork["id"], "Cite V<n>, F<n>, or user <reference>."))
    if not gaps and not static:
        for task in records:
            baseline = {"kind": "baseline", "task": task.id, "cmd": task.command, "ts": now()}
            if task.expect:
                code, duration, detail = _verify(root, task.command, package)
                durations[task.id] = duration
                if (task.expect == "green") != (code == 0):
                    message = detail or f"expect {task.expect}, exit {code}"
                    gaps.append(_gap("PLAN-001", message, "Check that verify proves the intended behavior.", task.id))
                if duration > 120:
                    warning = _gap("PLAN-001", f"verify took {duration}s", "Consider a narrower verify.", task.id)
                    gaps.append(dict(warning, severity="warning"))
                if record:
                    baseline.update(exit=code, expect=task.expect, duration=duration, tree_fp=tree_fingerprint(root))
            if record:
                append_run(package, baseline)
    return {
        "ready": all(gap.get("severity") == "warning" for gap in gaps),
        "gaps": gaps,
        "durations": durations,
    }


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


def _quick_ui_path(path: str) -> bool:
    clean = path.lower().replace("\\", "/")
    suffix = PurePosixPath(clean).suffix
    visual = {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".sass", ".less", ".html", ".htm", ".svg"}
    assets = {".json", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".woff", ".woff2", ".ttf", ".ico"}
    names = set(clean.split("/"))
    ui = {"app", "components", "frontend", "pages", "public", "static", "styles", "templates", "ui", "views", "web"}
    static = {"assets", "css", "i18n", "locales", "public", "static", "styles", "themes"}
    ui_files = visual | assets | {".ts", ".js", ".mjs", ".cjs", ".py"}
    return suffix in visual or bool(names & ui and suffix in ui_files) or bool(names & static and suffix in assets)


def start_quick(root: Path, slug: str | None, goal: str, done: str, paths: list[str], verify: str) -> dict:
    fields = (goal, done, verify)
    if any(not value.strip() or not value.isprintable() for value in fields) or "|" in done or "`" in verify:
        raise ValueError("quick requires a one-line goal, criterion, and verify command")
    if not 1 <= len(paths) <= 2 or len(set(paths)) != len(paths):
        raise ValueError("quick requires one or two exact product files")
    head, branch = gitx.head(root), gitx.branch(root)
    if not head or not branch or gitx.git(root, "diff", "--cached", "--quiet", check=False).returncode:
        raise ValueError("quick requires a named checkout with a clean index")
    if tree_fingerprint(root) != staged_product_tree(root, revision=head):
        raise ValueError("quick requires a clean product checkout; keep existing changes and use a standard plan")
    if any(path.is_symlink() for path in (root / ".spec", specs_root(root), specs_root(root) / "archive")):
        raise ValueError("quick evidence directories must not be symlinked")
    for active in specs_root(root).glob("*/spec.md"):
        text = active.read_text(encoding="utf-8")
        if not re.search(r"(?m)^- branch:", text) or extract_branch(text) == branch:
            raise ValueError("finish the existing package or draft before starting quick")
    patterns = []
    for path in paths:
        current = root / path
        regular = not current.exists() or current.is_file()
        unsafe_part = any(
            part.startswith(".") or part in {"package-lock.json", "poetry.lock", "uv.lock"} for part in path.split("/")
        )
        if (
            not re.fullmatch(r"[\w.+/-]+", path)
            or any(part in {"", ".", "..", ".git", ".spec", ".github"} for part in path.split("/"))
            or unsafe_part
            or any(token in path.lower() for token in IRREVERSIBLE_GLOBS)
            or path.lower().endswith((".pem", ".key", ".p12", ".pfx"))
            or _quick_ui_path(path)
            or current.resolve() != current
            or not regular
        ):
            raise ValueError(f"{path} requires a standard plan or is not an exact regular-file path")
        listing = gitx.git(root, "--literal-pathspecs", "ls-files", "--error-unmatch", "--", path, check=False)
        tracked = listing.returncode == 0
        if not tracked and gitx.git(root, "check-ignore", "-q", "--", path, check=False).returncode == 0:
            raise ValueError(f"{path} is ignored by Git")
        patterns.append(path if tracked else "new:" + path)
    slug = slug or f"{now()[:10]}_quick-{time.time_ns()}"
    directory = package_dir(root, slug)
    if (specs_root(root) / "archive" / slug).exists() or directory.exists():
        raise ValueError("quick package name is already in use")
    ignore_package(root, slug)
    directory.mkdir(parents=True, exist_ok=False)
    boundary = ", ".join(patterns)
    (directory / "spec.md").write_text(
        f"# Quick fix\n<!-- spec-format: 3 -->\n\n## Goal\n- kind: quick\n- goal: {goal}\n- done means: {done}\n"
        f"## Scope\n- in: {boundary}\n## Criteria\n- C1 | {done} | task-fix\n",
        encoding="utf-8",
    )
    (directory / "tasks.md").write_text(
        f"- [ ] Verify the fix\n  - id: task-fix\n  - boundary: {boundary}\n  - verify: `{verify}`\n", encoding="utf-8"
    )
    result = go(root, slug, quick=True)
    if not result["ready"]:
        shutil.rmtree(directory)
    return result


def go(root: Path, slug: str, *, quick: bool = False) -> dict:
    package, spec, _tasks = read_package(root, slug)
    if (package_kind(spec) == "quick") != quick:
        raise ValueError("quick packages must be frozen through spec.py quick")
    policy = charter.resolve(root, spec)
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
    for other in specs_root(root).glob("*/spec.md"):
        if other.parent == package:
            continue
        text = other.read_text(encoding="utf-8")
        if re.search(r"(?m)^- frozen:", text) and extract_branch(text) == branch:
            raise ValueError(f"branch already binds active package {other.parent.name}")
    worktree: Path | None = None
    if not bound and not quick and (current in {"main", "master", "develop"} or current.startswith("release/")):
        branch = f"spec/{slug}"
        gate = lint(root, slug, static=True, policy=policy)
        if not gate["ready"]:
            return gate
        worktree = _spec_worktree(root, slug, branch)
        destination = worktree / ".spec" / "specs" / slug
        if destination.exists():
            raise ValueError(f"worktree package already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(package), str(destination))
        root, package = worktree, destination
    result = lint(root, slug, record=True, policy=policy)
    if not result["ready"]:
        return {**result, **({"worktree": str(worktree)} if worktree else {})}
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
    dirty = gitx.dirty_paths(root, exclude=(".spec/",))
    # Plan summary snapshot: lets revision_summary diff task ids and line
    # counts exactly even though package files may not be Git-tracked yet.
    frozen_spec = (package / "spec.md").read_text(encoding="utf-8")
    frozen_tasks = (package / "tasks.md").read_text(encoding="utf-8")
    cards = parse_tasks(frozen_tasks)
    append_run(
        package,
        {
            "kind": "baseline",
            "task": "package",
            "evidence_kernel": 1,
            "branch": branch,
            "head": gitx.head(root),
            "dirty": dirty,
            "task_ids": [task.id for task in cards],
            "task_boundaries": {task.id: list(task.globs) for task in cards},
            "task_depends": {task.id: list(task.depends_on) for task in cards},
            "criteria": criteria(frozen_spec),
            "goal_fp": goal_fingerprint(frozen_spec),
            "finish": "worktree" if quick else policy["V4"],
            "charter": policy,
            "charter_overrides": parse_charter(frozen_spec),
            "spec_lines": len(frozen_spec.splitlines()),
            "tasks_lines": len(frozen_tasks.splitlines()),
            "spec_fp": hashlib.sha256(frozen_spec.encode()).hexdigest(),
            "tasks_fp": hashlib.sha256(frozen_tasks.encode()).hexdigest(),
            "ts": now(),
        },
    )
    seal_freeze(package)
    result = {"ready": True, "slug": slug, "branch": branch}
    if worktree is not None:
        result["worktree"] = str(worktree)
    return result


def _revised_task_block(existing, replacement) -> str:
    """Keep frozen identity fields and require fresh evidence for the revised check."""
    lines = [
        f"- [ ] {replacement.title}",
        f"  - id: {existing.id}",
        f"  - boundary: {existing.boundary}",
        f"  - verify: {replacement.verify}",
    ]
    if expected := replacement.expect or existing.expect:
        lines.append(f"  - expect: {expected}")
    if existing.depends_on:
        lines.append("  - depends-on: " + ", ".join(existing.depends_on))
    lines.append(f"  - origin: {existing.origin}")
    return "\n".join(lines)


def _replace_task_block(tasks: str, task_id: str, block: str) -> str:
    """Swap one card's block in place, keeping the file's trailing blank lines."""
    lines = tasks.splitlines()
    task_indexes = [index for index, line in enumerate(lines) if TASK_RE.match(line)]
    for position, index in enumerate(task_indexes):
        end = task_indexes[position + 1] if position + 1 < len(task_indexes) else len(lines)
        chunk = lines[index:end]
        if re.search(rf"(?m)^\s*- id:\s*{re.escape(task_id)}\s*$", "\n".join(chunk)):
            tail = []
            for line in reversed(chunk):
                if line.strip():
                    break
                tail.append(line)
            lines[index:end] = block.splitlines() + tail[::-1]
            return "\n".join(lines) + "\n"
    raise ValueError(f"task not found: {task_id}")


def _revise_task(root: Path, slug: str, task_text: str, by: str) -> dict:
    """Replace one check under V2; retain identity and force re-verification."""
    package, spec, tasks = require_execution_started(root, slug)
    v2 = frozen_charter(package, spec).get("V2", "strict")
    authority = by.strip()
    if not authority:
        raise ValueError("plan-amend --revise requires --by: V<n>/F<n> or user <reference>")
    if not amend_authorized(authority, v2):
        allowed = "user <reference>" if v2 == "strict" else "V<n>, F<n> or user <reference>"
        raise ValueError(f"charter V2={v2} does not accept --by {authority!r}; use {allowed}")
    replacements = parse_tasks(task_text)
    if len(replacements) != 1:
        raise ValueError("plan-amend --revise replaces exactly one existing task card")
    replacement = replacements[0]
    if not replacement.command:
        raise ValueError("plan-amend --revise requires one executable verify command")
    if replacement.verify != f"`{replacement.command}`":
        raise ValueError("revise verify must contain exactly one complete backticked command")
    existing = next((record for record in parse_tasks(tasks) if record.id == replacement.id), None)
    if existing is None:
        raise ValueError(f"unknown task id: {replacement.id}")
    updated = _replace_task_block(tasks, existing.id, _revised_task_block(existing, replacement))
    checked = lint(root, slug, static=True, contents=(spec, updated))
    if not checked["ready"]:
        return checked
    (package / "tasks.md").write_text(updated, encoding="utf-8")
    append_run(
        package,
        {
            "kind": "amend",
            "task": existing.id,
            "cmd": replacement.command,
            "boundary": list(existing.globs),
            "by": authority,
            "ts": now(),
        },
    )
    return checked


def amend(root: Path, slug: str, task_text: str, revise: bool = False, by: str = "") -> dict:
    if package_kind(read_package(root, slug)[1]) == "quick":
        raise ValueError("quick scope and proof are fixed; use a standard package for expanded work")
    if revise:
        return _revise_task(root, slug, task_text, by)
    package, spec, tasks = require_execution_started(root, slug)
    v2 = frozen_charter(package, spec).get("V2", "strict")
    authority = by.strip() or "V2"
    if v2 == "strict" or not amend_authorized(authority, v2):
        raise ValueError(f"charter V2={v2} does not authorize emergent tasks by {authority}")
    addition = re.sub(r"(?m)^([ \t]*- origin:).*$", r"\1 emergent", task_text.strip())
    if "- origin:" not in addition:
        addition += "\n  - origin: emergent"
    added = parse_tasks(addition)
    if len(added) != 1 or not added[0].command or not addition.startswith("- [ ] "):
        raise ValueError("plan-amend adds exactly one unchecked task with an executable verify")
    if added[0].verify != f"`{added[0].command}`":
        raise ValueError("amend verify must contain exactly one complete backticked command")
    updated = tasks.rstrip() + "\n" + addition + "\n"
    checked = lint(root, slug, static=True, contents=(spec, updated))
    if checked["ready"]:
        task = added[0]
        identity = dict(task=task.id, cmd=task.command, boundary=list(task.globs), depends=list(task.depends_on))
        append_run(package, dict(kind="amend", by=authority, ts=now(), **identity))
        (package / "tasks.md").write_text(updated, encoding="utf-8")
    return checked

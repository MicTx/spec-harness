"""Spec v3 disk model: package selection, tasks, decisions and evidence."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import gitx
import local_evidence

SLUG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_[a-z0-9][a-z0-9_-]*$")
TASK_RE = re.compile(r"^-\s*\[([ x!>])\]\s+(.+)$")
TASK_BLOCK_RE = re.compile(r"(?ms)^-\s*\[([ x!>])\]\s+(.+?)\n(.*?)(?=^-\s*\[[ x!>]\]\s+|\Z)")
PROTECTED = {"main", "master", "develop"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_root(start: str | Path = ".") -> Path:
    current = Path(start).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".spec" / "specs").is_dir():
            return candidate
    raise ValueError("no .spec/specs tree found from the current directory")


def primary_root(root: Path) -> Path:
    """Return the primary worktree root for shared `.spec` state.

    Linked spec worktrees own their package trees, but the repo-level charter
    (`.spec/charter.md`) belongs to the primary checkout,
    which `git worktree list` always reports first.
    """
    result = gitx.git(root, "worktree", "list", "--porcelain", check=False)
    if result.returncode != 0:
        return root
    first = next(
        (line.removeprefix("worktree ") for line in result.stdout.splitlines() if line.startswith("worktree ")),
        "",
    )
    return Path(first).resolve() if first else root


def specs_root(root: Path) -> Path:
    return root / ".spec" / "specs"


def package_dir(root: Path, slug: str) -> Path:
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("invalid Development Record slug")
    return specs_root(root) / slug


# Hot-path read cache for the (spec.md, tasks.md) pair.  Entries are keyed by
# the package directory and validated against each file's (st_mtime_ns,
# st_size) stamp, so an in-place rewrite is always visible to the next read.
_READ_PACKAGE_CACHE: dict[Path, tuple[tuple, str, str]] = {}


def _file_stamp(info: os.stat_result) -> tuple[int, ...]:
    return info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_package(root: Path, slug: str) -> tuple[Path, str, str]:
    directory = package_dir(root, slug)
    spec_path = directory / "spec.md"
    tasks_path = directory / "tasks.md"
    spec_info, tasks_info = spec_path.stat(), tasks_path.stat()
    stamps = (_file_stamp(spec_info), _file_stamp(tasks_info))
    cached = _READ_PACKAGE_CACHE.get(directory)
    if cached is not None and cached[0] == stamps:
        return directory, cached[1], cached[2]
    spec = spec_path.read_text(encoding="utf-8")
    tasks = tasks_path.read_text(encoding="utf-8")
    _READ_PACKAGE_CACHE[directory] = (stamps, spec, tasks)
    return directory, spec, tasks


def read_archived_package(root: Path, slug: str) -> tuple[Path, str, str]:
    """Read a delivered package from the archive tree."""
    directory = specs_root(root) / "archive" / slug
    return (
        directory,
        (directory / "spec.md").read_text(encoding="utf-8"),
        (directory / "tasks.md").read_text(encoding="utf-8"),
    )


def inventory(root: Path) -> list[dict]:
    """Read-only machine inventory of active and archived v3 packages.

    This is a plain disk projection for tooling and dashboards; lifecycle
    next-action semantics stay with route.py, never here.
    """
    result = []
    for archived in (False, True):
        base = specs_root(root) / "archive" if archived else specs_root(root)
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir() or (not archived and child.name == "archive"):
                continue
            if not (child / "spec.md").is_file() or not (child / "tasks.md").is_file():
                continue
            spec = (child / "spec.md").read_text(encoding="utf-8")
            if "spec-format: 3" not in spec:
                # Frozen pre-v3 packages stay untouched on disk but are not
                # inventory entries; same membership test as check_all.
                continue
            text = (child / "tasks.md").read_text(encoding="utf-8")
            records = parse_tasks(text) if archived else task_records(child, text)
            latest = latest_runs(child)
            proven = {task.id for task in records if task.checked and latest.get(task.id, {}).get("exit") == 0}
            criterion = criteria(spec)
            try:
                branch = extract_branch(spec)
            except ValueError:
                branch = ""
            result.append(
                {
                    "slug": child.name,
                    "archived": archived,
                    "branch": branch,
                    "frozen": bool(re.search(r"(?m)^- frozen:", spec)),
                    "tasks": {
                        "total": len(records),
                        "checked": sum(1 for record in records if record.checked),
                    },
                    "criteria": {
                        "total": len(criterion),
                        "passed": sum(task in proven for _key, _text, task in criterion),
                    },
                }
            )
    return result


def extract_branch(spec: str) -> str:
    match = re.search(r"^- branch:\s*`?([^\s`;]+)`?", spec, re.MULTILINE)
    if not match:
        raise ValueError("v3 spec requires branch: <non-protected-branch>")
    branch = match.group(1)
    if (branch in PROTECTED or branch.startswith("release/")) and package_kind(spec) != "quick":
        raise ValueError("a task package cannot bind a protected branch")
    return branch


def worktree_packages(root: Path) -> list[dict]:
    """Active packages bound inside other worktrees of the same repository."""
    found = []
    current = gitx.toplevel(root)
    for entry in gitx.worktrees(root):
        path = entry["path"]
        if current is not None and path.resolve() == current:
            continue
        specs = path / ".spec" / "specs"
        if not specs.is_dir():
            continue
        for child in sorted(specs.iterdir()):
            if not child.is_dir() or child.name == "archive":
                continue
            try:
                branch = extract_branch((child / "spec.md").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            found.append({"slug": child.name, "branch": branch, "worktree": str(path)})
    return found


def resolve_slug(root: Path, explicit: str | None = None) -> str:
    packages = sorted(path.name for path in specs_root(root).iterdir() if path.is_dir() and path.name != "archive")
    if explicit:
        if explicit not in packages:
            raise ValueError(f"active package not found: {explicit}")
        return explicit
    branch = gitx.branch(root)
    matches = []
    for slug in packages:
        try:
            if extract_branch((specs_root(root) / slug / "spec.md").read_text(encoding="utf-8")) == branch:
                matches.append(slug)
        except (OSError, ValueError):
            continue
    if len(matches) == 1:
        return matches[0]
    if not branch and len(packages) == 1 and gitx.toplevel(root) is None:
        return packages[0]
    if not matches:
        elsewhere = worktree_packages(root)
        hint = ""
        if packages:
            hint += "; local packages: " + ", ".join(packages)
        if elsewhere:
            hint += "; active in worktrees: " + ", ".join(f"{item['slug']} @ {item['worktree']}" for item in elsewhere)
        raise ValueError(f"current branch does not bind one active v3 package{hint}")
    raise ValueError("multiple active packages bind the current branch: " + ", ".join(matches))


def _scan_archived_branches(root: Path) -> dict[str, str]:
    """Linear archive scan returning slug -> bound branch for parseable specs."""
    archive = specs_root(root) / "archive"
    branches: dict[str, str] = {}
    if not archive.is_dir():
        return branches
    for child in sorted(archive.iterdir()):
        if not child.is_dir():
            continue
        try:
            branches[child.name] = extract_branch((child / "spec.md").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return branches


def archived_branch_index(root: Path) -> dict[str, str]:
    """Disk-cached archive branch index (slug -> bound branch).

    The cache at `.spec/cache/archive-index.json` is valid while the archive
    directory's ``st_mtime_ns`` is unchanged and the stored entry count still
    describes the stored mapping; otherwise rescan and write back.  Writing is
    best effort: a read-only tree just pays the scan on every call.
    """
    archive = specs_root(root) / "archive"
    if not archive.is_dir():
        return {}
    try:
        archive_ns = archive.stat().st_mtime_ns
    except OSError:
        return {}
    cache = root / ".spec" / "cache" / "archive-index.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        branches = data["branches"]
        if (
            data.get("archive_ns") == archive_ns
            and data.get("count") == len(branches)
            and isinstance(branches, dict)
            and all(isinstance(key, str) and isinstance(value, str) for key, value in branches.items())
        ):
            return branches
    except (OSError, ValueError, TypeError, KeyError):
        pass
    branches = _scan_archived_branches(root)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({"archive_ns": archive_ns, "count": len(branches), "branches": branches}, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        pass
    return branches


def resolve_archived_slug(root: Path, explicit: str | None = None, *, latest_quick: bool = False) -> str:
    if explicit:
        if not SLUG_RE.fullmatch(explicit):
            raise ValueError("invalid Development Record slug")
        archive = specs_root(root) / "archive"
        known = {path.name for path in archive.iterdir() if path.is_dir()} if archive.is_dir() else set()
        if explicit not in known:
            raise ValueError(f"archived package not found: {explicit}")
        return explicit
    branch = gitx.branch(root)
    matches = sorted(slug for slug, bound in archived_branch_index(root).items() if bound == branch)
    if len(matches) == 1:
        return matches[0]
    if latest_quick and matches and all("_quick-" in slug for slug in matches):
        return matches[-1]
    if not matches:
        raise ValueError("no archived package binds the current branch")
    raise ValueError("multiple archived packages bind the current branch: " + ", ".join(matches))


def section(spec: str, name: str) -> list[str]:
    lines = spec.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == f"## {name}") + 1
    except StopIteration:
        return []
    result = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            result.append(line[2:].strip())
    return result


def field(line: str, key: str) -> str:
    match = re.search(rf"(?:^|\|)\s*{re.escape(key)}:\s*([^|]*)", line)
    return match.group(1).strip() if match else ""


def package_kind(spec: str) -> str:
    """Return the declared package kind; packages without one are standard."""
    match = re.search(r"(?m)^- kind:\s*([a-z][a-z-]*)\s*$", spec)
    return match.group(1) if match else "standard"


def repair_origin(spec: str) -> str:
    """Return the archived slug a repair package refers to, if declared."""
    match = re.search(r"(?m)^- repair-of:\s*`?([^\s`|]+)`?", spec)
    return match.group(1) if match else ""


def parse_charter(spec: str) -> dict[str, str]:
    result = {}
    for line in section(spec, "Charter"):
        match = re.match(r"V(\d+)\s+([^:]+):\s*([^|]+)", line)
        if match:
            result[f"V{match.group(1)}"] = match.group(3).strip()
    return result


def frozen_charter(directory: Path, spec: str) -> dict[str, str]:
    return package_baseline(directory).get("charter", parse_charter(spec))


def amend_authorized(by: str, v2: str) -> bool:
    """Whether an amendment authority satisfies the package's own V2.

    A strict package accepts only ``user <reference>``. Adjacent work accepts
    the same vocabulary ``decide`` and plan.lint accept for reversible forks:
    ``V<n>``, ``F<n>`` or ``user <reference>``.
    """
    value = by.strip()
    if not value:
        return False
    if v2 == "strict":
        return bool(re.fullmatch(r"user \S.*", value))
    return bool(re.fullmatch(r"(V[1-7]|F\d+|user .+)", value))


def scope_patterns(spec: str, include: bool = True) -> list[str]:
    """Return normalized Scope.in / Scope.out globs.

    A scope line may carry several comma-separated patterns.  Treating the
    whole line as one pattern silently made every multi-path scope unusable.
    """
    prefix = "in:" if include else "out:"
    result: list[str] = []
    for line in section(spec, "Scope"):
        if not line.startswith(prefix):
            continue
        for item in line[len(prefix) :].split(","):
            pattern = item.strip()
            if pattern:
                result.append(pattern)
    return result


def parse_forks(spec: str) -> list[dict[str, str]]:
    result = []
    for line in section(spec, "Forks"):
        match = re.match(r"(F\d+)\s*\|(.*)", line)
        if match:
            result.append(
                {
                    "id": match.group(1),
                    "text": match.group(2),
                    "pick": field(line, "pick"),
                    "by": field(line, "by"),
                    "reversal": field(line, "reversal"),
                }
            )
    return result


def parse_ledger(spec: str, name: str, prefix: str) -> list[dict[str, str]]:
    result = []
    for line in section(spec, name):
        match = re.match(rf"({prefix}\d+)\s*\|(.*)", line)
        if match:
            result.append({"id": match.group(1), "text": match.group(2)})
    return result


HANDOFF_ID = "P0"
HANDOFF_CHOICES = {
    "wait": "Keep the original V4 and leave delivery pending",
    "local-commit": "Commit locally only; no merge or push",
}
PARK_CHOICES = {
    "wait": "Keep this decision pending without asking again",
    "resolve": "Close this parked item with the recorded decision",
}


def park_decisions(directory: Path) -> dict[str, dict]:
    return {
        row["id"]: row
        for row in runs(directory)
        if row.get("kind") == "park-decision" and re.fullmatch(r"user\s+\S.*", str(row.get("by", "")))
    }


def pending_parked(directory: Path, spec: str) -> list[dict]:
    decisions = park_decisions(directory)
    return [
        {**item, "status": "waiting" if decisions.get(item["id"], {}).get("choice") == "wait" else "open"}
        for item in parse_ledger(spec, "Parked", "P")
        if decisions.get(item["id"], {}).get("choice") != "resolve"
    ]


def effective_finish(directory: Path, spec: str) -> tuple[str, str]:
    if package_baseline(directory).get("finish") == "worktree":
        return "worktree", "worktree"
    requested = frozen_charter(directory, spec).get("V4", "commit")
    decision = park_decisions(directory).get(HANDOFF_ID, {})
    if decision.get("choice") == "local-commit" and decision.get("requested") == requested:
        return "commit", requested
    return requested, requested


def handoff_completed(directory: Path, finish: str) -> bool:
    return any(row.get("kind") == "handoff" and row.get("finish") == finish for row in runs(directory))


def resolve_parked(root: Path, slug: str, identifier: str, choice: str, by: str, reason: str) -> dict:
    directory, spec, _ = require_execution_started(root, slug)
    if not re.fullmatch(r"user\s+\S.*", by) or not reason or any(c in by + reason for c in "\r\n"):
        raise ValueError("resolving a parked item requires --by 'user <reference>' and a one-line reason")
    previous = park_decisions(directory).get(identifier, {})
    requested = frozen_charter(directory, spec).get("V4", "commit")
    if identifier == HANDOFF_ID:
        if not package_baseline(directory).get("finish"):
            raise ValueError("handoff choice requires a frozen V4; re-plan this legacy package")
        if frozen_commands(directory, spec, parse_tasks(read_package(root, slug)[2])):
            raise ValueError("handoff choice requires an intact frozen plan")
        live = latest_gate(directory, execution=True)
        eligible = live.get("head") == gitx.head(root) and live.get("tree_fp") == tree_fingerprint(root)
        eligible = eligible and requested in {"merge", "push"} and not pending_parked(directory, spec)
        eligible = eligible and previous.get("requested", requested) == requested
        eligible = eligible and any(
            item.get("code") == "handoff-blocked" and item.get("requested") == requested
            for item in live.get("findings", [])
        )
        choices = HANDOFF_CHOICES
    else:
        eligible = any(item["id"] == identifier for item in pending_parked(directory, spec))
        choices = PARK_CHOICES
    if not eligible or choice not in choices or previous.get("choice") in {choice, "local-commit", "resolve"}:
        raise ValueError(f"decision {identifier} does not offer {choice or 'an empty choice'} on the current package")
    row = dict(kind="park-decision", id=identifier, choice=choice, by=by, reason=reason, requested=requested, ts=now())
    append_run(directory, row)
    return row


def criteria(spec: str) -> list[tuple[str, str, str]]:
    result = []
    for line in section(spec, "Criteria"):
        parts = [part.strip() for part in line.split("|")]
        if len(parts) == 3:
            result.append((parts[0], parts[1], parts[2]))
    return result


@dataclass
class Task:
    id: str
    title: str
    checked: bool
    blocked: bool
    boundary: str
    verify: str
    expect: str
    depends_on: tuple[str, ...]
    origin: str
    block: str

    @property
    def command(self) -> str:
        match = re.search(r"`([^`]+)`", self.verify)
        return match.group(1).strip() if match else ""

    @property
    def globs(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.boundary.split(",") if item.strip())


def parse_tasks(content: str) -> list[Task]:
    records = []
    for match in TASK_BLOCK_RE.finditer(content):
        marker, title, block = match.groups()
        values = {}
        for detail in re.finditer(r"(?m)^\s*-\s*([a-z-]+):\s*(.*)$", block):
            values[detail.group(1)] = detail.group(2).strip()
        task_id = values.get("id", f"task_{len(records) + 1:04d}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", task_id):
            raise ValueError(f"invalid task id: {task_id}")
        records.append(
            Task(
                task_id,
                title.strip(),
                marker.lower() == "x",
                marker == "!",
                values.get("boundary", ""),
                values.get("verify", ""),
                values.get("expect", ""),
                tuple(part.strip() for part in values.get("depends-on", "").split(",") if part.strip()),
                values.get("origin", "planned"),
                block,
            )
        )
    ids = [record.id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task id")
    return records


def dependency_errors(records: list[Task]) -> list[str]:
    known = {record.id for record in records}
    errors = [
        f"{record.id}: unknown dependency {dep}" for record in records for dep in record.depends_on if dep not in known
    ]
    visiting, visited = set(), set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            errors.append(f"dependency cycle: {task_id}")
            return
        if task_id in visited:
            return
        visiting.add(task_id)
        for dep in next(record for record in records if record.id == task_id).depends_on:
            if dep in known:
                visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)

    for record in records:
        visit(record.id)
    return errors


def ready_tasks(records: list[Task]) -> list[Task]:
    if dependency_errors(records):
        return []
    done = {record.id for record in records if record.checked}
    return [
        record
        for record in records
        if not record.checked
        and not record.blocked
        and record.command
        and record.globs
        and all(dep in done for dep in record.depends_on)
    ]


def topological_tasks(records: list[Task]) -> list[Task]:
    """Return a stable dependency order for a valid task graph."""
    errors = dependency_errors(records)
    if errors:
        raise ValueError("; ".join(errors))
    pending = {record.id: record for record in records}
    completed: set[str] = set()
    ordered: list[Task] = []
    while pending:
        ready = next(
            (
                record
                for record in records
                if record.id in pending and all(dep in completed for dep in record.depends_on)
            ),
            None,
        )
        if ready is None:
            raise ValueError("dependency cycle")
        ordered.append(ready)
        completed.add(ready.id)
        del pending[ready.id]
    return ordered


def render_task_marks(tasks: str, marks: dict[str, str]) -> str:
    def render(match: re.Match) -> str:
        identifier = re.search(r"(?m)^\s*- id:\s*(\S+)\s*$", match[3])
        if not identifier or identifier[1] not in marks:
            return match[0]
        return tasks[match.start() : match.start(1)] + marks[identifier[1]] + tasks[match.end(1) : match.end()]

    return TASK_BLOCK_RE.sub(render, tasks)


# Hot-path parse cache for runs.jsonl.  Keyed by the jsonl path and validated
# against its (st_mtime_ns, st_size) stamp; append_run rewrites the file, so a
# stale entry invalidates itself and no write-through is needed.
_RUNS_CACHE: dict[Path, tuple[tuple[int, ...], list[dict]]] = {}


def runs(package: Path) -> list[dict]:
    path = package / "runs.jsonl"
    try:
        info = path.stat()
    except FileNotFoundError:
        _RUNS_CACHE.pop(path, None)
        return []
    stamp = _file_stamp(info)
    cached = _RUNS_CACHE.get(path)
    if cached is not None and cached[0] == stamp:
        return list(cached[1])
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    _RUNS_CACHE[path] = (stamp, records)
    return list(records)


def latest_runs(package: Path) -> dict[str, dict]:
    latest = {}
    for record in runs(package):
        if record.get("kind", "run") == "run" and isinstance(record.get("task"), str):
            latest[record["task"]] = record
        if record.get("kind") != "gate" or record.get("final") is not True:
            continue
        for check in record.get("checks", ()):
            if not isinstance(check, dict) or not isinstance(check.get("tasks"), list):
                continue
            for task in check["tasks"]:
                if isinstance(task, str):
                    latest[task] = dict(check, kind="run", task=task, final=True, tree_fp=record.get("tree_fp"))
    return latest


def record_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def checkpoint_receipt(slug: str, row: dict) -> str:
    return record_digest([slug, {key: value for key, value in row.items() if key != "checkpoint"}])


def _freeze_path(directory: Path) -> Path:
    root = directory.parents[2]
    relative = gitx.stdout(root, "rev-parse", "--git-path", f"spec-freezes/{directory.name}")
    return root / relative


def seal_freeze(directory: Path) -> None:
    target = _freeze_path(directory)
    digest = record_digest([row for row in runs(directory) if row.get("kind") == "baseline"])
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="ascii") as stream:
        stream.write(digest + "\n")


def _freeze_valid(directory: Path, baseline: dict) -> bool:
    if directory.parent.name == "archive" or not baseline:
        return True
    root = directory.parents[2]
    if not (root / ".git").exists():
        return baseline.get("evidence_kernel") != 1
    path = _freeze_path(directory)
    if not path.exists():
        return baseline.get("evidence_kernel") != 1
    digest = record_digest([row for row in runs(directory) if row.get("kind") == "baseline"])
    return path.read_text(encoding="ascii").splitlines()[:1] == [digest]


def worktree_receipt(root: Path, slug: str, digest: str = "") -> str:
    path = _freeze_path(package_dir(root, slug))
    if digest:
        with path.open("a", encoding="ascii") as stream:
            stream.write(digest + "\n")
    lines = path.read_text(encoding="ascii").splitlines() if path.is_file() else []
    return lines[-1] if len(lines) > 1 else ""


def _checkpoint_receipts(directory: Path, baseline: dict) -> dict[str, str]:
    base = str(baseline.get("head", ""))
    if not re.fullmatch(r"[0-9a-f]{40,64}", base):
        return {}
    options = ("-z", "--format=%H%x00%B", "--fixed-strings", f"--grep=Spec: {directory.name}", f"{base}..HEAD")
    log = gitx.git(directory.parents[2], "log", *options, check=False)
    fields = log.stdout.split("\0")
    return {
        commit: match[1]
        for commit, message in zip(fields[::2], fields[1::2])
        if f"Spec: {directory.name}" in message.splitlines()
        if (match := re.search(r"(?m)^Spec-Verify: ([0-9a-f]{64})$", message))
    }


def task_records(directory: Path, tasks: str) -> list[Task]:
    """Project active progress from the ordered ledger; markdown marks are a view."""
    baseline = package_baseline(directory)
    state: dict[str, dict] = {}
    for row in runs(directory):
        key, kind = row.get("task"), row.get("kind", "run")
        if kind == "baseline" and key == "package":
            state.clear()
        if not isinstance(key, str):
            continue
        if kind == "run" and not row.get("final"):
            state[key] = row
        elif kind in {"amend", "unblock"}:
            state.pop(key, None)
    if not _freeze_valid(directory, baseline):
        state.clear()
    sealed = baseline.get("evidence_kernel") == 1
    ok = {key: row for key, row in state.items() if type(row.get("exit")) is int and row["exit"] == 0}
    receipts = _checkpoint_receipts(directory, baseline) if sealed and ok else {}
    records = parse_tasks(tasks)
    for task in records:
        row = state.get(task.id, {}) if baseline else {}
        task.checked = (
            task.id in ok
            and row.get("cmd") == task.command
            and not row.get("checkpoint_error")
            and (not sealed or receipts.get(str(row.get("checkpoint"))) == checkpoint_receipt(directory.name, row))
        )
        task.blocked = row.get("cmd") == task.command and row.get("blocked") is True and not task.checked
    return records


def latest_gate(package: Path, *, execution: bool = False) -> dict:
    return next(
        (row for row in reversed(runs(package)) if row.get("kind") == "gate" and not (execution and row.get("probe"))),
        {},
    )


def delivery_receipt(root: Path, slug: str, revision: str = "HEAD") -> tuple[str, str]:
    options = ("-1", "--format=%H%x00%B", "--fixed-strings", f"--grep=feat(spec): deliver {slug}", revision)
    commit, _, message = gitx.git(root, "log", *options).stdout.partition("\0")
    match = re.search(r"(?m)^Spec-Proof: ([0-9a-f]{64})$", message)
    if match and f"Spec: {slug}" in message.splitlines():
        return commit, match[1]
    return "", ""


def gate_actions(row: dict) -> list[dict]:
    priority = {
        "bound-worktree": 0,
        "restore-plan": 0,
        "plan": 0,
        "isolate-change": 0,
        "run-task": 1,
        "fix-verify": 2,
        "unblock": 3,
        "inspect-delivery": 4,
        "choose-handoff": 6,
    }
    blockers = [item for item in row.get("findings", []) if item.get("severity") == "block"]
    return sorted(blockers, key=lambda item: priority.get(item.get("action"), 4))


def append_run(package: Path, record: dict) -> None:
    with (package / "runs.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


_SNAPSHOT_CONFIG = tuple(
    part
    for option in (
        "core.fileMode=true",
        "core.ignoreStat=false",
        "core.fsmonitor=false",
        "core.trustctime=true",
        "core.checkStat=default",
        "core.splitIndex=false",
    )
    for part in ("-c", option)
)


def product_path(name: bytes) -> bool:
    return name.startswith(b".spec/architecture/") or not (name == b".spec" or name.startswith(b".spec/"))


def _index_paths(root: Path, env: dict, *args: str) -> list[bytes]:
    data = gitx.git(root, *_SNAPSHOT_CONFIG, *args, "-z", env=env, text=False).stdout
    return [name for name in data.split(b"\0") if name]


def _with_index_paths(root: Path, env: dict, paths: list[bytes], *args: str) -> None:
    if paths:
        gitx.git(root, *_SNAPSHOT_CONFIG, *args, env=env, text=False, input=b"\0".join(paths) + b"\0")


def _update_index(root: Path, env: dict, flag: str, paths: list[bytes]) -> None:
    _with_index_paths(root, env, paths, "update-index", flag, "-z", "--stdin")


def _product_entries(root: Path, env: dict) -> list[tuple[bytes, bytes, bytes]]:
    entries, excluded = [], []
    for entry in _index_paths(root, env, "ls-files", "-v", "--stage"):
        metadata, name = entry.split(b"\t", 1)
        tag, mode, _object, stage = metadata.split()
        if not product_path(name):
            excluded.append(name)
        elif stage != b"0":
            raise ValueError(f"unmerged product path: {os.fsdecode(name)}")
        else:
            entries.append((tag, mode, name))
    _update_index(root, env, "--force-remove", excluded)
    return entries


def _write_tree(root: Path, env: dict) -> str:
    return gitx.git(root, *_SNAPSHOT_CONFIG, "write-tree", env=env).stdout.strip()


def staged_product_tree(root: Path, *, index_file: Path | None = None, revision: str | None = None) -> str:
    """The Git product tree at the actual commit boundary, not its working copy."""
    with gitx.temporary_index(root, index_file=index_file, revision=revision) as env:
        _product_entries(root, env)
        return _write_tree(root, env)


def refresh_product_tree(root: Path, env: dict, depth: int = 0) -> tuple[str, str]:
    """Update only changed index entries; the second ID includes unpackaged nested dirt."""
    entries = _product_entries(root, env)
    _update_index(root, env, "--no-assume-unchanged", [name for tag, _, name in entries if tag.islower()])
    present = [name for tag, _, name in entries if tag.upper() == b"S" and os.path.lexists(root / os.fsdecode(name))]
    _update_index(root, env, "--no-skip-worktree", present)
    paths = set(_index_paths(root, env, "diff-files", "--name-only", "--ignore-submodules=none"))
    untracked = _index_paths(root, env, "ls-files", "--others", "--exclude-standard")
    paths.update(name.rstrip(b"/") for name in untracked)
    nested = {name for _, mode, name in entries if mode == b"160000"}
    nested.update(name.rstrip(b"/") for name in untracked if name.endswith(b"/"))
    supplements = []
    for name in sorted(filter(product_path, nested)):
        folder = root / os.fsdecode(name)
        if not (folder / ".git").exists():
            continue
        head = gitx.git(folder, "rev-parse", "--verify", "HEAD^{tree}", check=False)
        if head.returncode:
            paths.discard(name)
        if depth < 8:
            current = tree_fingerprint(folder, depth + 1)
            baseline = staged_product_tree(folder, revision="HEAD") if not head.returncode else ""
            if current != baseline:
                supplements.append((name, current))
    selected, removed = [], []
    for name in sorted(filter(product_path, paths)):
        try:
            kind = stat.S_IFMT((root / os.fsdecode(name)).lstat().st_mode)
        except (FileNotFoundError, NotADirectoryError):
            kind = 0
        if kind in (0, stat.S_IFREG, stat.S_IFLNK, stat.S_IFDIR):
            selected.append(name)
        else:
            removed.append(name)
            supplements.append((name, str(kind)))
    _update_index(root, env, "--force-remove", removed)
    add = ("--literal-pathspecs", "add", "-A", "--sparse", "--pathspec-from-file=-", "--pathspec-file-nul")
    _with_index_paths(root, env, selected, *add)
    tree = _write_tree(root, env)
    if not supplements:
        return tree, tree
    extra = hashlib.sha256(b"\0".join(name + b"\0" + value.encode() for name, value in supplements)).hexdigest()
    return tree, f"{tree}:{extra}"


def tree_fingerprint(root: Path, depth: int = 0) -> str:
    with gitx.temporary_index(root) as env:
        return refresh_product_tree(root, env, depth)[1]


def product_delta(root: Path, env: dict, head: str) -> list[bytes]:
    paths = _index_paths(root, env, "diff", "--cached", "--name-only", "--no-renames", head)
    return [name for name in paths if product_path(name)]


def owned_product_tree(root: Path, env: dict, patterns: tuple[str, ...], head: str) -> str:
    paths = product_delta(root, env, head)
    outside = [name for name in paths if not any(path_matches(os.fsdecode(name), pattern) for pattern in patterns)]
    restore = (
        "--literal-pathspecs",
        "restore",
        f"--source={head}",
        "--staged",
        "--pathspec-from-file=-",
        "--pathspec-file-nul",
    )
    _with_index_paths(root, env, outside, *restore)
    return _write_tree(root, env)


def verify_groups(records: list[Task]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for task in topological_tasks(records):
        groups.setdefault(task.command, []).append(task.id)
    return groups


def goal_fingerprint(spec: str) -> str:
    """Bind the problem definition independently of decisions and parked items."""
    return hashlib.sha256("\n".join(section(spec, "Goal")).encode()).hexdigest()


def criteria_status(spec: str, package: Path, records: list[Task]) -> list[tuple[str, str, str, bool]]:
    latest = latest_runs(package)
    proven = {task.id for task in records if task.checked and latest.get(task.id, {}).get("exit") == 0}
    return [(key, text, task, task in proven) for key, text, task in criteria(spec)]


def path_matches(path: str, pattern: str) -> bool:
    """Match a repository path using component-aware POSIX glob semantics.

    A single ``*`` never crosses ``/``; only ``**`` spans directories. A
    wildcard-free pattern names a file or a directory subtree.
    """
    path_parts = [part for part in _portable_path(path).split("/") if part]
    pattern_clean = _portable_path(pattern.removeprefix("new:")).rstrip("/")
    pattern_parts = [part for part in pattern_clean.split("/") if part]
    if not pattern_parts:
        return False
    if not any(char in pattern_clean for char in "*?["):
        return path_parts[: len(pattern_parts)] == pattern_parts

    memo: dict[tuple[int, int], bool] = {}

    def match(path_index: int, pattern_index: int) -> bool:
        key = (path_index, pattern_index)
        if key in memo:
            return memo[key]
        if pattern_index == len(pattern_parts):
            result = path_index == len(path_parts)
        elif pattern_parts[pattern_index] == "**":
            result = match(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and match(path_index + 1, pattern_index)
            )
        else:
            result = (
                path_index < len(path_parts)
                and fnmatch.fnmatchcase(path_parts[path_index], pattern_parts[pattern_index])
                and match(path_index + 1, pattern_index + 1)
            )
        memo[key] = result
        return result

    return match(0, 0)


def _portable_path(value: str) -> str:
    return value.replace(os.sep, "/") if os.sep != "/" else value


def changed_paths(root: Path, base: str | None = None) -> list[str]:
    """Include checkpointed changes from *base*, plus untracked files."""
    args = ["diff", "--name-only", "--no-renames", "-z", base or "HEAD"]
    tracked = [os.fsdecode(name) for name in gitx.git(root, *args, check=False, text=False).stdout.split(b"\0") if name]
    untracked = [
        os.fsdecode(name)
        for name in gitx.git(root, "ls-files", "--others", "--exclude-standard", "-z", text=False).stdout.split(b"\0")
        if name
    ]
    return sorted(set(tracked + untracked))


def revision_summary(root: Path, slug: str) -> dict:
    """Structured revision summary of the bound package against its plan-go baseline.

    Anchor is the recorded baseline head compared to the current worktree, so
    checkpoint commits and uncommitted edits are both visible in one view.
    """
    package, _spec, tasks = read_package(root, slug)
    base = str(package_baseline(package).get("head") or "")
    if not base:
        raise ValueError("package has not passed plan-go")
    files = []
    for line in gitx.git(root, "diff", "--name-status", base, check=False).stdout.splitlines():
        parts = line.split("\t")
        if not parts[-1].startswith(".spec/"):
            files.append({"status": parts[0], "path": parts[-1]})
    for path in gitx.git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines():
        if path.strip() and not path.startswith(".spec/"):
            files.append({"status": "??", "path": path})
    files.sort(key=lambda item: item["path"])

    baseline = package_baseline(package)
    old_ids = set(baseline.get("task_ids") or [])
    records = parse_tasks(tasks)
    new_ids = {record.id for record in records}
    return {
        "slug": slug,
        "baseline": base,
        "files": files,
        "spec": {
            "added": max(0, len(_spec.splitlines()) - int(baseline.get("spec_lines") or 0)),
            "removed": max(0, int(baseline.get("spec_lines") or 0) - len(_spec.splitlines())),
            "changed": hashlib.sha256(_spec.encode()).hexdigest() != baseline.get("spec_fp"),
        },
        "tasks": {
            "added": max(0, len(tasks.splitlines()) - int(baseline.get("tasks_lines") or 0)),
            "removed": max(0, int(baseline.get("tasks_lines") or 0) - len(tasks.splitlines())),
            "changed": hashlib.sha256(tasks.encode()).hexdigest() != baseline.get("tasks_fp"),
        },
        "task_ids": {
            "added": sorted(new_ids - old_ids),
            "removed": sorted(old_ids - new_ids),
        },
    }


def package_baseline(package: Path) -> dict:
    for record in reversed(runs(package)):
        if record.get("kind") == "baseline" and record.get("task") == "package":
            return record
    return {}


def frozen_commands(directory: Path, spec: str, records: list[Task], *, legacy: bool = False) -> list[str]:
    baseline = package_baseline(directory)
    if not _freeze_valid(directory, baseline):
        return ["plan-go baseline does not match its Git freeze receipt"]
    frozen = set(baseline.get("task_ids") or [])
    if not baseline.get("goal_fp") or not frozen:
        return []
    errors = []
    if goal_fingerprint(spec) != baseline["goal_fp"]:
        errors.append("problem definition changed after plan-go")
    frozen_criteria = {tuple(row) for row in baseline.get("criteria", [])}
    if not frozen_criteria <= set(criteria(spec)):
        errors.append("acceptance criteria changed after plan-go")
    declared = baseline.get("charter_overrides", baseline.get("charter"))
    if declared is not None and parse_charter(spec) != declared:
        errors.append("charter changed after plan-go; use an authorized package decision")
    elif declared is None and "finish" in baseline and parse_charter(spec).get("V4", "commit") != baseline["finish"]:
        errors.append("V4 finish changed after plan-go; use a package decision")
    if baseline.get("branch") and extract_branch(spec) != baseline["branch"]:
        errors.append("branch changed after plan-go")
    if "task_boundaries" in baseline and any(
        task.id in frozen and list(task.globs) != baseline["task_boundaries"].get(task.id) for task in records
    ):
        errors.append("task boundary changed after plan-go; use an authorized plan-amend")
    if "task_depends" in baseline and any(
        task.id in frozen and list(task.depends_on) != baseline["task_depends"].get(task.id) for task in records
    ):
        errors.append("task dependencies changed after plan-go")
    for name in sorted(frozen - {task.id for task in records}):
        errors.append(f"{name}: removed after plan-go; open a new package")
    accepted, amended, old_amends = {}, {}, {}
    v2 = frozen_charter(directory, spec).get("V2", "strict")
    for row in runs(directory):
        task = row.get("task")
        if row.get("kind") == "baseline" and task in frozen:
            accepted[task] = row.get("cmd")
        if row.get("kind") == "amend" and amend_authorized(str(row.get("by", "")), v2):
            amended[task] = row
            old_amends.setdefault(task, set()).add(str(row.get("cmd") or ""))
    for task in records:
        authorization = amended.get(task.id, {})
        skip = task.id not in frozen or not task.command or accepted.get(task.id, task.command) == task.command
        if legacy and (skip or bool({"", task.command} & old_amends.get(task.id, set()))):
            continue
        boundary = authorization.get("boundary", list(task.globs))
        authorized_amend = task.command == authorization.get("cmd") and list(task.globs) == boundary
        if task.id not in frozen and "task_boundaries" in baseline:
            authorized_amend = (
                authorized_amend
                and "boundary" in authorization
                and list(task.depends_on) == authorization.get("depends", [])
            )
        if (
            task.command
            and task.verify == f"`{task.command}`"
            and (task.command == accepted.get(task.id) or authorized_amend)
        ):
            continue
        if task.id not in frozen:
            errors.append(f"{task.id}: added after plan-go; use an authorized plan-amend")
        elif v2 == "strict":
            errors.append(f"{task.id}: verify changed after plan-go")
        else:
            errors.append(f"{task.id}: use plan-amend --revise")
    return errors


def require_execution_started(root: Path, slug: str) -> tuple[Path, str, str]:
    """Require a frozen package on its bound branch before execution."""
    package, spec, tasks = read_package(root, slug)
    if not package_baseline(package):
        raise ValueError("package has not passed plan-go")
    bound = extract_branch(spec)
    current = gitx.branch(root)
    if current != bound:
        raise ValueError(f"package is bound to {bound}, current branch is {current}")
    return package, spec, tasks


def foreign_index_paths(root: Path, patterns: tuple[str, ...], *, local: bool) -> list[str]:
    """Never consume or silently unstage someone else's index entries."""
    result = gitx.git(root, "diff", "--cached", "--name-only", "--no-renames", "-z", text=False)
    paths = (os.fsdecode(raw) for raw in result.stdout.split(b"\0") if raw)
    return [
        path
        for path in paths
        if not any(path_matches(path, pattern) for pattern in patterns)
        and not (local and local_evidence.is_evidence_path(path))
    ]


_GIT_PATH_CHUNK = 200


def _tracked_paths(root: Path, candidates: list[str]) -> set[str]:
    """Batched index lookup: one `ls-files` per 200-path chunk (ARG_MAX guard)."""
    tracked: set[str] = set()
    for start in range(0, len(candidates), _GIT_PATH_CHUNK):
        chunk = candidates[start : start + _GIT_PATH_CHUNK]
        out = gitx.git(root, "--literal-pathspecs", "ls-files", "-z", "--", *chunk, text=False).stdout
        tracked.update(os.fsdecode(name) for name in out.split(b"\0") if name)
    return tracked


def _ignored_paths(root: Path, candidates: list[str]) -> set[str]:
    """Batched ignore lookup: one `check-ignore --stdin` per chunk; exit 1,
    empty out means nothing in the chunk is ignored."""
    ignored: set[str] = set()
    for start in range(0, len(candidates), _GIT_PATH_CHUNK):
        chunk = candidates[start : start + _GIT_PATH_CHUNK]
        payload = b"\0".join(os.fsencode(path) for path in chunk) + b"\0"
        result = gitx.git(root, "check-ignore", "-z", "--stdin", check=False, text=False, input=payload)
        ignored.update(os.fsdecode(name) for name in result.stdout.split(b"\0") if name)
    return ignored


def git_add_paths(root: Path, paths: list[str]) -> None:
    """Stage only concrete changed paths admitted by the requested patterns.

    Pattern matching stays in Python; the per-path `ls-files --error-unmatch` /
    `check-ignore -q` pairs collapse into at most one batched call per chunk
    per question, so a wide boundary no longer costs two git processes per
    changed file.  Selection semantics are unchanged: a path is admitted when
    it is tracked or not ignored.
    """
    changed = gitx.dirty_paths(root, exclude=())
    candidates = sorted(path for path in changed if any(path_matches(path, pattern) for pattern in paths if pattern))
    if any(local_evidence.is_evidence_path(path) for path in candidates) and local_evidence.is_local(root):
        candidates = [path for path in candidates if not local_evidence.is_evidence_path(path)]
    if not candidates:
        return
    tracked = _tracked_paths(root, candidates).intersection(candidates)
    untracked = [path for path in candidates if path not in tracked and os.path.lexists(root / path)]
    ignored = _ignored_paths(root, untracked)
    selected = sorted(tracked.union(path for path in untracked if path not in ignored))
    if selected:
        gitx.git(root, "--literal-pathspecs", "add", "-A", "--", *selected)


def checkpoint_paths(root: Path, slug: str, patterns: tuple[str, ...]) -> tuple[tuple[str, ...], bool]:
    paths = (*patterns, f".spec/specs/{slug}")
    local = local_evidence.is_local(root)
    foreign = foreign_index_paths(root, paths, local=local)
    if foreign:
        raise ValueError("staged path outside the task boundary: " + ", ".join(foreign))
    return paths, local


def checkpoint_commit(root: Path, slug: str, title: str, patterns: tuple[str, ...] = (), *, receipt: str = "") -> str:
    paths, local = checkpoint_paths(root, slug, patterns)
    if local:
        local_evidence.ensure(root)
        local_evidence.untrack(root)
    git_add_paths(root, list(paths))
    status = gitx.git(root, "diff", "--cached", "--quiet", check=False)
    if status.returncode == 0 and not receipt:
        return ""
    message = f"wip({slug}): {title}\n\nSpec: {slug}\n"
    if receipt:
        message += f"Spec-Verify: {receipt}\n"
    gitx.git(root, "commit", *(["--allow-empty"] if receipt else []), "-m", message)
    return gitx.head(root)


# Leave all semantic components below the runner's temporary root intact.
TEMP_ROOT = re.compile(
    rb"(?<![\w/.-])(?:/private)?(?:/var/folders/[^/\s]+/[^/\s]+/T|/tmp)"
    rb"(?:/(?:pytest-of-[^/\s]+/pytest-\d+|tmp[\w-]+|spec-tree-[\w-]+))?(?=$|[/\s'\":),])"
)
OBJECT_ADDRESS = re.compile(rb"(?<= at )0x[\da-fA-F]{6,}\b")
ASSERTION = re.compile(rb"\b(?:assert|AssertionError)\b")
RUN_DURATION = re.compile(
    rb"\b((?:failed|passed|skipped|errors?|warnings?) in |took )\d+(?:\.\d+)? ?(?:[mun]?s|seconds?|sec)\b"
)
VOLATILE_OUTPUT = (
    (re.compile(rb"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?"), b"<ts>"),
    (re.compile(rb"\b\d\d:\d\d:\d\d(?:\.\d+)?\b"), b"<clock>"),
    (RUN_DURATION, rb"\1<dur>"),
    (re.compile(rb"(?i)\b(pid[=: ]?)\d+"), rb"\1<pid>"),
)


def failure_signature(stdout: bytes, stderr: bytes, outcome: str = "") -> str:
    """Hash bounded tails; preserve exit reason, assertion operands and binary stream boundaries."""
    digest = hashlib.sha256(outcome.encode() + b"\0")
    for stream in (stdout, stderr):
        lines = []
        data = OBJECT_ADDRESS.sub(b"0x<addr>", TEMP_ROOT.sub(b"<tmp>", stream[-8192:]))
        for line in data.splitlines(keepends=True):
            if not ASSERTION.search(line):
                for pattern, replacement in VOLATILE_OUTPUT:
                    line = pattern.sub(replacement, line)
            lines.append(line)
        tail = b"".join(lines)[-4096:]
        digest.update(len(tail).to_bytes(2, "big") + tail)
    return digest.hexdigest()[:16]


def subprocess_output(result: subprocess.CompletedProcess) -> tuple[str, str]:
    return result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")

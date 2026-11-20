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
PROTECTED = {"main", "master", "develop"}
CHUNK = 64 * 1024

# Shared suffix of gate stale-evidence errors. Both route.py (next-action
# projection) and spec.py (curable-only delivery path) match on it, so it
# lives in this shared-support module: support modules must not import the
# command entry points.
STALE_EVIDENCE_SUFFIX = "evidence is stale for the current command or tree"


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

    Linked spec worktrees own their package trees, but repo-level memory
    (`.spec/charter.md`, `.spec/lessons.md`) belongs to the primary checkout,
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
_READ_PACKAGE_CACHE: dict[Path, tuple[tuple[tuple[int, int], tuple[int, int]], str, str]] = {}


def read_package(root: Path, slug: str) -> tuple[Path, str, str]:
    directory = package_dir(root, slug)
    spec_path = directory / "spec.md"
    tasks_path = directory / "tasks.md"
    spec_info, tasks_info = spec_path.stat(), tasks_path.stat()
    stamps = ((spec_info.st_mtime_ns, spec_info.st_size), (tasks_info.st_mtime_ns, tasks_info.st_size))
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
            records = parse_tasks((child / "tasks.md").read_text(encoding="utf-8"))
            latest = latest_runs(child)
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
                        "passed": sum(1 for _key, _text, task in criterion if latest.get(task, {}).get("exit") == 0),
                    },
                }
            )
    return result


def extract_branch(spec: str) -> str:
    match = re.search(r"^- branch:\s*`?([^\s`;]+)`?", spec, re.MULTILINE)
    if not match:
        raise ValueError("v3 spec requires branch: <non-protected-branch>")
    branch = match.group(1)
    if branch in PROTECTED or branch.startswith("release/"):
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


def resolve_archived_slug(root: Path, explicit: str | None = None) -> str:
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


DEFAULT_CHARTER = (
    "V1 priority: quality > maintainability > speed > cost | charter.md",
    "V2 scope: adjacent-fixes | charter.md",
    "V3 verification: project | charter.md",
    "V4 finish: commit | charter.md",
    "V5 dependencies: none-new | charter.md",
    "V6 compatibility: keep-public-api | charter.md",
    "V7 blocked: park-and-continue | charter.md",
)


def scaffold_repair(root: Path, origin: str) -> dict:
    """Scaffold a thin-planning repair package from an archived origin.

    The repair keeps the origin's charter and scope so the delivery gate,
    branch binding, and evidence rules stay identical to a standard package;
    only the planning conversation is thinner.
    """
    origin = resolve_archived_slug(root, origin)
    _directory, origin_spec, _tasks = read_archived_package(root, origin)
    slug = f"{now()[:10]}_repair-{origin.split('_', 1)[-1]}"
    directory = package_dir(root, slug)
    if directory.exists():
        raise ValueError(f"repair package already exists: {slug}")
    directory.mkdir(parents=True)
    charter = "\n".join(f"- {line}" for line in section(origin_spec, "Charter"))
    scope = "\n".join(f"- {line}" for line in section(origin_spec, "Scope"))
    # A minimal archived origin may lack Charter/Scope; fall back to the
    # project defaults so the scaffold always passes plan-lint --static and
    # the planner then refines the copied values.
    if not charter:
        charter = "\n".join(f"- {line}" for line in DEFAULT_CHARTER)
    if not scope:
        scope = "- in: app.py"
    patterns = scope_patterns(origin_spec)
    boundary = patterns[0] if patterns else "app.py"
    spec = f"""# Repair {origin}

<!-- spec-format: 3 -->

## Goal
- goal: Repair the delivered package {origin} with a minimal verified change.
- literal ask: "repair {origin}"
- done means: The repair verify passes and the full delivery gate stays green.
- kind: repair
- repair-of: {origin}
## Charter
{charter}
## Scope
{scope}
## Assumptions
- A1 | The archived origin package defines the intended behavior | probe: `ls .spec/specs/archive/{origin}` | CONFIRMED
## Forks
- F1 | when: repair outgrows a fix | a) park for a full package b) widen | pick: a | by: V2 | reversal: reversible
## Criteria
- C1 | The repair verify passes on the final tree | task-repair
## Decisions
## Parked
"""
    (directory / "spec.md").write_text(spec, encoding="utf-8")
    (directory / "tasks.md").write_text(
        f"""- [ ] Implement the minimal repair
  - id: task-repair
  - boundary: {boundary}
  - verify: `true`
  - expect: green
""",
        encoding="utf-8",
    )
    return {"slug": slug, "package": str(directory), "repair_of": origin}


def parse_charter(spec: str) -> dict[str, str]:
    result = {}
    for line in section(spec, "Charter"):
        match = re.match(r"V(\d+)\s+([^:]+):\s*([^|]+)", line)
        if match:
            result[f"V{match.group(1)}"] = match.group(3).strip()
    return result


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
    for match in re.finditer(r"(?ms)^-\s*\[([ x!>])\]\s+(.+?)\n(.*?)(?=^-\s*\[[ x!>]\]\s+|\Z)", content):
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


def tick(root: Path, slug: str, task_id: str, blocked: bool = False) -> None:
    directory, _spec, tasks = read_package(root, slug)
    lines = tasks.splitlines()
    found = False
    task_indexes = [index for index, line in enumerate(lines) if TASK_RE.match(line)]
    for position, index in enumerate(task_indexes):
        match = TASK_RE.match(lines[index])
        if not match:
            continue
        end = task_indexes[position + 1] if position + 1 < len(task_indexes) else len(lines)
        block = "\n".join(lines[index:end])
        if re.search(rf"(?m)^\s*- id:\s*{re.escape(task_id)}\s*$", block):
            lines[index] = re.sub(r"^-\s*\[[ x!>]\]", "- [!]" if blocked else "- [x]", lines[index])
            found = True
            break
    if not found:
        raise ValueError(f"task not found: {task_id}")
    (directory / "tasks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def unblock(root: Path, slug: str, task_id: str) -> None:
    directory, _spec, tasks = read_package(root, slug)
    lines = tasks.splitlines()
    task_indexes = [index for index, line in enumerate(lines) if TASK_RE.match(line)]
    for position, index in enumerate(task_indexes):
        match = TASK_RE.match(lines[index])
        if not match:
            continue
        end = task_indexes[position + 1] if position + 1 < len(task_indexes) else len(lines)
        block = "\n".join(lines[index:end])
        if re.search(rf"(?m)^\s*- id:\s*{re.escape(task_id)}\s*$", block):
            if match.group(1) != "!":
                raise ValueError(f"task is not blocked: {task_id}")
            lines[index] = re.sub(r"^-\s*\[!\]", "- [ ]", lines[index])
            (directory / "tasks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    raise ValueError(f"task not found: {task_id}")


# Hot-path parse cache for runs.jsonl.  Keyed by the jsonl path and validated
# against its (st_mtime_ns, st_size) stamp; append_run rewrites the file, so a
# stale entry invalidates itself and no write-through is needed.
_RUNS_CACHE: dict[Path, tuple[tuple[int, int], list[dict]]] = {}


def runs(package: Path) -> list[dict]:
    path = package / "runs.jsonl"
    try:
        info = path.stat()
    except FileNotFoundError:
        _RUNS_CACHE.pop(path, None)
        return []
    stamp = (info.st_mtime_ns, info.st_size)
    cached = _RUNS_CACHE.get(path)
    if cached is not None and cached[0] == stamp:
        return list(cached[1])
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    _RUNS_CACHE[path] = (stamp, records)
    return list(records)


def latest_runs(package: Path) -> dict[str, dict]:
    return {
        record["task"]: record
        for record in runs(package)
        if record.get("kind", "run") == "run" and isinstance(record.get("task"), str)
    }


def latest_gate(package: Path) -> dict:
    """Return the most recent delivery-gate record, if the harness wrote one."""
    for record in reversed(runs(package)):
        if record.get("kind") == "gate":
            return record
    return {}


def append_run(package: Path, record: dict) -> None:
    with (package / "runs.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _hash_tree(root: Path) -> str:
    """Hash product files and source architecture contracts, excluding local evidence."""
    if gitx.toplevel(root) is None:
        raise ValueError("v3 evidence requires a Git worktree")
    digest = hashlib.sha256()
    files = gitx.git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", text=False).stdout
    for raw in sorted(item for item in files.split(b"\0") if item):
        name = os.fsdecode(raw)
        if name == ".spec" or (name.startswith(".spec/") and not name.startswith(".spec/architecture/")):
            continue
        path = root / name
        digest.update(raw + b"\0")
        if not path.exists() and not path.is_symlink():
            digest.update(b"deleted\0")
            continue
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            digest.update(b"link\0")
            digest.update(os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(mode):
            digest.update(b"executable\0" if mode & stat.S_IXUSR else b"file\0")
            with path.open("rb") as stream:
                while chunk := stream.read(CHUNK):
                    digest.update(chunk)
        else:
            raise ValueError(f"unsupported file type in evidence fingerprint: {name}")
        digest.update(b"\0")
    return digest.hexdigest()


def tree_fingerprint(root: Path) -> str:
    """Hash the current product tree, reading every tracked or untracked file.

    The hash must be recomputed on every call: Git status describes path states,
    not content, so memoizing across calls would let later content edits with an
    unchanged porcelain entry reuse stale evidence. Callers that need one hash
    for several checks pass it explicitly (for example ``gate.compute``).
    """
    return _hash_tree(root)


def goal_fingerprint(spec: str) -> str:
    """Hash the accepted problem definition: the Goal ledger rows.

    plan-go records this hash in the package baseline; the delivery gate
    compares it with the current Goal section, so a post-Go rewrite of the
    problem is visible while Criteria, Decisions and Parked stay editable.
    """
    return hashlib.sha256("\n".join(section(spec, "Goal")).encode()).hexdigest()


def criteria_status(spec: str, package: Path) -> list[tuple[str, str, str, bool]]:
    latest = latest_runs(package)
    return [(key, text, task, latest.get(task, {}).get("exit") == 0) for key, text, task in criteria(spec)]


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
    """Tracked changes against *base* plus all untracked files.

    Passing the package's recorded baseline head is what makes delivery audit
    cumulative: once a task checkpoint commits its edits, a HEAD-relative diff
    would forget them.
    """
    args = ["diff", "--name-only", "-z"] + ([base] if base else ["HEAD"])
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


def _baseline_dirty(root: Path, package: Path) -> set[str]:
    del root
    return set(package_baseline(package).get("dirty", []))


def _unstage_baseline_dirty(root: Path, baseline: set[str], patterns: tuple[str, ...] = ()) -> None:
    """Drop pre-Go dirt from the index unless it sits inside a task boundary.

    Baseline changes matching a task boundary belong to the delivery: they are
    staged by ``git_add_paths`` and must stay so the wip and delivery commits
    carry them. Only baseline paths outside every boundary are unstaged back to
    the worktree.
    """
    for path in sorted(baseline):
        if any(path_matches(path, pattern) for pattern in patterns):
            continue
        if (root / path).exists():
            gitx.git(root, "restore", "--staged", "--", path, check=False)
        else:
            gitx.git(root, "rm", "--cached", "-q", "--", path, check=False)


_GIT_PATH_CHUNK = 200


def _tracked_paths(root: Path, candidates: list[str]) -> set[str]:
    """Batched index lookup: one `ls-files` per 200-path chunk (ARG_MAX guard)."""
    tracked: set[str] = set()
    for start in range(0, len(candidates), _GIT_PATH_CHUNK):
        chunk = candidates[start : start + _GIT_PATH_CHUNK]
        out = gitx.git(root, "ls-files", "-z", "--", *chunk, text=False).stdout
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
    tracked = _tracked_paths(root, candidates)
    untracked = [path for path in candidates if path not in tracked]
    ignored = _ignored_paths(root, untracked)
    selected = [path for path in candidates if path in tracked or path not in ignored]
    if selected:
        gitx.git(root, "add", "-A", "--", *selected)


def checkpoint_commit(root: Path, slug: str, title: str, patterns: tuple[str, ...] = ()) -> str:
    """Commit only the task boundary and its package state.

    `git add -A` would capture unrelated dirt that existed before Go.  The
    package baseline records that dirt, so it is staged and then removed from
    the index again; it remains recoverable in the user's worktree.
    """
    package = package_dir(root, slug)
    if local_evidence.is_local(root):
        # Evidence is local-only: never stage it, and retire any package files
        # still tracked on a branch created before the policy was enabled.
        local_evidence.ensure(root)
        local_evidence.untrack(root)
    if not gitx.dirty_paths(root, exclude=()):
        return ""
    paths = [pattern.removeprefix("new:") for pattern in patterns if pattern]
    paths.extend([f".spec/specs/{slug}", ".spec/charter.md"])
    git_add_paths(root, paths)
    _unstage_baseline_dirty(root, _baseline_dirty(root, package), patterns)
    status = gitx.git(root, "diff", "--cached", "--quiet", check=False)
    if status.returncode == 0:
        return ""
    message = f"wip({slug}): {title}\n\nSpec: {slug}\n"
    gitx.git(root, "commit", "-m", message)
    return gitx.head(root)


def failure_signature(stdout: bytes, stderr: bytes) -> str:
    return hashlib.sha256(stdout[-4096:] + b"\0" + stderr[-4096:]).hexdigest()[:16]


def subprocess_output(result: subprocess.CompletedProcess) -> tuple[str, str]:
    return result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")

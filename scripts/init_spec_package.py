#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""
Initialize a Spec task package under .spec/specs/<slug>.

New Development Records should use YYYY-MM-DD_slug. Legacy slugs remain accepted
so existing projects can still be inspected and completed by later gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

from safe_open_support import SafeOpenError, open_regular_read
from spec_package_support import (
    PROTECTED_BRANCHES,
    PROTECTED_PREFIXES,
    active_branch_bindings,
    active_package_slugs,
    add_specs_dir_arg,
    combined_process_output,
    is_development_record_slug,
    load_reference_templates,
    remote_unavailable_detected,
    resolve_specs_child,
    resolve_specs_root,
    validate_slug,
    write_text,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Initialize a Spec task package under .spec/specs/<slug>. "
            "New Development Records should use YYYY-MM-DD_slug."
        )
    )
    parser.add_argument("--slug", required=True, help="Task package slug, e.g. 2026-06-12_billing-rewrite")
    parser.add_argument("--title", required=True, help="Human-readable project title")
    parser.add_argument(
        "--root",
        default=".",
        help="Project root where the .spec directory should be created (default: current directory)",
    )
    add_specs_dir_arg(parser)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing task package if it already exists",
    )
    parser.add_argument(
        "--branch",
        help="Integration branch to create for this package (default: spec/<slug>)",
    )
    parser.add_argument(
        "--main-branch",
        default="main",
        help="Main branch to start the integration branch from in Git repositories (default: main)",
    )
    parser.add_argument(
        "--skip-git-branch",
        action="store_true",
        help="Do not create/switch Git branches before writing the package",
    )
    parser.add_argument(
        "--adopt",
        action="store_true",
        help=(
            "Explicit takeover of in-flight work: bind the package to the CURRENT non-protected branch and record the "
            "dirty baseline, without switching branch, stashing, committing, or touching anyone's files"
        ),
    )
    parser.add_argument(
        "--confirm-adopt",
        action="store_true",
        help="Required with --adopt: confirm that binding in-flight work to a package is intended",
    )
    parser.add_argument(
        "--teammate",
        action="append",
        default=[],
        help="With --adopt: name of an existing teammate working in this tree (reused, never recreated)",
    )
    parser.add_argument(
        "--writers-paused",
        action="store_true",
        help="With --adopt: operator attests running writers were paused or handed off (recorded as a claim)",
    )
    parser.add_argument(
        "--disable-git-hooks",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if args.adopt:
        conflicting = [
            flag
            for flag, used in (("--branch", args.branch is not None), ("--skip-git-branch", args.skip_git_branch))
            if used
        ]
        if conflicting:
            parser.error(
                "cannot be combined with --adopt: " + ", ".join(conflicting) + " (adoption binds the current branch; "
                "it never creates or skips one)"
            )
    else:
        adopt_only = [
            flag
            for flag, used in (
                ("--confirm-adopt", args.confirm_adopt),
                ("--teammate", bool(args.teammate)),
                ("--writers-paused", args.writers_paused),
            )
            if used
        ]
        if adopt_only:
            parser.error("these options need --adopt: " + ", ".join(adopt_only))
    return args


def render_template(
    template: str,
    title: str,
    integration_branch: str | None = None,
    branch_note: str | None = None,
) -> str:
    rendered = template.replace("[项目名称]", title)
    if integration_branch:
        replacement = f"Git integration branch：`{integration_branch}`"
        if branch_note:
            replacement = f"{replacement}；{branch_note}"
        rendered = rendered.replace(
            "Git integration branch：`spec/YYYY-MM-DD_<slug>` 或适用外理由",
            replacement,
        )
    return rendered


class GitPreflightError(RuntimeError):
    pass


class BranchCreation(NamedTuple):
    branch: str | None
    mode: str = "synced"
    note: str = ""


LOCAL_BRANCH_NOTE = "本地模式：upstream fetch 传输失败，已从本地主分支创建；网络恢复后由 push 再同步远端"


def git(root: Path, *args: str, check: bool = True, disable_hooks: bool = False) -> subprocess.CompletedProcess[str]:
    command = ["git"]
    if disable_hooks:
        command.extend(["-c", "core.hooksPath=/dev/null"])
    command.extend(args)
    return subprocess.run(
        command,
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
    )


def git_stdout(root: Path, *args: str, disable_hooks: bool = False) -> str:
    return git(root, *args, disable_hooks=disable_hooks).stdout.strip()


def repository_root(root: Path) -> Path | None:
    result = git(root, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def current_branch(root: Path) -> str:
    branch = git_stdout(root, "branch", "--show-current")
    if not branch:
        raise GitPreflightError("cannot determine current Git branch")
    return branch


def ensure_safe_branch_name(root: Path, branch: str, label: str) -> None:
    if not branch or branch.startswith("-") or branch.startswith("refs/"):
        raise GitPreflightError(f"invalid {label}: {branch}")
    result = git(root, "check-ref-format", "--branch", branch, check=False)
    if result.returncode != 0:
        raise GitPreflightError(f"invalid {label}: {branch}")


def ensure_not_protected(branch: str) -> None:
    if branch in PROTECTED_BRANCHES or branch.startswith(PROTECTED_PREFIXES):
        raise GitPreflightError(f"refusing to use protected integration branch: {branch}")


def _unquote_porcelain_path(field: str) -> str:
    """Unquote one C-quoted porcelain path; decode failure keeps the raw
    text so the entry stays blocking dirt (fail closed)."""
    if field.startswith('"') and field.endswith('"') and len(field) >= 2:
        body = field[1:-1]
        try:
            return body.encode("latin-1", "backslashreplace").decode("unicode_escape").encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return field
    return field


def _porcelain_line_paths(status_line: str) -> list[str]:
    """File paths named by one ``git status --porcelain`` line.

    Rename lines carry two paths (``old -> new``); both sides of the rename
    must be active-package documents for the line to be carried.
    """
    if len(status_line) < 4:
        return []
    field = status_line[3:]
    sides = field.split(" -> ", 1) if " -> " in field else [field]
    return [_unquote_porcelain_path(side) for side in sides]


def _is_active_package_document(path: str, specs_roots: list[Path], repo_root: Path) -> bool:
    """True when the path lives inside a live active package's own directory.

    Only ``<specs-root>/specs/<slug>/...`` counts: the slug must be a valid
    Development Record slug, must exist as a directory on disk, and must not
    be the ``archive/`` container (archive dirt still blocks /spec:new).
    """
    candidate = Path(path)
    if candidate.is_absolute():
        return False
    for specs_root in specs_roots:
        try:
            relative = candidate.relative_to(specs_root)
        except ValueError:
            continue
        parts = relative.parts
        if len(parts) < 2 or parts[0] != "specs" or parts[1] == "archive":
            continue
        slug = parts[1]
        if not is_development_record_slug(slug):
            continue
        if not (repo_root / specs_root / "specs" / slug).is_dir():
            continue
        return True
    return False


def ensure_clean_tree(root: Path, specs_root: Path | None = None) -> None:
    # --untracked-files=all keeps untracked entries as file paths so a fresh
    # package's documents can be attributed to their slug instead of showing
    # up as one collapsed ".spec/" line that cannot be attributed (AUD-07:
    # a previous package's uncommitted documents must not block /spec:new).
    dirty = git_stdout(root, "status", "--porcelain", "--untracked-files=all")
    if not dirty:
        return
    specs_roots = [name for name in (Path(".spec"), Path(".trae")) if (root / name).is_dir()]
    if specs_root is not None:
        try:
            primary = specs_root.resolve(strict=False).relative_to(root.resolve(strict=False))
        except ValueError:
            primary = None
        if primary is not None and primary not in specs_roots:
            specs_roots.append(primary)
    residual: list[str] = []
    for line in dirty.splitlines():
        paths = _porcelain_line_paths(line)
        if paths and all(_is_active_package_document(path, specs_roots, root) for path in paths):
            continue
        residual.append(line)
    if not residual:
        return
    raise GitPreflightError(
        "working tree is not clean; /spec:new must start from a clean tree before "
        "checking out main and creating an independent integration branch\n"
        "active task package documents (<specs>/specs/<slug>/) are carried automatically; "
        "everything below is unrelated work:\n" + "\n".join(residual) + "\n"
        "action: commit, stash, or move unrelated work to its own Development Record before creating this package"
    )


def ensure_local_branch_exists(root: Path, branch: str, label: str) -> None:
    result = git(root, "show-ref", "--verify", f"refs/heads/{branch}", check=False)
    if result.returncode != 0:
        raise GitPreflightError(f"required local {label} does not exist: {branch}")


def fast_forward_main_if_upstream_exists(
    root: Path,
    main_branch: str,
    *,
    disable_hooks: bool = False,
) -> str:
    """Fast-forward main to upstream when reachable.

    Returns ``synced`` when fetch succeeded or no upstream is configured.
    Returns ``local`` when fetch failed with a transport-layer outage so the
    caller can still create the integration branch from local main.
    """
    upstream = git(
        root,
        "rev-parse",
        "--abbrev-ref",
        f"{main_branch}@{{upstream}}",
        check=False,
        disable_hooks=disable_hooks,
    )
    if upstream.returncode != 0:
        return "synced"
    upstream_ref = upstream.stdout.strip()
    remote = upstream_ref.split("/", 1)[0]
    fetch = git(root, "fetch", remote, check=False, disable_hooks=disable_hooks)
    if fetch.returncode != 0:
        output = combined_process_output(fetch)
        if remote_unavailable_detected(output):
            return "local"
        raise GitPreflightError(f"failed to fetch upstream for {main_branch}: {output}")
    merge = git(root, "merge", "--ff-only", upstream_ref, check=False, disable_hooks=disable_hooks)
    if merge.returncode != 0:
        raise GitPreflightError(
            f"local {main_branch} cannot fast-forward to {upstream_ref}; resolve main before /spec:new\n"
            f"{combined_process_output(merge)}"
        )
    return "synced"


def create_integration_branch_from_main(
    target_root: Path,
    slug: str,
    main_branch: str,
    integration_branch: str | None,
    *,
    skip: bool = False,
    disable_hooks: bool = False,
    specs_root: Path | None = None,
) -> BranchCreation:
    repo_root = repository_root(target_root)
    if repo_root is None or skip:
        return BranchCreation(None, "skipped")

    branch = integration_branch or f"spec/{slug}"
    ensure_safe_branch_name(repo_root, main_branch, "main branch")
    ensure_safe_branch_name(repo_root, branch, "integration branch")
    ensure_not_protected(branch)
    bindings = active_branch_bindings(repo_root)
    owners = tuple(owner for owner in bindings.get(branch, ()) if owner != slug)
    if owners:
        raise GitPreflightError(
            f"integration branch {branch} is already bound to active task package(s): {', '.join(owners)}"
        )
    ensure_clean_tree(repo_root, specs_root=specs_root)
    ensure_local_branch_exists(repo_root, main_branch, "main branch")

    branch_exists = (
        git(
            repo_root,
            "show-ref",
            "--verify",
            f"refs/heads/{branch}",
            check=False,
            disable_hooks=disable_hooks,
        ).returncode
        == 0
    )
    if branch_exists:
        raise GitPreflightError(
            f"integration branch already exists: {branch};"
            " choose --branch for a distinct package branch or resume that branch explicitly"
        )

    original_branch = current_branch(repo_root)
    try:
        if original_branch != main_branch:
            git(repo_root, "checkout", main_branch, disable_hooks=disable_hooks)
        branch_mode = fast_forward_main_if_upstream_exists(repo_root, main_branch, disable_hooks=disable_hooks)
        git(repo_root, "checkout", "-b", branch, disable_hooks=disable_hooks)
    except Exception:
        current = current_branch(repo_root) if repository_root(repo_root) else ""
        if current != original_branch and not git_stdout(
            repo_root,
            "status",
            "--porcelain",
            disable_hooks=disable_hooks,
        ):
            git(repo_root, "checkout", original_branch, check=False, disable_hooks=disable_hooks)
        raise
    if branch_mode == "local":
        return BranchCreation(branch, "local", LOCAL_BRANCH_NOTE)
    return BranchCreation(branch, "synced")


TEAMMATE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ADOPTION_FILE = "adoption.json"
MAX_BASELINE_ENTRIES = 500
MAX_HASHED_FILE_BYTES = 64 * 1024 * 1024  # larger files are listed, never read whole
ADOPT_LOCK_NAME = ".adopt.lock"
#: Names the archive layout uses below ``specs/``; a package must never take one.
RESERVED_SLUGS = frozenset({"archive", "retired"})


class AdoptionError(RuntimeError):
    pass


def _baseline_entry(repo_root: Path, path: str, status: str) -> dict[str, str]:
    entry: dict[str, str] = {"path": path, "status": status.strip() or "?"}
    digest = ""
    target = repo_root / path
    try:
        if target.is_file() and not target.is_symlink():
            if target.stat().st_size > MAX_HASHED_FILE_BYTES:
                entry["skipped"] = "too_large"
            else:
                hasher = hashlib.sha256()
                with target.open("rb") as handle:
                    for block in iter(lambda: handle.read(1 << 20), b""):
                        hasher.update(block)
                digest = hasher.hexdigest()
    except OSError:
        entry["skipped"] = "unreadable"  # listed by path; an unreadable file must not abort the adoption
        digest = ""
    entry["sha256"] = digest
    entry["path"] = entry["path"].encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
    return entry


def _dirty_baseline(repo_root: Path, exclude: str | None = None) -> tuple[list[dict[str, str]], int]:
    """Path, status and content hash of every dirty entry; contents are never stored.

    Returns the recorded entries (at most MAX_BASELINE_ENTRIES) and the total dirty count, so a
    truncated record never passes for the whole tree. ``exclude`` is the adopter's own lock directory
    (repo-relative); teammates' edits under the specs tree are recorded like any other entry.
    """
    # --no-optional-locks: a plain `git status` may refresh and rewrite .git/index (and fight a
    # teammate for index.lock); adoption must stay strictly read-only on Git state.
    completed = subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=all"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    # bytes -> str with surrogateescape: a path git reports in another encoding must not abort adoption
    raw = completed.stdout.decode("utf-8", errors="surrogateescape")
    entries: list[dict[str, str]] = []
    total = 0
    fields = [field for field in raw.split("\0") if field]
    index = 0
    while index < len(fields):
        record = fields[index]
        status, path = record[:2], record[3:]
        if "R" in status or "C" in status:
            index += 1  # rename/copy carries the origin path as a second field
        index += 1
        if exclude and (path == exclude or path.startswith(exclude + "/")):
            continue
        total += 1
        if len(entries) < MAX_BASELINE_ENTRIES:
            entries.append(_baseline_entry(repo_root, path, status))
    return entries, total


def _repo_relative(path: Path, repo_root: Path) -> str | None:
    try:
        return path.resolve(strict=False).relative_to(repo_root).as_posix()
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


OWNERLESS_GRACE_SECONDS = 120
MAX_OWNER_BYTES = 64


def _read_owner(lock_dir: Path) -> str | None:
    """The owner record as written (stripped), or None when it is absent, unreadable, or not a small
    regular file. A FIFO or device planted as the owner is never read (reading one can block forever)."""
    owner = lock_dir / "owner"
    try:
        with open_regular_read(owner, max_bytes=MAX_OWNER_BYTES) as descriptor:
            data = os.read(descriptor, MAX_OWNER_BYTES + 1)
    except SafeOpenError as exc:
        if exc.reason != "unsupported":
            return None
        try:  # platforms without O_NOFOLLOW (Windows): no FIFOs or devices to plant there
            with owner.open("rb") as handle:
                data = handle.read(MAX_OWNER_BYTES + 1)
        except OSError:
            return None
    except OSError:
        return None
    return data.decode("utf-8", errors="replace").strip()


def _claim_stale_lock(lock_dir: Path, observed_owner: str | None) -> bool:
    """Atomically take a stale lock out of the way; True only if WE removed this exact lock.

    Reading the owner and deleting it are two steps, so a second adopter could replace the lock in
    between and have its fresh lock deleted. Renaming the directory to a unique name is a single
    atomic step: whoever wins the rename owns the stale lock, and we then verify it is still a plain
    directory holding the owner record we judged dead before discarding it.
    """
    graveyard = lock_dir.with_name(f".adopt.lock.stale-{os.getpid()}-{time.time_ns()}")
    try:
        os.rename(lock_dir, graveyard)
    except OSError:
        return False
    if graveyard.is_symlink() or not graveyard.is_dir() or _read_owner(graveyard) != observed_owner:
        # swapped for a link or changed hands before our rename: put it back if the slot is free, never delete it
        try:
            os.rename(graveyard, lock_dir)
        except OSError:
            pass
        return False
    try:
        (graveyard / "owner").unlink(missing_ok=True)
        graveyard.rmdir()
    except OSError:
        pass  # already out of the way under a unique name; anything unexpected inside is left for inspection
    return True


def _acquire_adoption_lock(lock_dir: Path) -> None:
    """Exclusive per-project lock; only a provably dead or long-abandoned holder is displaced.

    A lock with a live owner is never taken. A lock without a usable owner record (missing, empty, or
    not a pid) is either a holder between mkdir and its owner write (seconds) or a crash leftover; it
    is reclaimed only after a grace window. A lock path that is not a plain directory is never followed.
    """
    busy = AdoptionError("another adoption is in progress for this project; retry after it finishes")
    for _attempt in range(3):
        try:
            lock_dir.mkdir()  # atomic: a concurrent adopter fails here
        except FileExistsError:
            if lock_dir.is_symlink() or not lock_dir.is_dir():
                # stale-lock cleanup would act on whatever a link points at
                raise AdoptionError(
                    f"refusing the adoption lock: {lock_dir} is not a plain directory; remove it by hand"
                ) from None
            observed = _read_owner(lock_dir)
            try:
                holder = int(observed) if observed is not None else None
            except ValueError:
                holder = None
            if holder is not None and not 0 < holder < 2**31:
                # not one process: 0 and negatives address process groups, larger values overflow pid_t
                holder = None
            if holder is not None:
                if _pid_alive(holder):
                    raise busy from None
            else:
                try:
                    age = time.time() - lock_dir.stat().st_mtime
                except OSError:
                    continue  # it vanished: retry the mkdir
                if age < OWNERLESS_GRACE_SECONDS:
                    raise busy from None
            if not _claim_stale_lock(lock_dir, observed):
                raise busy from None
            continue
        try:
            (lock_dir / "owner").write_text(str(os.getpid()), encoding="utf-8")
        except OSError as exc:
            # an ownerless lock would block every adoption for the grace window
            _release_adoption_lock(lock_dir)
            raise AdoptionError(
                f"could not record the adoption lock owner ({exc.__class__.__name__}); nothing was adopted"
            ) from exc
        return
    raise busy


def _release_adoption_lock(lock_dir: Path) -> None:
    try:
        (lock_dir / "owner").unlink(missing_ok=True)
        lock_dir.rmdir()
    except OSError:
        pass


def adopt_in_flight_work(args: argparse.Namespace, slug: str, title: str, specs_root: Path, skill_root: Path) -> int:
    """Bind a new package to the current branch; strictly additive under .spec/."""
    if not args.confirm_adopt:
        raise AdoptionError("--adopt requires --confirm-adopt: binding in-flight work to a package must be deliberate")
    target_root = Path(args.root).resolve()
    if not target_root.is_dir():
        raise AdoptionError(f"adoption needs an existing project directory; {target_root} is not a directory")
    repo_root = repository_root(target_root)
    if repo_root is None:
        raise AdoptionError("adoption needs a Git repository; this directory is not one")
    for name in args.teammate:
        if not TEAMMATE_RE.fullmatch(name):
            raise AdoptionError("teammate names must be 1-64 letters, digits, '.', '_' or '-'")
    branch = git_stdout(repo_root, "branch", "--show-current")
    if not branch:
        raise AdoptionError("adoption needs a named branch; detached HEAD cannot be bound")
    ensure_safe_branch_name(repo_root, branch, "current branch")
    try:
        ensure_not_protected(branch)
        if branch == args.main_branch:
            raise GitPreflightError(
                f"refusing to use protected integration branch: {branch} (the declared --main-branch)"
            )
    except GitPreflightError as exc:
        raise AdoptionError(str(exc)) from exc
    package_dir = resolve_specs_child(specs_root, "specs", slug)
    lock_dir = specs_root / ADOPT_LOCK_NAME
    specs_root.mkdir(parents=True, exist_ok=True)
    _acquire_adoption_lock(lock_dir)
    try:
        return _adopt_locked(args, slug, title, specs_root, skill_root, repo_root, branch, package_dir)
    finally:
        _release_adoption_lock(lock_dir)


def _adopt_locked(
    args: argparse.Namespace,
    slug: str,
    title: str,
    specs_root: Path,
    skill_root: Path,
    repo_root: Path,
    branch: str,
    package_dir: Path,
) -> int:
    if package_dir.exists():
        raise AdoptionError(f"task package already exists: {package_dir}")
    owners = active_branch_bindings(repo_root).get(branch, ())
    if owners:
        raise AdoptionError(f"branch {branch} is already bound to active task package(s): {', '.join(owners)}")
    foreign = active_package_slugs(resolve_specs_child(specs_root, "specs"))
    if foreign:
        raise AdoptionError(
            "ambiguous ownership: another active package exists (" + ", ".join(foreign) + "); "
            "finish or archive it before adopting this work"
        )
    templates = load_reference_templates(skill_root)
    head = git_stdout(repo_root, "rev-parse", "HEAD")
    dirty, dirty_total = _dirty_baseline(repo_root, _repo_relative(specs_root / ADOPT_LOCK_NAME, repo_root))
    baseline = {
        "version": 1,
        "branch": branch,
        "head": head,
        "dirty": dirty,
        "dirty_total": dirty_total,
        "dirty_truncated": dirty_total > len(dirty),
        "teammates": list(args.teammate),
        "coordinator": "main-session",
        "writers_paused": bool(args.writers_paused),
        "writers_paused_source": "operator-attested" if args.writers_paused else "none",
    }
    note = f"接管在途工作：绑定当前分支，基线 {head[:12]}，不切换分支、不改动任何队友文件"
    staging = package_dir.with_name(f".{package_dir.name}.adopt-{os.getpid()}")
    created = False
    try:
        staging.mkdir(parents=True)
        created = True
        for filename, template in templates.items():
            write_text(staging / filename, render_template(template, title, branch, note))
        write_text(staging / ADOPTION_FILE, json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        package_dir.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staging, package_dir)  # atomic claim: a concurrent adopter loses here
    except OSError as exc:
        raise AdoptionError(
            f"could not claim the package atomically ({exc.__class__.__name__}); nothing was written"
        ) from exc
    finally:
        # clean only the directory this call created; a pre-existing path (or a link) is never followed
        if created and staging.is_dir() and not staging.is_symlink():
            for leftover in staging.iterdir():
                leftover.unlink()
            staging.rmdir()
    print(f"adopted: {package_dir}")
    print(f"branch: {branch} (unchanged)")
    if baseline["dirty_truncated"]:
        print(
            f"baseline: {len(dirty)} of {dirty_total} dirty entries recorded by hash "
            f"(truncated at {MAX_BASELINE_ENTRIES}; the rest are counted, not listed)"
        )
    else:
        print(f"baseline: {len(dirty)} dirty entries recorded by hash")
    if not args.writers_paused:
        print(
            "note: running writers were not confirmed paused. Ask each teammate to pause or hand off at a clean point "
            "before assuming a stable baseline; adoption does not stop or direct anyone."
        )
    if not branch.startswith("spec/"):
        print(
            "note: this branch is outside `spec/`. With the Spec git hooks installed, pre-commit runs the full "
            "gate here, so commits (teammates' included) fail until this package passes its check, and spec:push "
            f"merges only `spec/` branches. Rename it now (git branch -m {shlex.quote(branch)} spec/<name>) and "
            "update the "
            "package's `Git integration branch` line to match."
        )
    return 0


def main() -> int:
    args = parse_args()

    try:
        slug = validate_slug(args.slug.strip())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if slug in RESERVED_SLUGS:
        print(f"error: slug '{slug}' is reserved for the archive layout under specs/", file=sys.stderr)
        return 1

    title = args.title.strip()
    if not title:
        print("error: title cannot be empty", file=sys.stderr)
        return 1

    target_root = Path(args.root).resolve()
    skill_root = Path(__file__).resolve().parent.parent
    try:
        specs_root = resolve_specs_root(target_root, args.specs_dir)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        package_dir = resolve_specs_child(specs_root, "specs", slug)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if package_dir.exists() and not args.force:
        print(f"error: task package already exists: {package_dir}", file=sys.stderr)
        return 1

    if args.adopt:
        try:
            return adopt_in_flight_work(args, slug, title, specs_root, skill_root)
        except (AdoptionError, GitPreflightError, ValueError, subprocess.CalledProcessError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    # Load templates BEFORE creating branches so a template failure doesn't
    # leave the user stranded on a feature branch with no package files.
    # The spec/tasks templates are required; the checklist template is
    # optional (v2 packages ship no checklist.md — F11), so whatever
    # load_reference_templates returns is exactly what gets rendered.
    try:
        templates = load_reference_templates(skill_root)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        creation = create_integration_branch_from_main(
            target_root,
            slug,
            args.main_branch.strip(),
            args.branch.strip() if args.branch else None,
            skip=args.skip_git_branch,
            disable_hooks=args.disable_git_hooks,
            specs_root=specs_root,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, GitPreflightError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    package_dir.mkdir(parents=True, exist_ok=True)

    for filename, template in templates.items():
        try:
            target_path = resolve_specs_child(specs_root, "specs", slug, filename)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        rendered = render_template(template, title, creation.branch, creation.note or None)
        write_text(target_path, rendered)

    if creation.branch:
        print(f"branch: {creation.branch}")
        if creation.mode == "local":
            print("branch-mode: local")
            print(f"note: {creation.note}")
    for filename in templates:
        print(f"created: {package_dir / filename}")
    print(f"ready: {package_dir}")
    if repository_root(target_root) is not None:
        print(
            "hint: install the Spec git hooks to enforce the gates on commit/push: "
            f"python3 {Path(__file__).resolve().parent / 'install_git_hooks.py'} --root <repo>"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

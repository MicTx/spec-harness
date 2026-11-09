#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Initialize a task package; adopt in-flight work or branch from clean main."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import gitx
from spec_package_support import (
    PACKAGE_WORKFLOWS,
    PROTECTED_BRANCHES,
    PROTECTED_PREFIXES,
    active_branch_bindings,
    active_package_slugs,
    add_specs_dir_arg,
    combined_process_output,
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
    parser.add_argument("--root", default=".", help="Project root containing .spec (default: current directory)")
    add_specs_dir_arg(parser)
    parser.add_argument(
        "--workflow",
        choices=PACKAGE_WORKFLOWS,
        default="lite",
        help="Package tier: lite (default) or full (adds intent and option ledgers for risky work)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing task package")
    parser.add_argument("--branch", help="Integration branch to create (default: spec/<slug>)")
    parser.add_argument(
        "--adopt",
        action="store_true",
        help=(
            "Explicit takeover of in-flight work: bind the package to the CURRENT non-protected branch and record "
            "the dirty baseline, without switching branch, stashing, committing, or touching anyone's files"
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
    args = parser.parse_args()
    if args.adopt:
        if args.branch is not None:
            parser.error("--branch cannot be combined with --adopt; adoption binds the current branch")
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


class AdoptionError(RuntimeError):
    pass


class BranchCreation(NamedTuple):
    branch: str | None
    mode: str = "synced"
    note: str = ""


LOCAL_BRANCH_NOTE = "本地模式：upstream fetch 传输失败，已从本地主分支创建；网络恢复后由 push 再同步远端"


def ensure_safe_branch_name(root: Path, branch: str, label: str) -> None:
    if not branch or branch.startswith("-") or branch.startswith("refs/"):
        raise GitPreflightError(f"invalid {label}: {branch}")
    if gitx.git(root, "check-ref-format", "--branch", branch, check=False).returncode != 0:
        raise GitPreflightError(f"invalid {label}: {branch}")


def ensure_not_protected(branch: str) -> None:
    if branch in PROTECTED_BRANCHES or branch.startswith(PROTECTED_PREFIXES):
        raise GitPreflightError(f"refusing to use protected integration branch: {branch}")


def fast_forward_main_if_upstream_exists(root: Path, main_branch: str) -> str:
    """Fast-forward main to upstream; return ``local`` on a transport outage."""
    upstream = gitx.git(root, "rev-parse", "--abbrev-ref", f"{main_branch}@{{upstream}}", check=False)
    if upstream.returncode != 0:
        return "synced"
    upstream_ref = upstream.stdout.strip()
    remote = upstream_ref.split("/", 1)[0]
    fetch = gitx.git(root, "fetch", remote, check=False)
    if fetch.returncode != 0:
        output = combined_process_output(fetch)
        if remote_unavailable_detected(output):
            return "local"
        raise GitPreflightError(f"failed to fetch upstream for {main_branch}: {output}")
    merge = gitx.git(root, "merge", "--ff-only", upstream_ref, check=False)
    if merge.returncode != 0:
        raise GitPreflightError(
            f"local {main_branch} cannot fast-forward to {upstream_ref}; "
            f"resolve main before /spec:new\n{combined_process_output(merge)}"
        )
    return "synced"


def create_integration_branch_from_main(
    target_root: Path,
    slug: str,
    main_branch: str,
    integration_branch: str | None,
) -> BranchCreation:
    repo_root = gitx.toplevel(target_root)
    if repo_root is None:
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
    residual = gitx.dirty_paths(repo_root)
    if residual:
        raise GitPreflightError(
            "working tree is not clean; /spec:new needs a clean tree outside .spec/ before creating a branch\n"
            + "\n".join(residual)
            + "\naction: commit, stash, or move unrelated work to its own Development Record"
        )
    if gitx.git(repo_root, "show-ref", "--verify", f"refs/heads/{main_branch}", check=False).returncode != 0:
        raise GitPreflightError(f"required local main branch does not exist: {main_branch}")
    if gitx.git(repo_root, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0:
        raise GitPreflightError(
            f"integration branch already exists: {branch};"
            " choose --branch for a distinct package branch or resume that branch explicitly"
        )

    original_branch = gitx.branch(repo_root)
    if not original_branch:
        raise GitPreflightError("cannot determine current Git branch")
    try:
        if original_branch != main_branch:
            gitx.git(repo_root, "checkout", main_branch)
        branch_mode = fast_forward_main_if_upstream_exists(repo_root, main_branch)
        gitx.git(repo_root, "checkout", "-b", branch)
    except Exception:
        if gitx.branch(repo_root) != original_branch and not gitx.dirty_paths(repo_root):
            gitx.git(repo_root, "checkout", original_branch, check=False)
        raise
    return BranchCreation(branch, branch_mode, LOCAL_BRANCH_NOTE if branch_mode == "local" else "")


TEAMMATE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ADOPTION_FILE = "adoption.json"
RESERVED_SLUGS = frozenset({"archive", "retired"})


def adopt_in_flight_work(args: argparse.Namespace, slug: str, title: str, specs_root: Path, skill_root: Path) -> int:
    """Bind a new package to the current branch; strictly additive under Spec state."""
    if not args.confirm_adopt:
        raise AdoptionError("--adopt requires --confirm-adopt: binding in-flight work to a package must be deliberate")
    target_root = Path(args.root).resolve()
    if not target_root.is_dir():
        raise AdoptionError(f"adoption needs an existing project directory; {target_root} is not a directory")
    repo_root = gitx.toplevel(target_root)
    if repo_root is None:
        raise AdoptionError("adoption needs a Git repository; this directory is not one")
    for name in args.teammate:
        if not TEAMMATE_RE.fullmatch(name):
            raise AdoptionError("teammate names must be 1-64 letters, digits, '.', '_' or '-'")
    branch = gitx.branch(repo_root)
    if not branch:
        raise AdoptionError("adoption needs a named branch; detached HEAD cannot be bound")
    ensure_safe_branch_name(repo_root, branch, "current branch")
    ensure_not_protected(branch)

    package_dir = resolve_specs_child(specs_root, "specs", slug)
    if package_dir.resolve() != package_dir:
        raise AdoptionError("package write path must not contain symlinks")
    force = getattr(args, "force", False)
    if package_dir.exists() and not force:
        raise AdoptionError(f"task package already exists: {package_dir}")
    owners = tuple(owner for owner in active_branch_bindings(repo_root).get(branch, ()) if owner != slug or not force)
    if owners:
        raise AdoptionError(f"branch {branch} is already bound to active task package(s): {', '.join(owners)}")
    foreign = [owner for owner in active_package_slugs(resolve_specs_child(specs_root, "specs")) if owner != slug]
    if foreign:
        raise AdoptionError(
            "ambiguous ownership: another active package exists (" + ", ".join(foreign) + "); "
            "finish or archive it before adopting this work"
        )

    templates = load_reference_templates(skill_root, args.workflow)
    outputs = {name: resolve_specs_child(specs_root, "specs", slug, name) for name in (*templates, ADOPTION_FILE)}
    head = gitx.head(repo_root)
    if not head:
        raise AdoptionError("adoption needs an existing Git commit")
    status = gitx.git(repo_root, "status", "--porcelain", "--untracked-files=all").stdout.rstrip("\n")
    baseline = {
        "version": 1,
        "branch": branch,
        "head": head,
        "status": status,
        "teammates": list(args.teammate),
        "coordinator": "main-session",
        "writers_paused": bool(args.writers_paused),
        "writers_paused_source": "operator-attested" if args.writers_paused else "none",
    }
    note = f"接管在途工作：绑定当前分支，基线 {head[:12]}，不切换分支、不改动任何队友文件"
    package_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        package_dir.mkdir(exist_ok=force)
        for filename, template in templates.items():
            write_text(outputs[filename], render_template(template, title, branch, note))
        write_text(outputs[ADOPTION_FILE], json.dumps(baseline, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        raise AdoptionError(f"could not write the package ({exc.__class__.__name__}): {exc}") from exc
    print(f"adopted: {package_dir}")
    print(f"branch: {branch} (unchanged)")
    print(f"baseline: dirty status recorded ({len(status.splitlines()) if status else 0} entries)")
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
            "update the package's `Git integration branch` line to match."
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
        package_dir = resolve_specs_child(specs_root, "specs", slug)
        if package_dir.resolve() != package_dir:
            raise ValueError("package write path must not contain symlinks")
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
    try:
        templates = load_reference_templates(skill_root, args.workflow)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        creation = create_integration_branch_from_main(
            target_root,
            slug,
            "main",
            args.branch.strip() if args.branch else None,
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
    if gitx.toplevel(target_root) is not None:
        print(
            "hint: install the Spec git hooks to enforce the gates on commit/push: "
            f"python3 {Path(__file__).resolve().parent / 'install_git_hooks.py'} --root <repo>"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Initialize a Spec task package under .spec/specs/<slug>.

New Development Records should use YYYY-MM-DD_slug. Legacy slugs remain accepted
so existing projects can still be inspected and completed by later gates.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from spec_package_support import (
    PROTECTED_BRANCHES,
    PROTECTED_PREFIXES,
    active_branch_bindings,
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
        "--disable-git-hooks",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


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


def ensure_clean_tree(root: Path) -> None:
    dirty = git_stdout(root, "status", "--porcelain")
    if not dirty:
        return
    raise GitPreflightError(
        "working tree is not clean; /spec:new must start from a clean tree before "
        "checking out main and creating an independent integration branch\n"
        "dirty status:\n"
        f"{dirty}\n"
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
    ensure_clean_tree(repo_root)
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


def main() -> int:
    args = parse_args()

    try:
        slug = validate_slug(args.slug.strip())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
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

    # Load templates BEFORE creating branches so a template failure doesn't
    # leave the user stranded on a feature branch with no package files.
    # The checklist template ships the optional 裁决性检查：<命令> @ <时间>
    # acceptance-evidence line: load_reference_templates parses only the first
    # fence under "## `checklist.md`" in references/templates.md, so that fence
    # IS the init behavior — keep this note and the fence in sync.
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
    print(f"created: {package_dir / 'spec.md'}")
    print(f"created: {package_dir / 'tasks.md'}")
    print(f"created: {package_dir / 'checklist.md'}")
    print(f"ready: {package_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

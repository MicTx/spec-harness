#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Merge and publish a completed Spec branch; delete it only after verification."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import gitx
from check_all_spec_packages import (
    active_package_dirs,
    check_git_revision,
    discover_specs_roots,
    footer_interval_warnings,
    git_revision_snapshot,
    render_failures,
    render_footer_interval_warnings,
)
from dashboard_support import HEALTH_OK, Dashboard
from spec_package_support import (
    PROTECTED_BRANCHES,
    PROTECTED_PREFIXES,
    combined_process_output,
    scope_from_changed_paths,
)


class PushError(RuntimeError):
    pass


SUBPROCESS_NETWORK_TIMEOUT_SECONDS = 300
SAFE_SUBPROCESS_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"
SPEC_BRANCH_PREFIX = "spec/"
SPEC_NONE_FOOTER_PATTERN = re.compile(r"^Spec:\s*none\s*\(([^()]+)\)\s*$")
SPEC_SLUG_FOOTER_PATTERN = re.compile(r"^Spec:\s*([a-z0-9][a-z0-9_-]*[a-z0-9]|[a-z0-9])\s*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", help="Working branch to merge and delete (default: current branch)")
    parser.add_argument("--main-branch", default="main", help="Main branch to merge into (default: main)")
    parser.add_argument("--remote", default="origin", help="Remote name (default: origin)")
    parser.add_argument("--root", default=".", help="Git repository root (default: current directory)")
    parser.add_argument(
        "--specs-dir", action="append", default=[], dest="specs_dirs", help="Additional trusted Spec root"
    )
    parser.add_argument("--slug", action="append", default=[], dest="slugs", help="Also gate this package; repeatable")
    parser.add_argument("--all-packages", action="store_true", help="Gate all active packages and changed archives")
    parser.add_argument(
        "--allow-unarchived", action="store_true", help="Allow selected active packages with an advisory"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show the plan without changing local or remote refs")
    return parser.parse_args()


def clean_git_env() -> dict[str, str]:
    """Use the shared Git context filter plus the push command's bounded PATH."""
    env = gitx.clean_env()
    env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
    return {**env, "PATH": SAFE_SUBPROCESS_PATH}


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return gitx.git(root, *args, check=check, env=clean_git_env(), timeout=SUBPROCESS_NETWORK_TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise PushError("git is not available on the sanitized PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise PushError(f"git {' '.join(args[:3])} timed out within {SUBPROCESS_NETWORK_TIMEOUT_SECONDS}s") from exc


def git_stdout(root: Path, *args: str) -> str:
    return git(root, *args).stdout.strip()


def ensure_safe_ref_name(root: Path, name: str, label: str) -> None:
    if not name or name.startswith(("-", "refs/")):
        raise PushError(f"invalid {label}: {name}")
    if git(root, "check-ref-format", "--branch", name, check=False).returncode != 0:
        raise PushError(f"invalid {label}: {name}")


def ensure_safe_remote_name(name: str) -> None:
    if not name or name.startswith("-") or any(char in name for char in "/\\"):
        raise PushError(f"invalid remote: {name}")


def ensure_configured_remote(root: Path, remote: str) -> None:
    if remote not in git_stdout(root, "remote").splitlines():
        raise PushError(f"remote is not configured: {remote}")


def remote_default_branch(root: Path, remote: str) -> str | None:
    result = git(root, "ls-remote", "--symref", remote, "HEAD", check=False)
    if result.returncode != 0:
        raise PushError(
            f"remote unavailable or cannot determine remote default branch: {remote}\n{combined_process_output(result)}"
        )
    for line in result.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line.split("\t", 1)[0].removeprefix("ref: refs/heads/")
    return None


def ensure_branch_is_deletable(root: Path, remote: str, branch: str, main_branch: str) -> None:
    if branch == main_branch or branch in PROTECTED_BRANCHES or branch.startswith(PROTECTED_PREFIXES):
        raise PushError(f"refusing to delete protected branch: {branch}")
    if branch == remote_default_branch(root, remote):
        raise PushError(f"refusing to delete protected branch: {branch}")


def ensure_spec_branch_prefix(branch: str) -> None:
    if not branch.startswith(SPEC_BRANCH_PREFIX):
        raise PushError(
            f"refusing to push non-spec branch: {branch}\n"
            f"spec workflow branches must start with `{SPEC_BRANCH_PREFIX}`; "
            f"rename this branch with `git branch -m {branch} {SPEC_BRANCH_PREFIX}<name>` and retry"
        )


def repository_root(root: Path) -> Path:
    result = git(root, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise PushError("not a git repository")
    return Path(result.stdout.strip()).resolve()


def current_branch(root: Path) -> str:
    branch = git_stdout(root, "branch", "--show-current")
    if not branch:
        raise PushError("cannot determine current branch")
    return branch


def ensure_clean_tree(root: Path) -> None:
    dirty = git_stdout(root, "status", "--porcelain")
    if dirty:
        raise PushError(
            "working tree is not clean\ntriage required before push:\n"
            "1) if an active Spec package exists: split boundary-related changes into that package commit\n"
            "2) batch unrelated changes by theme into atomic commits, or create a temporary Development Record\n"
            "3) if no active Spec package (discrete unscoped work then push): retro-pack first\n"
            "4) never mix unrelated dirty files into the current package; "
            "never push unscoped dirty without retro-pack\n"
            f"dirty status:\n{dirty}"
        )


def ensure_local_branch_exists(root: Path, branch: str, label: str) -> None:
    if git(root, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode != 0:
        raise PushError(f"required local {label} does not exist: {branch}")


def remote_branch_sha(root: Path, remote: str, branch: str, *, required: bool) -> str | None:
    result = git(root, "ls-remote", "--exit-code", "--heads", remote, f"refs/heads/{branch}", check=False)
    if result.returncode == 2:
        if required:
            raise PushError(f"remote main branch not found: {remote}/{branch}")
        return None
    if result.returncode != 0:
        raise PushError(f"remote unavailable while reading {remote}/{branch}: {combined_process_output(result)}")
    fields = result.stdout.strip().split()
    if not fields:
        raise PushError(f"remote returned no branch SHA for {remote}/{branch}")
    return fields[0]


def merge_base(root: Path, baseline: str, branch: str) -> str:
    result = git(root, "merge-base", baseline, f"refs/heads/{branch}", check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise PushError(f"cannot determine merge-base for {baseline} and {branch}")
    return result.stdout.strip()


def changed_files_against_main(root: Path, branch: str, baseline: str) -> list[str]:
    base = merge_base(root, baseline, branch)
    return [
        path
        for path in git_stdout(root, "diff", "--name-only", "--no-renames", "-z", base, f"refs/heads/{branch}").split(
            "\0"
        )
        if path
    ]


def spec_footer_attribution(root: Path, branch: str, baseline: str) -> tuple[list[str], list[str], list[str]]:
    """Parse footer hints and report unattributed non-merge commits without blocking."""
    base = merge_base(root, baseline, branch)
    shas = git_stdout(root, "rev-list", "--no-merges", f"{base}..refs/heads/{branch}").splitlines()
    slugs: set[str] = set()
    none_reasons: list[str] = []
    unattributed: list[str] = []
    for sha in shas:
        lines = git_stdout(root, "show", "-s", "--format=%B", sha).splitlines()
        slugs_here = {
            match.group(1)
            for line in lines
            if (match := SPEC_SLUG_FOOTER_PATTERN.match(line.strip())) and match.group(1) != "none"
        }
        reasons = [
            match.group(1).strip()
            for line in lines
            if (match := SPEC_NONE_FOOTER_PATTERN.match(line.strip())) and match.group(1).strip()
        ]
        slugs.update(slugs_here)
        none_reasons.extend(reasons)
        if not slugs_here and not reasons:
            unattributed.append(sha[:12])
    return sorted(slugs), none_reasons, unattributed


def run_spec_gate(
    root: Path,
    branch: str,
    main_branch: str,
    explicit_slugs: list[str],
    all_packages: bool,
    *,
    allow_unarchived: bool = False,
    specs_dirs: list[str] | None = None,
    remote_baseline: str | None = None,
) -> list[str]:
    """Check the target revision and changed archives against the pinned remote baseline."""
    baseline = remote_baseline or f"refs/heads/{main_branch}"
    revision = f"refs/heads/{branch}"
    base = merge_base(root, baseline, branch)
    touched = scope_from_changed_paths(changed_files_against_main(root, branch, baseline), specs_dirs or [])
    footers, reasons, unattributed = spec_footer_attribution(root, branch, baseline)
    notes: list[str] = []
    if unattributed:
        print(
            "spec gate advisory: commit(s) without `Spec: <slug>` attribution: " + ", ".join(unattributed),
            file=sys.stderr,
        )
    if touched:
        notes.append("changed-package attribution: " + ", ".join(touched))
    elif footers:
        notes.append("commit-footer attribution: " + ", ".join(footers))
        warnings = footer_interval_warnings(root, footers, revision=revision, window_base=base, specs_dirs=specs_dirs)
        if warnings:
            print(render_footer_interval_warnings(warnings), file=sys.stderr)
    elif reasons:
        notes.append("lite-channel attribution: " + ", ".join(f"none ({reason})" for reason in reasons))
    else:
        notes.append("no Spec attribution found (advisory only)")

    # Footers gate the packages they resolve to; an unknown slug stays advisory (not checkable).
    try:
        with git_revision_snapshot(root, revision) as snapshot:
            roots = discover_specs_roots(snapshot, specs_dirs)
            active = [package.name for specs_root in roots for package in active_package_dirs(specs_root)]
            known = set(active)
            for specs_root in roots:
                archive = specs_root / "specs" / "archive"
                if archive.is_dir():
                    known.update(path.name for path in archive.iterdir() if path.is_dir() and not path.is_symlink())
            unresolved = sorted(set(footers) - known)
            if unresolved:
                print("spec gate advisory: unknown footer slug (skipped): " + ", ".join(unresolved), file=sys.stderr)
            selected = None if all_packages else sorted(set(touched) | set(explicit_slugs) | (set(footers) & known))
        if all_packages or selected:
            failures = check_git_revision(root, revision, specs_dirs, slugs=selected, baseline=base)
            if failures:
                raise PushError(render_failures(root, failures))
        selected_active = sorted(name for name in active if all_packages or name in (selected or []))
    except PushError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise PushError(f"target-revision Spec check unavailable: {exc}") from exc
    if selected_active:
        detail = ", ".join(selected_active)
        if not allow_unarchived:
            raise PushError(f"unarchived active Spec package(s) block push: {detail}")
        print(
            f"# Spec Push Advisory\n\n- active Spec package(s): {detail}\n- action: continue (--allow-unarchived)",
            file=sys.stderr,
        )
        notes.append("active Spec package(s) present; push continues (--allow-unarchived)")
    elif active and not selected and reasons:
        print(
            "spec gate advisory: `Spec: none` push while active task package(s) exist: " + ", ".join(active),
            file=sys.stderr,
        )
    if all_packages:
        notes.append("all-packages gate")
    elif selected:
        notes.append("package gate: " + ", ".join(selected))
    return notes


def ensure_local_can_fast_forward_to_remote_main(
    root: Path, remote: str, main_branch: str, expected_remote_sha: str
) -> None:
    local = git_stdout(root, "rev-parse", f"refs/heads/{main_branch}")
    remote_ref = f"refs/remotes/{remote}/{main_branch}"
    if git_stdout(root, "rev-parse", remote_ref) != expected_remote_sha:
        raise PushError(f"remote main branch changed during execution: {remote}/{main_branch}")
    if git_stdout(root, "merge-base", f"refs/heads/{main_branch}", remote_ref) != local:
        raise PushError(
            f"local main branch does not fast-forward to remote main branch: {main_branch} != {remote}/{main_branch}"
        )


def ensure_local_matches_or_ahead_of_remote_branch(root: Path, branch: str, remote_sha: str) -> bool:
    local = git_stdout(root, "rev-parse", f"refs/heads/{branch}")
    if local == remote_sha:
        return False
    result = git(root, "merge-base", "--is-ancestor", remote_sha, local, check=False)
    if result.returncode == 0:
        return True
    raise PushError(f"local branch does not match remote branch: {branch} != {remote_sha}")


def ensure_remote_branch_merged(root: Path, remote_sha: str, main_branch: str) -> None:
    if git_stdout(root, "merge-base", f"refs/heads/{main_branch}", remote_sha) != remote_sha:
        raise PushError("remote branch is not fully merged into main branch")


def ensure_remote_main_contains_branch(root: Path, remote: str, main_branch: str, branch_sha: str) -> None:
    if git_stdout(root, "merge-base", f"refs/remotes/{remote}/{main_branch}", branch_sha) != branch_sha:
        raise PushError("remote main branch no longer contains merged branch")


def plan_commands(
    remote: str,
    branch: str,
    main_branch: str,
    expected_remote_sha: str | None,
    *,
    push_branch_first: bool = False,
    branch_tip: str | None = None,
) -> list[list[str]]:
    branch_ref = f"refs/heads/{branch}"
    source = branch_tip or branch_ref
    commands = [["git", "fetch", remote]]
    if push_branch_first:
        lease = f"--force-with-lease={branch_ref}:{expected_remote_sha or ''}"
        commands.append(["git", "push", "-u", "--porcelain", lease, remote, f"{source}:{branch_ref}"])
    deletion_sha = (branch_tip if push_branch_first else expected_remote_sha) or "<branch-tip>"
    commands.extend(
        [
            ["git", "checkout", main_branch],
            ["git", "merge", "--ff-only", f"refs/remotes/{remote}/{main_branch}"],
            ["git", "merge", "--no-ff", source],
            ["git", "push", remote, main_branch],
            ["git", "fetch", remote, f"+refs/heads/{main_branch}:refs/remotes/{remote}/{main_branch}"],
            ["git", "push", f"--force-with-lease={branch_ref}:{deletion_sha}", remote, ":" + branch],
            ["git", "branch", "-d", branch],
        ]
    )
    return commands


def render_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def render_plan(
    remote: str,
    branch: str,
    main_branch: str,
    expected_remote_sha: str | None,
    *,
    push_branch_first: bool,
    notes: list[str],
    dry_run: bool,
    branch_tip: str | None = None,
) -> str:
    lines = [
        "# Spec Push Plan",
        "",
        f"- mode: {'dry-run' if dry_run else 'execute'}",
        f"- branch: {branch}",
        f"- main branch: {main_branch}",
        f"- remote: {remote}",
    ]
    if expected_remote_sha:
        lines.append(f"- expected remote branch sha: {expected_remote_sha}")
    if push_branch_first:
        lines.append("- publish working branch first: yes")
    lines.extend(f"- note: {note}" for note in notes)
    lines.extend(
        [
            "",
            "## Commands",
            *(
                "- " + render_command(command)
                for command in plan_commands(
                    remote,
                    branch,
                    main_branch,
                    expected_remote_sha,
                    push_branch_first=push_branch_first,
                    branch_tip=branch_tip,
                )
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Safety",
            "- Core prechecks: clean tree and protected-branch refusal; "
            "unarchived active Spec packages hard-block by default; "
            "only --allow-unarchived downgrades that gate to advisory.",
        ]
    )
    return "\n".join(lines)


def run_checked(root: Path, command: list[str]) -> None:
    if command[:1] != ["git"]:
        raise PushError(f"not a Git command: {render_command(command)}")
    completed = git(root, *command[1:], check=False)
    output = combined_process_output(completed)
    if completed.returncode != 0:
        raise PushError(f"command failed: {render_command(command)}\n{output}")
    # Git skips lease checks when the advertised target already equals the source.
    if "-u" in command and "--porcelain" in command:
        if not any(line.startswith((" \t", "+\t", "*\t")) for line in completed.stdout.splitlines()):
            raise PushError(f"lease push did not update remote branch: {render_command(command)}\n{output}")
    if output:
        print(output, file=sys.stderr)


def run_best_effort(root: Path, command: list[str]) -> None:
    try:
        git(root, *command[1:], check=False)
    except (PushError, OSError, subprocess.SubprocessError):
        return


def execute_plan(
    root: Path,
    remote: str,
    branch: str,
    main_branch: str,
    expected_remote_sha: str | None,
    expected_remote_main_sha: str,
    *,
    push_branch_first: bool,
    publish_sha: str,
) -> None:
    ensure_clean_tree(root)
    original_branch = current_branch(root)
    try:
        run_checked(root, ["git", "fetch", remote])
        ensure_local_can_fast_forward_to_remote_main(root, remote, main_branch, expected_remote_main_sha)
        if git_stdout(root, "rev-parse", f"refs/heads/{branch}") != publish_sha:
            raise PushError(f"local branch moved after review: {branch}")
        if expected_remote_sha:
            remote_ref = f"refs/remotes/{remote}/{branch}"
            if git_stdout(root, "rev-parse", remote_ref) != expected_remote_sha:
                raise PushError(f"remote branch changed during execution: {remote}/{branch}")
            needs_publish = ensure_local_matches_or_ahead_of_remote_branch(root, branch, expected_remote_sha)
        else:
            needs_publish = True
        if needs_publish:
            ref = f"refs/heads/{branch}"
            lease = f"--force-with-lease={ref}:{expected_remote_sha or ''}"
            # Push the reviewed object, never the mutable branch name.
            run_checked(root, ["git", "push", "-u", "--porcelain", lease, remote, f"{publish_sha}:{ref}"])
            if git_stdout(root, "rev-parse", ref) != publish_sha:
                raise PushError(f"local branch moved during publish: {branch}")
            run_checked(root, ["git", "fetch", remote])
        run_checked(root, ["git", "checkout", main_branch])
        run_checked(root, ["git", "merge", "--ff-only", f"refs/remotes/{remote}/{main_branch}"])
        run_checked(root, ["git", "merge", "--no-ff", publish_sha])
        try:
            run_checked(root, ["git", "push", remote, main_branch])
        except PushError as exc:
            raise PushError(
                f"{exc}\nlocal {main_branch} contains the merge; remote state may be unchanged or unknown"
            ) from exc
        ensure_remote_branch_merged(root, publish_sha, main_branch)
        run_checked(root, ["git", "fetch", remote, f"+refs/heads/{main_branch}:refs/remotes/{remote}/{main_branch}"])
        ensure_remote_main_contains_branch(root, remote, main_branch, publish_sha)
        run_checked(
            root, ["git", "push", f"--force-with-lease=refs/heads/{branch}:{publish_sha}", remote, ":" + branch]
        )
        run_checked(root, ["git", "branch", "-d", branch])
    except PushError:
        run_best_effort(root, ["git", "merge", "--abort"])
        if original_branch and not git_stdout(root, "status", "--porcelain"):
            run_best_effort(root, ["git", "checkout", original_branch])
        raise


def _run_push_plan(args: argparse.Namespace, root: Path, branch: str) -> str:
    remote_main = remote_branch_sha(root, args.remote, args.main_branch, required=True)
    assert remote_main is not None
    remote_branch = remote_branch_sha(root, args.remote, branch, required=False)
    if args.dry_run:
        # Download objects without updating tracking refs or FETCH_HEAD.
        for name, sha in ((args.main_branch, remote_main), (branch, remote_branch)):
            if sha and git(root, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode != 0:
                run_checked(
                    root,
                    ["git", "fetch", "--refmap=", "--no-write-fetch-head", args.remote, f"refs/heads/{name}"],
                )
    else:
        refspec = f"+refs/heads/{args.main_branch}:refs/remotes/{args.remote}/{args.main_branch}"
        run_checked(root, ["git", "fetch", args.remote, refspec])
        if git_stdout(root, "rev-parse", f"refs/remotes/{args.remote}/{args.main_branch}") != remote_main:
            raise PushError(f"remote main changed while establishing baseline: {args.remote}/{args.main_branch}")
    branch_tip = git_stdout(root, "rev-parse", f"refs/heads/{branch}")
    notes = run_spec_gate(
        root,
        branch,
        args.main_branch,
        args.slugs,
        args.all_packages,
        allow_unarchived=args.allow_unarchived,
        specs_dirs=args.specs_dirs,
        remote_baseline=remote_main,
    )
    if git_stdout(root, "rev-parse", f"refs/heads/{branch}") != branch_tip:
        raise PushError(f"local branch moved during review: {branch}")
    publish_first = remote_branch is None or ensure_local_matches_or_ahead_of_remote_branch(root, branch, remote_branch)
    if publish_first:
        notes.append(f"working branch will be published first: {branch}")
    plan = render_plan(
        args.remote,
        branch,
        args.main_branch,
        remote_branch,
        push_branch_first=publish_first,
        notes=notes,
        dry_run=args.dry_run,
        branch_tip=branch_tip,
    )
    if not args.dry_run:
        execute_plan(
            root,
            args.remote,
            branch,
            args.main_branch,
            remote_branch,
            remote_main,
            push_branch_first=publish_first,
            publish_sha=branch_tip,
        )
    return plan


def main() -> int:
    args = parse_args()
    # In-process helpers (shared Spec checkers) spawn git through gitx, so pin
    # the PATH for the whole process instead of only for this module's calls.
    os.environ["PATH"] = SAFE_SUBPROCESS_PATH
    try:
        root = repository_root(Path(args.root).resolve())
        branch = args.branch or current_branch(root)
        ensure_safe_ref_name(root, branch, "branch")
        ensure_safe_ref_name(root, args.main_branch, "main branch")
        if args.main_branch != "main" and (
            args.main_branch in PROTECTED_BRANCHES or args.main_branch.startswith(PROTECTED_PREFIXES)
        ):
            raise PushError(f"refusing to write protected branch: {args.main_branch}")
        ensure_safe_remote_name(args.remote)
        ensure_configured_remote(root, args.remote)
        ensure_branch_is_deletable(root, args.remote, branch, args.main_branch)
        ensure_spec_branch_prefix(branch)
        ensure_clean_tree(root)
        ensure_local_branch_exists(root, branch, "branch")
        ensure_local_branch_exists(root, args.main_branch, "main branch")
        plan = _run_push_plan(args, root, branch)
        if args.dry_run:
            print(plan)
            return 0
        print(
            Dashboard(
                slug="spec",
                stage="push",
                title="Project changes published",
                health=HEALTH_OK,
                delta=[
                    f"Merged into {args.main_branch}",
                    f"Synced to {args.remote}/{args.main_branch}",
                    f"Deleted working branch {branch}",
                ],
                detail=plan.splitlines(),
            ).render()
        )
        return 0
    except (PushError, ValueError, subprocess.SubprocessError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

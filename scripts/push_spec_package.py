#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""
Execute the post-done Spec Git push workflow.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dashboard_support import HEALTH_OK, HEALTH_RISK, Dashboard
from spec_package_support import (
    PROTECTED_BRANCHES,
    PROTECTED_PREFIXES,
    combined_process_output,
    remote_unavailable_detected,
    scope_from_changed_paths,
)

try:
    from check_all_spec_packages import (
        CheckFailure,
        check_all_packages,
        git_revision_snapshot,
        legacy_archive_hashes,
        render_failures,
    )
except ImportError:  # pragma: no cover - optional until disk-truth lands
    CheckFailure = None  # type: ignore[assignment]
    check_all_packages = None  # type: ignore[assignment]
    git_revision_snapshot = None  # type: ignore[assignment]
    legacy_archive_hashes = None  # type: ignore[assignment]

    def render_failures(root, failures):  # type: ignore[no-redef]
        return f"spec check unavailable under {root}: {failures}"


SAFE_SUBPROCESS_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"


class PushError(RuntimeError):
    pass


class RemoteUnavailable(PushError):
    pass


GIT_HOOKS_BROKEN_MARKERS = (
    "this repository's git hooks seem to be broken",
    "this repository’s git hooks seem to be broken",
    "repository git hooks seem to be broken",
    "git hooks seem to be broken",
    "server-side git hooks seem to be broken",
    "push hook / webhook / actions aren't running",
    "push hook / webhook / actions aren’t running",
    "仓库的 git 钩子似乎已损坏",
    "git 钩子似乎已损坏",
    "服务端 git 钩子似乎已损坏",
)

GIT_HOOKS_FIX_GUIDANCE = """Remote reported broken repository Git hooks during push.

Pushes may succeed while repository activity, webhooks, mirrors, or CI/Actions
jobs do not run when server-side Git hooks are out of sync, missing, or cannot
execute.

Repair the remote Git hosting service before running Spec push cleanup again:
1) Resynchronize or reinstall the repository server-side hooks
   (pre-receive, update, post-receive)
2) Resynchronize repository branch and tag metadata from Git data into the
   hosting service database, when the platform provides that maintenance
   action
3) Verify the repository storage filesystem supports executable scripts
   (`chmod a+x any-script`) and is not mounted with `noexec`
4) Verify the hosting service/runtime version is compatible with executing
   server-side hooks
5) After repair, push a new commit to refresh repository activity, webhook,
   mirror, and CI/Actions state

Built-in Gitea repair: when the remote is a Gitea instance you administer,
rerun push with `--repair-gitea-hooks --gitea-url <base-url> --gitea-token
<admin-token>` (or env SPEC_GITEA_URL / SPEC_GITEA_TOKEN). This runs the
official FAQ maintenance actions (sync_repo_branches, sync_repo_tags,
resync_all_hooks) via the site admin API and retries the push once.

Spec push stopped before branch cleanup so the working branch remains available
for retry after the remote Git hooks are repaired."""


def is_git_push_command(command: list[str]) -> bool:
    return len(command) >= 2 and command[0] == "git" and command[1] == "push"


def git_hooks_broken_detected(output: str) -> bool:
    normalized = output.lower()
    return any(marker in normalized for marker in GIT_HOOKS_BROKEN_MARKERS)


class GiteaRepairSettings:
    """Opt-in repair configuration resolved from flags with env fallback."""

    def __init__(
        self,
        enabled: bool,
        base_url: str | None,
        token: str | None,
    ) -> None:
        self.enabled = enabled
        self.base_url = (base_url or os.environ.get("SPEC_GITEA_URL") or "").strip() or None
        self.token = (token or os.environ.get("SPEC_GITEA_TOKEN") or "").strip() or None


class BrokenGitHooksError(PushError):
    """Raised when a push reports broken remote Git hooks.

    Carries the push command output so an opt-in repair attempt can decide
    whether the failure is repairable and rerun the plan once.
    """


class HooksRepairedRetryNeeded(PushError):
    """Raised after a successful repair; the plan must be rerun once."""


def ensure_no_broken_git_hooks_warning(
    command: list[str],
    output: str,
    repair: GiteaRepairSettings | None = None,
) -> None:
    """Fail closed on broken remote hooks, with one opt-in repair retry.

    Without repair settings this matches the historical behavior: raise and
    keep the working branch. With `repair.enabled`, run the Gitea admin
    repair sequence first, then raise HooksRepairedRetryNeeded so the
    caller reruns the plan once without repair (at most one repair). A
    failed repair raises BrokenGitHooksError (fail closed).
    """
    if not (is_git_push_command(command) and git_hooks_broken_detected(output)):
        return
    if repair is not None and repair.enabled:
        repair_gitea_hooks_once(repair)
        raise HooksRepairedRetryNeeded(
            f"Gitea hook repair completed; rerunning the push plan once.\nOriginal git output:\n{output.strip()}"
        )
    raise BrokenGitHooksError(f"{GIT_HOOKS_FIX_GUIDANCE}\n\nGit output:\n{output.strip()}")


def repair_gitea_hooks_once(repair: GiteaRepairSettings) -> None:
    """Run the Gitea hook repair sequence; raise on any failure."""
    try:
        from gitea_hook_repair import (
            GiteaCredentialsMissing,
            GiteaHookRepairError,
            repair_gitea_hooks,
        )
    except ImportError as exc:  # pragma: no cover - module ships alongside
        raise PushError(f"Gitea hook repair module unavailable: {exc}") from exc
    if not repair.base_url or not repair.token:
        raise BrokenGitHooksError(
            "repair-gitea-hooks is enabled but credentials are incomplete; "
            "provide --gitea-url/--gitea-token or SPEC_GITEA_URL/SPEC_GITEA_TOKEN\n" + GIT_HOOKS_FIX_GUIDANCE
        )
    print(
        f"Gitea hook repair: running official maintenance tasks on {repair.base_url} (site-wide operation)...",
        file=sys.stderr,
    )
    try:
        executed = repair_gitea_hooks(repair.base_url, repair.token)
    except (GiteaHookRepairError, GiteaCredentialsMissing) as exc:
        raise BrokenGitHooksError(f"Gitea hook repair failed: {exc}\n" + GIT_HOOKS_FIX_GUIDANCE) from exc
    print(
        f"Gitea hook repair completed: {', '.join(executed)}; retrying push plan once",
        file=sys.stderr,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge the current Spec branch into main and delete the merged branch."
    )
    parser.add_argument(
        "--branch",
        help="Working branch to merge and delete (default: current branch)",
    )
    parser.add_argument(
        "--main-branch",
        default="main",
        help="Main branch to merge into (default: main)",
    )
    parser.add_argument(
        "--remote",
        default="origin",
        help="Remote name (default: origin)",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Git repository root (default: current directory)",
    )
    parser.add_argument(
        "--specs-dir",
        action="append",
        default=[],
        dest="specs_dirs",
        help="Additional trusted Spec root relative to root; may be repeated",
    )
    parser.add_argument(
        "--slug",
        action="append",
        default=[],
        dest="slugs",
        help=(
            "Single-package mode: gate only the named Spec package; may be "
            "repeated. Omit for touched-package detection."
        ),
    )
    parser.add_argument(
        "--all-packages",
        action="store_true",
        help=(
            "All-packages mode: gate every active Spec package (legacy "
            "behavior). Default gates only packages touched by this branch."
        ),
    )
    parser.add_argument(
        "--allow-unarchived",
        action="store_true",
        help=(
            "Downgrade the unarchived-package check from hard block to advisory. "
            "Use for retro-pack scenarios where archival happens after the push."
        ),
    )
    parser.add_argument(
        "--repair-gitea-hooks",
        action="store_true",
        help=(
            "When a push reports broken remote Git hooks (Gitea FAQ), run the "
            "official admin maintenance tasks via the Gitea API and retry the "
            "push plan once. Site-wide operation; requires admin token."
        ),
    )
    parser.add_argument(
        "--gitea-url",
        default=None,
        help="Gitea base URL for hook repair (env: SPEC_GITEA_URL)",
    )
    parser.add_argument(
        "--gitea-token",
        default=None,
        help="Gitea site admin token for hook repair (env: SPEC_GITEA_TOKEN)",
    )
    return parser.parse_args()


def _temp_roots() -> list[str]:
    """Lowercased, resolved temp roots considered attacker-controlled.

    A PATH-injected fake git is placed under a temp dir (see
    test_push_ignores_path_injected_git_binary); excluding temp roots keeps
    such a fake off the sanitized PATH while preserving system and git dirs.
    """
    roots: list[str] = []
    for var in ("TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(var)
        if not value:
            continue
        try:
            roots.append(str(Path(value).resolve()).lower())
        except OSError:
            continue
    return roots


def _windows_safe_path() -> str:
    """Filter the inherited PATH to existing, non-temp directories.

    Git for Windows needs its mingw64/bin (DLLs), usr/bin (MSYS runtime),
    cmd and exec-path dirs on PATH for local-transport helpers to load; a
    minimal hardcoded PATH crashes git (STATUS_ACCESS_VIOLATION), so on
    Windows we filter the inherited PATH by location instead of replacing
    it. Temp roots are dropped so a PATH-injected fake git never runs.
    """
    excluded = _temp_roots()

    def under_temp(entry: str) -> bool:
        try:
            resolved = str(Path(entry).resolve()).lower()
        except OSError:
            return False
        for root in excluded:
            prefix = root.rstrip("\\") + "\\"
            if resolved == root or resolved.startswith(prefix):
                return True
        return False

    kept: list[str] = []
    seen: set[str] = set()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or entry.lower() in seen:
            continue
        if not Path(entry).exists() or under_temp(entry):
            continue
        seen.add(entry.lower())
        kept.append(entry)
    return os.pathsep.join(kept)


def safe_subprocess_path() -> str:
    """Return a hardened PATH for git subprocesses.

    On POSIX, a fixed set of trusted system directories keeps a PATH-injected
    fake git from running. On Windows that approach crashes git (git for
    Windows needs several of its own dirs on PATH), so we instead filter the
    inherited PATH down to existing, non-temp directories.
    """
    if sys.platform == "win32":
        return _windows_safe_path()
    return SAFE_SUBPROCESS_PATH


def clean_git_env() -> dict[str, str]:
    blocked_git_vars = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in blocked_git_vars and not key.startswith("GIT_CONFIG_") and key != "SSH_ASKPASS"
    }
    return {**env, "PATH": safe_subprocess_path()}


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            env=clean_git_env(),
            check=check,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PushError("git is not available on the sanitized PATH") from exc


def git_stdout(root: Path, *args: str) -> str:
    return git(root, *args).stdout.strip()


def ensure_safe_ref_name(root: Path, name: str, label: str) -> None:
    if not name or name.startswith("-") or name.startswith("refs/"):
        raise PushError(f"invalid {label}: {name}")
    result = git(root, "check-ref-format", "--branch", name, check=False)
    if result.returncode != 0:
        raise PushError(f"invalid {label}: {name}")


def ensure_safe_remote_name(name: str) -> None:
    if not name or name.startswith("-") or any(char in name for char in "/\\"):
        raise PushError(f"invalid remote: {name}")


def ensure_configured_remote(root: Path, remote: str) -> None:
    remotes = set(git_stdout(root, "remote").splitlines())
    if remote not in remotes:
        raise PushError(f"remote is not configured: {remote}")


def remote_default_branch(root: Path, remote: str) -> str | None:
    result = git(root, "ls-remote", "--symref", remote, "HEAD", check=False)
    if result.returncode != 0:
        output = combined_process_output(result)
        if remote_unavailable_detected(output):
            return None
        raise PushError(f"cannot determine remote default branch: {remote}\n{output}")
    for line in result.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line.split("\t", 1)[0].removeprefix("ref: refs/heads/")
    return None


def ensure_branch_is_deletable(root: Path, remote: str, branch: str, main_branch: str) -> None:
    protected = branch in PROTECTED_BRANCHES or branch.startswith(PROTECTED_PREFIXES)
    default_branch = remote_default_branch(root, remote)
    if branch == main_branch or branch == default_branch or protected:
        raise PushError(f"refusing to delete protected branch: {branch}")


SPEC_BRANCH_PREFIX = "spec/"
# Branches whose unique work started before this moment keep legacy
# non-spec-prefix compatibility; strict enforcement applies afterwards.
SPEC_PREFIX_ENFORCED_FROM = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _branch_work_predates_enforcement(root: Path, main_branch: str, branch: str) -> bool:
    result = git(
        root,
        "log",
        "--format=%aI%n%cI",
        f"refs/heads/{main_branch}..refs/heads/{branch}",
        check=False,
    )
    if result.returncode != 0:
        # Undateable history stays compatible so old branches keep merging.
        return True
    stamps = result.stdout.split()
    for stamp_text in stamps:
        if stamp_text.endswith("Z"):
            stamp_text = stamp_text[:-1] + "+00:00"
        try:
            stamp = datetime.fromisoformat(stamp_text)
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp < SPEC_PREFIX_ENFORCED_FROM:
            return True
    # No unique commits cannot be dated; treat them as legacy (the merge
    # itself is a no-op, so tolerating them only enables branch cleanup).
    return not stamps


def ensure_spec_branch_prefix(root: Path, branch: str, main_branch: str) -> str | None:
    """Enforce the spec/ prefix; return an advisory note for tolerated legacy branches."""
    if branch.startswith(SPEC_BRANCH_PREFIX):
        return None
    if branch in PROTECTED_BRANCHES or branch.startswith(PROTECTED_PREFIXES):
        # Protected branches keep their precise refusal from
        # ensure_branch_is_deletable below.
        return None
    if _branch_work_predates_enforcement(root, main_branch, branch):
        return (
            f"legacy branch {branch} predates `spec/` prefix enforcement "
            f"(strict from {SPEC_PREFIX_ENFORCED_FROM.date().isoformat()} UTC); "
            f"rename recommended: git branch -m {branch} {SPEC_BRANCH_PREFIX}<name>"
        )
    raise PushError(
        f"refusing to push non-spec branch: {branch}\n"
        f"spec workflow branches must start with `{SPEC_BRANCH_PREFIX}`; "
        f"rename this branch with `git branch -m {branch} {SPEC_BRANCH_PREFIX}<name>` and retry"
    )


def repository_root(root: Path) -> Path:
    result = git(root, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        return root
    return Path(result.stdout.strip()).resolve()


def ensure_git_repo(root: Path) -> None:
    result = git(root, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise PushError("not a git repository")


def current_branch(root: Path) -> str:
    branch = git_stdout(root, "branch", "--show-current")
    if not branch:
        raise PushError("cannot determine current branch")
    return branch


def changed_files_against_main(root: Path, branch: str, main_branch: str) -> list[str] | None:
    base = git(root, "merge-base", f"refs/heads/{main_branch}", f"refs/heads/{branch}", check=False)
    if base.returncode != 0:
        return None
    base_sha = base.stdout.strip()
    diff = git(root, "diff", "--name-only", base_sha, f"refs/heads/{branch}", check=False)
    if diff.returncode != 0:
        return None
    return [line.strip() for line in diff.stdout.splitlines() if line.strip()]


def spec_footer_slugs(root: Path, branch: str, main_branch: str) -> list[str]:
    base = git(root, "merge-base", f"refs/heads/{main_branch}", f"refs/heads/{branch}", check=False)
    if base.returncode != 0:
        return []
    commits = git(
        root,
        "rev-list",
        "--no-merges",
        f"{base.stdout.strip()}..refs/heads/{branch}",
        check=False,
    )
    if commits.returncode != 0:
        return []
    commit_shas = [line.strip() for line in commits.stdout.splitlines() if line.strip()]
    if not commit_shas:
        commit_shas = [git_stdout(root, "rev-parse", f"refs/heads/{branch}")]
    slugs: set[str] = set()
    unattributed: list[str] = []
    for sha in commit_shas:
        message = git(root, "show", "-s", "--format=%B", sha, check=False)
        if message.returncode != 0:
            unattributed.append(sha[:12])
            continue
        commit_slugs = {
            match.group(1)
            for line in message.stdout.splitlines()
            if (match := re.match(r"^Spec:\s*([a-z0-9][a-z0-9_-]*[a-z0-9]|[a-z0-9])\s*$", line.strip()))
        }
        if not commit_slugs:
            unattributed.append(sha[:12])
        slugs.update(commit_slugs)
    if unattributed:
        raise PushError(
            "working branch contains commit(s) without `Spec: <slug>` attribution: "
            + ", ".join(unattributed)
            + "; retro-pack and amend the commits before push"
        )
    return sorted(slugs)


def touched_spec_scope(
    root: Path,
    branch: str,
    main_branch: str,
    specs_dirs: list[str] | None = None,
) -> list[str] | None:
    """Return Spec slugs touched by one branch diff; None when undeterminable."""
    changed = changed_files_against_main(root, branch, main_branch)
    if changed is None:
        return None
    return scope_from_changed_paths(changed, specs_dirs or [])


def ensure_clean_tree(root: Path) -> None:
    dirty = git_stdout(root, "status", "--porcelain")
    if not dirty:
        return
    changed = git_stdout(root, "diff", "--name-only", "HEAD")
    untracked = git_stdout(root, "ls-files", "--others", "--exclude-standard")
    lines = [
        "working tree is not clean",
        "triage required before push:",
        "1) if an active Spec package exists: split boundary-related changes into that package commit",
        "2) batch unrelated changes by theme into atomic commits, or create a temporary Development Record",
        "3) if no active Spec package (discrete unscoped work then push): retro-pack first —",
        "   either one Development Record per independent theme, or one whole Spec package when intent is coherent",
        "4) never mix unrelated dirty files into the current package; never push unscoped dirty without retro-pack",
        "dirty status:",
        dirty,
    ]
    if changed:
        lines.extend(["changed files:", changed])
    if untracked:
        lines.extend(["untracked files:", untracked])
    raise PushError("\n".join(lines))


def ensure_local_branch_exists(root: Path, branch: str, label: str) -> None:
    result = git(root, "show-ref", "--verify", f"refs/heads/{branch}", check=False)
    if result.returncode != 0:
        raise PushError(f"required local {label} does not exist: {branch}")


def remote_branch_sha(root: Path, remote: str, branch: str, label: str) -> str:
    result = git(root, "ls-remote", "--exit-code", "--heads", remote, branch, check=False)
    output = combined_process_output(result)
    if result.returncode != 0 and remote_unavailable_detected(output):
        raise RemoteUnavailable(f"remote unavailable while reading {remote}/{branch}: {output}")
    if result.returncode != 0:
        raise PushError(f"{label} not found: {remote}/{branch}")
    fields = result.stdout.strip().split()
    if not fields:
        raise PushError(f"{label} not found: {remote}/{branch}")
    return fields[0]


def optional_remote_branch_sha(root: Path, remote: str, branch: str) -> str | None:
    result = git(root, "ls-remote", "--heads", remote, branch, check=False)
    output = combined_process_output(result)
    if result.returncode != 0 and remote_unavailable_detected(output):
        raise RemoteUnavailable(f"remote unavailable while reading {remote}/{branch}: {output}")
    if result.returncode != 0:
        return None
    fields = result.stdout.strip().split()
    if not fields:
        return None
    return fields[0]


def ensure_remote_main_exists(root: Path, remote: str, main_branch: str) -> str:
    return remote_branch_sha(root, remote, main_branch, "remote main branch")


def ensure_local_can_fast_forward_to_remote_main(
    root: Path, remote: str, main_branch: str, expected_remote_sha: str
) -> None:
    local_ref = f"refs/heads/{main_branch}"
    local_sha = git_stdout(root, "rev-parse", local_ref)
    remote_ref = f"refs/remotes/{remote}/{main_branch}"
    remote_sha = git_stdout(root, "rev-parse", remote_ref)
    if remote_sha != expected_remote_sha:
        raise PushError(f"remote main branch changed during execution: {remote}/{main_branch}")
    merge_base = git_stdout(root, "merge-base", local_ref, remote_ref)
    if merge_base != local_sha:
        raise PushError(
            f"local main branch does not fast-forward to remote main branch: {main_branch} != {remote}/{main_branch}"
        )


def ensure_local_matches_or_ahead_of_remote_branch(
    root: Path, remote: str, branch: str, expected_remote_sha: str
) -> tuple[str, bool]:
    """Return (remote_sha, needs_branch_push).

    Local == remote: ok.
    Local ahead of remote (remote is ancestor): ok, but publish first.
    Remote has commits local lacks: hard fail (avoid clobbering).
    """
    local_sha = git_stdout(root, "rev-parse", f"refs/heads/{branch}")
    remote_ref = f"refs/remotes/{remote}/{branch}"
    remote_sha = git_stdout(root, "rev-parse", remote_ref)
    if remote_sha != expected_remote_sha:
        raise PushError(f"remote branch changed during execution: {remote}/{branch}")
    if local_sha == remote_sha:
        return remote_sha, False
    merge_base = git_stdout(root, "merge-base", f"refs/heads/{branch}", remote_ref)
    if merge_base == remote_sha:
        # local is strictly ahead
        return remote_sha, True
    raise PushError(f"local branch does not match remote branch: {branch} != {remote}/{branch}")


def ensure_remote_branch_merged(root: Path, remote_sha: str, main_branch: str) -> None:
    merge_base = git_stdout(root, "merge-base", main_branch, remote_sha)
    if merge_base != remote_sha:
        raise PushError("remote branch is not fully merged into main branch")


def ensure_remote_main_contains_branch(root: Path, remote: str, main_branch: str, branch_sha: str) -> None:
    remote_main_ref = f"refs/remotes/{remote}/{main_branch}"
    merge_base = git_stdout(root, "merge-base", remote_main_ref, branch_sha)
    if merge_base != branch_sha:
        raise PushError("remote main branch no longer contains merged branch")


def ensure_branch_merged(root: Path, branch: str, main_branch: str) -> None:
    main_ref = f"refs/heads/{main_branch}"
    branch_ref = f"refs/heads/{branch}"
    main_sha = git_stdout(root, "rev-parse", main_ref)
    merge_base = git_stdout(root, "merge-base", main_ref, branch_ref)
    branch_sha = git_stdout(root, "rev-parse", branch_ref)
    if merge_base != branch_sha and main_sha != branch_sha:
        raise PushError(f"branch is not fully merged into {main_branch}: {branch}")


def plan_commands(
    remote: str,
    branch: str,
    main_branch: str,
    expected_remote_sha: str | None = None,
    *,
    push_branch_first: bool = False,
) -> list[list[str]]:
    delete_command = ["git", "push", remote, "--delete", branch]
    if expected_remote_sha:
        delete_command = [
            "git",
            "push",
            f"--force-with-lease=refs/heads/{branch}:{expected_remote_sha}",
            remote,
            ":" + branch,
        ]
    commands: list[list[str]] = [["git", "fetch", remote]]
    if push_branch_first:
        # Publish or update the working branch before merging main so local-only
        # or locally-ahead feature branches are not blocked.
        commands.append(["git", "push", "-u", remote, f"refs/heads/{branch}:refs/heads/{branch}"])
    commands.extend(
        [
            ["git", "checkout", main_branch],
            ["git", "merge", "--ff-only", f"refs/remotes/{remote}/{main_branch}"],
            ["git", "merge", "--no-ff", f"refs/heads/{branch}"],
            ["git", "push", remote, main_branch],
            [
                "git",
                "fetch",
                remote,
                f"+refs/heads/{main_branch}:refs/remotes/{remote}/{main_branch}",
            ],
            delete_command,
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
    expected_remote_sha: str | None = None,
    *,
    push_branch_first: bool = False,
    notes: list[str] | None = None,
    mode: str = "execute",
) -> str:
    lines = [
        "# Spec Push Plan",
        "",
        f"- mode: {mode}",
        f"- branch: {branch}",
        f"- main branch: {main_branch}",
        f"- remote: {remote}",
    ]
    if expected_remote_sha:
        lines.append(f"- expected remote branch sha: {expected_remote_sha}")
    if push_branch_first:
        lines.append("- publish working branch first: yes")
    if notes:
        lines.extend(f"- note: {note}" for note in notes)
    lines.extend(["", "## Commands"])
    if mode == "local-only":
        lines.extend(
            [
                f"- git checkout {shlex.quote(main_branch)}",
                f"- git merge --no-ff refs/heads/{shlex.quote(branch)}",
            ]
        )
    else:
        lines.extend(
            "- " + render_command(command)
            for command in plan_commands(
                remote,
                branch,
                main_branch,
                expected_remote_sha,
                push_branch_first=push_branch_first,
            )
        )
    lines.extend(
        [
            "",
            "## Safety",
            (
                "- Core prechecks: clean tree, protected-branch refusal, and "
                "merge/push only for a non-main working branch. "
                "Unarchived active Spec packages hard-block by default; "
                "only --allow-unarchived downgrades that gate to advisory. "
                "Local-only or locally-ahead feature branches are published first."
            ),
        ]
    )
    return "\n".join(lines)


def run_checked(
    root: Path,
    command: list[str],
    repair: GiteaRepairSettings | None = None,
) -> None:
    completed = subprocess.run(
        command,
        cwd=root,
        env=clean_git_env(),
        capture_output=True,
        text=True,
    )
    combined_output = combined_process_output(completed)
    ensure_no_broken_git_hooks_warning(command, combined_output, repair)
    if completed.returncode != 0:
        if command[:2] in (["git", "fetch"], ["git", "push"]) and remote_unavailable_detected(combined_output):
            raise RemoteUnavailable(f"remote unavailable while running {render_command(command)}\n{combined_output}")
        detail = combined_output
        raise PushError(f"command failed: {render_command(command)}\n{detail}")


def run_best_effort(root: Path, command: list[str]) -> None:
    subprocess.run(
        command,
        cwd=root,
        env=clean_git_env(),
        capture_output=True,
        text=True,
    )


def run_spec_gate(
    root: Path,
    branch: str,
    main_branch: str,
    explicit_slugs: list[str],
    all_packages: bool,
    *,
    allow_unarchived: bool = False,
    specs_dirs: list[str] | None = None,
    legacy_baseline: str | None = None,
) -> list[str]:
    """Run the Spec disk gate in the selected scope and return plan notes.

    Modes:
      explicit_slugs  - single-package mode: gate only the named slugs.
      all_packages    - legacy all-packages mode.
      (default)       - touched mode: gate only packages changed on this branch.

    By default, unarchived active packages hard-block the push (the push
    script enforces the same contract as the Git pre-push hook).
    Pass ``allow_unarchived=True`` to downgrade to advisory (e.g. for
    retro-pack scenarios where archival happens after the push).

    Raises PushError when a gated package is broken or unarchived.
    """
    notes: list[str] = []
    if check_all_packages is None or git_revision_snapshot is None or legacy_archive_hashes is None:
        notes.append("check_all_spec_packages not installed; disk advisory skipped")
        return notes

    attribution_slugs = touched_spec_scope(root, branch, main_branch, specs_dirs)
    if attribution_slugs is None:
        raise PushError("cannot determine target-branch Spec attribution from Git diff")
    if not attribution_slugs:
        attribution_slugs = spec_footer_slugs(root, branch, main_branch)
        if not attribution_slugs:
            raise PushError(
                "working branch has no Spec attribution; retro-pack the change, archive its Development Record, "
                "and add `Spec: <slug>` to each implementation commit"
            )
        notes.append(f"commit-footer attribution: {', '.join(attribution_slugs)}")
    else:
        notes.append(f"changed-package attribution: {', '.join(attribution_slugs)}")

    if all_packages:
        slugs: list[str] | None = None
        notes.append("all-packages gate: every active/archive package")
    elif explicit_slugs:
        slugs = sorted(set(attribution_slugs).union(explicit_slugs))
        notes.append(f"explicit + attribution gate: {', '.join(slugs)}")
    else:
        slugs = attribution_slugs
        notes.append(f"touched-package gate: {', '.join(slugs)}")
    revision = f"refs/heads/{branch}"
    baseline_hashes = legacy_archive_hashes(root, legacy_baseline, specs_dirs)
    archive_gate_reason = "active package must be archived"
    try:
        with git_revision_snapshot(root, revision) as snapshot:
            # One pass with the archive gate on; classify failures instead of
            # re-running the whole validation a second time on the same snapshot.
            failures = check_all_packages(
                snapshot,
                explicit=specs_dirs,
                require_archived=True,
                slugs=slugs,
                legacy_hashes=baseline_hashes,
            )
            broken = [f for f in failures if f.reason != archive_gate_reason]
            active_unarchived = [f for f in failures if f.reason == archive_gate_reason]
            if broken:
                raise PushError(render_failures(root, broken))

            if active_unarchived:
                if allow_unarchived:
                    notes.append("active Spec package(s) present; push continues (--allow-unarchived)")
                    print(
                        "# Spec Push Advisory\n\n"
                        + render_failures(root, active_unarchived)
                        + "\n\n- action: continue (--allow-unarchived set)\n",
                        file=sys.stderr,
                    )
                else:
                    raise PushError(
                        "unarchived active Spec package(s) block push; "
                        "archive the completed delivery record first, or use --allow-unarchived for retro-pack\n\n"
                        + render_failures(root, active_unarchived)
                    )
    except PushError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise PushError(f"target-revision Spec check unavailable: {exc}") from exc
    return notes


def return_to_branch_if_clean(root: Path, branch: str) -> None:
    if branch and not git_stdout(root, "status", "--porcelain"):
        run_best_effort(root, ["git", "checkout", branch])


def ensure_execute_not_from_main(root: Path, main_branch: str, target_branch: str) -> None:
    """Refuse only when the *target* branch is main.

    Being checked out on main is fine if ``--branch`` names a deletable
    working branch; the plan will switch as needed.
    """
    if target_branch == main_branch:
        raise PushError("refusing to execute against main branch as the working branch")
    _ = root  # root reserved for future checkout validation


def execute_local_only_plan(
    root: Path,
    branch: str,
    main_branch: str,
    repair: GiteaRepairSettings | None = None,
) -> None:
    ensure_clean_tree(root)
    original_branch = current_branch(root)
    try:
        run_checked(root, ["git", "checkout", main_branch], repair)
        run_checked(root, ["git", "merge", "--no-ff", f"refs/heads/{branch}"], repair)
        ensure_branch_merged(root, branch, main_branch)
    except HooksRepairedRetryNeeded:
        # Repair retry is handled at the plan boundary; skip merge cleanup so
        # the rerun starts from the exact pre-merge state.
        raise
    except PushError:
        run_best_effort(root, ["git", "merge", "--abort"])
        return_to_branch_if_clean(root, original_branch)
        raise


def local_main_contains_branch_and_remote_main(root: Path, remote: str, branch: str, main_branch: str) -> bool:
    main_ref = f"refs/heads/{main_branch}"
    branch_ref = f"refs/heads/{branch}"
    remote_main_ref = f"refs/remotes/{remote}/{main_branch}"
    branch_sha = git_stdout(root, "rev-parse", branch_ref)
    remote_main_sha = git_stdout(root, "rev-parse", remote_main_ref)
    main_contains_branch = git_stdout(root, "merge-base", main_ref, branch_ref) == branch_sha
    main_contains_remote = git_stdout(root, "merge-base", main_ref, remote_main_ref) == remote_main_sha
    return main_contains_branch and main_contains_remote


def local_main_ahead_only_by_branch(
    root: Path, remote: str, branch: str, main_branch: str, expected_remote_main_sha: str
) -> bool:
    remote_ref = f"refs/remotes/{remote}/{main_branch}"
    local_ref = f"refs/heads/{main_branch}"
    if git_stdout(root, "rev-parse", remote_ref) != expected_remote_main_sha:
        raise PushError(f"remote main branch changed during execution: {remote}/{main_branch}")
    if git_stdout(root, "merge-base", local_ref, remote_ref) != expected_remote_main_sha:
        return False
    return local_main_contains_branch_and_remote_main(root, remote, branch, main_branch)


def execute_recovery_plan(
    root: Path,
    remote: str,
    branch: str,
    main_branch: str,
    expected_remote_sha: str | None,
    expected_remote_main_sha: str,
    repair: GiteaRepairSettings | None = None,
) -> None:
    ensure_clean_tree(root)
    original_branch = current_branch(root)
    try:
        run_checked(root, ["git", "fetch", remote], repair)
        if not local_main_ahead_only_by_branch(root, remote, branch, main_branch, expected_remote_main_sha):
            raise PushError(
                "local main branch does not fast-forward to remote main branch:"
                f" {main_branch} != {remote}/{main_branch}"
            )
        if expected_remote_sha:
            ensure_local_matches_or_ahead_of_remote_branch(root, remote, branch, expected_remote_sha)
        run_checked(root, ["git", "push", remote, main_branch], repair)
        tip_sha = git_stdout(root, "rev-parse", f"refs/heads/{branch}")
        run_checked(
            root,
            [
                "git",
                "fetch",
                remote,
                f"+refs/heads/{main_branch}:refs/remotes/{remote}/{main_branch}",
            ],
            repair,
        )
        ensure_remote_main_contains_branch(root, remote, main_branch, tip_sha)
        if expected_remote_sha:
            run_checked(
                root,
                [
                    "git",
                    "push",
                    f"--force-with-lease=refs/heads/{branch}:{tip_sha}",
                    remote,
                    ":" + branch,
                ],
                repair,
            )
        run_checked(root, ["git", "branch", "-d", branch])
    except HooksRepairedRetryNeeded:
        # Repair retry is handled at the plan boundary; skip merge cleanup so
        # the rerun starts from the exact pre-merge state.
        raise
    except PushError:
        run_best_effort(root, ["git", "merge", "--abort"])
        return_to_branch_if_clean(root, original_branch)
        raise


def execute_plan(
    root: Path,
    remote: str,
    branch: str,
    main_branch: str,
    expected_remote_sha: str | None,
    expected_remote_main_sha: str,
    *,
    push_branch_first: bool,
    repair: GiteaRepairSettings | None = None,
) -> None:
    ensure_clean_tree(root)
    original_branch = current_branch(root)
    try:
        run_checked(root, ["git", "fetch", remote], repair)
        ensure_local_can_fast_forward_to_remote_main(root, remote, main_branch, expected_remote_main_sha)

        needs_publish = push_branch_first
        lease_sha = expected_remote_sha
        if expected_remote_sha:
            lease_sha, ahead = ensure_local_matches_or_ahead_of_remote_branch(root, remote, branch, expected_remote_sha)
            needs_publish = needs_publish or ahead
        else:
            needs_publish = True

        if needs_publish:
            run_checked(
                root,
                [
                    "git",
                    "push",
                    "-u",
                    remote,
                    f"refs/heads/{branch}:refs/heads/{branch}",
                ],
                repair,
            )
            # After publish, local tip is the lease for remote delete.
            lease_sha = git_stdout(root, "rev-parse", f"refs/heads/{branch}")
            run_checked(root, ["git", "fetch", remote], repair)

        # Merge path: checkout main, ff remote main, merge feature, push main.
        run_checked(root, ["git", "checkout", main_branch], repair)
        run_checked(root, ["git", "merge", "--ff-only", f"refs/remotes/{remote}/{main_branch}"], repair)
        run_checked(root, ["git", "merge", "--no-ff", f"refs/heads/{branch}"], repair)
        run_checked(root, ["git", "push", remote, main_branch], repair)
        ensure_branch_merged(root, branch, main_branch)
        ensure_remote_branch_merged(
            root,
            lease_sha or git_stdout(root, "rev-parse", f"refs/heads/{branch}"),
            main_branch,
        )
        tip_sha = lease_sha or git_stdout(root, "rev-parse", f"refs/heads/{branch}")
        run_checked(
            root,
            [
                "git",
                "fetch",
                remote,
                f"+refs/heads/{main_branch}:refs/remotes/{remote}/{main_branch}",
            ],
            repair,
        )
        ensure_remote_main_contains_branch(root, remote, main_branch, tip_sha)
        if tip_sha:
            run_checked(
                root,
                [
                    "git",
                    "push",
                    f"--force-with-lease=refs/heads/{branch}:{tip_sha}",
                    remote,
                    ":" + branch,
                ],
                repair,
            )
        else:
            run_checked(root, ["git", "push", remote, "--delete", branch], repair)
        run_checked(root, ["git", "branch", "-d", branch])
    except HooksRepairedRetryNeeded:
        # Repair retry is handled at the plan boundary; skip merge cleanup so
        # the rerun starts from the exact pre-merge state.
        raise
    except PushError:
        run_best_effort(root, ["git", "merge", "--abort"])
        return_to_branch_if_clean(root, original_branch)
        raise


def _run_push_plan(
    args: argparse.Namespace,
    root: Path,
    branch: str,
    repair: GiteaRepairSettings | None,
    legacy_note: str | None = None,
) -> tuple[list[str], str, str]:
    """Run gate + one push plan; return (args-notes, plan_text, mode).

    mode is "recovery", "execute", or "local-only". Raises the plan's
    PushError subclasses untouched for the caller to disposition.
    """
    try:
        expected_remote_main_sha = ensure_remote_main_exists(root, args.remote, args.main_branch)
        run_checked(
            root,
            [
                "git",
                "fetch",
                args.remote,
                f"+refs/heads/{args.main_branch}:refs/remotes/{args.remote}/{args.main_branch}",
            ],
            repair,
        )
        fetched_remote_main = git_stdout(root, "rev-parse", f"refs/remotes/{args.remote}/{args.main_branch}")
        if fetched_remote_main != expected_remote_main_sha:
            raise PushError(f"remote main changed while establishing legacy baseline: {args.remote}/{args.main_branch}")
        expected_remote_sha = optional_remote_branch_sha(root, args.remote, branch)
    except RemoteUnavailable as exc:
        local_main_sha = git_stdout(root, "rev-parse", f"refs/heads/{args.main_branch}")
        notes = run_spec_gate(
            root,
            branch,
            args.main_branch,
            args.slugs,
            args.all_packages,
            allow_unarchived=args.allow_unarchived,
            specs_dirs=args.specs_dirs,
            legacy_baseline=local_main_sha,
        )
        if legacy_note:
            notes.append(legacy_note)
        notes.append(f"remote unavailable: {exc}")
        notes.append("local main selected explicitly as the local-only legacy baseline")
        notes.append("publish to the remote when it is reachable")
        plan_text = render_plan(
            args.remote,
            branch,
            args.main_branch,
            None,
            push_branch_first=False,
            notes=notes,
            mode="local-only",
        )
        execute_local_only_plan(root, branch, args.main_branch, repair)
        return notes, plan_text, "local-only"

    notes = run_spec_gate(
        root,
        branch,
        args.main_branch,
        args.slugs,
        args.all_packages,
        allow_unarchived=args.allow_unarchived,
        specs_dirs=args.specs_dirs,
        legacy_baseline=expected_remote_main_sha,
    )
    if legacy_note:
        notes.append(legacy_note)

    push_branch_first = expected_remote_sha is None
    if push_branch_first:
        notes.append(f"remote branch missing; will publish {branch} first")

    recovery_mode = local_main_contains_branch_and_remote_main(root, args.remote, branch, args.main_branch)
    if recovery_mode:
        notes.append("local-only recovery: local main already contains branch")

    plan_text = render_plan(
        args.remote,
        branch,
        args.main_branch,
        expected_remote_sha,
        push_branch_first=push_branch_first,
        notes=notes,
    )
    if recovery_mode:
        execute_recovery_plan(
            root,
            args.remote,
            branch,
            args.main_branch,
            expected_remote_sha,
            expected_remote_main_sha,
            repair,
        )
        return notes, plan_text, "recovery"
    execute_plan(
        root,
        args.remote,
        branch,
        args.main_branch,
        expected_remote_sha,
        expected_remote_main_sha,
        push_branch_first=push_branch_first,
        repair=repair,
    )
    return notes, plan_text, "execute"


def main() -> int:
    args = parse_args()
    requested_root = Path(args.root).resolve()
    repair = GiteaRepairSettings(
        enabled=args.repair_gitea_hooks,
        base_url=args.gitea_url,
        token=args.gitea_token,
    )
    try:
        root = repository_root(requested_root)
        ensure_git_repo(root)
        branch = args.branch or current_branch(root)
        ensure_safe_ref_name(root, branch, "branch")
        ensure_safe_ref_name(root, args.main_branch, "main branch")
        ensure_safe_remote_name(args.remote)
        ensure_configured_remote(root, args.remote)
        ensure_branch_is_deletable(root, args.remote, branch, args.main_branch)
        ensure_clean_tree(root)
        ensure_execute_not_from_main(root, args.main_branch, branch)
        ensure_local_branch_exists(root, branch, "branch")
        ensure_local_branch_exists(root, args.main_branch, "main branch")
        legacy_branch_note = ensure_spec_branch_prefix(root, branch, args.main_branch)

        try:
            notes, plan_text, mode = _run_push_plan(args, root, branch, repair, legacy_note=legacy_branch_note)
        except HooksRepairedRetryNeeded:
            # One repair was performed by ensure_no_broken_git_hooks_warning;
            # rerun the plan with repair disabled so at most one repair and
            # one retry can ever happen (a second broken-hooks failure on
            # rerun fails closed as BrokenGitHooksError).
            print(
                "retrying Spec push plan after Gitea hook repair...",
                file=sys.stderr,
            )
            notes, plan_text, mode = _run_push_plan(args, root, branch, None, legacy_note=legacy_branch_note)
        if mode == "local-only":
            if legacy_branch_note:
                notes.append(legacy_branch_note)
            dash = Dashboard(
                slug="spec",
                stage="push",
                title="项目变更已合并到本地",
                health=HEALTH_RISK,
                alerts=[
                    "远端暂时不可达",
                    "变更尚未同步到远端，工作分支已保留",
                ],
                delta=[f"已合并到本地 {args.main_branch}"],
                next_step="网络恢复后同步远端并清理工作分支",
                detail=plan_text.splitlines(),
            )
            print(dash.render())
            return 0

        dash = Dashboard(
            slug="spec",
            stage="push",
            title="项目变更已发布",
            health=HEALTH_OK,
            delta=[
                f"已合并到 {args.main_branch}",
                f"已同步到 {args.remote}/{args.main_branch}",
                f"已删除工作分支 {branch}",
            ],
            detail=plan_text.splitlines(),
        )
        print(dash.render())
        return 0
    except BrokenGitHooksError as exc:
        # When repair is enabled, the repair sequence already ran before this
        # error propagated (see ensure_no_broken_git_hooks_warning); a second
        # broken-hooks failure means repair did not restore hooks: one
        # repair attempt only, then fail closed with the branch preserved.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (subprocess.CalledProcessError, PushError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

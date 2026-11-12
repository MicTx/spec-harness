"""Archive-bound merge and push; remote publication remains a separate gate."""

from __future__ import annotations

from pathlib import Path

import gitx
from package import extract_branch, path_matches, specs_root


def _branch_entries(worktrees: list[dict], branch: str) -> list[dict]:
    wanted = f"refs/heads/{branch}"
    return [entry for entry in worktrees if entry["branch"] == wanted]


def _worktrees(root: Path) -> list[dict]:
    return gitx.worktrees(root)


def _branch_worktrees(root: Path, branch: str) -> list[Path]:
    return [entry["path"] for entry in _branch_entries(_worktrees(root), branch)]


def _delivery_topology(
    root: Path,
    worktrees: list[dict],
    branch: str,
    target: str,
) -> tuple[Path, Path | None, Path]:
    delivery_entries = _branch_entries(worktrees, branch)
    if not delivery_entries:
        raise ValueError(f"no worktree is bound to delivery branch {branch}")
    if len(delivery_entries) > 1:
        paths = ", ".join(str(entry["path"]) for entry in delivery_entries)
        raise ValueError(f"multiple worktrees are bound to delivery branch {branch}: {paths}")

    delivery = delivery_entries[0]["path"]
    current = gitx.toplevel(root)
    if current is None or current != delivery.resolve():
        raise ValueError(f"delivery must run from the bound worktree {delivery}; current worktree is {current}")

    target_entries = _branch_entries(worktrees, target)
    if len(target_entries) > 1:
        paths = ", ".join(str(entry["path"]) for entry in target_entries)
        raise ValueError(f"multiple worktrees are bound to target branch {target}: {paths}")
    target_worktree = target_entries[0]["path"] if target_entries else None
    if target_worktree is not None:
        dirty = gitx.dirty_paths(target_worktree, exclude=())
        if dirty:
            lines = "\n".join(f"  - {path}" for path in dirty)
            raise ValueError(f"target worktree is not clean: {target_worktree}\n{lines}")
    anchor = target_worktree or delivery
    return delivery, target_worktree, anchor


def _remove_linked_worktrees(root: Path, branch: str) -> list[dict]:
    current = gitx.toplevel(root)
    removed = []
    for path in _branch_worktrees(root, branch):
        resolved = path.resolve()
        if current is not None and resolved == current:
            continue
        if gitx.dirty_paths(path, exclude=()):
            removed.append({"path": str(path), "removed": False, "warning": "worktree has uncommitted changes"})
            continue
        result = gitx.git(root, "worktree", "remove", str(path), check=False)
        removed.append(
            {
                "path": str(path),
                "removed": result.returncode == 0,
                **({"warning": result.stderr.strip() or "worktree remove failed"} if result.returncode != 0 else {}),
            }
        )
    return removed


def main_branch(root: Path, remote: str) -> str:
    result = gitx.git(root, "ls-remote", "--symref", remote, "HEAD", check=False)
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
                return line.removeprefix("ref: refs/heads/").removesuffix("\tHEAD")
    branches = gitx.git(root, "branch", "--format=%(refname:short)").stdout.splitlines()
    for candidate in ("main", "master"):
        if candidate in branches:
            return candidate
    raise ValueError("cannot resolve the delivery main branch")


def _delivery_preconditions(
    root: Path,
    slug: str,
    branch: str,
    bound: str,
) -> tuple[str, str, list[dict], tuple[Path, Path | None, Path]]:
    """Shared deterministic delivery checks used by ``preflight`` and ``deliver``.

    Returns ``(remote, target, worktrees, topology)`` so callers do not re-resolve them.
    """
    if branch in {"main", "master", "develop"}:
        raise ValueError(f"delivery for {slug} requires the package branch, current branch is {branch}")
    if bound != branch:
        raise ValueError(f"package is bound to {bound}, current branch is {branch}")
    remotes = gitx.git(root, "remote").stdout.splitlines()
    if not remotes:
        raise ValueError("no Git remote is configured")
    remote = "origin" if "origin" in remotes else remotes[0]
    target = main_branch(root, remote)
    worktrees = _worktrees(root)
    if not worktrees:
        raise ValueError("cannot enumerate Git worktrees")
    topology = _delivery_topology(root, worktrees, branch, target)
    return remote, target, worktrees, topology


def preflight(root: Path, slug: str, spec_text: str, globs: list[str]) -> None:
    """Fail fast on deterministic delivery blockers before the package is archived.

    Mirrors the checks ``deliver`` runs after archive, plus a residual-dirty
    prediction so ``done`` never strands an archived package mid-delivery.
    Every dirty path inside a task boundary — including pre-Go baseline dirt —
    is committed by delivery, so only outside-boundary residue can block.
    """
    branch = gitx.branch(root)
    bound = extract_branch(spec_text)
    _delivery_preconditions(root, slug, branch, bound)
    dirty = gitx.dirty_paths(root, exclude=())
    residual = []
    for path in dirty:
        if path.startswith(".spec/"):
            continue
        if any(path_matches(path, pattern) for pattern in globs):
            continue
        residual.append(path)
    if not residual:
        return
    outside = residual
    lines = [f"delivery for {slug} would leave uncommitted changes that block merge/push:"]
    if outside:
        lines.append("outside task boundary (commit or stash before done):")
        lines.extend(f"  - {p}" for p in outside)
    raise ValueError("\n".join(lines))


def deliver(root: Path, slug: str, push_enabled: bool) -> dict:
    archive = specs_root(root) / "archive" / slug
    if not archive.is_dir():
        raise ValueError("push requires an archived package bound to this branch")
    branch = gitx.branch(root)
    if gitx.dirty_paths(root, exclude=()):
        raise ValueError("working tree is not clean")
    spec = (archive / "spec.md").read_text(encoding="utf-8")
    bound = extract_branch(spec)
    remote, target, _worktrees, (_delivery_wt, target_wt, anchor) = _delivery_preconditions(root, slug, branch, bound)
    merge_wt = target_wt or _delivery_wt
    checked_out_target = False
    try:
        if target_wt is None:
            gitx.git(merge_wt, "checkout", target)
            checked_out_target = True
        gitx.git(merge_wt, "merge", "--no-ff", "-m", f"Merge Spec delivery {slug}", branch)
        if not push_enabled:
            return {
                "branch": branch,
                "target": target,
                "merged": True,
                "pushed": False,
                "head": gitx.head(merge_wt),
            }
        gitx.git(anchor, "push", remote, target)
        remote_head = gitx.git(anchor, "ls-remote", remote, f"refs/heads/{target}").stdout.split()[0]
        if remote_head != gitx.head(merge_wt):
            raise ValueError(f"remote {target} does not contain the delivered head")
    except Exception:
        # Leave the caller rerunnable: drop any in-progress merge, and if this
        # call checked out the delivery target on the root worktree, return it
        # to the spec branch. A completed merge stays on the target branch so a
        # retry merges "Already up to date" and continues at the failed step.
        gitx.git(merge_wt, "merge", "--abort", check=False)
        if checked_out_target:
            gitx.git(root, "checkout", branch, check=False)
        raise
    removed = _remove_linked_worktrees(anchor, branch)
    gitx.git(anchor, "push", remote, "--delete", branch, check=False)
    if any(item["removed"] is False for item in removed):
        result = {
            "branch": branch,
            "target": target,
            "merged": True,
            "pushed": True,
            "head": gitx.head(merge_wt),
            "worktrees": removed,
            "warnings": [
                f"cleanup: {item['path']}: {item.get('warning', 'worktree remove failed')}"
                for item in removed
                if not item["removed"]
            ],
        }
        return result
    gitx.git(anchor, "branch", "-d", branch)
    result = {
        "branch": branch,
        "target": target,
        "merged": True,
        "pushed": True,
        "head": gitx.head(merge_wt),
    }
    if removed:
        result["worktrees"] = removed
    warnings = [
        f"cleanup: {item['path']}: {item.get('warning', 'worktree remove failed')}"
        for item in removed
        if not item["removed"]
    ]
    if warnings:
        result["warnings"] = warnings
    return result

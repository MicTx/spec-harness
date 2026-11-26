"""Archive-bound merge and push; remote publication remains a separate gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

import gate
import gitx
from package import (
    delivery_receipt,
    effective_finish,
    extract_branch,
    handoff_completed,
    latest_gate,
    package_dir,
    package_kind,
    path_matches,
    specs_root,
    staged_product_tree,
)


class HandoffBlocked(ValueError):
    def __init__(self, subject: str, detail: str):
        super().__init__(detail)
        self.subject = subject


# `.spec` state Spec itself writes into a worktree: untracked copies of it must
# not count as target dirt, but a delivery branch that changes the same path
# keeps the block because the merge could overwrite the user's copy.
SPEC_STATE_PATHS = (".spec/charter.md", ".spec/cache", ".spec/worktrees")


def _range_touches(root: Path, target: str, branch: str, path: str) -> bool:
    result = gitx.git(root, "diff", "--name-only", f"{target}...{branch}", "--", path, check=False)
    return bool(result.stdout.strip())


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
        dirty = [
            path
            for path in gitx.dirty_paths(target_worktree, exclude=())
            if not any(path_matches(path, spec_path) for spec_path in SPEC_STATE_PATHS)
            or _range_touches(target_worktree, target, branch, path)
        ]
        if dirty:
            lines = "\n".join(f"  - {path}" for path in dirty)
            raise HandoffBlocked("target-dirty", f"target worktree is not clean: {target_worktree}\n{lines}")
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


def main_branch(root: Path, remote: str, *, require_remote: bool = False) -> str:
    try:
        result = gitx.git(root, "ls-remote", "--symref", remote, "HEAD", check=False, timeout=20, remote=True)
    except subprocess.TimeoutExpired as exc:
        raise HandoffBlocked("remote-unavailable", "Git remote HEAD lookup timed out") from exc
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
                return line.removeprefix("ref: refs/heads/").removesuffix("\tHEAD")
    if require_remote:
        raise HandoffBlocked("remote-unavailable", "Git remote HEAD is unavailable or has no default branch")
    branches = gitx.git(root, "branch", "--format=%(refname:short)").stdout.splitlines()
    for candidate in ("main", "master"):
        if candidate in branches:
            return candidate
    raise HandoffBlocked("target-unknown", "cannot resolve the delivery main branch")


def _delivery_preconditions(
    root: Path,
    slug: str,
    branch: str,
    bound: str,
    *,
    require_remote: bool = False,
) -> tuple[str, str, list[dict], tuple[Path, Path | None, Path]]:
    """Resolve the unique delivery/target worktrees before mutation."""
    if branch in {"main", "master", "develop"}:
        raise ValueError(f"delivery for {slug} requires the package branch, current branch is {branch}")
    if bound != branch:
        raise ValueError(f"package is bound to {bound}, current branch is {branch}")
    remotes = gitx.git(root, "remote").stdout.splitlines()
    if not remotes:
        raise HandoffBlocked("remote-missing", "no Git remote is configured")
    remote = "origin" if "origin" in remotes else remotes[0]
    target = main_branch(root, remote, require_remote=require_remote)
    worktrees = _worktrees(root)
    if not worktrees:
        raise ValueError("cannot enumerate Git worktrees")
    topology = _delivery_topology(root, worktrees, branch, target)
    return remote, target, worktrees, topology


def preflight(root: Path, slug: str, spec_text: str, globs: list[str]) -> None:
    """Reject topology or residual dirt before archiving."""
    branch = gitx.branch(root)
    bound = extract_branch(spec_text)
    finish, _ = effective_finish(package_dir(root, slug), spec_text)
    _delivery_preconditions(root, slug, branch, bound, require_remote=finish == "push")
    dirty = gitx.dirty_paths(root, exclude=())
    residual = [
        path
        for path in dirty
        if not path_matches(path, f".spec/specs/{slug}") and not any(path_matches(path, pattern) for pattern in globs)
    ]
    if not residual:
        return
    raise HandoffBlocked(
        "residual-dirty",
        f"delivery for {slug} would leave uncommitted changes outside task boundary: " + ", ".join(residual[:10]),
    )


def deliver(root: Path, slug: str, push_enabled: bool) -> dict:
    archive = specs_root(root) / "archive" / slug
    if not archive.is_dir():
        raise ValueError("push requires an archived package bound to this branch")
    branch = gitx.branch(root)
    if gitx.dirty_paths(root, exclude=()):
        raise ValueError("working tree is not clean")
    spec = (archive / "spec.md").read_text(encoding="utf-8")
    if package_kind(spec) == "quick":
        raise ValueError("quick delivery has no commit to merge or push")
    commit, receipt = delivery_receipt(root, slug)
    if receipt or latest_gate(archive, execution=True).get("product_tree"):
        if not receipt or commit != gitx.head(root):
            raise ValueError("the branch tip has no verified delivery receipt for this package")
        tasks = (archive / "tasks.md").read_text(encoding="utf-8")
        tree = staged_product_tree(root, revision=commit)
        if errors := gate.archive_proof_errors(archive, spec, tasks, tree, receipt):
            raise ValueError("; ".join(errors))
    finish, requested = effective_finish(archive, spec)
    if finish == "commit" and requested != finish:
        raise ValueError("this package was authorized for local commit only; no handoff is pending")
    if finish == "merge" and push_enabled and not handoff_completed(archive, "merge"):
        raise ValueError("merge recovery cannot implicitly authorize a push")
    bound = extract_branch(spec)
    remote, target, _worktrees, (_delivery_wt, target_wt, anchor) = _delivery_preconditions(
        root, slug, branch, bound, require_remote=push_enabled
    )
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
        gitx.git(anchor, "push", remote, target, remote=True)
        remote_head = gitx.git(anchor, "ls-remote", remote, f"refs/heads/{target}", remote=True).stdout.split()[0]
        if remote_head != gitx.head(merge_wt):
            raise ValueError(f"remote {target} does not contain the delivered head")
    except Exception:
        # Abort an incomplete merge; leave a completed merge for a push retry.
        gitx.git(merge_wt, "merge", "--abort", check=False)
        if checked_out_target:
            gitx.git(root, "checkout", branch, check=False)
        raise
    removed = _remove_linked_worktrees(anchor, branch)
    gitx.git(anchor, "push", remote, "--delete", branch, check=False, remote=True)
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

"""Archive-bound merge and push; remote publication remains a separate gate."""

from __future__ import annotations

from pathlib import Path

import gitx
from package import extract_branch, specs_root


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


def deliver(root: Path, slug: str, push_enabled: bool) -> dict:
    archive = specs_root(root) / "archive" / slug
    if not archive.is_dir():
        raise ValueError("push requires an archived package bound to this branch")
    branch = gitx.branch(root)
    if branch in {"main", "master", "develop"}:
        raise ValueError("push starts on the archived package branch")
    if gitx.dirty_paths(root, exclude=()):
        raise ValueError("working tree is not clean")
    spec = (archive / "spec.md").read_text(encoding="utf-8")
    if extract_branch(spec) != branch:
        raise ValueError("archived package is not bound to the current branch")
    remotes = gitx.git(root, "remote").stdout.splitlines()
    if not remotes:
        raise ValueError("no Git remote is configured")
    remote = "origin" if "origin" in remotes else remotes[0]
    target = main_branch(root, remote)
    gitx.git(root, "checkout", target)
    gitx.git(root, "merge", "--no-ff", "-m", f"Merge Spec delivery {slug}", branch)
    if not push_enabled:
        return {
            "branch": branch,
            "target": target,
            "merged": True,
            "pushed": False,
            "head": gitx.head(root),
        }
    gitx.git(root, "push", remote, target)
    remote_head = gitx.git(root, "ls-remote", remote, f"refs/heads/{target}").stdout.split()[0]
    if remote_head != gitx.head(root):
        raise ValueError(f"remote {target} does not contain the delivered head")
    gitx.git(root, "push", remote, "--delete", branch, check=False)
    gitx.git(root, "branch", "-d", branch)
    return {
        "branch": branch,
        "target": target,
        "merged": True,
        "pushed": True,
        "head": gitx.head(root),
    }

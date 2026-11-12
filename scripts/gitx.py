"""Small Git interface shared by the task-package commands."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT_CONTEXT = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_PREFIX"})
GIT_ENV_PREFIXES = ("GIT_CONFIG_", "GIT_OBJECT_DIRECTORY")
GIT_ENV_EXACT = frozenset({"GIT_INDEX_FILE", "SSH_ASKPASS", "GIT_SSH_COMMAND"})


def clean_env(*, index_file: Path | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in GIT_CONTEXT and key not in GIT_ENV_EXACT and not key.startswith(GIT_ENV_PREFIXES)
    }
    return {**env, "GIT_OPTIONAL_LOCKS": "0", **({"GIT_INDEX_FILE": str(index_file)} if index_file else {})}


def git(root: Path, *args: str, check: bool = True, **options) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=options.pop("text", True),
        check=check,
        env=options.pop("env", clean_env()),
        **options,
    )


def stdout(root: Path, *args: str) -> str:
    return git(root, *args).stdout.strip()


def branch(root: Path) -> str:
    result = git(root, "branch", "--show-current", check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def toplevel(root: Path) -> Path | None:
    result = git(root, "rev-parse", "--show-toplevel", check=False)
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def head(root: Path) -> str:
    result = git(root, "rev-parse", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def worktrees(root: Path) -> list[dict]:
    """Enumerate `git worktree list` entries as ``{path, branch}`` dicts."""
    result = git(root, "worktree", "list", "--porcelain", check=False)
    if result.returncode != 0:
        return []
    entries = []
    current: dict | None = None
    for line in result.stdout.splitlines() + [""]:
        if line.startswith("worktree "):
            current = {"path": Path(line.removeprefix("worktree ")), "branch": ""}
        elif line.startswith("branch "):
            if current is not None:
                current["branch"] = line.removeprefix("branch ")
        elif not line:
            if current is not None:
                entries.append(current)
            current = None
    return entries


def dirty_paths(root: Path, exclude: tuple[str, ...] = (".spec/",)) -> list[str]:
    fields = git(root, "status", "--porcelain=v1", "--untracked-files=all", "-z").stdout.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        candidates = [field[3:]]
        if "R" in field[:2] or "C" in field[:2]:
            candidates.append(fields[index])
            index += 1
        paths.update(path for path in candidates if not any(path.startswith(prefix) for prefix in exclude))
    return sorted(paths)

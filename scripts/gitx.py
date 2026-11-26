"""Small Git interface shared by the task-package commands."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

GIT_CONTEXT = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_PREFIX"})
GIT_ENV_PREFIXES = ("GIT_CONFIG_", "GIT_OBJECT_DIRECTORY")
GIT_ENV_EXACT = frozenset({"GIT_INDEX_FILE", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "SSH_ASKPASS", "GIT_SSH_COMMAND"})
GIT_TRANSPORT = frozenset({"GIT_SSH_COMMAND", "GIT_SSH", "SSH_ASKPASS", "GIT_ASKPASS", "GIT_SSH_VARIANT"})


def clean_env(*, index_file: Path | None = None, remote: bool = False) -> dict[str, str]:
    """Build a git environment without inherited repository context.

    Local calls strip the transport variables too so a developer's SSH
    overrides cannot silently redirect plumbing commands; remote calls
    (push/fetch/ls-remote) keep them because the user's remote access may
    depend on a custom key, port, proxy or askpass helper. Repository-context
    and ``GIT_CONFIG_*`` injection stay stripped in both modes.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in GIT_CONTEXT and key not in GIT_ENV_EXACT and not key.startswith(GIT_ENV_PREFIXES)
    }
    if remote:
        env.update({key: os.environ[key] for key in GIT_TRANSPORT if key in os.environ})
    else:
        env = {key: value for key, value in env.items() if key not in GIT_TRANSPORT}
    return {**env, "GIT_OPTIONAL_LOCKS": "0", **({"GIT_INDEX_FILE": str(index_file)} if index_file else {})}


def git(root: Path, *args: str, check: bool = True, remote: bool = False, **options) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=options.pop("text", True),
        check=check,
        env=options.pop("env", clean_env(remote=remote)),
        **options,
    )


LEGACY_GIT_HOOK_MARKER = b"SPEC_CHECK_ALL_SCRIPT"  # in every Spec pre-commit/pre-push hook up to 2.2.0


def legacy_git_hooks(root: Path, *, retire: bool = False) -> list[str]:
    """Spec Git hooks from 2.2.0 and earlier run a checker this runtime no longer ships; retire renames them."""
    hooks = git(root, "rev-parse", "--git-path", "hooks", check=False).stdout.strip()
    found, stamp = [], time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + f".{time.time_ns() % 10**9:09d}Z"
    for path in [root / hooks / name for name in ("pre-commit", "pre-push")] if hooks else []:
        backup = path.with_name(f"{path.name}.spec-retired-{stamp}")
        try:  # a hook that cannot be moved stays in place for doctor and the session note to report
            if path.is_file() and LEGACY_GIT_HOOK_MARKER in path.read_bytes() and not (retire and backup.exists()):
                found.append(path.rename(backup) if retire else path)
        except OSError as exc:  # unreadable: never moved (ownership unproven); reported if Git would still run it
            if isinstance(exc, PermissionError) and not retire and os.access(path, os.X_OK) and not path.is_dir():
                found.append(path)
    return [str(path) for path in found]


def stdout(root: Path, *args: str) -> str:
    return git(root, *args).stdout.strip()


@contextmanager
def temporary_index(root: Path, *, index_file: Path | None = None, revision: str | None = None):
    """Copy the index stat cache; never publish snapshot objects to the repository."""
    objects = (root / stdout(root, "rev-parse", "--git-path", "objects")).resolve()
    with tempfile.TemporaryDirectory(prefix="spec-tree-") as folder:
        index = Path(folder) / "index"
        private = index.parent / "objects"
        private.mkdir()
        env = clean_env(index_file=index)
        env.update(
            GIT_OBJECT_DIRECTORY=str(private),
            GIT_ALTERNATE_OBJECT_DIRECTORIES=json.dumps(str(objects), ensure_ascii=False),
        )
        if revision is not None:
            git(root, "-c", "core.splitIndex=false", "read-tree", revision, env=env)
        else:
            source = root / (index_file or stdout(root, "rev-parse", "--git-path", "index"))
            if source.exists():
                shutil.copy2(source, index)
            else:
                git(root, "-c", "core.splitIndex=false", "read-tree", "--empty", env=env)
        yield env


def materialize_tree(root: Path, env: dict, tree: str, commit: str) -> Path:
    """A private checkout for verifies that must not see unowned working files."""
    checkout = Path(env["GIT_INDEX_FILE"]).parent / "candidate"

    def populate(source: Path, target: Path, source_tree: str, head_oid: str, extra: tuple[str, ...] = ()) -> None:
        target.mkdir(exist_ok=True)
        object_format = stdout(source, "rev-parse", "--show-object-format")
        checkout_branch = branch(source) or "spec-candidate"
        git(target, "init", "-q", f"--object-format={object_format}", "-b", checkout_branch)
        real = (source / stdout(source, "rev-parse", "--git-path", "objects")).resolve()
        alternates = target / ".git" / "objects" / "info" / "alternates"
        alternates.write_text("\n".join((*extra, str(real))) + "\n", encoding="utf-8")
        git(target, "update-ref", "HEAD", head_oid)
        git(target, "read-tree", source_tree)
        git(target, "checkout-index", "--all")
        entries = git(target, "ls-files", "--stage", "-z", text=False).stdout.split(b"\0")
        for entry in entries:
            if not entry.startswith(b"160000 "):
                continue
            metadata, raw = entry.split(b"\t", 1)
            submodule = source / os.fsdecode(raw)
            if (submodule / ".git").exists():
                sub_commit = metadata.split()[1].decode("ascii")
                if git(submodule, "cat-file", "-e", f"{sub_commit}^{{commit}}", check=False).returncode == 0:
                    populate(submodule, target / os.fsdecode(raw), sub_commit, sub_commit)

    populate(root, checkout, tree, commit, (env["GIT_OBJECT_DIRECTORY"],))
    (checkout / ".spec").mkdir(exist_ok=True)
    return checkout


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

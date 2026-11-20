"""Local-only Spec records: index removal preserves files and never publishes.

The policy excludes harness records, not `.spec/architecture` source contracts.
Git's shared info/exclude and a local marker cover linked worktrees; a project
.gitignore with the same rules carries the policy to subsequent clones.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import gitx
from path_safety import validate_source_tree

MARKER_NAME = "spec-local-evidence.json"
EVIDENCE_DIRS = ("specs", "docs", "plans", "archive", "autoplan", "autorun", "artifacts", "cache")
EVIDENCE_FILES = ("charter.md", "lessons.md")
SCOPES = tuple(f".spec/{name}" for name in (*EVIDENCE_DIRS, *EVIDENCE_FILES))
IGNORE_RULES = tuple(f"/.spec/{name}/" for name in EVIDENCE_DIRS) + tuple(f"/.spec/{name}" for name in EVIDENCE_FILES)
EXCLUDE_HEADER = "# Spec Harness local evidence (files stay on disk; architecture stays tracked)"


def is_evidence_path(path: str) -> bool:
    return any(path == scope or path.startswith(scope + "/") for scope in SCOPES)


def _common_dir(root: Path) -> Path | None:
    result = gitx.git(root, "rev-parse", "--git-common-dir", check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    return (path if path.is_absolute() else root / path).resolve()


def marker_path(root: Path) -> Path | None:
    common = _common_dir(root)
    return common / MARKER_NAME if common is not None else None


def is_local(root: Path) -> bool:
    """Read policy without creating state, including on a fresh clone."""
    marker = marker_path(root)
    if marker is None:
        return False
    if marker.is_symlink():
        raise ValueError(f"local evidence marker must be a regular file: {marker}")
    if marker.is_file():
        data = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError(f"invalid local evidence marker: {marker}")
        return True
    result = gitx.git(root, "check-ignore", "--no-index", "-q", "--", ".spec/specs/", check=False)
    if result.returncode not in (0, 1):
        raise ValueError(result.stderr.strip() or "Git ignore lookup failed")
    return result.returncode == 0


def ensure(root: Path) -> None:
    """Install shared local rules; leave repository and unrelated excludes alone."""
    common = _common_dir(root)
    if common is None:
        raise ValueError("purge requires a Git worktree")
    marker = common / MARKER_NAME
    exclude = common / "info" / "exclude"
    if marker.is_symlink() or exclude.is_symlink() or exclude.parent.is_symlink():
        raise ValueError("local evidence metadata must not be symlinked")
    original = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    lines = original.splitlines()
    missing = [line for line in (EXCLUDE_HEADER, *IGNORE_RULES) if line not in lines]
    if missing:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as stream:
            if original and not original.endswith("\n"):
                stream.write("\n")
            stream.write("\n".join(missing) + "\n")
    content = json.dumps({"version": 1, "paths": list(SCOPES)}, indent=2) + "\n"
    if not marker.exists() or marker.read_text(encoding="utf-8") != content:
        marker.write_text(content, encoding="utf-8")


def _files(root: Path) -> list[Path]:
    files = []
    if (root / ".spec").is_symlink():
        raise ValueError(".spec must not be a symlink")
    for scope in SCOPES:
        base = root / scope
        if base.is_symlink() or base.is_file():
            files.append(base)
        elif base.is_dir():
            for parent, directories, names in os.walk(base, followlinks=False):
                files.extend(Path(parent) / name for name in names)
                files.extend(Path(parent) / name for name in directories if (Path(parent) / name).is_symlink())
    return files


def _tracked(root: Path) -> list[str]:
    names = gitx.git(root, "ls-files", "-z", "--", *SCOPES, text=False).stdout
    return [os.fsdecode(item) for item in names.split(b"\0") if item]


def untrack(root: Path) -> int:
    """Retire index entries, never force away staged content or remove files."""
    before = _tracked(root)
    if not before:
        return 0
    missing = [name for name in before if not os.path.lexists(root / name)]
    if missing:
        raise ValueError("restore missing evidence before purge: " + ", ".join(missing[:5]))
    result = gitx.git(root, "rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", *SCOPES, check=False)
    if result.returncode != 0:
        raise ValueError("evidence index removal failed: " + result.stderr.strip())
    remaining = _tracked(root)
    if remaining:
        raise ValueError("evidence remains tracked: " + ", ".join(remaining[:5]))
    return len(before)


def purge(root: Path, dry_run: bool = False) -> dict:
    """Report or localize known records, without commits, pushes or history edits."""
    root = gitx.toplevel(root) or root
    tracked = _tracked(root)
    files = _files(root)
    report = {
        "local_mode": is_local(root),
        "scopes": list(SCOPES),
        "tracked_before": len(tracked),
        "tracked_after": len(tracked),
        "files_on_disk": len(files),
        "bytes_on_disk": sum(path.lstat().st_size for path in files),
        "dry_run": dry_run,
    }
    if not dry_run:
        ensure(root)
        report.update(untracked=untrack(root), tracked_after=0, local_mode=True)
        report["marker"] = str(marker_path(root))
    return report


def _archive_target(root: Path, slug: str) -> Path:
    entries = gitx.worktrees(root)
    primary = entries[0]["path"].resolve() if entries else root.resolve()
    target = primary
    for name in (".spec", "specs", "archive", slug):
        target = target / name
        if target.is_symlink():
            raise ValueError(f"archive destination must not be a symlink: {target}")
    return target


def preflight_archive(root: Path, slug: str) -> None:
    if is_local(root):
        validate_source_tree(root / ".spec" / "specs" / slug)
        target = _archive_target(root, slug)
        if target.exists():
            raise ValueError(f"local archive already exists: {target}")


def sync_archive(root: Path, destination: Path, slug: str) -> str | None:
    """Preserve the archive before linked-worktree cleanup; never overlay one."""
    if not is_local(root):
        return None
    target = _archive_target(root, slug)
    if target.resolve() == destination.resolve():
        return str(target)
    if target.exists():
        raise ValueError(f"local archive already exists: {target}")
    validate_source_tree(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".spec-archive-", dir=target.parent) as temp:
        staged = Path(temp) / slug
        shutil.copytree(destination, staged)
        staged.rename(target)
    return str(target)

#!/usr/bin/env python3
"""Check active Spec packages and archives changed in a Git snapshot."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import gitx
from check_spec_package import overall_check_passed
from spec_package_support import (
    extract_integration_branch,
    is_development_record_slug,
    read_regular_text,
    resolve_specs_root,
    scope_from_changed_paths,
)

SKIP_DIRECTORIES = {
    ".claude",
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".svn",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
TIMEOUT = 60
COMMIT_INTERVAL_LINE_RE = re.compile(
    r"^- (?:提交区间：|Commit interval: )([0-9a-f]{40})\.\.([0-9a-f]{40})\s*$", re.MULTILINE
)
FOOTER_WINDOW_BASE_RE = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass(frozen=True)
class CheckFailure:
    specs_root: Path
    slug: str
    reason: str


@dataclass(frozen=True)
class FooterIntervalWarning:
    specs_identity: str
    slug: str
    base: str
    head: str
    window_base: str


def archive_commit_interval(summary_text: str) -> tuple[str, str] | None:
    match = COMMIT_INTERVAL_LINE_RE.search(summary_text)
    return (match.group(1), match.group(2)) if match else None


def discover_specs_roots(root: Path, explicit: list[str] | None = None) -> list[Path]:
    root = root.resolve()
    roots = {resolve_specs_root(root, name).resolve() for name in explicit or []}
    for name in (".spec", ".trae"):
        default = root / name
        if default.is_symlink():
            raise ValueError(f"default Spec root must not be a symlink: {default}")
        if (default / "specs").is_dir():
            roots.add(default.resolve())

    def on_error(error: OSError) -> None:
        raise error

    for current, directories, _files in os.walk(root, followlinks=False, onerror=on_error):
        current_path = Path(current)
        if current_path != root and (current_path / ".git").exists():
            directories[:] = []
            continue
        directories[:] = [
            name for name in directories if name not in SKIP_DIRECTORIES and not (current_path / name).is_symlink()
        ]
        if "specs" in directories:
            if current_path.name in {".spec", ".trae"}:
                roots.add(current_path.resolve())
            directories.remove("specs")
    return sorted(roots)


def active_package_dirs(specs_root: Path) -> list[Path]:
    path = specs_root / "specs"
    if not path.is_dir():
        return []
    return sorted(
        child
        for child in path.iterdir()
        if child.name != "archive" and not child.name.startswith(".") and (child.is_dir() or child.is_symlink())
    )


def _validate_package(specs_root: Path, package: Path, *, archived: bool, lint_only: bool) -> list[CheckFailure]:
    slug = package.name
    if package.is_symlink() or not package.is_dir():
        return [CheckFailure(specs_root, slug, "package must be a non-symlink directory")]
    if not is_development_record_slug(slug):
        return [CheckFailure(specs_root, slug, "invalid Development Record slug")]
    try:
        spec = read_regular_text(package / "spec.md")
        tasks = read_regular_text(package / "tasks.md")
        checklist_path = package / "checklist.md"
        checklist = read_regular_text(checklist_path) if checklist_path.exists() else None
        if archived and not read_regular_text(package / "completion-summary.md").strip():
            return [CheckFailure(specs_root, slug, "completion summary is empty")]
    except (OSError, UnicodeError, ValueError) as exc:
        return [CheckFailure(specs_root, slug, str(exc))]
    # check/done own live evidence and branch binding. This shared evaluator
    # enforces delivery content without trying to fingerprint an exported tree.
    if not lint_only and not overall_check_passed(spec, tasks, checklist, slug=slug, package_dir=package):
        return [CheckFailure(specs_root, slug, "check gates not passed")]
    return []


def _changed_archives(paths: list[str], identities: set[str]) -> set[tuple[str, str]]:
    changed: set[tuple[str, str]] = set()
    roots = sorted((Path(identity).parts, identity) for identity in identities)
    for path in paths:
        parts = Path(path).parts
        for prefix, identity in roots:
            tail = parts[len(prefix) :] if parts[: len(prefix)] == prefix else ()
            if len(tail) < 3 or tail[:2] != ("specs", "archive") or tail[2] == "retired":
                continue
            if len(tail) == 3 and not is_development_record_slug(tail[2]):
                continue  # e.g. archive/README.md
            changed.add((identity, tail[2]))
    return changed


def check_all_packages(
    root: Path,
    explicit: list[str] | None = None,
    *,
    slugs: list[str] | None = None,
    lint_only: bool = False,
    changed_paths: list[str] | None = None,
) -> list[CheckFailure]:
    root = root.resolve()
    try:
        specs_roots = discover_specs_roots(root, explicit)
        identities = {specs.relative_to(root).as_posix(): specs for specs in specs_roots}
        changed = _changed_archives(changed_paths or [], set(identities))
        failures: list[CheckFailure] = []
        found: set[str] = set()
        selected = set(slugs or [])
        invalid = {slug for slug in selected if not is_development_record_slug(slug)}
        if invalid:
            return [CheckFailure(root, slug, "invalid Development Record slug") for slug in sorted(invalid)]
        for identity, specs_root in identities.items():
            active = active_package_dirs(specs_root)
            branches: dict[str, list[str]] = {}
            for package in active:
                if (specs_root / "specs" / "archive" / package.name).exists():
                    failures.append(
                        CheckFailure(specs_root, package.name, "package exists in both active and archive directories")
                    )
                try:
                    branch = extract_integration_branch(read_regular_text(package / "spec.md"))
                    if branch:
                        branches.setdefault(branch, []).append(package.name)
                except (OSError, UnicodeError, ValueError):
                    pass  # The package read below reports the error.
                if selected and package.name not in selected:
                    continue
                found.add(package.name)
                failures.extend(_validate_package(specs_root, package, archived=False, lint_only=lint_only))
            for branch, owners in branches.items():
                if len(owners) > 1:
                    failures.append(
                        CheckFailure(
                            specs_root,
                            ",".join(owners),
                            f"integration branch bound to multiple active packages: {branch}",
                        )
                    )
            for changed_root, slug in sorted(changed):
                if changed_root != identity:
                    continue
                package = specs_root / "specs" / "archive" / slug
                # A fully removed archive has no remaining files to validate.
                if not package.exists() and not package.is_symlink():
                    continue
                found.add(slug)
                failures.extend(_validate_package(specs_root, package, archived=True, lint_only=lint_only))
            # Slug lookup is existence-only; never regrade a frozen archive.
            for slug in selected - found:
                archive = specs_root / "specs" / "archive" / slug
                if archive.is_dir() and not archive.is_symlink():
                    found.add(slug)
        failures.extend(
            CheckFailure(root, slug, "explicit package slug not found in active or archive")
            for slug in sorted(selected - found)
        )
        return failures
    except (OSError, UnicodeError, ValueError) as exc:
        return [CheckFailure(root, "<discovery>", str(exc))]


def _changed_paths(root: Path, revision: str | None = None, baseline: str | None = None) -> list[str]:
    env = gitx.clean_env(
        index_file=Path(os.environ["GIT_INDEX_FILE"]) if revision is None and os.environ.get("GIT_INDEX_FILE") else None
    )
    if revision is None:
        args = ("diff", "--cached", "--name-only", "--no-renames", "-z")
    elif baseline and set(baseline) != {"0"}:
        args = ("diff", "--name-only", "--no-renames", "-z", baseline, revision)
    elif baseline:  # A new remote ref has no predecessor: compare with the empty tree.
        empty_tree = gitx.git(root, "hash-object", "-t", "tree", "--stdin", input="", timeout=TIMEOUT).stdout.strip()
        args = ("diff", "--name-only", "--no-renames", "-z", empty_tree, revision)
    else:
        parent = gitx.git(root, "rev-parse", "--verify", f"{revision}^1", check=False, timeout=TIMEOUT)
        if parent.returncode == 0:
            args = ("diff", "--name-only", "--no-renames", "-z", parent.stdout.strip(), revision)
        else:
            args = ("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "--no-renames", "-z", revision)
    return [path for path in gitx.git(root, *args, env=env, timeout=TIMEOUT).stdout.split("\0") if path]


@contextmanager
def git_revision_snapshot(root: Path, revision: str):
    tree = gitx.git(root, "rev-parse", "--verify", f"{revision}^{{tree}}", timeout=TIMEOUT).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="spec-revision-") as temp_dir:
        temporary = Path(temp_dir)
        snapshot = temporary / "snapshot"
        snapshot.mkdir()
        environment = gitx.clean_env(index_file=temporary / "index")
        gitx.git(root, "read-tree", tree, env=environment, timeout=TIMEOUT)
        gitx.git(root, "checkout-index", "--all", f"--prefix={snapshot}{os.sep}", env=environment, timeout=TIMEOUT)
        yield snapshot


def check_git_index(
    root: Path,
    explicit: list[str] | None = None,
    *,
    slugs: list[str] | None = None,
    lint_only: bool = False,
) -> list[CheckFailure]:
    try:
        paths = _changed_paths(root)
        with tempfile.TemporaryDirectory(prefix="spec-index-") as temp_dir:
            snapshot = Path(temp_dir)
            environment = gitx.clean_env(
                index_file=Path(os.environ["GIT_INDEX_FILE"]) if os.environ.get("GIT_INDEX_FILE") else None
            )
            gitx.git(root, "checkout-index", "--all", f"--prefix={snapshot}{os.sep}", env=environment, timeout=TIMEOUT)
            return check_all_packages(snapshot, explicit, slugs=slugs, lint_only=lint_only, changed_paths=paths)
    except subprocess.CalledProcessError as exc:
        return [CheckFailure(root, "<git-index>", (exc.stderr or str(exc)).strip())]
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return [CheckFailure(root, "<git-index>", str(exc))]


def check_git_revision(
    root: Path,
    revision: str,
    explicit: list[str] | None = None,
    *,
    slugs: list[str] | None = None,
    baseline: str | None = None,
    lint_only: bool = False,
) -> list[CheckFailure]:
    try:
        paths = _changed_paths(root, revision, baseline)
        with git_revision_snapshot(root, revision) as snapshot:
            return check_all_packages(snapshot, explicit, slugs=slugs, lint_only=lint_only, changed_paths=paths)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return [CheckFailure(root, "<git-revision>", str(exc))]


def footer_interval_warnings(
    root: Path,
    slugs: list[str],
    *,
    revision: str,
    window_base: str,
    specs_dirs: list[str] | None = None,
) -> list[FooterIntervalWarning]:
    if not slugs or not FOOTER_WINDOW_BASE_RE.fullmatch(window_base):
        return []
    try:
        identities = [path.relative_to(root.resolve()).as_posix() for path in discover_specs_roots(root, specs_dirs)]
        warnings: list[FooterIntervalWarning] = []
        for identity in identities:
            for slug in sorted(set(slugs)):
                path = f"{identity}/specs/archive/{slug}/completion-summary.md"
                summary = gitx.git(root, "show", f"{revision}:{path}", check=False, timeout=TIMEOUT)
                interval = archive_commit_interval(summary.stdout) if summary.returncode == 0 else None
                if interval is None or interval[0] == window_base:
                    continue
                base, head = interval
                ancestor = gitx.git(
                    root, "merge-base", "--is-ancestor", base, window_base, check=False, timeout=TIMEOUT
                )
                if ancestor.returncode == 0:
                    warnings.append(FooterIntervalWarning(identity, slug, base, head, window_base))
        return warnings
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        return []


def render_footer_interval_warnings(warnings: list[FooterIntervalWarning]) -> str:
    lines = ["spec gate warning: footer attribution cites an archive delivered before this push window (advisory only)"]
    for warning in warnings:
        lines.extend(
            [
                f"- slug: {warning.slug}",
                f"- archive: {warning.specs_identity}/specs/archive/{warning.slug}",
                f"- archive commit range: {warning.base}..{warning.head}",
                f"- push window base: {warning.window_base}",
            ]
        )
    return "\n".join(lines)


def render_failures(root: Path, failures: list[CheckFailure]) -> str:
    lines = ["# Spec Disk Truth", "", f"- root: {root}", "- result: blocked", "", "## Failures"]
    return "\n".join([*lines, *(f"- {failure.specs_root}:{failure.slug}: {failure.reason}" for failure in failures)])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Project root (default: current directory)")
    parser.add_argument("--specs-dir", action="append", default=[], help="Additional trusted Spec root; repeatable")
    parser.add_argument("--slug", action="append", default=[], dest="slugs", help="Filter active packages; repeatable")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--index", action="store_true", help="Check the Git index and its staged archive changes")
    group.add_argument("--revision", help="Check a Git revision and its archive changes")
    parser.add_argument("--baseline", help="Start of revision diff (default: target commit's parent)")
    parser.add_argument("--lint-only", action="store_true", help="Skip delivery gates for work in progress")
    parser.add_argument("--scope-paths-file", help="Parse trusted Spec slugs from newline-delimited changed paths")
    parser.add_argument("--footer-window-base", help="Warn when footer attribution predates the push window")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    if args.scope_paths_file:
        try:
            paths = read_regular_text(Path(args.scope_paths_file)).splitlines()
            for slug in scope_from_changed_paths(paths, args.specs_dir):
                print(f"SPEC {slug}")
            return 0
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"error: cannot parse changed-path scope: {exc}", file=sys.stderr)
            return 1
    if args.baseline and not args.revision:
        print("error: --baseline requires --revision", file=sys.stderr)
        return 1
    if args.footer_window_base and (
        not args.revision or not args.slugs or not FOOTER_WINDOW_BASE_RE.fullmatch(args.footer_window_base)
    ):
        print("error: --footer-window-base requires --revision, --slug, and a commit SHA", file=sys.stderr)
        return 1
    options = {"slugs": args.slugs or None, "lint_only": args.lint_only}
    if args.index:
        failures = check_git_index(root, args.specs_dir, **options)
    elif args.revision:
        failures = check_git_revision(root, args.revision, args.specs_dir, baseline=args.baseline, **options)
    else:
        failures = check_all_packages(root, args.specs_dir, **options)
    if args.footer_window_base:
        warnings = footer_interval_warnings(
            root, args.slugs, revision=args.revision, window_base=args.footer_window_base, specs_dirs=args.specs_dir
        )
        if warnings:
            print(render_footer_interval_warnings(warnings), file=sys.stderr)
    if failures:
        print(render_failures(root, failures))
        return 1
    print("# Spec Disk Truth\n\n- result: passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Validate v3 package structure, branch attribution, and pushed snapshots."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import gitx
import package
import ui_review

SLUG = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}_[a-z0-9][a-z0-9_-]*$")
COMMIT_INTERVAL = re.compile(
    r"^- (?:Commit interval: |提交区间：)([0-9a-f]{40})(?:\.\.([0-9a-f]{40}))?\s*$", re.MULTILINE
)


def footer_interval_warnings(root: Path, slugs: list[str], revision: str, window_base: str) -> list[str]:
    if not re.fullmatch(r"[0-9a-f]{40,64}", window_base):
        raise ValueError("invalid footer window base")
    warnings = []
    for slug in sorted(set(slugs)):
        path = f".spec/specs/archive/{slug}/completion-summary.md"
        summary = gitx.git(root, "show", f"{revision}:{path}", check=False)
        interval = archive_commit_interval(summary.stdout) if summary.returncode == 0 else None
        if interval is None or interval[0] == window_base:
            continue
        base, head = interval
        ancestor = gitx.git(root, "merge-base", "--is-ancestor", base, window_base, check=False)
        if ancestor.returncode == 0:
            warnings.extend(
                [f"- slug: {slug}", f"- archive commit range: {base}..{head}", f"- push window base: {window_base}"]
            )
    return warnings


def archive_commit_interval(summary: str) -> tuple[str, str] | None:
    match = COMMIT_INTERVAL.search(summary)
    return (match.group(1), match.group(2) or match.group(1)) if match else None


def _evidence_errors(path: Path, tasks_text: str, spec: str) -> list[str]:
    latest = package.latest_runs(path)
    criteria_tasks = {task for _key, _text, task in package.criteria(spec)}
    errors = []
    for task in package.parse_tasks(tasks_text):
        record = latest.get(task.id, {})
        if task.blocked:
            errors.append(f"{task.id}: blocked task cannot be delivered")
        elif not task.checked:
            errors.append(f"{task.id}: unfinished task")
        if task.id in criteria_tasks and record.get("exit") != 0:
            errors.append(f"{task.id}: latest verify did not pass")
        if task.command and record.get("cmd") not in (None, task.command):
            errors.append(f"{task.id}: evidence command drift")
    if package.parse_ledger(spec, "Parked", "P"):
        errors.append("parked decisions remain")
    return errors


def package_errors(root: Path, path: Path, archived: bool, lint_only: bool) -> list[str]:
    if path.is_symlink() or not path.is_dir() or not SLUG.fullmatch(path.name):
        return [f"{path}: invalid Development Record package"]
    try:
        spec = (path / "spec.md").read_text(encoding="utf-8")
        tasks = (path / "tasks.md").read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: {exc}"]
    if "spec-format: 3" not in spec:
        return [f"{path}: only v3 packages are supported"]
    errors = []
    try:
        package.extract_branch(spec)
        package.parse_tasks(tasks)
    except ValueError as exc:
        errors.append(f"{path}: {exc}")
    if not lint_only:
        errors.extend(_evidence_errors(path, tasks, spec))
    if archived and not lint_only:
        summary = path / "completion-summary.md"
        if not summary.is_file() or not summary.read_text(encoding="utf-8").strip():
            errors.append(f"{path}: completion summary is empty")
        archive_runs = path / "runs.jsonl"
        if not archive_runs.is_file():
            errors.append(f"{path}: archived evidence is missing")
        errors.extend(f"{path}: {item}" for item in ui_review.archived_errors(path, spec, tasks))
    elif not archived and not lint_only and not errors:
        from gate import compute

        errors.extend(compute(root, path.name)["errors"])
    return errors


def scope_from_paths(paths: list[str], specs_dirs: list[str] | None = None) -> list[str]:
    roots = specs_dirs or [".spec"]
    found = set()
    for path in paths:
        for root in roots:
            if root in {".spec", ".trae"}:
                match = re.fullmatch(re.escape(root) + r"/specs/(?:archive/)?([^/]+)(?:/.+|)", path)
            else:
                match = re.fullmatch(re.escape(root.rstrip("/")) + r"/specs/(?:archive/)?([^/]+)/.+", path)
            if match and match.group(1) not in {"retired", "archive"} and SLUG.fullmatch(match.group(1)):
                found.add(match.group(1))
    return sorted(found)


def check_tree(
    root: Path,
    *,
    slugs: list[str] | None = None,
    lint_only: bool = False,
    changed_paths: list[str] | None = None,
    specs_dirs: list[str] | None = None,
) -> list[str]:
    selected = set(slugs or [])
    changed = set(changed_paths or [])
    errors = []
    roots = [root / name / "specs" for name in specs_dirs] if specs_dirs else [package.specs_root(root)]
    active = [(specs, path) for specs in roots for path in specs.glob("*/spec.md") if path.is_file()]
    branch_owners: dict[str, list[str]] = {}
    for _specs, spec_path in active:
        directory = spec_path.parent
        try:
            branch = package.extract_branch(spec_path.read_text(encoding="utf-8"))
            branch_owners.setdefault(branch, []).append(directory.name)
        except ValueError:
            pass
        if selected and directory.name not in selected:
            continue
        errors.extend(package_errors(root, directory, False, lint_only))
    for branch, owners in branch_owners.items():
        if len(owners) > 1:
            errors.append(f"integration branch bound to multiple active packages: {branch}")
    archives = []
    for path in sorted(changed):
        for specs in roots:
            identity = specs.relative_to(root).as_posix() if specs.is_relative_to(root) else specs.as_posix()
            match = re.fullmatch(re.escape(identity) + r"/archive/([^/]+)(?:/.+)?", path)
            if not match:
                continue
            name = match.group(1)
            if name == "retired":
                continue
            if len(path.split("/")) == len(identity.split("/")) + 2 and not SLUG.fullmatch(name):
                continue  # plain file under archive/, e.g. README.md
            if not SLUG.fullmatch(name):
                errors.append(f"invalid archive slug: {name} in {path}")
                continue
            archive = specs / "archive" / name
            if archive.exists() or archive.is_symlink():
                archives.append(archive)
    for path in sorted(set(archives)):
        errors.extend(package_errors(root, path, True, lint_only))
    known = {path.parent.name for _specs, path in active} | {
        path.name for specs in roots for path in (specs / "archive").glob("*") if path.is_dir()
    }
    errors.extend(f"explicit package slug not found: {slug}" for slug in sorted(selected - known))
    return errors


def changed_paths(root: Path, revision: str | None, baseline: str | None) -> list[str]:
    environment = gitx.clean_env(
        index_file=Path(os.environ["GIT_INDEX_FILE"]) if revision is None and os.environ.get("GIT_INDEX_FILE") else None
    )
    if revision is None:
        arguments = ["diff", "--cached", "--name-only", "--no-renames"]
    elif baseline and set(baseline) != {"0"}:
        arguments = ["diff", "--name-only", "--no-renames", baseline, revision]
    elif baseline:
        empty_tree = gitx.git(root, "hash-object", "-t", "tree", "--stdin", input="").stdout.strip()
        arguments = ["diff", "--name-only", "--no-renames", empty_tree, revision]
    else:
        parent = gitx.git(root, "rev-parse", "--verify", f"{revision}^1", check=False)
        if parent.returncode == 0:
            arguments = ["diff", "--name-only", "--no-renames", parent.stdout.strip(), revision]
        else:
            arguments = [
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "-r",
                "--no-renames",
                revision,
            ]
    return [line for line in gitx.git(root, *arguments, env=environment).stdout.splitlines() if line]


@contextmanager
def _checkout_tree(root: Path, revision: str | None, environment: dict[str, str]):
    with tempfile.TemporaryDirectory(prefix="spec-v3-index-") as temp:
        destination = Path(temp) / "tree"
        gitx.git(root, "checkout-index", "--all", f"--prefix={destination}{os.sep}", env=environment)
        yield destination


def snapshot(root: Path, revision: str):
    temporary = tempfile.TemporaryDirectory(prefix="spec-v3-revision-")
    destination = Path(temporary.name)
    environment = gitx.clean_env(index_file=destination / "index")
    gitx.git(root, "read-tree", revision, env=environment)
    gitx.git(root, "checkout-index", "--all", f"--prefix={destination / 'tree'}{os.sep}", env=environment)
    return temporary, destination / "tree"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--specs-dir", action="append", default=[])
    parser.add_argument("--slug", action="append", default=[])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--index", action="store_true")
    group.add_argument("--revision")
    parser.add_argument("--baseline")
    parser.add_argument("--lint-only", action="store_true")
    parser.add_argument("--scope-paths-file")
    parser.add_argument("--footer-window-base")
    parser.add_argument("--wip-channel", action="store_true")
    parser.add_argument("--wip-branch")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    if args.baseline and not args.revision:
        print("error: --baseline requires --revision", file=sys.stderr)
        return 1
    if args.footer_window_base and (not args.revision or not args.slug):
        print("error: --footer-window-base requires --revision and --slug", file=sys.stderr)
        return 1
    if args.wip_channel or args.wip_branch:
        branch = args.wip_branch or gitx.branch(root)
        roots = [root / name / "specs" for name in args.specs_dir] if args.specs_dir else [package.specs_root(root)]
        matches = []
        for specs in roots:
            for path in specs.glob("*/spec.md"):
                try:
                    if package.extract_branch(path.read_text(encoding="utf-8")) == branch:
                        matches.append(path.parent.name)
                except ValueError:
                    continue
        protected = branch in package.PROTECTED or branch.startswith("release/")
        passed = len(matches) == 1 and not protected
        print("# Spec Disk Truth\n\n- channel: wip\n- result: " + ("passed" if passed else "blocked"))
        return 0 if passed else 1
    if args.scope_paths_file:
        paths = Path(args.scope_paths_file).read_text(encoding="utf-8").splitlines()
        for slug in scope_from_paths(paths, args.specs_dir):
            print(f"SPEC {slug}")
        return 0
    temporary = None
    try:
        environment = gitx.clean_env()
        if args.revision:
            temporary, target = snapshot(root, args.revision)
            paths = changed_paths(root, args.revision, args.baseline)
        elif args.index:
            index_file = os.environ.get("GIT_INDEX_FILE")
            if index_file:
                environment = gitx.clean_env(index_file=Path(index_file))
            paths = changed_paths(root, None, None)
            with _checkout_tree(root, None, environment) as target:
                errors = check_tree(
                    target,
                    slugs=args.slug,
                    lint_only=args.lint_only,
                    changed_paths=paths,
                    specs_dirs=args.specs_dir,
                )
            target = None
        else:
            target = root
            paths = changed_paths(root, None, None)
        if target is not None:
            errors = check_tree(
                target,
                slugs=args.slug,
                lint_only=args.lint_only,
                changed_paths=paths,
                specs_dirs=args.specs_dir,
            )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        errors = [str(exc)]
    finally:
        if temporary:
            temporary.cleanup()
    if args.footer_window_base:
        warnings = footer_interval_warnings(root, args.slug, args.revision, args.footer_window_base)
        if warnings:
            print(
                "spec gate warning: footer attribution cites an archive delivered before this push window "
                "(advisory only)",
                file=sys.stderr,
            )
            print("\n".join(warnings), file=sys.stderr)
    if errors:
        print("# Spec Disk Truth\n\n- result: blocked\n\n## Failures")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("# Spec Disk Truth\n\n- result: passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
import local_evidence
import package
import ui_review
from path_safety import validate_source_tree

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
    if package.pending_parked(path, spec):
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


def _commit_footer_slugs(message: str) -> list[str]:
    return [
        match.group(1)
        for line in message.splitlines()
        if (match := re.fullmatch(r"Spec:\s*([a-z0-9][a-z0-9_-]*)\s*", line)) and SLUG.fullmatch(match.group(1))
    ]


def _window_footers(root: Path, revision: str, baseline: str | None) -> dict[str, str]:
    """Latest slug-attributed commits in the checked revision's push window."""
    arguments = ["log", "--topo-order", "--no-merges", "-z", "--format=%H%x00%B", revision]
    if baseline and set(baseline) != {"0"}:
        arguments.append(f"^{baseline}")
    fields = gitx.git(root, *arguments, text=False).stdout.split(b"\0")
    commits: dict[str, str] = {}
    for index in range(0, len(fields) - 1, 2):
        head = fields[index].decode("ascii")
        message = fields[index + 1].decode("utf-8", "replace")
        for slug in _commit_footer_slugs(message):
            commits.setdefault(slug, head)
    return commits


def _snapshot_package_exists(target: Path, slug: str, specs_dirs: list[str] | None) -> bool:
    roots = [target / name / "specs" for name in specs_dirs] if specs_dirs else [package.specs_root(target)]
    return any((specs / slug).exists() or (specs / "archive" / slug).exists() for specs in roots)


def _local_archive_candidates(root: Path, slug: str) -> list[Path]:
    """Search every registered worktree, preferring the current checkout."""
    roots = [root, *(entry["path"] for entry in gitx.worktrees(root))]
    paths: list[Path] = []
    seen: set[Path] = set()
    for candidate_root in roots:
        identity = candidate_root.resolve()
        if identity in seen:
            continue
        seen.add(identity)
        archive = identity / ".spec" / "specs" / "archive" / slug
        if archive.exists() or archive.is_symlink():
            paths.append(archive)
    return paths


def _historical_archive_matches(root: Path, archive: Path, slug: str, head: str) -> bool:
    """Frozen pre-v3 records retain their exact tracked bytes, not v3 grading."""
    prefix = f".spec/specs/archive/{slug}/"
    listing = gitx.git(root, "ls-tree", "-r", "-z", head, "--", prefix, check=False, text=False)
    if listing.returncode != 0 or not listing.stdout:
        return False
    names = set()
    for entry in listing.stdout.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, blob = metadata.split()
        name = os.fsdecode(raw_path).removeprefix(prefix)
        path = archive / name
        if kind != b"blob" or mode not in {b"100644", b"100755"} or not path.is_file():
            return False
        stored = gitx.git(root, "cat-file", "blob", blob.decode("ascii"), text=False).stdout
        if path.read_bytes() != stored:
            return False
        names.add(name)
    return {"spec.md", "tasks.md", "completion-summary.md"} <= names


def _local_archive_errors(
    root: Path, archive: Path, slug: str, head: str | None, revision: str, *, pending: bool = False
) -> list[str]:
    """Validate a new delivery, including its binding to the attributed commit."""
    try:
        owner = archive.parents[3]
        for directory in (owner / ".spec", owner / ".spec" / "specs", archive.parent):
            if directory.is_symlink():
                return [f"{archive}: local archive parent must not be a symlink"]
        validate_source_tree(archive)
        spec = (archive / "spec.md").read_text(encoding="utf-8")
        if "spec-format: 3" not in spec:
            if not pending and head and _historical_archive_matches(root, archive, slug, head):
                return []
            return [f"{archive}: historical archive is missing or differs from its attributed commit"]
        errors = package_errors(root, archive, archived=True, lint_only=False)
        if errors:
            return errors
        tasks = package.parse_tasks((archive / "tasks.md").read_text(encoding="utf-8"))
        records = package.runs(archive)
        latest = package.latest_runs(archive)
        criteria = package.criteria(spec)
        deliveries = [record for record in records if record.get("kind") == "delivery"]
        if deliveries or pending:
            errors.extend(package.dependency_errors(tasks))
            frozen = package.package_baseline(archive)
            final_gate = package.latest_gate(archive)
            legacy = not pending and "ui_criteria" not in frozen and "ui_review" not in final_gate
            errors.extend(package.frozen_commands(archive, spec, tasks, legacy=legacy))
            if not frozen:
                errors.append(f"{archive}: package baseline is missing")
            if not tasks or not criteria:
                errors.append(f"{archive}: delivery has no tasks or criteria")
            ids = {task.id for task in tasks}
            if any(task_id not in ids for _key, _text, task_id in criteria):
                errors.append(f"{archive}: criterion references an unknown task")
            criterion_tasks = {row[2] for row in criteria}
            if any(task.id not in criterion_tasks and task.origin != "support" for task in tasks):
                errors.append(f"{archive}: task is not covered by a criterion")
            for task in tasks:
                record = latest.get(task.id, {})
                if (
                    not task.command
                    or record.get("cmd") != task.command
                    or record.get("exit") != 0
                    or not record.get("final")
                ):
                    errors.append(f"{archive}: {task.id}: final verification is missing or invalid")
            if (
                final_gate.get("passed") is not True
                or final_gate.get("final") is not True
                or final_gate.get("errors")
                or final_gate.get("parked")
            ):
                errors.append(f"{archive}: passing final gate record is missing")
            if not pending:
                delivery_head = deliveries[-1].get("head")
                if head is not None:
                    if delivery_head != head:
                        errors.append(f"{archive}: delivery commit does not match Spec: {slug} ({head})")
                elif not isinstance(delivery_head, str) or not re.fullmatch(r"[0-9a-f]{40,64}", delivery_head):
                    errors.append(f"{archive}: delivery commit is missing or invalid")
                else:
                    ancestor = gitx.git(root, "merge-base", "--is-ancestor", delivery_head, revision, check=False)
                    if ancestor.returncode != 0:
                        errors.append(f"{archive}: delivery commit is not in the pushed history")
            if "ui_review" not in final_gate:
                declared = [*package.scope_patterns(spec), *(pattern for task in tasks for pattern in task.globs)]
                if any(ui_review._pattern_is_ui(pattern) for pattern in declared):
                    if not (archive / ui_review.RECORD_NAME).is_file():
                        errors.append(f"{archive}: required ui-review.json is missing")
        else:
            # Archives created before the local-delivery ledger have no
            # delivery record. Legacy semantics apply only when the archive
            # was really tracked in Git at its attributed commit, so a
            # new local delivery cannot pass by deleting its delivery row.
            summary = (archive / "completion-summary.md").read_text(encoding="utf-8")
            interval = archive_commit_interval(summary)
            if interval is None:
                errors.append(f"{archive}: legacy archive has no commit interval")
                return errors
            ancestor = gitx.git(root, "merge-base", "--is-ancestor", interval[1], revision, check=False)
            if ancestor.returncode != 0:
                errors.append(f"{archive}: legacy archive is not in the pushed history")
                return errors
            legacy_head = head or interval[1]
            for name in ("spec.md", "tasks.md", "runs.jsonl", "completion-summary.md"):
                blob = gitx.git(
                    root,
                    "show",
                    f"{legacy_head}:.spec/specs/archive/{slug}/{name}",
                    check=False,
                    text=False,
                )
                if blob.returncode != 0 or blob.stdout != (archive / name).read_bytes():
                    errors.append(f"{archive}: legacy archive was not tracked at its attributed commit")
                    break
        return errors
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return [f"{archive}: invalid local delivery evidence: {exc}"]


def _check_local_archives(root: Path, slugs: dict[str, str | None], revision: str) -> list[str]:
    errors: list[str] = []
    for slug, head in sorted(slugs.items()):
        if head is not None:
            ancestor = gitx.git(root, "merge-base", "--is-ancestor", head, revision, check=False)
            if ancestor.returncode != 0:
                errors.append(f"{slug}: delivery commit is not an ancestor of {revision}")
                continue
        candidates = _local_archive_candidates(root, slug)
        if not candidates:
            errors.append(f"explicit package slug not found: {slug}")
            continue
        attempts = [_local_archive_errors(root, archive, slug, head, revision) for archive in candidates]
        if not any(not attempt for attempt in attempts):
            errors.extend(attempts[0])
    return errors


def _legacy_deleted_slugs(
    root: Path,
    target: Path,
    slugs: set[str],
    baseline: str | None,
    changed_paths: list[str],
    specs_dirs: list[str] | None,
) -> set[str]:
    """Deletion of a tracked historical package is not a new delivery claim."""
    if not baseline or set(baseline) == {"0"}:
        return set()
    identities = specs_dirs or [".spec"]
    deleted = set()
    for slug in slugs:
        if _snapshot_package_exists(target, slug, specs_dirs):
            continue
        prefixes = tuple(f"{identity.rstrip('/')}/specs/{slug}/" for identity in identities) + tuple(
            f"{identity.rstrip('/')}/specs/archive/{slug}/" for identity in identities
        )
        if not any(path.startswith(prefixes) for path in changed_paths):
            continue
        for prefix in prefixes:
            result = gitx.git(root, "ls-tree", "-r", "--name-only", baseline, "--", prefix, check=False)
            if result.returncode == 0 and result.stdout.strip():
                deleted.add(slug)
                break
    return deleted


def _check_local_index(
    root: Path,
    target: Path,
    slugs: list[str],
    lint_only: bool,
    paths: list[str],
    specs_dirs: list[str],
) -> list[str]:
    """Use the on-disk pending archive during done's pre-commit hook.

    The delivery row is written only after the commit succeeds. Before it
    exists, require the final gate and verifies and bind them to both the
    current product tree and unchanged index, never an unrelated local archive.
    """
    selected = set(slugs)
    missing = {slug for slug in selected if not _snapshot_package_exists(target, slug, specs_dirs)}
    branch = gitx.branch(root)
    if not selected:
        for archive in (package.specs_root(root) / "archive").glob("*"):
            if not archive.is_dir():
                continue
            try:
                if package.extract_branch((archive / "spec.md").read_text(encoding="utf-8")) != branch:
                    continue
                if any(record.get("kind") == "delivery" for record in package.runs(archive)):
                    continue
                if package.latest_gate(archive).get("final") is True:
                    missing.add(archive.name)
            except (OSError, ValueError, AttributeError):
                continue
    errors = check_tree(
        target,
        slugs=sorted(selected - missing),
        lint_only=lint_only,
        changed_paths=paths,
        specs_dirs=specs_dirs,
    )
    if not missing or lint_only:
        return errors
    diff = gitx.git(root, "diff", "--name-only", "--", ".", ":(exclude).spec").stdout.splitlines()
    if diff:
        return [*errors, "local delivery index differs from the verified working tree"]
    fingerprint = package.tree_fingerprint(root)
    for slug in sorted(missing):
        if not SLUG.fullmatch(slug):
            errors.append(f"invalid package slug: {slug}")
            continue
        archive = package.specs_root(root) / "archive" / slug
        if not archive.is_dir():
            errors.append(f"explicit package slug not found: {slug}")
            continue
        archive_errors = _local_archive_errors(root, archive, slug, None, gitx.head(root), pending=True)
        errors.extend(archive_errors)
        if archive_errors:
            continue
        spec = (archive / "spec.md").read_text(encoding="utf-8")
        if package.extract_branch(spec) != branch:
            errors.append(f"{archive}: pending archive is bound to another branch")
        latest = package.latest_runs(archive)
        if any(record.get("tree_fp") != fingerprint for record in latest.values()):
            errors.append(f"{archive}: final evidence is stale for the current tree")
    return errors


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
    if args.footer_window_base and (not args.revision or (not args.slug and not local_evidence.is_local(root))):
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
            if local_evidence.is_local(root):
                selected = set(args.slug)
                footers = _window_footers(root, args.revision, args.footer_window_base or args.baseline)
                missing = {
                    slug: head
                    for slug, head in footers.items()
                    if not _snapshot_package_exists(target, slug, args.specs_dir)
                }
                deleted = _legacy_deleted_slugs(
                    root, target, selected - set(footers), args.baseline, paths, args.specs_dir
                )
                fallback = set(missing)
                if fallback or deleted:
                    target_slugs = sorted(selected - fallback - deleted)
                    errors = check_tree(
                        target,
                        slugs=target_slugs,
                        lint_only=args.lint_only,
                        changed_paths=paths,
                        specs_dirs=args.specs_dir,
                    )
                    errors.extend(
                        _check_local_archives(root, {slug: missing[slug] for slug in fallback}, args.revision)
                    )
                    target = None
        elif args.index:
            index_file = os.environ.get("GIT_INDEX_FILE")
            if index_file:
                environment = gitx.clean_env(index_file=Path(index_file))
            paths = changed_paths(root, None, None)
            with _checkout_tree(root, None, environment) as target:
                if local_evidence.is_local(root):
                    errors = _check_local_index(root, target, args.slug, args.lint_only, paths, args.specs_dir)
                else:
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
            if local_evidence.is_local(root):
                deleted = set(
                    gitx.git(
                        root, "diff", "--cached", "--name-only", "--diff-filter=D", "--no-renames"
                    ).stdout.splitlines()
                )
                paths = [path for path in paths if path not in deleted or not local_evidence.is_evidence_path(path)]
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

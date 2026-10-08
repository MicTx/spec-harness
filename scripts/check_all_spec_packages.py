#!/usr/bin/env python3
"""Validate every active Spec package in a repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import spec_package_support as _spec_support
from check_spec_package import overall_check_passed, package_physical_state_errors
from issue_closure_support import CLOSURE_HEADING, validate_completion_summary
from safe_open_support import SafeOpenError, open_regular_read
from spec_package_support import (
    extract_integration_branch,
    is_development_record_slug,
    read_regular_text,
    resolve_specs_root,
    scope_from_changed_paths,
)

MAX_SPEC_FILE_BYTES = _spec_support.MAX_SPEC_FILE_BYTES

ACTIVE_REQUIRED_FILES = ("spec.md", "tasks.md", "checklist.md")
ARCHIVE_REQUIRED_FILES = (*ACTIVE_REQUIRED_FILES, "completion-summary.md")
# Directories whose children are trusted as genuine Spec package roots.
# .spec is the default; .trae is the legacy name.  Other directories with
# a specs/ subdirectory are NOT automatically treated as Spec roots to avoid
# false positives (e.g. hardware/specs/draft-a).
TRUSTED_SPECS_PARENT_NAMES = {".spec", ".trae"}
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
TRUSTED_POSIX_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"


def _windows_safe_path() -> str:
    temp_roots: list[Path] = []
    for name in ("TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            temp_roots.append(Path(value).resolve(strict=False))
    kept: list[str] = []
    seen: set[str] = set()
    for value in os.environ.get("PATH", "").split(os.pathsep):
        if not value:
            continue
        candidate = Path(value).resolve(strict=False)
        lowered = str(candidate).lower()
        if lowered in seen or not candidate.is_dir():
            continue
        if any(candidate == root or root in candidate.parents for root in temp_roots):
            continue
        seen.add(lowered)
        kept.append(str(candidate))
    return os.pathsep.join(kept)


def clean_git_env(*, index_file: Path | None = None) -> dict[str, str]:
    blocked = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in blocked and not key.startswith("GIT_CONFIG_") and key != "SSH_ASKPASS"
    }
    if sys.platform == "win32":
        environment["PATH"] = _windows_safe_path()
    else:
        environment["PATH"] = TRUSTED_POSIX_PATH
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = str(index_file)
    return environment


# F21: git reads in this gate are bounded — a stalled repository
# operation fails inside the bound instead of hanging pre-commit.
SUBPROCESS_LOCAL_TIMEOUT_SECONDS = 60


def trusted_git() -> str:
    path = clean_git_env().get("PATH", "")
    candidate = shutil.which("git", path=path)
    if not candidate:
        raise ValueError("git is not available on the trusted PATH")
    return candidate


@dataclass(frozen=True)
class CheckFailure:
    specs_root: Path
    slug: str
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate all active Spec packages under a project root.")
    parser.add_argument(
        "--scope-paths-file",
        help="Internal: parse trusted Spec slugs from newline-delimited changed paths and exit",
    )
    parser.add_argument("--root", default=".", help="Project root (default: current directory)")
    parser.add_argument(
        "--specs-dir",
        action="append",
        default=[],
        help="Additional trusted Spec root relative to root; may be repeated",
    )
    parser.add_argument(
        "--slug",
        action="append",
        default=[],
        dest="slugs",
        help="Only validate the named package slug; may be repeated (default: all)",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="Validate the Git index snapshot instead of the working tree",
    )
    parser.add_argument(
        "--revision",
        help="Validate one Git revision/ref snapshot instead of the working tree",
    )
    parser.add_argument(
        "--legacy-baseline",
        help="Authoritative Git revision used only to recognize byte-identical legacy archives",
    )
    parser.add_argument(
        "--require-archived",
        action="store_true",
        help="Fail when any active package remains outside archive",
    )
    return parser.parse_args()


def discover_specs_roots(root: Path, explicit: list[str] | None = None) -> list[Path]:
    roots: set[Path] = set()
    for name in explicit or []:
        roots.add(resolve_specs_root(root, name).resolve(strict=False))

    resolved_root = root.resolve(strict=False)
    for name in (".spec", ".trae"):
        default = root / name
        if default.is_symlink():
            raise ValueError(f"default Spec root must not be a symlink: {default}")
        if (default / "specs").is_dir():
            resolved_default = default.resolve(strict=False)
            if resolved_default != resolved_root and resolved_root not in resolved_default.parents:
                raise ValueError(f"default Spec root escapes project root: {default}")
            roots.add(resolved_default)

    def on_error(error: OSError) -> None:
        raise error

    for current, directories, _ in os.walk(root, topdown=True, followlinks=False, onerror=on_error):
        current_path = Path(current)
        if current_path != root and (current_path / ".git").exists():
            directories[:] = []
            continue
        directories[:] = [
            name for name in directories if name not in SKIP_DIRECTORIES and not (current_path / name).is_symlink()
        ]
        if "specs" in directories:
            # Only auto-discover specs roots whose parent is a known
            # trusted container (.spec or .trae).  Other directories with
            # a specs/ subdirectory — e.g. hardware/specs/draft-a — are NOT
            # treated as Spec roots to avoid false positives blocking user
            # commits.  Custom locations must use --specs-dir explicitly.
            parent_name = current_path.name
            if parent_name in TRUSTED_SPECS_PARENT_NAMES:
                roots.add(current_path.resolve(strict=False))
            directories.remove("specs")
    return sorted(roots)


def package_dirs(specs_root: Path, *, archived: bool) -> list[Path]:
    packages_root = specs_root / "specs" / ("archive" if archived else "")
    if not packages_root.is_dir():
        return []
    result: list[Path] = []
    for child in sorted(packages_root.iterdir()):
        if not archived and child.name == "archive":
            continue
        if archived and child.name == "retired":
            # organize's retired container holds retired assets plus MANIFEST.md,
            # not a package bundle
            continue
        if child.name.startswith("."):
            continue
        if child.is_symlink():
            result.append(child)
            continue
        if child.is_dir():
            result.append(child)
    return result


def active_package_dirs(specs_root: Path) -> list[Path]:
    return package_dirs(specs_root, archived=False)


def archived_package_dirs(specs_root: Path) -> list[Path]:
    return package_dirs(specs_root, archived=True)


def _archive_bundle_sha256(contents: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for filename in ARCHIVE_REQUIRED_FILES:
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(contents[filename].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _archive_path_identity(
    path: str,
    explicit_roots: list[str] | None = None,
) -> tuple[str, str, str] | None:
    parts = Path(path).parts
    prefixes = {Path(name).parts for name in explicit_roots or []}
    for index, part in enumerate(parts):
        if part in {".spec", ".trae"}:
            prefixes.add(parts[: index + 1])
    for prefix in prefixes:
        length = len(prefix)
        if parts[:length] != prefix or parts[length : length + 2] != ("specs", "archive"):
            continue
        if len(parts) != length + 4 or parts[-1] not in ARCHIVE_REQUIRED_FILES:
            return None
        return Path(*prefix).as_posix(), parts[length + 2], parts[-1]
    return None


def _normalized_archive_text(data: bytes) -> str:
    """Decode archive bytes with the SAME strict pipeline on both sides.

    The baseline side used to decode ``git show`` output through the process
    locale (``text=True``), so a non-UTF-8 locale (e.g. Windows GBK) garbled or
    crashed on Chinese content while the worktree side decoded strict UTF-8 —
    producing false "not byte-identical" failures. Both sides now go through
    this single strict-UTF-8 + universal-newline pipeline, so the comparison
    is locale-independent while keeping the intentional CRLF tolerance.
    """
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _read_archive_file_bytes(path: Path) -> bytes:
    """Raw bytes of one archive file with the same safety guards as text reads."""
    try:
        with open_regular_read(path, max_bytes=MAX_SPEC_FILE_BYTES) as descriptor:
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                return handle.read(MAX_SPEC_FILE_BYTES + 1)
    except SafeOpenError as exc:
        raise ValueError(str(exc)) from exc


_LEGACY_CACHE_KEY_SEP = "\x1f"
_LEGACY_CACHE_DIR = "spec-cache"


def _legacy_cache_path(root: Path, cache_key: str) -> Path:
    return root / ".git" / _LEGACY_CACHE_DIR / f"legacy-archive-hashes-{cache_key}.json"


def _load_legacy_cache(root: Path, cache_key: str) -> dict[tuple[str, str], str] | None:
    """Best-effort read of the immutable-archive hash cache; None on any miss."""
    try:
        data = json.loads(_legacy_cache_path(root, cache_key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("hashes"), dict):
        return None
    hashes: dict[tuple[str, str], str] = {}
    for key, value in data["hashes"].items():
        parts = key.split(_LEGACY_CACHE_KEY_SEP)
        if len(parts) == 2 and isinstance(value, str):
            hashes[(parts[0], parts[1])] = value
    return hashes or None


def _store_legacy_cache(root: Path, cache_key: str, hashes: dict[tuple[str, str], str]) -> None:
    """Best-effort cache write; failures never break the gate."""
    path = _legacy_cache_path(root, cache_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"hashes": {f"{a}{_LEGACY_CACHE_KEY_SEP}{b}": v for (a, b), v in hashes.items()}},
            ensure_ascii=False,
            sort_keys=True,
        )
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def legacy_archive_hashes(
    root: Path,
    baseline_revision: str | None = None,
    specs_dirs: list[str] | None = None,
) -> dict[tuple[str, str], str]:
    """Return immutable legacy archive hashes from an authoritative baseline."""
    git_bin = trusted_git()
    if not (root / ".git").exists():
        return {}
    baseline = None
    candidates = (
        (baseline_revision,)
        if baseline_revision
        else (
            "refs/remotes/origin/main",
            "refs/remotes/origin/master",
        )
    )
    for candidate in candidates:
        if not candidate or set(candidate) == {"0"}:
            continue
        checked = subprocess.run(
            [git_bin, "rev-parse", "--verify", candidate],
            cwd=root,
            env=clean_git_env(),
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_LOCAL_TIMEOUT_SECONDS,
        )
        if checked.returncode == 0:
            baseline = checked.stdout.strip()
            break
    if baseline is None:
        return {}
    # The baseline sha pins immutable archive content, so (baseline, specs_dirs)
    # is a sound cache key: same key always means same blobs in git.
    cache_key = hashlib.sha256(
        f"{baseline}{_LEGACY_CACHE_KEY_SEP}{json.dumps(specs_dirs or [], ensure_ascii=False)}".encode("utf-8")
    ).hexdigest()[:16]
    cached = _load_legacy_cache(root, cache_key)
    if cached is not None:
        return cached
    try:
        listed = subprocess.run(
            [git_bin, "ls-tree", "-r", "--name-only", baseline],
            cwd=root,
            env=clean_git_env(),
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_LOCAL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {}
    if listed.returncode != 0:
        return {}
    bundles: dict[tuple[str, str], dict[str, str]] = {}
    for path in listed.stdout.splitlines():
        identity = _archive_path_identity(path, specs_dirs)
        if identity is None:
            continue
        specs_name, slug, filename = identity
        shown = subprocess.run(
            [git_bin, "show", f"{baseline}:{path}"],
            cwd=root,
            env=clean_git_env(),
            capture_output=True,
        )
        if shown.returncode == 0:
            bundles.setdefault((specs_name, slug), {})[filename] = _normalized_archive_text(shown.stdout)
    hashes: dict[tuple[str, str], str] = {}
    for identity, bundle in bundles.items():
        if set(bundle) != set(ARCHIVE_REQUIRED_FILES):
            continue
        digest = hashlib.sha256()
        for filename in ARCHIVE_REQUIRED_FILES:
            digest.update(filename.encode("utf-8"))
            digest.update(b"\0")
            digest.update(bundle[filename].encode("utf-8"))
            digest.update(b"\0")
        hashes[identity] = digest.hexdigest()
    _store_legacy_cache(root, cache_key, hashes)
    return hashes


def archive_baseline_equivalence(
    specs_root: Path,
    specs_identity: str,
    legacy_hashes: dict[tuple[str, str], str],
) -> Callable[[str], bool]:
    """Build a slug predicate: is this archive byte-identical to the baseline?

    Single source of the baseline-equivalence ruling: callers (package loop,
    follow-up graph, done-time closure validation) must reuse this instead of
    growing parallel re-grade policies. Reads go through the same raw-byte +
    strict-UTF-8 pipeline the baseline side uses, so identity never depends on
    the process locale.
    """

    def equivalent(slug: str) -> bool:
        expected = legacy_hashes.get((specs_identity, slug))
        if expected is None:
            return False
        package = specs_root / "specs" / "archive" / slug
        try:
            texts = {
                filename: _normalized_archive_text(_read_archive_file_bytes(package / filename))
                for filename in ARCHIVE_REQUIRED_FILES
            }
        except (OSError, UnicodeError, ValueError):
            return False
        return _archive_bundle_sha256(texts) == expected

    return equivalent


def _validate_package(
    root: Path,
    specs_root: Path,
    package: Path,
    *,
    archived: bool,
    legacy_hashes: dict[tuple[str, str], str],
    specs_identity: str,
) -> list[CheckFailure]:
    slug = package.name
    failures: list[CheckFailure] = []
    if package.is_symlink() or not package.is_dir():
        return [CheckFailure(specs_root, slug, "package must be a non-symlink directory")]
    if not is_development_record_slug(slug):
        return [CheckFailure(specs_root, slug, "invalid Development Record slug")]
    filenames = ARCHIVE_REQUIRED_FILES if archived else ACTIVE_REQUIRED_FILES
    contents: dict[str, str] = {}
    try:
        for filename in filenames:
            contents[filename] = read_regular_text(package / filename)
        # Hash input goes through the same raw-byte pipeline the baseline side
        # uses, so archive identity never depends on the process locale.
        archive_texts = (
            {filename: _normalized_archive_text(_read_archive_file_bytes(package / filename)) for filename in filenames}
            if archived
            else None
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return [CheckFailure(specs_root, slug, str(exc))]
    # Read-only legacy compatibility (storage-and-archive contract): an
    # archive whose four-file bundle is byte-identical to the authoritative
    # baseline was graded under the gates of its day and is never
    # retrospectively re-graded. Any byte change (hash mismatch) or a missing
    # baseline immediately restores full current-gate validation; structural
    # checks and the v1 closure validation below always stay in force.
    legacy_compatible = (
        archived
        and archive_texts is not None
        and legacy_hashes.get((specs_identity, slug)) == _archive_bundle_sha256(archive_texts)
    )
    if not archived:
        failures.extend(CheckFailure(specs_root, slug, error) for error in package_physical_state_errors(package))
    # Evidence freshness is an active-package gate. Archived records retain
    # the evidence captured at their closeout; re-grading them against every
    # later HEAD would make a valid archive fail merely because new work landed.
    check_root = root if not archived else None
    if not legacy_compatible and not overall_check_passed(
        contents["spec.md"],
        contents["tasks.md"],
        contents["checklist.md"],
        slug=slug,
        root=check_root,
    ):
        failures.append(CheckFailure(specs_root, slug, "check gates not passed"))
    if archived:
        summary = contents["completion-summary.md"]
        if not summary.strip():
            failures.append(CheckFailure(specs_root, slug, "completion summary is empty"))
        elif CLOSURE_HEADING in summary:
            closure_failures = validate_completion_summary(
                summary,
                specs_root=specs_root,
                current_slug=slug,
                current_tasks_content=contents["tasks.md"],
                require_v1=True,
                baseline_equivalent=archive_baseline_equivalence(specs_root, specs_identity, legacy_hashes),
            )
            failures.extend(CheckFailure(specs_root, slug, failure) for failure in closure_failures)
        elif legacy_hashes.get((specs_identity, slug)) != _archive_bundle_sha256(archive_texts or contents):
            failures.append(
                CheckFailure(
                    specs_root,
                    slug,
                    "archive lacks v1 issue closure and is not byte-identical to a baseline legacy summary",
                )
            )
    return failures


def check_all_packages(
    root: Path,
    explicit: list[str] | None = None,
    *,
    require_archived: bool = False,
    slugs: list[str] | None = None,
    legacy_hashes: dict[tuple[str, str], str] | None = None,
) -> list[CheckFailure]:
    failures: list[CheckFailure] = []
    try:
        specs_roots = discover_specs_roots(root, explicit)
    except (OSError, ValueError) as exc:
        return [CheckFailure(root, "<discovery>", str(exc))]

    slug_filter = set(slugs) if slugs else None
    found_slugs: set[str] = set()
    legacy_hashes = legacy_hashes or {}

    for specs_root in specs_roots:
        try:
            specs_identity = specs_root.relative_to(root.resolve(strict=False)).as_posix()
        except ValueError:
            failures.append(CheckFailure(specs_root, "<identity>", "Spec root is outside project root"))
            continue
        try:
            active = active_package_dirs(specs_root)
            archived = archived_package_dirs(specs_root)
        except OSError as exc:
            failures.append(CheckFailure(specs_root, "<enumeration>", str(exc)))
            continue

        active_by_slug = {package.name: package for package in active}
        archived_by_slug = {package.name: package for package in archived}
        branch_owners: dict[str, list[str]] = {}
        for package in active:
            try:
                branch = extract_integration_branch(read_regular_text(package / "spec.md"))
            except (OSError, UnicodeError, ValueError):
                branch = None
            if branch:
                branch_owners.setdefault(branch, []).append(package.name)
        for branch, owners in sorted(branch_owners.items()):
            if len(owners) > 1:
                failures.append(
                    CheckFailure(
                        specs_root,
                        ",".join(owners),
                        f"integration branch bound to multiple active packages: {branch}",
                    )
                )
        duplicate_slugs = set(active_by_slug).intersection(archived_by_slug)
        failures.extend(
            CheckFailure(specs_root, slug, "package exists in both active and archive directories")
            for slug in sorted(duplicate_slugs)
        )

        if slug_filter is not None:
            active = [package for package in active if package.name in slug_filter]
            archived = [package for package in archived if package.name in slug_filter]
        found_slugs.update(package.name for package in (*active, *archived))

        for package in active:
            failures.extend(
                _validate_package(
                    root,
                    specs_root,
                    package,
                    archived=False,
                    legacy_hashes=legacy_hashes,
                    specs_identity=specs_identity,
                )
            )
            if require_archived:
                failures.append(CheckFailure(specs_root, package.name, "active package must be archived"))
        for package in archived:
            failures.extend(
                _validate_package(
                    root,
                    specs_root,
                    package,
                    archived=True,
                    legacy_hashes=legacy_hashes,
                    specs_identity=specs_identity,
                )
            )

    if slug_filter is not None:
        for missing in sorted(slug_filter - found_slugs):
            failures.append(CheckFailure(root, missing, "explicit package slug not found in active or archive"))
    return failures


def check_git_index(
    root: Path,
    explicit: list[str] | None = None,
    *,
    slugs: list[str] | None = None,
    legacy_baseline: str | None = None,
) -> list[CheckFailure]:
    with tempfile.TemporaryDirectory(prefix="spec-index-") as temp_dir:
        snapshot = Path(temp_dir)
        try:
            completed = subprocess.run(
                [trusted_git(), "checkout-index", "--all", f"--prefix={snapshot}{os.sep}"],
                cwd=root,
                env=clean_git_env(),
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_LOCAL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return [CheckFailure(root, "<git-index>", "checkout-index timed out within 60s")]
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            return [CheckFailure(root, "<git-index>", detail or "checkout-index failed")]
        return check_all_packages(
            snapshot,
            explicit,
            slugs=slugs,
            legacy_hashes=legacy_archive_hashes(root, legacy_baseline, explicit),
        )


@contextmanager
def git_revision_snapshot(root: Path, revision: str):
    git_bin = trusted_git()
    try:
        resolved = subprocess.run(
            [git_bin, "rev-parse", "--verify", f"{revision}^{{tree}}"],
            cwd=root,
            env=clean_git_env(),
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_LOCAL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"git rev-parse timed out within {SUBPROCESS_LOCAL_TIMEOUT_SECONDS}s") from exc
    if resolved.returncode != 0:
        detail = resolved.stderr.strip() or resolved.stdout.strip()
        raise ValueError(detail or f"invalid revision: {revision}")
    tree_sha = resolved.stdout.strip()
    with tempfile.TemporaryDirectory(prefix="spec-revision-") as temp_dir:
        temporary = Path(temp_dir)
        snapshot = temporary / "snapshot"
        snapshot.mkdir()
        environment = clean_git_env(index_file=temporary / "index")
        try:
            read_tree = subprocess.run(
                [git_bin, "read-tree", tree_sha],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_LOCAL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise ValueError(f"git read-tree timed out within {SUBPROCESS_LOCAL_TIMEOUT_SECONDS}s")
        if read_tree.returncode != 0:
            detail = read_tree.stderr.strip() or read_tree.stdout.strip()
            raise ValueError(detail or f"cannot read revision: {revision}")
        checkout = subprocess.run(
            [git_bin, "checkout-index", "--all", f"--prefix={snapshot}{os.sep}"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
        )
        if checkout.returncode != 0:
            detail = checkout.stderr.strip() or checkout.stdout.strip()
            raise ValueError(detail or f"cannot export revision: {revision}")
        yield snapshot


def check_git_revision(
    root: Path,
    revision: str,
    explicit: list[str] | None = None,
    *,
    require_archived: bool = False,
    slugs: list[str] | None = None,
    legacy_baseline: str | None = None,
) -> list[CheckFailure]:
    try:
        with git_revision_snapshot(root, revision) as snapshot:
            return check_all_packages(
                snapshot,
                explicit,
                require_archived=require_archived,
                slugs=slugs,
                legacy_hashes=legacy_archive_hashes(root, legacy_baseline, explicit),
            )
    except (OSError, ValueError) as exc:
        return [CheckFailure(root, "<git-revision>", str(exc))]


def render_failures(root: Path, failures: list[CheckFailure]) -> str:
    lines = ["# Spec Disk Truth", "", f"- root: {root}", "- result: blocked", "", "## Failures"]
    lines.extend(f"- {failure.specs_root}:{failure.slug}: {failure.reason}" for failure in failures)
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve(strict=False)
    if args.scope_paths_file:
        try:
            content = read_regular_text(Path(args.scope_paths_file))
            slugs = scope_from_changed_paths(content.splitlines(), args.specs_dir)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"error: cannot parse changed-path scope: {exc}", file=sys.stderr)
            return 1
        for slug in slugs:
            print(f"SPEC {slug}")
        return 0
    slug_filter = args.slugs or None
    if args.index and args.revision:
        print("error: --index and --revision are mutually exclusive", file=sys.stderr)
        return 1
    if args.index:
        if args.require_archived:
            print(
                "error: --require-archived is not supported with --index; "
                "the index snapshot has no archive lifecycle state",
                file=sys.stderr,
            )
            return 1
        failures = check_git_index(
            root,
            args.specs_dir,
            slugs=slug_filter,
            legacy_baseline=args.legacy_baseline,
        )
    elif args.revision:
        failures = check_git_revision(
            root,
            args.revision,
            args.specs_dir,
            require_archived=args.require_archived,
            slugs=slug_filter,
            legacy_baseline=args.legacy_baseline,
        )
    else:
        failures = check_all_packages(
            root,
            args.specs_dir,
            require_archived=args.require_archived,
            slugs=slug_filter,
            legacy_hashes=legacy_archive_hashes(root, specs_dirs=args.specs_dir),
        )
    if failures:
        print(render_failures(root, failures))
        return 1
    print("# Spec Disk Truth\n\n- result: passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

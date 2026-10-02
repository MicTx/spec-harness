#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""
Build versioned distributable artifacts for the spec package.

Produces in an output directory outside the source tree (default ``../spec-harness-dist/``):
  spec-harness-{version}.tar.gz  full runtime package (Unix, preserves permissions)
  spec-harness-{version}.zip     full runtime package (Windows)
  SHA256SUMS                    SHA-256 checksums for every artifact
  RELEASE_NOTES.md              Changelog excerpt for the matching version

Usage:
  python3 scripts/build_release.py
  python3 scripts/build_release.py --skip-checks
  python3 scripts/build_release.py --output /tmp/dist --expect-version 0.3.0
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from path_safety import UnsafePathError, ensure_output_outside_root

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
PYTHON = sys.executable
PROJECT_NAME = "spec-harness"
# Keep the default artifact directory outside the source tree: ``--force``
# must never be able to delete checked-out source files.
DEFAULT_DIST = REPO_ROOT.parent / f"{PROJECT_NAME}-dist"

VERSION_RE = re.compile(r'^version\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


class BuildError(Exception):
    """Raised when the build cannot proceed."""


# --------------------------------------------------------------------------- #
# Version
# --------------------------------------------------------------------------- #


def read_version(root: Path) -> str:
    """Read the project version from pyproject.toml."""
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        raise BuildError(f"pyproject.toml not found at {pyproject}")
    content = pyproject.read_text(encoding="utf-8")
    match = VERSION_RE.search(content)
    if not match:
        raise BuildError("could not find version= in [project] section of pyproject.toml")
    version = match.group(1)
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        raise BuildError(f"version does not look like semver (X.Y.Z): {version}")
    return version


# --------------------------------------------------------------------------- #
# Pre-build checks
# --------------------------------------------------------------------------- #


def _run_cmd(args: list[str], cwd: Path, label: str, *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"{label} timed out after {timeout}s") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BuildError(f"{label} failed (exit {result.returncode}):\n{detail}")
    return result


def run_prebuild_checks(root: Path) -> None:
    """Validate the tree before packaging: compile, smoke, pytest, ruff, ruff-format, export."""
    compile_roots = ("scripts", "server", "hooks", "slots", "tests", "book")

    print("  compileall ...", end=" ", flush=True)
    _run_cmd([PYTHON, "-m", "compileall", "-q", *compile_roots], root, "compileall")
    print("ok")

    print("  smoke_test ...", end=" ", flush=True)
    _run_cmd([PYTHON, str(SCRIPTS_DIR / "smoke_test_spec_skill.py")], root, "smoke_test")
    print("ok")

    print("  slot registry ...", end=" ", flush=True)
    _run_cmd(
        [PYTHON, str(SCRIPTS_DIR / "slot_registry.py"), "validate", "--root", str(root)],
        root,
        "slot_registry",
    )
    print("ok")

    print("  pytest ......", end=" ", flush=True)
    _run_cmd([PYTHON, "-m", "pytest", "tests", "slots", "-q"], root, "pytest")
    print("ok")

    print("  ruff ........", end=" ", flush=True)
    # Probe ruff availability via --version so a genuine lint failure
    # (ruff installed but returns violations) is not silently swallowed.
    probe = subprocess.run(
        [PYTHON, "-m", "ruff", "--version"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode != 0:
        print("skipped (ruff not installed)")
    else:
        _run_cmd(
            [PYTHON, "-m", "ruff", "check", "scripts/", "server/", "hooks/", "slots/", "tests/", "book/"],
            root,
            "ruff",
        )
        print("ok")

    print("  ruff format --check ....", end=" ", flush=True)
    if probe.returncode != 0:
        print("skipped (ruff not installed)")
    else:
        _run_cmd(
            [
                PYTHON,
                "-m",
                "ruff",
                "format",
                "--check",
                "scripts/",
                "server/",
                "hooks/",
                "slots/",
                "tests/",
                "book/",
            ],
            root,
            "ruff format",
        )
        print("ok")

    print("  pi extension tests ...", end=" ", flush=True)
    # Every extension test file ships in the release; verify all of them so a
    # newly added suite cannot silently skip verification.
    extension_tests = sorted((root / "pi-extension").glob("*.test.ts"))
    tsx = Path.home() / ".pi" / "agent" / "npm" / "node_modules" / ".bin" / "tsx"
    if extension_tests and tsx.is_file():
        _run_cmd([str(tsx), "--test", *map(str, extension_tests)], root, "pi extension tests")
        print("ok")
    elif extension_tests:
        print("skipped (tsx not installed; Pi loads TypeScript directly)")
    else:
        print("skipped (no Pi extension tests)")

    print("  installer syntax ...", end=" ", flush=True)
    bash = shutil.which("bash")
    if bash:
        _run_cmd([bash, "-n", str(root / "server" / "install.sh")], root, "bash -n")
        print("ok")
    else:
        print("skipped (bash not installed)")

    print("  export ......", end=" ", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        _run_cmd(
            [
                PYTHON,
                str(SCRIPTS_DIR / "export_skill_package.py"),
                "--root",
                str(root),
                "--output",
                tmp,
                "--force",
                "--skip-signing",
            ],
            root,
            "export_skill_package",
        )
    print("ok")


# --------------------------------------------------------------------------- #
# Export + stamp
# --------------------------------------------------------------------------- #


def export_runtime(root: Path, dest: Path, *, skip_signing: bool = False) -> None:
    """Export the clean runtime package using export_skill_package.py."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    command = [
        PYTHON,
        str(SCRIPTS_DIR / "export_skill_package.py"),
        "--root",
        str(root),
        "--output",
        str(dest),
        "--force",
    ]
    if skip_signing:
        command.append("--skip-signing")
    _run_cmd(command, root, "export_skill_package")


def compute_tree_sha256(root: Path, *, excluded: set[str] | None = None) -> str:
    """Hash relative paths, executable bits, and contents for one runtime tree."""
    ignored = excluded or set()
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name not in ignored):
        relative = path.relative_to(root).as_posix()
        executable = "x" if path.stat().st_mode & 0o111 else "-"
        digest.update(f"{relative}\0{executable}\0".encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def stamp_build_info(runtime_dir: Path, version: str, root: Path | None = None) -> None:
    """Write VERSION and BUILD_INFO into the exported runtime tree.

    When *root* is given, the Git SHA is read from that repository; otherwise
    it falls back to REPO_ROOT (the directory containing this script).
    """
    repo = root if root is not None else REPO_ROOT
    # Purge any __pycache__ / *.pyc that may have leaked through copytree.
    for pyc in runtime_dir.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    for pyc in runtime_dir.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)

    # git_sha and build_time are commit-derived only. The working tree's
    # dirty state is deliberately NOT recorded: release/ tracks the build
    # outputs, so a dirty-vs-clean stamp would make the archive bytes depend
    # on their own presence in the tree — an unbreakable parity loop.
    git_sha = "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            git_sha = result.stdout.strip()
    except FileNotFoundError:
        pass

    # Deterministic stamp: derive build_time from the last commit, not the
    # wall clock — identical source states must produce byte-identical
    # archives (the tracked release/ payload check depends on it). Outside
    # a git repo, fall back to the epoch constant.
    build_time = "1970-01-01T00:00:00Z"
    try:
        stamped = subprocess.run(
            ["git", "log", "-1", "--format=%cI"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if stamped.returncode == 0 and stamped.stdout.strip():
            build_time = datetime.fromisoformat(stamped.stdout.strip()).strftime("%Y-%m-%dT%H:%M:%SZ")
    except FileNotFoundError:
        pass

    (runtime_dir / "VERSION").write_text(f"{version}\n", encoding="utf-8")

    runtime_sha256 = compute_tree_sha256(runtime_dir, excluded={"VERSION", "BUILD_INFO"})
    build_info = (
        f"version={version}\n"
        f"git_sha={git_sha}\n"
        f"runtime_sha256={runtime_sha256}\n"
        f"build_time={build_time}\n"
        f"built_by={Path(__file__).name}\n"
    )
    (runtime_dir / "BUILD_INFO").write_text(build_info, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Archive creation
# --------------------------------------------------------------------------- #


def create_tarball(src_dir: Path, archive_name: str, output: Path) -> None:
    """Create a .tar.gz with src_dir contents under archive_name/.

    Deterministic: every entry (files and dirs) carries a fixed mtime, and
    the gzip container itself is written with mtime=0 via a streamed
    GzipFile, so identical inputs produce byte-identical archives — the
    tracked release/ payload check depends on this
    (see tests/test_release_payload.py).
    """
    fixed_mtime = 0
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for item in sorted(src_dir.rglob("*")):
                    rel = item.relative_to(src_dir)
                    info = tar.gettarinfo(item, arcname=f"{archive_name}/{rel}")
                    info.mtime = fixed_mtime
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    if item.is_file():
                        with open(item, "rb") as fh:
                            tar.addfile(info, fh)
                    else:
                        tar.addfile(info)


def create_zip(src_dir: Path, archive_name: str, output: Path) -> None:
    """Create a .zip with src_dir contents under archive_name/.

    ZipInfo.from_file() stores the Unix mode bits in external_attr, so
    executable permissions survive for tools that honour them (info-zip,
    Python's zipfile on readback). Deterministic: fixed date_time so
    identical inputs produce byte-identical archives.
    """
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(src_dir.rglob("*")):
            if item.is_file():
                rel = item.relative_to(src_dir)
                info = zipfile.ZipInfo(f"{archive_name}/{rel}", date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = (item.stat().st_mode & 0o777) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, item.read_bytes())


# --------------------------------------------------------------------------- #
# Checksums
# --------------------------------------------------------------------------- #


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksums(dist: Path) -> Path:
    """Write SHA256SUMS for every artifact file in the selected output directory."""
    sums_file = dist / "SHA256SUMS"
    artifacts = sorted(p for p in dist.iterdir() if p.is_file() and p.name not in ("SHA256SUMS", "RELEASE_NOTES.md"))
    lines = [f"{compute_sha256(p)}  {p.name}" for p in artifacts]
    sums_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sums_file


# --------------------------------------------------------------------------- #
# Release notes
# --------------------------------------------------------------------------- #


def extract_release_notes(root: Path, version: str) -> str:
    """Extract the changelog section for *version*.

    Handles both ``## [0.2.0]`` and ``## [0.2.0] - date`` heading styles.
    Returns the full text of that section (heading + body) or a fallback
    notice if the section is not found.
    """
    changelog = root / "CHANGELOG.md"
    if not changelog.exists():
        return f"Release {version}\n"

    text = changelog.read_text(encoding="utf-8")
    # Match the version heading and capture until the next ## heading or EOF.
    pattern = re.compile(
        rf"(##\s*\[{re.escape(version)}\][^\n]*\n)(.*?)(?=\n##\s|\Z)",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match:
        return match.group(0).strip() + "\n"
    return f"Release {version}\n"


RELEASE_NOTES_EDITOR_PROMPT = (
    "You are a release-notes editor for the spec-harness project. "
    "Input is a draft changelog section in markdown. Rewrite it strictly user-facing in "
    "itemized style (contract: references/changelog-guide.md 'Release notes style'): "
    "keep the version heading and the Added/Changed/Fixed headings; under each heading one "
    "bullet per capability, each bullet formatted as **bold capability name** followed by a "
    "colon and one sentence of mechanism plus its user-facing guarantee, at most two "
    "sentences. Write plain, accessible language; a fitting everyday analogy is welcome. "
    "No internal codenames: no task slugs, finding IDs, or internal script names. State only "
    "shipped behavior: no promises, plans, or unverified claims. Preserve factual claims "
    "exactly. Output markdown only."
)


def polish_release_notes(draft: str, timeout: int = 120) -> str:
    """Condense the draft through a pi agent; fail open to the raw excerpt.

    The agent is an editorial pass, not a build dependency: any failure
    (pi missing, provider unready, timeout, garbage output) returns the
    draft unchanged so the build never blocks on it.
    """
    if not draft.strip():
        return draft
    try:
        result = subprocess.run(
            [
                "pi",
                "--provider",
                "anthropic",
                "-p",
                "--no-session",
                "--system-prompt",
                RELEASE_NOTES_EDITOR_PROMPT,
                draft,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return draft
    polished = result.stdout.strip()
    if result.returncode != 0 or not polished or "###" not in polished:
        return draft
    return polished + "\n"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def build(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        dist = ensure_output_outside_root(root, Path(args.output), label="release output")
    except UnsafePathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    version = read_version(root)

    print(f"Building {PROJECT_NAME} v{version}")

    if args.expect_version and version != args.expect_version:
        print(
            f"error: version mismatch — pyproject.toml has {version}, expected {args.expect_version}",
            file=sys.stderr,
        )
        return 1

    if not args.skip_checks:
        print("Pre-build checks:")
        run_prebuild_checks(root)
    else:
        print("Skipping pre-build checks")

    # Fresh output directory (already proven disjoint from the source tree).
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)

    archive_name = f"{PROJECT_NAME}-{version}"

    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp) / archive_name

        print("Exporting runtime package ...")
        export_runtime(root, runtime_dir, skip_signing=args.skip_signing)

        print("Stamping build info ...")
        stamp_build_info(runtime_dir, version, root)

        tarball = dist / f"{archive_name}.tar.gz"
        zip_file = dist / f"{archive_name}.zip"

        print(f"Creating {tarball.name} ...")
        create_tarball(runtime_dir, archive_name, tarball)

        print(f"Creating {zip_file.name} ...")
        create_zip(runtime_dir, archive_name, zip_file)

    print("Generating checksums ...")
    write_checksums(dist)

    print("Extracting release notes ...")
    notes = extract_release_notes(root, version)
    if not getattr(args, "no_ai_notes", False):
        notes = polish_release_notes(notes)
    (dist / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")

    # Summary
    print("\nBuild complete:")
    for f in sorted(dist.iterdir()):
        if f.is_file():
            size = f.stat().st_size
            if size > 1024:
                print(f"  {f.name:42s} {size / 1024:>8.1f} KiB")
            else:
                print(f"  {f.name:42s} {size:>8}   B")
    print(f"\nArtifacts in: {dist}")
    return 0


def parse_args() -> argparse.Namespace:
    try:
        default_output_label = str(DEFAULT_DIST.relative_to(REPO_ROOT))
    except ValueError:
        default_output_label = str(DEFAULT_DIST)
    parser = argparse.ArgumentParser(
        description="Build versioned distributable artifacts for the spec package.",
    )
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="Repository root (default: auto-detected from script location)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=str(DEFAULT_DIST),
        help=f"Output directory (default: {default_output_label})",
    )
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip pre-build validation (compile, smoke, pytest, ruff)",
    )
    parser.add_argument(
        "--no-ai-notes",
        action="store_true",
        help="Skip the pi agent editorial pass for RELEASE_NOTES.md (raw changelog excerpt is used as-is)",
    )
    parser.add_argument(
        "--skip-signing",
        action="store_true",
        help="Build unsigned. Tests and keyless checkouts must opt in; release paths must not.",
    )
    parser.add_argument(
        "--expect-version",
        metavar="VERSION",
        help="Fail if the version in pyproject.toml does not match this value",
    )
    return parser.parse_args()


def main() -> int:
    try:
        return build(parse_args())
    except BuildError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

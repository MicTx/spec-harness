#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build a filtered public source tree for Spec Harness.

The private development repository remains the source of truth. This command
copies only the public source allowlist, emits a digest manifest, and scans
the result before any optional GitHub publish step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PUBLIC_ROOT_FILES = (
    ".gitignore",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README-en.md",
    "README.md",
    "RELEASE.md",
    "SECURITY.md",
    "SKILL.md",
    "SUPPORT.md",
    "install.sh",
    "pyproject.toml",
)
PUBLIC_DIRS = (
    "agent-plugin",
    "agents",
    "docs",
    "hooks",
    "references",
    "scripts",
    "server",
    "slots",
    "tests",
)
PUBLIC_GITHUB_FILES = (
    ".github/workflows/ci-public.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/CODEOWNERS",
    ".github/PULL_REQUEST_TEMPLATE.md",
)
PRIVATE_PATH_PARTS = {".agents", ".maintainer", ".spec", ".zcode", "release", ".github"}
PRIVATE_TEXT_PATTERNS = (
    re.compile(r"git\.mxk\.dev", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?:AKIA|ghp_|github_pat_|xox[baprs]-)[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-[0-9A-Za-z]{20,}"),
    re.compile(r"/(?:Users|private|home)/[A-Za-z0-9_.-]+/"),
)


class PublicExportError(ValueError):
    pass


def _safe_files(source: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for name in PUBLIC_ROOT_FILES:
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise PublicExportError(f"required public file missing or symlinked: {name}")
        files.append((path, name))
    for dirname in PUBLIC_DIRS:
        base = source / dirname
        if not base.is_dir() or base.is_symlink():
            raise PublicExportError(f"required public directory missing or symlinked: {dirname}")
        for path in sorted(base.rglob("*")):
            if path.is_dir():
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"} or path.name == ".DS_Store":
                continue
            if path.is_symlink() or not path.is_file():
                raise PublicExportError(f"public tree contains unsafe entry: {path}")
            files.append((path, path.relative_to(source).as_posix()))
    for relative in PUBLIC_GITHUB_FILES:
        path = source / relative
        if not path.is_file() or path.is_symlink():
            raise PublicExportError(f"required public file missing or symlinked: {relative}")
        files.append((path, relative))
    return sorted(files, key=lambda pair: pair[1])


def _scan(path: Path, data: bytes) -> None:
    text = data.decode("utf-8", "replace")
    for pattern in PRIVATE_TEXT_PATTERNS:
        if pattern.search(text):
            raise PublicExportError(f"public payload contains forbidden pattern {pattern.pattern!r}: {path}")


def build_public_tree(source: Path, output: Path) -> dict[str, str]:
    source = source.resolve()
    output = output.resolve()
    if output == source or source in output.parents:
        raise PublicExportError("public output must be outside the private source tree")
    if output.exists():
        if output.is_symlink():
            raise PublicExportError("public output must not be a symlink")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    manifest: dict[str, str] = {}
    for path, relative in _safe_files(source):
        data = path.read_bytes()
        _scan(relative, data)
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        # write_bytes creates 0644 regardless of the source mode; keep the
        # executable bit so the exported tree can be re-packed faithfully.
        if path.stat().st_mode & 0o111:
            target.chmod(target.stat().st_mode | 0o111)
        manifest[relative] = hashlib.sha256(data).hexdigest()
    manifest_path = output / "PUBLIC_MANIFEST.json"
    manifest_path.write_text(
        json.dumps({"format": 1, "project": "spec-harness", "files": manifest}, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _run(args: list[str], cwd: Path, timeout: int = 180) -> None:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise PublicExportError(f"command timed out: {' '.join(args)}") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PublicExportError(f"command failed: {' '.join(args)}\n{detail}")


def publish_public_tree(output: Path, repo: str, tag: str | None) -> None:
    """Create or update a GitHub repository only when the caller passes --publish."""
    gh = shutil.which("gh")
    if not gh:
        raise PublicExportError("gh CLI is required for --publish")
    with tempfile.TemporaryDirectory(prefix="spec-harness-public-git-") as temp:
        work = Path(temp) / "repo"
        try:
            view = subprocess.run(
                [gh, "repo", "view", repo, "--json", "name"], capture_output=True, text=True, timeout=60
            )
        except subprocess.TimeoutExpired as exc:
            raise PublicExportError("GitHub repository lookup timed out") from exc
        if view.returncode != 0 and "not found" not in (view.stderr or "").lower():
            raise PublicExportError(f"GitHub repository lookup failed: {(view.stderr or view.stdout).strip()}")
        if view.returncode == 0:
            _run(["git", "clone", f"https://github.com/{repo}.git", str(work)], Path(temp))
        else:
            work.mkdir()
            _run(["git", "init", "-q", "-b", "main"], work)
            _run(["git", "config", "user.name", "Spec Harness Publisher"], work)
            _run(["git", "config", "user.email", "spec-harness-publisher@users.noreply.github.com"], work)
        for child in list(work.iterdir()):
            if child.name != ".git":
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        for path in output.iterdir():
            target = work / path.name
            shutil.copytree(path, target) if path.is_dir() else shutil.copy2(path, target)
        # The allowlist has already rejected unsafe entries. Force-add the
        # filtered tree so repository-level ignore rules cannot create a
        # manifest/clone mismatch caused by repository ignore rules.
        _run(["git", "add", "-A", "-f"], work)
        status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=work)
        if status.returncode:
            _run(["git", "commit", "-m", "chore(public): sync Spec Harness public source"], work)
        if view.returncode != 0:
            _run(
                [gh, "repo", "create", repo, "--public", "--source", str(work), "--remote", "origin", "--push"],
                Path(temp),
            )
        else:
            _run(["git", "push", "origin", "main"], work)
        if tag:
            _run(["git", "tag", "-f", tag], work)
            _run(["git", "push", "origin", tag, "--force"], work)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or publish the filtered Spec Harness public source tree")
    parser.add_argument("--source", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo", default="MicTx/spec-harness")
    parser.add_argument("--tag")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    try:
        manifest = build_public_tree(Path(args.source), Path(args.output))
        print(f"public export: {len(manifest)} files -> {Path(args.output).resolve()}")
        if args.publish:
            publish_public_tree(Path(args.output).resolve(), args.repo, args.tag)
            print(f"published: https://github.com/{args.repo}")
    except (OSError, PublicExportError) as exc:
        print(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

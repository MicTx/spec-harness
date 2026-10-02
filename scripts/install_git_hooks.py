#!/usr/bin/env python3
"""Install Spec disk-truth Git hooks without overwriting project-owned hooks."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HOOK_NAMES = ("pre-commit", "pre-push")
# Placeholder replaced at install time with the actual absolute path to
# check_all_spec_packages.py, so Codex/custom-path installations don't end up
# with a stale ~/.claude default.
CHECKER_PATH_PLACEHOLDER = "__SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER__"
PYTHON_PATH_PLACEHOLDER = "__SPEC_PYTHON_PLACEHOLDER__"


def clean_git_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Spec Git gates into a repository.")
    parser.add_argument("--root", default=".", help="Target Git repository (default: current directory)")
    return parser.parse_args()


def git_paths(root: Path) -> tuple[Path, Path]:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel", "--git-common-dir", "--git-path", "hooks"],
        cwd=root,
        env=clean_git_env(),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError("not a Git repository")
    lines = completed.stdout.splitlines()
    if len(lines) != 3:
        raise ValueError("cannot resolve Git repository paths")
    repo = Path(lines[0]).resolve()
    common_dir = Path(lines[1])
    if not common_dir.is_absolute():
        common_dir = (root / common_dir).resolve()
    hooks = Path(lines[2])
    if not hooks.is_absolute():
        hooks = (root / hooks).resolve()
    allowed_roots = (repo, common_dir)
    if not any(hooks == allowed or allowed in hooks.parents for allowed in allowed_roots):
        raise ValueError(f"refusing hooks path outside repository or Git common dir: {hooks}")
    return repo, hooks


def install_hooks(root: Path, source_dir: Path) -> list[Path]:
    _, hooks_dir = git_paths(root)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    # Render the actual checker path into the hooks so Codex/custom-path
    # installations don't fall back to a stale ~/.claude default.
    checker_path = (source_dir.parent / "scripts" / "check_all_spec_packages.py").resolve()
    python_path = Path(sys.executable).resolve()
    installed: list[Path] = []
    for name in HOOK_NAMES:
        source = source_dir / name
        target = hooks_dir / name
        content = source.read_text(encoding="utf-8")
        content = content.replace(CHECKER_PATH_PLACEHOLDER, str(checker_path))
        content = content.replace(PYTHON_PATH_PLACEHOLDER, str(python_path))
        if target.exists() or target.is_symlink():
            if target.is_file() and not target.is_symlink() and target.read_text(encoding="utf-8") == content:
                target.chmod(target.stat().st_mode | 0o111)
                installed.append(target)
                continue
            raise ValueError(f"refusing to overwrite project-owned hook: {target}; chain {source} manually")
        target.write_text(content, encoding="utf-8")
        target.chmod(target.stat().st_mode | 0o111)
        installed.append(target)
    return installed


def main() -> int:
    args = parse_args()
    source_dir = Path(__file__).resolve().parent.parent / "hooks"
    try:
        installed = install_hooks(Path(args.root).resolve(), source_dir)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in installed:
        print(f"installed: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

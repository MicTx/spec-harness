#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""
Export a clean runtime skill package from this authoring repository.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from path_safety import UnsafePathError, ensure_output_outside_root, validate_source_tree
from payload_contract import (
    COPY_DIRS,
    IGNORE_PATTERNS,
    PROFILE_VALUES,
    ROOT_FILES,
    SCRIPT_BLACKLIST,
)


def _collect_script_files() -> tuple[str, ...]:
    scripts_dir = Path(__file__).resolve().parent
    if scripts_dir.name != "scripts":
        scripts_dir = scripts_dir / "scripts"
    return tuple(
        p.name
        for p in sorted(scripts_dir.glob("*.py"))
        if p.name not in SCRIPT_BLACKLIST and not p.name.startswith("_")
    )


SCRIPT_FILES = _collect_script_files()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a clean runtime skill package from this authoring repository.")
    parser.add_argument(
        "--output",
        required=True,
        help="Destination directory for the exported skill package",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the destination if it already exists",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Authoring repository root (default: current directory)",
    )
    parser.add_argument(
        "--profile",
        choices=PROFILE_VALUES,
        default="full",
        help="Compatibility alias; core and full export the identical five-stage kernel payload.",
    )
    return parser.parse_args()


def copy_file(src: Path, dest: Path) -> None:
    if src.is_symlink():
        raise UnsafePathError(f"source path is a symlink: {src}")
    if not src.exists():
        raise FileNotFoundError(f"missing required file: {src}")
    if not src.is_file():
        raise UnsafePathError(f"source path is not a regular file: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def copy_tree(src: Path, dest: Path) -> None:
    if not src.exists():
        return
    validate_source_tree(src)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*IGNORE_PATTERNS))


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    try:
        output = ensure_output_outside_root(root, Path(args.output), label="export output")
        for name in ROOT_FILES:
            validate_source_tree(root / name)
        for name in COPY_DIRS:
            validate_source_tree(root / name)
        validate_source_tree(root / "scripts")
    except UnsafePathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if output.exists():
        if not args.force:
            print(f"error: output already exists: {output}", file=sys.stderr)
            return 1
        shutil.rmtree(output)

    output.mkdir(parents=True, exist_ok=True)

    for name in ROOT_FILES:
        copy_file(root / name, output / name)

    for name in COPY_DIRS:
        copy_tree(root / name, output / name)

    scripts_output = output / "scripts"
    scripts_output.mkdir(parents=True, exist_ok=True)
    for name in SCRIPT_FILES:
        copy_file(root / "scripts" / name, scripts_output / name)

    print(f"exported: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

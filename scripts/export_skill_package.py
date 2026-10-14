#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""
Export a clean runtime skill package from this authoring repository.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from path_safety import UnsafePathError, ensure_output_outside_root, validate_source_tree
from payload_contract import (
    CORE_EXCLUDED_DIRS,
    CORE_EXCLUDED_SCRIPTS,
    IGNORE_PATTERNS,
    PROFILE_VALUES,
    ROOT_FILES,
    SCRIPT_BLACKLIST,
    copy_dirs_for_profile,
    excluded_scripts_for_profile,
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

#: Carrier for the invisible distribution signature (AUD-17): a file hosts
#: never load into model context, keeping the entry SKILL.md plain text.
#: The visible text is public-safe; the identity rides only in the mark.
NOTICE_TEXT = (
    "Spec Harness — distribution notice\n"
    "\n"
    "This package carries an invisible distribution signature (see\n"
    "scripts/skill_watermark.py in the authoring repository): a U+2063 mark\n"
    "with a PAD-encoded bitstream binding the release holder and contact.\n"
    "The signature lives in this file so entry documents (SKILL.md) stay\n"
    "plain text and are never loaded into model context. Commercial use\n"
    "requires separate written authorization; see LICENSE.\n"
)
NOTICE_CARRIER = "NOTICE"


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
        "--skip-signing",
        action="store_true",
        help="Export without signing. Tests and keyless checkouts must opt in; release paths must not.",
    )
    parser.add_argument(
        "--profile",
        choices=PROFILE_VALUES,
        default="full",
        help=(
            "Component profile of the exported tree (default: full, byte-equivalent to the "
            "release payload). core excludes the extension components: "
            + ", ".join(CORE_EXCLUDED_DIRS)
            + " plus the chain-family scripts ("
            + ", ".join(sorted(CORE_EXCLUDED_SCRIPTS))
            + "). install.sh passes its resolved INSTALL_PROFILE here."
        ),
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
    copy_dirs = copy_dirs_for_profile(args.profile)
    excluded_scripts = excluded_scripts_for_profile(args.profile)
    script_files = tuple(name for name in SCRIPT_FILES if name not in excluded_scripts)
    try:
        output = ensure_output_outside_root(root, Path(args.output), label="export output")
        for name in ROOT_FILES:
            validate_source_tree(root / name)
        for name in copy_dirs:
            validate_source_tree(root / name)
        validate_source_tree(root / "scripts")
    except UnsafePathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    identity = Path(os.environ.get("SPEC_SIGNING_IDENTITY", root / ".watermark-identity.json"))
    key = Path(os.environ.get("SPEC_SIGNING_KEY", root / ".watermark-key"))
    if not args.skip_signing and (not identity.is_file() or not key.is_file()):
        print(
            "error: signing identity and key are required unless --skip-signing is set "
            "(install.sh applies it automatically on keyless source trees; release builds always sign)",
            file=sys.stderr,
        )
        return 1

    if output.exists():
        if not args.force:
            print(f"error: output already exists: {output}", file=sys.stderr)
            return 1
        shutil.rmtree(output)

    output.mkdir(parents=True, exist_ok=True)

    for name in ROOT_FILES:
        copy_file(root / name, output / name)

    for name in copy_dirs:
        copy_tree(root / name, output / name)

    scripts_output = output / "scripts"
    scripts_output.mkdir(parents=True, exist_ok=True)
    for name in script_files:
        copy_file(root / "scripts" / name, scripts_output / name)

    if not args.skip_signing:
        from skill_watermark import load_identity, load_key, stamp_tree

        # The signature rides in NOTICE, never the entry SKILL.md (AUD-17).
        (output / NOTICE_CARRIER).write_text(NOTICE_TEXT, encoding="utf-8")
        stamp_tree(output, (NOTICE_CARRIER,), load_identity(identity), load_key(key))

    print(f"exported: {output}")
    if args.profile == "core":
        print(
            "profile: core (extension components excluded: "
            + ", ".join(CORE_EXCLUDED_DIRS)
            + "; chain-family scripts excluded: "
            + ", ".join(sorted(CORE_EXCLUDED_SCRIPTS))
            + ")"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

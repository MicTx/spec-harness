"""Filesystem guards shared by runtime exporters.

These checks run before any destructive operation.  They intentionally reject
symlink destinations and non-regular source entries instead of attempting to
guess whether a link is benign.
"""

from __future__ import annotations

import os
from pathlib import Path


class UnsafePathError(ValueError):
    """Raised when a package path could overwrite or escape its source tree."""


def _resolved(path: Path) -> Path:
    return Path(os.path.abspath(path)).resolve(strict=False)


def ensure_output_outside_root(root: Path, output: Path, *, label: str = "output") -> Path:
    """Return the resolved destination after proving it is disjoint from root.

    Both directions matter: a destination inside the source tree can delete
    source files with ``--force``; a destination which contains the source
    tree can delete the entire checkout. Existing symlink destinations are
    rejected before ``rmtree``/``mkdir`` can follow them.
    """

    root_real = _resolved(root)
    output_abs = Path(os.path.abspath(output))
    if output_abs.is_symlink():
        raise UnsafePathError(f"{label} must not be a symlink: {output}")
    output_real = _resolved(output_abs)
    if output_real == root_real or root_real.is_relative_to(output_real) or output_real.is_relative_to(root_real):
        raise UnsafePathError(f"{label} overlaps source root: {output}")
    return output_real


def validate_source_tree(path: Path) -> None:
    """Reject symlinks and non-regular entries in a copied source subtree."""

    if path.is_symlink():
        raise UnsafePathError(f"source path is a symlink: {path}")
    if not path.exists():
        return
    if not path.is_dir() and not path.is_file():
        raise UnsafePathError(f"source path is not a regular file or directory: {path}")
    if path.is_file():
        return
    for entry in path.iterdir():
        if entry.is_symlink():
            raise UnsafePathError(f"source path contains symlink: {entry}")
        if entry.is_dir():
            validate_source_tree(entry)
        elif not entry.is_file():
            raise UnsafePathError(f"source path contains non-regular entry: {entry}")


def safe_child(root: Path, name: str) -> Path:
    """Resolve a generated child and reject links/escapes from *root*."""

    base = _resolved(root)
    child = Path(os.path.abspath(os.fspath(root) + os.sep + name))
    if child.is_symlink():
        raise UnsafePathError(f"generated path is a symlink: {child}")
    child_real = _resolved(child)
    if not child_real.is_relative_to(base):
        raise UnsafePathError(f"generated path escapes output root: {name}")
    return child_real

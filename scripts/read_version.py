#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""Print the project version from pyproject.toml (installer helper)."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: read_version.py <pyproject.toml>", file=sys.stderr)
        return 2
    pyproject = Path(sys.argv[1])
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return 1
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        return 1
    print(match.group(1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

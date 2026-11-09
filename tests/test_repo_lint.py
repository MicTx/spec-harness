"""Local parity with the repository's CI lint gates.

CI runs ``ruff check`` and ``ruff format --check`` over the shipped trees
(``.github/workflows/ci.yml``); a round that only runs pytest can therefore
land lint or formatting drift that stays invisible until the push turns CI
red. These two checks keep the gate visible locally.

They skip when ruff is unavailable, so a plain ``pytest`` run without the dev
toolchain still works. The local and CI ruff builds are resolved from the
same ``pyproject.toml`` configuration; a materially different ruff version can
still disagree on formatting, which is why the check reports the actual diff
rather than only a boolean.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LINT_TARGETS = ("scripts", "hooks", "slots", "tests")


def _ruff(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ruff", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def _ruff_available() -> bool:
    try:
        return _ruff("--version").returncode == 0
    except OSError:  # pragma: no cover - toolchain missing entirely
        return False


pytestmark = pytest.mark.skipif(not _ruff_available(), reason="ruff is not installed")


def test_repository_passes_ruff_check() -> None:
    result = _ruff("check", *LINT_TARGETS)

    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def test_repository_is_ruff_formatted() -> None:
    result = _ruff("format", "--check", *LINT_TARGETS)

    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")

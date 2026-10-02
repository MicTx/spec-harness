"""pytest 共享夹具。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
HOOKS_DIR = SKILL_DIR / "hooks"

for p in (str(SCRIPTS_DIR), str(HOOKS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def tmp_root(tmp_path):
    return tmp_path

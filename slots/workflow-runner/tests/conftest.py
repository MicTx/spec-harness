"""pytest 共享夹具。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"

p = str(SCRIPTS_DIR)
if p not in sys.path:
    sys.path.insert(0, p)


@pytest.fixture(autouse=True)
def isolated_worker_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

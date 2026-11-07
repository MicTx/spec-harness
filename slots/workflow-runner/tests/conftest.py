"""pytest 共享夹具。"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"

p = str(SCRIPTS_DIR)
if p not in sys.path:
    sys.path.insert(0, p)


@pytest.fixture(autouse=True)
def codex_reasoning_catalog(monkeypatch):
    importlib.import_module("workflow_fanout")

    policy = importlib.import_module("reasoning_policy")
    catalog = policy.CodexCatalog("/test/codex", "codex-cli test", {"test-model": ("high", "max")})
    monkeypatch.setattr(policy, "load_codex_catalog", lambda: catalog)


@pytest.fixture(autouse=True)
def isolated_worker_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

"""F5 (AUD-20): the suite conftest clears exactly the four host env vars.

The real proof of the fixture is the poisoned-env acceptance command
(``CLAUDECODE=1 CODEX_SANDBOX=1 PI_CODING_AGENT=1 SPEC_AUTORUN_HOST=claude
pytest tests/test_autorun_chain.py``); these tests lock the fixture's
contract so the list cannot silently drift from or widen past the host
marker true source.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_conftest():
    module_spec = importlib.util.spec_from_file_location("tests_conftest", ROOT / "tests" / "conftest.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def test_cleared_list_is_exactly_the_four_named_vars():
    conftest = _load_conftest()
    assert conftest.CLEARED_HOST_ENV_VARS == (
        "CLAUDECODE",
        "CODEX_SANDBOX",
        "PI_CODING_AGENT",
        "SPEC_AUTORUN_HOST",
    )


def test_cleared_list_matches_host_marker_true_source():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import autorun_spawn
    finally:
        sys.path.pop(0)
    conftest = _load_conftest()
    marker_vars = {var for _host, var in autorun_spawn.HOST_ENV_MARKERS}
    assert set(conftest.CLEARED_HOST_ENV_VARS) == marker_vars | {"SPEC_AUTORUN_HOST"}
    assert len(conftest.CLEARED_HOST_ENV_VARS) == 4


def test_host_markers_absent_during_tests():
    # Vacuous in a clean environment; fails if the autouse fixture is
    # removed or broken while the suite runs inside a host CLI session.
    for var in ("CLAUDECODE", "CODEX_SANDBOX", "PI_CODING_AGENT", "SPEC_AUTORUN_HOST"):
        assert var not in os.environ

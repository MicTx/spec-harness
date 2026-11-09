"""A leftover ``update-checkpoint.json`` (retired mechanism) is advisory only."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_spec_package import package_physical_state_errors  # noqa: E402
from report_spec_package import detect_update_checkpoint  # noqa: E402


def test_leftover_file_is_detected_but_never_gates_delivery(tmp_path):
    (tmp_path / "update-checkpoint.json").write_text("{}", encoding="utf-8")
    # The mechanism is retired: route surfaces an advisory, check/done do not refuse.
    assert package_physical_state_errors(tmp_path) == []
    assert detect_update_checkpoint(tmp_path).interrupted


def test_no_file_no_error(tmp_path):
    assert package_physical_state_errors(tmp_path) == []
    assert not detect_update_checkpoint(tmp_path).interrupted

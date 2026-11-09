"""chain_state.py: plan discovery, round accounting, completion and legacy refusal."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "chain_state.py"


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--root", str(root), "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )


def write_plan(root: Path, body: str) -> Path:
    plan = root / ".spec" / "plans" / "2026-11-08_add-demo.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(body, encoding="utf-8")
    return plan


def test_plan_without_documents_exits_3(tmp_path):
    result = run(tmp_path, "plan")
    assert result.returncode == 3
    assert "no planning document" in result.stderr


def test_plan_counts_features_and_skips_archive(tmp_path):
    write_plan(tmp_path, "# Demo\n- [x] one\n- [ ] two\n")
    archived = tmp_path / ".spec" / "plans" / "archive" / "old.md"
    archived.parent.mkdir(parents=True)
    archived.write_text("- [ ] old\n", encoding="utf-8")
    payload = json.loads(run(tmp_path, "plan").stdout)
    assert (payload["checked"], payload["total"], payload["complete"]) == (1, 2, False)
    assert [doc["path"] for doc in payload["docs"]] == [".spec/plans/2026-11-08_add-demo.md"]


def test_autorun_defaults_to_no_cap(tmp_path):
    """Omitting the flag leaves the chain unbounded; the fifth and later steps still start."""
    write_plan(tmp_path, "- [ ] one\n- [ ] two\n- [ ] three\n- [ ] four\n- [ ] five\n- [ ] six\n")
    state = json.loads(run(tmp_path, "begin", "--chain", "autorun", "--step", "pkg-a").stdout)
    assert state["max_rounds"] is None
    for index in range(2, 8):
        result = run(tmp_path, "begin", "--chain", "autorun", "--step", f"pkg-{index}")
        assert result.returncode == 0, f"begin {index} must not hit a cap"
        assert json.loads(result.stdout)["round"] == index


def test_autorun_rounds_repeat_steps_and_cap(tmp_path):
    write_plan(tmp_path, "- [ ] one\n- [ ] two\n")
    first = json.loads(run(tmp_path, "begin", "--chain", "autorun", "--step", "pkg-a", "--max-rounds", "1").stdout)
    assert (first["round"], first["status"]) == (1, "active")
    again = json.loads(run(tmp_path, "begin", "--chain", "autorun", "--step", "pkg-a").stdout)
    assert again["round"] == 1, "resuming the same step spends no round"
    capped = run(tmp_path, "begin", "--chain", "autorun", "--step", "pkg-b")
    assert capped.returncode == 3
    assert json.loads(capped.stdout)["status"] == "paused"


def test_autorun_completes_when_every_feature_is_checked(tmp_path):
    plan = write_plan(tmp_path, "- [ ] one\n")
    run(tmp_path, "begin", "--chain", "autorun", "--step", "pkg-a")
    plan.write_text("- [x] one\n", encoding="utf-8")
    finished = json.loads(run(tmp_path, "finish", "--chain", "autorun").stdout)
    assert finished["status"] == "complete"


def test_status_flags_scope_change(tmp_path):
    plan = write_plan(tmp_path, "- [ ] one\n")
    run(tmp_path, "begin", "--chain", "autorun", "--step", "pkg-a")
    plan.write_text("- [ ] one, reworded\n", encoding="utf-8")
    assert json.loads(run(tmp_path, "status", "--chain", "autorun").stdout)["scope_changed"] is True


def test_legacy_state_is_refused_with_instruction(tmp_path):
    state = tmp_path / ".spec" / "autorun" / "chain.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"schema_version": 3, "execution_mode": "current"}), encoding="utf-8")
    result = run(tmp_path, "status", "--chain", "autorun")
    assert result.returncode == 1
    assert "delete it" in result.stderr


def test_close_moves_state_to_history(tmp_path):
    write_plan(tmp_path, "- [ ] one\n")
    run(tmp_path, "begin", "--chain", "autoplan", "--step", "framework-1")
    closed = json.loads(run(tmp_path, "close", "--chain", "autoplan").stdout)
    assert closed["closed_to"].startswith(".spec/autoplan/history/")
    assert json.loads(run(tmp_path, "status", "--chain", "autoplan").stdout)["status"] == "none"

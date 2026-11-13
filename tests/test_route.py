import json
import subprocess
import sys
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo, started_package

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

import route  # noqa: E402
from spec_hook import Action, handle  # noqa: E402


def _card(root: Path) -> dict:
    result = cli(root, "next")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _package_on_main(tmp_path: Path) -> Path:
    """Fixture: an unbound package on main so plan-go creates a linked worktree."""
    repo(tmp_path)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(spec.read_text(encoding="utf-8").split("## Binding")[0].rstrip() + "\n", encoding="utf-8")
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-d", f"spec/{SLUG}"], cwd=tmp_path, check=True, capture_output=True)
    return package


def _gate_records(package: Path) -> list[dict]:
    return [
        json.loads(line) for line in (package / "runs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_next_card_walks_the_full_lifecycle(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)

    planning = _card(tmp_path)
    assert planning["state"] == "plan"
    assert planning["stage"] == "plan"
    assert "plan-lint" in planning["next"]

    assert cli(tmp_path, "plan-go").returncode == 0
    running = _card(tmp_path)
    assert running["state"] == "run"
    assert running["stage"] == "run"
    assert running["task"]["id"] == "task-value"

    assert cli(tmp_path, "verify", "task-value").returncode == 0
    checking = _card(tmp_path)
    assert checking["state"] == "deliver"
    assert checking["stage"] == "check"
    assert "spec.py check" in checking["next"]

    assert cli(tmp_path, "check").returncode == 0
    delivered = _card(tmp_path)
    assert delivered["state"] == "delivered"
    assert delivered["stage"] == "done"
    assert delivered["archived"] is True
    assert "Delivery complete" in delivered["next"]


def test_failed_check_is_recorded_and_routed_to_repair(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    (tmp_path / "other.txt").write_text("outside the task boundary\n", encoding="utf-8")

    checked = cli(tmp_path, "check", "--check-only")

    assert checked.returncode == 1
    gate = [record for record in _gate_records(package) if record.get("kind") == "gate"][-1]
    assert gate["passed"] is False
    assert any("changed path outside" in error for error in gate["errors"])
    routed = _card(tmp_path)
    assert routed["state"] == "check-failed"
    assert routed["stage"] == "check"
    assert "Move the change inside the owning task boundary" in routed["next"]
    assert any("changed path outside" in error for error in routed["gate"]["errors"])


def test_stop_after_failed_check_reports_gate_error_then_stalls(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    (tmp_path / "other.txt").write_text("outside the task boundary\n", encoding="utf-8")
    assert cli(tmp_path, "check", "--check-only").returncode == 1

    first = handle("stop", {"cwd": str(tmp_path)})
    second = handle("stop", {"cwd": str(tmp_path)})
    third = handle("stop", {"cwd": str(tmp_path)})

    assert first.block and "Move the change inside the owning task boundary" in first.reason
    assert second.block
    assert not third.block
    assert third.context.startswith("stalled:")


def test_start_hook_points_to_the_active_linked_worktree(tmp_path):
    _package_on_main(tmp_path)
    assert cli(tmp_path, "--slug", SLUG, "plan-go").returncode == 0

    action = handle("start", {"cwd": str(tmp_path)})

    assert isinstance(action, Action)
    assert SLUG in action.context
    assert ".spec/worktrees" in action.context
    assert "continue there" in action.context


def test_start_hook_reports_a_completed_archived_delivery(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "check").returncode == 0

    action = handle("start", {"cwd": str(tmp_path)})

    assert "Delivery complete" in action.context
    assert SLUG in action.context


def test_archived_card_keeps_a_pending_push_visible(tmp_path):
    repo(tmp_path)
    archive = tmp_path / ".spec" / "specs" / "archive" / SLUG
    archive.mkdir(parents=True)
    (archive / "spec.md").write_text(
        "# Fixture\n<!-- spec-format: 3 -->\n## Charter\n- V4 finish: push | charter.md\n"
        f"## Binding\n- branch: `spec/{SLUG}`\n",
        encoding="utf-8",
    )
    (archive / "tasks.md").write_text("", encoding="utf-8")

    card = route.archived_card(tmp_path)

    assert card["state"] == "push"
    assert card["stage"] == "push"
    assert "spec.py push" in card["next"]
    assert route.completion_card(SLUG, "push", push_failed=True)["state"] == "push"

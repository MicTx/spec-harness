"""Only durable checkpoint evidence advances the active task graph."""

import json

import gitx
import package

from tests.v3_support import SLUG, cli, repo, started_package


def test_hand_checked_card_cannot_finish_or_unlock_work(tmp_path):
    repo(tmp_path)
    directory = started_package(tmp_path)
    tasks = directory / "tasks.md"
    tasks.write_text(tasks.read_text().replace("- [ ]", "- [x]"))

    assert json.loads(cli(tmp_path, "next").stdout)["ready"] == ["task-value"]
    check = cli(tmp_path, "check")
    assert check.returncode == 1
    assert "task-unfinished" in {item["code"] for item in json.loads(check.stdout)["findings"]}
    assert directory.exists()


def test_interrupted_projection_resumes_from_a_durable_checkpoint(tmp_path):
    repo(tmp_path)
    directory = started_package(tmp_path)
    tasks = directory / "tasks.md"
    original = tasks.read_bytes()
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    tasks.write_bytes(original)
    (directory / "state.json").write_text('{"task": {"id": "other"}, "terminal": "stalled"}\n')

    card = json.loads(cli(tmp_path, "next").stdout)
    assert card["state"] == "deliver" and card["criteria"][0]["passed"]
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    assert cli(tmp_path, "check").returncode == 0
    archived = directory.parent / "archive" / SLUG
    assert "- [x]" in (archived / "tasks.md").read_text()


def test_ledger_without_checkpoint_receipt_does_not_advance_progress(tmp_path):
    repo(tmp_path)
    directory = started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    rows = package.runs(directory)
    completed = next(row for row in rows if row.get("kind") == "run")
    assert completed["checkpoint"] == gitx.head(tmp_path)
    completed["tree_fp"] = "not the verified product tree"
    (directory / "runs.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))

    card = json.loads(cli(tmp_path, "next").stdout)
    assert card["ready"] == ["task-value"] and not card["criteria"][0]["passed"]
    assert cli(tmp_path, "check").returncode == 1
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert json.loads(cli(tmp_path, "next").stdout)["ready"] == []


def test_edited_baseline_cannot_drop_the_checkpoint_protocol(tmp_path):
    repo(tmp_path)
    directory = started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    rows = package.runs(directory)
    next(row for row in rows if row.get("task") == "package").pop("evidence_kernel")
    (directory / "runs.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))

    checked = cli(tmp_path, "check")
    assert checked.returncode == 1
    assert "Git freeze receipt" in checked.stderr
    assert directory.exists()

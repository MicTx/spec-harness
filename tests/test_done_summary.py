from __future__ import annotations

import json

from memory import lessons_add

from tests.v3_support import SLUG, cli, git, repo, started_package


def _deliver(root):
    started_package(root)
    assert cli(root, "verify", "task-value").returncode == 0
    done = cli(root, "done")
    assert done.returncode == 0, done.stderr
    return _archive_summary(root)


def _archive_summary(root):
    return (root / ".spec/specs/archive" / SLUG / "completion-summary.md").read_text(encoding="utf-8")


def _archive_runs(root):
    path = root / ".spec/specs/archive" / SLUG / "runs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_done_summary_marks_no_lessons_when_file_absent(tmp_path):
    repo(tmp_path)
    summary = _deliver(tmp_path)
    assert "## Lessons" in summary
    assert "- No retained lessons." in summary
    assert "See .spec/lessons.md" not in summary


def test_done_summary_ignores_unused_global_ledger_lessons(tmp_path):
    repo(tmp_path)
    (tmp_path / ".spec").mkdir(exist_ok=True)
    lessons_add(tmp_path, "same signature", "reproduce first")

    summary = _deliver(tmp_path)

    assert "- No retained lessons." in summary
    assert "See .spec/lessons.md" not in summary
    assert "Promote:" not in summary


def test_ledger_done_archives_recorded_lessons_with_enforce_targets(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    learn = cli(tmp_path, "learn", "same signature repeats", "build a reproduction", "--enforce", "app.py")
    assert learn.returncode == 0, learn.stderr

    done = cli(tmp_path, "done", "--lesson-used", "L1")

    assert done.returncode == 0, done.stderr
    summary = _archive_summary(tmp_path)
    assert "- L1 | when: same signature repeats | do: build a reproduction | enforce: app.py" in summary
    assert "See .spec/lessons.md" not in summary
    assert "Promote:" not in summary
    assert "hits:" not in summary
    learn_rows = [record for record in _archive_runs(tmp_path) if record.get("kind") == "learn"]
    assert [record["lesson"] for record in learn_rows] == ["L1"]
    assert learn_rows[0]["enforce"] == "app.py"


def test_ledger_done_rejects_recorded_lesson_without_lesson_used(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "learn", "when", "do", "--enforce", "app.py").returncode == 0

    done = cli(tmp_path, "done")

    assert done.returncode == 1
    assert "--lesson-used" in done.stderr
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_ledger_done_requires_every_recorded_lesson(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "learn", "first pain", "first rule", "--enforce", "app.py").returncode == 0
    assert cli(tmp_path, "learn", "second pain", "second rule", "--enforce", "app.py").returncode == 0

    done = cli(tmp_path, "done", "--lesson-used", "L1")

    assert done.returncode == 1
    assert "L2" in done.stderr
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_ledger_done_rejects_missing_enforce_target(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "learn", "when", "do", "--enforce", "app.py").returncode == 0
    (tmp_path / "app.py").unlink()

    done = cli(tmp_path, "done", "--lesson-used", "L1")

    assert done.returncode == 1
    assert "does not exist" in done.stderr
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_ledger_done_rejects_untracked_enforce_target(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    assert cli(tmp_path, "learn", "when", "do", "--enforce", "app.py").returncode == 0
    git(tmp_path, "rm", "--cached", "app.py")

    done = cli(tmp_path, "done", "--lesson-used", "L1")

    assert done.returncode == 1
    assert "not tracked" in done.stderr
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_ledger_done_lists_used_global_lessons(tmp_path):
    repo(tmp_path)
    (tmp_path / ".spec").mkdir(exist_ok=True)
    (tmp_path / ".spec/lessons.md").write_text(
        "# Spec Lessons\n- L7 | when: prior pain | do: reuse the pattern | enforce: app.py\n",
        encoding="utf-8",
    )
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    done = cli(tmp_path, "done", "--lesson-used", "L7")

    assert done.returncode == 0, done.stderr
    summary = _archive_summary(tmp_path)
    assert "- L7 | when: prior pain | do: reuse the pattern | enforce: app.py" in summary

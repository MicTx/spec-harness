from __future__ import annotations

from memory import lessons_add

from tests.v3_support import SLUG, cli, repo, started_package


def _deliver(root):
    started_package(root)
    assert cli(root, "verify", "task-value").returncode == 0
    done = cli(root, "done")
    assert done.returncode == 0, done.stderr
    return (root / ".spec/specs/archive" / SLUG / "completion-summary.md").read_text(encoding="utf-8")


def test_done_summary_marks_no_lessons_when_file_absent(tmp_path):
    repo(tmp_path)
    summary = _deliver(tmp_path)
    assert "## Lessons" in summary
    assert "- No retained lessons." in summary
    assert "See .spec/lessons.md" not in summary


def test_done_summary_points_at_lessons_when_retained(tmp_path):
    repo(tmp_path)
    (tmp_path / ".spec").mkdir(exist_ok=True)
    lessons_add(tmp_path, "same signature", "reproduce first")
    summary = _deliver(tmp_path)
    assert "- See .spec/lessons.md for retained project lessons." in summary

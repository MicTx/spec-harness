import json

import memory
from memory import lessons_add, lessons_top

from tests.v3_support import SLUG, cli, repo, started_package


def _package_runs(root):
    path = root / ".spec" / "specs" / SLUG / "runs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_ledger_round_trip_records_when_do_and_enforce(tmp_path):
    repo(tmp_path)
    lesson = lessons_add(tmp_path, "same signature", "build a reproduction", "app.py")

    assert lesson == "L1"
    assert lessons_top(tmp_path) == ["- L1 | when: same signature | do: build a reproduction | enforce: app.py"]
    assert "hits:" not in lessons_top(tmp_path)[0]


def test_learn_enforce_writes_ledger_row_and_kind_learn_record(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)

    result = cli(tmp_path, "learn", "same signature repeats", "build a reproduction", "--enforce", "app.py")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["lesson"] == "L1"
    assert payload["package"] == SLUG
    assert payload["enforce"] == "app.py"
    assert payload["kind"] == "learn"
    assert lessons_top(tmp_path) == ["- L1 | when: same signature repeats | do: build a reproduction | enforce: app.py"]
    learn_rows = [record for record in _package_runs(tmp_path) if record.get("kind") == "learn"]
    assert len(learn_rows) == 1
    assert learn_rows[0]["task"] == "learn"
    assert learn_rows[0]["lesson"] == "L1"
    assert learn_rows[0]["enforce"] == "app.py"
    assert learn_rows[0]["ts"]


def test_learn_enforce_is_required(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)

    result = cli(tmp_path, "learn", "when", "do")

    assert result.returncode == 2
    assert "--enforce" in result.stderr
    assert lessons_top(tmp_path) == []
    assert [record for record in _package_runs(tmp_path) if record.get("kind") == "learn"] == []


def test_learn_enforce_rejects_missing_target(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)

    result = cli(tmp_path, "learn", "when", "do", "--enforce", "missing_rule.py")

    assert result.returncode == 1
    assert "does not exist" in result.stderr
    assert lessons_top(tmp_path) == []
    assert [record for record in _package_runs(tmp_path) if record.get("kind") == "learn"] == []


def test_learn_enforce_rejects_untracked_target(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    (tmp_path / "notes.txt").write_text("untracked\n", encoding="utf-8")

    result = cli(tmp_path, "learn", "when", "do", "--enforce", "notes.txt")

    assert result.returncode == 1
    assert "not tracked" in result.stderr
    assert lessons_top(tmp_path) == []


def test_learn_enforce_rejects_target_inside_the_package(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)

    result = cli(tmp_path, "learn", "when", "do", "--enforce", f".spec/specs/{SLUG}/spec.md")

    assert result.returncode == 1
    assert "outside the package" in result.stderr
    assert lessons_top(tmp_path) == []


def test_learn_without_package_writes_only_the_global_ledger(tmp_path):
    repo(tmp_path)
    (tmp_path / ".spec" / "specs").mkdir(parents=True)

    result = cli(tmp_path, "learn", "no active package", "keep the global ledger", "--enforce", "app.py")

    assert result.returncode == 0, result.stderr
    assert "global" in result.stderr.lower()
    assert json.loads(result.stdout)["package"] is None
    assert lessons_top(tmp_path) == ["- L1 | when: no active package | do: keep the global ledger | enforce: app.py"]
    assert not (tmp_path / ".spec" / "specs" / SLUG).exists()


def test_lessons_resolve_primary_worktree_root(tmp_path):
    from tests.v3_support import git as run_git
    from tests.v3_support import repo as make_repo

    make_repo(tmp_path)
    linked = tmp_path.parent / f"{tmp_path.name}-linked"
    run_git(tmp_path, "worktree", "add", "-b", "side", str(linked))

    lesson = lessons_add(linked, "concurrent spec", "share lessons at the primary root")

    assert lesson == "L1"
    assert (tmp_path / ".spec" / "lessons.md").is_file()
    assert not (linked / ".spec" / "lessons.md").exists()
    assert lessons_top(linked)[0].startswith("- L1")


def test_memory_has_no_retired_lesson_api():
    assert not hasattr(memory, "lessons_adjust")
    assert not hasattr(memory, "lesson_proposals")


def test_charter_resolves_primary_worktree_root(tmp_path):
    import charter

    from tests.v3_support import git, repo

    repo(tmp_path)
    linked = tmp_path.parent / f"{tmp_path.name}-linked"
    git(tmp_path, "worktree", "add", "-b", "side", str(linked))

    charter.merge(linked, {"V4": "push"})

    assert (tmp_path / ".spec" / "charter.md").is_file()
    assert not (linked / ".spec" / "charter.md").exists()
    assert charter.load(linked)["V4"] == "push"
    assert charter.summary_line(tmp_path).count("V4=push") == 1

from memory import lessons_add, lessons_adjust, lessons_top


def test_lessons_round_trip_and_wrong_lesson_removal(tmp_path):
    (tmp_path / ".spec").mkdir()
    lesson = lessons_add(tmp_path, "same signature", "build a reproduction")
    assert lesson == "L1"
    assert lessons_top(tmp_path)
    lessons_adjust(tmp_path, [lesson], [])
    assert "hits: 1" in lessons_top(tmp_path)[0]
    lessons_adjust(tmp_path, [], [lesson])
    assert lessons_top(tmp_path) == []


def test_lessons_resolve_primary_worktree_root(tmp_path):
    from tests.v3_support import git, repo

    repo(tmp_path)
    linked = tmp_path.parent / f"{tmp_path.name}-linked"
    git(tmp_path, "worktree", "add", "-b", "side", str(linked))

    lesson = lessons_add(linked, "concurrent spec", "share lessons at the primary root")

    assert lesson == "L1"
    assert (tmp_path / ".spec" / "lessons.md").is_file()
    assert not (linked / ".spec" / "lessons.md").exists()
    assert lessons_top(linked)[0].startswith("- L1")
    lessons_adjust(linked, [lesson], [])
    assert "hits: 1" in lessons_top(tmp_path)[0]


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

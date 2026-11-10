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

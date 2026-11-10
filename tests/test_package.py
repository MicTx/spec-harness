from package import parse_charter, parse_tasks, path_matches, ready_tasks

from tests.v3_support import SLUG, make_package, repo


def test_v3_model_parses_tasks_charter_and_dependencies(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    content = (tmp_path / ".spec/specs" / SLUG / "tasks.md").read_text()
    tasks = parse_tasks(content)
    assert tasks[0].id == "task-value"
    assert tasks[0].expect == "green"
    assert ready_tasks(tasks) == tasks
    spec = (tmp_path / ".spec/specs" / SLUG / "spec.md").read_text()
    assert parse_charter(spec)["V2"] == "adjacent-fixes"


def test_boundaries_match_files_and_directories():
    assert path_matches("tests/unit/test_x.py", "tests/**")
    assert path_matches("app.py", "app.py")
    assert not path_matches("other.py", "app.py")


def test_v2_is_not_an_implicit_v3_package(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    (package / "spec.md").write_text("<!-- spec-format: 2 -->")
    import pytest
    from package import resolve_slug

    with pytest.raises(ValueError):
        resolve_slug(tmp_path)

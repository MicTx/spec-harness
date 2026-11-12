from gate import boundary_audit

from tests.v3_support import make_package, repo


def test_out_of_boundary_edit_is_reported(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    (tmp_path / "other.py").write_text("bad\n")
    assert any("other.py" in item for item in boundary_audit(tmp_path, "2026-11-10_add-v3-fixture"))


def test_narrow_glob_does_not_admit_nested_paths(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    (package / "tasks.md").write_text(
        "- [ ] Narrow boundary\n  - id: task-value\n  - boundary: src/*.py\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "src" / "direct.py").write_text("DIRECT = True\n", encoding="utf-8")
    (tmp_path / "src" / "deep" / "nested.py").write_text("NESTED = True\n", encoding="utf-8")

    errors = boundary_audit(tmp_path, package.name)

    assert any("src/deep/nested.py" in error for error in errors)
    assert not any("src/direct.py" in error for error in errors)

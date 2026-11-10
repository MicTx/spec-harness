from gate import boundary_audit

from tests.v3_support import make_package, repo


def test_out_of_boundary_edit_is_reported(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    (tmp_path / "other.py").write_text("bad\n")
    assert any("other.py" in item for item in boundary_audit(tmp_path, "2026-11-10_add-v3-fixture"))

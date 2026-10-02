import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import import_kiro_specs as importer
from check_spec_package import compute_gate_results, is_likely_template
from spec_package_support import clarification_gaps

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "kiro" / "user-authentication"
SLUG = "2026-09-02_import-kiro-user-authentication"

TEMPLATE_PLACEHOLDERS = ("[项目名称]", "功能 A", "xxx")


def write_simple_spec(root, name="simple-ears", with_design=True, with_tasks=True):
    spec_dir = root / name
    spec_dir.mkdir(parents=True)
    (spec_dir / "requirements.md").write_text(
        "# Requirements\n"
        "\n"
        "## User Authentication\n"
        "\n"
        "WHEN a user submits a form with invalid data THE SYSTEM SHALL display validation errors"
        " next to the relevant fields\n",
        encoding="utf-8",
    )
    if with_design:
        (spec_dir / "design.md").write_text(
            "# Design\n\n## Architecture\n\nThin validation layer in front of the form handler.\n",
            encoding="utf-8",
        )
    if with_tasks:
        (spec_dir / "tasks.md").write_text("# Implementation Plan\n\n- [ ] 1. Add validation layer\n", encoding="utf-8")
    return spec_dir


def apply_fixture(root, extra=None):
    argv = [str(FIXTURE), "--root", str(root), "--date", "2026-09-02", "--apply"]
    if extra:
        argv.extend(extra)
    return importer.main(argv)


# --------------------------------------------------------------------------- #
# Slug building
# --------------------------------------------------------------------------- #


def test_normalize_kiro_name():
    assert importer.normalize_kiro_name("User Authentication_Extras!!") == "user-authentication-extras"
    assert importer.normalize_kiro_name("  --Payment--Processing--  ") == "payment-processing"
    assert importer.normalize_kiro_name("订单结算") == ""


def test_build_slug_valid_and_conflict_prone():
    assert importer.build_slug("2026-09-02", "user-authentication") == SLUG
    assert importer.build_slug("2026-09-02", "User Authentication") == SLUG


def test_build_slug_rejects_unmappable_name():
    try:
        importer.build_slug("2026-09-02", "订单结算")
    except importer.ImportError_ as exc:
        assert "slug" in str(exc)
    else:
        raise AssertionError("expected ImportError_ for CJK-only spec name")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_parse_requirements_canonical_fixture():
    requirements, purpose = importer.parse_requirements((FIXTURE / "requirements.md").read_text("utf-8"))
    assert purpose and "secure user authentication" in purpose
    assert [req.title for req in requirements] == ["User Registration", "Login"]
    assert len(requirements[0].scenarios) == 3
    assert len(requirements[1].scenarios) == 2
    assert requirements[0].scenarios[0].ears[0].startswith("WHEN a user submits valid registration data")


def test_parse_requirements_plain_ears_fallback():
    text = (
        "# Requirements\n\n## User Authentication\n\n"
        "WHEN a user submits a form with invalid data THE SYSTEM SHALL display validation errors\n"
    )
    requirements, _ = importer.parse_requirements(text)
    assert len(requirements) == 1
    assert requirements[0].title == "User Authentication"
    assert requirements[0].ears and "THE SYSTEM SHALL" in requirements[0].ears[0]


def test_parse_tasks_nested_and_done():
    tasks = importer.parse_tasks((FIXTURE / "tasks.md").read_text("utf-8"))
    assert [task.number for task in tasks] == ["1", "2", "2.1", "2.2", "3", "4"]
    assert tasks[2].text == "Validate email format before persistence"
    assert tasks[4].done is True
    assert tasks[5].details == ["Note: follow the existing API documentation style"]


# --------------------------------------------------------------------------- #
# Dry-run / apply behavior
# --------------------------------------------------------------------------- #


def test_dry_run_prints_plan_and_writes_nothing(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        code = importer.main([str(FIXTURE), "--root", tmpdir, "--date", "2026-09-02"])
        captured = capsys.readouterr()
        assert code == 0
        assert "dry-run" in captured.out
        assert SLUG in captured.out
        assert "映射表" in captured.out
        assert not (Path(tmpdir) / ".spec").exists()


def test_apply_creates_package_structure(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        assert apply_fixture(root) == 0
        captured = capsys.readouterr()
        assert captured.out.count("created:") == 3
        package = root / ".spec" / "specs" / SLUG
        for filename in ("spec.md", "tasks.md", "checklist.md"):
            assert (package / filename).is_file(), filename

        spec_md = (package / "spec.md").read_text("utf-8")
        assert "来源：Kiro spec `user-authentication`" in spec_md
        assert "迁移日期 2026-09-02" in spec_md
        assert "import_kiro_specs.py v1.0" in spec_md
        assert "- [ ] User Registration：" in spec_md
        assert "- 标准 A（User Registration / Valid registration" in spec_md
        assert "## 附录 A：Kiro design.md 原文" in spec_md
        assert "## 附录 B：Kiro requirements.md 原文" in spec_md
        assert "sequenceDiagram" in spec_md

        tasks_md = (package / "tasks.md").read_text("utf-8")
        assert "- [ ] 2.1. Validate email format before persistence" in tasks_md
        assert "- boundary: 迁移自 Kiro 任务 2.1，待人工校准" in tasks_md
        assert "- verify: 待迁移补齐" in tasks_md
        assert "- [x] 3. Write unit tests for validation rules" in tasks_md
        assert "- Note: follow the existing API documentation style" in tasks_md

        checklist_md = (package / "checklist.md").read_text("utf-8")
        assert "# Kiro 迁移：user-authentication - 验收清单" in checklist_md
        assert "import_kiro_specs.py" in checklist_md


def test_apply_conflict_rejected_then_different_date_succeeds(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        assert apply_fixture(root) == 0
        capsys.readouterr()

        code = apply_fixture(root)
        captured = capsys.readouterr()
        assert code == 1
        assert "already exists" in captured.err
        assert "--date" in captured.err

        code = apply_fixture(root, extra=["--date", "2026-09-03"])
        captured = capsys.readouterr()
        assert code == 0
        assert (root / ".spec" / "specs" / "2026-09-03_import-kiro-user-authentication" / "spec.md").is_file()


def test_batch_slug_collision_rejected_before_writes(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        specs_root = root / ".kiro" / "specs"
        for name in ("User Auth", "user-auth"):
            write_simple_spec(specs_root, name=name)
        code = importer.main([str(specs_root), "--root", str(root / "target"), "--date", "2026-09-02", "--apply"])
        captured = capsys.readouterr()
        assert code == 1
        assert "slug conflict" in captured.err
        assert not (root / "target" / ".spec" / "specs").exists()


def test_multi_spec_root_imports_each(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        specs_root = root / ".kiro" / "specs"
        write_simple_spec(specs_root, name="alpha")
        write_simple_spec(specs_root, name="beta", with_design=False)
        code = importer.main([str(specs_root), "--root", str(root / "target"), "--date", "2026-09-02", "--apply"])
        assert code == 0
        for slug in ("2026-09-02_import-kiro-alpha", "2026-09-02_import-kiro-beta"):
            assert (root / "target" / ".spec" / "specs" / slug / "tasks.md").is_file()


def test_kiro_root_directory_auto_descends():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        write_simple_spec(root / ".kiro" / "specs", name="gamma")
        assert importer.resolve_source(root / ".kiro") == root / ".kiro" / "specs"


def test_missing_requirements_rejected(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source = write_simple_spec(root, name="no-reqs")
        (source / "requirements.md").unlink()
        code = importer.main([str(source), "--root", str(root / "target"), "--date", "2026-09-02", "--apply"])
        captured = capsys.readouterr()
        assert code == 1
        assert "requirements.md" in captured.err


def test_invalid_ears_rejected(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source = write_simple_spec(root, name="bad-reqs")
        (source / "requirements.md").write_text("# Requirements\n\nJust some prose.\n", encoding="utf-8")
        code = importer.main([str(source), "--root", str(root / "target"), "--date", "2026-09-02", "--apply"])
        captured = capsys.readouterr()
        assert code == 1
        assert "EARS" in captured.err


def test_missing_design_noted_not_fatal(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source = write_simple_spec(root, name="no-design", with_design=False)
        code = importer.main([str(source), "--root", str(root / "target"), "--date", "2026-09-02", "--apply"])
        assert code == 0
        spec_md = (root / "target" / ".spec" / "specs" / "2026-09-02_import-kiro-no-design" / "spec.md").read_text(
            "utf-8"
        )
        assert "源 spec 未提供 design.md" in spec_md
        assert "附录 A：Kiro design.md 原文" not in spec_md


def test_invalid_date_rejected(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        code = importer.main([str(FIXTURE), "--root", tmpdir, "--date", "2026-13-99"])
        captured = capsys.readouterr()
        assert code == 1
        assert "--date" in captured.err


# --------------------------------------------------------------------------- #
# Converted product quality: no template placeholders, honest scaffolding
# --------------------------------------------------------------------------- #


def converted_package(root):
    assert apply_fixture(root) == 0
    package = root / ".spec" / "specs" / SLUG
    contents = {name: (package / name).read_text("utf-8") for name in ("spec.md", "tasks.md", "checklist.md")}
    return contents


def test_converted_package_has_no_template_placeholders():
    with tempfile.TemporaryDirectory() as tmpdir:
        contents = converted_package(Path(tmpdir))
        for name, text in contents.items():
            for placeholder in TEMPLATE_PLACEHOLDERS:
                assert placeholder not in text, f"{placeholder} leaked into {name}"


def test_converted_package_passes_gates_except_pending_todo():
    with tempfile.TemporaryDirectory() as tmpdir:
        contents = converted_package(Path(tmpdir))
        spec_md = contents["spec.md"]
        tasks_md = contents["tasks.md"]
        checklist_md = contents["checklist.md"]

        assert clarification_gaps(spec_md) == []
        assert is_likely_template(tasks_md) is False

        results = compute_gate_results(spec_md, tasks_md, checklist_md, slug=SLUG)
        assert results.development_record_ok is True
        assert results.assumptions_ok is True
        assert results.simplicity_ok is True
        assert results.boundary_ok is True
        assert results.command_evidence_ok is True
        assert results.pending_questions == []
        assert results.missing_boundary == 0
        assert results.missing_verify == 0
        # Fresh migration is intentionally not converged: unchecked tasks remain.
        assert results.function_ok is False
        assert results.overall_ok is False


def test_check_cli_reports_todo_not_template():
    with tempfile.TemporaryDirectory() as tmpdir:
        converted_package(Path(tmpdir))
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/check_spec_package.py",
                "--root",
                tmpdir,
                "--slug",
                SLUG,
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert completed.returncode == 1
        assert "未收敛" in completed.stdout
        assert "未勾任务" in completed.stdout
        assert "任务仍是初始化模板" not in completed.stdout
        assert "[项目名称]" not in completed.stdout

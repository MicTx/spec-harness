from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from migrate_task_ids import migrate, migration
from spec_package_support import SpecControlError, dependency_errors, parse_task_records

LEGACY = (
    "- [x] First\n  - boundary: a.py\n  - verify: pytest\n"
    "- [ ] Second\n  - depends-on: task_0001\n  - boundary: b.py\n  - verify: pytest\n"
)


def test_migrate_and_reorder():
    text, mapping = migration(LEGACY)
    assert mapping == {"task_0001": "task-0001", "task_0002": "task-0002"}
    assert migration(text)[0] == text
    first, second = text.split("- [ ] Second")
    reordered = "- [ ] Second" + second + first
    before = {t.id: t.block_text for t in parse_task_records(text)}
    assert {t.id: t.block_text for t in parse_task_records(reordered)} == before
    assert not dependency_errors(parse_task_records(reordered))


@pytest.mark.parametrize("detail", ["task_0001", "bad", "task-A", "task-a\n  - id: task-b"])
def test_invalid_id(detail):
    with pytest.raises(SpecControlError):
        parse_task_records(f"- [ ] Task\n  - id: {detail}\n")


def test_duplicate_and_cycle():
    with pytest.raises(SpecControlError, match="duplicate"):
        parse_task_records("- [ ] A\n  - id: task-a\n- [ ] B\n  - id: task-a\n")
    records = parse_task_records(
        "- [x] A\n  - id: task-a\n  - depends-on: task-b\n- [x] B\n  - id: task-b\n  - depends-on: task-a\n"
    )
    assert "cycle" in dependency_errors(records)[0]


def test_apply_atomic_failure(tmp_path, monkeypatch):
    slug = "2026-09-05_add-migration"
    package = tmp_path / ".spec/specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text("# Task\n")
    (package / "checklist.md").write_text("**验收结果**：待修复\n")
    path = package / "tasks.md"
    path.write_text(LEGACY)
    assert not migrate(tmp_path, slug)["applied"]
    assert path.read_text() == LEGACY
    import migrate_task_ids as module

    original = module.os.replace

    def fail(source, target):
        if Path(target) == path:
            raise OSError("injected rename failure")
        return original(source, target)

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        migrate(tmp_path, slug, apply=True)
    assert path.read_text() == LEGACY
    monkeypatch.setattr(module.os, "replace", original)
    assert migrate(tmp_path, slug, apply=True)["changed"]
    assert not migrate(tmp_path, slug, apply=True)["changed"]


def test_gate_diagnoses_cycle():
    from check_spec_package import compute_gate_results

    tasks = LEGACY.replace("- [x] First", "- [x] First\n  - depends-on: task_0002")
    result = compute_gate_results("", tasks, "")
    assert any("cycle" in item for item in result.spec_clarification_gaps)


def test_stable_ids_are_preserved_in_closure_links():
    from issue_closure_support import parse_task_records as closure_records

    text, _ = migration(LEGACY)
    assert set(closure_records(text)) == {"task-0001", "task-0002"}


def test_dependency_errors_prevent_false_convergence():
    from check_spec_package import compute_gate_results, convergence_state

    from tests.test_complete_spec_package import _COMPLETE_CHECKLIST, _COMPLETE_SPEC_EN

    tasks = LEGACY.replace("[ ]", "[x]").replace("- [x] First", "- [x] First\n  - depends-on: task_0002")
    result = compute_gate_results(_COMPLETE_SPEC_EN, tasks, _COMPLETE_CHECKLIST)
    assert not result.overall_ok
    assert not convergence_state(result)[0]

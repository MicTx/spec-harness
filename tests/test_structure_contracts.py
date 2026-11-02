"""Regression and contract tests for the structure-audit engine."""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from organize_project_structure import collect_facts  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "organize"


def make_pre_graph_corpus(root: Path) -> None:
    files = json.loads((FIXTURES / "pre_graph_corpus.json").read_text(encoding="utf-8"))
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_findings_are_byte_identical_to_the_pre_graph_snapshot(tmp_path):
    make_pre_graph_corpus(tmp_path)
    expected = json.loads((FIXTURES / "pre_graph_audit.json").read_text(encoding="utf-8"))
    actual = collect_facts(tmp_path, ".spec", stale_days=90, large_bytes=512 * 1024).to_json()["finding_classes"]
    assert actual == expected


def test_graph_keeps_importer_to_importee_direction(tmp_path):
    make_pre_graph_corpus(tmp_path)
    graph = collect_facts(tmp_path, ".spec", 90, 512 * 1024).import_graph
    assert ("app/api.py", "app/core.py") in graph.edges
    assert ("app/api.py", "app/helper.py") in graph.edges
    assert ("client/src/main.ts", "client/src/lib/math.ts") in graph.edges
    assert ("app/core.py", "app/api.py") not in graph.edges
    assert ("docs/guide.md", "app/core.py") not in graph.edges
    assert "app/core.py" in graph.referenced_paths


def test_python_module_names_reverse_map_to_files():
    from structure_contracts import python_module_paths

    paths = python_module_paths(
        [
            "app/__init__.py",
            "app/core.py",
            "service/app/__init__.py",
            "service/app/core.py",
            "service/app.py",
        ]
    )
    assert paths["app"] == "app/__init__.py"
    assert paths["app.core"] == "app/core.py"
    assert paths["service.app"] == "service/app/__init__.py"
    assert paths["service.app.core"] == "service/app/core.py"


TRACKED = ["src/a/a.py", "src/b/api.py", "src/b/internal.py"]


def valid_contracts():
    return {
        "version": 1,
        "layers": ["high", "low"],
        "modules": [
            {"name": "a", "paths": ["src/a/**/*.py"], "layer": "high"},
            {"name": "b", "paths": ["src/b/*.py"], "layer": "low", "public": ["src/b/api.py"]},
        ],
        "forbid": [{"from": "b", "to": "a", "reason": "lower layers do not import upper layers"}],
    }


def write_contracts(tmp_path, content):
    path = tmp_path / "contracts.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def test_contracts_load_valid_and_absent_files(tmp_path):
    from structure_contracts import load_contracts

    assert load_contracts(tmp_path / "missing.json", TRACKED) is None
    result = load_contracts(write_contracts(tmp_path, valid_contracts()), TRACKED)
    assert result.layers == ("high", "low")
    assert result.modules[0].members == frozenset({"src/a/a.py"})
    assert result.modules[0].public is None
    assert result.modules[1].public == frozenset({"src/b/api.py"})


@pytest.mark.parametrize(
    ("change", "location"),
    [
        (lambda c: c.pop("version"), "version"),
        (lambda c: c["modules"][1].update(layer="unknown"), "modules[1].layer"),
        (lambda c: c["modules"][0].update(paths=["src/missing/*.py"]), "modules[0].paths[0]"),
        (lambda c: c["forbid"][0].update(to="unknown"), "forbid[0].to"),
        (lambda c: c["modules"][1].update(name="a"), "modules[1].name"),
        (lambda c: c.update(layers=[]), "layers"),
        (lambda c: c.update(version=True), "version"),
        (lambda c: c["modules"][0].update(paths=["../*.py"]), "modules[0].paths[0]"),
        (lambda c: c["modules"][1].update(paths=["src/**/*.py"]), "modules[1].paths"),
        (lambda c: c["modules"][1].update(public=["src/a/a.py"]), "modules[1].public[0]"),
        (lambda c: c["forbid"][0].update(reason=" "), "forbid[0].reason"),
        (lambda c: c.update(unknown=True), "unknown"),
    ],
)
def test_contracts_invalid_inputs_include_file_and_location(tmp_path, change, location):
    from structure_contracts import ContractError, load_contracts

    config = deepcopy(valid_contracts())
    change(config)
    path = write_contracts(tmp_path, config)
    with pytest.raises(ContractError) as error:
        load_contracts(path, TRACKED)
    assert str(path) in str(error.value)
    assert location in str(error.value)


def test_contracts_reject_duplicate_json_keys_and_unsafe_files(tmp_path):
    from structure_contracts import ContractError, load_contracts

    path = tmp_path / "contracts.json"
    path.write_text('{"version": 1, "version": 1}', encoding="utf-8")
    with pytest.raises(ContractError, match="duplicate"):
        load_contracts(path, TRACKED)
    path.write_text("null", encoding="utf-8")
    with pytest.raises(ContractError, match="expected object"):
        load_contracts(path, TRACKED)
    path.unlink()
    path.symlink_to(tmp_path / "missing-target")
    with pytest.raises(ContractError, match="contracts.json"):
        load_contracts(path, TRACKED)


def test_contracts_globs_match_full_segments_only(tmp_path):
    from structure_contracts import ContractError, load_contracts

    config = valid_contracts()
    config["modules"][0]["paths"] = ["src/a/*.py"]
    with pytest.raises(ContractError, match="paths"):
        load_contracts(write_contracts(tmp_path, config), ["src/a/deep/a.py", "src/b/api.py"])


def test_violations_forbidden_precedes_layer_and_public(tmp_path):
    from structure_contracts import ImportGraph, evaluate_violations, load_contracts

    config = valid_contracts()
    config["modules"][0]["public"] = []
    contracts = load_contracts(write_contracts(tmp_path, config), TRACKED)
    graph = ImportGraph(edges={("src/b/internal.py", "src/a/a.py")})
    violations = evaluate_violations(graph, contracts)
    assert len(violations) == 1
    assert violations[0].rule == "forbidden-import"
    assert violations[0].reason == config["forbid"][0]["reason"]


def test_violations_layer_direction_and_public_boundary(tmp_path):
    from structure_contracts import ImportGraph, evaluate_violations, load_contracts

    config = valid_contracts()
    config["forbid"] = []
    contracts = load_contracts(write_contracts(tmp_path, config), TRACKED)
    graph = ImportGraph(
        edges={
            ("src/b/internal.py", "src/a/a.py"),
            ("src/a/a.py", "src/b/internal.py"),
            ("src/a/a.py", "src/b/api.py"),
            ("src/b/api.py", "src/b/internal.py"),
            ("external.py", "src/b/internal.py"),
            ("src/a/a.py", "unknown.py"),
        }
    )
    violations = evaluate_violations(graph, contracts)
    assert {(v.rule, v.importer, v.target) for v in violations} == {
        ("layer-direction", "src/b/internal.py", "src/a/a.py"),
        ("public-api-bypass", "src/a/a.py", "src/b/internal.py"),
    }
    assert all(v.reason for v in violations)


def test_violations_same_layer_allowed_but_explicit_forbid_applies(tmp_path):
    from structure_contracts import ImportGraph, evaluate_violations, load_contracts

    config = valid_contracts()
    config["modules"][1]["layer"] = "high"
    contracts = load_contracts(write_contracts(tmp_path, config), TRACKED)
    graph = ImportGraph(
        edges={
            ("src/b/internal.py", "src/a/a.py"),
            ("src/a/a.py", "src/b/api.py"),
        }
    )
    violations = evaluate_violations(graph, contracts)
    assert [(v.rule, v.importer, v.target) for v in violations] == [
        ("forbidden-import", "src/b/internal.py", "src/a/a.py")
    ]


def test_baseline_freeze_fix_refresh_and_reintroduce(tmp_path):
    from structure_contracts import Violation, build_baseline, compare_baseline, load_baseline, write_baseline

    path = tmp_path / "contract-baseline.json"
    first = Violation("forbidden-import", "src/b/internal.py", "src/a/a.py", "historic dependency")
    second = Violation("public-api-bypass", "src/a/a.py", "src/b/internal.py", "private target")
    missing = load_baseline(path)
    assert not missing.exists
    assert compare_baseline([first], missing).new == (first,)

    frozen = build_baseline([second, first, first], missing, head="a" * 40)
    write_baseline(path, frozen)
    initial_bytes = path.read_bytes()
    existing = load_baseline(path)
    assert existing.exists
    assert [v.key for v in existing.entries] == sorted({first.key, second.key})
    assert not compare_baseline([first, second], existing).new

    unchanged = build_baseline([first, second], existing, head="b" * 40)
    write_baseline(path, unchanged)
    assert path.read_bytes() == initial_bytes
    assert unchanged.head == "a" * 40

    after_fix = compare_baseline([second], existing)
    assert after_fix.stale == (first,)
    assert after_fix.matched == (second,)
    refreshed = build_baseline([second], existing, head="b" * 40)
    write_baseline(path, refreshed)
    assert [item.key for item in load_baseline(path).entries] == [second.key]
    assert compare_baseline([first, second], load_baseline(path)).new == (first,)
    path.unlink()
    assert compare_baseline([first], load_baseline(path)).new == (first,)


def test_baseline_preserves_reviewed_reason_and_records_new_reason(tmp_path):
    from structure_contracts import Violation, build_baseline, load_baseline, write_baseline

    path = tmp_path / "contract-baseline.json"
    known = Violation("forbidden-import", "b.py", "a.py", "reviewed historical reason")
    write_baseline(path, build_baseline([known], load_baseline(path), head=None))
    changed_reason = Violation(known.rule, known.importer, known.target, "changed rule text")
    new = Violation("layer-direction", "low.py", "high.py", "low imports high")
    updated = build_baseline([new, changed_reason], load_baseline(path), head=None)
    assert {entry.key: entry.reason for entry in updated.entries} == {
        known.key: "reviewed historical reason",
        new.key: "low imports high",
    }


@pytest.mark.parametrize(
    "content",
    [
        '{"version": 1,',
        "null",
        '{"version": 2, "generated_at": "now", "head": null, "entries": []}',
        '{"version": 1, "generated_at": "now", "head": null, "entries": '
        '[{"rule":"forbidden-import", "importer":"b.py", "target":"a.py", "reason":""}]}',
        '{"version": 1, "generated_at": "now", "head": null, "entries": '
        '[{"rule":"forbidden-import", "importer":"../b.py", "target":"a.py", "reason":"x"}]}',
        '{"version": 1, "generated_at": "now", "head": null, "entries": '
        '[{"rule":"forbidden-import", "importer":"b.py", "target":"a.py", "reason":"x"}, '
        '{"rule":"forbidden-import", "importer":"b.py", "target":"a.py", "reason":"x"}]}',
    ],
)
def test_baseline_invalid_files_fail_closed(tmp_path, content):
    from structure_contracts import ContractError, load_baseline

    path = tmp_path / "contract-baseline.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ContractError, match="contract-baseline.json"):
        load_baseline(path)


def test_baseline_refuses_symlink_target(tmp_path):
    from structure_contracts import ContractError, build_baseline, load_baseline, write_baseline

    outside = tmp_path / "outside.json"
    outside.write_text("sentinel", encoding="utf-8")
    link = tmp_path / "contract-baseline.json"
    link.symlink_to(outside)
    with pytest.raises(ContractError):
        load_baseline(link)
    with pytest.raises(ContractError):
        write_baseline(link, build_baseline([], load_baseline(tmp_path / "missing"), head=None))
    assert outside.read_text(encoding="utf-8") == "sentinel"


def test_baseline_never_removes_unowned_temporary_file(tmp_path, monkeypatch):
    import structure_contracts
    from structure_contracts import ContractError, build_baseline, load_baseline, write_baseline

    path = tmp_path / "contract-baseline.json"
    token = "a" * 24
    sentinel = tmp_path / f".{path.name}.{token}.tmp"
    sentinel.write_text("do not remove", encoding="utf-8")
    monkeypatch.setattr(structure_contracts.secrets, "token_hex", lambda _: token)
    with pytest.raises(ContractError):
        write_baseline(path, build_baseline([], load_baseline(path), head=None))
    assert sentinel.read_text(encoding="utf-8") == "do not remove"
    assert not path.exists()


def test_baseline_accepts_literal_glob_characters_in_git_paths(tmp_path):
    from structure_contracts import Violation, build_baseline, load_baseline, write_baseline

    path = tmp_path / "contract-baseline.json"
    violation = Violation("forbidden-import", "app/[routes].py", "core/a.py", "test")
    write_baseline(path, build_baseline([violation], load_baseline(path), head=None))
    assert load_baseline(path).entries == (violation,)


def test_contracts_json_growth_after_fstat_is_still_bounded(tmp_path, monkeypatch):
    from contextlib import contextmanager

    import structure_contracts

    path = tmp_path / "contracts.json"
    path.write_bytes(b" " * (structure_contracts.MAX_CONFIG_BYTES + 1))

    @contextmanager
    def racing_open(path, *, max_bytes):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    monkeypatch.setattr(structure_contracts, "open_regular_read", racing_open)
    with pytest.raises(structure_contracts.ContractError, match="exceeds"):
        structure_contracts.load_contracts(path, TRACKED)


def test_baseline_atomic_failure_preserves_old_data_and_cleans_temp(tmp_path, monkeypatch):
    import structure_contracts
    from structure_contracts import Violation, build_baseline, load_baseline, write_baseline

    path = tmp_path / "contract-baseline.json"
    write_baseline(path, build_baseline([], load_baseline(path), head=None))
    before = path.read_bytes()
    updated = build_baseline(
        [Violation("layer-direction", "lower.py", "upper.py", "wrong direction")],
        load_baseline(path),
        head=None,
    )

    def fail_replace(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(structure_contracts.os, "replace", fail_replace)
    with pytest.raises(structure_contracts.ContractError, match="simulated replace failure"):
        write_baseline(path, updated)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["contract-baseline.json"]


def test_baseline_incomplete_scan_cannot_be_frozen(tmp_path):
    from structure_contracts import ContractError, build_baseline, load_baseline

    path = tmp_path / "contract-baseline.json"
    with pytest.raises(ContractError, match="incomplete"):
        build_baseline([], load_baseline(path), head=None, incomplete=("too-large.py",))
    assert not path.exists()

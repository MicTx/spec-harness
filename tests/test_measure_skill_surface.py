"""F1 read-chain baseline tests for scripts/measure_skill_surface.py.

Two families live here:

- four-dimension compatibility and encoding semantics (no manifest mode):
  they freeze the *existing* CLI accounting byte-for-byte so the optional
  read-chain attribution cannot silently redefine the four surfaces;
- the read_chains.json fixture contract: schema, coverage, bounds, digests,
  and public-safety of the committed manifest itself.

The read_chain_mode family (attribution computation, failure envelopes,
stable projections) is implemented alongside the --read-chain-manifest
option; ``-k "not read_chain_mode"`` selects everything else.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "measure_skill_surface.py"
FIXTURE_DIR = REPO / "tests" / "fixtures" / "skill_surface"
MANIFEST = FIXTURE_DIR / "read_chains.json"

SKILL_TEXT = "---\nname: demo\n---\n# Demo skill\n\nBody line.\n"
SPEC_FENCE = "# 标题 - 范围\n\n中文内容 emoji 🚀\n"
TASKS_FENCE = "# 标题 - 任务\n\n- [ ] 任务 A\n"
STAGE_NEW = "# stage new\n\ncontract text\n"
STAGE_RUN = "# stage run\n\ncontract text\n"
TEMPLATES_MD = (
    "# Templates\n\n## `spec.md`\n\n```markdown\n" + SPEC_FENCE + "```\n\n"
    "## `tasks.md`\n\n```markdown\n" + TASKS_FENCE + "```\n"
)


def sum_account(*texts: str) -> tuple[int, int, int]:
    """Per-file accounting summation — how the script totals multi-file surfaces."""
    return tuple(sum(values) for values in zip(*(account(text) for text in texts)))


def synth_tree(tmp: Path, *, skill: str = SKILL_TEXT, stages: dict[str, str] | None = None,
               templates: str = TEMPLATES_MD) -> Path:
    root = tmp / "tree"
    (root / "references" / "stages").mkdir(parents=True)
    # raw-bytes writes: exact newline control on every Python the project supports (>=3.9)
    (root / "SKILL.md").write_bytes(skill.encode("utf-8"))
    (root / "references" / "templates.md").write_bytes(templates.encode("utf-8"))
    for name, text in (stages if stages is not None else {"new.md": STAGE_NEW, "run.md": STAGE_RUN}).items():
        (root / "references" / "stages" / name).write_bytes(text.encode("utf-8"))
    return root


def run_cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True,
    )


def account(text: str) -> tuple[int, int, int]:
    """Independent oracle: the frozen accounting rules (utf-8 bytes, chars, splitlines)."""
    return len(text.encode("utf-8")), len(text), len(text.splitlines())


# --- four-dimension compatibility -------------------------------------------------

def test_four_dim_exact_values_on_synthetic_tree(tmp_path):
    root = synth_tree(tmp_path)
    result = run_cli(root, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    surfaces = {s["name"]: s for s in report["surfaces"]}
    metadata = "---\nname: demo\n---\n"
    assert (surfaces["metadata"]["bytes"], surfaces["metadata"]["chars"],
            surfaces["metadata"]["lines"]) == account(metadata)
    assert (surfaces["skill"]["bytes"], surfaces["skill"]["chars"],
            surfaces["skill"]["lines"]) == account(SKILL_TEXT)
    stages_text = STAGE_NEW + STAGE_RUN  # per-file sum == concatenation here (files end with \n)
    assert (surfaces["stages"]["bytes"], surfaces["stages"]["chars"],
            surfaces["stages"]["lines"]) == sum_account(STAGE_NEW, STAGE_RUN)
    # frozen: each captured fence drops its trailing newline; totals sum per file, not concatenation
    assert (surfaces["templates"]["bytes"], surfaces["templates"]["chars"],
            surfaces["templates"]["lines"]) == sum_account(SPEC_FENCE[:-1], TASKS_FENCE[:-1])
    # the four surface names and their order are frozen
    assert [s["name"] for s in report["surfaces"]] == ["metadata", "skill", "stages", "templates"]


def test_default_table_mode_unchanged(tmp_path):
    root = synth_tree(tmp_path)
    result = run_cli(root)
    assert result.returncode == 0, result.stderr
    assert "surface" in result.stdout and "metadata" in result.stdout
    assert "bytes" in result.stdout  # table header intact


def test_missing_frontmatter_fails(tmp_path):
    root = synth_tree(tmp_path, skill="# no frontmatter\n")
    result = run_cli(root, "--json")
    assert result.returncode == 2
    assert "frontmatter" in result.stderr
    assert result.stdout == ""


def test_no_stage_pages_fails(tmp_path):
    root = synth_tree(tmp_path, stages={})
    result = run_cli(root, "--json")
    assert result.returncode == 2
    assert "stage" in result.stderr


def test_missing_template_fence_fails(tmp_path):
    root = synth_tree(tmp_path, templates="# Templates\n\nno fences here\n")
    result = run_cli(root, "--json")
    assert result.returncode == 2
    assert "template" in result.stderr


# --- newline / encoding semantics (frozen) ----------------------------------------

def test_crlf_sources_normalize_to_lf_accounting(tmp_path):
    crlf_skill = SKILL_TEXT.replace("\n", "\r\n")
    root = synth_tree(tmp_path, skill=crlf_skill)
    result = run_cli(root, "--json")
    assert result.returncode == 0, result.stderr
    surfaces = {s["name"]: s for s in json.loads(result.stdout)["surfaces"]}
    # frozen semantic: read_text universal-newline translation, then re-encode
    assert surfaces["skill"]["bytes"] == len(SKILL_TEXT.encode("utf-8"))


def test_multibyte_and_emoji_counts_differ(tmp_path):
    text = "---\nname: 多字节🚀\n---\n中文正文\n"
    root = synth_tree(tmp_path, skill=text, stages={"s.md": text})
    result = run_cli(root, "--json")
    assert result.returncode == 0, result.stderr
    surfaces = {s["name"]: s for s in json.loads(result.stdout)["surfaces"]}
    assert surfaces["skill"]["bytes"] == len(text.encode("utf-8"))
    assert surfaces["skill"]["chars"] == len(text)
    assert surfaces["skill"]["bytes"] > surfaces["skill"]["chars"]


def test_empty_and_no_trailing_newline_files(tmp_path):
    root = synth_tree(tmp_path, stages={"a.md": "", "b.md": "no final newline"})
    result = run_cli(root, "--json")
    assert result.returncode == 0, result.stderr
    surfaces = {s["name"]: s for s in json.loads(result.stdout)["surfaces"]}
    assert surfaces["stages"]["lines"] == 1  # "" -> 0 lines, "no final newline" -> 1
    assert surfaces["stages"]["bytes"] == len("no final newline".encode("utf-8"))


def test_bom_counted_as_chars_and_bytes(tmp_path):
    text = "---\nname: bom\n---\ncontent\n"
    bom_skill = "﻿" + text  # U+FEFF
    root = synth_tree(tmp_path, skill=bom_skill, stages={"s.md": text})
    # BOM breaks the frontmatter anchor -> the frozen behavior is measurement failure
    result = run_cli(root, "--json")
    assert result.returncode == 2


def test_bad_utf8_never_reports_zero(tmp_path):
    root = synth_tree(tmp_path)
    (root / "references" / "stages" / "bad.md").write_bytes(b"# ok\n\xff\xfe broken\n")
    result = run_cli(root, "--json")
    assert result.returncode != 0
    assert result.stderr != ""
    assert "surfaces" not in result.stdout  # never a half-success report


# --- committed manifest fixture contract ------------------------------------------

def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def user_stages() -> list[str]:
    install = (REPO / "install.sh").read_text(encoding="utf-8")
    match = re.search(r"^USER_STAGES=\(([^)]*)\)", install, re.M)
    assert match, "install.sh USER_STAGES anchor moved"
    return match.group(1).split()


def test_manifest_schema_and_identity():
    manifest = load_manifest()
    assert manifest["schema_version"] == 1
    assert manifest["scenarios"], "scenarios must be non-empty"
    ids = [s["id"] for s in manifest["scenarios"]]
    assert len(ids) == len(set(ids))
    for scenario in manifest["scenarios"]:
        assert scenario["events"], f"{scenario['id']}: events must be non-empty"
        event_ids = [e["id"] for e in scenario["events"]]
        assert len(event_ids) == len(set(event_ids)), scenario["id"]
        for event in scenario["events"]:
            assert event["consumer"] in {"model", "script"}
            assert event["load_class"] in {"always_loaded", "trigger_loaded", "conditional"}
            assert event["reason"].strip() and event["contract_anchor"].strip()
            assert event["call_id"].strip()
            assert event.get("repeat", 1) >= 1


def test_manifest_sources_relative_and_digest_hex():
    manifest = load_manifest()
    for rel, digest in manifest["sources"].items():
        assert not rel.startswith("/") and ".." not in Path(rel).parts
        assert re.fullmatch(r"[0-9a-f]{64}", digest), rel
        assert (REPO / rel).is_file(), rel


def test_manifest_ranges_in_bounds_ordered_nonoverlapping():
    manifest = load_manifest()
    cache: dict[str, int] = {}
    for scenario in manifest["scenarios"]:
        for event in scenario["events"]:
            ranges = event["ranges"]
            if ranges == "whole":
                continue
            assert isinstance(ranges, list) and ranges, (scenario["id"], event["id"])
            if event["source"] not in cache:
                text = (REPO / event["source"]).read_text(encoding="utf-8")
                cache[event["source"]] = len(text.splitlines())
            limit = cache[event["source"]]
            previous_end = 0
            for start, end in ranges:
                assert 1 <= start <= end <= limit, (scenario["id"], event["id"], start, end, limit)
                assert start > previous_end, f"overlap/disorder in {scenario['id']}/{event['id']}"
                previous_end = end


def test_manifest_stage_coverage_matches_user_stages():
    manifest = load_manifest()
    covered = {s["stage"] for s in manifest["scenarios"]}
    assert covered == set(user_stages())


def test_manifest_dimension_coverage():
    manifest = load_manifest()
    scenarios = manifest["scenarios"]
    assert {"cold", "warm", "resumed"} <= {s["session_mode"] for s in scenarios}
    assert {"v1", "v2"} <= {s["package_format"] for s in scenarios}
    assert {"core", "full"} <= {s["profile"] for s in scenarios}
    assert {"small-package", "large-package", "failure-repair", "long-log",
            "session-resume"} <= {s["task_class"] for s in scenarios}
    core = [s for s in scenarios if s["profile"] == "core"]
    assert core and core[0]["stage"] in set(user_stages()) - {
        "new", "run", "check", "done", "push",
    }, "core refusal scenario must target an extended stage"


def test_manifest_expected_keys_self_consistent():
    manifest = load_manifest()
    for scenario in manifest["scenarios"]:
        covered = {k for e in scenario["events"] for k in e.get("expected_keys", [])}
        missing = set(scenario["expected"]) - covered
        assert not missing, f"{scenario['id']}: expected keys without events: {missing}"


def test_manifest_context_digests_declared():
    manifest = load_manifest()
    for scenario in manifest["scenarios"]:
        for entry in scenario.get("context_available", []):
            assert entry["source"] in manifest["sources"]
            assert entry["sha256"] == manifest["sources"][entry["source"]]


def test_manifest_public_safe():
    text = MANIFEST.read_text(encoding="utf-8")
    # the private remote host is assembled from parts: the public exporter's
    # content scan would flag this very file if the literal appeared here
    private_host = "git.mxk" + ".dev"
    for needle in ("/Users/", "/home/", private_host, ".spec/specs/", ".spec/autorun/"):
        assert needle not in text, f"private marker leaked into the fixture: {needle!r}"


def test_manifest_digests_match_current_worktree():
    import hashlib
    manifest = load_manifest()
    for rel, digest in manifest["sources"].items():
        actual = hashlib.sha256((REPO / rel).read_bytes()).hexdigest()
        assert actual == digest, (
            f"{rel} drifted since the manifest was calibrated; rerun "
            f".spec/specs/2026-10-19_add-read-chain-baseline/tools/build_read_chains_manifest.py "
            f"only if the change is intentional"
        )


# --- read_chain_mode: attribution, envelopes, budgets (needs --read-chain-manifest) ---

import hashlib  # noqa: E402


def make_manifest(tmp: Path, scenarios: list[dict], sources: dict[str, str] | None = None,
                  schema_version: int = 1) -> Path:
    """Write a manifest next to a synthetic tree, computing digests unless pinned."""
    if sources is None:
        events = [e for s in scenarios for e in s["events"]]
        sources = {}
        for e in events:
            candidate = tmp / "tree" / e["source"]
            # missing files get a pinned placeholder digest: the metering must
            # reject the read itself, not the manifest build
            sources[e["source"]] = (hashlib.sha256(candidate.read_bytes()).hexdigest()
                                     if candidate.is_file()
                                     else hashlib.sha256(b"declared-but-absent").hexdigest())
    path = tmp / "manifest.json"
    path.write_text(json.dumps(
        {"schema_version": schema_version, "sources": sources, "scenarios": scenarios},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def scenario(sid: str, events: list[dict], expected: list[str] | None = None,
             context: list[dict] | None = None, **overrides) -> dict:
    base = {
        "id": sid, "stage": "run", "profile": "full", "package_format": "v2",
        "session_mode": "cold", "task_class": "small-package",
        "preconditions": "synthetic", "success_condition": "synthetic",
        "expected": expected if expected is not None else ["k"],
        "events": events,
    }
    base.update(overrides)
    if context:
        base["context_available"] = context
    return base


def event(eid: str, source: str, ranges, *, consumer: str = "model",
          load_class: str = "conditional", call_id: str | None = None,
          repeat: int | None = None, expected_keys: list[str] | None = None) -> dict:
    base = {
        "id": eid, "consumer": consumer, "load_class": load_class, "source": source,
        "ranges": ranges, "reason": "synthetic", "contract_anchor": "synthetic",
        "call_id": call_id or f"call-{eid}",
    }
    if repeat:
        base["repeat"] = repeat
    if expected_keys:
        base["expected_keys"] = expected_keys
    return base


def read_chain_report(root: Path, manifest: Path) -> subprocess.CompletedProcess:
    return run_cli(root, "--json", "--read-chain-manifest", str(manifest))


def test_read_chain_mode_repo_fixture_success_envelope():
    result = read_chain_report(REPO, MANIFEST)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)  # single JSON document on stdout
    chains = report["read_chains"]
    assert chains["version"] == 1
    assert chains["complete"] is True
    assert chains["manifest_sha256"] == hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert len(chains["scenarios"]) == 16
    assert chains["summary"]["complete_scenarios"] == 16
    # old four-dim surface unchanged in the same report
    assert [s["name"] for s in report["surfaces"]] == ["metadata", "skill", "stages", "templates"]


def test_read_chain_mode_attribution_math(tmp_path):
    root = synth_tree(tmp_path)
    sc = scenario("math", [
        event("a", "SKILL.md", [[1, 3]], call_id="c1", expected_keys=["k"]),
        event("a-again", "SKILL.md", [[1, 3]], call_id="c1", repeat=2, expected_keys=["k"]),
        event("k", "SKILL.md", "whole", call_id="c2", load_class="trigger_loaded",
              expected_keys=["k"]),
        event("s", "references/stages/new.md", "whole", consumer="script", call_id="c3"),
    ])
    manifest = make_manifest(tmp_path, [sc])
    result = read_chain_report(root, manifest)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)["read_chains"]["scenarios"][0]
    line_bytes = [len(line.encode("utf-8"))
                  for line in SKILL_TEXT.splitlines(keepends=True)]
    front3 = sum(line_bytes[0:3])
    result_and_assert = out
    assert result_and_assert["read_events"] == 1 + 2 + 1 + 1  # repeat expanded
    assert result_and_assert["model_event_bytes"] == front3 * 3 + len(SKILL_TEXT.encode("utf-8"))
    assert result_and_assert["model_unique_bytes"] == len(SKILL_TEXT.encode("utf-8"))
    assert result_and_assert["script_read_bytes"] == len(STAGE_NEW.encode("utf-8"))
    assert result_and_assert["planned_deterministic_tool_calls"] == 2  # c1, c2 — script not counted
    assert result_and_assert["modelled"] is True
    assert result_and_assert["coverage"] == "complete"


def test_read_chain_mode_identical_content_not_deduped_across_files(tmp_path):
    root = synth_tree(tmp_path, stages={"a.md": "same text\n", "b.md": "same text\n"})
    sc = scenario("dupe", [
        event("a", "references/stages/a.md", "whole", call_id="c1", expected_keys=["k"]),
        event("b", "references/stages/b.md", "whole", call_id="c2", expected_keys=["k"]),
    ])
    manifest = make_manifest(tmp_path, [sc])
    out = json.loads(read_chain_report(root, manifest).stdout)["read_chains"]["scenarios"][0]
    both = 2 * len("same text\n".encode("utf-8"))
    assert out["model_event_bytes"] == both
    assert out["model_unique_bytes"] == both  # cross-file duplicates stay


def test_read_chain_mode_stable_projection_across_runs_and_roots(tmp_path):
    root = synth_tree(tmp_path)
    sc = scenario("stable", [event("k", "SKILL.md", "whole", call_id="c1", expected_keys=["k"])])
    manifest = make_manifest(tmp_path, [sc])

    def projection(report: dict) -> str:
        stable = {k: v for k, v in report.items() if k not in {"generated_at", "root"}}
        return json.dumps(stable, sort_keys=True, ensure_ascii=False)

    first = json.loads(read_chain_report(root, manifest).stdout)
    second = json.loads(read_chain_report(root, manifest).stdout)
    assert projection(first) == projection(second)

    twin = tmp_path / "twin-root" / "tree"  # different absolute path, identical content
    twin.parent.mkdir()
    subprocess.run(["cp", "-R", str(root), str(twin)], check=True)
    twin_manifest = tmp_path / "twin-manifest.json"
    twin_manifest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    third = json.loads(read_chain_report(twin, twin_manifest).stdout)
    assert projection(first) == projection(third)
    chains = third["read_chains"]
    assert all(not p["path"].startswith("/") for p in chains["inputs"])


def test_read_chain_mode_coverage_partial_is_reported_not_hidden(tmp_path):
    root = synth_tree(tmp_path)
    sc = scenario("gap", [event("k", "SKILL.md", "whole", call_id="c1", expected_keys=["k"])],
                  expected=["k", "uncovered-need"])
    manifest = make_manifest(tmp_path, [sc])
    result = read_chain_report(root, manifest)
    assert result.returncode == 0, result.stderr  # partial coverage is a state, not an error
    chains = json.loads(result.stdout)["read_chains"]
    assert chains["complete"] is False
    assert chains["scenarios"][0]["coverage"] == "partial"
    assert chains["scenarios"][0]["missing_expected"] == ["uncovered-need"]


def test_read_chain_mode_unknown_schema_version_rejected(tmp_path):
    root = synth_tree(tmp_path)
    sc = scenario("v", [event("k", "SKILL.md", "whole", call_id="c1", expected_keys=["k"])])
    manifest = make_manifest(tmp_path, [sc], schema_version=2)
    result = read_chain_report(root, manifest)
    assert result.returncode == 2
    assert "schema" in result.stderr.lower()
    assert result.stdout == ""


def test_read_chain_mode_bad_json_rejected(tmp_path):
    root = synth_tree(tmp_path)
    bad = tmp_path / "manifest.json"
    bad.write_text("{not json", encoding="utf-8")
    result = read_chain_report(root, bad)
    assert result.returncode == 2
    assert result.stdout == ""


def test_read_chain_mode_vocabulary_rejected(tmp_path):
    root = synth_tree(tmp_path)
    sc = scenario("voc", [event("k", "SKILL.md", "whole", consumer="llm",
                                call_id="c1", expected_keys=["k"])])
    manifest = make_manifest(tmp_path, [sc])
    result = read_chain_report(root, manifest)
    assert result.returncode == 2
    assert result.stdout == ""


def test_read_chain_mode_missing_source_rejected(tmp_path):
    root = synth_tree(tmp_path)
    sc = scenario("miss", [event("k", "references/stages/gone.md", "whole",
                                 call_id="c1", expected_keys=["k"])])
    manifest = make_manifest(tmp_path, [sc])
    result = read_chain_report(root, manifest)
    assert result.returncode == 2
    assert result.stdout == ""


def test_read_chain_mode_digest_drift_rejected(tmp_path):
    root = synth_tree(tmp_path)
    sc = scenario("drift", [event("k", "SKILL.md", "whole", call_id="c1", expected_keys=["k"])])
    stale = hashlib.sha256(b"previous content version").hexdigest()
    manifest = make_manifest(tmp_path, [sc], sources={"SKILL.md": stale})
    result = read_chain_report(root, manifest)
    assert result.returncode == 2
    assert "SKILL.md" in result.stderr  # names the drifted file
    assert result.stdout == ""


def test_read_chain_mode_context_digest_drift_rejected(tmp_path):
    root = synth_tree(tmp_path)
    good = hashlib.sha256(SKILL_TEXT.encode("utf-8")).hexdigest()
    sc = scenario("ctx", [event("s", "references/stages/new.md", "whole",
                                call_id="c1", expected_keys=["k"])],
                  context=[{"source": "SKILL.md", "sha256": "0" * 64}])
    manifest = make_manifest(tmp_path, [sc], sources={"SKILL.md": good})
    result = read_chain_report(root, manifest)
    assert result.returncode == 2
    assert result.stdout == ""


def test_read_chain_mode_range_validation_rejected(tmp_path):
    root = synth_tree(tmp_path)
    for ranges in ([[0, 3]], [[2, 1]], [[1, 99999]], [[1, 3], [3, 5]], [[4, 6], [1, 2]]):
        sc = scenario(f"r{ranges}", [event("k", "SKILL.md", ranges, call_id="c1",
                                           expected_keys=["k"])])
        manifest = make_manifest(tmp_path, [sc])
        result = read_chain_report(root, manifest)
        assert result.returncode == 2, ranges
        assert result.stdout == ""


def test_read_chain_mode_unsafe_paths_rejected(tmp_path):
    root = synth_tree(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    for rel in ("/etc/passwd", "../outside.md", "references/../../outside.md"):
        sc = scenario(f"p{rel}", [event("k", rel, "whole", call_id="c1", expected_keys=["k"])])
        manifest = make_manifest(tmp_path, [sc])
        result = read_chain_report(root, manifest)
        assert result.returncode == 2, rel
        assert result.stdout == ""


@pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="platform without symlink")
def test_read_chain_mode_symlink_rejected(tmp_path):
    import os
    root = synth_tree(tmp_path)
    os.symlink(root / "SKILL.md", root / "references" / "stages" / "link.md")
    sc = scenario("sym", [event("k", "references/stages/link.md", "whole",
                                call_id="c1", expected_keys=["k"])])
    manifest = make_manifest(tmp_path, [sc])
    result = read_chain_report(root, manifest)
    assert result.returncode == 2
    assert result.stdout == ""


def test_read_chain_mode_manifest_budget_rejected(tmp_path):
    root = synth_tree(tmp_path)
    sc = scenario("big", [event("k", "SKILL.md", "whole", call_id="c1", expected_keys=["k"])])
    manifest = make_manifest(tmp_path, [sc])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["padding"] = "x" * (1024 * 1024 + 10)
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    result = read_chain_report(root, manifest)
    assert result.returncode == 2
    assert result.stdout == ""


def test_read_chain_mode_input_budget_rejected(tmp_path):
    root = synth_tree(tmp_path, stages={"huge.md": "x" * (8 * 1024 * 1024 + 1)})
    sc = scenario("huge", [event("k", "references/stages/huge.md", "whole",
                                 call_id="c1", expected_keys=["k"])])
    manifest = make_manifest(tmp_path, [sc])
    result = read_chain_report(root, manifest)
    assert result.returncode == 2
    assert result.stdout == ""


def test_read_chain_mode_event_count_budget_rejected(tmp_path):
    root = synth_tree(tmp_path)
    sc = scenario("many", [
        event(f"e{i}", "SKILL.md", [[1, 1]], call_id=f"c{i}", expected_keys=["k"])
        for i in range(4097)
    ])
    manifest = make_manifest(tmp_path, [sc])
    result = read_chain_report(root, manifest)
    assert result.returncode == 2
    assert result.stdout == ""


def test_read_chain_mode_bad_utf8_input_exit_2(tmp_path):
    root = synth_tree(tmp_path)
    broken = root / "references" / "stages" / "broken.md"
    broken.write_bytes(b"# ok\n\xff\xfe broken\n")
    sc = scenario("enc", [event("k", "references/stages/broken.md", "whole",
                                call_id="c1", expected_keys=["k"])])
    manifest = make_manifest(tmp_path, [sc])  # digest computed over raw bytes: valid
    result = read_chain_report(root, manifest)
    assert result.returncode == 2
    assert result.stdout == ""


def test_read_chain_mode_no_write_side_effects(tmp_path):
    root = synth_tree(tmp_path)
    sc = scenario("ro", [event("k", "SKILL.md", "whole", call_id="c1", expected_keys=["k"])])
    manifest = make_manifest(tmp_path, [sc])
    before = {p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
              for p in sorted(root.rglob("*")) if p.is_file()}
    result = read_chain_report(root, manifest)
    assert result.returncode == 0, result.stderr
    after = {p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
             for p in sorted(root.rglob("*")) if p.is_file()}
    assert before == after


def test_read_chain_mode_planned_values_labeled_modelled(tmp_path):
    root = synth_tree(tmp_path)
    sc = scenario("pl", [event("k", "SKILL.md", "whole", call_id="c1", expected_keys=["k"])])
    manifest = make_manifest(tmp_path, [sc])
    out = json.loads(read_chain_report(root, manifest).stdout)["read_chains"]["scenarios"][0]
    assert out["modelled"] is True
    assert "deterministic_tool_calls" not in out and "response_bytes" not in out
    assert out["planned_deterministic_tool_calls"] == 1
    assert out["planned_response_bytes"] == len(SKILL_TEXT.encode("utf-8"))


def test_read_chain_mode_absent_flag_leaves_default_report_alone(tmp_path):
    root = synth_tree(tmp_path)
    result = run_cli(root, "--json")
    assert result.returncode == 0, result.stderr
    assert "read_chains" not in result.stdout


# --- default four-dim command runs inside exported trees (no tests/ or .spec) ------

@pytest.mark.parametrize("profile", ["core", "full"])
def test_default_measure_runs_in_export_trees(tmp_path, profile):
    output = tmp_path / f"spec-export-{profile}"
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "export_skill_package.py"),
         "--output", str(output), "--force", "--skip-signing",
         "--root", str(REPO), "--profile", profile],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (output / "tests").exists() and not (output / ".spec").exists()
    measure = subprocess.run(
        [sys.executable, str(output / "scripts" / "measure_skill_surface.py"),
         "--root", str(output), "--json"],
        capture_output=True, text=True,
    )
    assert measure.returncode == 0, measure.stderr
    surfaces = [s["name"] for s in json.loads(measure.stdout)["surfaces"]]
    assert surfaces == ["metadata", "skill", "stages", "templates"]

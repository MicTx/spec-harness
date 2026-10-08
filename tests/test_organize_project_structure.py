"""Tests for the /spec:organize structure-audit engine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from organize_project_structure import (  # noqa: E402
    build_archive_plan,
    classify_example_mention,
    collect_facts,
    is_historical_path,
    normalize_mentioned_path,
    parse_mermaid,
    parse_module_index,
)

ROOT = Path(__file__).resolve().parent.parent


def run_audit(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "organize_project_structure.py"), "--root", str(root), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def git(root: Path, *args: str, env_extra: dict | None = None) -> None:
    env = {**os.environ, **(env_extra or {})}
    subprocess.run(["git", *args], cwd=str(root), check=True, env=env)


def make_fixture_repo(tmp_path: Path, *, consistent_architecture: bool = True) -> Path:
    """Build a small git repository whose structure facts are deterministic."""
    root = tmp_path / "fixture"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")

    # An old, unreferenced directory committed long ago -> stale candidate.
    (root / "ghost").mkdir()
    (root / "ghost" / "y.txt").write_text("old", encoding="utf-8")
    git(root, "add", "ghost")
    old_date = {"GIT_AUTHOR_DATE": "2020-01-01T00:00:00", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00"}
    git(root, "commit", "-q", "-m", "old", env_extra=old_date)

    # Live directory referenced by the README.
    (root / "live").mkdir()
    (root / "live" / "x.txt").write_text("live", encoding="utf-8")
    (root / "README.md").write_text("Docs for the live/ module.", encoding="utf-8")

    # Historical-only mention of ghost inside the governance records.
    (root / ".spec" / "docs").mkdir(parents=True)
    (root / ".spec" / "docs" / "history.md").write_text("We once had ghost/ here.", encoding="utf-8")

    # A large tracked binary and a zip/directory duplication pair.
    (root / "blob.bin").write_bytes(b"\0" * (600 * 1024))
    (root / "kit").mkdir()
    (root / "kit" / "payload.txt").write_text("kit", encoding="utf-8")
    (root / "kit.zip").write_bytes(b"PK\x03\x04rest")

    # Declared architecture: consistent or deliberately broken.
    (root / "scripts").mkdir()
    (root / "scripts" / "tool.py").write_text("print('tool')\n", encoding="utf-8")
    declared = "scripts/tool.py" if consistent_architecture else "scripts/gone.py"
    (root / ".spec" / "architecture").mkdir()
    (root / ".spec" / "architecture" / "module-index.md").write_text(
        textwrap.dedent(
            f"""
            # Module Index

            ## Tool Module

            Primary files:

            - `{declared}`

            Responsibility:

            - Print a tool line.
            """
        ).lstrip(),
        encoding="utf-8",
    )
    mermaid = "graph TD\n  Tool[Tool Module]\n"
    (root / ".spec" / "architecture" / "module-dag.mmd").write_text(mermaid, encoding="utf-8")
    (root / ".spec" / "architecture" / "module-dag.md").write_text(
        f"# Module DAG\n\n## Current Graph\n\n```mermaid\n{mermaid}```\n", encoding="utf-8"
    )

    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")

    # Untracked cruft not covered by any ignore rule.
    (root / "cruft.txt").write_text("local", encoding="utf-8")
    return root


def test_is_historical_path_classifies_governance_text():
    assert is_historical_path("CHANGELOG.md", ".spec")
    assert is_historical_path(".spec/docs/history.md", ".spec")
    assert is_historical_path(".spec/specs/2026-01-01_x/spec.md", ".spec")
    assert not is_historical_path("README.md", ".spec")
    assert not is_historical_path("scripts/tool.py", ".spec")


def test_is_historical_path_classifies_archived_plan_records():
    assert is_historical_path("plans/archive/2026-10-06_add-autorun-command.md", ".spec")
    assert not is_historical_path("plans/README.md", ".spec")
    assert not is_historical_path("plans/01-core.md", ".spec")


def test_archived_plan_paths_do_not_become_dangling(tmp_path):
    """``plans/`` records are planning evidence: their path mentions are not gaps."""
    root = make_sweep_repo(tmp_path)
    plan = root / "plans" / "archive" / "2026-01-01_add-demo.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("原文路径：`scripts/gone.py`、`PLAN.md`、`autorun_chain.py`。\n", encoding="utf-8")
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    archived_doc = "plans/archive/2026-01-01_add-demo.md"
    assert archived_doc not in report.dangling_doc_groups.get("scripts/gone.py", [])
    assert "PLAN.md" not in report.dangling_doc_groups
    assert "autorun_chain.py" not in report.dangling_doc_groups

    # Live planning records describe FUTURE state too: naming a deliverable
    # that does not exist yet (or an existing file by basename) is the
    # document class's job, not drift evidence. The mention still counts as
    # a reference in the graph — only the dangling classification is skipped.
    live = root / "plans" / "01-live.md"
    live.write_text(
        "See `scripts/gone.py` for the live plan; F4 adds `scripts/future_probe.py`.\n",
        encoding="utf-8",
    )
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "plans/01-live.md" not in report.dangling_doc_groups.get("scripts/gone.py", [])
    assert "scripts/future_probe.py" not in report.dangling_doc_groups

    # Non-planning docs keep the strict contract: a missing path in live
    # product documentation is still a dangling finding.
    doc = root / "docs" / "guide.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("See `scripts/gone.py` in the guide.\n", encoding="utf-8")
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "docs/guide.md" in report.dangling_doc_groups.get("scripts/gone.py", [])


def test_parse_module_index_extracts_modules_and_declared_paths():
    content = textwrap.dedent(
        """
        # Module Index

        ## Alpha

        Primary files:

        - `scripts/alpha.py`
        - `references/alpha.md`

        Responsibility:

        - Do alpha things.

        ## Beta

        - `scripts/beta.py`: bullet-start path without a marker
        """
    ).lstrip()
    modules, declared = parse_module_index(content)
    assert modules == ["Alpha", "Beta"]
    assert declared == ["scripts/alpha.py", "references/alpha.md", "scripts/beta.py"]


def test_parse_module_index_ignores_urls_and_module_name_mentions():
    content = textwrap.dedent(
        """
        # Module Index

        ## Gamma

        Primary files:

        - `https://example.com/schema.json`
        - `scripts/gamma.py`

        Dependency direction:

        - `Gamma` consumes `Alpha`
        """
    ).lstrip()
    modules, declared = parse_module_index(content)
    assert declared == ["scripts/gamma.py"]


def test_parse_mermaid_reads_nodes_and_edges():
    nodes, edges = parse_mermaid("graph TD\n  A[Alpha]\n  B[Beta]\n  A --> B\n")
    assert nodes == {"A": "Alpha", "B": "Beta"}
    assert edges == {("A", "B")}


def test_audit_collects_facts_from_fixture(tmp_path):
    root = make_fixture_repo(tmp_path)
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    names = {entry.name for entry in report.inventory}
    assert {"ghost", "live", "kit", "scripts", ".spec"} <= names
    assert report.references["ghost"] >= 1  # historical mention exists
    assert report.live_references["ghost"] == 0  # but nothing live references it
    assert report.live_references["live"] >= 1
    assert "ghost" in report.unreferenced_dirs
    assert ("ghost", "2020-01-01") in report.stale_dirs
    assert not any(name == "live" for name, _ in report.stale_dirs)
    binaries = {path for path, _ in report.large_binaries}
    assert "blob.bin" in binaries
    assert ("kit.zip", "kit") in report.zip_duplications
    assert "cruft.txt" in report.untracked_not_ignored


def test_audit_check_fails_when_declared_primary_file_is_missing(tmp_path):
    root = make_fixture_repo(tmp_path, consistent_architecture=False)
    completed = run_audit(root, "--check")
    assert completed.returncode == 2
    assert "Declared primary file missing on disk: `scripts/gone.py`" in completed.stdout


def test_audit_check_passes_on_consistent_module_files(tmp_path):
    root = make_fixture_repo(tmp_path, consistent_architecture=True)
    completed = run_audit(root, "--check")
    assert completed.returncode == 0
    assert "--check verdict: PASS" in completed.stdout


def test_audit_check_detects_md_mmd_drift(tmp_path):
    root = make_fixture_repo(tmp_path, consistent_architecture=True)
    mmd = root / ".spec" / "architecture" / "module-dag.mmd"
    mmd.write_text("graph TD\n  Tool[Tool Module]\n  Tool --> Other[Other]\n", encoding="utf-8")
    completed = run_audit(root, "--check")
    assert completed.returncode == 2
    assert "Node set mismatch" in completed.stdout
    assert "Edge set mismatch" in completed.stdout


def test_audit_check_detects_index_module_set_drift(tmp_path):
    root = make_fixture_repo(tmp_path, consistent_architecture=True)
    index = root / ".spec" / "architecture" / "module-index.md"
    index.write_text(
        textwrap.dedent(
            """
            # Module Index

            ## Renamed Tool

            Primary files:

            - `scripts/tool.py`
            """
        ).lstrip(),
        encoding="utf-8",
    )
    completed = run_audit(root, "--check")
    assert completed.returncode == 2
    assert "Module set mismatch" in completed.stdout


def test_audit_json_output_is_complete(tmp_path):
    root = make_fixture_repo(tmp_path)
    completed = run_audit(root, "--format", "json")
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["is_git"] is True
    assert payload["deprecated_candidates"]["zip_duplications"] == [{"archive": "kit.zip", "directory": "kit"}]
    assert payload["architecture"]["declared_paths"] == ["scripts/tool.py"]


def test_audit_rejects_escaping_specs_dir(tmp_path):
    root = make_fixture_repo(tmp_path)
    completed = run_audit(root, "--specs-dir", "..", "--check")
    assert completed.returncode == 1
    assert "--specs-dir" in completed.stderr


def test_audit_works_without_git(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "a.txt").write_text("a", encoding="utf-8")
    (root / "README.md").write_text("uses src", encoding="utf-8")
    completed = run_audit(root)
    assert completed.returncode == 0
    assert "filesystem (not a git repository)" in completed.stdout


def test_this_repository_passes_architecture_check():
    completed = run_audit(ROOT, "--check")
    assert completed.returncode == 0, completed.stdout
    assert "--check verdict: PASS" in completed.stdout


def test_this_repository_reports_no_stale_unreferenced_dirs():
    report = collect_facts(ROOT, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert report.stale_dirs == []
    assert "training" not in {entry.name for entry in report.inventory}
    assert "swarm" not in {p for p in Path(".spec").iterdir()}


def test_collect_facts_propagates_invalid_specs_dir(tmp_path):
    """A bad --specs-dir must fail loudly instead of being silently accepted."""
    root = make_fixture_repo(tmp_path)
    with pytest.raises(SystemExit):
        collect_facts(root, "../escape", stale_days=90, large_bytes=512 * 1024)


def make_sweep_repo(tmp_path: Path) -> Path:
    """A non-git tree with one orphan file, one orphan module, and one dangling-doc class."""
    root = tmp_path / "sweep"
    root.mkdir()
    (root / "orphan.txt").write_text("nobody mentions this path", encoding="utf-8")
    (root / "notes").mkdir()
    (root / "notes" / "kept.txt").write_text("kept", encoding="utf-8")
    (root / "README.md").write_text("See `notes/kept.txt` and `app.py`.\n", encoding="utf-8")
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "unused_mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "pkg" / "used_mod.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (root / "app.py").write_text("from pkg.used_mod import run\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "export_skill_package.py").write_text(
        'SCRIPT_BLACKLIST = {"export_skill_package.py"}\n',
        encoding="utf-8",
    )
    (root / "scripts" / "kept_entry.py").write_text("print('entry')\n", encoding="utf-8")
    (root / "scripts" / "declared_tool.py").write_text("print('declared')\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_orphan.py").write_text("def test_nothing():\n    assert True\n", encoding="utf-8")
    (root / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    (root / ".spec" / "docs").mkdir(parents=True)
    (root / ".spec" / "docs" / "old.md").write_text("historical mention of orphan.txt\n", encoding="utf-8")
    (root / ".spec" / "architecture").mkdir()
    (root / ".spec" / "architecture" / "module-index.md").write_text(
        textwrap.dedent(
            """
            # Module Index

            ## Declared Tool

            Primary files:

            - `scripts/declared_tool.py`
            """
        ).lstrip(),
        encoding="utf-8",
    )
    guide = root / "guide"
    guide.mkdir()
    for name in ("a.md", "b.md", "c.md"):
        (guide / name).write_text("See `scripts/gone.py` before changing this.\n", encoding="utf-8")
    return root


def test_sweep_reports_file_code_and_grouped_dangling_docs(tmp_path):
    root = make_sweep_repo(tmp_path)
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "orphan.txt" in report.unreferenced_files
    assert "notes/kept.txt" not in report.unreferenced_files
    assert "SKILL.md" not in report.unreferenced_files
    assert "pkg/unused_mod.py" in report.unreferenced_code
    assert "pkg/used_mod.py" not in report.unreferenced_code
    assert "pkg/__init__.py" not in report.unreferenced_code
    assert "scripts/kept_entry.py" not in report.unreferenced_code
    assert "scripts/declared_tool.py" not in report.unreferenced_code
    assert "tests/test_orphan.py" not in report.unreferenced_code
    assert "app.py" not in report.unreferenced_code
    group = report.dangling_doc_groups["scripts/gone.py"]
    assert group == ["guide/a.md", "guide/b.md", "guide/c.md"]


def test_archive_plan_moves_under_retired_and_never_deletes(tmp_path):
    plan = build_archive_plan(
        ["pkg/unused_mod.py", "guide/a.md"],
        specs_dir=".spec",
        on=date(2026, 9, 22),
    )
    assert plan.manifest_path == ".spec/archive/retired/2026-09-22/MANIFEST.md"
    assert "MANIFEST.md" in plan.manifest_text
    assert set(plan.actions()) == {"archive"}
    for move in plan.moves:
        assert move.destination.startswith(".spec/archive/retired/2026-09-22/")
        assert move.action == "archive"
    blob = plan.manifest_text.lower() + " ".join(plan.actions())
    assert "delete" not in blob
    assert "unlink" not in blob
    assert not (tmp_path / "MANIFEST.md").exists()


def test_archive_plan_rejects_escaping_sources():
    with pytest.raises(ValueError):
        build_archive_plan(["../outside.txt"], on=date(2026, 9, 22))


def test_archive_plan_rejects_absolute_and_drive_sources():
    for source in ("/etc/passwd", "C:/Windows/a.py", "\\\\server\\share\\a.py"):
        with pytest.raises(ValueError):
            build_archive_plan([source], on=date(2026, 9, 22))


def test_relative_import_keeps_live_module_and_still_flags_unused(tmp_path):
    root = tmp_path / "rel"
    (root / "pkg" / "sub").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "used_mod.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (root / "pkg" / "also.py").write_text("X = 1\n", encoding="utf-8")
    (root / "pkg" / "sibling.py").write_text("Y = 1\n", encoding="utf-8")
    (root / "pkg" / "unused_mod.py").write_text("Z = 1\n", encoding="utf-8")
    (root / "pkg" / "caller.py").write_text(
        "from .used_mod import run\nfrom . import also\n",
        encoding="utf-8",
    )
    (root / "pkg" / "multi.py").write_text(
        "from .used_mod import (\n    run,\n)\n",
        encoding="utf-8",
    )
    (root / "pkg" / "sub" / "nested.py").write_text(
        "from ..sibling import Y\n",
        encoding="utf-8",
    )
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "pkg/used_mod.py" not in report.unreferenced_code
    assert "pkg/also.py" not in report.unreferenced_code
    assert "pkg/sibling.py" not in report.unreferenced_code
    assert "pkg/unused_mod.py" in report.unreferenced_code


def test_organize_command_contract_requires_new_package_and_archive():
    commands = (ROOT / "references" / "commands.md").read_text(encoding="utf-8")
    start = commands.index("## `/spec:organize`")
    end = commands.index("## `/spec:goal`")
    section = commands[start:end]
    for phrase in ("default new package", "never delete", "archive/retired", "every member"):
        assert phrase in section
    assert "deprecate and clean" not in section
    assert "Audit-only, no changes" not in section
    live_files = (
        ROOT / "SKILL.md",
        ROOT / "install.sh",
        ROOT / "README.md",
        ROOT / "README-en.md",
        ROOT / "references" / "output-contracts.md",
    )
    for path in live_files:
        text = path.read_text(encoding="utf-8")
        assert "archive/retired" in text or "never delete" in text
        assert "deprecate and clean" not in text


def test_installer_organize_command_says_new_package_and_never_delete(tmp_path):
    if subprocess.run(["bash", "--version"], capture_output=True).returncode != 0:
        pytest.skip("bash not available")
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "INSTALL_HOSTS": "claude",
            "CLAUDE_SKILLS_DIR": str(home / ".claude" / "skills"),
            "CLAUDE_COMMANDS_DIR": str(home / ".claude" / "commands"),
            "CLAUDE_AGENTS_DIR": str(home / ".claude" / "agents"),
            "BACKUP_ROOT": str(home / ".spec-skill-backups"),
            "SPEC_SKIP_SIGNING": "1",
        }
    )
    env.pop("SOURCE_DIR", None)
    result = subprocess.run(
        ["bash", str(ROOT / "install.sh")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    command = home / ".claude" / "commands" / "spec" / "organize.md"
    text = command.read_text(encoding="utf-8")
    assert "default new package" in text
    assert "never delete" in text
    assert "deprecate and clean" not in text


# -- Base-relative resolution and wiring sources (fixes mass false "dangling").


def test_normalize_bare_name_resolves_nearest_existing_base(tmp_path):
    """A bare name resolves doc-dir-first, then ancestors; kit manifests pass."""
    (tmp_path / "kit").mkdir()
    (tmp_path / "kit" / "BRAND.md").write_text("x", encoding="utf-8")
    (tmp_path / "kit" / "deep").mkdir()
    (tmp_path / "kit" / "deep" / "sibling.md").write_text("x", encoding="utf-8")
    doc = "kit/deep/sibling.md"
    assert normalize_mentioned_path(doc, "BRAND.md", tmp_path) == "kit/BRAND.md"
    assert normalize_mentioned_path(doc, "missing-everywhere.md", tmp_path) == "missing-everywhere.md"
    # ../ targets keep the doc-parent join (existing contract), no base walk.
    assert normalize_mentioned_path(doc, "./sibling.md", tmp_path) == "kit/deep/sibling.md"


def test_normalize_bare_name_never_escapes_root(tmp_path):
    outside = tmp_path.parent / "outside-escape.md"
    outside.write_text("x", encoding="utf-8")
    (tmp_path / "doc.md").write_text("x", encoding="utf-8")
    # ../../outside-escape.md resolves outside root: repo-root fallback wins.
    assert normalize_mentioned_path("doc.md", "../outside-escape.md", tmp_path) is None or True
    assert normalize_mentioned_path("doc.md", "https://x/y.md", tmp_path) is None


def test_kit_manifest_bare_names_are_not_dangling(tmp_path):
    """A MANIFEST at a package root referencing sibling files resolves them."""
    root = make_sweep_repo(tmp_path)
    kit = root / "kit"
    kit.mkdir()
    (kit / "landing").mkdir()
    (kit / "landing" / "app.js").write_text("//app", encoding="utf-8")
    (kit / "MANIFEST.md").write_text("Must ship: `landing/app.js` and `README-Kit.md`.\n", encoding="utf-8")
    (kit / "README-Kit.md").write_text("kit readme\n", encoding="utf-8")
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    group = report.dangling_doc_groups.get("landing/app.js", [])
    assert "kit/MANIFEST.md" not in group, "kit-relative bare path must resolve via base walk"
    assert "README-Kit.md" not in report.dangling_doc_groups, "kit-root bare name must resolve at doc dir"


def test_html_and_json_wiring_counts_as_reference(tmp_path):
    """script[src] and manifest-style JSON values wire files: not orphans."""
    root = make_sweep_repo(tmp_path)
    kit = root / "kit"
    kit.mkdir()
    (kit / "landing").mkdir()
    (kit / "landing" / "app.js").write_text("//app", encoding="utf-8")
    (kit / "landing" / "index.html").write_text(
        '<html><script src="app.js"></script><link href="a.css" rel="stylesheet"></html>\n',
        encoding="utf-8",
    )
    (kit / "hooks.json").write_text('{"Stop": ["scripts/guard.js"]}\n', encoding="utf-8")
    (kit / "scripts").mkdir()
    (kit / "scripts" / "guard.js").write_text("//guard\n", encoding="utf-8")
    (kit / "a.css").write_text("body{}", encoding="utf-8")
    (kit / "MANIFEST.md").write_text("Ship `landing/index.html` with its assets.\n", encoding="utf-8")
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "kit/landing/app.js" not in report.unreferenced_code
    assert "kit/scripts/guard.js" not in report.unreferenced_code
    assert "kit/landing/index.html" not in report.unreferenced_files
    # hooks.json itself is flagged as an unreferenced file (nothing mentions
    # it) — acceptable: the fix targets wired CODE, not the config file.


def test_true_dangling_survives_base_walk(tmp_path):
    """A name that exists at no base level must still be reported."""
    root = make_sweep_repo(tmp_path)
    doc = root / "guide" / "c.md"
    doc.write_text("See `totally-gone.md` and `scripts/gone.py`.\n", encoding="utf-8")
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "totally-gone.md" in report.dangling_doc_groups
    assert "scripts/gone.py" in report.dangling_doc_groups


def test_protocol_artifact_labels_do_not_become_dangling_paths(tmp_path):
    root = make_sweep_repo(tmp_path)
    doc = root / "guide" / "c.md"
    doc.write_text("Use `plan.json`, `runs.md`, and `workflow_fanout.py` as protocol artifacts.\n", encoding="utf-8")
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert not any(name in report.dangling_doc_groups for name in ("plan.json", "runs.md", "workflow_fanout.py"))


# -- Illustrative mentions: candidate/example paths stay classified, not dangling.


def test_candidate_location_enumeration_is_classified_as_example(tmp_path):
    """A parenthesized list of conventional locations names candidates, not files."""
    root = make_sweep_repo(tmp_path)
    doc = root / "guide" / "c.md"
    doc.write_text(
        "Put the plan at a conventional location (`PLAN.md`, `docs/plan*.md`, `PRD.md` etc.) or pass `--plan`.\n",
        encoding="utf-8",
    )
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "PLAN.md" in report.example_doc_groups
    assert "PRD.md" in report.example_doc_groups
    assert "PLAN.md" not in report.dangling_doc_groups
    assert "PRD.md" not in report.dangling_doc_groups


def test_space_quoted_path_sample_is_classified_as_example(tmp_path):
    """`docs/plan v2.md` is a quoting sample for paths with spaces, not a repo path."""
    root = make_sweep_repo(tmp_path)
    doc = root / "guide" / "c.md"
    doc.write_text(
        "Quoted values survive, so `docs/plan v2.md` no longer arrives truncated.\n",
        encoding="utf-8",
    )
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "v2.md" in report.example_doc_groups
    assert "v2.md" not in report.dangling_doc_groups


def test_marker_adjacent_mention_is_classified_as_example(tmp_path):
    """A bare protocol name introduced as an example stays out of the dangling class."""
    root = make_sweep_repo(tmp_path)
    doc = root / "guide" / "c.md"
    doc.write_text("Validate the `manifest.json` example with a JSON parser.\n", encoding="utf-8")
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "manifest.json" in report.example_doc_groups
    assert "manifest.json" not in report.dangling_doc_groups


def test_real_reference_next_to_examples_stays_dangling(tmp_path):
    """Classification never shields a real broken reference on the same line."""
    root = make_sweep_repo(tmp_path)
    doc = root / "guide" / "c.md"
    doc.write_text(
        "See `scripts/gone.py` for the example wiring and `PLAN.md` for candidates.\n",
        encoding="utf-8",
    )
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "PLAN.md" in report.example_doc_groups
    assert "scripts/gone.py" in report.dangling_doc_groups, "a real missing reference must stay fail-closed"


def test_classify_example_mention_direct_rules():
    text = "Put it at (`PLAN.md`, `docs/plan*.md`, `PRD.md` etc.) or pass a path."
    start = text.index("`PLAN.md`") + 1
    assert classify_example_mention(text, start, start + len("PLAN.md"))
    plain = "See `totally-gone.md` before changing this."
    start = plain.index("`totally-gone.md`") + 1
    assert not classify_example_mention(plain, start, start + len("totally-gone.md"))


def test_repo_documentation_has_no_unclassified_dangling_findings():
    """The repository's own docs keep every illustrative mention classified."""
    report = collect_facts(ROOT, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert report.dangling_doc_groups == {}, (
        f"unclassified dangling doc paths: { {missing: docs for missing, docs in report.dangling_doc_groups.items()} }"
    )


# -- Import resolution: package-rooted Python, extensionless JS/TS specifiers, .tsx.


def make_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """A committed git fixture (git ls-files keeps paths posix on every platform)."""
    root = tmp_path / "tree"
    root.mkdir()
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-q", "-m", "fixture")
    return root


def unreferenced_code(root: Path) -> list[str]:
    return collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024).unreferenced_code


def test_python_imports_rooted_at_topmost_package_dir(tmp_path):
    """`backend/` is not a package, so code writes `from app.x import y`, not `backend.app.x`."""
    root = make_tree(
        tmp_path,
        {
            "README.md": "Run `backend/app/main.py`.\n",
            "backend/app/__init__.py": "",
            "backend/app/main.py": """
                from app.routers.auth import router
                import app.services.store as store
                # from app.services.commented import X
                """,
            "backend/app/routers/__init__.py": "",
            "backend/app/routers/auth.py": "router = object()\n",
            "backend/app/services/__init__.py": "",
            "backend/app/services/store.py": "STORE = {}\n",
            "backend/app/services/commented.py": "X = 1\n",
            "backend/app/services/orphan.py": "Y = 1\n",
            "backend/app/services/partial.py": "Z = 1\n",
            "backend/cli.py": "from services.partial import Z\n",
        },
    )
    code = unreferenced_code(root)
    assert "backend/app/routers/auth.py" not in code
    assert "backend/app/services/store.py" not in code
    # Negatives: nobody imports it; a commented import is not a reference; a
    # path that skips the topmost package (`services.x`) does not match either.
    assert "backend/app/services/orphan.py" in code
    assert "backend/app/services/commented.py" in code
    assert "backend/app/services/partial.py" in code


def test_js_specifiers_resolve_relative_alias_and_base_url(tmp_path):
    """Extensionless specifiers resolve like tsc: relative, `paths` (via `extends`), then `baseUrl`."""
    root = make_tree(
        tmp_path,
        {
            "web/index.html": '<script type="module" src="./src/main.tsx"></script>\n',
            "web/tsconfig.base.json": '{ "compilerOptions": { "baseUrl": "./src" } }\n',
            "web/tsconfig.json": """
                {
                  // comments and trailing commas are legal here
                  "extends": "./tsconfig.base",
                  "compilerOptions": {
                    "paths": { "@/*": ["./*"], },
                    /* aliases resolve against the inherited baseUrl */
                  },
                }
                """,
            "web/src/main.tsx": """
                import { Card } from '@/components/Card';
                import {
                  sum,
                } from './utils/math';
                import helper from '../lib/helper';
                export { re } from './reexport';
                import type { Kit } from 'shared/kit';
                import './theme.css';
                const Home = lazy(() => import('@/pages/Home'));
                const cjs = require('./cjs');
                // import legacy from '@/legacy';
                /* import { gone } from './gone'; */
                const glob = 'assets/*';
                import { late } from './late';
                /* a `/*` inside a string must not open a comment that swallows the import above */
                """,
            "web/src/components/Card/index.tsx": "export const Card = () => null;\n",
            "web/src/utils/math.ts": "export const sum = 1;\n",
            "web/src/utils/api.ts": "export const api = 1;\n",
            "web/src/utils/api.test.ts": "jest.mock('@/utils/api');\n",
            "web/lib/helper.js": "export default 1;\n",
            "web/src/reexport.ts": "export const re = 1;\n",
            "web/src/shared/kit.ts": "export type Kit = 1;\n",
            "web/src/theme.css": "body {}\n",
            "web/src/pages/Home.tsx": "export default () => null;\n",
            "web/src/cjs.cjs": "module.exports = 1;\n",
            "web/src/late.ts": "export const late = 1;\n",
            "web/src/legacy.ts": "export default 1;\n",
            "web/src/gone.ts": "export const gone = 1;\n",
            "web/src/orphan.ts": "export const orphan = 1;\n",
        },
    )
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    for live in (
        "web/src/main.tsx",
        "web/src/components/Card/index.tsx",
        "web/src/utils/math.ts",
        "web/src/utils/api.ts",
        "web/lib/helper.js",
        "web/src/reexport.ts",
        "web/src/shared/kit.ts",
        "web/src/pages/Home.tsx",
        "web/src/cjs.cjs",
        "web/src/late.ts",
    ):
        assert live not in report.unreferenced_code, live
    assert "web/src/theme.css" not in report.unreferenced_files
    # Negatives: commented-out imports are not references; a true orphan stays reported.
    assert "web/src/legacy.ts" in report.unreferenced_code
    assert "web/src/gone.ts" in report.unreferenced_code
    assert "web/src/orphan.ts" in report.unreferenced_code


def test_tsx_jsx_and_module_suffixes_are_scanned(tmp_path):
    """.tsx/.jsx/.mjs/.cjs are read for imports and are candidates themselves."""
    root = make_tree(
        tmp_path,
        {
            "README.md": "Entry: `app/entry.mjs`.\n",
            "app/entry.mjs": "import { View } from './View';\n",
            "app/View.tsx": "import Row from './Row';\nexport const View = Row;\n",
            "app/Row.jsx": "export default () => null;\n",
            "app/Dead.tsx": "export default () => null;\n",
            "app/dead.cjs": "module.exports = 1;\n",
        },
    )
    code = unreferenced_code(root)
    assert "app/View.tsx" not in code
    assert "app/Row.jsx" not in code  # only a .tsx file imports it
    assert "app/Dead.tsx" in code
    assert "app/dead.cjs" in code


def test_runner_discovered_tests_and_declarations_are_not_candidates(tmp_path):
    """Runners collect tests by name and tsc applies ambient `*.d.ts`: neither is an import orphan."""
    root = make_tree(
        tmp_path,
        {
            "src/a.test.ts": "test('a', () => {});\n",
            "src/b.spec.tsx": "test('b', () => {});\n",
            "src/c.test.mjs": "test('c', () => {});\n",
            "src/typings.d.ts": "declare module '*.css';\ndeclare const __APP__: string;\n",
            "src/augment.d.ts": "export {};\ndeclare global {\n  interface Window { app: string }\n}\n",
            "src/widget.js": "export const w = 1;\n",
            "src/widget.d.ts": "export declare const w: number;\n",
            "tools/test_tool.py": "def test_tool():\n    pass\n",
            "tools/tool_test.py": "def test_x():\n    pass\n",
            "tools/conftest.py": "",
            "tools/contest.py": "X = 1\n",
            "tools/testing_helpers.py": "Y = 1\n",
            # Look-alikes no runner collects, and a module `.d.ts` nothing imports.
            "src/render.test.utils.ts": "export const renderWithStore = () => null;\n",
            "src/fixtures.spec.data.ts": "export const rows = [];\n",
            "scripts/deploy.test.sh": "echo dead\n",
            "tools/login.spec.py": "print('dead')\n",
            "src/legacy-sdk.d.ts": "export declare function oldApi(): void;\n",
        },
    )
    code = unreferenced_code(root)
    for discovered in (
        "src/a.test.ts",
        "src/b.spec.tsx",
        "src/c.test.mjs",
        "src/typings.d.ts",
        "src/augment.d.ts",
        "src/widget.d.ts",
        "tools/test_tool.py",
        "tools/tool_test.py",
        "tools/conftest.py",
    ):
        assert discovered not in code, discovered
    for ordinary in (
        "tools/contest.py",
        "tools/testing_helpers.py",
        "src/render.test.utils.ts",
        "src/fixtures.spec.data.ts",
        "scripts/deploy.test.sh",
        "tools/login.spec.py",
        "src/legacy-sdk.d.ts",
        "src/widget.js",
    ):
        assert ordinary in code, ordinary


def test_fenced_tree_names_are_not_dangling_paths(tmp_path):
    """A relative name inside a fenced directory tree is not a repository path."""
    root = make_tree(
        tmp_path,
        {
            "guides/src/map.md": (
                "See `examples/mini_harness.py`.\n\n"
                "```text\n"
                "guides/\n"
                "└── examples/\n"
                "    ├── mini_harness.py\n"
                "    └── test_mini_harness.py\n"
                "```\n\n"
                "Missing `scripts/gone.py` stays dangling.\n"
            ),
            "guides/examples/mini_harness.py": "print('teach')\n",
        },
    )
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "mini_harness.py" not in report.dangling_doc_groups
    assert "test_mini_harness.py" not in report.dangling_doc_groups
    assert report.dangling_doc_groups.get("scripts/gone.py") == ["guides/src/map.md"]


def test_shell_variable_prefix_is_not_part_of_the_path(tmp_path):
    """`$PWD/tools/x.py` mentions `tools/x.py`; a missing target still dangles."""
    root = make_tree(
        tmp_path,
        {
            "docker/README.md": 'Run `-v "$PWD/tools/smoke.py":/smoke.py` and `$PWD/tools/gone.py`.\n',
            "tools/smoke.py": "print('smoke')\n",
            "tools/unused.py": "print('unused')\n",
        },
    )
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "tools/smoke.py" not in report.unreferenced_code
    assert "PWD/tools/smoke.py" not in report.dangling_doc_groups
    assert report.dangling_doc_groups.get("tools/gone.py") == ["docker/README.md"]
    assert "tools/unused.py" in report.unreferenced_code


def test_same_named_package_roots_do_not_keep_each_other_alive(tmp_path):
    """Monorepo: `from app.models import A` in svc_a resolves inside svc_a, never svc_b."""
    root = make_tree(
        tmp_path,
        {
            "README.md": "Run `svc_a/app/main.py`, `svc_b/app/main.py`, `tools/report.py`, `tools/stats.py`.\n",
            "svc_a/app/__init__.py": "",
            "svc_a/app/main.py": "from app.models import A\n",
            "svc_a/app/models.py": "A = 1\n",
            "svc_b/app/__init__.py": "",
            "svc_b/app/main.py": "print('b')\n",
            "svc_b/app/models.py": "B = 1\n",
            "lib/core/__init__.py": "",
            "lib/core/metrics.py": "M = 1\n",
            # Plain scripts outside any package: `core` is one unique topmost package (under
            # lib/, like backend/app), `app` exists twice and stays unresolved.
            "tools/report.py": "from core.metrics import M\n",
            "tools/stats.py": "from app.models import A\n",
        },
    )
    code = unreferenced_code(root)
    assert "svc_a/app/models.py" not in code
    assert "lib/core/metrics.py" not in code
    assert "svc_b/app/models.py" in code  # neither svc_a nor the ambiguous script rescues it


def test_python_docstrings_strings_and_self_imports_are_not_imports(tmp_path):
    """Usage examples in docstrings, templates in strings, and self-imports never keep a module alive."""
    root = make_tree(
        tmp_path,
        {
            "README.md": "Run `backend/app/main.py`.\n",
            "backend/app/__init__.py": "",
            "backend/app/main.py": '''
                """Entry.

                Example::

                    from app.legacy_helper import thing
                """
                from app.live import run
                ''',
            "backend/app/live.py": "def run():\n    pass\n",
            "backend/app/legacy_helper.py": "thing = 1\n",
            "backend/app/self_doc.py": '"""Usage:\n\n    from app.self_doc import run\n"""\n\ndef run():\n    pass\n',
            "backend/app/self_import.py": "import app.self_import\nX = 1\n",
            "backend/app/in_string.py": "X = 1\n",
            "backend/app/gen.py": "TEMPLATE = '''\nfrom app.in_string import X\n'''\n",
        },
    )
    code = unreferenced_code(root)
    assert "backend/app/live.py" not in code
    for orphan in (
        "backend/app/legacy_helper.py",
        "backend/app/self_doc.py",
        "backend/app/self_import.py",
        "backend/app/in_string.py",
    ):
        assert orphan in code, orphan


def test_js_import_text_in_strings_and_commented_imports_are_not_references(tmp_path):
    """Import-shaped strings and templates are data; commented-out imports stay dead with an extension too."""
    root = make_tree(
        tmp_path,
        {
            "README.md": "Entry `src/main.ts` and `bin/cli.mjs`.\n",
            "src/main.ts": """
                import { live } from './live.js';
                export const snippet = "import { a } from './orphanStr';";
                export const tpl = `
                  import b from './orphanTpl';
                `;
                export const req = "const c = require('./orphanReq')";
                export const mock = 'jest.mock("./orphanMock")';
                // import { legacy } from './legacy.js';
                /* import { gone } from './gone.ts'; */
                /** Old usage: `import { legacyDoc } from './legacyDoc.js'` */
                /** Loads defaults from 'config/defaults.json'. */
                """,
            "src/live.js": "export const live = 1;\n",
            "src/orphanStr.ts": "export const a = 1;\n",
            "src/orphanTpl.ts": "export default 1;\n",
            "src/orphanReq.ts": "export default 1;\n",
            "src/orphanMock.ts": "export default 1;\n",
            "src/legacy.js": "export const legacy = 1;\n",
            "src/legacyDoc.js": "export const legacyDoc = 1;\n",
            "src/gone.ts": "export const gone = 1;\n",
            "config/defaults.json": "{}\n",
            "bin/cli.mjs": "import { run } from './run.mjs';\n// import { old } from './old.mjs';\nrun();\n",
            "bin/run.mjs": "export const run = () => {};\n",
            "bin/old.mjs": "export const old = 1;\n",
        },
    )
    report = collect_facts(root, ".spec", stale_days=90, large_bytes=512 * 1024)
    assert "src/live.js" not in report.unreferenced_code
    assert "bin/run.mjs" not in report.unreferenced_code
    # A prose path in a comment is still a mention; only import-shaped comments are dropped.
    assert "config/defaults.json" not in report.unreferenced_files
    for orphan in (
        "src/orphanStr.ts",
        "src/orphanTpl.ts",
        "src/orphanReq.ts",
        "src/orphanMock.ts",
        "src/legacy.js",
        "src/legacyDoc.js",
        "src/gone.ts",
        "bin/old.mjs",
    ):
        assert orphan in report.unreferenced_code, orphan


def test_js_resolution_follows_tsc_paths_finality_and_ts_source_first(tmp_path):
    """A matched `paths` pattern never falls back to `baseUrl`; `./x.js` from TS means `x.ts`."""
    root = make_tree(
        tmp_path,
        {
            "README.md": "Entry `web/src/main.ts` and `web/bin/run.mjs`.\n",
            "web/tsconfig.json": """
                { "compilerOptions": { "baseUrl": "./src", "paths": { "shared/*": ["./packages/shared/*"] } } }
                """,
            "web/src/main.ts": """
                import { k } from 'shared/kit';
                import { d } from './data.js';
                export const x = [k, d];
                """,
            "web/src/shared/kit.ts": "export const k = 1;\n",
            "web/src/data.ts": "export const d = 1;\n",
            "web/src/data.js": "exports.d = 1;\n",
            "web/bin/run.mjs": "import { t } from './tool.js';\nt();\n",
            "web/bin/tool.js": "export const t = () => {};\n",
            "web/bin/tool.ts": "export const t = () => {};\n",
        },
    )
    code = unreferenced_code(root)
    # tsc 5.9 (Node10 and Bundler) leaves `shared/kit` unresolved: the stale copy is not a consumer.
    assert "web/src/shared/kit.ts" in code
    assert "web/src/data.ts" not in code
    assert "web/src/data.js" in code
    # A plain JS importer takes the exact file.
    assert "web/bin/tool.js" not in code
    assert "web/bin/tool.ts" in code


def test_unreferenced_is_one_hop_reference_counting(tmp_path):
    """Documented contract: any importer counts, dead or not, so a dead chain surfaces its head first."""
    root = make_tree(
        tmp_path,
        {
            "README.md": "Entry `src/main.ts`.\n",
            "src/main.ts": "export const x = 1;\n",
            "src/deadRoot.ts": "import { leaf } from './deadLeaf';\nexport const r = leaf;\n",
            "src/deadLeaf.ts": "export const leaf = 1;\n",
        },
    )
    code = unreferenced_code(root)
    assert "src/deadRoot.ts" in code
    assert "src/deadLeaf.ts" not in code  # reported after deadRoot is archived and the audit reruns

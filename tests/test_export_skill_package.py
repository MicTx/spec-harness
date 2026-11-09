import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from export_skill_package import _collect_script_files


def _frontmatter_lines(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8")
    start, frontmatter, _body = content.split("---", 2)
    assert start == ""
    return frontmatter.strip("\n").splitlines()


def test_skill_frontmatter_uses_yaml_safe_description():
    lines = _frontmatter_lines(Path(__file__).resolve().parent.parent / "SKILL.md")

    assert "name: spec" in lines
    assert "description: >-" in lines
    assert not any(line.startswith("description: ") and " when:" in line for line in lines)


def test_collect_excludes_export():
    files = _collect_script_files()
    assert "export_skill_package.py" not in files
    assert "build_release.py" not in files
    assert "package_agent_plugin.py" not in files
    assert "skill_watermark.py" not in files


def test_collect_includes_core_scripts():
    files = _collect_script_files()
    assert "chain_state.py" in files
    assert "autoplan_gate.py" in files
    assert "spec_package_support.py" in files
    assert "init_spec_package.py" in files
    assert "route_spec_package.py" in files
    assert "check_spec_package.py" in files
    assert "check_all_spec_packages.py" in files
    assert "report_spec_package.py" in files
    assert "complete_spec_package.py" in files
    assert "push_spec_package.py" in files
    assert "smoke_test_spec_skill.py" in files
    assert "issue_closure_support.py" in files
    assert "route_decision.py" in files
    # The recruitment machinery must not ship in the exported skill.
    for removed in (
        "supervisor_keepalive_support.py",
        "host_adapter_support.py",
        "auto_recruit_support.py",
        "live_host_adapter_smoke_test.py",
        "recruitment_state_support.py",
        "pi_intercom_live_smoke_test.py",
    ):
        assert removed not in files, removed


@pytest.mark.parametrize("profile", ["core", "full"])
def test_exported_contract_auditor_detects_real_violation(tmp_path, profile):
    skill_root = Path(__file__).resolve().parent.parent
    output = tmp_path / "exported"
    result = subprocess.run(
        [
            sys.executable,
            str(skill_root / "scripts" / "export_skill_package.py"),
            "--root",
            str(skill_root),
            "--output",
            str(output),
            "--profile",
            profile,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    organize = output / "slots" / "organize" / "scripts" / "organize_project_structure.py"
    if profile == "core":
        # The organize slot is extension surface: it ships only under full.
        assert not organize.exists()
        return
    assert organize.is_file()
    project = tmp_path / "project"
    arch = project / ".spec" / "architecture"
    arch.mkdir(parents=True)
    (project / "upper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "lower.py").write_text("import upper\n", encoding="utf-8")
    (arch / "contracts.json").write_text(
        json.dumps(
            {
                "version": 1,
                "layers": ["high", "low"],
                "modules": [
                    {"name": "high", "layer": "high", "paths": ["upper.py"]},
                    {"name": "low", "layer": "low", "paths": ["lower.py"]},
                ],
                "forbid": [{"from": "low", "to": "high", "reason": "forbidden reverse dependency"}],
            }
        ),
        encoding="utf-8",
    )
    audit = subprocess.run(
        [
            sys.executable,
            str(organize),
            "--root",
            str(project),
            "--check",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    assert audit.returncode == 2, audit.stderr
    assert json.loads(audit.stdout)["contracts"]["new_violations"][0]["rule"] == "forbidden-import"


def test_export_end_to_end():
    skill_root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "spec-export"

        result = subprocess.run(
            [
                sys.executable,
                str(skill_root / "scripts" / "export_skill_package.py"),
                "--output",
                str(output),
                "--force",
                "--root",
                str(skill_root),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

        assert (output / "SKILL.md").exists()
        assert (output / "agents" / "openai.yaml").exists()
        assert (output / "references" / "templates.md").exists()
        assert (output / "hooks" / "pre-commit").exists()
        assert (output / "hooks" / "pre-push").exists()
        assert (output / "scripts" / "spec_package_support.py").exists()
        assert (output / "scripts" / "issue_closure_support.py").is_file()
        assert (output / "scripts" / "init_spec_package.py").exists()
        # full keeps the chain-family scripts (extension surface under core
        # only — planning F16 sub-step 2).
        assert (output / "scripts" / "chain_state.py").is_file()
        assert (output / "scripts" / "autoplan_gate.py").is_file()
        assert (output / "scripts" / "route_decision.py").is_file()
        assert (output / "agents" / "orchestrator.md").is_file()
        assert (output / "agents" / "planner.md").is_file()
        assert (output / "references" / "orchestration.md").is_file()
        assert (output / "slots" / "team-loop" / "manifest.json").is_file()
        assert (output / "slots" / "workflow-runner" / "manifest.json").is_file()
        assert (output / "slots" / "workflow-runner" / "scripts" / "workflow_fanout.py").is_file()
        assert not (output / "scripts" / "export_skill_package.py").exists()
        # payload_contract.py ships: install.sh queries it for the profile lists.
        assert (output / "scripts" / "payload_contract.py").is_file()
        assert not (output / "scripts" / "path_safety.py").exists()
        assert not (output / "scripts" / "skill_watermark.py").exists()
        assert not (output / ".watermark-key").exists()
        assert not (output / ".watermark-identity.json").exists()
        exported = (output / "SKILL.md").read_text(encoding="utf-8")
        assert "\u2063" not in exported
        assert exported == (skill_root / "SKILL.md").read_text(encoding="utf-8")
        # __pycache__ must not leak into the export
        assert not any(output.rglob("__pycache__")), "__pycache__ leaked into export"
        assert not any(output.rglob("*.pyc")), "*.pyc leaked into export"
        assert not any(output.rglob(".DS_Store")), ".DS_Store leaked into export"


@pytest.mark.parametrize("destination", ["root", "child", "ancestor"])
def test_export_rejects_output_overlapping_source_tree(tmp_path: Path, destination: str):
    skill_root = Path(__file__).resolve().parent.parent
    if destination == "root":
        output = skill_root
    elif destination == "child":
        output = skill_root / ".tmp-export-under-source"
    else:
        output = skill_root.parent
    sentinel = skill_root / "SKILL.md"
    before = sentinel.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            str(skill_root / "scripts" / "export_skill_package.py"),
            "--output",
            str(output),
            "--force",
            "--root",
            str(skill_root),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert sentinel.read_bytes() == before


TREE_MARKER = re.compile(r"^([ │├└─]*)[├└]──\s*(.+?)\s*$")


def _readme_export_tree_files(readme: str) -> set[str]:
    """Collect the file paths declared in the README export-tree code block."""
    match = re.search(r"```text\nspec/\n(.*?)```", readme, re.DOTALL)
    assert match, "README export tree block not found"
    stack: list[str] = []
    files: set[str] = set()
    for line in match.group(1).splitlines():
        line_match = TREE_MARKER.match(line)
        if not line_match:
            continue
        prefix, entry = line_match.group(1), line_match.group(2)
        depth = len(prefix) // 4  # one tree level per 4 leading columns
        del stack[depth:]
        entry = entry.split("#")[0].strip()
        if not entry:
            continue
        full = "/".join([*stack, entry])
        if entry.endswith("/"):
            stack.append(entry[:-1])
        if depth == 0:
            files.add(full.rstrip("/"))
    return files


def _exporter_expected_files() -> set[str]:
    from export_skill_package import IGNORE_PATTERNS, ROOT_FILES, SCRIPT_FILES
    from payload_contract import COPY_DIRS

    def ignored(path: Path) -> bool:
        if "__pycache__" in path.parts:
            return True
        return any(path.match(pattern) for pattern in IGNORE_PATTERNS)

    skill_root = Path(__file__).resolve().parent.parent
    expected: set[str] = set(ROOT_FILES)
    for name in COPY_DIRS:
        for path in sorted((skill_root / name).rglob("*")):
            if path.is_file() and not ignored(path):
                expected.add(path.relative_to(skill_root).as_posix())
    for name in SCRIPT_FILES:
        expected.add(f"scripts/{name}")
    return expected


def test_readme_export_tree_matches_exporter_output():
    readme_zh = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    readme_en = (Path(__file__).resolve().parent.parent / "README-en.md").read_text(encoding="utf-8")
    expected = _exporter_expected_files()

    for readme, label in ((readme_zh, "README.md"), (readme_en, "README-en.md")):
        declared = _readme_export_tree_files(readme)
        expected_roots = {path.split("/", 1)[0] for path in expected}
        assert declared == expected_roots, (
            f"{label} export roots drifted from the exporter rule: "
            f"missing={sorted(expected_roots - declared)} extra={sorted(declared - expected_roots)}"
        )

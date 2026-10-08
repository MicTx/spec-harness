"""Narrow truth-source gates: machine-pinned "documentation <-> script constant" contracts.

Boundary (F8, plans/02 §F8): these tests pin only contracts that are
mechanically checkable between shipped documents and module constants —

  Gate A  references/commands.md /spec:doctor check catalog  <->  PROBE_IDS
  Gate B  catalog "all N runtime files" count               <->  len(REQUIRED_RUNTIME_FILES)
  Gate C  smoke assert_runtime_layout required_paths/expected_slots <-> doctor constants
  Gate D  pyproject.toml public description                 <->  README-en.md first prose paragraph
  Gate E  CHANNEL_SPOT_CHECK_SCRIPTS                        <->  REQUIRED_RUNTIME_FILES

They deliberately do not duplicate the neighbor suites (stage inventory,
plans index, slot examples, public docs tree). Everything here is offline,
deterministic, sub-second: no subprocesses, no network, no file writes.
Every gate ships a constructed negative sample driven through the same
parse/compare helpers, proving the gate can go red without mutating the
repository.
"""

import ast
import re
import sys
from pathlib import Path
from typing import List, Sequence, Set, Tuple

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from doctor_spec_environment import (  # noqa: E402  # type: ignore
    BUILTIN_SLOT_NAMES,
    CHANNEL_SPOT_CHECK_SCRIPTS,
    PROBE_IDS,
    REQUIRED_RUNTIME_FILES,
)

REFERENCES_COMMANDS = ROOT / "references" / "commands.md"
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_test_spec_skill.py"
PYPROJECT = ROOT / "pyproject.toml"
README_EN = ROOT / "README-en.md"

CATALOG_MARKER = "Check catalog:"
# A probe id is ≥2 dot-separated lowercase segments (python.runtime,
# skill.layout). Single-segment spans (pyproject.toml would not match anyway,
# but e.g. `install.sh`, `--host`) and colon-bearing spans (`spec:<stage>.md`)
# are prose, never ids.
PROBE_ID_PATTERN = re.compile(r"^[a-z]+(?:\.[a-z]+)+$")
COUNT_PATTERN = re.compile(r"all (\d+) runtime files")


# --------------------------------------------------------------- parsers


def parse_check_catalog(text: str) -> Tuple[List[str], int]:
    """Return (probe ids, runtime-file count) from a Check catalog block.

    The catalog is the numbered list immediately following the
    "Check catalog:" marker line. Each entry declares its id(s) in backticks
    before the first colon; anything after the colon is prose and is never
    parsed as an id (that is where `str.removeprefix`-style spans live).
    """
    lines = text.splitlines()
    entries: List[str] = []
    for idx, line in enumerate(lines):
        if line.strip() == CATALOG_MARKER:
            for entry_line in lines[idx + 1 :]:
                if re.match(r"^\s*\d+\.\s", entry_line):
                    entries.append(entry_line)
                elif entry_line.strip() == "":
                    # tolerate blank lines around and inside the list
                    continue
                else:
                    break
            break
    if not entries:
        raise AssertionError("no numbered check catalog found after 'Check catalog:' marker")

    ids: List[str] = []
    for entry in entries:
        head = entry.split(":", 1)[0]
        ids.extend(span for span in re.findall(r"`([^`]+)`", head) if PROBE_ID_PATTERN.match(span))
    if not ids:
        raise AssertionError("catalog entries found but no probe ids extracted")

    counts = [int(match.group(1)) for entry in entries for match in COUNT_PATTERN.finditer(entry)]
    if len(counts) != 1:
        raise AssertionError(f"expected exactly one 'all N runtime files' count in the catalog, found {counts}")
    return ids, counts[0]


def first_prose_paragraph(text: str) -> str:
    """First non-empty prose paragraph, skipping titles/badges/links/html/fences.

    Newline/whitespace layout inside the paragraph is normalized to single
    spaces so reflowed markdown stays equal to the pyproject string.
    """
    paragraph: List[str] = []
    in_fence = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line:
            if paragraph:
                break
            continue
        if line.startswith(("#", "![", "[", "<")):
            continue
        paragraph.append(line)
    if not paragraph:
        raise AssertionError("no prose paragraph found before end of document")
    return " ".join(part.strip() for part in paragraph)


def parse_pyproject_description(text: str) -> str:
    """Extract the `description = "..."` key line from pyproject.toml.

    The key-line form is stable (single line, double quotes); a full TOML
    parser would need Python 3.11's tomllib and buys nothing for one key.
    """
    match = re.search(r'^description\s*=\s*"(.*)"\s*$', text, re.MULTILINE)
    if not match:
        raise AssertionError('no `description = "..."` key line found in pyproject.toml')
    return match.group(1)


def extract_smoke_constants(source: str) -> Tuple[Tuple[str, ...], Set[str]]:
    """Pull required_paths and expected_slots out of assert_runtime_layout.

    Uses AST literal evaluation so multi-line tuples parse naturally; regex
    line-matching breaks on wrapped literals.
    """
    func = next(
        (
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "assert_runtime_layout"
        ),
        None,
    )
    if func is None:
        raise AssertionError("assert_runtime_layout not found in smoke source")
    values = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("required_paths", "expected_slots"):
                    values[target.id] = ast.literal_eval(node.value)
    missing = [name for name in ("required_paths", "expected_slots") if name not in values]
    if missing:
        raise AssertionError(f"smoke layout constants not found: {missing}")
    return tuple(values["required_paths"]), set(values["expected_slots"])


# ------------------------------------------------------- compare helpers


def gate_a_catalog_matches_registry(catalog_ids: Sequence[str], registry_ids: Sequence[str]) -> None:
    listed, registry = set(catalog_ids), set(registry_ids)
    undocumented = sorted(listed - registry)
    not_listed = sorted(registry - listed)
    if undocumented or not_listed:
        raise AssertionError(
            f"check catalog / PROBE_IDS drift: undocumented-in-registry={undocumented}, "
            f"missing-from-catalog={not_listed}"
        )


def gate_b_count_matches_manifest(counted: int, expected: int) -> None:
    if counted != expected:
        raise AssertionError(f"catalog says {counted} runtime files, REQUIRED_RUNTIME_FILES has {expected}")


def gate_c_smoke_manifest_single_source(required_paths: Sequence[str], expected_slots: Set[str]) -> None:
    extra = sorted(set(required_paths) - set(REQUIRED_RUNTIME_FILES))
    if extra:
        raise AssertionError(f"smoke required_paths not in REQUIRED_RUNTIME_FILES: {extra}")
    if expected_slots != set(BUILTIN_SLOT_NAMES):
        raise AssertionError(
            f"smoke expected_slots {sorted(expected_slots)} != BUILTIN_SLOT_NAMES {sorted(BUILTIN_SLOT_NAMES)}"
        )


def gate_d_public_description_matches(description: str, paragraph: str) -> None:
    if description != paragraph:
        raise AssertionError(
            "pyproject description and README-en first prose paragraph differ:\n"
            f"  pyproject: {description!r}\n  readme-en: {paragraph!r}"
        )


def gate_e_sample_membership(sample: Sequence[str], manifest: Sequence[str]) -> None:
    extra = sorted(set(sample) - set(manifest))
    if extra:
        raise AssertionError(f"channel spot-check sample not in runtime manifest: {extra}")


# ------------------------------------------------------ Gate A–E positives


def test_gate_a_catalog_ids_match_probe_registry():
    ids, _ = parse_check_catalog(REFERENCES_COMMANDS.read_text(encoding="utf-8"))
    gate_a_catalog_matches_registry(ids, PROBE_IDS)


def test_gate_b_catalog_count_matches_required_runtime_files():
    _, count = parse_check_catalog(REFERENCES_COMMANDS.read_text(encoding="utf-8"))
    gate_b_count_matches_manifest(count, len(REQUIRED_RUNTIME_FILES))


def test_gate_c_smoke_layout_single_source():
    required_paths, expected_slots = extract_smoke_constants(SMOKE_SCRIPT.read_text(encoding="utf-8"))
    gate_c_smoke_manifest_single_source(required_paths, expected_slots)


def test_gate_d_public_description_matches_readme_en():
    description = parse_pyproject_description(PYPROJECT.read_text(encoding="utf-8"))
    paragraph = first_prose_paragraph(README_EN.read_text(encoding="utf-8"))
    gate_d_public_description_matches(description, paragraph)


def test_gate_e_channel_sample_membership():
    gate_e_sample_membership(CHANNEL_SPOT_CHECK_SCRIPTS, REQUIRED_RUNTIME_FILES)


# ------------------------------------------------------ Gate A–E negatives

# Synthetic minimal catalog used by negative samples: same shape as the real
# one (marker line, numbered entries, ids before the first colon, one count
# sentence), so fixtures exercise the very parsers the positives use.
SAMPLE_CATALOG = """Check catalog:

1. `python.runtime`: Python >= 3.9 (newest feature is `str.removeprefix`)
2. `skill.layout`: all 39 runtime files present (full manifest)
3. `skill.marker` + `skill.drift`: marker; drift when a source exists
"""


def test_gate_a_negative_extra_catalog_id_is_undocumented():
    drifted = SAMPLE_CATALOG.replace(
        "3. `skill.marker` + `skill.drift`:", "3. `skill.marker` + `skill.drift` + `ghost.probe`:"
    )
    ids, _ = parse_check_catalog(drifted)
    with pytest.raises(AssertionError, match=r"ghost\.probe"):
        gate_a_catalog_matches_registry(ids, ["python.runtime", "skill.layout", "skill.marker", "skill.drift"])


def test_gate_a_negative_registry_id_missing_from_catalog():
    ids, _ = parse_check_catalog(SAMPLE_CATALOG)
    with pytest.raises(AssertionError, match=r"missing-from-catalog=\['zcode\.symlink'\]"):
        gate_a_catalog_matches_registry(
            ids,
            ["python.runtime", "skill.layout", "skill.marker", "skill.drift", "zcode.symlink"],
        )


def test_gate_b_negative_count_drift_fails_both_ways():
    drifted = SAMPLE_CATALOG.replace("all 39 runtime files", "all 40 runtime files")
    _, count = parse_check_catalog(drifted)
    with pytest.raises(AssertionError, match=r"40 runtime files, REQUIRED_RUNTIME_FILES has 39"):
        gate_b_count_matches_manifest(count, 39)
    with pytest.raises(AssertionError):
        gate_b_count_matches_manifest(38, 39)


def test_gate_c_negative_superset_manifest_and_slot_mismatch():
    with pytest.raises(AssertionError, match=r"not in REQUIRED_RUNTIME_FILES"):
        gate_c_smoke_manifest_single_source(["SKILL.md", "scripts/ghost_probe.py"], {"team-loop", "workflow-runner"})
    with pytest.raises(AssertionError, match=r"expected_slots"):
        gate_c_smoke_manifest_single_source(["SKILL.md"], {"team-loop"})


def test_gate_d_negative_rewritten_paragraph_fails():
    with pytest.raises(AssertionError, match=r"pyproject description and README-en"):
        gate_d_public_description_matches("Task-package workflow for AI coding agents.", "Rewritten marketing copy.")


def test_gate_e_negative_sample_path_without_prefix_fails():
    with pytest.raises(AssertionError, match=r"doctor_spec_environment\.py"):
        gate_e_sample_membership(
            ["doctor_spec_environment.py", "scripts/route_spec_package.py"],
            ["scripts/doctor_spec_environment.py", "scripts/route_spec_package.py"],
        )


# ------------------------------------------------- parser tolerance tests


def test_catalog_parser_ignores_prose_backticks_after_colon():
    # `str.removeprefix` sits after the entry's colon: it is prose, not an id.
    ids, _ = parse_check_catalog(SAMPLE_CATALOG)
    assert ids == ["python.runtime", "skill.layout", "skill.marker", "skill.drift"]


def test_catalog_parser_reports_missing_marker_precisely():
    with pytest.raises(AssertionError, match=r"no numbered check catalog"):
        parse_check_catalog("# unrelated\n\n1. `python.runtime`: nothing above marks a catalog\n")


def test_catalog_parser_requires_exactly_one_count_sentence():
    no_count = SAMPLE_CATALOG.replace("all 39 runtime files", "every runtime file")
    with pytest.raises(AssertionError, match=r"expected exactly one 'all N runtime files'"):
        parse_check_catalog(no_count)
    doubled = SAMPLE_CATALOG.replace("(full manifest)", "(all 40 runtime files too)")
    with pytest.raises(AssertionError, match=r"expected exactly one"):
        parse_check_catalog(doubled)


def test_first_prose_paragraph_skips_badges_and_normalizes_lines():
    document = "\n".join(
        [
            "# spec",
            "",
            "![badge](https://example.invalid/badge.svg)",
            "[![stars](https://example.invalid/s.svg)](https://example.invalid)",
            "",
            "Task-package workflow",
            "with verifiable scope, evidence, and Git gates.",
            "",
            "Later paragraph is not the first.",
        ]
    )
    assert first_prose_paragraph(document) == ("Task-package workflow with verifiable scope, evidence, and Git gates.")


def test_first_prose_paragraph_reports_absence():
    with pytest.raises(AssertionError, match=r"no prose paragraph"):
        first_prose_paragraph("# title\n\n[only a link](https://example.invalid)\n")


def test_pyproject_description_parser_reports_missing_key():
    with pytest.raises(AssertionError, match=r"no `description"):
        parse_pyproject_description('[project]\nname = "spec"\n')


def test_smoke_constant_extractor_handles_wrapped_literals_and_reports_missing():
    source = "\n".join(
        [
            "def assert_runtime_layout(root):",
            "    required_paths = (",
            "        'SKILL.md',",
            "        'install.sh',",
            "    )",
            "    expected_slots = {'team-loop', 'workflow-runner'}",
            "    return required_paths, expected_slots",
        ]
    )
    required_paths, expected_slots = extract_smoke_constants(source)
    assert required_paths == ("SKILL.md", "install.sh")
    assert expected_slots == {"team-loop", "workflow-runner"}

    with pytest.raises(AssertionError, match=r"constants not found"):
        extract_smoke_constants("def assert_runtime_layout(root):\n    return None\n")

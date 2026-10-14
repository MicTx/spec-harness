"""Stage-contract split guards (AUD-15 / cluster F13).

`references/stages/<stage>.md` is the per-stage contract surface carved out
of the former single-file `commands.md` (design record: `docs/adr/0002`,
source repository). These tests keep the split's structural invariants:

- one stage file per installer stage (`USER_STAGES` is the single list —
  no second stage table may grow beside it);
- a hard byte budget per stage file, so implementation narrative keeps
  living in CHANGELOG/ADR instead of creeping back into the contracts;
- every relative markdown link in the runtime docs (SKILL.md plus the
  references tree) resolves, including the new stages/ cross-links;
- the runtime docs stay free of the retired F-numbered feature labels
  (history lives in CHANGELOG/ADR and the archives, not the contracts).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGES_DIR = ROOT / "references" / "stages"
INSTALL = ROOT / "install.sh"

#: 5 KB budget per stage contract file (the plan's "≤3–5KB" fixed at 5 KB).
STAGE_FILE_BYTE_BUDGET = 5120

STAGE_ARRAY = re.compile(r"^USER_STAGES=\((?P<stages>[^)]*)\)", re.MULTILINE)
RELATIVE_LINK = re.compile(r"\[[^]]+\]\(([^)#\s]+)(?:#[^)]*)\)")
REMOTE_OR_ANCHOR_PREFIXES = ("http://", "https://", "mailto:", "ftp://")
#: Retired feature-label pattern; runtime docs carry semantics, archives carry history.
F_LABEL = re.compile(r"\bF1[0-9]\b")

RUNTIME_DOC_FILES = [ROOT / "SKILL.md", *sorted((ROOT / "references").rglob("*.md"))]


def _install_stages() -> list[str]:
    match = STAGE_ARRAY.search(INSTALL.read_text(encoding="utf-8"))
    assert match, "install.sh must declare USER_STAGES"
    return match.group("stages").split()


def test_stage_files_match_installer_stage_list_exactly():
    stages = _install_stages()
    files = sorted(path.stem for path in STAGES_DIR.glob("*.md"))
    assert files == sorted(stages), (
        "references/stages must hold exactly one file per USER_STAGES entry "
        f"(no second stage table): installer={sorted(stages)} files={files}"
    )


def test_stage_files_stay_within_byte_budget():
    sizes = {path.name: len(path.read_bytes()) for path in STAGES_DIR.glob("*.md")}
    assert sizes, "references/stages is empty"
    over = {name: size for name, size in sizes.items() if size > STAGE_FILE_BYTE_BUDGET}
    assert not over, (
        f"stage contract files over {STAGE_FILE_BYTE_BUDGET} B — narrative belongs in "
        f"CHANGELOG/ADR, not the contracts: {over}"
    )


def test_every_stage_file_opens_with_its_command_heading():
    for path in STAGES_DIR.glob("*.md"):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first == f"# `/spec:{path.stem}`", f"{path.name} must open with its command heading, got: {first!r}"


def test_runtime_doc_relative_links_resolve():
    assert len(RUNTIME_DOC_FILES) > 1, "scan scope must not collapse"
    for source in RUNTIME_DOC_FILES:
        text = source.read_text(encoding="utf-8")
        for target in RELATIVE_LINK.findall(text):
            if target.startswith(REMOTE_OR_ANCHOR_PREFIXES):
                continue
            resolved = (source.parent / target).resolve()
            assert resolved.exists(), f"{source.relative_to(ROOT)}: relative link {target!r} does not resolve"


def test_runtime_docs_carry_no_retired_feature_labels():
    for source in RUNTIME_DOC_FILES:
        hit = F_LABEL.search(source.read_text(encoding="utf-8"))
        assert hit is None, (
            f"{source.relative_to(ROOT)} carries retired feature label {hit.group(0)!r}; "
            "history belongs in CHANGELOG/ADR and the archives"
        )

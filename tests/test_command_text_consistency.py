"""Command-description semantic consistency (AUD-09 / cluster F4).

`references/stages/<stage>.md` is the semantic source of truth for what each
`/spec:*` stage does. The installer renders per-host command bodies from
`install.sh`'s `command_body` heredocs, and nothing derives one from the
other mechanically — so drift needs a guard. These tests pin the *key
semantic anchors* on both sides (never whole-paragraph equality, which
would be brittle), keep the audit's old wordings as negative samples that
must stay mutually exclusive with the current text, and verify that a
sandboxed install lands the same semantics on disk.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0 or not Path("/bin/bash").exists(),
    reason="bash not available",
)

IGNORE_WHEN_COPYING = shutil.ignore_patterns(
    ".git",
    ".spec",
    "release",
    ".watermark-identity.json",
    ".watermark-key",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
)

STAGES = ("goal", "autoplan", "autorun")

# Key semantic anchors per stage. must_contain pins the contract meaning;
# must_not_contain pins the audited old wording so both sides of the
# exclusivity are machine-checked.
ANCHORS = {
    "goal": {
        "must_contain": [
            "self-verify each delivery at the package's declared verification level",
            "stop at the local commit",
        ],
        "must_not_contain": ["independently verify", "then use the safe spec:push flow"],
    },
    "autoplan": {
        "must_contain": [
            "Desktop and IDE-plugin sessions use current-session",
            "serial fresh-session chain",
            "No in-process subagent delegation transfers chain ownership",
            "main-session review",
        ],
        "must_not_contain": ["No in-process subagent delegation anywhere in autoplan"],
    },
    "autorun": {
        "must_contain": [
            "round cap on by default",
            "--unbounded explicitly removes it",
            "desktop and IDE-plugin sessions continue in the current session",
            "Do not launch another agent just because its CLI is on PATH",
        ],
        "must_not_contain": ["Safety rails: round cap, single-chain lock, sequential spawn only."],
    },
}

# The contract side (semantic source of truth) must keep the matching
# statements, or the installer text would be consistent with nothing.
CONTRACT_ANCHORS = {
    "goal": (
        "self-verify at the package's declared verification level",
        "The default stop is the local commit",
    ),
    "autoplan": (
        "Desktop and IDE-plugin sessions use current-session",
        "main-session review",
    ),
    "autorun": (
        "a restored default bounds every chain",
        "default current despite CLI markers/PATH",
    ),
}

# Reverting any of these current phrases back to its audited old wording
# must fail the anchor checks — the negative-sample pairs below simulate
# that regression without touching install.sh.
REVERT_PAIRS = {
    "goal": (
        "self-verify each delivery at the package's declared verification level",
        "independently verify each delivery",
    ),
    "autoplan": (
        "Desktop and IDE-plugin sessions use current-session",
        "All hosts require an external CLI",
    ),
    "autorun": (
        "round cap on by default (5 rounds unless flagged; --unbounded explicitly removes it)",
        "round cap",
    ),
}


def assert_semantic_anchors(stage: str, text: str) -> None:
    anchors = ANCHORS[stage]
    for needle in anchors["must_contain"]:
        assert needle in text, f"{stage} command text lost semantic anchor: {needle!r}"
    for needle in anchors["must_not_contain"]:
        assert needle not in text, f"{stage} command text drifted back to old wording: {needle!r}"


def extract_command_body(installer: Path, stage: str) -> str:
    """Source install.sh with its final `main "$@"` stripped and capture one command body.

    The extraction depends on install.sh's tail shape (function definitions
    only, single top-level `main "$@"`); assert that shape first so a future
    refactor fails this test readably instead of silently skipping it.
    """
    lines = installer.read_text().splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    assert lines, "install.sh is empty"
    tail = lines[-1].strip()
    assert tail == 'main "$@"', (
        f"install.sh tail shape changed: expected final 'main \"$@\"' but found {tail!r}; "
        "update the extraction in tests/test_command_text_consistency.py"
    )
    body_source = "\n".join(lines[:-1]) + "\n"
    script = installer.parent / f".install-sh-extracted-{stage}.sh"
    script.write_text(body_source)
    try:
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; command_body "$2" "$3"', "bash", str(script), f"spec:{stage}", stage],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        script.unlink(missing_ok=True)
    assert result.returncode == 0, f"command_body extraction failed for {stage}: {result.stderr}"
    return result.stdout


@requires_bash
@pytest.mark.parametrize("stage", STAGES)
def test_install_command_bodies_carry_semantic_anchors(stage: str) -> None:
    assert_semantic_anchors(stage, extract_command_body(INSTALLER, stage))


@requires_bash
@pytest.mark.parametrize("stage", STAGES)
def test_reverting_to_audited_old_wording_fails(stage: str) -> None:
    body = extract_command_body(INSTALLER, stage)
    current, old = REVERT_PAIRS[stage]
    assert current in body, f"{stage} body no longer contains the anchor phrase the revert pair depends on: {current!r}"
    reverted = body.replace(current, old)
    assert reverted != body
    with pytest.raises(AssertionError):
        assert_semantic_anchors(stage, reverted)


@requires_bash
@pytest.mark.parametrize(
    ("anchor", "wrong"),
    [
        (
            "desktop and IDE-plugin sessions continue in the current session",
            "all sessions spawn a fresh agent window",
        ),
        (
            "Do not launch another agent just because its CLI is on PATH",
            "launch whichever agent CLI appears first on PATH",
        ),
    ],
)
def test_autorun_body_anchor_mutation_fails(anchor: str, wrong: str) -> None:
    body = extract_command_body(INSTALLER, "autorun")
    assert anchor in body
    mutated = body.replace(anchor, wrong)
    assert mutated != body
    with pytest.raises(AssertionError):
        assert_semantic_anchors("autorun", mutated)


@pytest.mark.parametrize("stage", STAGES)
def test_contract_source_of_truth_keeps_matching_statements(stage: str) -> None:
    contract = (ROOT / "references" / "stages" / f"{stage}.md").read_text()
    for anchor in CONTRACT_ANCHORS[stage]:
        assert anchor in contract, (
            f"references/stages/{stage}.md lost the {stage} contract statement the installed text is anchored to: "
            f"{anchor!r}"
        )


@requires_bash
def test_installed_claude_commands_land_the_same_semantics(tmp_path: Path) -> None:
    tree = tmp_path / "srctree"
    shutil.copytree(ROOT, tree, ignore=IGNORE_WHEN_COPYING)
    home = tmp_path / "home"
    home.mkdir()
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(home),
            "INSTALL_HOSTS": "claude",
            # This suite pins the full 12-stage command surface; core (the
            # default since F16) ships only the five kernel stages.
            "INSTALL_PROFILE": "full",
            "SOURCE_DIR": str(tree),
            "CLAUDE_SKILLS_DIR": str(home / ".claude" / "skills"),
            "CLAUDE_COMMANDS_DIR": str(home / ".claude" / "commands"),
            "CLAUDE_AGENTS_DIR": str(home / ".claude" / "agents"),
            "BACKUP_ROOT": str(home / ".spec-skill-backups"),
        }
    )
    for var in ("SPEC_SIGNING_IDENTITY", "SPEC_SIGNING_KEY", "SPEC_SKIP_SIGNING"):
        environment.pop(var, None)
    result = subprocess.run(
        ["bash", str(tree / "install.sh")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        timeout=180,
    )
    assert result.returncode == 0, f"sandboxed install failed: {result.stdout}\n{result.stderr}"
    for stage in STAGES:
        landed = (home / ".claude" / "commands" / "spec" / f"{stage}.md").read_text()
        assert_semantic_anchors(stage, landed)
        body = extract_command_body(tree / "install.sh", stage)
        assert body.strip() in landed, f"landed /spec:{stage}.md does not contain the source command body verbatim"

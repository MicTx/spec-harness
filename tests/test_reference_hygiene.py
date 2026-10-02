"""Credential-hygiene contracts over the skill's own carriers.

Three pinned guarantees:

- Discipline text: the assignment contract's ``Excluded areas`` entry
  carries the secret-exclusion wording, the ``/spec:done`` distillation
  discipline carries the 蒸馏文本不得含真实凭证值 ban, and ``templates.md``
  writing requirements extend the same rule to ``boundary`` / ``verify``
  lines and acceptance evidence with placeholder-only examples.
- Enum consistency: the assignment-contract field enumeration stays the
  same five tokens in the same order across all four carriers
  (``references/orchestration.md``, ``SKILL.md``, ``agents/orchestrator.md``,
  ``slots/workflow-runner/README.md``). The discipline deliberately merges
  into the existing Excluded areas entry instead of adding a sixth field,
  so this is the drift guard: any carrier that adds, drops, or reorders a
  field fails here.
- Pattern scan: ``SKILL.md`` and ``references/*.md`` carry zero
  high-confidence credential patterns (AKIA access keys, ``ghp_`` /
  ``github_pat_`` prefixes, ``sk-`` keys, ``xox[baprs]-`` tokens, PEM
  private-key blocks). Only confirmatory formats are scanned — keyword
  matches (e.g. the word "token") are noisy and deliberately out of scope.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FIVE_FIELDS = ("goal", "scope", "excluded areas", "output", "verification")

# Inline slash-separated form used by the three carriers outside
# orchestration.md (whose source-of-truth form is one bullet per field).
INLINE_ENUM_RE = re.compile(
    r"goal\s*/\s*scope\s*/\s*excluded areas\s*/\s*output\s*/\s*verification",
    re.IGNORECASE,
)

FIELD_BULLET_RE = re.compile(r"^- ([^:]+):", re.MULTILINE)

SECRET_PATTERNS = {
    "aws-access-key-id": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github-personal-access-token": re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    "github-fine-grained-token": re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    "sk-style-api-key": re.compile(r"sk-[0-9A-Za-z]{20,}"),
    "slack-token": re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),
    "pem-private-key-block": re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
}


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _section(content: str, heading: str) -> str:
    """Return the body of a ``## <heading>`` section (heading excluded)."""
    start = content.index(heading)
    body_start = content.index("\n", start) + 1
    next_heading = content.find("\n## ", body_start)
    if next_heading < 0:
        return content[body_start:]
    return content[body_start:next_heading]


# -- Discipline text --


def _excluded_areas_bullet() -> str:
    section = _section(_read("references", "orchestration.md"), "## Assignment contract")
    return next(line for line in section.splitlines() if line.startswith("- Excluded areas:"))


def test_assignment_contract_excluded_areas_carries_secret_exclusion():
    bullet = _excluded_areas_bullet()
    assert "credential hygiene" in bullet, "Excluded areas must name the credential-hygiene discipline"
    assert "secret" in bullet, "Excluded areas must state the secret exclusion"
    for carrier in (
        "contract text",
        "lane prompts",
        "command-line arguments",
        "acceptance evidence",
        "distilled notes",
    ):
        assert carrier in bullet, f"secret exclusion must name the carrier: {carrier}"
    assert "env files / host-side injection" in bullet, "Excluded areas must state the legitimate injection path"
    assert "<admin-token>" in bullet, "examples must be placeholders, never real values"


def test_done_distillation_discipline_carries_credential_ban():
    section = _section(_read("references", "commands.md"), "## `/spec:done`")
    assert "蒸馏文本不得含真实凭证值" in section, "/spec:done distillation discipline must carry the credential ban"
    assert "env file / host-side injection" in section, "ban must point at the legitimate injection path"
    assert "<admin-token>" in section, "replacement example must be a placeholder"


def test_templates_writing_requirements_extend_hygiene_to_boundary_and_evidence():
    content = _read("references", "templates.md")
    block_start = content.index("Writing requirements:", content.index("# [项目名称] - 任务拆解"))
    block = content[block_start : content.index("# [项目名称] - 验收清单")]
    assert "Credential hygiene" in block, "tasks writing requirements must carry the credential-hygiene bullet"
    assert "`boundary` / `verify`" in block, "hygiene must extend to boundary/verify lines"
    assert "acceptance evidence" in block, "hygiene must extend to acceptance evidence"
    assert "env files / host-side injection" in block
    assert "(`<admin-token>`)" in block, "example must be a placeholder"
    assert "never real values" in block


# -- Enum consistency across the four carriers --


def _assignment_contract_field_names() -> tuple[str, ...]:
    section = _section(_read("references", "orchestration.md"), "## Assignment contract")
    names = FIELD_BULLET_RE.findall(section)
    assert len(names) == len(FIVE_FIELDS), f"assignment contract must enumerate exactly five fields, got: {names}"
    return tuple(name.strip().lower() for name in names)


def test_assignment_contract_enumerates_exactly_the_five_fields():
    assert _assignment_contract_field_names() == FIVE_FIELDS


def test_five_field_enum_stays_identical_across_all_four_carriers():
    """orchestration.md 的 bullet 枚举与三处内联副本必须同为五项且逐一对应。"""
    assert _assignment_contract_field_names() == FIVE_FIELDS
    carriers = (
        ("SKILL.md", "goal / scope / excluded areas / output / verification"),
        ("agents/orchestrator.md", None),
        ("slots/workflow-runner/README.md", None),
    )
    for path, _ in carriers:
        content = _read(*path.split("/"))
        match = INLINE_ENUM_RE.search(content)
        assert match, f"{path} lost the inline five-field enumeration"
        normalized = re.sub(r"\s+", " ", match.group(0)).lower()
        assert normalized == "goal / scope / excluded areas / output / verification", f"{path}: {normalized}"


def test_inline_carriers_keep_the_enumeration_on_the_pinned_lines():
    """锚定三处内联副本的既有行位，防止措辞在无意中被挪出原句。"""
    assert INLINE_ENUM_RE.search(_read("SKILL.md")), "SKILL.md"
    orchestrator = _read("agents", "orchestrator.md")
    assert "lane ownership" in orchestrator and INLINE_ENUM_RE.search(orchestrator), "agents/orchestrator.md"
    workflow = _read("slots", "workflow-runner", "README.md")
    assert "full assignment" in workflow and INLINE_ENUM_RE.search(workflow), "slots/workflow-runner/README.md"


# -- Zero-host-engine boundary --


def test_active_runtime_has_no_host_workflow_engine_surface():
    """active runtime 只保留仓库驱动，不枚举 Claude/ZCode 原生编排面。"""
    forbidden = re.compile(
        r"CreateWorkflow|create-workflow|workflow-tool|Workflow tool|dynamic-workflows|workflow\.ts|mode (?:A|C)\b",
        flags=re.IGNORECASE,
    )
    paths = [
        ROOT / "SKILL.md",
        ROOT / "references" / "orchestration.md",
        ROOT / "references" / "slots.md",
        ROOT / "slots" / "workflow-runner" / "README.md",
        ROOT / "slots" / "workflow-runner" / "manifest.json",
        ROOT / "slots" / "workflow-runner" / "scripts" / "workflow_route.py",
        ROOT / "slots" / "workflow-runner" / "scripts" / "workflow_fanout.py",
        ROOT / "slots" / "workflow-runner" / "hooks" / "workflow_route_hook.py",
    ]
    for path in paths:
        hit = forbidden.search(path.read_text(encoding="utf-8"))
        assert hit is None, f"{path.relative_to(ROOT)} retains host workflow engine text: {hit.group(0)!r}"


def test_route_surface_is_repository_owned():
    route = _read("slots", "workflow-runner", "scripts", "workflow_route.py")
    assert '"surface": "subprocess-fanout"' in route
    assert '"owner": "spec-harness"' in route


# -- High-confidence credential pattern scan --


def test_skill_and_references_carry_no_high_confidence_credential_patterns():
    files = [ROOT / "SKILL.md", *sorted((ROOT / "references").glob("*.md"))]
    assert len(files) > 1, "scan scope must not collapse to an empty set"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for name, pattern in SECRET_PATTERNS.items():
            hit = pattern.search(text)
            assert hit is None, f"{path.relative_to(ROOT)} matches high-confidence credential pattern: {name}"

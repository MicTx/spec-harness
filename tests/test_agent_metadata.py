"""Agent metadata contract tests for the host-facing skill declaration.

``agents/openai.yaml`` is listing text, not runtime contract: the host shows it
before the skill runs, so it must stay a short one-line description plus one
starter prompt that names ``$spec``. Stage routing, boundaries, and gate rules
belong to ``SKILL.md`` and ``references/`` and must not be duplicated here.

Parsed line-by-line on purpose: the repo's frontmatter-style carriers use the
same convention (see ``test_export_skill_package``), and the test suite declares
no YAML dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METADATA = ROOT / "agents" / "openai.yaml"

SHORT_DESCRIPTION_MIN = 25
SHORT_DESCRIPTION_MAX = 64
DEFAULT_PROMPT_MAX = 160
PROMPT_LEAK_MARKERS = ("boundary", "verify", "tasks.md", "checklist.md", "references/", "done/push", "gate")
LISTING_KEYS = {"display_name", "short_description", "default_prompt"}

INTERFACE_LINE = re.compile(r'^  (?P<key>[A-Za-z_][A-Za-z0-9_]*):\s*"(?P<value>.*)"\s*$')


def _interface_fields() -> dict[str, str]:
    text = METADATA.read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("#"), "leading comment block documents the fields"
    assert "\ninterface:\n" in text, "metadata must declare one top-level interface mapping"
    fields: dict[str, str] = {}
    for line in text.split("\ninterface:\n", 1)[1].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = INTERFACE_LINE.match(line)
        if match:
            fields[match.group("key")] = match.group("value")
            continue
        if line.startswith("  ") and not line.startswith("    "):
            # A nested mapping (e.g. policy) opens here; its children are not
            # listing text and are recorded as a key without a scalar value.
            fields.setdefault(line.strip().rstrip(":"), "")
            continue
        assert line.startswith("    "), f"unparsed metadata line: {line!r}"
    return fields


def test_display_name_identifies_the_skill():
    assert _interface_fields()["display_name"] == "Spec Harness"


def test_short_description_fits_the_host_listing_budget():
    description = _interface_fields()["short_description"]
    assert description == description.strip(), "listing text must not carry padding whitespace"
    assert SHORT_DESCRIPTION_MIN <= len(description) <= SHORT_DESCRIPTION_MAX, (
        f"short_description must stay within {SHORT_DESCRIPTION_MIN}-{SHORT_DESCRIPTION_MAX} characters, "
        f"got {len(description)}"
    )


def test_default_prompt_is_one_short_starter_that_names_spec():
    prompt = _interface_fields()["default_prompt"]
    assert "$spec" in prompt, "the starter prompt must name the trigger, never leave it implicit"
    assert len(prompt) <= DEFAULT_PROMPT_MAX, "the starter prompt must stay short"
    assert "\n" not in prompt, "one starter prompt, not a multi-line rules block"


def test_metadata_does_not_duplicate_the_runtime_contract():
    prompt = _interface_fields()["default_prompt"].lower()
    leaked = [marker for marker in PROMPT_LEAK_MARKERS if marker in prompt]
    assert not leaked, f"listing text duplicates runtime contract markers: {leaked}"


def test_metadata_carries_only_listing_text_and_optional_policy():
    keys = set(_interface_fields())
    assert LISTING_KEYS <= keys, f"listing fields missing: {sorted(LISTING_KEYS - keys)}"
    assert keys <= LISTING_KEYS | {"policy"}, f"unexpected listing fields: {sorted(keys - LISTING_KEYS - {'policy'})}"


def test_implicit_invocation_stays_available():
    """The host may auto-trigger the skill: implicit invocation is never disabled."""
    body = METADATA.read_text(encoding="utf-8").split("\ninterface:\n", 1)[1]
    assert "allow_implicit_invocation" not in body or "allow_implicit_invocation: true" in body

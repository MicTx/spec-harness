import json
from pathlib import Path

import pytest

from scripts.skill_watermark import stamp, stamp_tree, strip, verify

IDENTITY = {
    "holder": "Rights Holder Example",
    "contact": "holder@example.invalid",
    "channel": "example-channel-only",
    "commercial": "not-permitted",
}
KEY = b"k" * 32
OTHER_KEY = b"z" * 32


def test_stamp_roundtrip_keeps_visible_text():
    source = "# Skill\n\nVisible line.\n"
    marked = stamp(source, IDENTITY, KEY)

    assert verify(marked, KEY) == IDENTITY
    assert strip(marked) == source
    assert marked.split("\u2063", 1)[0] == source
    assert "Rights Holder Example" not in marked
    assert "holder@example.invalid" not in marked


def test_restamp_replaces_previous_mark():
    revised = {
        "holder": "Second Example",
        "contact": "second@example.invalid",
        "channel": "channel",
        "commercial": "not-permitted",
    }
    marked = stamp(stamp("# Skill\n", IDENTITY, KEY), revised, KEY)

    assert verify(marked, KEY) == revised
    assert marked.count("\u2063") == 2


def test_wrong_key_truncated_and_tampered_marks_fail():
    marked = stamp("# Skill\n", IDENTITY, KEY)

    with pytest.raises(ValueError, match="integrity"):
        verify(marked, OTHER_KEY)
    end = marked.rfind("\u2063")
    with pytest.raises(ValueError, match="truncated|malformed"):
        verify(marked[: end - 3] + marked[end:], KEY)

    chars = list(marked)
    pad = next(index for index, char in enumerate(chars) if char in "\u2060\u200b")
    chars[pad] = "\u200b" if chars[pad] == "\u2060" else "\u2060"
    with pytest.raises(ValueError, match="integrity"):
        verify("".join(chars), KEY)


def test_strip_removes_mark_and_verify_reports_missing():
    source = "# Skill\n\nVisible.\n"
    assert strip(stamp(source, IDENTITY, KEY)) == source
    with pytest.raises(ValueError, match="missing"):
        verify(source, KEY)


def test_stamp_tree_only_touches_existing_targets(tmp_path: Path):
    (tmp_path / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    stamped = stamp_tree(tmp_path, ("SKILL.md", "README.md"), IDENTITY, KEY)

    assert stamped == ["SKILL.md"]
    assert verify((tmp_path / "SKILL.md").read_text(encoding="utf-8"), KEY)["channel"] == IDENTITY["channel"]
    assert not (tmp_path / "README.md").exists()


def test_stamp_tree_refuses_empty_target(tmp_path: Path):
    with pytest.raises(ValueError, match="no watermark target"):
        stamp_tree(tmp_path, ("SKILL.md",), IDENTITY, KEY)


def test_cli_does_not_print_identity_on_stamp(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    from scripts.skill_watermark import main

    identity = tmp_path / "identity.json"
    key = tmp_path / "key"
    skill = tmp_path / "SKILL.md"
    identity.write_text(json.dumps(IDENTITY), encoding="utf-8")
    key.write_bytes(KEY)
    skill.write_text("# Skill\n", encoding="utf-8")

    stamped = main(
        ["stamp-tree", "--root", str(tmp_path), "--identity", str(identity), "--key", str(key), "--file", "SKILL.md"]
    )
    assert stamped == 0
    assert "Rights Holder Example" not in capsys.readouterr().out
    assert main(["verify", "--file", str(skill), "--key", str(key)]) == 0
    assert "channel=example-channel-only" in capsys.readouterr().out

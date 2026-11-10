import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_installer_exposes_only_three_user_commands():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(r"^USER_STAGES=\(([^)]*)\)", text, re.MULTILINE)
    assert match and match.group(1).split() == ["push", "doctor"]
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "`/spec <goal>`" in skill
    assert "`/spec:push`" in skill and "`/spec:doctor`" in skill
    for retired in ("/spec:new", "/spec:run", "/spec:check", "/spec:done"):
        assert retired not in skill

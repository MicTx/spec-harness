import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_installer_exposes_the_stage_commands():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(r"^USER_STAGES=\(([^)]*)\)", text, re.MULTILINE)
    assert match and match.group(1).split() == ["plan", "run", "check", "push", "doctor"]
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "`/spec <goal>`" in skill
    for stage in ("/spec:plan", "/spec:run", "/spec:check", "/spec:push", "/spec:doctor"):
        assert f"`{stage}" in skill
    for retired in ("/spec:new", "/spec:done", "/spec:goal", "/spec:update", "/spec:organize", "/spec:status"):
        assert retired not in skill

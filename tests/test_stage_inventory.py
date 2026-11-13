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


def test_check_stage_requires_ui_review_in_documented_command_surface():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_en = (ROOT / "README-en.md").read_text(encoding="utf-8")
    assert "真实操作" in readme and "回写" in readme
    assert "UI/UX real-operation review" in readme_en


def test_architecture_index_documents_the_five_stage_surface():
    index = (ROOT / ".spec" / "architecture" / "module-index.md").read_text(encoding="utf-8")
    assert "three-command" not in index
    assert "five-stage host surface" in index
    for stage in ("/spec:plan", "/spec:run", "/spec:check", "/spec:push", "/spec:doctor"):
        assert stage in index


def test_readme_command_tables_match_the_installer_stage_list():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    stages = re.search(r"^USER_STAGES=\(([^)]*)\)", installer, re.MULTILINE).group(1).split()
    for name in ("README.md", "README-en.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for stage in stages:
            assert f"`/spec:{stage}" in text, (name, stage)

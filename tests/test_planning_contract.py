from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_planning_contract_names_plan_and_update_modes():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "user commands" not in skill.lower() or "`/spec`" in skill
    assert "`/spec:new`" not in skill and "`/spec:update`" not in skill
    assert "`/spec` routes an unbound goal to `plan`" in skill
    assert "bound package to `update`" in skill

    plan = (ROOT / "references/plan.md").read_text(encoding="utf-8")
    assert "## Plan vs Update" in plan
    assert "reason backward from failure" in plan
    assert "never reopen planning" in plan
    assert "product files stay untouched" in plan

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_en = (ROOT / "README-en.md").read_text(encoding="utf-8")
    assert "`plan` 规划流" in readme and "`update` 规划流" in readme
    assert "`plan` planning flow" in readme_en and "`update`" in readme_en

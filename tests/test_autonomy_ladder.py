"""Pin the autonomy ladder and the question budget across prompt surfaces."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_skill_ladder_contract():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "## Autonomy Contract" in skill
    assert "last resort, not the first" in skill
    assert "climb the ladder" in skill
    assert "at most four cards, once per package" in skill
    assert "Re-asking what evidence already answers is a violation" in skill
    assert len(skill.encode("utf-8")) <= 4096


def test_references_question_budget():
    contract = (ROOT / "references/exec.md").read_text(encoding="utf-8")
    planning = (ROOT / "references/plan.md").read_text(encoding="utf-8")
    assert "Question budget" in contract
    assert "only user-bound channel is `## Parked`" in contract
    assert "resolve from disk evidence first" in planning
    total = sum(p.stat().st_size for p in sorted((ROOT / "references").glob("*.md")))
    assert total <= 16384


def test_surfaces_direct_autonomy():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    hook = (ROOT / "hooks/spec_hook.py").read_text(encoding="utf-8")
    router = (ROOT / "scripts/route.py").read_text(encoding="utf-8")
    agents_path = ROOT / "AGENTS.md"
    assert "decided by V1 and logged, not asked" in installer
    assert "do not ask the user to choose" in hook
    assert "only parked items reach the user" in router
    if agents_path.is_file() or not (ROOT / "PUBLIC_MANIFEST.json").is_file():
        agents_md = agents_path.read_text(encoding="utf-8")
        assert "plan run repair check push doctor" in agents_md

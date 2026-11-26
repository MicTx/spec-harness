"""Pin the autonomy hardening that remains in the minimal runtime."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_plan_ladder_frontloaded():
    planning = (ROOT / "references/plan.md").read_text(encoding="utf-8")
    contract = (ROOT / "references/exec.md").read_text(encoding="utf-8")
    assert "a card disk evidence can answer is a planning defect" in planning
    assert "resolve from disk evidence first" in planning
    assert "Every ask that does reach the user carries its ledger" in contract
    assert "an ask without that record is a violation" in contract


def test_surfaces_evidence_ledger():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    hook = (ROOT / "hooks/spec_hook.py").read_text(encoding="utf-8")
    router = (ROOT / "scripts/route.py").read_text(encoding="utf-8")
    assert "two-call quick path" in installer
    assert "List what you already tried" in hook
    assert "Every ask carries its evidence ledger" in router

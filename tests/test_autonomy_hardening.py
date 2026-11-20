"""Pin the autonomy hardening: planning ladder, evidence ledger, aggressive tier."""

from __future__ import annotations

from pathlib import Path

import route

from tests.test_prompt_tier import run_installer
from tests.v3_support import repo, started_package

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
    assert "asks carry their evidence ledger" in installer
    assert "List what you already tried" in hook
    assert "Every ask carries its evidence ledger" in router


def test_aggressive_tier_budget(tmp_path, monkeypatch):
    repo(tmp_path)
    started_package(tmp_path)
    monkeypatch.setenv("SPEC_PROMPT_TIER", "aggressive")
    brief = route.brief(tmp_path)
    assert len(brief) <= 700
    assert route.SPARK_MARKER in brief


def test_aggressive_install_end_to_end(tmp_path):
    result = run_installer(tmp_path, {"SPEC_PROMPT_TIER": "aggressive"})
    assert result.returncode == 0, result.stderr
    installed = tmp_path / ".claude" / "skills" / "spec"
    marker = (installed / ".spec-skill-install").read_text(encoding="utf-8")
    assert "tier=aggressive" in marker
    import json

    manifest = json.loads((installed / "install-manifest.json").read_text(encoding="utf-8"))
    assert manifest["prompt_tier"] == "aggressive"
    run_body = (tmp_path / ".claude" / "commands" / "spec" / "run.md").read_text(encoding="utf-8")
    assert "evidence ledger" in run_body

"""Distribution license, bilingual entry and public payload hygiene contracts."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import export_public_repo as public  # noqa: E402
from payload_contract import ROOT_FILES  # noqa: E402

LICENSE_ID = "PolyForm-Noncommercial-1.0.0"


def test_runtime_contains_license_and_metadata_agrees():
    assert "LICENSE" in ROOT_FILES
    assert "PolyForm Noncommercial License 1.0.0" in (ROOT / "LICENSE").read_text()
    metadata = (ROOT / "pyproject.toml").read_text()
    assert LICENSE_ID in metadata
    assert "OSI Approved" not in metadata
    for name in ("README.md", "README-en.md"):
        text = (ROOT / name).read_text()
        assert "PolyForm Noncommercial 1.0.0" in text
    assert "商业使用需另行书面授权" in (ROOT / "README.md").read_text()
    assert "Commercial use requires separate written authorization" in (ROOT / "README-en.md").read_text()


@pytest.mark.parametrize(
    "relative",
    [
        "scripts/.env.local",
        "tests/.agents/state.json",
        "docs/.pi/session.json",
        "server/logs/server.log",
        "agents/private.key",
        "scripts/.claude/settings.json",
        "tests/backups/config.bak",
        "docs/.codex/state.json",
        "server/.cursor/config.json",
    ],
)
def test_public_file_selection_excludes_nested_private_entries(tmp_path, relative):
    # Minimal complete input tree, no real secrets in this fixture.
    for name in public.PUBLIC_ROOT_FILES + public.PUBLIC_GITHUB_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    for name in public.PUBLIC_DIRS:
        (tmp_path / name).mkdir(exist_ok=True)
    private = tmp_path / relative
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("private fixture", encoding="utf-8")
    assert relative not in {name for _, name in public._safe_files(tmp_path)}


def test_public_snapshot_strips_unreviewed_skill_attribution(tmp_path):
    output = tmp_path / "public"
    public.build_public_tree(ROOT, output)
    assert "\u2063" not in (output / "SKILL.md").read_text(encoding="utf-8")
    assert (output / "README-en.md").is_file()
    assert LICENSE_ID in (output / "pyproject.toml").read_text()
    skill = output / "SKILL.md"
    skill.write_text(skill.read_text() + "\u2063\u2060\u200b\u2063", encoding="utf-8")
    clean = tmp_path / "clean"
    public.build_public_tree(output, clean)
    assert "\u2063" not in (clean / "SKILL.md").read_text()


def test_public_snapshot_rejects_unknown_license(tmp_path):
    (tmp_path / "LICENSE").write_text("not the approved license", encoding="utf-8")
    with pytest.raises(public.PublicExportError, match="PolyForm"):
        public.build_public_tree(tmp_path, tmp_path.parent / (tmp_path.name + "-out"))

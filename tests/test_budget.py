from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = 1024


def test_model_entry_and_references_fit_budget():
    skill = ROOT / "SKILL.md"
    references = list((ROOT / "references").glob("*.md"))
    assert skill.stat().st_size <= 4 * KB
    assert sum(path.stat().st_size for path in references) <= 16 * KB
    assert {path.name for path in references} == {"plan.md", "exec.md", "format.md", "philosophy.md", "git.md"}


def test_runtime_scripts_fit_budget():
    from payload_contract import SCRIPT_BLACKLIST

    paths = [path for path in (ROOT / "scripts").glob("*.py") if path.name not in SCRIPT_BLACKLIST]
    lines = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in paths)
    assert lines <= 6000

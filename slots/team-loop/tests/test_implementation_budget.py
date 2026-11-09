"""D9: the team-loop state implementation and CLI stay within one shared budget."""

from pathlib import Path

SLOT = Path(__file__).resolve().parents[1]
SCRIPTS = SLOT / "scripts"
# loop_control.py and loop_route.py predate D9 as separate modules. Everything
# else in scripts/ is the state implementation and its CLI, so a split cannot
# dodge the 600-line ceiling by moving logic into a new sibling file.
NON_STATE = {"loop_control.py", "loop_route.py"}
BUDGET = 600


def test_state_implementation_and_cli_fit_600_lines() -> None:
    counts = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(SCRIPTS.glob("*.py"))
        if path.name not in NON_STATE
    }
    assert counts, "no state implementation found"
    assert sum(counts.values()) <= BUDGET, counts

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SLUG = "2026-11-10_add-v3-fixture"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def repo(root: Path) -> None:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    git(root, "checkout", "-qb", f"spec/{SLUG}")


def make_package(root: Path, verify: str = "true", expect: str = "green") -> Path:
    package = root / ".spec" / "specs" / SLUG
    package.mkdir(parents=True)
    spec = f"""# Fixture
<!-- spec-format: 3 -->
## Goal
- goal: Change the fixture value
- literal ask: Test v3
- done means: The command passes
## Charter
- V1 priority: quality > maintainability > speed > cost | card
- V2 scope: adjacent-fixes | charter.md
- V3 verification: project | charter.md
- V4 finish: commit | charter.md
- V5 dependencies: none-new | charter.md
- V6 compatibility: break-ok | charter.md
- V7 blocked: park-and-continue | charter.md
## Scope
- in: app.py
- in: tests/**
- out: publication
## Assumptions
- A1 | app exists | probe: `test -f app.py` | CONFIRMED
## Forks
- F1 | when: unrelated publication | a) park b) publish | pick: a | by: user current | reversal: irreversible
## Criteria
- C1 | Fixture works | task-value
## Decisions
## Parked
## Binding
- branch: `spec/{SLUG}`
"""
    (package / "spec.md").write_text(spec, encoding="utf-8")
    (package / "tasks.md").write_text(
        f"""- [ ] Change value
  - id: task-value
  - boundary: app.py
  - verify: `{verify}`
  - expect: {expect}
""",
        encoding="utf-8",
    )
    return package


def cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "spec.py"), "--root", str(root), *args],
        cwd=root,
        capture_output=True,
        text=True,
    )

"""pytest 共享夹具。"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
HOOKS_DIR = SKILL_DIR / "hooks"

for p in (str(SCRIPTS_DIR), str(HOOKS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

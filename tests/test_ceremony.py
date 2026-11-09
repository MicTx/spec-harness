"""Ceremony budget: a default (LITE) package goes from init to archive in four
script calls and at most ten human-filled fields."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SLUG = "2026-11-08_add-ceremony-probe"
MAX_SCRIPT_CALLS = 4
MAX_FILLED_FIELDS = 10
PROBE = "`python3 -c \"import pathlib; assert pathlib.Path('README').exists()\"`"

SPEC_FILL = {
    "one sentence describing the problem this project solves": "验证默认任务包的仪式成本",
    "- Assumption A": "- 默认模板足以通过门禁",
    "- [ ] Question A": "- none",
    "- explicitly list what this round will not do": "- 不改业务代码",
    "- simplest viable approach: xxx": "- simplest viable approach: 只填模板必填项",
    "<local test or static check command for the changed area>": "python3 -m pytest -q tests/test_ceremony.py",
    "- [ ] Criterion A met -> verify: task_0001": "- [x] README 存在 -> verify: task_0001",
}
TASK_VERIFY_PREFIX = "  - verify: `python3 -m pytest -q tests/test_"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _script(root: Path, name: str, *args: str) -> subprocess.CompletedProcess:
    # --root trails the subcommand (spec_verify declares it on each subparser).
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args, "--root", str(root)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{name}\n{result.stdout}\n{result.stderr}"
    return result


def _fill_tasks(text: str) -> tuple[str, int]:
    lines, filled = [], 0
    for line in text.splitlines():
        if line.startswith(TASK_VERIFY_PREFIX):
            line, filled = f"  - verify: {PROBE}", filled + 1
        if line.strip() == "- [ ] Deliver the smallest end-to-end slice of core feature A":
            line, filled = "- [ ] 验证默认任务包仪式成本", filled + 1
        lines.append(line.replace("- [ ]", "- [x]"))
    return "\n".join(lines) + "\n", filled


def test_lite_package_reaches_archive_within_the_ceremony_budget(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "ceremony@example.invalid")
    _git(tmp_path, "config", "user.name", "Ceremony")
    (tmp_path / "README").write_text("probe\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")

    calls = 0
    _script(tmp_path, "init_spec_package.py", "--slug", SLUG, "--title", "Ceremony Probe")
    calls += 1

    package = tmp_path / ".spec" / "specs" / SLUG
    spec_path = package / "spec.md"
    spec = spec_path.read_text(encoding="utf-8")
    assert "- Workflow: LITE" in spec
    for placeholder, value in SPEC_FILL.items():
        assert placeholder in spec, placeholder
        spec = spec.replace(placeholder, value)
    spec_path.write_text(spec, encoding="utf-8")
    tasks, task_fields = _fill_tasks((package / "tasks.md").read_text(encoding="utf-8"))
    (package / "tasks.md").write_text(tasks, encoding="utf-8")
    assert len(SPEC_FILL) + task_fields <= MAX_FILLED_FIELDS

    _script(tmp_path, "spec_verify.py", "run", "--slug", SLUG, "--all")
    calls += 1
    _script(tmp_path, "check_spec_package.py", "--slug", SLUG)
    calls += 1
    _script(tmp_path, "complete_spec_package.py", "--slug", SLUG, "--archive")
    calls += 1

    assert calls <= MAX_SCRIPT_CALLS
    assert (tmp_path / ".spec" / "specs" / "archive" / SLUG / "completion-summary.md").is_file()

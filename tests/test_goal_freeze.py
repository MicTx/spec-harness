"""Go-freeze integrity: goal_fp, the Go-time command table and authorized revisions.

Four red/green proofs:

- a Goal-section rewrite after plan-go is blocked while a Criteria edit is not;
- a strict package rejects an unauthorized verify change until a ``by: user``
  amend record exists, and rejects ``by: V1`` there;
- an adjacent-fixes package reports ``use plan-amend --revise``, accepts a
  ``by: V1`` amend, and ``plan-amend --revise`` replaces a checked card in
  place using the package's own V2 even after the shared charter drifts;
- a failed first plan-go round followed by a corrected round reaches a green
  gate, because the command table keeps the last per-task baseline record.
"""

from __future__ import annotations

import json
from pathlib import Path

import gate
import package

from tests.v3_support import SLUG, cli, make_package, repo


def _records(pkg: Path) -> list[dict]:
    path = pkg / "runs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _errors(root: Path) -> list[str]:
    return gate.compute(root, SLUG)["errors"]


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"expected exactly one {old!r} in {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _strict_package(root: Path, verify: str = "true") -> Path:
    """Build a V2=strict package before plan-go so the package charter is strict."""
    pkg = make_package(root, verify=verify)
    _edit(pkg / "spec.md", "- V2 scope: adjacent-fixes | charter.md", "- V2 scope: strict | charter.md")
    return pkg


def test_goal_edit_after_go_is_blocked(tmp_path):
    repo(tmp_path)
    pkg = make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0
    spec_path = pkg / "spec.md"

    _edit(spec_path, "- goal: Change the fixture value", "- goal: Change a different fixture value")
    assert any("problem definition changed after plan-go" in error for error in _errors(tmp_path))

    _edit(spec_path, "- goal: Change a different fixture value", "- goal: Change the fixture value")
    _edit(spec_path, "- C1 | Fixture works | task-value", "- C1 | Fixture still works | task-value")
    assert not any("problem definition changed after plan-go" in error for error in _errors(tmp_path))


def test_strict_verify_change_requires_user_authorization(tmp_path):
    repo(tmp_path)
    pkg = _strict_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0
    assert package.package_baseline(pkg)["charter"]["V2"] == "strict"

    _edit(pkg / "tasks.md", "verify: `true`", "verify: `echo revised`")
    errors = _errors(tmp_path)
    assert any("verify changed after plan-go" in error for error in errors)
    assert not any("use plan-amend --revise" in error for error in errors)

    card = tmp_path / ".spec" / "revise-card.md"
    card.write_text(
        "- [ ] Tighten the check\n  - id: task-value\n  - verify: `echo revised`\n  - expect: green\n",
        encoding="utf-8",
    )
    rejected = cli(tmp_path, "plan-amend", "--revise", "--file", str(card), "--by", "V1")
    assert rejected.returncode == 1
    assert "does not accept" in rejected.stderr
    assert not [record for record in _records(pkg) if record.get("kind") == "amend"]

    package.append_run(
        pkg,
        {"kind": "amend", "task": "task-value", "cmd": "echo revised", "by": "V1", "ts": package.now()},
    )
    assert any("verify changed after plan-go" in error for error in _errors(tmp_path))

    package.append_run(
        pkg,
        {"kind": "amend", "task": "task-value", "cmd": "echo revised", "by": "user reviewer-1", "ts": package.now()},
    )
    assert not any("verify changed after plan-go" in error for error in _errors(tmp_path))


def test_failed_round_retry_reaches_green_gate(tmp_path):
    repo(tmp_path)
    pkg = make_package(tmp_path, verify="test -f missing.txt")

    first = cli(tmp_path, "plan-go")
    assert first.returncode == 1
    assert not package.package_baseline(pkg)
    baselines = [record for record in _records(pkg) if record.get("kind") == "baseline"]
    assert [record["task"] for record in baselines] == ["task-value"]
    assert baselines[0]["cmd"] == "test -f missing.txt" and baselines[0]["exit"] != 0

    _edit(pkg / "tasks.md", "verify: `test -f missing.txt`", "verify: `true`")
    assert cli(tmp_path, "plan-go").returncode == 0
    assert package.package_baseline(pkg)["goal_fp"]
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    errors = _errors(tmp_path)
    assert not any("verify changed after plan-go" in error for error in errors)
    assert not any("problem definition changed after plan-go" in error for error in errors)
    checked = cli(tmp_path, "check", "--check-only")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)["passed"] is True


def test_adjacent_fixes_revise_records_authorization(tmp_path):
    repo(tmp_path)
    pkg = make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    tasks_path = pkg / "tasks.md"
    assert package.parse_tasks(tasks_path.read_text(encoding="utf-8"))[0].checked is True

    # The shared charter drifts to strict after Go; the package's own V2 still governs.
    (tmp_path / ".spec" / "charter.md").write_text("- V2 scope: strict\n", encoding="utf-8")

    _edit(tasks_path, "verify: `true`", "verify: `echo revised`")
    errors = _errors(tmp_path)
    assert any("use plan-amend --revise" in error for error in errors)
    assert not any("verify changed after plan-go" in error for error in errors)

    card = tmp_path / ".spec" / "revise.md"
    card.write_text(
        "- [ ] Replace the fixture check\n"
        "  - id: task-value\n"
        "  - boundary: other.py\n"
        "  - verify: `echo revised`\n"
        "  - expect: green\n"
        "  - origin: emergent\n",
        encoding="utf-8",
    )
    before = tasks_path.read_text(encoding="utf-8")
    unauthorized = cli(tmp_path, "plan-amend", "--revise", "--file", str(card))
    assert unauthorized.returncode == 1
    assert "--by" in unauthorized.stderr
    assert tasks_path.read_text(encoding="utf-8") == before

    revised = cli(tmp_path, "plan-amend", "--revise", "--file", str(card), "--by", "V1")
    assert revised.returncode == 0, revised.stderr

    replaced = package.parse_tasks(tasks_path.read_text(encoding="utf-8"))[0]
    assert replaced.id == "task-value"
    assert replaced.checked is False
    assert replaced.title == "Replace the fixture check"
    assert replaced.boundary == "app.py"  # Go froze the task scope
    assert replaced.depends_on == ()
    assert replaced.origin == "planned"  # not the card's requested origin
    assert replaced.command == "echo revised"
    assert tasks_path.read_text(encoding="utf-8").startswith("- [ ] Replace the fixture check\n")

    amends = [record for record in _records(pkg) if record.get("kind") == "amend"]
    assert len(amends) == 1
    assert amends[0]["task"] == "task-value"
    assert amends[0]["by"] == "V1"
    assert amends[0]["cmd"] == "echo revised"

    assert not any("use plan-amend --revise" in error for error in _errors(tmp_path))
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    checked = cli(tmp_path, "check", "--check-only")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)["passed"] is True

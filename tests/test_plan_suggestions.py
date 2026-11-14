"""plan-lint --suggest: read-only refinement proposals for red/green traps."""

from __future__ import annotations

import json
from pathlib import Path

from tests.v3_support import cli, make_package, repo

ROOT = Path(__file__).resolve().parents[1]

PAIR = (
    "- [ ] Write the failing test\n"
    "  - id: task-foo-test\n"
    "  - boundary: new:tests/test_foo.py\n"
    "  - verify: `python3 -m pytest tests/test_foo.py -q`\n"
    "  - expect: red\n"
    "- [ ] Implement until green\n"
    "  - id: task-foo\n"
    "  - boundary: app.py\n"
    "  - verify: `python3 -m pytest tests/test_foo.py -q`\n"
    "  - expect: green\n"
    "  - depends-on: task-foo-test\n"
)


def _pair_repo(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- C1 | Fixture works | task-value",
            "- C1 | Test card | task-foo-test\n- C2 | Implementation | task-foo",
        ),
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(PAIR, encoding="utf-8")
    return package


def test_suggest_reports_shared_command_pair(tmp_path):
    package = _pair_repo(tmp_path)
    before = {name: (package / name).read_bytes() for name in ("spec.md", "tasks.md")}
    result = cli(tmp_path, "plan-lint", "--static", "--suggest")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    suggestion = next(item for item in payload["suggestions"] if item["rule"] == "SUGGEST-001")
    assert suggestion["task"] == "task-foo-test"
    assert "--collect-only" in suggestion["proposed_verify"]
    assert all((package / name).read_bytes() == data for name, data in before.items())


def test_suggest_reports_new_file_red_verify(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("- C1 | Fixture works | task-value", "- C1 | Solo | task-solo"),
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [ ] Solo red\n  - id: task-solo\n  - boundary: new:tests/test_solo.py\n"
        "  - verify: `python3 -m pytest tests/test_solo.py -q`\n  - expect: red\n",
        encoding="utf-8",
    )
    result = cli(tmp_path, "plan-lint", "--static", "--suggest")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    suggestion = next(item for item in payload["suggestions"] if item["rule"] == "SUGGEST-002")
    assert suggestion["task"] == "task-solo"
    assert suggestion["proposed_verify"] == "python3 -m pytest tests/test_solo.py --collect-only -q"


def test_suggest_is_silent_without_flag(tmp_path):
    _pair_repo(tmp_path)
    result = cli(tmp_path, "plan-lint", "--static")
    assert result.returncode == 0
    assert "suggestions" not in json.loads(result.stdout)


def test_suggest_never_changes_readiness_or_exit(tmp_path):
    _pair_repo(tmp_path)
    plain = cli(tmp_path, "plan-lint", "--static")
    suggested = cli(tmp_path, "plan-lint", "--static", "--suggest")
    assert plain.returncode == suggested.returncode == 0
    assert json.loads(plain.stdout)["ready"] == json.loads(suggested.stdout)["ready"]


def test_docs_suggestion_mode():
    plan = (ROOT / "references" / "plan.md").read_text(encoding="utf-8")
    assert "--suggest" in plan
    assert "SUGGEST-001" in plan

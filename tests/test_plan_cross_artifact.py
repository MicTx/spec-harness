"""Pin tests for cross-artifact plan lint (PLAN-011/012/013)."""

import json
from pathlib import Path

from tests.v3_support import cli, make_package, repo

ROOT = Path(__file__).resolve().parents[1]


def _gaps(result) -> list[dict]:
    return json.loads(result.stdout)["gaps"]


def _set_charter(package: Path, key: str, value: str) -> None:
    spec = package / "spec.md"
    text = spec.read_text(encoding="utf-8")
    lines = []
    for line in text.splitlines():
        if line.startswith(f"- {key}:"):
            prefix = line.split(":", 1)[0]
            line = f"{prefix}: {value} | charter.md"
        lines.append(line)
    spec.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_charter_rejects_unknown_v4_finish(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    _set_charter(package, "V4 finish", "deploy")

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 1
    gap = next(gap for gap in _gaps(result) if gap["rule"] == "PLAN-011")
    assert "V4" in gap["detail"]
    assert "commit" in gap["detail"] and "push" in gap["detail"]


def test_charter_rejects_unknown_v2_scope(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    _set_charter(package, "V2 scope", "anything-goes")

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 1
    gap = next(gap for gap in _gaps(result) if gap["rule"] == "PLAN-011")
    assert "adjacent-fixes" in gap["detail"]


def test_charter_rejects_unknown_v5_dependencies(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    _set_charter(package, "V5 dependencies", "vendored")

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 1
    assert any(gap["rule"] == "PLAN-011" and "V5" in gap["detail"] for gap in _gaps(result))


def test_charter_accepts_complete_fixture(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not [gap for gap in _gaps(result) if gap["rule"] == "PLAN-011"]


def test_reference_in_goal_must_resolve(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- done means: The command passes",
            "- done means: The command passes after task-missing lands",
        ),
        encoding="utf-8",
    )

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 1
    gap = next(gap for gap in _gaps(result) if gap["rule"] == "PLAN-012")
    assert "task-missing" in gap["detail"]


def test_reference_valid_passes(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- done means: The command passes",
            "- done means: The command passes once task-value is verified",
        ),
        encoding="utf-8",
    )

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 0, result.stdout + result.stderr


def test_stable_id_rejects_positional_fallback(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    tasks = package / "tasks.md"
    tasks.write_text(
        tasks.read_text(encoding="utf-8").replace("  - id: task-value\n", ""),
        encoding="utf-8",
    )
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("| task-value", "| task_0001"),
        encoding="utf-8",
    )

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 1
    gap = next(gap for gap in _gaps(result) if gap["rule"] == "PLAN-013")
    assert "task_0001" in gap["detail"]


def test_docs_describe_new_rules():
    plan = (ROOT / "references" / "plan.md").read_text(encoding="utf-8")
    fmt = (ROOT / "references" / "format.md").read_text(encoding="utf-8")
    for code in ("PLAN-011", "PLAN-012", "PLAN-013"):
        assert code in plan, code
    assert "never renumbered" in fmt

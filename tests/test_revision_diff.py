"""Pin tests for spec.py diff — structured revision summary vs the plan-go baseline."""

import json
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo

ROOT = Path(__file__).resolve().parents[1]


def _summary(root: Path) -> dict:
    result = cli(root, "diff")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_diff_clean_summary(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0

    summary = _summary(tmp_path)

    assert summary["slug"] == SLUG
    assert len(summary["baseline"]) == 40
    assert summary["files"] == []
    assert summary["spec"]["added"] == 0 and summary["spec"]["changed"] is False
    assert summary["tasks"]["added"] == 0 and summary["tasks"]["changed"] is False
    assert summary["task_ids"] == {"added": [], "removed": []}


def test_diff_summary_tracks_revisions(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0
    tasks = package / "tasks.md"
    tasks.write_text(
        tasks.read_text(encoding="utf-8")
        + "- [ ] Second task\n  - id: task-second\n  - boundary: app.py\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- done means: The command passes",
            "- done means: The command passes twice",
        ),
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    summary = _summary(tmp_path)

    paths = {item["path"] for item in summary["files"]}
    assert "app.py" in paths
    assert summary["spec"]["changed"] is True
    assert summary["tasks"]["added"] >= 1
    assert summary["task_ids"]["added"] == ["task-second"]
    assert summary["task_ids"]["removed"] == []


def test_diff_unfrozen_error(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)

    result = cli(tmp_path, "diff")

    assert result.returncode == 1
    assert "plan-go" in result.stderr


def test_diff_removed_lines_reported(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0
    spec = package / "spec.md"
    lines = [line for line in spec.read_text(encoding="utf-8").splitlines() if not line.startswith("- done means")]
    spec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tasks = package / "tasks.md"
    tasks.write_text(
        "- [ ] Shrink\n  - id: task-value\n  - boundary: app.py\n  - verify: `true`\n",
        encoding="utf-8",
    )

    summary = _summary(tmp_path)

    assert summary["spec"]["removed"] >= 1
    assert summary["tasks"]["removed"] >= 1


def test_docs_diff_projection():
    execution = (ROOT / "references" / "exec.md").read_text(encoding="utf-8")
    assert "spec.py diff" in execution

"""Each fanout item validates decision integrity before accepting its output."""

import json
import subprocess
import sys

import pytest
import workflow_fanout as fanout
from route_spec_package import route_for_package


@pytest.mark.parametrize("mutation", ["close", "downgrade", "delete"])
@pytest.mark.parametrize("classified", [False, True], ids=["unclassified", "decision"])
def test_fanout_worker_decision_mutation_is_abnormal_and_blocks_route(tmp_path, monkeypatch, mutation, classified):
    slug = "2026-11-07_decision-guard"
    package = tmp_path / ".spec/specs" / slug
    package.mkdir(parents=True)
    question = "[ ] [decision] Choose tier | reason: preference" if classified else "[ ] Choose tier"
    content = f"<!-- spec-format: 2 -->\n# Test\n### 2.3 待确认问题\n- {question}\n"
    spec_path = package / "spec.md"
    spec_path.write_text(content)
    (package / "tasks.md").write_text("- [ ] Work\n  - boundary: local\n  - verify: `python3 --version`\n")
    changes = {
        "close": "[x] [decision] Choose tier | reason: preference | answer: free | authority: user:claimed approval",
        "downgrade": "[ ] [fact] Choose tier | task: task_0001",
        "delete": "无",
    }
    if not classified:
        changes["close"] = "[x] Choose tier"
    command = [
        sys.executable,
        "-B",
        "-c",
        "from pathlib import Path; "
        f"path=Path({str(spec_path)!r}); "
        f"path.write_text(path.read_text().replace({question!r}, {changes[mutation]!r})); print('completed')",
    ]
    monkeypatch.setattr(fanout, "build_command", lambda *_args: command)
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    if not sys.platform.startswith("linux"):
        monkeypatch.setattr(
            fanout,
            "_run_bounded",
            lambda cmd, timeout: (subprocess.run(cmd, capture_output=True, text=True, timeout=timeout), False, False),
        )
    result = fanout.run_item("pi", {"id": "decision", "prompt": "inspect"}, out_dir, 10)
    assert not result["ok"] and result["returncode"] == -1
    assert "decision 问题发生变化" in result["error"]
    routed = json.loads(route_for_package(tmp_path, slug, output_format="json"))
    assert routed["stage"] == "status"
    assert any("受控 worker" in error for error in routed["questionErrors"])

import json
import subprocess
import sys
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import evidence  # noqa: E402


def test_verify_ticks_and_creates_checkpoint(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    result = cli(tmp_path, "verify", "task-value")
    assert result.returncode == 0, result.stderr
    tasks = (tmp_path / ".spec/specs" / SLUG / "tasks.md").read_text()
    assert "- [x] Change value" in tasks
    runs = [json.loads(line) for line in (tmp_path / ".spec/specs" / SLUG / "runs.jsonl").read_text().splitlines()]
    assert runs[-1]["exit"] == 0
    assert runs[-1]["sig"]


def test_checkpoint_skips_ignored_optional_memory_files(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    (tmp_path / ".gitignore").write_text(".spec/charter.md\n.spec/lessons.md\n")
    (tmp_path / ".spec/charter.md").write_text("# ignored\n")
    (tmp_path / ".spec/lessons.md").write_text("# ignored\n")
    result = cli(tmp_path, "verify", "task-value")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".spec/charter.md").read_text() == "# ignored\n"


def test_failure_changes_strategy_instead_of_repeating(tmp_path):
    repo(tmp_path)
    make_package(tmp_path, verify="false")
    first = cli(tmp_path, "verify", "task-value")
    second = cli(tmp_path, "verify", "task-value")
    assert "smallest fix" in first.stdout
    assert "minimal reproduction" in second.stdout


def test_final_evidence_is_explicitly_marked(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    result = cli(tmp_path, "done")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".spec/specs/archive" / SLUG / "completion-summary.md").is_file()


def test_timeout_is_recorded_as_failure_evidence(tmp_path, monkeypatch):
    repo(tmp_path)
    package = make_package(tmp_path, verify="python3 -c 'import time; time.sleep(3)'")

    class StubSubprocess:
        TimeoutExpired = subprocess.TimeoutExpired
        CompletedProcess = subprocess.CompletedProcess

        @staticmethod
        def run(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("stub", 0.01)

    monkeypatch.setattr(evidence, "subprocess", StubSubprocess)
    result = evidence.run_tasks(tmp_path, SLUG, ["task-value"])
    record = result["results"][0]
    assert result["ready"] is False
    assert record["exit"] == 124
    assert record["sig"]
    assert record["directive"].startswith("Verify timed out.")
    assert (package / "runs.jsonl").is_file()

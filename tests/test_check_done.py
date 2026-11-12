import json
import subprocess

from tests.v3_support import SLUG, cli, make_package, repo


def _finished_package(tmp_path, finish="commit"):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec_path = package / "spec.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace("V4 finish: commit", f"V4 finish: {finish}"),
        encoding="utf-8",
    )
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    return package


def test_check_passes_and_runs_done_automatically(tmp_path):
    _finished_package(tmp_path)

    result = cli(tmp_path, "check")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["archive"].endswith(f".spec/specs/archive/{SLUG}")
    assert payload["finish"] == "commit"
    assert (tmp_path / ".spec/specs/archive" / SLUG / "completion-summary.md").is_file()
    assert not (tmp_path / ".spec/specs" / SLUG).exists()
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout
    assert f"Spec: {SLUG}" in log


def test_check_only_reports_gate_without_archiving(tmp_path):
    _finished_package(tmp_path)

    result = cli(tmp_path, "check", "--check-only")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert "archive" not in payload
    assert (tmp_path / ".spec/specs" / SLUG / "spec.md").is_file()
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_check_cures_stale_evidence_and_delivers(tmp_path):
    _finished_package(tmp_path)
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    probe = cli(tmp_path, "check", "--check-only")
    assert any("stale" in error for error in json.loads(probe.stdout)["errors"])

    result = cli(tmp_path, "check")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["errors"] == []
    assert (tmp_path / ".spec/specs/archive" / SLUG / "completion-summary.md").is_file()


def test_check_failing_gate_does_not_archive(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0

    result = cli(tmp_path, "check")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert payload["errors"]
    assert (tmp_path / ".spec/specs" / SLUG / "spec.md").is_file()
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_check_done_chains_push_delivery(tmp_path):
    _finished_package(tmp_path, finish="push")
    remote = tmp_path.parent / f"{tmp_path.name}-remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=tmp_path, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=tmp_path, check=True)

    result = cli(tmp_path, "check")

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["finish"] == "push"
    assert payload["push"]["pushed"] is True
    assert (tmp_path / ".spec/specs/archive" / SLUG / "completion-summary.md").is_file()

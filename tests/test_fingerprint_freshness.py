"""Product fingerprints and evidence remain fresh within one process."""

from __future__ import annotations

import json
import shlex
import stat
import sys
from pathlib import Path

import gitx
import package
import plan
import pytest

from tests.v3_support import SLUG, cli, git, make_package, repo


@pytest.fixture(params=["tracked", "untracked"])
def product_change(tmp_path, request):
    """Two content versions with identical porcelain status and HEAD."""
    repo(tmp_path)
    name = "app.py" if request.param == "tracked" else "new.txt"
    before = "VALUE = 1\n"
    after = "VALUE = 2\n"
    path = tmp_path / name
    path.write_text(before, encoding="utf-8")
    return path, before, after


def _status(root: Path) -> bytes:
    return gitx.git(
        root,
        "status",
        "--porcelain",
        "-z",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude).spec",
        text=False,
    ).stdout


def _command(marker: Path, target: Path, expected: str) -> str:
    """Count real command executions outside the product tree, then check content."""
    code = (
        f"from pathlib import Path; Path({str(marker)!r}).open('a').write('x'); "
        f"assert Path({target.name!r}).read_text(encoding='utf-8') == {expected!r}"
    )
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def _count(marker: Path) -> int:
    return len(marker.read_text(encoding="utf-8")) if marker.exists() else 0


def _records(directory: Path) -> list[dict]:
    return [json.loads(line) for line in (directory / "runs.jsonl").read_text(encoding="utf-8").splitlines() if line]


def test_repeated_product_edits_refresh_fingerprint(tmp_path, product_change):
    path, _before, after = product_change
    status = _status(tmp_path)
    expected = b" M app.py\0" if path.name == "app.py" else b"?? new.txt\0"
    assert status == expected
    first = package.tree_fingerprint(tmp_path)
    path.write_text(after, encoding="utf-8")
    assert _status(tmp_path) == status
    second = package.tree_fingerprint(tmp_path)
    assert second != first
    path.write_text("VALUE = 3\n", encoding="utf-8")
    assert _status(tmp_path) == status
    assert package.tree_fingerprint(tmp_path) != second


def test_assume_unchanged_still_refreshes_fingerprint(tmp_path):
    repo(tmp_path)
    git(tmp_path, "update-index", "--assume-unchanged", "app.py")
    assert _status(tmp_path) == b""
    first = package.tree_fingerprint(tmp_path)
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert _status(tmp_path) == b""
    assert package.tree_fingerprint(tmp_path) != first


def test_filemode_false_still_tracks_executable_bit(tmp_path):
    repo(tmp_path)
    git(tmp_path, "config", "core.fileMode", "false")
    path = tmp_path / "app.py"
    first = package.tree_fingerprint(tmp_path)
    mode = path.stat().st_mode
    path.chmod(mode ^ stat.S_IXUSR)
    if bool(path.stat().st_mode & stat.S_IXUSR) == bool(mode & stat.S_IXUSR):
        pytest.skip("filesystem does not support executable-bit changes")
    assert _status(tmp_path) == b""
    assert package.tree_fingerprint(tmp_path) != first


def test_final_verify_reruns_a_changed_tree_and_gate_rejects_the_failure(tmp_path, product_change):
    path, before, after = product_change
    marker = tmp_path.parent / (tmp_path.name + "-verify-count")
    command = _command(marker, path, before)
    directory = make_package(tmp_path, verify=command)
    if path.name != "app.py":
        spec, tasks = directory / "spec.md", directory / "tasks.md"
        spec.write_text(spec.read_text().replace("- in: app.py", f"- in: app.py, {path.name}"))
        tasks.write_text(tasks.read_text().replace("boundary: app.py", f"boundary: app.py, new:{path.name}"))
    assert plan.go(tmp_path, SLUG)["ready"] is True
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    first_fp = package.latest_runs(directory)["task-value"]["tree_fp"]
    path.write_text(after, encoding="utf-8")

    probe = json.loads(cli(tmp_path, "check", "--check-only").stdout)
    assert probe["passed"] is True and probe["verification"] == "pending"
    assert _count(marker) == 2
    failed = cli(tmp_path, "check")
    assert failed.returncode == 1 and _count(marker) == 3
    assert "final verify did not pass" in failed.stderr
    final = package.latest_runs(directory)["task-value"]
    assert final["final"] and final["exit"] != 0 and final["tree_fp"] != first_fp


def test_plan_lint_reruns_a_changed_tree_and_reports_failure(tmp_path, product_change):
    path, before, after = product_change
    marker = tmp_path.parent / (tmp_path.name + "-plan-count")
    command = _command(marker, path, before)
    directory = make_package(tmp_path, verify=command)

    first = plan.lint(tmp_path, SLUG, record=True)
    assert first["ready"] is True
    assert _count(marker) == 1
    record = _records(directory)[-1]
    assert record["kind"] == "baseline" and record["exit"] == 0
    status = _status(tmp_path)
    assert plan.lint(tmp_path, SLUG)["ready"] is True
    assert _count(marker) == 1  # A genuinely unchanged tree still reuses green.
    path.write_text(after, encoding="utf-8")
    assert _status(tmp_path) == status

    second = plan.lint(tmp_path, SLUG)
    assert _count(marker) == 2
    assert second["ready"] is False
    assert any(gap["rule"] == "PLAN-001" and gap.get("task") == "task-value" for gap in second["gaps"])
    assert len(_records(directory)) == 1  # Ordinary plan-lint remains read-only.


@pytest.mark.parametrize("tracked", [True, False])
def test_architecture_contract_content_changes_fingerprint(tmp_path, tracked):
    """Architecture source changes invalidate evidence; local records do not."""
    repo(tmp_path)
    contract = tmp_path / ".spec" / "architecture" / "contracts.json"
    contract.parent.mkdir(parents=True)
    contract.write_text('{"version": 1}\n', encoding="utf-8")
    if tracked:
        git(tmp_path, "add", ".spec/architecture/contracts.json")
        git(tmp_path, "commit", "-qm", "contracts")
    first = package.tree_fingerprint(tmp_path)
    directory = make_package(tmp_path)
    package.append_run(directory, {"kind": "gate", "passed": True})
    assert package.tree_fingerprint(tmp_path) == first
    contract.write_text('{"version": 2}\n', encoding="utf-8")
    second = package.tree_fingerprint(tmp_path)
    assert second != first
    contract.write_text('{"version": 3}\n', encoding="utf-8")
    third = package.tree_fingerprint(tmp_path)
    assert third not in (first, second)
    contract.unlink()
    assert package.tree_fingerprint(tmp_path) != third


def test_contract_only_edit_forces_final_verify_rerun(tmp_path):
    repo(tmp_path)
    contract = tmp_path / ".spec" / "architecture" / "contracts.json"
    contract.parent.mkdir(parents=True)
    contract.write_text('{"version": 1}\n', encoding="utf-8")
    git(tmp_path, "add", ".spec/architecture/contracts.json")
    git(tmp_path, "commit", "-qm", "contracts")
    marker = tmp_path.parent / (tmp_path.name + "-contract-verify-count")
    code = (
        f"from pathlib import Path; import json; Path({str(marker)!r}).open('a').write('x'); "
        "assert json.loads(Path('.spec/architecture/contracts.json').read_text())['version'] == 1"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    directory = make_package(tmp_path, verify=command)
    spec, tasks = directory / "spec.md", directory / "tasks.md"
    path = contract.relative_to(tmp_path).as_posix()
    spec.write_text(spec.read_text().replace("- in: app.py", f"- in: app.py, {path}"))
    tasks.write_text(tasks.read_text().replace("boundary: app.py", f"boundary: app.py, {path}"))
    assert plan.go(tmp_path, SLUG)["ready"] is True
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    verified_count = _count(marker)
    contract.write_text('{"version": 2}\n', encoding="utf-8")
    changed = cli(tmp_path, "check")
    assert changed.returncode == 1 and "final verify did not pass" in changed.stderr
    assert _count(marker) == verified_count + 1
    assert package.latest_runs(directory)["task-value"]["exit"] != 0

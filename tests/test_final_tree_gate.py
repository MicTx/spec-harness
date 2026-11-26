"""Delivery executes real, deduplicated checks and binds them to Git's tree."""

import json
import subprocess
import sys

import gitx
import package
import pytest

from tests.v3_support import SCRIPTS, SLUG, cli, git, make_package, repo, started_package

LEGACY_CHECKER = SCRIPTS.parent / ".maintainer" / "legacy" / "check_all.py"
legacy_only = pytest.mark.skipif(
    not LEGACY_CHECKER.is_file(), reason="the legacy checker is maintenance-only and absent from the public export"
)


def _audit(root, *args):
    return subprocess.run(
        [sys.executable, str(LEGACY_CHECKER), "--root", str(root), *args],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_a_real_final_batch_executes_each_distinct_command_once(tmp_path):
    repo(tmp_path)
    command = "printf x >> .spec/check-count"
    directory = make_package(tmp_path, verify=command)
    spec, tasks = directory / "spec.md", directory / "tasks.md"
    spec.write_text(spec.read_text().replace("## Decisions", "- C2 | Same acceptance | task-extra\n## Decisions"))
    tasks.write_text(
        tasks.read_text()
        + f"- [ ] Check again\n  - id: task-extra\n  - boundary: app.py\n  - verify: `{command}`\n  - expect: green\n"
    )
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "--all").returncode == 0
    count = tmp_path / ".spec" / "check-count"
    before = count.read_bytes()
    probe = json.loads(cli(tmp_path, "check", "--check-only").stdout)
    assert probe["verification"] == "pending" and count.read_bytes() == before

    check = cli(tmp_path, "check")
    assert check.returncode == 0, check.stderr
    assert count.read_bytes() == before + b"x"
    final = package.latest_gate(directory.parent / "archive" / SLUG)
    assert final["product_tree"] == package.staged_product_tree(tmp_path, revision="HEAD")
    assert [(item["cmd"], item["tasks"], item["exit"]) for item in final["checks"]] == [
        (command, ["task-value", "task-extra"], 0)
    ]


def test_unowned_working_copy_cannot_make_candidate_green(tmp_path):
    repo(tmp_path)
    started_package(tmp_path, verify="test -f outside.txt", expect="red")
    unrelated = tmp_path / "outside.txt"
    unrelated.write_text("leave this intact\n")
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    before = gitx.head(tmp_path)

    check = cli(tmp_path, "check")

    assert check.returncode == 1
    assert "verify-failed" in {item["code"] for item in json.loads(check.stdout)["findings"]}
    assert gitx.head(tmp_path) == before
    assert unrelated.read_text() == "leave this intact\n"
    assert gitx.stdout(tmp_path, "diff", "--cached", "--name-only") == ""


@legacy_only
def test_tracked_archive_is_checked_against_its_delivery_receipt(tmp_path):
    repo(tmp_path)
    directory = started_package(tmp_path)
    assert cli(tmp_path, "verify", "--all").returncode == 0
    result = cli(tmp_path, "check")
    assert result.returncode == 0, result.stderr
    archive = directory.parent / "archive" / SLUG
    delivered_head = gitx.head(tmp_path)
    assert _audit(tmp_path, "--revision", delivered_head, "--slug", SLUG).returncode == 0
    summary = archive / "completion-summary.md"
    summary.write_text(summary.read_text() + "\nignored after delivery?\n")
    git(tmp_path, "add", str(summary.relative_to(tmp_path)))

    pending = _audit(tmp_path, "--index", "--slug", SLUG)
    assert pending.returncode != 0 and "receipt" in pending.stdout
    git(tmp_path, "commit", "-qm", "fixture changes archived summary")
    revision = _audit(tmp_path, "--revision", gitx.head(tmp_path), "--slug", SLUG)
    assert revision.returncode != 0 and "receipt" in revision.stdout


@legacy_only
def test_new_archive_without_a_delivery_receipt_fails_both_git_gates(tmp_path):
    repo(tmp_path)
    directory = make_package(tmp_path)
    tasks = directory / "tasks.md"
    tasks.write_text(tasks.read_text().replace("- [ ]", "- [x]"))
    (directory / "completion-summary.md").write_text("# Fixture summary\n")
    package.append_run(directory, {"task": "task-value", "cmd": "true", "exit": 0})
    archive = directory.parent / "archive" / SLUG
    archive.parent.mkdir()
    directory.rename(archive)
    git(tmp_path, "add", str(archive.relative_to(tmp_path)))

    pending = _audit(tmp_path, "--index", "--slug", SLUG)
    assert pending.returncode != 0 and "requires a verified delivery receipt" in pending.stdout
    git(tmp_path, "commit", "-qm", "synthetic incoming archive fixture")
    revision = _audit(tmp_path, "--revision", "HEAD", "--slug", SLUG)
    assert revision.returncode != 0 and "requires a verified delivery receipt" in revision.stdout


@legacy_only
def test_a_legacy_archive_is_readable_but_cannot_be_rewritten_as_legacy(tmp_path):
    repo(tmp_path)
    directory = make_package(tmp_path)
    tasks = directory / "tasks.md"
    tasks.write_text(tasks.read_text().replace("- [ ]", "- [x]"))
    (directory / "completion-summary.md").write_text("# Historical summary\n")
    package.append_run(directory, {"task": "task-value", "cmd": "true", "exit": 0})
    archive = directory.parent / "archive" / SLUG
    archive.parent.mkdir()
    directory.rename(archive)
    git(tmp_path, "add", str(archive.relative_to(tmp_path)))
    git(tmp_path, "commit", "-qm", "historical base fixture")

    historical = _audit(tmp_path, "--revision", "HEAD", "--baseline", "HEAD", "--slug", SLUG)
    assert historical.returncode == 0, historical.stdout
    summary = archive / "completion-summary.md"
    summary.write_text("# Replacement summary\n")
    git(tmp_path, "add", str(summary.relative_to(tmp_path)))
    assert _audit(tmp_path, "--index", "--slug", SLUG).returncode != 0

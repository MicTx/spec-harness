import json
import subprocess
from pathlib import Path

import local_evidence
import pytest

from tests.v3_support import SLUG, cli, make_package, repo


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _tracked(root: Path) -> list[str]:
    output = _git(root, "ls-files", "--", ".spec/specs").stdout
    return [line for line in output.splitlines() if line]


def _commit_all(root: Path, message: str = "fixture package") -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)


def test_purge_keeps_files_on_disk_and_untracks_them(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    _commit_all(tmp_path)
    before = _tracked(tmp_path)
    assert before

    probe = cli(tmp_path, "purge", "--check")

    assert probe.returncode == 0, probe.stderr
    payload = json.loads(probe.stdout)
    assert payload["dry_run"] is True
    assert payload["tracked_before"] == len(before)
    assert payload["files_on_disk"] >= len(before)
    assert _tracked(tmp_path) == before

    result = cli(tmp_path, "purge")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["local_mode"] is True
    assert payload["untracked"] == len(before)
    assert payload["tracked_after"] == 0
    assert _tracked(tmp_path) == []
    assert all((tmp_path / relative).is_file() for relative in before)
    exclude = tmp_path / ".git" / "info" / "exclude"
    assert ".spec/specs/" in exclude.read_text(encoding="utf-8")
    assert local_evidence.is_local(tmp_path) is True


def test_done_in_local_mode_archives_without_staging_evidence(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    _commit_all(tmp_path)
    assert cli(tmp_path, "purge").returncode == 0
    assert cli(tmp_path, "plan-go").returncode == 0

    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    result = cli(tmp_path, "check")

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    archive = tmp_path / ".spec/specs/archive" / SLUG
    assert (archive / "runs.jsonl").is_file()
    assert (archive / "completion-summary.md").is_file()
    assert not package.exists()
    committed = _git(tmp_path, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    assert not any(path.startswith(".spec/specs/") for path in committed)
    assert "app.py" in committed
    assert "VALUE = 1" in _git(tmp_path, "show", "HEAD:app.py").stdout


def test_default_mode_still_tracks_packages(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    _commit_all(tmp_path)

    assert local_evidence.is_local(tmp_path) is False
    assert _tracked(tmp_path)


def test_purge_is_idempotent_and_keeps_source_contracts(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    for directory in local_evidence.EVIDENCE_DIRS:
        path = tmp_path / ".spec" / directory / "note.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private record\n", encoding="utf-8")
    contract = tmp_path / ".spec/architecture/contracts.json"
    contract.parent.mkdir(parents=True)
    contract.write_text('{"entries": []}\n', encoding="utf-8")
    _commit_all(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.txt")}
    head = _git(tmp_path, "rev-parse", "HEAD").stdout

    assert cli(tmp_path, "purge").returncode == 0
    marker = local_evidence.marker_path(tmp_path)
    exclude = tmp_path / ".git/info/exclude"
    policy = (marker.read_bytes(), exclude.read_bytes())
    result = cli(tmp_path, "purge")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["untracked"] == 0
    assert policy == (marker.read_bytes(), exclude.read_bytes())
    assert all(path.read_bytes() == content for path, content in before.items())
    assert _git(tmp_path, "ls-files", ".spec").stdout.splitlines() == [".spec/architecture/contracts.json"]
    assert _git(tmp_path, "rev-parse", "HEAD").stdout == head


def test_preview_writes_nothing(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    _commit_all(tmp_path)
    exclude = tmp_path / ".git/info/exclude"
    original = exclude.read_bytes()
    index = (tmp_path / ".git/index").read_bytes()

    assert cli(tmp_path, "purge", "--check").returncode == 0

    assert not local_evidence.marker_path(tmp_path).exists()
    assert exclude.read_bytes() == original
    assert (tmp_path / ".git/index").read_bytes() == index


def test_index_conflict_is_reported_without_discarding_any_version(tmp_path):
    repo(tmp_path)
    directory = make_package(tmp_path)
    _commit_all(tmp_path)
    path = directory / "tasks.md"
    relative = path.relative_to(tmp_path).as_posix()
    path.write_text("staged evidence\n", encoding="utf-8")
    _git(tmp_path, "add", relative)
    path.write_text("working evidence\n", encoding="utf-8")
    index = _git(tmp_path, "ls-files", "-s").stdout

    result = cli(tmp_path, "purge")

    assert result.returncode == 1
    assert "index removal failed" in result.stderr
    assert _git(tmp_path, "ls-files", "-s").stdout == index
    assert _git(tmp_path, "show", f":{relative}").stdout == "staged evidence\n"
    assert path.read_text(encoding="utf-8") == "working evidence\n"


def test_missing_disk_record_blocks_index_removal(tmp_path):
    repo(tmp_path)
    directory = make_package(tmp_path)
    _commit_all(tmp_path)
    (directory / "tasks.md").unlink()
    tracked = _tracked(tmp_path)

    result = cli(tmp_path, "purge")

    assert result.returncode == 1
    assert "restore missing evidence" in result.stderr
    assert _tracked(tmp_path) == tracked


def test_repository_ignore_policy_is_inherited_without_marker(tmp_path):
    repo(tmp_path)
    ignore = tmp_path / ".gitignore"
    ignore.write_text(ignore.read_text() + "/.spec/specs/\n", encoding="utf-8")
    _commit_all(tmp_path, "source evidence policy")
    clone = tmp_path.parent / f"{tmp_path.name}-clone"
    _git(tmp_path, "clone", "-q", str(tmp_path), str(clone))

    assert not local_evidence.marker_path(clone).exists()
    assert local_evidence.is_local(clone)
    directory = make_package(clone)
    _git(clone, "add", "-A")
    assert _tracked(clone) == []
    assert (directory / "tasks.md").is_file()


def test_policy_reads_live_git_ignore_rules_without_stale_cache(tmp_path):
    repo(tmp_path)
    ignore = tmp_path / ".gitignore"
    original = ignore.read_text(encoding="utf-8")
    assert not local_evidence.is_local(tmp_path)
    ignore.write_text(original + "/.spec/specs/\n", encoding="utf-8")
    assert local_evidence.is_local(tmp_path)
    ignore.write_text(original + "/.spec/specs/\n!/.spec/specs/\n", encoding="utf-8")
    assert not local_evidence.is_local(tmp_path)


def test_linked_worktree_archive_survives_worktree_removal(tmp_path):
    repo(tmp_path)
    directory = make_package(tmp_path)
    spec_path = directory / "spec.md"
    spec_path.write_text(spec_path.read_text().split("## Binding")[0], encoding="utf-8")
    _git(tmp_path, "checkout", "main")
    _git(tmp_path, "branch", "-d", f"spec/{SLUG}")
    assert cli(tmp_path, "purge").returncode == 0
    result = cli(tmp_path, "--slug", SLUG, "plan-go")
    assert result.returncode == 0, result.stderr
    linked = Path(json.loads(result.stdout)["worktree"])
    assert local_evidence.is_local(linked)
    (linked / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert cli(linked, "verify", "task-value").returncode == 0
    delivered = cli(linked, "check")
    assert delivered.returncode == 0, delivered.stderr
    archive = tmp_path / ".spec/specs/archive" / SLUG
    source = linked / ".spec/specs/archive" / SLUG
    saved = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    assert saved and all((archive / name).read_bytes() == content for name, content in saved.items())

    _git(tmp_path, "worktree", "remove", str(linked))

    assert all((archive / name).read_bytes() == content for name, content in saved.items())
    listed = cli(tmp_path, "list")
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout)["packages"][0]["criteria"]["passed"] == 1
    assert _tracked(tmp_path) == []


def test_archive_sync_preserves_conflicting_archive_and_symlink_target(tmp_path):
    repo(tmp_path)
    assert cli(tmp_path, "purge").returncode == 0
    source = tmp_path / "saved-package"
    source.mkdir()
    (source / "spec.md").write_text("new\n", encoding="utf-8")
    target = tmp_path / ".spec/specs/archive" / SLUG
    target.mkdir(parents=True)
    (target / "spec.md").write_text("existing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        local_evidence.sync_archive(tmp_path, source, SLUG)
    assert (target / "spec.md").read_text() == "existing\n"
    alias = target.parent / "2026-11-10_symlink"
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        local_evidence.sync_archive(tmp_path, source, alias.name)
    assert (source / "spec.md").read_text() == "new\n"

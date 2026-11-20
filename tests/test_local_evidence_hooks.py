"""Directed coverage for local-only evidence at Git hook boundaries.

These tests exercise the snapshot fallback in ``check_all.py`` without touching
the repository under test and without requiring a real remote for the direct
cases.  The one hook replay uses a temporary bare remote and the checked-in
pre-push template.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CHECKER = SCRIPTS / "check_all.py"
PRE_PUSH = ROOT / "hooks" / "pre-push"
SLUG = "2026-11-20_local-evidence-probe"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _repo(root: Path, *, local: bool = True) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    ignore = "/.spec/specs/\n" if local else ""
    (root / ".gitignore").write_text(ignore, encoding="utf-8")
    (root / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "app.py")
    _git(root, "commit", "-qm", "initial")


def _archive(
    root: Path, slug: str = SLUG, delivery_head: str = "", *, ui: bool = False, delivery_record: bool = True
) -> Path:
    directory = root / ".spec" / "specs" / "archive" / slug
    (directory / "evidence").mkdir(parents=True, exist_ok=True)
    (directory / "spec.md").write_text(
        f"""# Local evidence
<!-- spec-format: 3 -->
## Goal
- goal: local evidence
- literal ask: probe
- done means: pass
## Charter
- V1 priority: quality > maintainability > speed > cost | charter.md
- V2 scope: strict | charter.md
- V3 verification: package | charter.md
- V4 finish: commit | charter.md
- V5 dependencies: none-new | charter.md
- V6 compatibility: keep-public-api | charter.md
- V7 blocked: park-and-continue | charter.md
## Scope
- in: {"web/**" if ui else "app.py"}
- out: unrelated
## Assumptions
## Forks
## Criteria
- C1 | It works | task-value
## Decisions
## Parked
## Binding
- branch: `spec/{slug}`
""",
        encoding="utf-8",
    )
    (directory / "tasks.md").write_text(
        "- [x] Prove it\n  - id: task-value\n  - boundary: "
        + ("web/app.py" if ui else "app.py")
        + "\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    (directory / "runs.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"kind": "baseline", "task": "package", "head": delivery_head}),
                json.dumps({"kind": "run", "task": "task-value", "cmd": "true", "exit": 0, "final": True}),
                json.dumps({"kind": "gate", "passed": True, "errors": [], "parked": [], "final": True}),
                *([json.dumps({"kind": "delivery", "head": delivery_head})] if delivery_record else []),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (directory / "completion-summary.md").write_text("# Completion Summary\n- pass\n", encoding="utf-8")
    return directory


def _check(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root), *args],
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def _delivery(root: Path, slug: str = SLUG, value: str = "VALUE = 1\n") -> str:
    (root / "app.py").write_text(value, encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-qm", "deliver local evidence", "-m", f"Spec: {slug}")
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def test_local_delivery_falls_back_to_primary_archive_without_indexing_it(tmp_path):
    _repo(tmp_path)
    head = _delivery(tmp_path)
    _archive(tmp_path, delivery_head=head)

    result = _check(tmp_path, "--revision", head, "--slug", SLUG)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"explicit package slug not found: {SLUG}" not in result.stdout


def test_local_delivery_missing_archive_fails_closed(tmp_path):
    _repo(tmp_path)
    head = _delivery(tmp_path)

    result = _check(tmp_path, "--revision", head, "--slug", SLUG)

    assert result.returncode != 0
    assert f"explicit package slug not found: {SLUG}" in result.stdout + result.stderr


def test_local_archive_must_be_bound_to_the_footer_commit(tmp_path):
    _repo(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    head = _delivery(tmp_path)
    _archive(tmp_path, delivery_head=base)

    result = _check(tmp_path, "--revision", head, "--slug", SLUG)

    assert result.returncode != 0
    assert "delivery commit does not match" in result.stdout + result.stderr


def test_local_archive_hit_without_ancestor_is_rejected(tmp_path):
    _repo(tmp_path)
    head = _delivery(tmp_path)
    # A delivery record for a commit outside this ref must not validate the tree.
    _git(tmp_path, "checkout", "-q", "-b", "other")
    other = _delivery(tmp_path, value="VALUE = 2\n")
    _git(tmp_path, "checkout", "-q", "main")
    _archive(tmp_path, delivery_head=other)

    result = _check(tmp_path, "--revision", head, "--slug", SLUG)

    assert result.returncode != 0
    assert "delivery commit does not match" in result.stdout + result.stderr


def test_tracked_mode_keeps_existing_snapshot_behaviour(tmp_path):
    _repo(tmp_path, local=False)
    directory = _archive(tmp_path, delivery_head="pending")
    _git(tmp_path, "add", ".spec")
    _git(tmp_path, "commit", "-qm", "archive", "-m", f"Spec: {SLUG}")
    head = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    lines = (directory / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    for record in records:
        if record.get("kind") in {"baseline", "delivery"}:
            record["head"] = head
    (directory / "runs.jsonl").write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    _git(tmp_path, "add", ".spec")
    _git(tmp_path, "commit", "-qm", "bind archive", "-m", f"Spec: {SLUG}")
    head = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    result = _check(tmp_path, "--revision", head, "--slug", SLUG)

    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_deletion_is_not_regraded_as_a_new_delivery(tmp_path):
    _repo(tmp_path)
    _archive(tmp_path, delivery_head="old")
    _git(tmp_path, "add", "-f", ".spec")
    _git(tmp_path, "commit", "-qm", "legacy archive")
    base = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    subprocess.run(["git", "rm", "-r", "-q", ".spec/specs/archive"], cwd=tmp_path, check=True)
    _git(tmp_path, "commit", "-qm", "purge legacy evidence")
    head = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    result = _check(tmp_path, "--revision", head, "--slug", SLUG, "--baseline", base)

    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_local_archive_uses_its_summary_interval(tmp_path):
    _repo(tmp_path)
    base = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    archive = _archive(tmp_path, delivery_head=base, delivery_record=False)
    (archive / "runs.jsonl").write_text(
        json.dumps({"kind": "run", "task": "task-value", "cmd": "true", "exit": 0}) + "\n",
        encoding="utf-8",
    )
    (archive / "completion-summary.md").write_text(
        f"# Completion Summary\n- Commit interval: {base}..{base}\n", encoding="utf-8"
    )
    _git(tmp_path, "add", "-f", ".spec")
    _git(tmp_path, "commit", "-qm", "legacy archive", "-m", f"Spec: {SLUG}")
    _git(tmp_path, "rm", "-rq", "--cached", ".spec")
    _git(tmp_path, "commit", "-qm", "untrack legacy evidence")
    head = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    result = _check(tmp_path, "--revision", head, "--slug", SLUG)

    assert result.returncode == 0, result.stdout + result.stderr


def test_deleted_delivery_row_cannot_downgrade_to_legacy(tmp_path):
    _repo(tmp_path)
    head = _delivery(tmp_path)
    archive = _archive(tmp_path, delivery_head=head, delivery_record=False)
    (archive / "runs.jsonl").write_text(
        json.dumps({"kind": "run", "task": "task-value", "cmd": "true", "exit": 0, "final": True})
        + "\n"
        + json.dumps({"kind": "gate", "passed": True, "errors": [], "parked": [], "final": True})
        + "\n",
        encoding="utf-8",
    )
    (archive / "completion-summary.md").write_text(
        f"# Completion Summary\n- Commit interval: {head}..{head}\n", encoding="utf-8"
    )

    result = _check(tmp_path, "--revision", head, "--slug", SLUG)

    assert result.returncode != 0
    assert "legacy archive was not tracked at its attributed commit" in result.stdout + result.stderr


def test_local_ui_delivery_requires_ui_review(tmp_path):
    _repo(tmp_path)
    head = _delivery(tmp_path)
    _archive(tmp_path, delivery_head=head, ui=True)

    result = _check(tmp_path, "--revision", head, "--slug", SLUG)

    assert result.returncode != 0
    assert "required ui-review.json is missing" in result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["touched", "all"])
def test_pre_push_source_only_delivery_accepts_local_archive(tmp_path, mode):
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo(repo)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-qu", "origin", "main")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    head = _delivery(repo)
    _archive(repo, delivery_head=head)
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(
        PRE_PUSH.read_text(encoding="utf-8")
        .replace("__SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER__", str(CHECKER))
        .replace("__SPEC_PYTHON_PLACEHOLDER__", sys.executable),
        encoding="utf-8",
    )
    hook.chmod(0o755)

    result = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "SPEC_PYTHON": sys.executable,
            "SPEC_PUSH_GATE": mode,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    listed = _git(remote, "rev-parse", "refs/heads/main").stdout.strip()
    assert listed == head
    assert base != head


@pytest.mark.parametrize("broken", ["summary", "runs", "final-gate", "ui"])
def test_local_archive_is_fully_checked(tmp_path, broken):
    _repo(tmp_path)
    head = _delivery(tmp_path)
    archive = _archive(tmp_path, delivery_head=head)
    if broken == "summary":
        (archive / "completion-summary.md").write_text("", encoding="utf-8")
    elif broken == "runs":
        (archive / "runs.jsonl").unlink()
    elif broken == "final-gate":
        with (archive / "runs.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"kind": "gate", "passed": False, "final": True}) + "\n")
    else:
        (archive / "ui-review.json").write_text("{}\n", encoding="utf-8")

    result = _check(tmp_path, "--revision", head, "--slug", SLUG)

    assert result.returncode != 0
    assert "result: blocked" in result.stdout


def test_local_archive_can_live_only_in_a_linked_worktree(tmp_path):
    _repo(tmp_path)
    head = _delivery(tmp_path)
    linked = tmp_path.parent / f"{tmp_path.name}-linked"
    _git(tmp_path, "worktree", "add", "-q", "-b", "fixture-linked", str(linked))
    _archive(linked, delivery_head=head)

    result = _check(tmp_path, "--revision", head, "--slug", SLUG)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(tmp_path, "ls-tree", "-r", "--name-only", "HEAD", ".spec").stdout == ""


def test_reusing_old_archive_for_a_later_same_slug_commit_fails(tmp_path):
    _repo(tmp_path)
    first = _delivery(tmp_path)
    _archive(tmp_path, delivery_head=first)
    second = _delivery(tmp_path, value="VALUE = 2\n")

    result = _check(tmp_path, "--revision", second, "--slug", SLUG, "--baseline", first)

    assert result.returncode != 0
    assert "delivery commit does not match" in result.stdout


def test_index_accepts_a_pending_verified_local_archive(tmp_path):
    import package

    _repo(tmp_path)
    _git(tmp_path, "checkout", "-qb", f"spec/{SLUG}")
    (tmp_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "app.py")
    archive = _archive(tmp_path, delivery_record=False)
    rows = [json.loads(line) for line in (archive / "runs.jsonl").read_text().splitlines()]
    fingerprint = package.tree_fingerprint(tmp_path)
    for row in rows:
        if row.get("kind") == "run":
            row["tree_fp"] = fingerprint
    (archive / "runs.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    result = _check(tmp_path, "--index", "--slug", SLUG)

    assert result.returncode == 0, result.stdout + result.stderr
    (tmp_path / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    stale = _check(tmp_path, "--index", "--slug", SLUG)
    assert stale.returncode != 0
    assert "index differs" in stale.stdout


def test_pre_v3_frozen_archive_is_matched_without_regrading(tmp_path):
    _repo(tmp_path)
    archive = _archive(tmp_path, delivery_record=False)
    spec = archive / "spec.md"
    spec.write_text(spec.read_text().replace("spec-format: 3", "spec-format: 2"), encoding="utf-8")
    _git(tmp_path, "add", "-f", ".spec")
    _git(tmp_path, "commit", "-qm", "old archive", "-m", f"Spec: {SLUG}")
    _git(tmp_path, "rm", "-rq", "--cached", ".spec")
    _git(tmp_path, "commit", "-qm", "localize old archive")
    head = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    result = _check(tmp_path, "--revision", head, "--slug", SLUG)

    assert result.returncode == 0, result.stdout + result.stderr
    spec.write_text("changed historical data\n", encoding="utf-8")
    altered = _check(tmp_path, "--revision", head, "--slug", SLUG)
    assert altered.returncode != 0
    assert "differs from its attributed commit" in altered.stdout


def test_pre_commit_after_purge_keeps_frozen_legacy_archive_on_disk(tmp_path):
    _repo(tmp_path, local=False)
    archive = _archive(tmp_path, delivery_record=False)
    spec = archive / "spec.md"
    spec.write_text(spec.read_text().replace("spec-format: 3", "spec-format: 2"), encoding="utf-8")
    _git(tmp_path, "add", ".spec")
    _git(tmp_path, "commit", "-qm", "historical archive")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "spec.py"), "--root", str(tmp_path), "purge"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        (ROOT / "hooks" / "pre-commit")
        .read_text(encoding="utf-8")
        .replace("__SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER__", str(CHECKER))
        .replace("__SPEC_PYTHON_PLACEHOLDER__", sys.executable),
        encoding="utf-8",
    )
    hook.chmod(0o755)

    checked = subprocess.run([str(hook)], cwd=tmp_path, capture_output=True, text=True)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert spec.is_file()
    assert _git(tmp_path, "ls-files", ".spec/specs").stdout == ""


def test_done_with_original_pre_commit_and_pre_push_hooks(tmp_path):
    from tests.v3_support import SLUG as fixture_slug
    from tests.v3_support import cli, make_package, repo

    repo(tmp_path)
    remote = tmp_path.parent / f"{tmp_path.name}-remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "remote", "add", "origin", str(remote))
    _git(tmp_path, "push", "-q", "origin", "main")
    make_package(tmp_path)
    assert cli(tmp_path, "purge").returncode == 0
    for name in ("pre-commit", "pre-push"):
        hook = tmp_path / ".git" / "hooks" / name
        hook.write_text(
            (ROOT / "hooks" / name)
            .read_text(encoding="utf-8")
            .replace("__SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER__", str(CHECKER))
            .replace("__SPEC_PYTHON_PLACEHOLDER__", sys.executable),
            encoding="utf-8",
        )
        hook.chmod(0o755)
    assert cli(tmp_path, "plan-go").returncode == 0
    (tmp_path / "app.py").write_text("VALUE = 5\n", encoding="utf-8")
    verified = cli(tmp_path, "verify", "task-value")
    assert verified.returncode == 0, verified.stdout + verified.stderr

    delivered = cli(tmp_path, "done")

    assert delivered.returncode == 0, delivered.stdout + delivered.stderr
    assert _git(tmp_path, "ls-files", ".spec/specs").stdout == ""
    pushed = _git(tmp_path, "push", "origin", f"spec/{fixture_slug}:main")
    assert "result: passed" in pushed.stdout
    assert _git(remote, "rev-parse", "main").stdout == _git(tmp_path, "rev-parse", "HEAD").stdout

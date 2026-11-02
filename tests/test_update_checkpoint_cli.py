# tests/test_update_checkpoint_cli.py
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""CLI contract tests for scripts/update_checkpoint.py.

The CLI is read-only: ``status`` recognizes historical checkpoints left
behind by the retired mechanism, and the retired write subcommands
(``begin`` / ``rollback`` / ``complete``) refuse with path-scoped recovery
guidance. Exercises a real interpreter process: POSIX exit codes per
subcommand, stdout/stderr separation, and the absence of on-disk effects.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from update_checkpoint_support import checkpoint_path  # noqa: E402

SLUG = "2026-09-13_demo-pkg"
OTHER_SLUG = "2026-09-14_other-pkg"
INTENT = "追加数据导出任务"
CREATED_AT = "2026-06-12T10:00:00+00:00"


def _write_text(path: Path, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def _make_package(root: Path, slug: str = SLUG, marker: str = "initial") -> Path:
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True, exist_ok=True)
    _write_text(package / "spec.md", f"# {marker} - 项目范围\n")
    _write_text(package / "tasks.md", f"# {marker} 任务\n\n- [ ] task one\n")
    _write_text(package / "checklist.md", f"# {marker} checklist\n\n**验收结果**：待修复\n")
    return package


def _write_historical_checkpoint(package: Path, intent: str = INTENT) -> None:
    payload = {
        "version": 1,
        "package": package.name,
        "intent": intent,
        "createdAt": CREATED_AT,
        "snapshot": {
            "spec.md": "# snapshot - 项目范围\n",
            "tasks.md": "# snapshot 任务\n",
            "checklist.md": "# snapshot checklist\n",
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["checksum"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    _write_text(checkpoint_path(package), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _run(root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "update_checkpoint.py"), "--root", str(root), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def _corrupt_checkpoint(package: Path) -> None:
    _write_text(checkpoint_path(package), "{ not json")


# --- status -----------------------------------------------------------------


def test_status_clean_package_exits_zero(tmp_path: Path) -> None:
    _make_package(tmp_path)

    result = _run(tmp_path, "status", "--slug", SLUG)

    assert result.returncode == 0, result.stderr
    assert f"{SLUG}：无未完成 update checkpoint" in result.stdout
    assert result.stderr == ""


def test_status_rejects_moved_checkpoint_for_single_and_scan(tmp_path: Path) -> None:
    other = _make_package(tmp_path, slug=OTHER_SLUG)
    _write_historical_checkpoint(other, INTENT)
    target = _make_package(tmp_path, slug=SLUG)
    checkpoint_path(target).write_bytes(checkpoint_path(other).read_bytes())
    checkpoint_path(other).unlink()
    before = checkpoint_path(target).read_bytes()
    for args in (("status", "--slug", SLUG), ("status",)):
        result = _run(tmp_path, *args)
        assert result.returncode == 4
        assert "记录已损坏" in result.stdout
        assert "有效 snapshot" not in result.stdout
    assert checkpoint_path(target).read_bytes() == before


def test_status_reports_historical_checkpoint_with_intent_and_git_recovery(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    _write_historical_checkpoint(package, INTENT)

    result = _run(tmp_path, "status", "--slug", SLUG)

    assert result.returncode == 3
    assert f"{SLUG}：存在未完成 update checkpoint" in result.stdout
    assert f"意图：{INTENT}" in result.stdout
    assert "先备份并审阅有效 snapshot" in result.stdout
    assert "references/stages/update.md" in result.stdout
    assert result.stderr == ""


def test_status_scan_reports_only_interrupted_packages(tmp_path: Path) -> None:
    _make_package(tmp_path, slug=OTHER_SLUG)
    interrupted = _make_package(tmp_path, slug=SLUG)
    _write_historical_checkpoint(interrupted, INTENT)

    result = _run(tmp_path, "status")

    assert result.returncode == 3
    assert SLUG in result.stdout
    assert OTHER_SLUG not in result.stdout


def test_status_scan_clean_specs_dir_exits_zero(tmp_path: Path) -> None:
    _make_package(tmp_path)

    result = _run(tmp_path, "status")

    assert result.returncode == 0, result.stderr
    assert "未发现未完成 update checkpoint" in result.stdout


def test_status_reports_corrupted_checkpoint(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    _write_historical_checkpoint(package, INTENT)
    _corrupt_checkpoint(package)

    result = _run(tmp_path, "status", "--slug", SLUG)

    assert result.returncode == 4
    assert f"{SLUG}：存在未完成 update checkpoint" in result.stdout
    assert "已损坏" in result.stdout


def test_status_scan_reports_worst_state_across_packages(tmp_path: Path) -> None:
    active = _make_package(tmp_path, slug=OTHER_SLUG)
    _write_historical_checkpoint(active, INTENT)
    corrupted = _make_package(tmp_path, slug=SLUG)
    _write_historical_checkpoint(corrupted, INTENT)
    _corrupt_checkpoint(corrupted)

    result = _run(tmp_path, "status")

    assert result.returncode == 4
    assert SLUG in result.stdout and OTHER_SLUG in result.stdout


# --- retired write subcommands ----------------------------------------------


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("begin", ()),
        ("begin", ("--slug", SLUG, "--intent", "再次改写")),
        ("rollback", ()),
        ("rollback", ("--slug", SLUG)),
        ("complete", ()),
        ("complete", ("--slug", SLUG, "--expect-intent", INTENT)),
    ],
)
def test_retired_subcommands_refuse_with_git_guidance(tmp_path: Path, command: str, extra: tuple[str, ...]) -> None:
    package = _make_package(tmp_path)
    before = {path.name: path.read_bytes() for path in package.iterdir()}

    result = _run(tmp_path, command, *extra)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("error:")
    assert "已退役" in result.stderr
    assert "不要擅自暂存、提交或更改索引" in result.stderr
    assert "有效 snapshot" in result.stderr
    assert "git reset --hard" not in result.stderr
    assert "git add -A" not in result.stderr
    assert "git revert" not in result.stderr
    assert "Traceback" not in result.stderr
    # Refusal is read-only: package state is untouched, nothing written.
    assert {path.name: path.read_bytes() for path in package.iterdir()} == before


def test_retired_help_prints_replacement_guidance(tmp_path: Path) -> None:
    for command in ("begin", "rollback", "complete"):
        result = _run(tmp_path, command, "--help")

        assert result.returncode == 0
        assert "已退役" in result.stdout
        assert "不要擅自暂存、提交或更改索引" in result.stdout
        assert "git reset --hard" not in result.stdout


# --- argument and path validation -------------------------------------------


def test_missing_package_directory_fails(tmp_path: Path) -> None:
    result = _run(tmp_path, "status", "--slug", "missing-pkg")
    assert result.returncode == 1
    assert "task package not found" in result.stderr


def test_invalid_slug_fails_as_operational_error(tmp_path: Path) -> None:
    result = _run(tmp_path, "status", "--slug", "BAD")

    assert result.returncode == 1
    assert result.stderr.startswith("error:")
    assert "slug" in result.stderr


def test_missing_subcommand_exits_two(tmp_path: Path) -> None:
    assert _run(tmp_path).returncode == 2


def test_rejects_invalid_specs_dir_without_traceback(tmp_path: Path) -> None:
    result = _run(tmp_path, "status", "--specs-dir", "../outside")

    assert result.returncode == 1
    assert "error: --specs-dir" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("command", ["status", "begin", "rollback", "complete"])
def test_help_documents_exit_code_contract(command: str, tmp_path: Path) -> None:
    result = _run(tmp_path, command, "--help")

    assert result.returncode == 0
    for code_line in ("0  success", "3  status: active checkpoint", "4  status: corrupted checkpoint"):
        assert code_line in result.stdout
    if command != "status":
        assert "2  usage error; retired subcommands" in result.stdout


@pytest.mark.parametrize("target_kind", ["external", "internal", "dangling"])
def test_status_refuses_package_directory_symlinks(tmp_path: Path, target_kind: str) -> None:
    root = tmp_path / "project"
    specs = root / ".spec" / "specs"
    specs.mkdir(parents=True)
    target_root = root / "storage" if target_kind == "internal" else tmp_path / "outside"
    target = _make_package(target_root)
    _write_historical_checkpoint(target)
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    link = specs / SLUG
    link.symlink_to(target if target_kind != "dangling" else target / "missing", target_is_directory=True)

    result = _run(root, "status", "--slug", SLUG)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error:")
    assert "symlink" in result.stderr
    assert "Traceback" not in result.stderr
    assert link.is_symlink()
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


def test_status_refuses_specs_directory_resolving_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".spec").mkdir(parents=True)
    target = _make_package(tmp_path / "outside")
    _write_historical_checkpoint(target)
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    (root / ".spec" / "specs").symlink_to(target.parent, target_is_directory=True)

    result = _run(root, "status", "--slug", SLUG)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error:")
    assert "must resolve under specs root" in result.stderr
    assert "Traceback" not in result.stderr
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


def test_status_scan_still_skips_package_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "project"
    specs = root / ".spec" / "specs"
    specs.mkdir(parents=True)
    target = _make_package(tmp_path / "outside")
    _write_historical_checkpoint(target)
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    (specs / SLUG).symlink_to(target, target_is_directory=True)

    result = _run(root, "status")

    assert result.returncode == 0, result.stderr
    assert "未发现未完成 update checkpoint" in result.stdout
    assert SLUG not in result.stdout
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


@pytest.mark.parametrize("damage", ["deep-json", "intent-surrogate", "snapshot-surrogate"])
def test_malformed_checkpoint_cli_contract_and_byte_preservation(tmp_path: Path, damage: str) -> None:
    package = _make_package(tmp_path)
    _write_historical_checkpoint(package)
    path = checkpoint_path(package)
    if damage == "deep-json":
        path.write_text("[" * 1100 + "0" + "]" * 1100, encoding="utf-8")
    else:
        document = json.loads(path.read_text(encoding="utf-8"))
        if damage == "intent-surrogate":
            document["intent"] = "\ud800"
        else:
            document["snapshot"]["tasks.md"] = "\ud800"
        path.write_text(json.dumps(document, ensure_ascii=True), encoding="utf-8")
    _write_text(package / "tasks.md", "# half-applied update\n")
    before = {p.name: p.read_bytes() for p in package.iterdir()}

    for args in (("status", "--slug", SLUG), ("status",)):
        result = _run(tmp_path, *args)
        assert result.returncode == 4, result.stderr
        assert "已损坏" in result.stdout
        assert result.stderr == ""
        assert "Traceback" not in result.stdout
        assert {p.name: p.read_bytes() for p in package.iterdir()} == before

    result = _run(tmp_path, "status", "--slug", SLUG)
    assert {p.name: p.read_bytes() for p in package.iterdir()} == before

# Execute the actual documented shell blocks in isolated Git repositories.
# This tests the guidance rather than a separate hand-written approximation.

def _doc_block(fragment: str) -> str:
    text = (SCRIPTS_DIR.parent / "references/update-recovery.md").read_text(encoding="utf-8")
    matches = [block for block in re.findall(r"~~~bash\n(.*?)\n~~~", text, re.DOTALL) if fragment in block]
    assert len(matches) == 1, (fragment, len(matches))
    return matches[0]


@pytest.mark.parametrize("block", [
    "recovery_metadata_safe() {",
    'paths=("$pkg/spec.md"',
    "step=$(umask 077; mktemp",
    "Postimage changed: stop and reconcile.",
    "from update_checkpoint_support import inspect_update_checkpoint",
    "Legacy snapshot unavailable or current file changed",
    "Checkpoint changed or not backed up",
])
def test_documented_recovery_blocks_parse_with_host_bash(block: str) -> None:
    """Every executable example must parse before its safety checks can run."""
    result = subprocess.run(["bash", "-n"], input=_doc_block(block), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def _isolated_git_env() -> dict[str, str]:
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_COUNT": "0"}
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(name, None)
    return env


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                            text=True, env=_isolated_git_env())
    return result.stdout.strip()


def _dirty_repo(tmp_path: Path, *, adopted: bool = False) -> tuple[Path, Path, Path]:
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Isolated Test")
    package = _make_package(repo)
    (package / "checklist.md").unlink()  # a v2 package may not have a checklist
    if adopted:
        _write_text(package / "adoption.json", '{"adopted": true}\n')
    _write_text(repo / "unrelated.txt", "base\n")
    _git(repo, "add", "--", ".")
    _git(repo, "commit", "-q", "-m", "isolated fixture")
    _write_text(package / "tasks.md", "staged task state\n")
    _write_text(repo / "unrelated.txt", "unrelated staged state\n")
    _git(repo, "add", "--", str((package / "tasks.md").relative_to(repo)), "unrelated.txt")
    _write_text(package / "tasks.md", "staged task state\nexisting unstaged task state\n")
    _write_text(repo / "unrelated.txt", "unrelated staged state\nunrelated unstaged state\n")
    _write_text(repo / "untracked.txt", "unrelated untracked state\n")
    recovery_parent = tmp_path / "private-recovery"
    recovery_parent.mkdir(mode=0o700)
    return repo, package, recovery_parent


def _bash_doc(repo: Path, package: Path, recovery_parent: Path, script: str, *, pythonpath: str | None = None) -> subprocess.CompletedProcess[str]:
    if not hasattr(os, "listxattr"):
        pytest.skip("documented metadata recovery requires os.listxattr; Linux behavior is covered separately")
    env = {
        **_isolated_git_env(),
        "pkg": str(package.relative_to(repo)),
        "TMPDIR": str(recovery_parent),
        "PYTHONPATH": str(repo / "scripts") if pythonpath is None else pythonpath,  # ignored by -I
        "skill_scripts": str(SCRIPTS_DIR),
    }
    helpers = _doc_block("recovery_metadata_safe() {")
    # Fixtures are private, created by this process, and have no hidden metadata.
    return subprocess.run(["bash", "-c", helpers + "\nrecovery_metadata_visibility=verified\n" + script],
                          cwd=repo, env=env, capture_output=True, text=True)


@pytest.mark.parametrize("adopted", [False, True], ids=["ordinary", "adopted"])
@pytest.mark.parametrize("concurrent", [False, True], ids=["recover", "conflict"])
def test_documented_update_recovery_preserves_dirty_index_and_unrelated_files(
    tmp_path: Path, adopted: bool, concurrent: bool
) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path, adopted=adopted)
    (package / "tasks.md").chmod(0o640)
    before_task = (package / "tasks.md").read_bytes()
    before_stat = (package / "tasks.md").stat()
    before_other = (repo / "unrelated.txt").read_bytes()
    before_untracked = (repo / "untracked.txt").read_bytes()
    before_index = (repo / ".git/index").read_bytes()
    before_head = _git(repo, "rev-parse", "HEAD")
    snapshot = _doc_block('paths=("$pkg/spec.md"')
    capture = _doc_block('step=$(umask 077; mktemp')
    restore = _doc_block("Postimage changed: stop and reconcile.")
    write_update = "printf 'owned update\\n' >> \"$pkg/tasks.md\"\n"
    write_conflict = "printf 'concurrent edit\\n' >> \"$path\"\n" if concurrent else ""
    result = _bash_doc(repo, package, recovery_parent, snapshot + '\n[ -n "$backup" ] || exit 10\n' +
                       write_update + capture + '\n[ -n "$step" ] || exit 11\n' + write_conflict + restore)
    assert (result.returncode != 0) == concurrent, result.stderr
    if concurrent:
        assert "Postimage changed: stop" in result.stderr
        assert (package / "tasks.md").read_bytes() == before_task + b"owned update\nconcurrent edit\n"
    else:
        assert result.stderr == ""
        assert (package / "tasks.md").read_bytes() == before_task
        recovered_stat = (package / "tasks.md").stat()
        assert recovered_stat.st_mode & 0o7777 == 0o640
        assert (recovered_stat.st_uid, recovered_stat.st_gid, recovered_stat.st_mtime_ns) == (
            before_stat.st_uid, before_stat.st_gid, before_stat.st_mtime_ns
        )
    assert (repo / "unrelated.txt").read_bytes() == before_other
    assert (repo / "untracked.txt").read_bytes() == before_untracked
    assert (repo / ".git/index").read_bytes() == before_index
    assert _git(repo, "rev-parse", "HEAD") == before_head
    assert _git(repo, "show", ":" + str((package / "tasks.md").relative_to(repo))) == "staged task state"


@pytest.mark.parametrize("case", ["added", "deleted", "unchanged-absent"])
def test_documented_recovery_preserves_absence_and_restores_deleted_file(tmp_path: Path, case: str) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    snapshot = _doc_block('paths=("$pkg/spec.md"')
    capture = _doc_block('step=$(umask 077; mktemp')
    restore = _doc_block("Postimage changed: stop and reconcile.")
    if case in ("added", "unchanged-absent"):
        target = package / "checklist.md"
        write_update = "printf 'created by update\\n' > \"$pkg/checklist.md\"\n" if case == "added" else ""
        capture = capture.replace('path="$pkg/tasks.md"', 'path="$pkg/checklist.md"')
        previous = None
    else:
        target = package / "tasks.md"
        previous = target.read_bytes()
        before_stat = target.stat()
        write_update = 'rm -- "$pkg/tasks.md"\n'
    before_index = (repo / ".git/index").read_bytes()
    result = _bash_doc(repo, package, recovery_parent, snapshot + '\n[ -n "$backup" ] || exit 10\n' +
                       write_update + capture + '\n[ -n "$step" ] || exit 11\n' + restore)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert target.read_bytes() == previous if previous is not None else not target.exists()
    if previous is not None:
        after_stat = target.stat()
        assert (after_stat.st_mode, after_stat.st_uid, after_stat.st_gid, after_stat.st_mtime_ns) == (
            before_stat.st_mode, before_stat.st_uid, before_stat.st_gid, before_stat.st_mtime_ns
        )
    assert (repo / ".git/index").read_bytes() == before_index


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "missing-required", "backup-inside-repo"])
def test_documented_snapshot_refuses_unsafe_inputs(tmp_path: Path, unsafe: str) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    target = package / "tasks.md"
    if unsafe == "symlink":
        target.unlink()
        target.symlink_to(repo / "unrelated.txt")
    elif unsafe == "hardlink":
        target.unlink()
        os.link(repo / "unrelated.txt", target)
    elif unsafe == "missing-required":
        target.unlink()
    else:
        recovery_parent = repo
    before_index = (repo / ".git/index").read_bytes()
    script = _doc_block('paths=("$pkg/spec.md"')
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0
    assert "Recovery copy failed: do not rewrite." in result.stderr
    assert (repo / ".git/index").read_bytes() == before_index


@pytest.mark.parametrize("unsafe", ["hardlink", "parent-symlink", "dangling-parent", "fifo"])
def test_documented_postimage_refuses_unsafe_paths(tmp_path: Path, unsafe: str) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tasks.md").write_text("outside content must not be captured\n", encoding="utf-8")
    snapshot = _doc_block('paths=("$pkg/spec.md"')
    capture = _doc_block('step=$(umask 077; mktemp')
    if unsafe == "hardlink":
        mutate = 'ln -- "$pkg/tasks.md" "$pkg/alias"'
    elif unsafe == "fifo":
        mutate = 'rm -- "$pkg/tasks.md"; mkfifo -- "$pkg/tasks.md"'
    else:
        target = outside if unsafe == "parent-symlink" else tmp_path / "missing-parent"
        mutate = 'mv -- "$pkg" "$pkg-moved"; ln -s -- ' + shlex.quote(str(target)) + ' "$pkg"'
    script = snapshot + '\n[ -n "$backup" ] || exit 10\n' + mutate + '\n' + capture
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0
    assert "Postimage capture failed: stop." in result.stderr
    assert not list(recovery_parent.glob("*/step.*/present"))


@pytest.mark.parametrize("pythonpath", [None, ""], ids=["untrusted", "empty"])
def test_documented_recovery_ignores_untrusted_project_modules(tmp_path: Path, pythonpath: str | None) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    _write_historical_checkpoint(package)
    marker = repo / "untrusted-import-executed"
    poison = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise SystemExit(23)\n"
    (repo / "pathlib.py").write_text(poison, encoding="utf-8")
    (repo / "update_checkpoint_support.py").write_text(poison, encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "update_checkpoint_support.py").write_text(poison, encoding="utf-8")
    snapshot = _doc_block('paths=("$pkg/spec.md"').replace(
        '# Example: paths+=("references/actual-source-of-truth.md")',
        'paths+=("$pkg/update-checkpoint.json")',
    )
    extract = _doc_block("from update_checkpoint_support import inspect_update_checkpoint")
    result = _bash_doc(repo, package, recovery_parent, snapshot + '\n[ -n "$backup" ] || exit 10\n' + extract, pythonpath=pythonpath)
    assert result.returncode == 0, result.stderr
    assert "Validated snapshot copies:" in result.stdout
    assert not marker.exists()


@pytest.mark.parametrize("damage", ["none", "checksum", "package"])
def test_documented_historical_snapshot_extracts_only_validated_text(tmp_path: Path, damage: str) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    _write_historical_checkpoint(package)
    checkpoint = checkpoint_path(package)
    if damage == "checksum":
        _corrupt_checkpoint(package)
    elif damage == "package":
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        payload["package"] = OTHER_SLUG
        payload.pop("checksum")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload["checksum"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        checkpoint.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in package.iterdir()}
    before_index = (repo / ".git/index").read_bytes()
    snapshot = _doc_block('paths=("$pkg/spec.md"').replace(
        '# Example: paths+=("references/actual-source-of-truth.md")',
        'paths+=("$pkg/update-checkpoint.json")',
    )
    extract = _doc_block("from update_checkpoint_support import inspect_update_checkpoint")
    result = _bash_doc(repo, package, recovery_parent,
                       snapshot + '\n[ -n "$backup" ] || exit 10\nprintf "BACKUP=%s\\n" "$backup"\n' + extract)
    assert (result.returncode == 0) == (damage == "none"), result.stderr
    backup_line = next(line for line in result.stdout.splitlines() if line.startswith("BACKUP="))
    backup = Path(backup_line.removeprefix("BACKUP="))
    assert (backup / "before" / package.relative_to(repo) / "update-checkpoint.json").read_bytes() == before[checkpoint.name]
    if damage == "none":
        assert (backup / "legacy-snapshot/tasks.md").read_text(encoding="utf-8") == "# snapshot 任务\n"
        assert (backup / "legacy-snapshot/spec.md").read_text(encoding="utf-8") == "# snapshot - 项目范围\n"
    else:
        assert not (backup / "legacy-snapshot").exists()
    assert {p.name: p.read_bytes() for p in package.iterdir()} == before
    assert (repo / ".git/index").read_bytes() == before_index


@pytest.mark.parametrize("concurrent", [False, True])
def test_documented_checkpoint_resolution_only_removes_unchanged_path(tmp_path: Path, concurrent: bool) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    _write_historical_checkpoint(package)
    original = checkpoint_path(package).read_bytes()
    before_index = (repo / ".git/index").read_bytes()
    snapshot = _doc_block('paths=("$pkg/spec.md"').replace(
        '# Example: paths+=("references/actual-source-of-truth.md")',
        'paths+=("$pkg/update-checkpoint.json")',
    )
    removal = _doc_block("Checkpoint changed or not backed up")
    concurrent_edit = 'printf "changed\\n" >> "$pkg/update-checkpoint.json"\n' if concurrent else ""
    result = _bash_doc(repo, package, recovery_parent,
                       snapshot + '\n[ -n "$backup" ] || exit 10\n' + concurrent_edit + removal)
    assert (result.returncode != 0) == concurrent, result.stderr
    if concurrent:
        assert "Checkpoint changed or not backed up" in result.stderr
        assert checkpoint_path(package).read_bytes() == original + b"changed\n"
    else:
        assert result.stderr == ""
        assert not checkpoint_path(package).exists()
    assert (repo / ".git/index").read_bytes() == before_index



@pytest.mark.parametrize("concurrent", [False, True])
def test_documented_legacy_restore_uses_snapshot_without_touching_index(tmp_path: Path, concurrent: bool) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path, adopted=True)
    _write_historical_checkpoint(package)
    target = package / "tasks.md"
    target.chmod(0o640)
    before_target = target.read_bytes()
    before_other = (repo / "unrelated.txt").read_bytes()
    before_checkpoint = checkpoint_path(package).read_bytes()
    before_index = (repo / ".git/index").read_bytes()
    snapshot = _doc_block('paths=("$pkg/spec.md"').replace(
        '# Example: paths+=("references/actual-source-of-truth.md")',
        'paths+=("$pkg/update-checkpoint.json")',
    )
    extract = _doc_block("from update_checkpoint_support import inspect_update_checkpoint")
    capture = _doc_block('step=$(umask 077; mktemp')
    restore = _doc_block("Legacy snapshot unavailable or current file changed")
    conflict = 'printf "concurrent\\n" >> "$path"\n' if concurrent else ""
    result = _bash_doc(repo, package, recovery_parent,
                       snapshot + '\n[ -n "$backup" ] || exit 10\n' + extract + '\n' + capture +
                       '\n[ -n "$step" ] || exit 11\n' + conflict + restore)
    assert (result.returncode != 0) == concurrent, result.stderr
    if concurrent:
        assert "current file changed: stop" in result.stderr
        assert target.read_bytes() == before_target + b"concurrent\n"
    else:
        assert result.stderr == ""
        assert target.read_bytes() == "# snapshot 任务\n".encode()
        assert target.stat().st_mode & 0o777 == 0o640
    assert checkpoint_path(package).read_bytes() == before_checkpoint
    assert (repo / "unrelated.txt").read_bytes() == before_other
    assert (repo / ".git/index").read_bytes() == before_index



def _metadata_change(tmp_path: Path, kind: str, shell_path: str) -> str:
    """Create the real metadata on a probe first; never fake the feature under test."""
    probe = tmp_path / "metadata-probe"
    probe.write_bytes(b"probe")
    probe.chmod(0o640)
    if kind == "xattr":
        try:
            os.setxattr(probe, "user.spec-recovery", b"changed")
        except (AttributeError, OSError) as exc:
            pytest.skip(f"user xattrs unavailable: {exc}")
        assert os.getxattr(probe, "user.spec-recovery") == b"changed"
        code = 'import os,sys; os.setxattr(sys.argv[1], "user.spec-recovery", b"changed")'
        return f'{shlex.quote(sys.executable)} -I -c {shlex.quote(code)} "{shell_path}"\n'
    if kind == "acl":
        setfacl = shutil.which("setfacl")
        if not setfacl:
            pytest.skip("setfacl unavailable")
        result = subprocess.run([setfacl, "-m", "u:12345:---", str(probe)], capture_output=True)
        if result.returncode != 0:
            pytest.skip(f"extended ACL unavailable: {result.stderr!r}")
        assert "system.posix_acl_access" in os.listxattr(probe)
        assert probe.stat().st_mode & 0o777 == 0o640
        return f'{shlex.quote(setfacl)} -m u:12345:--- "{shell_path}"\n'
    mode = {"setuid": "u+s", "setgid": "g+s", "sticky": "+t"}[kind]
    return f'chmod {mode} -- "{shell_path}"\n'


def _assert_metadata_change(target: Path, kind: str) -> None:
    if kind == "xattr":
        assert os.getxattr(target, "user.spec-recovery") == b"changed"
    elif kind == "acl":
        assert "system.posix_acl_access" in os.listxattr(target)
    else:
        assert target.stat().st_mode & {"setuid": 0o4000, "setgid": 0o2000, "sticky": 0o1000}[kind]


def _failing_python_wrapper(tmp_path: Path, code: str) -> Path:
    bindir = tmp_path / "injected-bin"
    bindir.mkdir()
    python = bindir / "python3"
    # The documented calls are python3 -I - <args>; preserve their argv and stdin.
    python.write_text(f'#!/bin/sh\nshift 2\nexec {shlex.quote(sys.executable)} -I -c {shlex.quote(code)} "$@"\n',
                      encoding="utf-8")
    python.chmod(0o755)
    return bindir


@pytest.mark.parametrize("kind", ["xattr", "acl", "setuid", "setgid", "sticky"])
@pytest.mark.parametrize("phase", ["preimage", "postimage", "restore", "remove-created"])
def test_documented_recovery_rejects_unsafe_metadata_without_overwriting(
    tmp_path: Path, kind: str, phase: str
) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    target = package / ("checklist.md" if phase == "remove-created" else "tasks.md")
    original = target.read_bytes() if target.exists() else b""
    before_index = (repo / ".git/index").read_bytes()
    before_other = (repo / "unrelated.txt").read_bytes()
    snapshot = _doc_block('paths=("$pkg/spec.md"')
    capture = _doc_block('step=$(umask 077; mktemp')
    restore = _doc_block("Postimage changed: stop and reconcile.")
    if phase == "remove-created":
        capture = capture.replace('path="$pkg/tasks.md"', 'path="$pkg/checklist.md"')
    change = _metadata_change(tmp_path, kind, "$path")
    if phase == "preimage":
        script = 'path="$pkg/tasks.md"\n' + change + snapshot
        expected = original
        message = "Recovery copy failed: do not rewrite."
    else:
        written = b"created update\n" if phase == "remove-created" else b"owned update\n"
        action = ('printf "created update\\n" > "$pkg/checklist.md"\n' if phase == "remove-created"
                  else 'printf "owned update\\n" >> "$pkg/tasks.md"\n')
        script = snapshot + '\n[ -n "$backup" ] || exit 10\n' + action
        if phase == "postimage":
            script += 'path="$pkg/tasks.md"\n' + change + capture
            message = "Postimage capture failed: stop."
        else:
            script += capture + '\n[ -n "$step" ] || exit 11\n'
            script += 'stat -c "META_BEFORE=%a:%u:%g:%y:%h" -- "$path"\n' + change
            script += 'stat -c "META_AFTER=%a:%u:%g:%y:%h" -- "$path"\n' + restore
            message = "Postimage changed: stop and reconcile."
        expected = original + written
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0
    assert message in result.stderr
    assert target.read_bytes() == expected
    _assert_metadata_change(target, kind)
    if phase in ("restore", "remove-created") and kind in ("xattr", "acl"):
        before, after = [line.partition("=")[2] for line in result.stdout.splitlines() if line.startswith("META_")]
        assert before == after  # ACL/xattr changed without changing the previously compared fields
    assert (repo / ".git/index").read_bytes() == before_index
    assert (repo / "unrelated.txt").read_bytes() == before_other
    assert not list(package.glob("*.recovery.*"))


@pytest.mark.parametrize("kind", ["xattr", "setuid"])
@pytest.mark.parametrize("deleted", [False, True])
def test_documented_recovery_rejects_unsafe_backup_metadata(
    tmp_path: Path, kind: str, deleted: bool
) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    target = package / "tasks.md"
    before = target.read_bytes()
    before_index = (repo / ".git/index").read_bytes()
    snapshot = _doc_block('paths=("$pkg/spec.md"')
    capture = _doc_block('step=$(umask 077; mktemp')
    restore = _doc_block("Postimage changed: stop and reconcile.")
    action = 'rm -- "$pkg/tasks.md"\n' if deleted else 'printf "update\\n" >> "$pkg/tasks.md"\n'
    change = _metadata_change(tmp_path, kind, "$backup/before/$path")
    script = snapshot + '\n[ -n "$backup" ] || exit 10\n' + action + capture + '\n[ -n "$step" ] || exit 11\n' + change + restore
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0
    assert "Preimage metadata is unsafe: stop." in result.stderr
    assert not target.exists() if deleted else target.read_bytes() == before + b"update\n"
    preimage = next(recovery_parent.glob("spec-update.*/before/.spec/specs/*/tasks.md"))
    _assert_metadata_change(preimage, kind)
    assert (repo / ".git/index").read_bytes() == before_index


@pytest.mark.parametrize("kind", ["xattr", "acl", "setuid"])
@pytest.mark.parametrize("operation", ["legacy", "checkpoint"])
def test_documented_historical_paths_reject_metadata_only_changes(
    tmp_path: Path, kind: str, operation: str
) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    _write_historical_checkpoint(package)
    target = package / ("tasks.md" if operation == "legacy" else "update-checkpoint.json")
    before = target.read_bytes()
    before_index = (repo / ".git/index").read_bytes()
    snapshot = _doc_block('paths=("$pkg/spec.md"').replace(
        '# Example: paths+=("references/actual-source-of-truth.md")',
        'paths+=("$pkg/update-checkpoint.json")',
    )
    script = snapshot + '\n[ -n "$backup" ] || exit 10\n'
    if operation == "legacy":
        script += _doc_block("from update_checkpoint_support import inspect_update_checkpoint") + '\n'
        script += _doc_block('step=$(umask 077; mktemp') + '\n[ -n "$step" ] || exit 11\n'
        script += _metadata_change(tmp_path, kind, "$path")
        script += _doc_block("Legacy snapshot unavailable or current file changed")
        message = "Legacy snapshot unavailable or current file changed"
    else:
        script += 'path="$pkg/update-checkpoint.json"\n' + _metadata_change(tmp_path, kind, "$path")
        script += _doc_block("Checkpoint changed or not backed up")
        message = "Checkpoint changed or not backed up"
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0
    assert message in result.stderr
    assert target.read_bytes() == before
    _assert_metadata_change(target, kind)
    assert (repo / ".git/index").read_bytes() == before_index


@pytest.mark.parametrize("phase", ["preimage", "postimage", "restore"])
def test_documented_recovery_requires_explicit_visibility_attestation(tmp_path: Path, phase: str) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    target = package / "tasks.md"
    before = target.read_bytes()
    before_index = (repo / ".git/index").read_bytes()
    snapshot = _doc_block('paths=("$pkg/spec.md"')
    capture = _doc_block('step=$(umask 077; mktemp')
    restore = _doc_block("Postimage changed: stop and reconcile.")
    unverified = 'unset recovery_metadata_visibility\n'
    if phase == "preimage":
        script = unverified + snapshot
        expected = before
    else:
        script = snapshot + '\n[ -n "$backup" ] || exit 10\nprintf "update\\n" >> "$pkg/tasks.md"\n'
        script += (unverified + capture if phase == "postimage" else
                   capture + '\n[ -n "$step" ] || exit 11\n' + unverified + restore)
        expected = before + b"update\n"
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0
    assert "Metadata visibility not verified: stop." in result.stderr
    assert target.read_bytes() == expected
    assert (repo / ".git/index").read_bytes() == before_index


@pytest.mark.parametrize("phase", ["preimage", "postimage", "restore"])
def test_documented_recovery_stops_if_xattrs_cannot_be_queried(tmp_path: Path, phase: str) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    target = package / "tasks.md"
    before = target.read_bytes()
    before_index = (repo / ".git/index").read_bytes()
    code = ('import os,sys\n'
            'def deny(*args, **kwargs): raise OSError(95, "simulated xattr unsupported")\n'
            'os.listxattr = deny\nexec(compile(sys.stdin.read(), "<document>", "exec"))')
    bindir = _failing_python_wrapper(tmp_path, code)
    inject = f'PATH={shlex.quote(str(bindir))}:$PATH\n'
    snapshot = _doc_block('paths=("$pkg/spec.md"')
    capture = _doc_block('step=$(umask 077; mktemp')
    restore = _doc_block("Postimage changed: stop and reconcile.")
    if phase == "preimage":
        script = inject + snapshot
        expected = before
    else:
        script = snapshot + '\n[ -n "$backup" ] || exit 10\nprintf "update\\n" >> "$pkg/tasks.md"\n'
        script += (inject + capture if phase == "postimage" else capture + '\n[ -n "$step" ] || exit 11\n' + inject + restore)
        expected = before + b"update\n"
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0
    assert "simulated xattr unsupported" in result.stderr
    assert target.read_bytes() == expected
    assert (repo / ".git/index").read_bytes() == before_index


def test_documented_recovery_does_not_replace_target_if_staging_copy_fails(tmp_path: Path) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    target = package / "tasks.md"
    before = target.read_bytes()
    before_index = (repo / ".git/index").read_bytes()
    bindir = tmp_path / "injected-bin"
    bindir.mkdir()
    broken_cp = bindir / "cp"
    broken_cp.write_text('#!/bin/sh\nfor arg do last=$arg; done\nprintf partial > "$last"\nexit 1\n', encoding="utf-8")
    broken_cp.chmod(0o755)
    script = (_doc_block('paths=("$pkg/spec.md"') + '\n[ -n "$backup" ] || exit 10\n'
              + 'printf "update\\n" >> "$pkg/tasks.md"\n' + _doc_block('step=$(umask 077; mktemp')
              + '\n[ -n "$step" ] || exit 11\n' + f'PATH={shlex.quote(str(bindir))}:$PATH\n'
              + _doc_block("Postimage changed: stop and reconcile."))
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0
    assert "Could not safely restore preimage: stop." in result.stderr
    assert target.read_bytes() == before + b"update\n"
    assert not list(package.glob("*.recovery.*"))
    assert (repo / ".git/index").read_bytes() == before_index


@pytest.mark.parametrize("case", ["reappeared", "missing"])
def test_documented_recovery_refuses_reappeared_or_missing_postimage(tmp_path: Path, case: str) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    target = package / "tasks.md"
    before_index = (repo / ".git/index").read_bytes()
    update = 'rm -- "$pkg/tasks.md"\n' if case == "reappeared" else 'printf "update\\n" >> "$pkg/tasks.md"\n'
    conflict = 'printf "new external state\\n" > "$path"\n' if case == "reappeared" else 'rm -- "$path"\n'
    script = (_doc_block('paths=("$pkg/spec.md"') + '\n[ -n "$backup" ] || exit 10\n' + update
              + _doc_block('step=$(umask 077; mktemp') + '\n[ -n "$step" ] || exit 11\n'
              + conflict + _doc_block("Postimage changed: stop and reconcile."))
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0
    assert "Postimage changed: stop and reconcile." in result.stderr
    assert target.read_bytes() == b"new external state\n" if case == "reappeared" else not target.exists()
    assert (repo / ".git/index").read_bytes() == before_index


def test_incomplete_legacy_extraction_is_not_accepted_if_shell_continues(tmp_path: Path) -> None:
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    _write_historical_checkpoint(package)
    target = package / "tasks.md"
    before = target.read_bytes()
    before_index = (repo / ".git/index").read_bytes()
    code = ('import io,sys\nold_open=io.open\n'
            'def fail_checklist(path, mode="r", *args, **kwargs):\n'
            '    if str(path).endswith("/checklist.md") and "x" in mode:\n'
            '        raise OSError("simulated partial extraction")\n'
            '    return old_open(path, mode, *args, **kwargs)\n'
            'io.open=fail_checklist\nexec(compile(sys.stdin.read(), "<document>", "exec"))')
    bindir = _failing_python_wrapper(tmp_path, code)
    snapshot = _doc_block('paths=("$pkg/spec.md"').replace(
        '# Example: paths+=("references/actual-source-of-truth.md")',
        'paths+=("$pkg/update-checkpoint.json")',
    )
    script = snapshot + '\n[ -n "$backup" ] || exit 10\nsaved_path=$PATH\n'
    script += f'PATH={shlex.quote(str(bindir))}:$PATH\n'
    script += _doc_block("from update_checkpoint_support import inspect_update_checkpoint")
    # Deliberately violate the instruction to stop on extraction failure.
    script += '\nextract_status=$?\nPATH=$saved_path\nprintf "EXTRACT_STATUS=%s\\n" "$extract_status"\n'
    script += _doc_block('step=$(umask 077; mktemp') + '\n[ -n "$step" ] || exit 11\n'
    script += _doc_block("Legacy snapshot unavailable or current file changed")
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0
    assert "EXTRACT_STATUS=1" in result.stdout
    assert "simulated partial extraction" in result.stderr
    assert "Legacy snapshot unavailable or current file changed" in result.stderr
    partial = next(recovery_parent.glob("spec-update.*/legacy-snapshot"))
    assert (partial / "tasks.md").read_text(encoding="utf-8") == "# snapshot 任务\n"
    assert not (partial / ".complete").exists()
    assert target.read_bytes() == before
    assert (repo / ".git/index").read_bytes() == before_index


def test_same_basename_checkpoint_does_not_prove_project_provenance(tmp_path: Path) -> None:
    source = _make_package(tmp_path / "source")
    destination_root = tmp_path / "destination"
    destination = _make_package(destination_root)
    _write_historical_checkpoint(source)
    checkpoint_path(destination).write_bytes(checkpoint_path(source).read_bytes())
    result = _run(destination_root, "status", "--slug", SLUG)
    assert result.returncode == 3, result.stderr  # schema/checksum/basename are insufficient provenance
    assert "有效 snapshot" in result.stdout


@pytest.mark.parametrize("profile", ["core", "full"])
def test_recovery_guide_is_exported_and_linked_in_each_profile(tmp_path: Path, profile: str) -> None:
    output = tmp_path / profile
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "export_skill_package.py"), "--root", str(SCRIPTS_DIR.parent),
         "--output", str(output), "--skip-signing", "--profile", profile],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    stage = output / "references/stages/update.md"
    guide = output / "references/update-recovery.md"
    assert "../update-recovery.md" in stage.read_text(encoding="utf-8")
    assert guide.read_bytes() == (SCRIPTS_DIR.parent / "references/update-recovery.md").read_bytes()

def test_recovery_binds_step_to_current_backup(tmp_path: Path) -> None:
    """A postimage step captured under one backup must not be restored against
    a different (stale) backup, even when the file bytes still match."""
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    target = package / "tasks.md"
    before = target.read_bytes()
    before_index = (repo / ".git/index").read_bytes()
    snapshot = _doc_block('paths=("$pkg/spec.md"')
    capture = _doc_block('step=$(umask 077; mktemp')
    restore = _doc_block("Postimage changed: stop and reconcile.")
    script = (
        snapshot + '\n[ -n "$backup" ] || exit 10\n'
        + "printf 'update A\\n' >> \"$pkg/tasks.md\"\n"
        + capture + '\n[ -n "$step" ] || exit 11\n'
        + "backup1=$backup\n"
        + snapshot + '\n[ -n "$backup" ] || exit 10\n'
        + "printf 'update B\\n' >> \"$pkg/tasks.md\"\n"
        + capture + '\n[ -n "$step" ] || exit 11\n'
        + "backup=$backup1\n"
        + restore
    )
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0, result.stderr
    assert "Recovery binding mismatch" in result.stderr
    assert target.read_bytes() == before + b"update A\nupdate B\n"
    assert (repo / ".git/index").read_bytes() == before_index


def test_recovery_binds_backup_to_project_root(tmp_path: Path) -> None:
    """A backup captured in one checkout must not restore into another checkout
    with the same relative paths and identical bytes."""
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    repo_a, package_a, recovery_a = _dirty_repo(tmp_path / "a")
    repo_b, package_b, recovery_b = _dirty_repo(tmp_path / "b")
    snapshot = _doc_block('paths=("$pkg/spec.md"')
    capture = _doc_block('step=$(umask 077; mktemp')
    restore = _doc_block("Postimage changed: stop and reconcile.")
    result_a = _bash_doc(repo_a, package_a, recovery_a,
                         snapshot + '\n[ -n "$backup" ] || exit 10\n' + capture + '\n[ -n "$step" ] || exit 11\n')
    assert result_a.returncode == 0, result_a.stderr
    backup_a = next(recovery_a.glob("spec-update.*"))
    step_a = next(backup_a.glob("step.*"))
    before_b = (package_b / "tasks.md").read_bytes()
    before_index_b = (repo_b / ".git/index").read_bytes()
    script_b = (
        'path="$pkg/tasks.md"\n'
        + "backup=" + shlex.quote(str(backup_a)) + "\n"
        + "step=" + shlex.quote(str(step_a)) + "\n"
        + restore
    )
    result_b = _bash_doc(repo_b, package_b, recovery_b, script_b)
    assert result_b.returncode != 0, result_b.stderr
    assert "Recovery binding mismatch" in result_b.stderr
    assert (package_b / "tasks.md").read_bytes() == before_b
    assert (repo_b / ".git/index").read_bytes() == before_index_b


def test_recovery_interruption_leaves_no_world_readable_preimage(tmp_path: Path) -> None:
    """Interruption between the staged copy and the final move must never leave a
    world-readable preimage beside the target; any leftover is a 0700 directory."""
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    bindir = tmp_path / "injected-bin"
    bindir.mkdir()
    broken_mv = bindir / "mv"
    broken_mv.write_text("#!/bin/sh\nkill -9 $PPID\n", encoding="utf-8")
    broken_mv.chmod(0o755)
    snapshot = _doc_block('paths=("$pkg/spec.md"')
    capture = _doc_block('step=$(umask 077; mktemp')
    restore = _doc_block("Postimage changed: stop and reconcile.")
    script = (
        snapshot + '\n[ -n "$backup" ] || exit 10\n'
        + "printf 'owned update\\n' >> \"$pkg/tasks.md\"\n"
        + capture + '\n[ -n "$step" ] || exit 11\n'
        + "PATH=" + shlex.quote(str(bindir)) + ":$PATH\n"
        + restore
    )
    _bash_doc(repo, package, recovery_parent, script)
    # The helper was killed mid-staging; the shell may exit by signal.
    for leftover in package.glob("*.recovery.*"):
        assert leftover.is_dir(), leftover
        assert leftover.stat().st_mode & 0o077 == 0, oct(leftover.stat().st_mode)
    # Remove the interrupted run's private staging dir so a clean run can proceed.
    for leftover in package.glob("*.recovery.*"):
        shutil.rmtree(leftover)
    # A clean (non-interrupted) path removes the staging directory entirely.
    result2 = _bash_doc(repo, package, recovery_parent,
                        snapshot + '\n[ -n "$backup" ] || exit 10\n'
                        + "printf 'owned update\\n' >> \"$pkg/tasks.md\"\n"
                        + capture + '\n[ -n "$step" ] || exit 11\n' + restore)
    assert result2.returncode == 0, result2.stderr
    assert not list(package.glob("*.recovery.*"))


@pytest.mark.parametrize("backup_expr,message", [
    ("", "Missing recovery directory"),
    ("relative/path", "must be absolute"),
    ("$repo", "must be outside the repository"),
], ids=["empty", "relative", "inside-repo"])
def test_extraction_refuses_invalid_backup_location(tmp_path: Path, backup_expr: str, message: str) -> None:
    """The historical-snapshot extraction must fail closed for a missing, relative,
    or in-repo backup directory instead of writing legacy-snapshot into the tree."""
    repo, package, recovery_parent = _dirty_repo(tmp_path)
    _write_historical_checkpoint(package)
    extract = _doc_block("from update_checkpoint_support import inspect_update_checkpoint")
    backup_val = str(repo) if backup_expr == "$repo" else backup_expr
    script = "backup=" + shlex.quote(backup_val) + "\n" + extract
    result = _bash_doc(repo, package, recovery_parent, script)
    assert result.returncode != 0, result.stdout
    assert message in result.stderr
    assert not (repo / "legacy-snapshot").exists()


def test_recovery_docs_disable_git_pathspec_magic() -> None:
    """Every git diff/log guidance line that names <path> must pass --literal-pathspecs
    so a path beginning with :(...) is not silently ignored by pathspec magic."""
    recovery = (SCRIPTS_DIR.parent / "references/update-recovery.md").read_text(encoding="utf-8")
    stage = (SCRIPTS_DIR.parent / "references/stages/update.md").read_text(encoding="utf-8")
    for name, text in (("update-recovery.md", recovery), ("update.md", stage)):
        lines = [line for line in text.splitlines()
                 if re.search(r"git (diff|log).*-- <path>", line)]
        assert lines, name
        for line in lines:
            assert "--literal-pathspecs" in line, (name, line)


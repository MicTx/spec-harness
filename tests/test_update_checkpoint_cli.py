# tests/test_update_checkpoint_cli.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""CLI contract tests for scripts/update_checkpoint.py.

Exercises the recovery CLI end to end through a real interpreter process:
POSIX exit codes per subcommand, stdout/stderr separation, and the on-disk
effects (or deliberate absence of effects) of status/rollback/complete.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from update_checkpoint_support import begin_update_checkpoint, checkpoint_path  # noqa: E402

SLUG = "2026-09-13_demo-pkg"
OTHER_SLUG = "2026-09-14_other-pkg"
INTENT = "追加数据导出任务"


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


def test_status_reports_active_checkpoint_with_intent(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    begin_update_checkpoint(package, INTENT)

    result = _run(tmp_path, "status", "--slug", SLUG)

    assert result.returncode == 3
    assert f"{SLUG}：存在未完成 update checkpoint" in result.stdout
    assert f"意图：{INTENT}" in result.stdout
    assert result.stderr == ""


def test_status_scan_reports_only_interrupted_packages(tmp_path: Path) -> None:
    _make_package(tmp_path, slug=OTHER_SLUG)
    interrupted = _make_package(tmp_path, slug=SLUG)
    begin_update_checkpoint(interrupted, INTENT)

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
    begin_update_checkpoint(package, INTENT)
    _corrupt_checkpoint(package)

    result = _run(tmp_path, "status", "--slug", SLUG)

    assert result.returncode == 4
    assert f"{SLUG}：存在未完成 update checkpoint" in result.stdout
    assert "已损坏" in result.stdout


def test_status_scan_reports_worst_state_across_packages(tmp_path: Path) -> None:
    active = _make_package(tmp_path, slug=OTHER_SLUG)
    begin_update_checkpoint(active, INTENT)
    corrupted = _make_package(tmp_path, slug=SLUG)
    begin_update_checkpoint(corrupted, INTENT)
    _corrupt_checkpoint(corrupted)

    result = _run(tmp_path, "status")

    assert result.returncode == 4
    assert SLUG in result.stdout and OTHER_SLUG in result.stdout


# --- rollback ---------------------------------------------------------------


def test_rollback_restores_triad_and_clears_checkpoint(tmp_path: Path) -> None:
    package = _make_package(tmp_path, marker="original")
    original = (package / "spec.md").read_text(encoding="utf-8")
    begin_update_checkpoint(package, INTENT)
    _write_text(package / "spec.md", "# half-updated\n")

    result = _run(tmp_path, "rollback", "--slug", SLUG)

    assert result.returncode == 0, result.stderr
    assert f"{SLUG}：update checkpoint 已回滚" in result.stdout
    assert f"意图：{INTENT}" in result.stdout
    assert (package / "spec.md").read_text(encoding="utf-8") == original
    assert not checkpoint_path(package).exists()
    assert _run(tmp_path, "status", "--slug", SLUG).returncode == 0


def test_rollback_without_checkpoint_fails_cleanly(tmp_path: Path) -> None:
    _make_package(tmp_path)

    result = _run(tmp_path, "rollback", "--slug", SLUG)

    assert result.returncode == 1
    assert result.stderr.startswith("error:")
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_rollback_corrupted_checkpoint_is_refused(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    begin_update_checkpoint(package, INTENT)
    _corrupt_checkpoint(package)

    result = _run(tmp_path, "rollback", "--slug", SLUG)

    assert result.returncode == 1
    assert result.stderr.startswith("error:")
    assert checkpoint_path(package).exists()
    assert checkpoint_path(package).read_text(encoding="utf-8") == "{ not json"


# --- complete ---------------------------------------------------------------


def test_complete_clears_checkpoint_and_keeps_update(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    begin_update_checkpoint(package, INTENT)
    _write_text(package / "tasks.md", "# updated tasks\n\n- [ ] task one\n- [ ] new task\n")

    result = _run(tmp_path, "complete", "--slug", SLUG)

    assert result.returncode == 0, result.stderr
    assert f"{SLUG}：update checkpoint 已确认完成" in result.stdout
    assert f"意图：{INTENT}" in result.stdout
    assert not checkpoint_path(package).exists()
    assert "new task" in (package / "tasks.md").read_text(encoding="utf-8")
    assert _run(tmp_path, "status", "--slug", SLUG).returncode == 0


def test_complete_with_matching_expect_intent_succeeds(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    begin_update_checkpoint(package, INTENT)

    result = _run(tmp_path, "complete", "--slug", SLUG, "--expect-intent", INTENT)

    assert result.returncode == 0, result.stderr
    assert not checkpoint_path(package).exists()


def test_complete_with_wrong_expect_intent_refuses_and_keeps_checkpoint(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    begin_update_checkpoint(package, INTENT)

    result = _run(tmp_path, "complete", "--slug", SLUG, "--expect-intent", "另一个更新意图")

    assert result.returncode == 1
    assert result.stderr.startswith("error:")
    assert checkpoint_path(package).exists()


def test_complete_without_checkpoint_fails_cleanly(tmp_path: Path) -> None:
    _make_package(tmp_path)

    result = _run(tmp_path, "complete", "--slug", SLUG)

    assert result.returncode == 1
    assert result.stderr.startswith("error:")
    assert "Traceback" not in result.stderr


def test_complete_corrupted_checkpoint_is_refused(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    begin_update_checkpoint(package, INTENT)
    _corrupt_checkpoint(package)

    result = _run(tmp_path, "complete", "--slug", SLUG)

    assert result.returncode == 1
    assert checkpoint_path(package).exists()


# --- argument and path validation -------------------------------------------


def test_missing_package_directory_fails(tmp_path: Path) -> None:
    result = _run(tmp_path, "status", "--slug", "missing-pkg")
    assert result.returncode == 1
    assert "task package not found" in result.stderr

    result = _run(tmp_path, "rollback", "--slug", "missing-pkg")
    assert result.returncode == 1
    assert "task package not found" in result.stderr


def test_invalid_slug_fails_as_operational_error(tmp_path: Path) -> None:
    result = _run(tmp_path, "status", "--slug", "BAD")

    assert result.returncode == 1
    assert result.stderr.startswith("error:")
    assert "slug" in result.stderr


def test_missing_subcommand_and_missing_slug_exit_two(tmp_path: Path) -> None:
    assert _run(tmp_path).returncode == 2
    assert _run(tmp_path, "rollback").returncode == 2
    assert _run(tmp_path, "complete").returncode == 2


def test_rejects_invalid_specs_dir_without_traceback(tmp_path: Path) -> None:
    result = _run(tmp_path, "status", "--specs-dir", "../outside")

    assert result.returncode == 1
    assert "error: --specs-dir" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("command", ["status", "rollback", "complete"])
def test_help_documents_exit_code_contract(command: str, tmp_path: Path) -> None:
    result = _run(tmp_path, command, "--help")

    assert result.returncode == 0
    for code_line in ("0  success", "3  status: active checkpoint", "4  status: corrupted checkpoint"):
        assert code_line in result.stdout


@pytest.mark.parametrize("command", ["status", "rollback", "complete"])
@pytest.mark.parametrize("target_kind", ["external", "internal", "dangling"])
def test_recovery_refuses_package_directory_symlinks(tmp_path: Path, command: str, target_kind: str) -> None:
    root = tmp_path / "project"
    specs = root / ".spec" / "specs"
    specs.mkdir(parents=True)
    target_root = root / "storage" if target_kind == "internal" else tmp_path / "outside"
    target = _make_package(target_root)
    begin_update_checkpoint(target, INTENT)
    _write_text(target / "tasks.md", "# half-applied update\n")
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    link = specs / SLUG
    link.symlink_to(target if target_kind != "dangling" else target / "missing", target_is_directory=True)

    result = _run(root, command, "--slug", SLUG)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error:")
    assert "symlink" in result.stderr
    assert "Traceback" not in result.stderr
    assert link.is_symlink()
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


@pytest.mark.parametrize("command", ["status", "rollback", "complete"])
def test_recovery_refuses_specs_directory_resolving_outside_root(tmp_path: Path, command: str) -> None:
    root = tmp_path / "project"
    (root / ".spec").mkdir(parents=True)
    target = _make_package(tmp_path / "outside")
    begin_update_checkpoint(target, INTENT)
    _write_text(target / "tasks.md", "# half-applied update\n")
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    (root / ".spec" / "specs").symlink_to(target.parent, target_is_directory=True)

    result = _run(root, command, "--slug", SLUG)

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
    begin_update_checkpoint(target, INTENT)
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
    begin_update_checkpoint(package, INTENT)
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
        assert "已损坏，需人工恢复" in result.stdout
        assert result.stderr == ""
        assert "Traceback" not in result.stdout
        assert {p.name: p.read_bytes() for p in package.iterdir()} == before
    for command in ("complete", "rollback"):
        result = _run(tmp_path, command, "--slug", SLUG)
        assert result.returncode == 1, result.stderr
        assert result.stdout == ""
        assert result.stderr.startswith("error:")
        assert "corrupted update checkpoint" in result.stderr
        assert "Traceback" not in result.stderr
        assert {p.name: p.read_bytes() for p in package.iterdir()} == before

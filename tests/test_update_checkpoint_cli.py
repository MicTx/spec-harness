# tests/test_update_checkpoint_cli.py
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""CLI contract tests for scripts/update_checkpoint.py.

The CLI is read-only: ``status`` recognizes historical checkpoints left
behind by the retired mechanism, and the retired write subcommands
(``begin`` / ``rollback`` / ``complete``) refuse with the Git WIP replacement
guidance. Exercises a real interpreter process: POSIX exit codes per
subcommand, stdout/stderr separation, and the absence of on-disk effects.
"""

from __future__ import annotations

import hashlib
import json
import os
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


def test_status_reports_historical_checkpoint_with_intent_and_git_recovery(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    _write_historical_checkpoint(package, INTENT)

    result = _run(tmp_path, "status", "--slug", SLUG)

    assert result.returncode == 3
    assert f"{SLUG}：存在未完成 update checkpoint" in result.stdout
    assert f"意图：{INTENT}" in result.stdout
    assert "Git 历史确认/恢复三件套后删除该文件" in result.stdout
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
    assert "Git WIP" in result.stderr
    assert "git reset --hard" in result.stderr
    assert "Traceback" not in result.stderr
    # Refusal is read-only: package state is untouched, nothing written.
    assert {path.name: path.read_bytes() for path in package.iterdir()} == before


def test_retired_help_prints_replacement_guidance(tmp_path: Path) -> None:
    for command in ("begin", "rollback", "complete"):
        result = _run(tmp_path, command, "--help")

        assert result.returncode == 0
        assert "已退役" in result.stdout
        assert "git reset --hard" in result.stdout


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

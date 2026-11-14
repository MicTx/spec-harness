"""Pin tests for the repair package kind (spec-kit #442 / OpenSpec #821 parity)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo, started_package

ROOT = Path(__file__).resolve().parents[1]


def _mark_repair(package: Path, origin: str | None = SLUG) -> None:
    spec = package / "spec.md"
    lines = "- kind: repair\n" + (f"- repair-of: {origin}\n" if origin else "")
    spec.write_text(
        spec.read_text(encoding="utf-8").replace("## Goal\n", "## Goal\n" + lines, 1),
        encoding="utf-8",
    )


def _archive_origin(root: Path, origin: str = SLUG) -> None:
    archive = root / ".spec" / "specs" / "archive" / origin
    archive.mkdir(parents=True, exist_ok=True)
    source = root / ".spec" / "specs" / origin
    for name in ("spec.md", "tasks.md"):
        (archive / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")


def _deliver_fixture(root: Path) -> None:
    started_package(root)
    assert cli(root, "verify", "task-value").returncode == 0
    result = cli(root, "check")
    assert result.returncode == 0, result.stderr
    assert (root / ".spec" / "specs" / "archive" / SLUG / "spec.md").is_file()


def test_scaffold_repair_from_archive(tmp_path):
    repo(tmp_path)
    _deliver_fixture(tmp_path)

    result = cli(tmp_path, "repair", "--of", SLUG)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    slug = payload["slug"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_repair-[a-z0-9-]+", slug)
    package = tmp_path / ".spec" / "specs" / slug
    spec = (package / "spec.md").read_text(encoding="utf-8")
    assert "<!-- spec-format: 3 -->" in spec
    assert "- kind: repair" in spec
    assert f"- repair-of: {SLUG}" in spec
    assert (package / "tasks.md").is_file()
    lint = cli(tmp_path, "--slug", slug, "plan-lint", "--static")
    assert lint.returncode == 0, lint.stdout + lint.stderr
    assert json.loads(lint.stdout)["ready"] is True


def test_scaffold_repair_rejects_unknown_origin(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)

    result = cli(tmp_path, "repair", "--of", "2026-01-01_missing")

    assert result.returncode == 1
    assert "archived package not found" in result.stderr


def test_scaffold_repair_refuses_duplicate(tmp_path):
    repo(tmp_path)
    _deliver_fixture(tmp_path)
    assert cli(tmp_path, "repair", "--of", SLUG).returncode == 0

    result = cli(tmp_path, "repair", "--of", SLUG)

    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_route_repair_note(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path)
    _mark_repair(package)

    card = json.loads(cli(tmp_path, "next").stdout)

    assert card["kind"] == "repair"
    assert card["repair_of"] == SLUG
    assert "Repair of" in card["next"]
    assert "full gate" in card["next"]


def test_lint_repair_requires_origin(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    _mark_repair(package, origin=None)

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 1
    assert any("repair-of" in gap["detail"] for gap in json.loads(result.stdout)["gaps"])


def test_lint_repair_rejects_missing_archive(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    _mark_repair(package, origin="2026-01-01_missing")

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 1
    assert any("archived" in gap["detail"] for gap in json.loads(result.stdout)["gaps"])


def test_lint_repair_accepts_valid_origin(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    _archive_origin(tmp_path)
    _mark_repair(package)

    result = cli(tmp_path, "plan-lint", "--static")

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["ready"] is True


def test_repair_scaffold_tolerates_minimal_origin(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    archive = tmp_path / ".spec" / "specs" / "archive" / SLUG
    archive.mkdir(parents=True)
    minimal = (tmp_path / ".spec" / "specs" / SLUG / "spec.md").read_text(encoding="utf-8")
    minimal = minimal.split("## Charter")[0].split("## Scope")[0]
    minimal += "## Criteria\n- C1 | Fixture works | task-value\n## Decisions\n## Parked\n"
    (archive / "spec.md").write_text(minimal, encoding="utf-8")
    (archive / "tasks.md").write_text(
        (tmp_path / ".spec" / "specs" / SLUG / "tasks.md").read_text(encoding="utf-8"), encoding="utf-8"
    )

    result = cli(tmp_path, "repair", "--of", SLUG)

    assert result.returncode == 0, result.stderr
    slug = json.loads(result.stdout)["slug"]
    lint = cli(tmp_path, "--slug", slug, "plan-lint", "--static")
    assert lint.returncode == 0, lint.stdout + lint.stderr


def test_stage_surface_repair():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    user = re.search(r"^USER_STAGES=\(([^)]*)\)", installer, re.MULTILINE).group(1).split()
    core = re.search(r"^CORE_STAGES=\(([^)]*)\)", installer, re.MULTILINE).group(1).split()
    assert "repair" in user
    assert user == core
    for name in ("SKILL.md", "README.md", "README-en.md", "docs/README.md", "docs/README-en.md"):
        assert "/spec:repair" in (ROOT / name).read_text(encoding="utf-8"), name
    for name in ("references/plan.md", "references/exec.md", "references/format.md"):
        assert "repair" in (ROOT / name).read_text(encoding="utf-8"), name
    retired = re.search(r"^RETIRED_STAGES=\(([^)]*)\)", installer, re.MULTILINE).group(1).split()
    assert "repair" not in retired


def test_scaffold_repair_keeps_origin_charter(tmp_path):
    repo(tmp_path)
    _deliver_fixture(tmp_path)

    result = cli(tmp_path, "repair", "--of", SLUG)

    assert result.returncode == 0, result.stderr
    slug = json.loads(result.stdout)["slug"]
    origin_spec = (tmp_path / ".spec" / "specs" / "archive" / SLUG / "spec.md").read_text(encoding="utf-8")
    repair_spec = (tmp_path / ".spec" / "specs" / slug / "spec.md").read_text(encoding="utf-8")
    for key in (
        "V1 priority",
        "V2 scope",
        "V3 verification",
        "V4 finish",
        "V5 dependencies",
        "V6 compatibility",
        "V7 blocked",
    ):
        origin_line = next(line for line in origin_spec.splitlines() if line.startswith(f"- {key}:"))
        assert origin_line in repair_spec

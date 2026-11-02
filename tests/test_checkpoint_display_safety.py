# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Historical checkpoint diagnostics must not execute terminal controls."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from update_checkpoint_support import inspect_update_checkpoint  # noqa: E402

SLUG = "2026-09-13_display-safety"
INTENT = "正常中文🙂\x1b[2J\x1b[H伪造\n第二行\x9b\u202e"


def checkpoint(root: Path, slug: str = SLUG) -> Path:
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text("# Display - 项目范围\n", encoding="utf-8")
    (package / "tasks.md").write_text("## Tasks\n- [ ] work\n", encoding="utf-8")
    payload = {
        "version": 1,
        "package": slug,
        "intent": INTENT,
        "createdAt": "2026-09-21T12:00:00+08:00",
        "snapshot": {"spec.md": "历史原文\n", "tasks.md": "原任务\n"},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["checksum"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    (package / "update-checkpoint.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return package


def run_cli(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


@pytest.mark.parametrize("surface", ["status", "route", "route-json", "route-brief", "report"])
def test_checkpoint_intent_is_escaped_on_each_output_surface(tmp_path: Path, surface: str) -> None:
    package = checkpoint(tmp_path)
    record = package / "update-checkpoint.json"
    before = record.read_bytes()
    if surface == "status":
        result = run_cli("update_checkpoint.py", "--root", str(tmp_path), "status", "--slug", SLUG)
        assert result.returncode == 3, result.stderr
    elif surface.startswith("route"):
        extra = ["--format", "json"] if surface == "route-json" else ["--brief"] if surface == "route-brief" else []
        result = run_cli("route_spec_package.py", "--root", str(tmp_path), "--slug", SLUG, *extra)
        assert result.returncode == 0, result.stderr
    else:
        result = run_cli("report_spec_package.py", "--root", str(tmp_path), "--view", "status", "--slug", SLUG)
        assert result.returncode == 0, result.stderr
    output = result.stdout
    if surface == "route-json":
        # The ordinary machine response reports only checkpoint state, not intent.
        assert json.loads(output)["updateCheckpoint"]
        assert "正常中文🙂" not in output
    else:
        if surface == "route-brief":
            output = " ".join(json.loads(output)["alerts"])
        assert "正常中文🙂" in output
        assert "\\x1b[2J" in output and "\\n第二行" in output
        assert "\\x9b" in output and "\\u202e" in output
    assert "\x1b" not in output and "\x9b" not in output and "\u202e" not in output
    assert record.read_bytes() == before
    inspected = inspect_update_checkpoint(package)
    assert inspected.intent == INTENT and inspected.snapshot["spec.md"] == "历史原文\n"


def test_scan_escapes_untrusted_directory_name_and_corruption_error(tmp_path: Path) -> None:
    slug = "2026-09-13_control\x1b[2J"
    package = checkpoint(tmp_path, slug)
    (package / "update-checkpoint.json").write_text("{invalid", encoding="utf-8")
    result = run_cli("update_checkpoint.py", "--root", str(tmp_path), "status")
    assert result.returncode == 4
    assert "\x1b" not in result.stdout
    assert "\\x1b[2J" in result.stdout


def test_checkpoint_display_bounds_large_text_without_changing_record(tmp_path: Path) -> None:
    from update_checkpoint_support import checkpoint_display_text

    text = "测" * 600 + "\x1b"
    rendered = checkpoint_display_text(text)
    assert rendered == "测" * 512 + "…"
    assert len(rendered) == 513

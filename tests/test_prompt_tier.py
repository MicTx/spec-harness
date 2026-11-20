"""Pin the installer prompt tier (full/compact) and the runtime brief budget.

``SPEC_PROMPT_TIER`` selects the density of the command bodies baked into the
installed host commands: ``full`` (default) keeps the complete per-stage
instructions, ``compact`` ships a short body that still carries one autonomy
line. The tier is recorded in the installed skill's ``.spec-skill-install``
marker and in ``install-manifest.json``, and ``scripts/route.py`` honors it
when truncating session briefs (1450 default, 900 in compact).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.v3_support import cli, make_package, repo, started_package

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import route  # noqa: E402

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0 or not Path("/bin/bash").exists(),
    reason="bash not available",
)

COMPACT_RUN_AUTONOMY = "Reversible blockers are decided by you and logged, not asked"
FULL_RUN_AUTONOMY = "Reversible blockers are decided by V1 and logged, not asked"


def sandbox_env(home: Path, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "BACKUP_ROOT": str(home / ".spec-skill-backups"),
    }
    for var in ("INSTALL_HOSTS", "INSTALL_PROFILE", "SOURCE_DIR", "FORCE", "INSTALL_DIR", "SPEC_PROMPT_TIER"):
        env.pop(var, None)
    env.update({key: value for key, value in os.environ.items() if key in ("TMPDIR",)})
    if extra_env:
        env.update(extra_env)
    return env


def run_installer(home: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = sandbox_env(home, {"INSTALL_HOSTS": "claude"})
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


def write_marker(tmp_path: Path, body: str) -> Path:
    marker = tmp_path / ".spec-skill-install"
    marker.write_text(body, encoding="utf-8")
    return marker


# --- route.prompt_tier: env override, then marker line, else full ---


def test_prompt_tier_env_value_wins_over_marker(tmp_path, monkeypatch):
    compact_marker = write_marker(tmp_path, "installed_by=spec/install.sh\ntier=compact\n")
    monkeypatch.setenv("SPEC_PROMPT_TIER", "full")
    assert route.prompt_tier(compact_marker) == "full"
    monkeypatch.setenv("SPEC_PROMPT_TIER", "compact")
    assert route.prompt_tier(tmp_path / "missing-marker") == "compact"


def test_prompt_tier_marker_line_parses_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SPEC_PROMPT_TIER", raising=False)
    compact_marker = write_marker(tmp_path, "installed_by=spec/install.sh\nversion=1.1.0\ntier=compact\n")
    assert route.prompt_tier(compact_marker) == "compact"
    full_marker = write_marker(tmp_path, "installed_by=spec/install.sh\ntier=full\n")
    assert route.prompt_tier(full_marker) == "full"
    # A padded tier line is not the pinned ^tier=compact$ marker row.
    padded = write_marker(tmp_path, "tier=compact # trimmed\n")
    assert route.prompt_tier(padded) == "full"
    assert route.prompt_tier(None) == "full"
    assert route.prompt_tier(tmp_path / "absent") == "full"


def test_prompt_tier_invalid_env_falls_back_to_marker(tmp_path, monkeypatch):
    compact_marker = write_marker(tmp_path, "installed_by=spec/install.sh\ntier=compact\n")
    monkeypatch.setenv("SPEC_PROMPT_TIER", "bogus")
    assert route.prompt_tier(compact_marker) == "compact"
    monkeypatch.delenv("SPEC_PROMPT_TIER")
    assert route.prompt_tier(compact_marker) == "compact"


# --- route.brief: compact tier truncates at 900 and keeps the Spark line ---


def test_compact_env_brief_stays_within_900_and_keeps_spark(tmp_path, monkeypatch):
    repo(tmp_path)
    started_package(tmp_path)
    monkeypatch.setenv("SPEC_PROMPT_TIER", "compact")

    brief = route.brief(tmp_path)

    assert len(brief) <= 900
    assert route.SPARK_MARKER in brief


def test_compact_env_truncates_long_brief_to_900(tmp_path, monkeypatch):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- goal: Change the fixture value", "- goal: " + "Divergent design thinking " * 40
        ),
        encoding="utf-8",
    )
    assert cli(tmp_path, "plan-go").returncode == 0

    monkeypatch.delenv("SPEC_PROMPT_TIER", raising=False)
    assert route.brief_budget() == 1450
    full_brief = route.brief(tmp_path)
    assert len(full_brief) <= 1450
    assert "Next:" in full_brief
    assert "task-value" in full_brief
    assert route.SPARK_MARKER in full_brief

    monkeypatch.setenv("SPEC_PROMPT_TIER", "compact")
    assert route.brief_budget() == 900
    compact_brief = route.brief(tmp_path)
    assert len(compact_brief) <= 900
    # Priority rendering, not a raw prefix slice: the execution core and the
    # complete Spark line survive while the long goal is the compressible part.
    assert "Next:" in compact_brief
    assert "task-value" in compact_brief
    assert route.SPARK_MARKER in compact_brief
    assert compact_brief.endswith(route.spark("run"))


def test_long_verify_brief_points_at_full_card_not_a_truncated_command(tmp_path):
    repo(tmp_path)
    long_verify = "true VERIFY_COMMAND_HEAD " + "x" * 4096 + " VERIFY_COMMAND_TAIL"
    make_package(tmp_path, verify=long_verify)
    assert cli(tmp_path, "plan-go").returncode == 0

    card = route.next_card(tmp_path)
    # The JSON card keeps the full command verbatim; only the brief swaps it
    # for a pointer so nobody copies a half-cut command as executable.
    assert card["task"]["verify"] == long_verify
    assert long_verify in card["next"]
    assert long_verify in json.loads(cli(tmp_path, "next").stdout)["next"]
    brief = route.brief(tmp_path)
    assert "VERIFY_COMMAND_HEAD" not in brief
    assert "spec.py next" in brief
    assert len(brief) <= 1450

    compact = route.brief(tmp_path, budget=520)
    assert len(compact) <= 520
    assert route.SPARK_MARKER in compact
    assert "VERIFY_COMMAND_HEAD" not in compact


def test_brief_budget_stress_keeps_failure_pending_and_spark(tmp_path, monkeypatch):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    text = spec.read_text(encoding="utf-8")
    text = text.replace("- goal: Change the fixture value", "- goal: " + "G" * 2000)
    text = text.replace("## Parked\n", "## Parked\n- P1 | " + "P" * 1200 + " | by: V1\n")
    spec.write_text(text, encoding="utf-8")
    assert cli(tmp_path, "plan-go").returncode == 0
    with (package / "runs.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "kind": "run",
                    "task": "task-value",
                    "cmd": "true",
                    "exit": 1,
                    "sig": "FAILURE_ANCHOR " + "F" * 1200,
                }
            )
            + "\n"
        )
    (package / "state.json").write_text(
        json.dumps(
            {
                "task": {"id": "task-value"},
                "pending_task": "task-value",
                "pending": ["PENDING_ANCHOR " + "C" * 300],
            }
        ),
        encoding="utf-8",
    )

    for tier, limit in (("full", 1450), ("compact", 900), ("aggressive", 700)):
        monkeypatch.setenv("SPEC_PROMPT_TIER", tier)
        brief = route.brief(tmp_path)
        assert len(brief) <= limit, tier
        assert "task-value" in brief, tier
        assert "Next:" in brief, tier
        assert "FAILURE_ANCHOR" in brief, tier
        assert "PENDING_ANCHOR" in brief, tier
        assert route.SPARK_MARKER in brief, tier
        assert brief.endswith(route.spark("run")), tier


def test_blocked_task_ignores_directive_for_old_command(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path, verify="true")
    tasks = package / "tasks.md"
    tasks.write_text(
        tasks.read_text(encoding="utf-8")
        .replace("- [ ] Change value", "- [!] Change value")
        .replace("- verify: `true`", "- verify: `false`"),
        encoding="utf-8",
    )
    with (package / "runs.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"kind": "run", "task": "task-value", "cmd": "true", "exit": 1, "directive": "OLD_RUNG"}) + "\n"
        )
    card = route.next_card(tmp_path)
    assert 'spec.py unblock task-value --reason "<new evidence>"' in card["next"]
    assert "OLD_RUNG" not in json.dumps(card)


# --- installer: tier lands in marker, manifest, command bodies and log ---


@requires_bash
def test_compact_install_writes_tier_marker_manifest_and_run_autonomy(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, {"SPEC_PROMPT_TIER": "compact"})
    assert result.returncode == 0, result.stderr
    assert "six-stage command kernel" in result.stdout
    assert "; prompt tier: compact" in result.stdout

    skill = home / ".claude" / "skills" / "spec"
    marker = (skill / ".spec-skill-install").read_text(encoding="utf-8")
    assert re.search(r"^tier=compact$", marker, flags=re.MULTILINE)
    manifest = json.loads((skill / "install-manifest.json").read_text(encoding="utf-8"))
    assert manifest["prompt_tier"] == "compact"

    run_body = (home / ".claude" / "commands" / "spec" / "run.md").read_text(encoding="utf-8")
    assert COMPACT_RUN_AUTONOMY in run_body
    assert "park the irreversible" in run_body
    assert "built-in one-shot" not in run_body


@requires_bash
@pytest.mark.parametrize("extra_env", [None, {"SPEC_PROMPT_TIER": "full"}], ids=["default", "explicit-full"])
def test_full_tier_install_keeps_full_bodies_and_marker(tmp_path, extra_env):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, extra_env)
    assert result.returncode == 0, result.stderr
    assert "six-stage command kernel" in result.stdout
    assert "; prompt tier: full" in result.stdout

    skill = home / ".claude" / "skills" / "spec"
    marker = (skill / ".spec-skill-install").read_text(encoding="utf-8")
    assert re.search(r"^tier=full$", marker, flags=re.MULTILINE)
    manifest = json.loads((skill / "install-manifest.json").read_text(encoding="utf-8"))
    assert manifest["prompt_tier"] == "full"

    run_body = (home / ".claude" / "commands" / "spec" / "run.md").read_text(encoding="utf-8")
    assert FULL_RUN_AUTONOMY in run_body
    assert "built-in one-shot" in run_body


@requires_bash
def test_invalid_prompt_tier_fails_before_install(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, {"SPEC_PROMPT_TIER": "bogus"})
    assert result.returncode != 0
    assert "unknown SPEC_PROMPT_TIER: bogus" in result.stderr
    assert "expected full, compact, or aggressive" in result.stderr
    assert not (home / ".claude").exists()

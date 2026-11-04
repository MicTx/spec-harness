import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from export_skill_package import _collect_script_files  # noqa: E402

REQUIRED = {"spec_context.py", "session_context_support.py", "session_adapters.py"}


def _fingerprint(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = path.read_bytes()
    return out


def test_recovery_scripts_ship_in_collector():
    assert REQUIRED <= set(_collect_script_files())


def test_recovery_contract_text_present():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    handoff = (ROOT / "references" / "handoff.md").read_text(encoding="utf-8")
    commands = (ROOT / "references" / "commands.md").read_text(encoding="utf-8")
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "spec_context.py discover" in skill
    assert "## Native session recovery" in handoff
    assert "spec_context.py read" in handoff
    assert "--client" in handoff
    assert "native session recovery" in commands
    assert "native session recovery contract" in install


def test_recovery_precedes_routing_and_selected_body_reads():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    handoff = (ROOT / "references" / "handoff.md").read_text(encoding="utf-8")
    assert skill.index("spec_context.py discover") < skill.index("scripts/route_spec_package.py")
    assert handoff.index("spec_context.py discover") < handoff.index("spec_context.py read")
    for stage in ("new", "run"):
        text = (ROOT / "references" / "stages" / f"{stage}.md").read_text(encoding="utf-8")
        assert "../handoff.md#native-session-recovery" in text


def test_status_and_skill_frontload_recovery_for_bare_continuation():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    status = (ROOT / "references" / "stages" / "status.md").read_text(encoding="utf-8")
    assert skill.index("spec_context.py discover") < skill.index("## When to use")
    assert status.index("spec_context.py discover") < status.index("Compatible invocation:")
    assert "archives/test failures are not a goal" in status


def test_history_prohibition_has_explicit_safe_route():
    handoff = (ROOT / "references" / "handoff.md").read_text(encoding="utf-8")
    assert "do not invoke discover/read" in handoff
    assert "--no-native-context" in handoff


def _seed_claude(home: Path, cwd: Path):
    enc = "".join(c if c.isalnum() or c in "_-" else "-" for c in str(cwd))
    bucket = home / ".claude" / "projects" / enc
    bucket.mkdir(parents=True)
    records = [
        {
            "type": "user",
            "cwd": str(cwd),
            "sessionId": "sid-1",
            "uuid": "u1",
            "timestamp": "2026-11-02T10:00:00Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "export alpha"}]},
        },
        {
            "type": "assistant",
            "cwd": str(cwd),
            "sessionId": "sid-1",
            "uuid": "u2",
            "parentUuid": "u1",
            "timestamp": "2026-11-02T10:01:00Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "alpha next step"}]},
        },
    ]
    with open(bucket / "sid-1.jsonl", "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _env(home: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@pytest.mark.parametrize("profile", ["core", "full"])
def test_exported_cli_recovers_context_in_isolated_target(tmp_path, profile):
    output = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "export_skill_package.py"),
            "--output",
            str(output),
            "--force",
            "--skip-signing",
            "--profile",
            profile,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-500:]
    candidates = list(output.rglob("spec_context.py"))
    assert len(candidates) == 1
    script = candidates[0]
    for name in REQUIRED:
        assert list(output.rglob(name)), name

    target = tmp_path / "target-proj"
    target.mkdir()
    (target / "app.txt").write_text("app")
    fake_home = tmp_path / "fake-home"
    _seed_claude(fake_home, target)
    before_target = _fingerprint(target)
    before_home = _fingerprint(fake_home)

    env = _env(fake_home)
    discovered = subprocess.run(
        [sys.executable, "-B", str(script), "discover", "--root", ".", "--home", str(fake_home), "--client", "devin"],
        cwd=target,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert discovered.returncode == 0, discovered.stderr[-500:]
    payload = json.loads(discovered.stdout)
    assert payload["candidates"]
    assert payload["candidates"][0]["agent"] == "claude"
    assert payload["schema_version"] == 1
    sid = payload["candidates"][0]["session_id"]

    read = subprocess.run(
        [
            sys.executable,
            "-B",
            str(script),
            "read",
            "--root",
            ".",
            "--home",
            str(fake_home),
            "--agent",
            "claude",
            "--session",
            sid,
        ],
        cwd=target,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert read.returncode == 0, read.stderr[-500:]
    body = json.loads(read.stdout)
    assert [m["text"] for m in body["messages"]] == ["export alpha", "alpha next step"]
    assert body["authority"] == "context-only"
    assert "untrusted" in " ".join(body["advisories"])

    assert _fingerprint(target) == before_target
    assert _fingerprint(fake_home) == before_home
    assert not (target / ".spec").exists()
    assert not list(output.rglob("__pycache__"))
    assert not list(target.rglob("__pycache__"))

"""End-to-end admission loop in an isolated HOME and project (task-integration-regression).

install (real bash install.sh) -> opt in once -> first business request ->
assessment -> package created by the coordinator -> scope grows after reading
code -> re-assessment -> existing teammates adopted without disturbing them ->
verification -> explicit exit. Events are real subprocess stdin/stdout calls to
the installed copy; the host itself is stubbed and is labelled so: this proves
the wiring and the safety properties, not that a model decides correctly.

The real home directory and the source checkout must see zero writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"

pytestmark = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0, reason="bash not available"
)


def sh_env(home):
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "BACKUP_ROOT": str(home / ".spec-skill-backups"),
        "SPEC_SKIP_SIGNING": "1",
        "INSTALL_HOSTS": "claude",
        **({"TMPDIR": os.environ["TMPDIR"]} if "TMPDIR" in os.environ else {}),
    }


#: Only the locations this feature could ever write. The rest of the real
#: ~/.claude (session transcripts, caches) is written continuously by live
#: sessions and says nothing about this test, so hashing it would be both noisy
#: and blind to the files that matter.
REAL_CONFIG_FILES = ("settings.json", "settings.local.json", "CLAUDE.md")
REAL_CONFIG_DIRS = ("skills/spec", "commands/spec", "hooks")


def real_config_state():
    base = Path.home() / ".claude"
    digest = hashlib.sha256()
    for name in REAL_CONFIG_FILES:
        path = base / name
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.is_file() else b"<absent>")
    for name in REAL_CONFIG_DIRS:
        root = base / name
        digest.update(name.encode())
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    digest.update(path.relative_to(base).as_posix().encode())
                    digest.update(path.read_bytes())
        else:
            digest.update(b"<absent>")
    return digest.hexdigest()


def tree_digest(root: Path, skip=(".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache")):
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if any(part in skip for part in path.parts) or not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def source_state():
    """Content digest of every tracked-or-untracked file e2e could plausibly touch.

    Comparing `git status` text would also flag the developer's own concurrent
    edits; hashing the runtime and state locations asks the real question: did
    this test write there?
    """
    watched = [ROOT / ".git" / "spec-activation", ROOT / ".claude", ROOT / ".spec" / "specs" / "archive"]
    digest = hashlib.sha256()
    for base in watched:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(ROOT).as_posix().encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    base = tmp_path_factory.mktemp("e2e")
    home = base / "home"
    home.mkdir()
    real_home_before = real_config_state()
    source_before = source_state()
    result = subprocess.run(
        ["bash", str(INSTALLER)], cwd=ROOT, capture_output=True, text=True, env=sh_env(home), timeout=300
    )
    assert result.returncode == 0, result.stderr
    skill = home / ".claude" / "skills" / "spec"
    yield {"home": home, "skill": skill, "base": base, "source_before": source_before, "real_before": real_home_before}


def hook(installed, payload, name="claude_activation_guard.py"):
    return subprocess.run(
        [sys.executable, str(installed["skill"] / "hooks" / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def assess(installed, project, facts, session="e2e"):
    return subprocess.run(
        [
            sys.executable,
            str(installed["skill"] / "scripts/assess_spec_activation.py"),
            "--root",
            str(project),
            "--session",
            session,
            "--facts",
            json.dumps(facts),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def make_project(base, name):
    project = base / name
    project.mkdir()
    git(project, "init", "-q", "-b", "feature/work")
    git(project, "config", "user.name", "E2E")
    git(project, "config", "user.email", "e2e@example.test")
    git(project, "config", "commit.gpgsign", "false")
    (project / "app.py").write_text("v1\n")
    git(project, "add", ".")
    git(project, "commit", "-qm", "init")
    return project


def pre_tool(project, tool="Edit", session="e2e", **extra):
    return {
        "session_id": session,
        "cwd": str(project),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(project / "app.py")},
        **extra,
    }


def test_install_ships_the_guard_but_wires_nothing(installed):
    skill, home = installed["skill"], installed["home"]
    for rel in (
        "hooks/claude_activation_guard.py",
        "scripts/assess_spec_activation.py",
        "scripts/install_activation_hooks.py",
    ):
        assert (skill / rel).is_file(), rel
    assert (skill / "agents/openai.yaml").is_file()
    assert not (skill / "agents/orchestrator.md").exists()
    assert not (home / ".claude" / "settings.json").exists()


def test_full_loop_from_first_request_to_verified_exit(installed):
    skill, home, base = installed["skill"], installed["home"], installed["base"]
    # 1. one-time opt-in writes exactly our two events and nothing else
    on = subprocess.run(
        [sys.executable, str(skill / "scripts/install_activation_hooks.py"), "--mode", "on", "--root", str(skill)],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        timeout=30,
    )
    assert on.returncode == 0, on.stderr
    settings = json.loads((home / ".claude" / "settings.json").read_text())
    assert set(settings["hooks"]) == {"UserPromptSubmit", "PreToolUse"}

    project = make_project(base, "proj")
    before_code = (project / "app.py").read_bytes()

    # 2. first business request: reminder injected, edit blocked until assessed
    reminder = json.loads(
        hook(
            installed,
            {
                "session_id": "e2e",
                "cwd": str(project),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "加筛选、加开关、加导出",
            },
        ).stdout
    )
    assert "assess_spec_activation.py" in reminder["hookSpecificOutput"]["additionalContext"]
    assert json.loads(hook(installed, pre_tool(project)).stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    # 3. the coordinator assesses: three acceptance surfaces on a clean feature branch -> create
    created = json.loads(assess(installed, project, {"version": 1, "acceptance_surfaces": 3, "implementers": 1}).stdout)
    assert created["decision"] == "create"
    assert hook(installed, pre_tool(project)).stdout.strip() == "", "a recorded create unlocks implementation"

    # 4. the coordinator (not the guard) creates the package
    package = subprocess.run(
        [
            sys.executable,
            str(skill / "scripts/init_spec_package.py"),
            "--root",
            str(project),
            "--slug",
            "2026-10-23_add-suite",
            "--title",
            "Suite",
            "--skip-git-branch",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert package.returncode == 0, package.stderr
    assert (project / ".spec/specs/2026-10-23_add-suite/spec.md").is_file()
    assert (project / "app.py").read_bytes() == before_code, "assessment and creation never touch business files"

    # 5. exit: turning it off removes only our entries
    off = subprocess.run(
        [sys.executable, str(skill / "scripts/install_activation_hooks.py"), "--mode", "off", "--root", str(skill)],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        timeout=30,
    )
    assert off.returncode == 0
    assert "hooks" not in json.loads((home / ".claude" / "settings.json").read_text())


def test_scope_growth_forces_a_fresh_decision(installed):
    project = make_project(installed["base"], "grow")
    assert (
        json.loads(
            assess(
                installed,
                project,
                {"version": 1, "acceptance_surfaces": 1, "implementers": 1},
            ).stdout
        )["decision"]
        == "direct"
    )
    assert hook(installed, pre_tool(project)).stdout.strip() == ""
    grown = json.loads(
        assess(
            installed,
            project,
            {"version": 1, "acceptance_surfaces": 3, "implementers": 1, "scope_grew": True},
        ).stdout
    )
    assert grown["decision"] == "create"


def test_existing_teammates_are_adopted_without_disturbing_them(installed):
    skill, base = installed["skill"], installed["base"]
    project = make_project(base, "team")
    (project / "app.py").write_text("teammate in progress\n")
    (project / "new_feature.py").write_text("x = 1\n")
    git(project, "add", "new_feature.py")
    bytes_before = {n: (project / n).read_bytes() for n in ("app.py", "new_feature.py")}
    head, branch, index = (
        git(project, "rev-parse", "HEAD"),
        git(project, "branch", "--show-current"),
        git(project, "diff", "--cached"),
    )
    adopted = subprocess.run(
        [
            sys.executable,
            str(skill / "scripts/init_spec_package.py"),
            "--root",
            str(project),
            "--slug",
            "2026-10-23_add-team-suite",
            "--title",
            "Team",
            "--adopt",
            "--confirm-adopt",
            "--teammate",
            "impl-1",
            "--teammate",
            "impl-2",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert adopted.returncode == 0, adopted.stderr
    assert {n: (project / n).read_bytes() for n in bytes_before} == bytes_before
    assert (
        git(project, "rev-parse", "HEAD"),
        git(project, "branch", "--show-current"),
        git(project, "diff", "--cached"),
    ) == (head, branch, index)
    # a worker event never asks to create or adopt, and is never gated
    assert hook(installed, pre_tool(project, teammate_name="impl-1")).stdout.strip() == ""
    # a bound package alone no longer satisfies the guard: the resume decision must be recorded first
    denied = json.loads(hook(installed, pre_tool(project, session="fresh")).stdout)["hookSpecificOutput"]
    assert denied["permissionDecision"] == "deny" and "--session" in denied["permissionDecisionReason"]
    resumed = json.loads(
        assess(
            installed,
            project,
            {"version": 1, "acceptance_surfaces": 2, "package_slug": "2026-10-23_add-team-suite"},
            session="fresh",
        ).stdout
    )
    assert resumed["decision"] == "resume"
    assert hook(installed, pre_tool(project, session="fresh")).stdout.strip() == ""


def test_an_explicit_opt_out_cannot_be_self_reported_to_unlock_work(installed):
    project = make_project(installed["base"], "optout")
    assert (
        json.loads(hook(installed, pre_tool(project, session="oo")).stdout)["hookSpecificOutput"]["permissionDecision"]
        == "deny"
    )
    refused = assess(
        installed,
        project,
        {"version": 1, "acceptance_surfaces": 3, "implementers": 3, "opt_out": True},
        session="oo",
    )
    assert refused.returncode == 2 and "opt_out" in refused.stderr
    still = json.loads(hook(installed, pre_tool(project, session="oo")).stdout)["hookSpecificOutput"]
    assert still["permissionDecision"] == "deny"
    assert "opt-out" in still["permissionDecisionReason"]


def test_hook_input_cannot_inject_commands_or_leak(installed):
    project = make_project(installed["base"], "inject")
    secret = "sk-live-" + "Q" * 24
    payload = pre_tool(project, session="$(touch /tmp/pwned-e2e)")
    payload["tool_input"] = {"command": f"echo {secret}; touch /tmp/pwned-e2e"}
    out = hook(installed, payload)
    assert secret not in out.stdout + out.stderr
    assert not Path("/tmp/pwned-e2e").exists()


def test_a_corrupt_or_oversized_event_never_locks_work(installed):
    make_project(installed["base"], "robust")
    for raw in ("not json", json.dumps({"hook_event_name": "PreToolUse", "x": "a" * 400000})):
        done = subprocess.run(
            [sys.executable, str(installed["skill"] / "hooks/claude_activation_guard.py")],
            input=raw,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert done.returncode == 0 and done.stdout.strip() == ""


def test_the_source_checkout_and_real_home_saw_zero_writes(installed):
    assert source_state() == installed["source_before"], "e2e wrote into the source checkout's runtime/state locations"
    assert real_config_state() == installed["real_before"], "real Claude config changed"

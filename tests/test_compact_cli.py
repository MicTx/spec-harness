"""Concise user output, explicit JSON, and the installed quick-run contract."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from tests.v3_support import SCRIPTS, git, repo

ROOT = Path(__file__).resolve().parents[1]


def run_installer(home):
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "INSTALL_HOSTS": "claude,pi"}
    if "TMPDIR" in os.environ:
        env["TMPDIR"] = os.environ["TMPDIR"]
    return subprocess.run(
        ["bash", str(ROOT / "install.sh")], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300
    )


def _invoke(root, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "spec.py"), "--root", str(root), *args],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_quick_start_and_delivery_are_two_concise_cli_calls(tmp_path):
    repo(tmp_path)
    git(tmp_path, "checkout", "main")
    args = ("quick", "Correct the value", "--done", "The value is 1", "--path", "app.py")
    start = _invoke(tmp_path, *args, "--verify", "grep -qx 'VALUE = 1' app.py")
    assert start.returncode == 0 and "Edit" in start.stdout
    (tmp_path / "app.py").write_text("VALUE = 1\n")
    check = _invoke(tmp_path, "check")
    assert check.returncode == 0 and "no commit" in check.stdout
    assert len(start.stdout.encode()) + len(check.stdout.encode()) < 512
    structured = _invoke(tmp_path, "next", "--json")
    assert json.loads(structured.stdout)["state"] == "delivered"
    assert "{" not in start.stdout and "{" not in check.stdout


def test_every_installed_prompt_routes_to_quick_and_keeps_a_bounded_protocol(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home)
    assert result.returncode == 0, result.stdout + result.stderr
    run = (home / ".claude" / "commands" / "spec" / "run.md").read_text()
    skill = (home / ".claude" / "skills" / "spec" / "SKILL.md").read_text()
    instructions = (home / ".claude" / "skills" / "spec" / "references" / "exec.md").read_text()
    pi = (home / ".pi" / "agent" / "prompts" / "spec:run.md").read_text()
    assert "quick" in run and "references/exec.md" in run
    assert "two-call" in skill and "quick" in instructions and "--verify" in instructions
    assert "installed skill" in pi and "references/exec.md" in pi
    surface = sum(len(text.encode()) for text in (run, skill, instructions.split("## Standard path")[0]))
    assert surface < 7000
    assert not re.search(r"--attempt|--retry|--restart", run)
    base = home / "test"
    base.mkdir()
    repo(base)
    git(base, "checkout", "main")
    script = home / ".claude" / "skills" / "spec" / "scripts" / "spec.py"
    args = ("quick", "Correct the value", "--path", "app.py", "--verify", "grep -qx 'VALUE = 1' app.py")
    quick = subprocess.run([sys.executable, str(script), *args], cwd=base, capture_output=True, text=True)
    assert quick.returncode == 0, quick.stderr
    (base / "app.py").write_text("VALUE = 1\n")
    checked = subprocess.run([sys.executable, str(script), "check"], cwd=base, capture_output=True, text=True)
    assert checked.returncode == 0 and "no commit" in checked.stdout, checked.stderr

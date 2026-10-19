"""F4 install/export acceptance matrix — sandbox four paths + export-tree capabilities.

Four representative sandbox paths (main evidence layer, doc 04 §2.2):

* fresh core (default profile, 5-stage command surface);
* fresh full (12-stage command surface);
* full -> full upgrade with injected stale files (reinstall restores the
  installer-owned surface; zero net change vs a clean full install);
* full -> core downgrade sweep (extension surface cleaned, core capabilities
  still callable).

Each path asserts install parity for its profile and that the internal read
capabilities (``route_spec_package.py --format json --brief`` and
``spec_verify.py show``) are callable on the installed tree with valid
envelopes. Export trees (core/full, ``--skip-signing``) must run from
relative paths with no repository dependency and answer the four measurement
surfaces of ``measure_skill_surface.py``.

Sandbox discipline follows ``tests/test_install_profile.py`` (hermetic HOME,
controlled PATH, ``SPEC_SKIP_SIGNING=1``, inherited INSTALL_* stripped).
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
EXPORTER = ROOT / "scripts" / "export_skill_package.py"
PARITY = ROOT / "scripts" / "verify_install_parity.py"

CORE_STAGES = ("new", "run", "check", "done", "push")
EXTENDED_STAGES = ("goal", "autoplan", "autorun", "update", "status", "doctor", "organize")
CORE_EXCLUDED_SCRIPTS = (
    "autorun_spawn.py",
    "autoplan_spawn.py",
    "autoplan_gate.py",
    "chain_spawn_support.py",
    "chain_recovery.py",
    "chain_lifecycle.py",
    "sweep_chain_windows.py",
)

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0 or not Path("/bin/bash").exists(),
    reason="bash not available",
)


def run_export(output: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(EXPORTER), "--output", str(output), "--force", "--skip-signing", *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def tree_manifest(root: Path, skip_names: frozenset = frozenset({".spec-skill-install"})) -> dict:
    manifest: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not any(part in ("__pycache__",) for part in path.parts):
            relative = path.relative_to(root).as_posix()
            if relative in skip_names or any(part in skip_names for part in path.parts):
                continue
            manifest[relative] = path.read_bytes()
    return manifest


def sandbox_env(home: Path, extra_env: dict | None = None) -> dict:
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "BACKUP_ROOT": str(home / ".spec-skill-backups"),
        "SPEC_SKIP_SIGNING": "1",
    }
    for var in (
        "INSTALL_HOSTS",
        "INSTALL_PROFILE",
        "SOURCE_DIR",
        "SPEC_SIGNING_IDENTITY",
        "SPEC_SIGNING_KEY",
        "FORCE",
        "INSTALL_DIR",
    ):
        env.pop(var, None)
    env.update({k: v for k, v in os.environ.items() if k in ("TMPDIR",)})
    if extra_env:
        env.update(extra_env)
    return env


def run_installer(home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
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


def claude_stage_commands(home: Path) -> list:
    commands_dir = home / ".claude" / "commands" / "spec"
    return sorted(path.name for path in commands_dir.glob("*.md")) if commands_dir.is_dir() else []

FIXTURE_SLUG = "2026-10-19-matrix-fixture"
FIXTURE_SPEC = """# 沙箱矩阵夹具包 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：安装/导出验收矩阵的合成夹具；非真实项目。
- **目标用户**：F4 验收测试。
- **核心价值**：无私有数据。

## 5. 技术决策
- 证据协议：evidence: runs（合成夹具：记录固定）

## 6. 成功标准与验证方式
- [ ] 标准 A：envelope 可调用 -> verify: manual: 合成夹具无独立验证命令
"""

FIXTURE_TASKS = """# 沙箱矩阵夹具包 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`

## 阶段一：合成
- [ ] 合成任务 A
  - boundary: 仅合成
  - verify: `python3 -c "print('matrix-a')"`
"""

FIXTURE_RUNS = json.dumps(
    {
        "task": "task_0001",
        "cmd": "python3 -c \"print('matrix-a')\"",
        "exit": 1,
        "duration_s": 0.1,
        "stdout_tail_sha256": "0" * 64,
        "tree_fp": "0" * 64,
        "at": "2026-10-19T00:00:00+00:00",
    },
    ensure_ascii=False,
)


def skill_dir(home: Path) -> Path:
    return home / ".claude" / "skills" / "spec"


def install_fixture_package(root: Path) -> Path:
    """Drop a synthetic v2 package under <root>/.spec/specs/ for capability calls."""
    package = root / ".spec" / "specs" / FIXTURE_SLUG
    package.mkdir(parents=True, exist_ok=True)
    (package / "spec.md").write_text(FIXTURE_SPEC, encoding="utf-8")
    (package / "tasks.md").write_text(FIXTURE_TASKS, encoding="utf-8")
    (package / "runs.jsonl").write_text(FIXTURE_RUNS + "\n", encoding="utf-8")
    return package


def run_parity(install: Path, profile: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PARITY), "--install", str(install), "--repo", str(ROOT), "--profile", profile],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )


def capability_envelopes(root: Path) -> dict:
    """brief (--slug) and show envelopes from the tree's own scripts, relative root."""
    brief = subprocess.run(
        [sys.executable, "scripts/route_spec_package.py", "--root", ".", "--format", "json", "--brief", "--slug", FIXTURE_SLUG],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert brief.returncode == 0, brief.stderr
    envelope = json.loads(brief.stdout)
    assert envelope["capability"] == "brief"
    assert envelope["slug"] == FIXTURE_SLUG
    assert envelope["source"]["spec_sha256"] == _digest(root / ".spec" / "specs" / FIXTURE_SLUG / "spec.md")
    assert envelope["source"]["tasks_sha256"] == _digest(root / ".spec" / "specs" / FIXTURE_SLUG / "tasks.md")
    assert envelope["complete"] is True

    show = subprocess.run(
        [sys.executable, "scripts/spec_verify.py", "show", "--root", ".", "--slug", FIXTURE_SLUG],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert show.returncode == 0, show.stderr
    show_envelope = json.loads(show.stdout)
    assert show_envelope["capability"] == "evidence"
    assert show_envelope["complete"] is True
    assert "freshness" in show_envelope
    return {"brief": envelope, "show": show_envelope}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Path 1: fresh core (default profile)
# ---------------------------------------------------------------------------


@requires_bash
def test_matrix_fresh_core_install(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home)
    assert result.returncode == 0, result.stderr
    assert "Install profile: core (default)" in result.stdout

    assert claude_stage_commands(home) == sorted(f"{stage}.md" for stage in CORE_STAGES)
    skill = skill_dir(home)
    for name in CORE_EXCLUDED_SCRIPTS:
        assert not (skill / "scripts" / name).exists(), name

    parity = run_parity(skill, "core")
    assert parity.returncode == 0, parity.stdout
    assert "PASS" in parity.stdout

    install_fixture_package(skill)
    envelopes = capability_envelopes(skill)
    assert envelopes["brief"]["tasks"], "brief envelope must carry task slices on a core install"


# ---------------------------------------------------------------------------
# Path 2: fresh full
# ---------------------------------------------------------------------------


@requires_bash
def test_matrix_fresh_full_install(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, {"INSTALL_PROFILE": "full"})
    assert result.returncode == 0, result.stderr

    assert claude_stage_commands(home) == sorted(f"{stage}.md" for stage in CORE_STAGES + EXTENDED_STAGES)
    skill = skill_dir(home)
    parity = run_parity(skill, "full")
    assert parity.returncode == 0, parity.stdout

    install_fixture_package(skill)
    capability_envelopes(skill)


# ---------------------------------------------------------------------------
# Path 3: full -> full upgrade with injected stale files
# ---------------------------------------------------------------------------


@requires_bash
def test_matrix_full_upgrade_restores_installer_owned_surface(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    reference = tmp_path / "reference-home"
    reference.mkdir()
    assert run_installer(home, {"INSTALL_PROFILE": "full"}).returncode == 0
    assert run_installer(reference, {"INSTALL_PROFILE": "full"}).returncode == 0

    # Inject stale artifacts: a drifted script, a stale extended template with
    # the installer marker (installer-owned), and a user sidecar without the
    # marker (must survive, warn-not-FAIL semantics).
    skill = skill_dir(home)
    stale_script = skill / "scripts" / "route_spec_package.py"
    stale_script.write_text("# drifted local edit\n", encoding="utf-8")
    prompts = home / ".claude" / "commands" / "spec"
    stale_template = prompts / "spec:status.md"
    stale_template.write_text('---\ndescription: "stale"\n---\nold\n', encoding="utf-8")
    user_sidecar = skill / "scripts" / "user_sidecar_helper.py"
    user_sidecar.write_text("# user-authored sidecar\n", encoding="utf-8")

    result = run_installer(home, {"INSTALL_PROFILE": "full"})
    assert result.returncode == 0, result.stderr

    # Reinstall is zero net change on the installer-owned surface vs a clean
    # full install (test_pi_install_contracts.py:220 semantics extended to the
    # whole skill tree). A user sidecar inside the skill dir is swept to the
    # backup root (recoverable, warn-not-FAIL), never silently deleted.
    expected = tree_manifest(skill_dir(reference))
    actual = tree_manifest(skill)
    assert "scripts/user_sidecar_helper.py" not in actual
    assert actual == expected
    backups = home / ".spec-skill-backups"
    swept = list(backups.rglob("user_sidecar_helper.py"))
    assert swept, "user sidecar must land in backups on reinstall"
    assert swept[0].read_text(encoding="utf-8").startswith("# user-authored sidecar")

    parity = run_parity(skill, "full")
    assert parity.returncode == 0, parity.stdout

    install_fixture_package(skill)
    capability_envelopes(skill)


# ---------------------------------------------------------------------------
# Path 4: full -> core downgrade sweep
# ---------------------------------------------------------------------------


@requires_bash
def test_matrix_core_downgrade_sweeps_and_keeps_capabilities(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_installer(home, {"INSTALL_PROFILE": "full"}).returncode == 0
    result = run_installer(home)
    assert result.returncode == 0, result.stderr
    assert "Install profile: core (default)" in result.stdout

    assert claude_stage_commands(home) == sorted(f"{stage}.md" for stage in CORE_STAGES)
    skill = skill_dir(home)
    for name in CORE_EXCLUDED_SCRIPTS:
        assert not (skill / "scripts" / name).exists(), name
    for excluded in ("agents", "server", "slots"):
        assert not (skill / excluded).exists(), excluded

    parity = run_parity(skill, "core")
    assert parity.returncode == 0, parity.stdout

    install_fixture_package(skill)
    capability_envelopes(skill)


# ---------------------------------------------------------------------------
# Export trees: relative-path runnability + measurement surfaces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["core", "full"])
def test_matrix_export_tree_runs_from_relative_paths(tmp_path, profile):
    export = tmp_path / f"{profile}-export"
    result = run_export(export, "--profile", profile)
    assert result.returncode == 0, result.stderr

    install_fixture_package(export)
    envelopes = capability_envelopes(export)
    assert envelopes["show"]["counts"]["total_matching"] >= 1
    assert json.loads(envelopes["show"]["items"][0]["raw"])["task"] == "task_0001"

    measure = subprocess.run(
        [sys.executable, "scripts/measure_skill_surface.py", "--root", ".", "--json"],
        cwd=str(export),
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert measure.returncode == 0, measure.stderr
    surfaces = {s["name"]: s["bytes"] for s in json.loads(measure.stdout)["surfaces"]}
    assert set(surfaces) == {"metadata", "skill", "stages", "templates"}
    assert all(isinstance(v, int) and v > 0 for v in surfaces.values())

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from doctor_spec_environment import (  # noqa: E402  # type: ignore
    CHANNEL_PROBE_TIMEOUT_SECONDS,
    CHANNEL_SPOT_CHECK_SCRIPTS,
    PROBE_IDS,
    REQUIRED_RUNTIME_FILES,
    Doctor,
    claude_commands_dir,
)

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")


def check_by_id(doctor: Doctor, check_id: str):
    for check in doctor.checks:
        if check.id == check_id:
            return check
    raise AssertionError(f"doctor did not run check: {check_id}")


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    """Redirect every home-derived host directory into a temp HOME."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in (
        "CLAUDE_SKILLS_DIR",
        "CLAUDE_COMMANDS_DIR",
        "CLAUDE_AGENTS_DIR",
        "CLAUDE_DESKTOP_SKILLS_DIR",
        "CODEX_SKILLS_DIR",
        "GEMINI_SKILLS_DIR",
        "GROK_SKILLS_DIR",
        "OPENCODE_SKILLS_DIR",
        "OPENCLAW_SKILLS_DIR",
        "HERMES_SKILLS_DIR",
        "HERMES_HOME",
        "PI_SKILLS_DIR",
        "PI_DIR",
        "PI_EXTENSIONS_DIR",
        "ZCODE_SKILLS_DIR",
        "XDG_CONFIG_HOME",
        "BACKUP_ROOT",
        "SPEC_HUD_BIN_DIR",
        "CLAUDE_SETTINGS_FILE",
    ):
        monkeypatch.delenv(var, raising=False)
    return home


def install_fake_skill(target: Path, *, marker: bool = True, version: str = "9.9.9") -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text("# spec fake\n", encoding="utf-8")
    (target / "pyproject.toml").write_text(f'[project]\nname = "spec"\nversion = "{version}"\n', encoding="utf-8")
    (target / "scripts").mkdir(parents=True, exist_ok=True)
    (target / "scripts" / "spec_package_support.py").write_text("# fixture\n", encoding="utf-8")
    if marker:
        (target / ".spec-skill-install").write_text(
            "installed_by=spec/install.sh\nsource=/nonexistent-source\n", encoding="utf-8"
        )


def run_doctor_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "doctor_spec_environment.py"), *args],
        capture_output=True,
        text=True,
        env=dict(os.environ),
    )


def test_probe_ids_registry_is_ordered_complete_and_unique():
    """PROBE_IDS is the declared truth source for the check catalog.

    Bidirectional consistency against every ``self.record("...")`` site in
    the doctor source, no duplicates, skill.channels included, and pinned to
    run() execution order (skill.drift right after skill.marker because its
    branch lives inside check_skill_marker).
    """
    source = (ROOT / "scripts" / "doctor_spec_environment.py").read_text(encoding="utf-8")
    recorded = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "record"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            recorded.add(node.args[0].value)

    assert len(PROBE_IDS) == len(set(PROBE_IDS)), "PROBE_IDS must not repeat an id"
    assert set(PROBE_IDS) == recorded, (
        f"PROBE_IDS and self.record() sites drifted apart: {sorted(set(PROBE_IDS) ^ recorded)}"
    )
    assert "skill.channels" in PROBE_IDS, "F6 probe must be in the registry"
    assert PROBE_IDS == (
        "python.runtime",
        "git.available",
        "skill.layout",
        "skill.scripts",
        "skill.channels",
        "skill.marker",
        "skill.drift",
        "hosts.installed",
        "claude.commands",
        "claude.agents",
        "zcode.symlink",
        "project.git",
        "project.spec",
        "project.hooks",
    ), "PROBE_IDS must follow run() execution order"


def test_check_catalog_is_stable_on_repo_root(tmp_path, isolated_home):
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    exit_code = doctor.run()
    ids = {check.id for check in doctor.checks}
    guaranteed = {
        "python.runtime",
        "git.available",
        "skill.layout",
        "skill.scripts",
        "skill.marker",
        "hosts.installed",
        "project.git",
        "project.spec",
        "project.hooks",
    }
    assert guaranteed <= ids
    optional = {"claude.commands", "claude.agents", "zcode.symlink", "skill.channels"}
    assert ids <= guaranteed | optional
    assert check_by_id(doctor, "python.runtime").status == "ok"
    assert check_by_id(doctor, "skill.layout").status == "ok"
    assert check_by_id(doctor, "skill.scripts").status == "ok"
    assert exit_code == 0


def test_layout_check_reports_missing_runtime_files(tmp_path):
    broken = tmp_path / "broken-skill"
    broken.mkdir()
    (broken / "SKILL.md").write_text("# empty\n", encoding="utf-8")
    doctor = Doctor(skill_root=broken, project_root=tmp_path)
    doctor.check_skill_layout()
    check = check_by_id(doctor, "skill.layout")
    assert check.status == "fail"
    assert "install.sh" in check.detail


def test_scripts_check_detects_broken_compile(tmp_path):
    broken = tmp_path / "broken-skill"
    scripts = broken / "scripts"
    scripts.mkdir(parents=True)
    (broken / "SKILL.md").write_text("# x\n", encoding="utf-8")
    (scripts / "bad.py").write_text("def (:\n", encoding="utf-8")
    doctor = Doctor(skill_root=broken, project_root=tmp_path)
    doctor.check_skill_scripts()
    check = check_by_id(doctor, "skill.scripts")
    assert check.status == "fail"
    assert "bad.py" in check.detail


def test_scripts_check_compiles_nested_slot_scripts(tmp_path):
    slot_script = tmp_path / "slots" / "team-loop" / "scripts" / "bad.py"
    slot_script.parent.mkdir(parents=True)
    slot_script.write_text("def (:\n", encoding="utf-8")
    doctor = Doctor(skill_root=tmp_path, project_root=tmp_path)
    doctor.check_skill_scripts()
    check = check_by_id(doctor, "skill.scripts")
    assert check.status == "fail"
    assert "bad.py" in check.detail


CHANNEL_OK_BODY = 'print("usage: spec fake")\n'


def write_channel_samples(skill_root: Path, bodies: dict) -> None:
    """Write channel-probe sample scripts; keys are CHANNEL_SPOT_CHECK_SCRIPTS members."""
    for name, body in bodies.items():
        path = skill_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def all_clean_bodies() -> dict:
    return {name: CHANNEL_OK_BODY for name in CHANNEL_SPOT_CHECK_SCRIPTS}


def test_channels_probe_ok_when_samples_are_clean(tmp_path):
    skill = tmp_path / "skill"
    write_channel_samples(skill, all_clean_bodies())
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_channels()
    check = check_by_id(doctor, "skill.channels")
    assert check.status == "ok"
    assert "5/5" in check.detail
    assert "stderr 干净" in check.detail


def test_channels_probe_fails_when_help_prints_only_stderr(tmp_path):
    skill = tmp_path / "skill"
    bodies = all_clean_bodies()
    bodies["scripts/route_spec_package.py"] = 'import sys\nsys.stderr.write("usage\\n")\n'
    write_channel_samples(skill, bodies)
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_channels()
    check = check_by_id(doctor, "skill.channels")
    assert check.status == "fail"
    assert "route_spec_package.py" in check.detail
    assert "stdout 为空" in check.detail
    assert "通道契约" in check.fix_note


def test_channels_probe_fails_on_silent_success(tmp_path):
    skill = tmp_path / "skill"
    bodies = all_clean_bodies()
    bodies["scripts/check_spec_package.py"] = "pass\n"
    write_channel_samples(skill, bodies)
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_channels()
    check = check_by_id(doctor, "skill.channels")
    assert check.status == "fail"
    assert "check_spec_package.py" in check.detail
    assert "契约输出缺位" in check.detail


def test_channels_probe_warns_on_stderr_bleed_during_success(tmp_path):
    skill = tmp_path / "skill"
    bodies = all_clean_bodies()
    bodies["scripts/slot_registry.py"] = 'import sys\nprint("usage: spec fake")\nsys.stderr.write("note\\n")\n'
    write_channel_samples(skill, bodies)
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_channels()
    check = check_by_id(doctor, "skill.channels")
    assert check.status == "warn"
    assert "slot_registry.py" in check.detail
    assert "stderr 非空" in check.detail


def test_channels_probe_warns_on_abnormal_exit_with_stderr_line(tmp_path):
    skill = tmp_path / "skill"
    bodies = all_clean_bodies()
    bodies["scripts/generate_changelog.py"] = 'import sys\nsys.stderr.write("boom\\n")\nsys.exit(3)\n'
    write_channel_samples(skill, bodies)
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_channels()
    check = check_by_id(doctor, "skill.channels")
    assert check.status == "warn"
    assert "退出码 3" in check.detail
    assert "boom" in check.detail


def test_channels_probe_warns_on_timeout(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    write_channel_samples(skill, all_clean_bodies())

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0] if args else "sample",
            timeout=CHANNEL_PROBE_TIMEOUT_SECONDS,
        )

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_channels()
    check = check_by_id(doctor, "skill.channels")
    assert check.status == "warn"
    assert "超时" in check.detail


def test_channels_probe_skips_single_missing_sample(tmp_path):
    skill = tmp_path / "skill"
    bodies = all_clean_bodies()
    del bodies["scripts/generate_changelog.py"]
    write_channel_samples(skill, bodies)
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_channels()
    check = check_by_id(doctor, "skill.channels")
    assert check.status == "ok"
    assert "4/5" in check.detail


def test_channels_probe_reports_info_when_all_samples_missing(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_channels()
    check = check_by_id(doctor, "skill.channels")
    assert check.status == "info"
    assert "skill.layout" in check.detail


def test_layout_rejects_invalid_slot_manifest(tmp_path):
    skill = tmp_path / "skill"
    shutil.copytree(ROOT / "slots", skill / "slots")
    for name in REQUIRED_RUNTIME_FILES:
        path = skill / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    (skill / "slots" / "workflow-runner" / "manifest.json").write_text("{}", encoding="utf-8")
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_layout()
    check = check_by_id(doctor, "skill.layout")
    assert check.status == "fail"
    assert "slot 资产无效" in check.detail


def test_marker_drift_against_source_checkout(tmp_path):
    source = tmp_path / "source"
    install = tmp_path / "install"
    for base in (source, install):
        base.mkdir()
        for name in REQUIRED_RUNTIME_FILES:
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"same")
    # One drifted file in the installed copy.
    (install / "SKILL.md").write_bytes(b"different")
    (install / ".spec-skill-install").write_text(f"installed_by=spec/install.sh\nsource={source}\n", encoding="utf-8")
    doctor = Doctor(skill_root=install, project_root=tmp_path)
    doctor.check_skill_marker()
    drift = check_by_id(doctor, "skill.drift")
    assert drift.status == "warn"
    assert "SKILL.md" in drift.detail


def test_hosts_installed_detects_foreign_and_partial(isolated_home):
    install_fake_skill(isolated_home / ".codex" / "skills" / "spec")
    install_fake_skill(isolated_home / ".gemini" / "skills" / "spec", marker=False)
    partial = isolated_home / ".grok" / "skills" / "spec"
    partial.mkdir(parents=True)
    (partial / "README").write_text("orphan", encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_hosts_installed()
    check = check_by_id(doctor, "hosts.installed")
    assert check.status == "warn"
    assert "Codex 9.9.9" in check.detail
    assert "外来同名 skill" in check.detail
    assert "残缺安装" in check.detail


def test_hosts_installed_warns_when_no_host_has_the_skill(isolated_home):
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_hosts_installed()
    check = check_by_id(doctor, "hosts.installed")
    assert check.status == "warn"
    assert "未在任何已知宿主目录发现" in check.detail


def test_claude_commands_skipped_when_host_unused(isolated_home):
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    assert doctor.check_claude_commands() is True
    assert doctor.checks == []


def test_claude_commands_skipped_when_host_filter_excludes_claude(isolated_home):
    # Same scoping rule as check_claude_agents: a --host that excludes claude
    # must not scan (or fail on) the claude side, even when it is installed.
    install_fake_skill(isolated_home / ".claude" / "skills" / "spec")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT, host_filter=["codex"])
    assert doctor.check_claude_commands() is None
    assert doctor.checks == []


def test_claude_commands_flags_missing_stage_files(isolated_home):
    install_fake_skill(isolated_home / ".claude" / "skills" / "spec")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_claude_commands()
    check = check_by_id(doctor, "claude.commands")
    assert check.status == "fail"
    assert "缺少阶段命令文件" in check.detail
    assert "check.md" in check.detail
    assert "doctor.md" in check.detail


def test_claude_commands_ok_when_all_stage_files_present(isolated_home):
    install_fake_skill(isolated_home / ".claude" / "skills" / "spec")
    from doctor_spec_environment import Doctor as _Doctor

    stages = _Doctor.USER_STAGES
    command_dir = claude_commands_dir() / "spec"
    command_dir.mkdir(parents=True)
    for stage in stages:
        (command_dir / f"{stage}.md").write_text(f"---\ndescription: d\n---\n# /spec:{stage}\n", encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_claude_commands()
    check = check_by_id(doctor, "claude.commands")
    assert check.status == "ok"
    assert "无旧版残留" in check.detail


def test_claude_commands_fix_removes_installer_owned_stale_layout(isolated_home):
    skills_dir = isolated_home / ".claude" / "skills"
    install_fake_skill(skills_dir / "spec")
    commands_dir = isolated_home / ".claude" / "commands"
    commands_dir.mkdir(parents=True)
    stale_command = commands_dir / "spec:check.md"
    stale_command.write_text("# stale\n", encoding="utf-8")
    (commands_dir / "spec:check.md.spec-skill-install").write_text(
        "installed_by=spec/install.sh\ntarget=spec\nstage=check\n", encoding="utf-8"
    )
    stale_alias = skills_dir / "spec:route"
    stale_alias.mkdir()
    (stale_alias / ".spec-skill-install").write_text(
        "installed_by=spec/install.sh\ntarget=spec\nstage=route\n", encoding="utf-8"
    )
    foreign_stale = commands_dir / "spec:done.md"
    foreign_stale.write_text("# foreign\n", encoding="utf-8")

    doctor = Doctor(skill_root=ROOT, project_root=ROOT, fix=True)
    doctor.check_claude_commands()
    check = check_by_id(doctor, "claude.commands")

    # Missing stage files remain (no bash reinstall path in this environment),
    # but installer-owned stale artifacts must be gone and backed up.
    assert not stale_command.exists()
    assert not stale_alias.exists()
    assert foreign_stale.exists()
    backups = list((isolated_home / ".spec-skill-backups").rglob("*check.md*"))
    assert backups, "stale removal must land under the backup root"
    assert any("spec" in path.name for path in backups)
    assert "已备份" in check.detail


def test_claude_commands_detects_retired_subdir_stages(isolated_home):
    install_fake_skill(isolated_home / ".claude" / "skills" / "spec")
    command_dir = claude_commands_dir() / "spec"
    command_dir.mkdir(parents=True)
    from doctor_spec_environment import Doctor as _Doctor

    for stage in _Doctor.USER_STAGES:
        (command_dir / f"{stage}.md").write_text(f"---\ndescription: d\n---\n# /spec:{stage}\n", encoding="utf-8")
    retired = command_dir / "combat.md"
    retired.write_text("# retired swarm command\n", encoding="utf-8")
    (command_dir / "combat.md.spec-skill-install").write_text(
        "installed_by=spec/install.sh\ntarget=spec\nstage=combat\n", encoding="utf-8"
    )

    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_claude_commands()
    check = check_by_id(doctor, "claude.commands")
    assert check.status == "warn"
    assert "旧版布局残留" in check.detail
    assert "combat.md" in check.detail


def test_claude_commands_fix_removes_retired_subdir_stages_with_sidecar(isolated_home):
    skills_dir = isolated_home / ".claude" / "skills"
    install_fake_skill(skills_dir / "spec")
    command_dir = claude_commands_dir() / "spec"
    command_dir.mkdir(parents=True)
    from doctor_spec_environment import Doctor as _Doctor

    for stage in _Doctor.USER_STAGES:
        (command_dir / f"{stage}.md").write_text(f"---\ndescription: d\n---\n# /spec:{stage}\n", encoding="utf-8")
    retired_files = []
    for stage in ("assemble", "combat", "marshal"):
        retired = command_dir / f"{stage}.md"
        retired.write_text(f"# retired {stage}\n", encoding="utf-8")
        sidecar = retired.with_name(retired.name + ".spec-skill-install")
        sidecar.write_text("installed_by=spec/install.sh\ntarget=spec\nstage=" + stage + "\n", encoding="utf-8")
        retired_files.append((retired, sidecar))
    foreign_retired = command_dir / "route.md"
    foreign_retired.write_text("# user authored\n", encoding="utf-8")

    doctor = Doctor(skill_root=ROOT, project_root=ROOT, fix=True)
    doctor.check_claude_commands()
    check = check_by_id(doctor, "claude.commands")

    for retired, sidecar in retired_files:
        assert not retired.exists(), retired
        assert not sidecar.exists(), sidecar
    assert foreign_retired.exists(), "installer-owned sweep must not touch user files"
    backups = list((isolated_home / ".spec-skill-backups").rglob("combat.md*"))
    assert any(path.name == "combat.md" for path in backups), "command file must be backed up"
    assert any(path.name == "combat.md.spec-skill-install" for path in backups), (
        "sidecar marker must be backed up together with the command file"
    )
    assert check.status == "fixed"
    assert "清除残留" in check.detail


def test_retired_stage_lists_stay_in_sync():
    """doctor and install.sh must agree on which subdir stages are retired."""
    from doctor_spec_environment import STALE_SUBDIR_STAGES

    installer = ROOT / "install.sh"
    declared: list[str] = []
    in_block = False
    for line in installer.read_text(encoding="utf-8").splitlines():
        if not in_block and line.startswith("STALE_CLAUDE_SUBDIR_STAGES=("):
            payload = line[len("STALE_CLAUDE_SUBDIR_STAGES=(") :]
            if payload.endswith(")"):
                declared.extend(payload[:-1].split())
                break
            declared.extend(payload.split())
            in_block = True
            continue
        if in_block:
            token = line.strip()
            if token.endswith(")"):
                declared.extend(token[:-1].split())
                break
            declared.extend(token.split())
    assert tuple(declared) == STALE_SUBDIR_STAGES
    live_stages = set(Doctor.USER_STAGES)
    assert not (set(STALE_SUBDIR_STAGES) & live_stages), "retired stages must not come back as live stages"


def test_zcode_dangling_symlink_fails(isolated_home):
    zcode_skills = isolated_home / ".zcode" / "skills"
    zcode_skills.mkdir(parents=True)
    (zcode_skills / "spec").symlink_to(isolated_home / ".claude" / "skills" / "spec")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_zcode_symlink()
    check = check_by_id(doctor, "zcode.symlink")
    assert check.status == "fail"
    assert "悬空" in check.detail


def test_zcode_valid_symlink_is_info(isolated_home):
    target = isolated_home / ".claude" / "skills" / "spec"
    install_fake_skill(target)
    zcode_skills = isolated_home / ".zcode" / "skills"
    zcode_skills.mkdir(parents=True)
    (zcode_skills / "spec").symlink_to(target)
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_zcode_symlink()
    check = check_by_id(doctor, "zcode.symlink")
    assert check.status == "info"


def test_project_git_warns_outside_work_tree(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    doctor = Doctor(skill_root=ROOT, project_root=plain)
    doctor.check_project_git()
    check = check_by_id(doctor, "project.git")
    assert check.status == "warn"
    assert "不是 Git 工作树" in check.detail


@requires_git
def test_project_git_ok_inside_work_tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_git()
    check = check_by_id(doctor, "project.git")
    assert check.status == "ok"


def test_project_spec_fix_creates_missing_dirs(tmp_path):
    project = tmp_path / "project"
    (project / ".spec" / "specs").mkdir(parents=True)
    doctor = Doctor(skill_root=ROOT, project_root=project, fix=True)
    doctor.check_project_spec()
    check = check_by_id(doctor, "project.spec")
    assert check.status == "fixed"
    assert (project / ".spec" / "specs" / "archive").is_dir()


def test_project_spec_reports_structure_without_fix(tmp_path):
    project = tmp_path / "project"
    (project / ".spec").mkdir(parents=True)
    doctor = Doctor(skill_root=ROOT, project_root=project)
    doctor.check_project_spec()
    check = check_by_id(doctor, "project.spec")
    assert check.status == "warn"
    assert "缺少目录" in check.detail


def test_project_spec_info_when_fresh(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    doctor = Doctor(skill_root=ROOT, project_root=project)
    doctor.check_project_spec()
    check = check_by_id(doctor, "project.spec")
    assert check.status == "info"


@requires_git
def test_project_hooks_detects_stale_rendered_pointer(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    stale = repo / ".git" / "hooks" / "pre-commit"
    # Rendered pointer to a location that no longer exists.
    stale.write_text(
        '#!/bin/sh\nchecker="/gone/skills/spec/scripts/check_all_spec_packages.py"\n'
        "# fingerprint: SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER template\n",
        encoding="utf-8",
    )
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks(repo.resolve())
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "fail"
    assert "指向不存在" in check.detail
    assert not doctor.fix


@requires_git
def test_project_hooks_fix_reinstalls_pointer(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    hooks_dir = repo / ".git" / "hooks"
    stale = hooks_dir / "pre-commit"
    stale.write_text(
        '#!/bin/sh\nchecker="/gone/skills/spec/scripts/check_all_spec_packages.py"\n'
        "# fingerprint: SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER template\n",
        encoding="utf-8",
    )
    doctor = Doctor(skill_root=ROOT, project_root=repo, fix=True)
    doctor.check_project_hooks(repo.resolve())
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "fixed"
    rendered = (hooks_dir / "pre-commit").read_text(encoding="utf-8")
    assert str(ROOT / "scripts" / "check_all_spec_packages.py") in rendered


@requires_git
def test_project_hooks_unrendered_template_fails(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    hooks_dir = repo / ".git" / "hooks"
    (hooks_dir / "pre-push").write_text(
        '#!/bin/sh\nchecker="__SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER__"\n', encoding="utf-8"
    )
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks(repo.resolve())
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "fail"
    assert "模板未渲染" in check.detail


@requires_git
def test_project_hooks_foreign_hook_is_info(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\necho project-owned\n", encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks(repo.resolve())
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "info"
    assert "项目自有" in check.detail


def test_doctor_cli_json_contract(isolated_home, tmp_path):
    completed = run_doctor_cli("--root", str(tmp_path), "--format", "json")
    assert completed.returncode in (0, 1)
    payload = json.loads(completed.stdout)
    assert payload["skillVersion"] not in ("", "unknown")
    assert payload["skillVersion"] == Doctor(skill_root=ROOT, project_root=ROOT).skill_version()
    assert {"id", "title", "status", "detail", "fixNote"} == set(payload["checks"][0].keys())
    assert set(payload["summary"].keys()) == {"fail", "warn", "fixed", "ok", "info"}
    assert payload["exitCode"] == completed.returncode


def test_doctor_cli_rejects_unknown_host(isolated_home, tmp_path):
    completed = run_doctor_cli("--root", str(tmp_path), "--host", "not-a-host")
    assert completed.returncode == 2
    assert "unknown --host" in completed.stderr


def test_doctor_cli_rejects_bad_roots(isolated_home, tmp_path):
    completed = run_doctor_cli("--root", str(tmp_path / "missing"))
    assert completed.returncode == 2
    completed = run_doctor_cli("--skill-root", str(tmp_path / "missing"))
    assert completed.returncode == 2


@requires_bash
def test_doctor_cli_end_to_end_reinstall_claude_commands(isolated_home, tmp_path):
    """Full loop: broken claude mount -> --fix reruns install.sh -> stage files exist."""
    skills_dir = isolated_home / ".claude" / "skills"
    install_fake_skill(skills_dir / "spec")
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "doctor_spec_environment.py"),
            "--root",
            str(tmp_path),
            "--skill-root",
            str(ROOT),
            "--fix",
        ],
        capture_output=True,
        text=True,
        env=dict(os.environ),
    )
    assert "claude.commands" in completed.stdout
    assert (claude_commands_dir() / "spec" / "doctor.md").is_file()
    assert (claude_commands_dir() / "spec" / "check.md").is_file()
    # The reinstall replaced the fake install with the real runtime.
    assert (skills_dir / "spec" / "scripts" / "doctor_spec_environment.py").is_file()


ORCHESTRATION_RUNTIME_FILES = (
    "scripts/route_decision.py",
    "references/orchestration.md",
    "agents/orchestrator.md",
    "agents/planner.md",
)


def test_doctor_requires_orchestration_runtime_files():
    missing = [name for name in ORCHESTRATION_RUNTIME_FILES if name not in REQUIRED_RUNTIME_FILES]
    assert missing == [], f"doctor runtime catalog omitted orchestration assets: {missing}"


def test_claude_agents_skipped_when_host_unused(isolated_home):
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_claude_agents()
    assert all(check.id != "claude.agents" for check in doctor.checks)


def test_claude_agents_fail_when_native_files_missing(isolated_home):
    install_fake_skill(isolated_home / ".claude" / "skills" / "spec")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_claude_agents()
    check = check_by_id(doctor, "claude.agents")
    assert check.status == "fail"
    assert "orchestrator.md" in check.detail
    assert "planner.md" in check.detail


def test_claude_agents_fail_when_new_contract_files_missing(isolated_home):
    install_fake_skill(isolated_home / ".claude" / "skills" / "spec")
    agents_dir = isolated_home / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    for name in ("orchestrator", "planner"):
        source = ROOT / "agents" / f"{name}.md"
        target = agents_dir / f"{name}.md"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        target.with_name(target.name + ".spec-skill-install").write_text(
            "installed_by=spec/install.sh\nsource=agents\n", encoding="utf-8"
        )
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_claude_agents()
    check = check_by_id(doctor, "claude.agents")
    assert check.status == "fail"
    assert "reviewer.md" in check.detail
    assert "confirmer.md" in check.detail


def test_claude_agents_ok_when_installer_owned_and_in_sync(isolated_home):
    install_fake_skill(isolated_home / ".claude" / "skills" / "spec")
    agents_dir = isolated_home / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    for name in ("orchestrator", "planner", "reviewer", "confirmer"):
        source = ROOT / "agents" / f"{name}.md"
        target = agents_dir / f"{name}.md"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        target.with_name(target.name + ".spec-skill-install").write_text(
            "installed_by=spec/install.sh\nsource=agents\n", encoding="utf-8"
        )
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_claude_agents()
    check = check_by_id(doctor, "claude.agents")
    assert check.status == "ok"


def test_claude_agents_ignore_leftover_user_agents_without_spec_skill(isolated_home):
    agents_dir = isolated_home / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "planner.md").write_text("# user's planner\n", encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_claude_agents()
    assert all(check.id != "claude.agents" for check in doctor.checks)


def test_claude_agents_skip_when_host_filter_excludes_claude(isolated_home):
    install_fake_skill(isolated_home / ".claude" / "skills" / "spec")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT, host_filter=["codex"])
    doctor.check_claude_agents()
    assert all(check.id != "claude.agents" for check in doctor.checks)


def test_claude_agents_skip_foreign_skill_like_commands(isolated_home):
    install_fake_skill(isolated_home / ".claude" / "skills" / "spec", marker=False)
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_claude_agents()
    check = check_by_id(doctor, "claude.agents")
    assert check.status == "info"
    assert "外来同名 skill" in check.detail


def test_claude_agents_fail_on_drift(isolated_home):
    install_fake_skill(isolated_home / ".claude" / "skills" / "spec")
    agents_dir = isolated_home / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    for name in ("orchestrator", "planner", "reviewer", "confirmer"):
        target = agents_dir / f"{name}.md"
        target.write_text("# drifted\n", encoding="utf-8")
        target.with_name(target.name + ".spec-skill-install").write_text(
            "installed_by=spec/install.sh\nsource=agents\n", encoding="utf-8"
        )
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    doctor.check_claude_agents()
    check = check_by_id(doctor, "claude.agents")
    assert check.status == "fail"
    assert "漂移" in check.detail


@pytest.mark.parametrize("missing", ["scripts/spec_package_support.py", "SKILL.md", "hooks/pre-commit"])
def test_doctor_requires_runtime_files(tmp_path, missing):
    assert missing in REQUIRED_RUNTIME_FILES
    skill = tmp_path / "skill"
    for name in REQUIRED_RUNTIME_FILES:
        path = skill / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    (skill / missing).unlink()
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_layout()
    check = check_by_id(doctor, "skill.layout")
    assert check.status == "fail"
    assert missing in check.detail

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
    EXTENSION_FILE_PREFIXES,
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
        "DEVIN_SKILLS_DIR",
        "CURSOR_SKILLS_DIR",
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
        "PI_PROMPTS_DIR",
        "AGENTS_SKILLS_DIR",
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
        "pi.prompts",
        "migration.wrappers",
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
    optional = {
        "claude.commands",
        "claude.agents",
        "pi.prompts",
        "migration.wrappers",
        "zcode.symlink",
        "skill.channels",
    }
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


@requires_git
def test_project_hooks_rendered_installer_hook_is_managed(tmp_path):
    """Rendering removes the fingerprint, so a real install must still be owned.

    install_git_hooks.py substitutes the template placeholder with the checker
    path; the fingerprint therefore never survives into .git/hooks. Detection
    that keys on the fingerprint alone reports a freshly installed gate as a
    project-owned hook.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    checker = ROOT / "scripts" / "check_all_spec_packages.py"
    (hooks_dir / "pre-commit").write_text(
        f'#!/bin/sh\nchecker="{checker}"\nexec "$checker" --root .\n', encoding="utf-8"
    )
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks(repo.resolve())
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "ok"
    assert "指向当前 skill" in check.detail


@requires_git
def test_project_hooks_foreign_checker_is_info(tmp_path):
    """A checker= line alone is not enough to claim a project-owned hook."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-commit").write_text('#!/bin/sh\nchecker="/usr/local/bin/team-lint"\n', encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks(repo.resolve())
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "info"
    assert "项目自有" in check.detail


@requires_git
def test_project_hooks_checker_name_substring_is_info(tmp_path):
    """Only an exact checker basename claims the hook, not a substring."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-commit").write_text(
        '#!/bin/sh\nchecker="/opt/team/my_check_all_spec_packages.py.sh"\n', encoding="utf-8"
    )
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks(repo.resolve())
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "info"


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
    # Doctor's command catalog still describes the full 12-stage surface
    # (profile-aware doctor lands with F16 sub-step 2), so its end-to-end
    # repair pins INSTALL_PROFILE=full; core is the install default.
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
        env=dict(os.environ, INSTALL_PROFILE="full"),
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


# ------------------------------------------------------------- pi.prompts


def write_pi_template(prompts_dir: Path, name: str, *, marker: bool = True) -> Path:
    """A well-formed installer template: quoted frontmatter + literal $@."""
    from doctor_spec_environment import pi_prompts_dir

    prompts_dir = prompts_dir or pi_prompts_dir()
    prompts_dir.mkdir(parents=True, exist_ok=True)
    target = prompts_dir / name
    if name == "spec.md":
        body = (
            "---\n"
            'description: "Spec: resolve the next action"\n'
            'argument-hint: "[query]"\n'
            "---\n"
            "Use the installed skill `spec` for this request. Follow the stage\n"
            "index in the skill's references/commands.md.\n"
            "Forward user arguments unchanged: $@\n"
        )
    else:
        stage = name[len("spec:") : -len(".md")]
        body = (
            "---\n"
            f'description: "Spec: {stage}"\n'
            'argument-hint: "[stage arguments]"\n'
            "---\n"
            f"Use the installed skill `spec` for this request. Follow the /spec:{stage}\n"
            "stage contract in the skill's references/stages/ (index: references/commands.md).\n"
            "Forward user arguments unchanged: $@\n"
        )
    target.write_text(body, encoding="utf-8")
    if marker:
        Path(f"{target}.spec-skill-install").write_text("installed_by=spec/install.sh\ntarget=spec\n", encoding="utf-8")
    return target


def test_pi_prompts_skipped_when_host_unused(isolated_home):
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    assert doctor.check_pi_prompts() is True
    assert doctor.checks == []


def test_pi_prompts_skipped_when_host_filter_excludes_pi(isolated_home):
    install_fake_skill(isolated_home / ".pi" / "agent" / "skills" / "spec")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT, host_filter=["claude"])
    assert doctor.check_pi_prompts() is None
    assert doctor.checks == []


def test_pi_prompts_ok_when_full_set_present(isolated_home):
    from doctor_spec_environment import pi_prompts_dir

    install_fake_skill(isolated_home / ".pi" / "agent" / "skills" / "spec")
    prompts = pi_prompts_dir()
    write_pi_template(prompts, "spec.md")
    for stage in Doctor.USER_STAGES:
        write_pi_template(prompts, f"spec:{stage}.md")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    assert doctor.check_pi_prompts() is True
    check = check_by_id(doctor, "pi.prompts")
    assert check.status == "ok"
    assert f"{len(Doctor.USER_STAGES) + 1} 个模板" in check.detail


def test_pi_prompts_fail_when_templates_missing(isolated_home):
    install_fake_skill(isolated_home / ".pi" / "agent" / "skills" / "spec")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    assert doctor.check_pi_prompts() is False
    check = check_by_id(doctor, "pi.prompts")
    assert check.status == "fail"
    assert "缺少模板" in check.detail
    assert "spec:check.md" in check.detail
    assert "spec.md" in check.detail


def test_pi_prompts_warn_on_shape_and_marker_drift(isolated_home):
    from doctor_spec_environment import pi_prompts_dir

    install_fake_skill(isolated_home / ".pi" / "agent" / "skills" / "spec")
    prompts = pi_prompts_dir()
    write_pi_template(prompts, "spec.md")
    for stage in Doctor.USER_STAGES:
        write_pi_template(prompts, f"spec:{stage}.md")
    # Unquoted description, no argument-hint, no literal $@, no index
    # pointer, no marker: every shape rule fires without blocking (the file
    # exists and is non-empty, so this is drift, not a missing template).
    broken = prompts / "spec:new.md"
    broken.write_text(
        "---\ndescription: Spec: create a task package\n---\n# /spec:new\nRun the workflow.\n",
        encoding="utf-8",
    )
    Path(f"{broken}.spec-skill-install").unlink()
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    assert doctor.check_pi_prompts() is False
    check = check_by_id(doctor, "pi.prompts")
    assert check.status == "warn"
    assert "未加引号" in check.detail
    assert "argument-hint 缺失" in check.detail
    assert "参数透传 $@" in check.detail
    assert "未指向阶段契约索引" in check.detail
    assert "缺少安装器 marker" in check.detail


def test_pi_prompts_flags_installer_owned_leftovers_not_user_files(isolated_home):
    from doctor_spec_environment import pi_prompts_dir

    skill_dir = isolated_home / ".pi" / "agent" / "skills" / "spec"
    install_fake_skill(skill_dir)
    # core-profile marker: the expected set shrinks to CORE stages.
    (skill_dir / ".spec-skill-install").write_text(
        "installed_by=spec/install.sh\nsource=/nonexistent-source\nprofile=core\n", encoding="utf-8"
    )
    prompts = pi_prompts_dir()
    write_pi_template(prompts, "spec.md")
    # Profile expectations follow the RUNNING skill copy's marker (same rule
    # as check_claude_commands), so bind the doctor to the core fixture.
    doctor = Doctor(skill_root=skill_dir, project_root=isolated_home)
    for stage in doctor.user_stages():
        write_pi_template(prompts, f"spec:{stage}.md")
    # Installer-owned leftover beyond the core set (marker sidecar present).
    write_pi_template(prompts, "spec:organize.md")
    # User-authored extended-stage file without a marker: never flagged.
    write_pi_template(prompts, "spec:goal.md", marker=False)
    assert doctor.check_pi_prompts() is False
    check = check_by_id(doctor, "pi.prompts")
    assert check.status == "warn"
    assert "阶段集外安装器残留模板：spec:organize.md" in check.detail
    assert "spec:goal.md" not in check.detail


# ------------------------------------------------------- migration.wrappers


def test_migration_wrappers_silent_when_none_present(isolated_home):
    (isolated_home / ".agents" / "skills").mkdir(parents=True)
    doctor = Doctor(skill_root=ROOT, project_root=ROOT)
    assert doctor.check_migration_wrappers() is True
    assert doctor.checks == []


def test_migration_wrappers_reports_fingerprints_and_never_removes(isolated_home):
    from doctor_spec_environment import agents_skills_dir

    base = agents_skills_dir()
    for stage in ("autoplan", "run"):
        wrapper = base / f"source-command-spec-{stage}"
        wrapper.mkdir(parents=True)
        (wrapper / "SKILL.md").write_text(
            f'---\nname: "source-command-spec-{stage}"\n'
            f'description: "Migrated source command `spec-{stage}`"\n---\n# wrapper\n',
            encoding="utf-8",
        )
    alien = base / "source-command-spec-alien"
    alien.mkdir()
    (alien / "SKILL.md").write_text("# not a migrated spec wrapper\n", encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=ROOT, fix=True)
    assert doctor.check_migration_wrappers() is False
    check = check_by_id(doctor, "migration.wrappers")
    assert check.status == "warn"
    assert "source-command-spec-autoplan" in check.detail
    assert "指纹不匹配" in check.detail
    assert "alien" in check.detail
    assert "只报告不删除" in check.fix_note
    # Report-only holds even with --fix: nothing was touched.
    assert alien.exists()
    assert (base / "source-command-spec-run").exists()


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


@requires_git
def test_project_hooks_missing_hooks_is_warn(tmp_path):
    """AUD-07: a repository without the disk-truth hooks loses commit/push
    gate enforcement silently, so the finding is a warning, not an info."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)

    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks(repo.resolve())

    check = check_by_id(doctor, "project.hooks")
    assert check.status == "warn"
    assert "未安装磁盘真源 hooks" in check.detail


# ---------------------------------------------------------------------------
# Install-profile awareness (planning F16 sub-step 2, AUD-18)
# ---------------------------------------------------------------------------


def _write_runtime_face(skill: Path, files) -> None:
    for name in files:
        path = skill / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")


def _core_face_files() -> list:
    return [name for name in REQUIRED_RUNTIME_FILES if not name.startswith(EXTENSION_FILE_PREFIXES)]


def test_install_profile_defaults_to_full_without_stamp(tmp_path):
    """Pre-split markers (no profile=) audit as full — existing installs unchanged."""
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / ".spec-skill-install").write_text(
        "installed_by=spec/install.sh\nsource=/nonexistent\nversion=1.0.0\n", encoding="utf-8"
    )
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    assert doctor.install_profile() == "full"
    assert doctor.required_runtime_files() == REQUIRED_RUNTIME_FILES
    assert doctor.user_stages() == list(Doctor.USER_STAGES)


def test_core_marker_selects_core_audit_face(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / ".spec-skill-install").write_text(
        "installed_by=spec/install.sh\nsource=/nonexistent\nversion=1.0.0\nprofile=core\n", encoding="utf-8"
    )
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    assert doctor.install_profile() == "core"
    face = doctor.required_runtime_files()
    assert face == tuple(_core_face_files())
    assert not any(name.startswith(("agents/", "server/")) for name in face)
    assert doctor.user_stages() == ["new", "run", "check", "done", "push"]

    # A bogus stamp never narrows the audit face silently.
    (skill / ".spec-skill-install").write_text("installed_by=spec/install.sh\nprofile=bogus\n", encoding="utf-8")
    assert Doctor(skill_root=skill, project_root=tmp_path).install_profile() == "full"


def test_layout_core_face_passes_without_extension_components(tmp_path):
    """The closed followup from 2026-10-13_add-install-profile: a core install
    must not be flagged for missing agents/server/slots."""
    skill = tmp_path / "skill"
    _write_runtime_face(skill, _core_face_files())
    (skill / ".spec-skill-install").write_text(
        "installed_by=spec/install.sh\nsource=/nonexistent\nprofile=core\n", encoding="utf-8"
    )
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_layout()
    check = check_by_id(doctor, "skill.layout")
    assert check.status == "ok"
    assert "profile=core" in check.detail

    # The same tree without the core stamp still audits as full and fails.
    (skill / ".spec-skill-install").write_text("installed_by=spec/install.sh\nsource=/nonexistent\n", encoding="utf-8")
    full_doctor = Doctor(skill_root=skill, project_root=tmp_path)
    full_doctor.check_skill_layout()
    full_check = check_by_id(full_doctor, "skill.layout")
    assert full_check.status == "fail"
    assert "agents/" in full_check.detail or "server/" in full_check.detail


def test_claude_agents_skipped_under_core(isolated_home, monkeypatch):
    skill = isolated_home / ".claude" / "skills" / "spec"
    _write_runtime_face(skill, _core_face_files())
    (skill / ".spec-skill-install").write_text(
        "installed_by=spec/install.sh\nsource=/nonexistent\nprofile=core\n", encoding="utf-8"
    )
    doctor = Doctor(skill_root=skill, project_root=isolated_home)
    doctor.check_claude_agents()
    assert all(check.id != "claude.agents" for check in doctor.checks)


def test_reinstall_claude_host_preserves_install_profile(isolated_home, monkeypatch):
    """A markerless reinstall would downgrade a full install to the core
    default; the repair path must forward the audited profile."""
    skill = isolated_home / ".claude" / "skills" / "spec"
    _write_runtime_face(skill, _core_face_files())
    (skill / ".spec-skill-install").write_text(
        "installed_by=spec/install.sh\nsource=/nonexistent\nprofile=full\n", encoding="utf-8"
    )
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        captured.update(kwargs.get("env") or {})
        return FakeCompleted()

    monkeypatch.setattr("subprocess.run", fake_run)
    doctor = Doctor(skill_root=skill, project_root=isolated_home)
    doctor._reinstall_claude_host()  # fixture tree: outcome irrelevant, env is the assertion
    assert captured.get("INSTALL_PROFILE") == "full"


def test_full_profile_layout_fails_when_chain_session_missing(tmp_path):
    skill = tmp_path / "skill"
    for name in REQUIRED_RUNTIME_FILES:
        if name == "scripts/chain_session.py":
            continue
        path = skill / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    for slot in ("team-loop", "workflow-runner"):
        source = ROOT / "slots" / slot
        if source.is_dir():
            shutil.copytree(source, skill / "slots" / slot)
        else:
            manifest = skill / "slots" / slot / "manifest.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text("{}\n", encoding="utf-8")
    (skill / ".spec-skill-install").write_text(
        "installed_by=spec/install.sh\nsource=/nonexistent\nprofile=full\n", encoding="utf-8"
    )
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    doctor.check_skill_layout()
    check = check_by_id(doctor, "skill.layout")
    assert check.status == "fail"
    assert "scripts/chain_session.py" in check.detail


def test_core_profile_layout_healthy_without_chain_session(tmp_path):
    skill = tmp_path / "skill"
    _write_runtime_face(skill, _core_face_files())
    assert not (skill / "scripts" / "chain_session.py").exists()
    (skill / ".spec-skill-install").write_text(
        "installed_by=spec/install.sh\nsource=/nonexistent\nprofile=core\n", encoding="utf-8"
    )
    doctor = Doctor(skill_root=skill, project_root=tmp_path)
    assert doctor.check_skill_layout() is True
    check = check_by_id(doctor, "skill.layout")
    assert check.status == "ok"
    assert "scripts/chain_session.py" not in doctor.required_runtime_files()


def test_host_filter_normalizes_devin_cursor_aliases(tmp_path):
    doctor = Doctor(skill_root=tmp_path, project_root=tmp_path, host_filter=["devin-desktop", "cursor-cli"])
    assert [host[0] for host in doctor.active_hosts()] == ["devin", "cursor"]
    single = Doctor(skill_root=tmp_path, project_root=tmp_path, host_filter=["cursor-cli"])
    assert [host[0] for host in single.active_hosts()] == ["cursor"]
    other = Doctor(skill_root=tmp_path, project_root=tmp_path, host_filter=["devin-desktop"])
    assert [host[0] for host in other.active_hosts()] == ["devin"]


def test_foreign_devin_skill_dir_is_protected(isolated_home):
    foreign = isolated_home / ".config" / "devin" / "skills" / "spec"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("# foreign spec\n", encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=isolated_home, host_filter=["devin-desktop"])
    assert [host[0] for host in doctor.active_hosts()] == ["devin"]
    assert doctor.is_foreign_skill(foreign) is True
    assert doctor.is_installer_owned(foreign) is False


def _repo_with_hooks_path(tmp_path, rel=".githooks"):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "core.hooksPath", rel], cwd=repo, check=True)
    hooks_dir = repo / rel
    hooks_dir.mkdir(parents=True, exist_ok=True)
    return repo, hooks_dir


@requires_git
def test_project_hooks_honors_core_hooks_path_rendered_gate(tmp_path):
    repo, hooks_dir = _repo_with_hooks_path(tmp_path)
    checker = ROOT / "scripts" / "check_all_spec_packages.py"
    (hooks_dir / "pre-commit").write_text(f'#!/bin/sh\nchecker="{checker}"\n', encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks(repo.resolve())
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "ok"
    assert "指向当前 skill" in check.detail


@requires_git
def test_project_hooks_path_stale_gate_fix_targets_hooks_path(tmp_path):
    repo, hooks_dir = _repo_with_hooks_path(tmp_path)
    (hooks_dir / "pre-commit").write_text(
        '#!/bin/sh\nchecker="/gone/skills/spec/scripts/check_all_spec_packages.py"\n', encoding="utf-8"
    )
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks(repo.resolve())
    assert check_by_id(doctor, "project.hooks").status == "fail"
    doctor = Doctor(skill_root=ROOT, project_root=repo, fix=True)
    doctor.check_project_hooks(repo.resolve())
    assert check_by_id(doctor, "project.hooks").status == "fixed"
    rendered = (hooks_dir / "pre-commit").read_text(encoding="utf-8")
    assert str(ROOT / "scripts" / "check_all_spec_packages.py") in rendered
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()


@requires_git
def test_project_hooks_path_foreign_hook_is_info(tmp_path):
    repo, hooks_dir = _repo_with_hooks_path(tmp_path)
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\necho project-owned\n", encoding="utf-8")
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks(repo.resolve())
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "info"
    assert "项目自有" in check.detail


@requires_git
def test_project_hooks_path_outside_repo_hint_is_not_installer_only(tmp_path):
    outside = tmp_path / "global-hooks"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "core.hooksPath", str(outside)], cwd=repo, check=True)
    doctor = Doctor(skill_root=ROOT, project_root=repo)
    doctor.check_project_hooks(repo.resolve())
    check = check_by_id(doctor, "project.hooks")
    assert check.status == "warn"
    assert str(outside) in check.detail
    assert "仓库外" in check.fix_note

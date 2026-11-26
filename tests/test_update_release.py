from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
SPEC = importlib.util.spec_from_file_location("update_release", ROOT / "updater/update_release.py")
assert SPEC and SPEC.loader
update_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update_release)
PROJECT_VERSION = subprocess.check_output(
    [sys.executable, str(ROOT / "scripts/read_version.py"), str(ROOT / "pyproject.toml")], text=True
).strip()


def newer_version(version: str) -> str:
    assert update_release.VERSION_PATTERN.fullmatch(version), f"expected a stable version, got {version!r}"
    major, minor, patch = map(int, version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def install(home: Path, hosts: str, profile: str = "core") -> None:
    environment = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "INSTALL_HOSTS": hosts,
        "INSTALL_PROFILE": profile,
    }
    result = subprocess.run(["bash", str(INSTALLER)], cwd=ROOT, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home with spaces"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    for variable in (
        "INSTALL_HOSTS",
        "SOURCE_DIR",
        "INSTALL_PROFILE",
        "FORCE",
        "DRY_RUN",
        "PREFLIGHT_ONLY",
        "SPEC_UPDATE_OWNED_ONLY",
        "CLAUDE_SKILLS_DIR",
        "CODEX_SKILLS_DIR",
        "AGENTS_SKILLS_DIR",
        "INSTALL_DIR",
        "BACKUP_ROOT",
    ):
        monkeypatch.delenv(variable, raising=False)
    return path


def make_release(tmp_path: Path, version: str, *, legacy: bool = False) -> bytes:
    root = tmp_path / f"spec-harness-{version}"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/export_skill_package.py"), "--root", str(ROOT), "--output", str(root)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    project = root / "pyproject.toml"
    project.write_text(
        re.sub(r'(?m)^version\s*=\s*"[^"]+"', f'version = "{version}"', project.read_text(encoding="utf-8"), count=1)
    )
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    (root / "BUILD_INFO").write_text(f"version={version}\n", encoding="utf-8")
    if legacy:
        installer = root / "install.sh"
        text = installer.read_text(encoding="utf-8")
        text = text.replace("SPEC_UPDATE_PREFLIGHT=1\n", "", 1)
        text = text.replace(
            '  if [ "$PREFLIGHT_ONLY" = "1" ]; then\n    log "Installer preflight passed."\n    return 0\n  fi\n', "", 1
        )
        text = text.replace('  install_update_launcher "$runtime_dir"\n', "", 1)
        installer.write_text(text, encoding="utf-8")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(root, arcname=root.name)
    return buffer.getvalue()


@pytest.fixture
def release(tmp_path):
    version = newer_version(PROJECT_VERSION)
    return version, make_release(tmp_path, version)


def fake_download(monkeypatch, version: str, archive: bytes, *, checksum: str | None = None):
    archive_name = f"spec-harness-{version}.tar.gz"
    digest = checksum or hashlib.sha256(archive).hexdigest()
    requests = []

    def release_info(selected):
        requests.append("release")
        assert selected is None or selected == version
        return version, archive_name

    def download(url, limit):
        requests.append(url)
        if url.endswith("/SHA256SUMS"):
            return f"{digest}  {archive_name}\n".encode()
        if url.endswith("/" + archive_name):
            return archive
        raise AssertionError(url)

    monkeypatch.setattr(update_release, "release_info", release_info)
    monkeypatch.setattr(update_release, "download", download)
    return requests


def snapshot(home: Path) -> dict[str, bytes | str]:
    return {
        str(path.relative_to(home)): os.readlink(path) if path.is_symlink() else path.read_bytes()
        for path in home.rglob("*")
        if path.is_file() or path.is_symlink()
    }


def marker(home: Path, host: str) -> str:
    return (home / f".{host}" / "skills" / "spec" / ".spec-skill-install").read_text(encoding="utf-8")


def set_marker_version(home: Path, host: str, version: str) -> None:
    installed_marker = home / f".{host}" / "skills" / "spec" / ".spec-skill-install"
    content, replacements = re.subn(r"(?m)^version=.*$", f"version={version}", marker(home, host))
    assert replacements == 1
    installed_marker.write_text(content, encoding="utf-8")
    assert f"version={version}" in marker(home, host).splitlines()


def test_installer_exposes_version_command_and_preserves_foreign_executable(home):
    install(home, "codex")
    launcher = home / ".local/bin/spec-harness"
    result = subprocess.run([str(launcher), "--version"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert f"codex: {PROJECT_VERSION} (" in result.stdout

    other_home = home.parent / "other"
    foreign = other_home / ".local/bin/spec-harness"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("user command\n", encoding="utf-8")
    install(other_home, "codex")
    assert foreign.read_text(encoding="utf-8") == "user command\n"
    assert (other_home / ".codex/skills/spec/SKILL.md").is_file()


def test_update_preview_and_upgrade_preserve_hosts_and_preferences(home, release, monkeypatch, capsys):
    install(home, "claude", "full")
    install(home, "codex", "core")
    foreign = home / ".gemini/skills/spec/SKILL.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("user skill\n", encoding="utf-8")
    project = home / "project/.spec/specs/active/spec.md"
    project.parent.mkdir(parents=True)
    project.write_text("user package\n", encoding="utf-8")
    settings = home / ".claude/settings.json"
    config = json.loads(settings.read_text(encoding="utf-8"))
    config["user-setting"] = "preserved"
    settings.write_text(json.dumps(config), encoding="utf-8")
    version, archive = release
    requests = fake_download(monkeypatch, version, archive)

    before = snapshot(home)
    assert update_release.main(["update", "--check"]) == 0
    assert len(requests) == 1
    assert snapshot(home) == before
    assert update_release.main(["update", "--dry-run"]) == 0
    assert snapshot(home) == before
    assert update_release.main(["update"]) == 0

    assert f"version={version}" in marker(home, "claude").splitlines()
    assert f"version={version}" in marker(home, "codex").splitlines()
    assert "profile=full" in marker(home, "claude")
    assert "profile=core" in marker(home, "codex")
    assert foreign.read_text(encoding="utf-8") == "user skill\n"
    assert project.read_text(encoding="utf-8") == "user package\n"
    assert json.loads(settings.read_text(encoding="utf-8"))["user-setting"] == "preserved"
    assert (home / ".spec-skill-backups/.claude-skills").is_dir()
    assert f"Updated Spec Harness to {version}" in capsys.readouterr().out

    requests.clear()
    after = snapshot(home)
    assert update_release.main(["update"]) == 0
    assert len(requests) == 1
    assert snapshot(home) == after


def test_update_preserves_shared_symlinks(home, release, monkeypatch):
    install(home, "claude")
    entity = home / "library/skills/spec"
    entity.parent.mkdir(parents=True)
    claude = home / ".claude/skills/spec"
    shutil.move(str(claude), str(entity))
    claude.symlink_to(entity, target_is_directory=True)
    codex = home / ".codex/skills/spec"
    codex.parent.mkdir(parents=True)
    codex.symlink_to(entity, target_is_directory=True)
    version, archive = release
    fake_download(monkeypatch, version, archive)

    assert update_release.main(["update"]) == 0
    assert claude.is_symlink() and codex.is_symlink()
    assert f"version={version}" in (entity / ".spec-skill-install").read_text(encoding="utf-8").splitlines()


def test_update_preserves_agents_chain_with_published_legacy_installer(home, tmp_path, monkeypatch):
    install(home, "claude")
    entity = home / "library/skills/spec"
    entity.parent.mkdir(parents=True)
    claude = home / ".claude/skills/spec"
    shutil.move(str(claude), str(entity))
    claude.symlink_to(entity, target_is_directory=True)
    shared = home / ".agents/skills/spec"
    shared.parent.mkdir(parents=True)
    shared.symlink_to(claude, target_is_directory=True)
    version = newer_version(PROJECT_VERSION)
    make_release(tmp_path, version)
    source = tmp_path / f"spec-harness-{version}"
    installer = source / "install.sh"
    text = installer.read_text(encoding="utf-8")
    text = text.replace("  local link resolved link_target claude_link\n", "  local link resolved\n")
    start = text.index('  if ! resolved="$(skill_link_resolve "$link")"; then\n')
    end = text.index("  # Only a target this installer owns", start)
    text = text[:start] + '  resolved="$(skill_link_resolve "$link")" || return 1\n' + text[end:]
    installer.write_text(text, encoding="utf-8")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(source, arcname=source.name)
    fake_download(monkeypatch, version, buffer.getvalue())

    before = snapshot(home)
    assert update_release.main(["update", "--dry-run"]) == 0
    assert snapshot(home) == before
    assert update_release.main(["update"]) == 0
    assert claude.is_symlink() and shared.is_symlink()
    assert f"version={version}" in (entity / ".spec-skill-install").read_text(encoding="utf-8").splitlines()


def test_update_refuses_agents_chain_through_chained_claude(home, release, monkeypatch, capsys):
    install(home, "claude")
    entity = home / "library/skills/spec"
    entity.parent.mkdir(parents=True)
    claude = home / ".claude/skills/spec"
    shutil.move(str(claude), str(entity))
    intermediary = home / "intermediary"
    intermediary.symlink_to(entity, target_is_directory=True)
    claude.symlink_to(intermediary, target_is_directory=True)
    shared = home / ".agents/skills/spec"
    shared.parent.mkdir(parents=True)
    shared.symlink_to(claude, target_is_directory=True)
    version, archive = release
    fake_download(monkeypatch, version, archive)
    before = snapshot(home)

    assert update_release.main(["update"]) == 1
    assert "single-hop Claude skill" in capsys.readouterr().err
    assert snapshot(home) == before


def test_bad_checksum_and_network_failure_leave_installation_untouched(home, release, monkeypatch, capsys):
    install(home, "codex")
    before = snapshot(home)
    version, archive = release
    fake_download(monkeypatch, version, archive, checksum="0" * 64)
    assert update_release.main(["update"]) == 1
    assert "SHA-256 does not match" in capsys.readouterr().err
    assert snapshot(home) == before

    def offline(version, source="github"):
        raise update_release.UpdateError("network unavailable")

    monkeypatch.setattr(update_release, "release_info", offline)
    assert update_release.main(["update"]) == 1
    assert "network unavailable" in capsys.readouterr().err
    assert snapshot(home) == before


@pytest.mark.parametrize(
    "entry", ["spec-harness-2.1.0/../../escape", "/absolute", "spec-harness-2.1.0/link", "spec-harness-2.1.0/C:/escape"]
)
def test_archive_rejects_traversal_and_links(tmp_path, entry):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo(entry)
        if entry.endswith("/link"):
            member.type = tarfile.SYMTYPE
            member.linkname = "../../escape"
        archive.addfile(member)
    with pytest.raises(update_release.UpdateError, match="unsafe release archive entry"):
        update_release.extract_archive(buffer.getvalue(), "2.1.0", tmp_path)
    assert not (tmp_path.parent / "escape").exists()


@pytest.mark.parametrize(
    "resolved",
    [
        "https://github.com/MicTx/spec-harness/releases/tag/v2.1.0-rc1",
        "https://example.org/MicTx/spec-harness/releases/tag/v2.1.0",
        "https://github.com/other/spec-harness/releases/tag/v2.1.0",
    ],
)
def test_release_page_rejects_prereleases_and_other_hosts(monkeypatch, resolved):
    monkeypatch.setattr(update_release, "fetch", lambda *args, **kwargs: (b"", resolved))
    with pytest.raises(update_release.UpdateError):
        update_release.release_info(None)


def test_download_falls_back_to_curl_after_proxy_failure(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise update_release.http.client.BadStatusLine("proxy failure")

    def run(command, **kwargs):
        destination = Path(command[command.index("--output") + 1])
        destination.write_bytes(b"archive")
        return subprocess.CompletedProcess(command, 0, "https://github.com/file", "")

    monkeypatch.setattr(update_release.urllib.request, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(update_release.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(update_release.subprocess, "run", run)
    assert update_release.fetch("https://github.com/file", 100) == (b"archive", "https://github.com/file")
    with pytest.raises(update_release.UpdateError, match="size limit"):
        update_release.fetch("https://github.com/file", 3)


def test_auto_source_uses_mirror_for_lookup_and_download(home, release, monkeypatch, capsys):
    install(home, "codex")
    version, archive = release
    archive_name = f"spec-harness-{version}.tar.gz"
    requests = []

    def fetch(url, limit, *, head=False):
        requests.append(url)
        if not url.startswith(update_release.CHINA_MIRROR):
            raise update_release.UpdateError("GitHub unavailable")
        if "/api.github.com/" in url:
            return json.dumps(
                {
                    "tag_name": f"v{version}",
                    "draft": False,
                    "prerelease": False,
                    "assets": [{"name": archive_name}, {"name": "SHA256SUMS"}],
                }
            ).encode(), url
        if url.endswith("/SHA256SUMS"):
            return f"{hashlib.sha256(archive).hexdigest()}  {archive_name}\n".encode(), url
        assert url.endswith("/" + archive_name)
        return archive, url

    monkeypatch.setattr(update_release, "fetch", fetch)
    assert update_release.main(["update"]) == 0
    assert f"version={version}" in marker(home, "codex").splitlines()
    assert "source: china" in capsys.readouterr().out
    assert requests[0].startswith("https://github.com/")
    assert all(url.startswith(update_release.CHINA_MIRROR) for url in requests[1:])


def test_mirror_metadata_rejects_unstable_or_mismatched_release(monkeypatch):
    metadata = {"tag_name": "v2.2.0", "draft": False, "prerelease": False, "assets": []}
    monkeypatch.setattr(update_release, "download", lambda url, limit: json.dumps(metadata).encode())
    with pytest.raises(update_release.UpdateError, match="missing"):
        update_release.release_info(None, "china")
    metadata["prerelease"] = True
    with pytest.raises(update_release.UpdateError, match="stable"):
        update_release.release_info(None, "china")
    metadata["prerelease"] = False
    with pytest.raises(update_release.UpdateError, match="does not match"):
        update_release.release_info("2.1.0", "china")


def test_download_fallback_checks_digest_from_each_source(monkeypatch):
    archive_name = "spec-harness-2.2.0.tar.gz"
    archive = b"valid archive"
    requested = []

    def download(url, limit):
        requested.append(url)
        mirror = url.startswith(update_release.CHINA_MIRROR)
        if url.endswith("/SHA256SUMS"):
            digest = hashlib.sha256(archive).hexdigest() if mirror else "0" * 64
            return f"{digest}  {archive_name}\n".encode()
        return archive

    monkeypatch.setattr(update_release, "download", download)
    data, source = update_release.download_release("2.2.0", archive_name, "auto", "github")
    assert data == archive and source == "china"
    assert len(requested) == 4
    with pytest.raises(update_release.UpdateError, match="SHA-256"):
        update_release.download_release("2.2.0", archive_name, "github", "github")


def test_source_option_and_environment_validate(home, monkeypatch, capsys):
    install(home, "codex")
    monkeypatch.setenv("SPEC_UPDATE_SOURCE", "invalid")
    assert update_release.main(["update", "--check"]) == 1
    assert "SPEC_UPDATE_SOURCE" in capsys.readouterr().err
    monkeypatch.setattr(update_release, "release_info", lambda version, source: ("2.2.0", "archive"))
    assert update_release.main(["update", "--check", "--source", "china"]) == 0


def test_update_preflights_all_groups_before_changing_any_host(home, release, monkeypatch, capfd):
    install(home, "claude")
    install(home, "codex")
    custom = home / "custom/skills"
    custom.mkdir(parents=True)
    shutil.move(str(home / ".codex/skills/spec"), str(custom / "spec"))
    monkeypatch.setenv("CODEX_SKILLS_DIR", str(custom))
    before = snapshot(home)
    version, archive = release
    fake_download(monkeypatch, version, archive)

    assert update_release.main(["update"]) == 1
    output = capfd.readouterr()
    assert "Codex install root is outside the default path" in output.err
    assert snapshot(home) == before

    monkeypatch.setenv("FORCE", "1")
    assert update_release.main(["update"]) == 0
    assert f"version={version}" in marker(home, "claude").splitlines()
    assert f"version={version}" in (custom / "spec/.spec-skill-install").read_text(encoding="utf-8").splitlines()


def test_custom_root_force_never_overwrites_foreign_command(home, release, monkeypatch, capfd):
    install(home, "claude")
    custom = home / "custom/commands"
    command = custom / "spec/plan.md"
    command.parent.mkdir(parents=True)
    command.write_text("user command\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_COMMANDS_DIR", str(custom))
    monkeypatch.setenv("FORCE", "1")
    version, archive = release
    fake_download(monkeypatch, version, archive)
    before = snapshot(home)

    assert update_release.main(["update"]) == 1
    assert "refusing to replace user-owned target without valid marker" in capfd.readouterr().err
    assert snapshot(home) == before


def test_rollback_requires_explicit_version(home, release, monkeypatch, capsys):
    install(home, "codex")
    version, archive = release
    installed_version = newer_version(version)
    set_marker_version(home, "codex", installed_version)
    fake_download(monkeypatch, version, archive)
    before = snapshot(home)
    assert update_release.main(["update"]) == 1
    assert "select --version explicitly to roll back" in capsys.readouterr().err
    assert snapshot(home) == before
    assert f"version={installed_version}" in marker(home, "codex").splitlines()
    assert update_release.main(["update", "--version", version]) == 0
    assert f"version={version}" in marker(home, "codex").splitlines()


def test_rollback_to_pre_updater_release_previews_without_writes(home, tmp_path, monkeypatch):
    install(home, "codex")
    set_marker_version(home, "codex", newer_version("2.0.0"))
    archive = make_release(tmp_path, "2.0.0", legacy=True)
    fake_download(monkeypatch, "2.0.0", archive)
    before = snapshot(home)

    assert update_release.main(["update", "--version", "2.0.0", "--dry-run"]) == 0
    assert snapshot(home) == before
    assert update_release.main(["update", "--version", "2.0.0"]) == 0
    assert "version=2.0.0" in marker(home, "codex")
    assert (home / ".local/bin/spec-harness").is_file()


def test_rollback_against_real_legacy_release(home, monkeypatch):
    archive_path = ROOT / "release/spec-harness-2.0.0.tar.gz"
    if not archive_path.is_file():
        pytest.skip("legacy release archive is not included in public source exports")
    install(home, "codex")
    set_marker_version(home, "codex", newer_version("2.0.0"))
    fake_download(monkeypatch, "2.0.0", archive_path.read_bytes())
    before = snapshot(home)

    assert update_release.main(["update", "--version", "2.0.0", "--dry-run"]) == 0
    assert snapshot(home) == before
    assert update_release.main(["update", "--version", "2.0.0"]) == 0
    assert "version=2.0.0" in marker(home, "codex")
    assert (home / ".local/bin/spec-harness").is_file()

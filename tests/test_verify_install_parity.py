"""Tests for scripts/verify_install_parity.py (install-vs-repo parity contract).

The three by-design asymmetries between a checkout and an installed copy —
the export-time \\u2063 signature riding in NOTICE, the maintainer
SCRIPT_BLACKLIST, and the ROOT_FILES-only install root — are pinned as PASS
conditions here, plus the failure shapes that must go red: version drift,
content drift, missing shipped files, stale maintainer scripts, a NOTICE
without a signature, and a signature leaked into the repo source or into
the entry SKILL.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import verify_install_parity as parity  # noqa: E402  # type: ignore
from payload_contract import SCRIPT_BLACKLIST  # noqa: E402  # type: ignore

VERSION = "9.9.9"


def build_repo(base: Path, *, stamped_skill: bool = False) -> Path:
    """A minimal fake authoring repo matching the payload contract's shape."""
    repo = base / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "references").mkdir()
    (repo / "slots").mkdir()
    skill = "# spec skill\n\nread me.\n"
    if stamped_skill:
        skill += "\u2063"  # the leak-under-test
    (repo / "SKILL.md").write_text(skill, encoding="utf-8")
    (repo / "install.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(f'[project]\nversion = "{VERSION}"\n', encoding="utf-8")
    (repo / "scripts" / "runtime_one.py").write_text("print('one')\n", encoding="utf-8")
    (repo / "scripts" / "runtime_two.py").write_text("print('two')\n", encoding="utf-8")
    (repo / "scripts" / "_private.py").write_text("print('private')\n", encoding="utf-8")
    for name in ("build_release.py", "payload_contract.py"):
        (repo / "scripts" / name).write_text(f"# maintainer-only {name}\n", encoding="utf-8")
    (repo / "references" / "commands.md").write_text("# commands\n", encoding="utf-8")
    (repo / "slots" / "manifest.json").write_text("{}\n", encoding="utf-8")
    # repo-level docs that are NOT part of the shipped root surface
    (repo / "RELEASE.md").write_text("# releases\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("# changelog\n", encoding="utf-8")
    return repo


def build_install(
    repo: Path, *, sign: bool = True, mutate=None, drop=None, stale=(), extra=(), sidecar_version=None
) -> Path:
    """A faithful installed copy: shipped set + export-time signature."""
    install = repo.parent / "install"
    install.mkdir()
    for source in parity.shipped_files(repo):
        target = install / source.relative_to(repo)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    if sign:
        # the real watermark shape: NOTICE = visible text + MARK + PAD-bitstream + MARK
        (install / "NOTICE").write_text(
            "distribution notice\n" + "\u2063\u2060\u200b\u2060\u200b\u2063", encoding="utf-8"
        )
    if mutate:
        mutate(install)
    for relative in drop or ():
        (install / relative).unlink()
    for name in stale:
        (install / "scripts" / name).write_text("# stale leftover\n", encoding="utf-8")
    for relative in extra:
        target = install / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("user sidecar\n", encoding="utf-8")
    if sidecar_version is not None:
        (install / ".spec-skill-install").write_text(
            f"installed_by=spec/install.sh\nsource=/tmp/repo\nversion={sidecar_version}\n", encoding="utf-8"
        )
    return install


class TestParityContract:
    def test_faithful_install_passes_with_expected_asymmetries(self, tmp_path, capsys):
        repo = build_repo(tmp_path)
        install = build_install(repo, extra=("notes.md",), sidecar_version=VERSION)  # user sidecar + installer sidecar
        assert parity.main(["--install", str(install), "--repo", str(repo)]) == 0
        report = parity.audit_install(install, repo)
        assert report["ok"] is True
        assert report["warnings"] == ["extra file in install (user-owned?): notes.md"]
        # the installer sidecar is expected, never a warning
        assert all(".spec-skill-install" not in w for w in report["warnings"])
        # the asymmetries are by design and never produce failures
        assert not (install / "RELEASE.md").exists()
        assert not (install / "scripts" / "build_release.py").exists()
        # the entry file stays plain text; the signature rides in NOTICE
        assert "\u2063" not in (install / "SKILL.md").read_text(encoding="utf-8")
        assert "\u2063" in (install / "NOTICE").read_text(encoding="utf-8")

    def test_version_drift_fails(self, tmp_path, capsys):
        repo = build_repo(tmp_path)
        install = build_install(repo)
        (install / "pyproject.toml").write_text('[project]\nversion = "0.0.1"\n', encoding="utf-8")
        assert parity.main(["--install", str(install), "--repo", str(repo)]) == 1
        assert any("version drift" in f for f in parity.audit_install(install, repo)["failures"])

    def test_content_drift_fails(self, tmp_path):
        repo = build_repo(tmp_path)

        def mutate(install: Path) -> None:
            (install / "references" / "commands.md").write_text("# tampered\n", encoding="utf-8")

        install = build_install(repo, mutate=mutate)
        failures = parity.audit_install(install, repo)["failures"]
        assert "content drift: references/commands.md" in failures

    def test_missing_shipped_file_fails(self, tmp_path):
        repo = build_repo(tmp_path)
        install = build_install(repo, drop=("scripts/runtime_one.py",))
        assert "missing shipped file: scripts/runtime_one.py" in parity.audit_install(install, repo)["failures"]

    def test_stale_maintainer_script_fails(self, tmp_path):
        repo = build_repo(tmp_path)
        install = build_install(repo, stale=("export_public_repo.py",))
        assert (
            "stale maintainer script in install: scripts/export_public_repo.py"
            in parity.audit_install(install, repo)["failures"]
        )

    def test_notice_without_signature_fails(self, tmp_path):
        # a NOTICE carrier that lost its mark is a broken signed install
        repo = build_repo(tmp_path)
        install = build_install(repo)

        def strip_mark(install: Path) -> None:
            notice = install / "NOTICE"
            notice.write_text("distribution notice\n", encoding="utf-8")

        strip_mark(install)
        assert any(
            "NOTICE carries no distribution signature" in f for f in parity.audit_install(install, repo)["failures"]
        )

    def test_signature_in_entry_file_fails(self, tmp_path):
        # stale pre-AUD-17 install: the mark still sits in SKILL.md
        repo = build_repo(tmp_path)
        install = build_install(repo, sign=False)
        skill = install / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\u2063\u2060\u200b\u2060\u200b\u2063", encoding="utf-8")
        failures = parity.audit_install(install, repo)["failures"]
        assert any("install SKILL.md carries a distribution signature" in f for f in failures)
        assert any("content drift: SKILL.md" in f for f in failures)

    def test_signature_leaked_into_repo_fails(self, tmp_path):
        repo = build_repo(tmp_path, stamped_skill=True)
        install = build_install(repo)
        assert any("repo SKILL.md is signature-stamped" in f for f in parity.audit_install(install, repo)["failures"])

    def test_skill_drift_fails(self, tmp_path):
        # the entry file is byte-compared now — any tampering is real drift
        repo = build_repo(tmp_path)

        def mutate(install: Path) -> None:
            (install / "SKILL.md").write_text("# tampered skill\n", encoding="utf-8")

        install = build_install(repo, mutate=mutate)
        assert "content drift: SKILL.md" in parity.audit_install(install, repo)["failures"]

    def test_sidecar_version_drift_fails(self, tmp_path):
        # half-upgraded install: payload moved on, ownership sidecar stale
        repo = build_repo(tmp_path)
        install = build_install(repo, sidecar_version="0.0.1")
        assert any("sidecar version drift" in f for f in parity.audit_install(install, repo)["failures"])

    def test_json_report_shape(self, tmp_path, capsys):
        repo = build_repo(tmp_path)
        install = build_install(repo)
        assert parity.main(["--install", str(install), "--repo", str(repo), "--json"]) == 0
        import json

        payload = json.loads(capsys.readouterr().out)
        assert payload["repo"].endswith("repo")
        assert len(payload["installs"]) == 1
        assert payload["installs"][0]["ok"] is True
        assert payload["installs"][0]["version"] == VERSION

    def test_not_an_install_directory_fails_cleanly(self, tmp_path, capsys):
        repo = build_repo(tmp_path)
        empty = tmp_path / "empty-install"
        empty.mkdir()
        assert parity.main(["--install", str(empty), "--repo", str(repo)]) == 1
        assert "no SKILL.md" in capsys.readouterr().out

    def test_no_installs_given_is_a_usage_error(self, tmp_path, capsys):
        repo = build_repo(tmp_path)
        assert parity.main(["--repo", str(repo)]) == 2
        assert "no installs to audit" in capsys.readouterr().err


class TestHostDiscovery:
    def test_discovery_finds_skill_shaped_installs_only(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".claude" / "skills" / "spec").mkdir(parents=True)
        (home / ".claude" / "skills" / "spec" / "SKILL.md").write_text("# spec\n", encoding="utf-8")
        (home / ".claude" / "skills" / "spec" / "scripts").mkdir()
        (home / ".pi" / "agent" / "skills" / "spec").mkdir(parents=True)  # no SKILL.md → skipped
        (home / ".codex" / "skills").mkdir(parents=True)  # no spec dir → skipped
        monkeypatch.setattr(Path, "home", lambda: home)
        installs = parity.discover_host_installs(home)
        assert [p.name for p in installs] == ["spec"]
        assert installs[0].parent == home / ".claude" / "skills"


class TestBlacklistSelfConsistency:
    def test_this_tool_is_maintainer_only(self):
        # verify_install_parity compares against a repo checkout — shipping it
        # into installs would be dead weight and a false promise
        assert "verify_install_parity.py" in SCRIPT_BLACKLIST

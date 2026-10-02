"""The tracked release/ payload must match the canonical source build.

The in-repo tracked delivery payload is what users download between GitHub
releases. This test fails when release/ drifts from what
`scripts/build_release.py` produces from the current source (e.g. shipping
0.8.0 archives while pyproject says 0.10.0), or when the naming rule
(`spec-harness-{version}`) is violated.

Parity is asserted at *content* level (unpacked, member by member), not at
archive-byte level: BUILD_INFO embeds commit-derived stamps (git_sha and
build_time, see ``build_release.stamp_build_info``), so the commit that adds
a payload can never be stamped inside its own payload. BUILD_INFO is
therefore the single structural exemption from byte comparison and is
validated semantically instead: its provenance fields must be internally
consistent and anchored to a real commit in this repository's history.
RELEASE_NOTES.md keeps its pre-existing exemption (the pi editorial pass is
intentionally nondeterministic prose). SHA256SUMS digests hash archive bytes
that necessarily differ across sides, so the parity gate checks it
structurally (name sets match, canonical side recomputes) while the tracked
side keeps its dedicated byte-accuracy test below.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from build_release import DEFAULT_DIST, PROJECT_NAME  # noqa: E402

RELEASE_DIR = ROOT / "release"
ARTIFACT_SUFFIXES = (".tar.gz", ".zip")

# Payload members exempt from byte comparison, each with an explicit reason:
# - BUILD_INFO: commit-derived stamps (git_sha/build_time) can never describe
#   the commit that adds the payload itself; validated semantically instead.
# - RELEASE_NOTES.md: pi editorial pass, intentionally nondeterministic prose.
BYTE_PARITY_EXEMPT = ("BUILD_INFO", "RELEASE_NOTES.md")

BUILD_INFO_KEYS = {"version", "git_sha", "runtime_sha256", "build_time", "built_by"}
RELEASE_PARITY_REQUIRED = os.environ.get("SPEC_RELEASE_PARITY") == "1"


def _project_version() -> str:
    return read_version(ROOT)


def read_version(root: Path) -> str:
    for line in (root / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError("pyproject.toml has no project version")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _canonical_build(tmp: Path) -> dict[str, bytes]:
    signing = []
    identity = Path(os.environ.get("SPEC_SIGNING_IDENTITY", ROOT / ".watermark-identity.json"))
    key = Path(os.environ.get("SPEC_SIGNING_KEY", ROOT / ".watermark-key"))
    if not (identity.is_file() and key.is_file()):
        signing = ["--skip-signing"]
    built = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_release.py"),
            "--output",
            str(tmp),
            "--skip-checks",
            # The AI editorial pass is nondeterministic; content-parity compares
            # the raw deterministic changelog excerpt instead.
            "--no-ai-notes",
            *signing,
            "--expect-version",
            _project_version(),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    return {path.name: path.read_bytes() for path in sorted(tmp.iterdir()) if path.is_file()}


def _run_git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def _commit_build_time(git_sha: str) -> str:
    """Replay the stamper's exact transform: %cI -> fromisoformat -> strftime."""
    committed_at = _run_git("log", "-1", "--format=%cI", git_sha)
    return datetime.fromisoformat(committed_at).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_archive_root(name: str, archive: Path) -> str:
    parts = name.split("/", 1)
    assert len(parts) == 2, f"{archive.name}: member outside archive root: {name}"
    return parts[1]


def _tar_members(archive: Path) -> dict[str, bytes]:
    """Map archive-root-relative member path -> file content for a .tar.gz."""
    members: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            assert handle is not None, member.name
            members[_strip_archive_root(member.name, archive)] = handle.read()
    return members


def _zip_members(archive: Path) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            members[_strip_archive_root(name, archive)] = zf.read(name)
    return members


def _parse_build_info(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition("=")
        assert sep, f"BUILD_INFO line is not key=value: {line!r}"
        fields[key] = value
    return fields


def _parse_sha256sums(path: Path) -> dict[str, str]:
    recorded = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        recorded[name.strip()] = digest
    return recorded


def _assert_content_parity(
    tracked: dict[str, bytes],
    canonical: dict[str, bytes],
    exempt: tuple[str, ...],
    label: str,
) -> None:
    """Per-member byte equality with an explicit exemption list."""
    expected = set(canonical) - set(exempt)
    actual = set(tracked) - set(exempt)
    assert actual == expected, (
        f"{label}: payload members drifted beyond exempt {sorted(exempt)}: "
        f"missing={sorted(expected - actual)} extra={sorted(actual - expected)}"
    )
    for name in sorted(expected):
        assert tracked[name] == canonical[name], f"{label}: release payload member {name} differs from canonical build"


def _validate_build_info(fields: dict[str, str], *, expect_version: str, side: str) -> None:
    """Semantic provenance validation for one side's BUILD_INFO.

    Commit currency is deliberately NOT required: the payload is stamped by
    the commit that built it, which necessarily predates HEAD (fixed point).
    The stamps must instead be internally consistent and anchored to a real
    commit; freshness is carried by runtime_sha256 equality across sides.
    """
    assert set(fields) == BUILD_INFO_KEYS, (
        f"{side}: BUILD_INFO member set drifted: {sorted(set(fields) ^ BUILD_INFO_KEYS)}"
    )
    assert fields["version"] == expect_version, f"{side}: BUILD_INFO.version != pyproject version ({expect_version})"
    assert fields["built_by"] == "build_release.py", f"{side}: unexpected built_by: {fields['built_by']!r}"
    assert re.fullmatch(r"[0-9a-f]{64}", fields["runtime_sha256"]), (
        f"{side}: runtime_sha256 is not a sha-256 hex digest"
    )
    git_sha = fields["git_sha"]
    assert re.fullmatch(r"[0-9a-f]{12}", git_sha), f"{side}: git_sha is not a 12-hex short sha: {git_sha!r}"
    # Must resolve to a real commit in this repository's history. On depth=1
    # checkouts this fails loudly by design: provenance needs full history
    # (workflows therefore check out with fetch-depth: 0).
    _run_git("cat-file", "-e", f"{git_sha}^{{commit}}")
    expected_time = _commit_build_time(git_sha)
    assert fields["build_time"] == expected_time, (
        f"{side}: build_time {fields['build_time']!r} != commit-derived {expected_time!r} "
        "(stamper transform: %cI -> fromisoformat -> strftime %Y-%m-%dT%H:%M:%SZ)"
    )


def _assert_runtime_digests_match(tracked: dict[str, str], canonical: dict[str, str]) -> None:
    """The freshness anchor: both sides stamp the same full payload content."""
    assert tracked["runtime_sha256"] == canonical["runtime_sha256"], (
        f"runtime_sha256 drifted: tracked={tracked['runtime_sha256']} canonical={canonical['runtime_sha256']}"
    )


@pytest.fixture(scope="module")
def canonical_dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One canonical build shared by the whole module (the build is slow)."""
    dist = tmp_path_factory.mktemp("canonical")
    _canonical_build(dist)
    return dist


@pytest.fixture(scope="module")
def tracked_tree() -> dict[str, bytes]:
    return _tar_members(RELEASE_DIR / f"{PROJECT_NAME}-{_project_version()}.tar.gz")


@pytest.fixture(scope="module")
def canonical_tree(canonical_dist: Path) -> dict[str, bytes]:
    return _tar_members(canonical_dist / f"{PROJECT_NAME}-{_project_version()}.tar.gz")


def test_release_dir_holds_versioned_artifacts_matching_naming_rule():
    version = _project_version()
    names = {path.name for path in RELEASE_DIR.iterdir() if path.is_file()}
    for suffix in ARTIFACT_SUFFIXES:
        assert f"{PROJECT_NAME}-{version}{suffix}" in names, f"release/ must track {PROJECT_NAME}-{{version}}{suffix}"
    assert "SHA256SUMS" in names
    assert "RELEASE_NOTES.md" in names
    # No sales-kit naming and no unversioned archives may survive here.
    for name in names:
        assert "sales" not in name.lower(), name
        assert not (name.endswith(ARTIFACT_SUFFIXES) and version not in name), name


@pytest.mark.skipif(not RELEASE_PARITY_REQUIRED, reason="tracked release parity belongs to the release round")
def test_tracked_release_payload_matches_canonical_source(
    canonical_dist: Path,
    tracked_tree: dict[str, bytes],
    canonical_tree: dict[str, bytes],
):
    """release/ content must equal a fresh canonical build, member by member.

    Archive-byte parity is structurally impossible (BUILD_INFO carries
    commit-derived stamps), so comparison happens on unpacked content;
    BUILD_INFO is exempt here and semantically validated in its own test.
    """
    version = _project_version()
    canonical = {path.name: path.read_bytes() for path in sorted(canonical_dist.iterdir()) if path.is_file()}
    tracked = {path.name: path.read_bytes() for path in sorted(RELEASE_DIR.iterdir()) if path.is_file()}
    assert set(tracked) == set(canonical), f"tracked release files drifted: {sorted(set(tracked) ^ set(canonical))}"

    tar_name = f"{PROJECT_NAME}-{version}.tar.gz"
    zip_name = f"{PROJECT_NAME}-{version}.zip"

    _assert_content_parity(tracked_tree, canonical_tree, BYTE_PARITY_EXEMPT, tar_name)

    # Both archives of one side are produced from the same runtime tree, so
    # the zip must carry the exact same members as the tar — no exemptions:
    # this is what keeps the Windows artifact under the same anchor.
    assert _zip_members(RELEASE_DIR / zip_name) == tracked_tree, (
        f"{zip_name} payload diverges from {tar_name} (tracked)"
    )
    assert _zip_members(canonical_dist / zip_name) == canonical_tree, (
        f"{zip_name} payload diverges from {tar_name} (canonical)"
    )

    # SHA256SUMS: structural parity only — its digests hash archive bytes
    # that necessarily differ across sides. Name sets must match and the
    # canonical side must recompute correctly; tracked-side accuracy keeps
    # its dedicated byte-level test below.
    tracked_sums = _parse_sha256sums(RELEASE_DIR / "SHA256SUMS")
    canonical_sums = _parse_sha256sums(canonical_dist / "SHA256SUMS")
    assert set(tracked_sums) == set(canonical_sums), (
        f"SHA256SUMS name sets drifted: {sorted(set(tracked_sums) ^ set(canonical_sums))}"
    )
    for name, digest in canonical_sums.items():
        assert digest == _sha256(canonical_dist / name), f"canonical SHA256SUMS entry inaccurate: {name}"


@pytest.mark.skipif(not RELEASE_PARITY_REQUIRED, reason="tracked release parity belongs to the release round")
def test_build_info_semantics_anchor_both_sides_to_real_commits(
    tracked_tree: dict[str, bytes],
    canonical_tree: dict[str, bytes],
):
    """BUILD_INFO is exempt from byte parity, so its provenance is validated semantically."""
    version = _project_version()
    tracked_info = _parse_build_info(tracked_tree["BUILD_INFO"].decode("utf-8"))
    canonical_info = _parse_build_info(canonical_tree["BUILD_INFO"].decode("utf-8"))
    _validate_build_info(tracked_info, expect_version=version, side="tracked")
    _validate_build_info(canonical_info, expect_version=version, side="canonical")
    _assert_runtime_digests_match(tracked_info, canonical_info)


def test_release_checksums_file_is_accurate():
    version = _project_version()
    lines = (RELEASE_DIR / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    recorded = {}
    for line in lines:
        digest, name = line.split(maxsplit=1)
        recorded[name.strip()] = digest
    for suffix in ARTIFACT_SUFFIXES:
        name = f"{PROJECT_NAME}-{version}{suffix}"
        assert name in recorded, f"SHA256SUMS missing {name}"
        assert recorded[name] == _sha256(RELEASE_DIR / name), name


def test_release_notes_carry_current_version():
    version = _project_version()
    text = (RELEASE_DIR / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert f"[{version}]" in text, f"RELEASE_NOTES.md must reference [{version}]"


def test_default_dist_still_targets_dist_not_release():
    """Default builds land outside the source tree; release/ is never implicit."""
    assert DEFAULT_DIST.parent == ROOT.parent
    assert DEFAULT_DIST.name == "spec-harness-dist"
    assert not DEFAULT_DIST.is_relative_to(ROOT)


# -- Negative samples: every mutation the gate must catch, actually caught.
# Samples mutate the parsed payload in memory only; release/ and the working
# tree are never touched.


class TestReleaseParityNegativeSamples:
    def test_tampered_payload_member_is_rejected(
        self,
        tracked_tree: dict[str, bytes],
        canonical_tree: dict[str, bytes],
    ):
        tampered = dict(canonical_tree)
        tampered["SKILL.md"] = canonical_tree["SKILL.md"] + b"\n# tampered\n"
        with pytest.raises(AssertionError, match="differs from canonical build"):
            _assert_content_parity(tampered, canonical_tree, BYTE_PARITY_EXEMPT, "tampered.tar.gz")

    def test_missing_payload_member_is_rejected(
        self,
        tracked_tree: dict[str, bytes],
        canonical_tree: dict[str, bytes],
    ):
        removed = {name: content for name, content in tracked_tree.items() if name != "SKILL.md"}
        with pytest.raises(AssertionError, match="missing="):
            _assert_content_parity(removed, canonical_tree, BYTE_PARITY_EXEMPT, "missing.tar.gz")

    def test_extra_payload_member_is_rejected(
        self,
        tracked_tree: dict[str, bytes],
        canonical_tree: dict[str, bytes],
    ):
        extra = dict(tracked_tree)
        extra["EVIL.md"] = b"injected"
        with pytest.raises(AssertionError, match="extra="):
            _assert_content_parity(extra, canonical_tree, BYTE_PARITY_EXEMPT, "extra.tar.gz")

    def test_build_info_version_forgery_is_rejected(self, tracked_tree: dict[str, bytes]):
        fields = _parse_build_info(tracked_tree["BUILD_INFO"].decode("utf-8"))
        fields["version"] = "9.9.9"
        with pytest.raises(AssertionError, match="BUILD_INFO.version"):
            _validate_build_info(fields, expect_version=_project_version(), side="forged")

    def test_build_info_runtime_sha256_format_forgery_is_rejected(self, tracked_tree: dict[str, bytes]):
        fields = _parse_build_info(tracked_tree["BUILD_INFO"].decode("utf-8"))
        fields["runtime_sha256"] = "z" * 64
        with pytest.raises(AssertionError, match="runtime_sha256"):
            _validate_build_info(fields, expect_version=_project_version(), side="forged")

    def test_build_info_runtime_sha256_side_drift_is_rejected(
        self,
        tracked_tree: dict[str, bytes],
        canonical_tree: dict[str, bytes],
    ):
        tracked = _parse_build_info(tracked_tree["BUILD_INFO"].decode("utf-8"))
        canonical = _parse_build_info(canonical_tree["BUILD_INFO"].decode("utf-8"))
        tracked["runtime_sha256"] = "0" * 64  # valid hex, wrong value
        with pytest.raises(AssertionError, match="runtime_sha256 drifted"):
            _assert_runtime_digests_match(tracked, canonical)

    def test_build_info_git_sha_format_forgery_is_rejected(self, tracked_tree: dict[str, bytes]):
        fields = _parse_build_info(tracked_tree["BUILD_INFO"].decode("utf-8"))
        fields["git_sha"] = "nope"
        with pytest.raises(AssertionError, match="git_sha"):
            _validate_build_info(fields, expect_version=_project_version(), side="forged")

    def test_build_info_git_sha_unresolvable_commit_is_rejected(self, tracked_tree: dict[str, bytes]):
        fields = _parse_build_info(tracked_tree["BUILD_INFO"].decode("utf-8"))
        fields["git_sha"] = "ffffffffffff"  # 12-hex but no such object exists
        with pytest.raises(AssertionError, match="cat-file"):
            _validate_build_info(fields, expect_version=_project_version(), side="forged")

    def test_build_info_build_time_forgery_is_rejected(self, tracked_tree: dict[str, bytes]):
        fields = _parse_build_info(tracked_tree["BUILD_INFO"].decode("utf-8"))
        fields["build_time"] = "2030-01-01T00:00:00Z"
        with pytest.raises(AssertionError, match="build_time"):
            _validate_build_info(fields, expect_version=_project_version(), side="forged")

    def test_stale_commit_stamp_stays_green_when_internally_consistent(
        self,
        tracked_tree: dict[str, bytes],
        canonical_tree: dict[str, bytes],
    ):
        """The fixed point, stated as a positive sample.

        The tracked payload was stamped by a commit that predates HEAD, and
        that is accepted: internally consistent provenance anchored to a real
        historical commit is the contract — currency with HEAD is explicitly
        not required (runtime_sha256 equality carries freshness instead).
        Splicing the tracked stamps into an otherwise canonical payload must
        therefore stay green: BUILD_INFO is the only structural exemption.
        """
        tracked_info = _parse_build_info(tracked_tree["BUILD_INFO"].decode("utf-8"))
        _validate_build_info(tracked_info, expect_version=_project_version(), side="tracked")
        hybrid = dict(canonical_tree)
        hybrid["BUILD_INFO"] = tracked_tree["BUILD_INFO"]
        _assert_content_parity(hybrid, canonical_tree, BYTE_PARITY_EXEMPT, "hybrid.tar.gz")


# -- AI release-notes polish: editorial pass, never a build dependency.


def test_polish_returns_draft_when_pi_missing(monkeypatch):
    import build_release as br

    monkeypatch.setattr(br.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no pi")))
    draft = "## [1.0.0]\n\n### Added\n- Something.\n"
    assert br.polish_release_notes(draft) == draft


def test_polish_returns_draft_on_timeout(monkeypatch):
    import subprocess

    import build_release as br

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pi", timeout=1)

    monkeypatch.setattr(br.subprocess, "run", slow)
    draft = "## [1.0.0]\n"
    assert br.polish_release_notes(draft) == draft


def test_polish_accepts_agent_output(monkeypatch):
    import build_release as br

    class Done:
        returncode = 0
        stdout = "## [1.0.0]\n\n### Added\n- Polished.\n"
        stderr = ""

    monkeypatch.setattr(br.subprocess, "run", lambda *a, **k: Done())
    assert "Polished." in br.polish_release_notes("## [1.0.0]\n\n### Added\n- Raw.\n")


def test_polish_rejects_garbage_output(monkeypatch):
    import build_release as br

    class Junk:
        returncode = 0
        stdout = "sorry I cannot help"
        stderr = ""

    monkeypatch.setattr(br.subprocess, "run", lambda *a, **k: Junk())
    draft = "## [1.0.0]\n"
    assert br.polish_release_notes(draft) == draft

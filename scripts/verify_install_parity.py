"""Verify an installed spec skill copy against this repository checkout.

The installer stages the runtime payload through the exporter
(``export_skill_package.py``), so three asymmetries between the repo and an
installed copy are BY DESIGN and this tool pins them as PASS conditions —
an install-parity audit should not re-derive them, and must never mistake
the signature for dirt and strip it:

1. A signed install carries the invisible ``\\u2063`` distribution signature
   in ``NOTICE`` (AUD-17: the entry ``SKILL.md`` stays plain text and is
   never loaded into model context); the repository source stays clean.
   An unsigned source-tree install legitimately has no ``NOTICE``.
2. Maintainer-side scripts (``SCRIPT_BLACKLIST`` in ``payload_contract.py``)
   are never shipped — a leftover copy in an install is stale upgrade dirt.
3. Only ``ROOT_FILES`` land at the install root; ``RELEASE.md``,
   ``CHANGELOG.md`` and other repo-level docs are not part of the skill.

Everything else in the shipped set must be byte-identical to the repo.

Usage:
  python3 scripts/verify_install_parity.py --install ~/.claude/skills/spec
  python3 scripts/verify_install_parity.py --all-hosts
  python3 scripts/verify_install_parity.py --install <path> --json

Exits 0 when every audited install passes, 1 otherwise. Pure file I/O —
no network, no subprocess.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from payload_contract import (  # noqa: E402
    COPY_DIRS,
    CORE_EXCLUDED_DIRS,
    CORE_EXCLUDED_SCRIPTS,
    IGNORE_PATTERNS,
    PROFILE_VALUES,
    ROOT_FILES,
    SCRIPT_BLACKLIST,
    copy_dirs_for_profile,
    excluded_scripts_for_profile,
)

# The export-time distribution signature (scripts/skill_watermark.py): a
# MARK + PAD-bitstream + MARK line appended to NOTICE. The repo source
# stays clean of all three codepoints.
SIGNATURE_MARK = "\u2063"

# The file carrying the signature in an install (export_skill_package's
# NOTICE text + mark). Not a repo file, not a user sidecar — a known
# generated root file.
SIGNATURE_CARRIER = "NOTICE"

# Installer-owned artifacts expected inside an install (install.sh's
# ownership sidecar); not user sidecars, not payload, never a warning.
KNOWN_INSTALL_EXTRAS = {".spec-skill-install", SIGNATURE_CARRIER}
SIDEAR_VERSION_PATTERN = re.compile(r"^version=(\S+)", re.MULTILINE)

# The installer's host skill directories (install.sh backup_namespace table).
HOST_SKILL_DIRS: Tuple[str, ...] = (
    ".claude/skills",
    ".claude-desktop/skills",
    ".codex/skills",
    ".config/devin/skills",
    ".config/opencode/skills",
    ".cursor/skills",
    ".gemini/skills",
    ".grok/skills",
    ".hermes/skills",
    ".openclaw/skills",
    ".pi/agent/skills",
    ".zcode/skills",
)

VERSION_PATTERN = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def read_version(root: Path) -> Optional[str]:
    """The project version from a tree's pyproject.toml (None when unreadable)."""
    pyproject = root / "pyproject.toml"
    try:
        match = VERSION_PATTERN.search(pyproject.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def _ignored(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in IGNORE_PATTERNS)


def shipped_files(
    repo: Path,
    copy_dirs: Tuple[str, ...] = COPY_DIRS,
    excluded_scripts: frozenset = frozenset(),
) -> List[Path]:
    """The exporter's shipped set as repo-relative paths (mirrors the payload
    contract exactly: ROOT_FILES + non-blacklisted top-level scripts/*.py
    minus the profile's excluded scripts + the profile's COPY_DIRS
    recursively, minus ignore patterns)."""
    files: List[Path] = []
    for name in ROOT_FILES:
        candidate = repo / name
        if candidate.is_file():
            files.append(candidate)
    scripts_dir = repo / "scripts"
    if scripts_dir.is_dir():
        for entry in sorted(scripts_dir.iterdir()):
            if (
                entry.is_file()
                and entry.suffix == ".py"
                and entry.name not in SCRIPT_BLACKLIST
                and entry.name not in excluded_scripts
                and not entry.name.startswith("_")
                and not _ignored(entry.name)
            ):
                files.append(entry)
    for dirname in copy_dirs:
        base = repo / dirname
        if not base.is_dir():
            continue
        for entry in sorted(base.rglob("*")):
            if entry.is_file() and not any(_ignored(part) for part in entry.relative_to(repo).parts):
                files.append(entry)
    return files


def audit_install(install: Path, repo: Path, profile: str = "full") -> Dict[str, object]:
    """One install against the repo: expected asymmetries pass, everything
    else in the shipped set must be byte-identical."""
    failures: List[str] = []
    warnings: List[str] = []

    repo_version = read_version(repo)
    install_version = read_version(install)
    if repo_version is None:
        failures.append("repo pyproject.toml has no readable version")
    if install_version is None:
        failures.append("install pyproject.toml has no readable version")
    if repo_version is not None and install_version is not None and repo_version != install_version:
        failures.append(f"version drift: install {install_version} != repo {repo_version}")

    shipped = shipped_files(repo, copy_dirs_for_profile(profile), excluded_scripts_for_profile(profile))
    checked = 0
    for source in shipped:
        relative = source.relative_to(repo)
        target = install / relative
        if not target.is_file():
            failures.append(f"missing shipped file: {relative}")
            continue
        checked += 1
        if source.read_bytes() != target.read_bytes():
            failures.append(f"content drift: {relative}")

    # A blacklisted maintainer script inside an install is stale dirt from an
    # era before the blacklist entry — the upgrade path leaves it behind.
    for name in sorted(SCRIPT_BLACKLIST):
        if (install / "scripts" / name).is_file():
            failures.append(f"stale maintainer script in install: scripts/{name}")
    # Under core, a leftover chain script is the same upgrade dirt class as
    # an excluded component directory: a warning (the installer's whole-tree
    # replace removes it on the next core upgrade), never a failure.
    for name in sorted(CORE_EXCLUDED_SCRIPTS):
        if profile == "core" and (install / "scripts" / name).is_file():
            warnings.append(f"stale extension script in install (core profile): scripts/{name}")

    install_skill = (
        (install / "SKILL.md").read_text(encoding="utf-8", errors="replace") if (install / "SKILL.md").is_file() else ""
    )
    repo_skill = (
        (repo / "SKILL.md").read_text(encoding="utf-8", errors="replace") if (repo / "SKILL.md").is_file() else ""
    )
    if SIGNATURE_MARK in install_skill:
        failures.append(
            "install SKILL.md carries a distribution signature (\\u2063) — the signature "
            f"lives in {SIGNATURE_CARRIER} now (AUD-17); this install is stale, reinstall"
        )
    if SIGNATURE_MARK in repo_skill:
        failures.append("repo SKILL.md is signature-stamped — the source tree must stay clean")
    notice = install / SIGNATURE_CARRIER
    if notice.is_file():
        if SIGNATURE_MARK not in notice.read_text(encoding="utf-8", errors="replace"):
            failures.append(f"install {SIGNATURE_CARRIER} carries no distribution signature (\\u2063)")

    # Unknown extras (user sidecars) are warnings, not failures: an install
    # directory may legitimately hold user-owned notes next to the payload.
    # Installer-owned artifacts (.spec-skill-install) are expected and never
    # surface; its version= field is cross-checked against the install's
    # pyproject — a half-upgraded install (payload moved, sidecar stale)
    # fails here. Under core, files from excluded components in an install
    # are stale upgrade dirt from a full install, still a warning here (the
    # installer's whole-tree replace removes them on the next core upgrade).
    shipped_set = {source.relative_to(repo).as_posix() for source in shipped}
    for entry in sorted(install.rglob("*")):
        if not entry.is_file():
            continue
        relative = entry.relative_to(install)
        if any(_ignored(part) for part in relative.parts):
            continue
        posix = relative.as_posix()
        if posix in shipped_set or posix in KNOWN_INSTALL_EXTRAS:
            continue
        warnings.append(f"extra file in install (user-owned?): {posix}")

    sidecar = install / ".spec-skill-install"
    if sidecar.is_file():
        sidecar_match = SIDEAR_VERSION_PATTERN.search(sidecar.read_text(encoding="utf-8", errors="replace"))
        if sidecar_match and install_version is not None and sidecar_match.group(1) != install_version:
            failures.append(
                f"sidecar version drift: .spec-skill-install says {sidecar_match.group(1)}, "
                f"payload says {install_version}"
            )

    return {
        "path": str(install),
        "ok": not failures,
        "version": install_version,
        "repo_version": repo_version,
        "profile": profile,
        "checked": checked,
        "failures": failures,
        "warnings": warnings,
    }


def discover_host_installs(home: Path) -> List[Path]:
    installs: List[Path] = []
    for relative in HOST_SKILL_DIRS:
        candidate = home / relative / "spec"
        if (candidate / "SKILL.md").is_file() and (candidate / "scripts").is_dir():
            installs.append(candidate)
    return installs


def _render(report: Dict[str, object]) -> str:
    lines = [f"spec install parity: {report['path']}"]
    version, repo_version = report.get("version"), report.get("repo_version")
    if version is not None and repo_version is not None:
        lines.append(f"  version: {version} {'==' if version == repo_version else '!='} {repo_version}")
    lines.append(f"  shipped: {report['checked']} files byte-checked")
    lines.append(f"  signature: {'stamped' if not any('signature' in f for f in report['failures']) else 'BROKEN'}")
    for failure in report["failures"]:
        lines.append(f"  FAIL {failure}")
    for warning in report["warnings"]:
        lines.append(f"  warn {warning}")
    lines.append("PASS" if report["ok"] else "FAIL")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Verify installed spec skill copies against this repository.")
    parser.add_argument(
        "--install", action="append", default=[], metavar="PATH", help="installed skill directory (repeatable)"
    )
    parser.add_argument("--all-hosts", action="store_true", help="audit every discovered host install")
    parser.add_argument(
        "--repo", default=str(Path(__file__).resolve().parent.parent), help="repository root (default: this checkout)"
    )
    parser.add_argument(
        "--profile",
        choices=PROFILE_VALUES,
        default="full",
        help=(
            "component profile of the audited installs (default: full). Pass core for "
            "INSTALL_PROFILE=core installs: the expected set drops "
            + ", ".join(CORE_EXCLUDED_DIRS)
            + " and the chain-family scripts ("
            + ", ".join(sorted(CORE_EXCLUDED_SCRIPTS))
            + ")"
        ),
    )
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not (repo / "SKILL.md").is_file():
        print(f"error: not a spec repository: {repo}", file=sys.stderr)
        return 2

    installs: List[Path] = [Path(p).expanduser() for p in args.install]
    if args.all_hosts:
        installs.extend(discover_host_installs(Path.home()))
    seen: set = set()
    unique: List[Path] = []
    for install in installs:
        resolved = str(install.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(install)
    if not unique:
        print("error: no installs to audit (pass --install PATH or --all-hosts)", file=sys.stderr)
        return 2

    reports = []
    for install in unique:
        if not (install / "SKILL.md").is_file():
            reports.append(
                {
                    "path": str(install),
                    "ok": False,
                    "checked": 0,
                    "failures": ["not an installed skill directory (no SKILL.md)"],
                    "warnings": [],
                }
            )
            continue
        reports.append(audit_install(install, repo, profile=args.profile))

    if args.json:
        print(json.dumps({"repo": str(repo), "installs": reports}, indent=2, sort_keys=True))
    else:
        for report in reports:
            print(_render(report))
    return 0 if all(report["ok"] for report in reports) else 1


if __name__ == "__main__":
    sys.exit(main())

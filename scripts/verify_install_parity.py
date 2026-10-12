"""Verify an installed spec skill copy against this repository checkout.

The installer stages the runtime payload through the exporter
(``export_skill_package.py``), so three asymmetries between the repo and an
installed copy are BY DESIGN and this tool pins them as PASS conditions —
an install-parity audit should not re-derive them, and must never mistake
the signature for dirt and strip it:

1. The installed ``SKILL.md`` carries the invisible ``\\u2063`` distribution
   signature stamped at export time; the repository source stays clean.
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

from payload_contract import COPY_DIRS, IGNORE_PATTERNS, ROOT_FILES, SCRIPT_BLACKLIST  # noqa: E402

# The export-time distribution signature (scripts/skill_watermark.py): a
# MARK + PAD-bitstream + MARK line appended to SKILL.md. The repo source
# stays clean of all three codepoints.
SIGNATURE_MARK = "\u2063"
SIGNATURE_PAD = "\u2060\u200b"
SIGNATURE_CHARS = SIGNATURE_MARK + SIGNATURE_PAD

# Installer-owned artifacts expected inside an install (install.sh's
# ownership sidecar); not user sidecars, not payload, never a warning.
KNOWN_INSTALL_EXTRAS = {".spec-skill-install"}
SIDEAR_VERSION_PATTERN = re.compile(r"^version=(\S+)", re.MULTILINE)

# The installer's host skill directories (install.sh backup_namespace table).
HOST_SKILL_DIRS: Tuple[str, ...] = (
    ".claude/skills",
    ".claude-desktop/skills",
    ".codex/skills",
    ".config/opencode/skills",
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


def shipped_files(repo: Path) -> List[Path]:
    """The exporter's shipped set as repo-relative paths (mirrors the payload
    contract exactly: ROOT_FILES + non-blacklisted top-level scripts/*.py +
    COPY_DIRS recursively, minus ignore patterns)."""
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
                and not entry.name.startswith("_")
                and not _ignored(entry.name)
            ):
                files.append(entry)
    for dirname in COPY_DIRS:
        base = repo / dirname
        if not base.is_dir():
            continue
        for entry in sorted(base.rglob("*")):
            if entry.is_file() and not any(_ignored(part) for part in entry.relative_to(repo).parts):
                files.append(entry)
    return files


def audit_install(install: Path, repo: Path) -> Dict[str, object]:
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

    shipped = shipped_files(repo)
    checked = 0
    for source in shipped:
        relative = source.relative_to(repo)
        target = install / relative
        if not target.is_file():
            failures.append(f"missing shipped file: {relative}")
            continue
        checked += 1
        if relative.as_posix() == "SKILL.md":
            # the one file allowed to differ: the export-time signature. The
            # install copy must equal the repo copy with every signature
            # codepoint filtered out (MARK and PAD bitstream — the same strip
            # skill_watermark itself uses); any other difference is real
            # content drift.
            install_text = "".join(
                ch for ch in target.read_text(encoding="utf-8", errors="replace") if ch not in SIGNATURE_CHARS
            )
            repo_text = source.read_text(encoding="utf-8", errors="replace")
            if install_text != repo_text:
                failures.append(f"content drift: {relative}")
        elif source.read_bytes() != target.read_bytes():
            failures.append(f"content drift: {relative}")

    # A blacklisted maintainer script inside an install is stale dirt from an
    # era before the blacklist entry — the upgrade path leaves it behind.
    for name in sorted(SCRIPT_BLACKLIST):
        if (install / "scripts" / name).is_file():
            failures.append(f"stale maintainer script in install: scripts/{name}")

    install_skill = (
        (install / "SKILL.md").read_text(encoding="utf-8", errors="replace") if (install / "SKILL.md").is_file() else ""
    )
    repo_skill = (
        (repo / "SKILL.md").read_text(encoding="utf-8", errors="replace") if (repo / "SKILL.md").is_file() else ""
    )
    if SIGNATURE_MARK not in install_skill:
        failures.append("install SKILL.md carries no distribution signature (\\u2063)")
    if SIGNATURE_MARK in repo_skill:
        failures.append("repo SKILL.md is signature-stamped — the source tree must stay clean")

    # Unknown extras (user sidecars) are warnings, not failures: an install
    # directory may legitimately hold user-owned notes next to the payload.
    # Installer-owned artifacts (.spec-skill-install) are expected and never
    # surface; its version= field is cross-checked against the install's
    # pyproject — a half-upgraded install (payload moved, sidecar stale)
    # fails here.
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
        reports.append(audit_install(install, repo))

    if args.json:
        print(json.dumps({"repo": str(repo), "installs": reports}, indent=2, sort_keys=True))
    else:
        for report in reports:
            print(_render(report))
    return 0 if all(report["ok"] for report in reports) else 1


if __name__ == "__main__":
    sys.exit(main())

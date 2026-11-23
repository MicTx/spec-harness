#!/usr/bin/env python3
"""Update installer-owned Spec Harness skills from a verified stable release."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

REPOSITORY = "MicTx/spec-harness"
RELEASES = f"https://github.com/{REPOSITORY}/releases"
DOWNLOADS = f"https://github.com/{REPOSITORY}/releases/download"
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
MAX_METADATA = 2 * 1024 * 1024
MAX_ARCHIVE = 50 * 1024 * 1024
MAX_EXTRACTED = 100 * 1024 * 1024


class UpdateError(Exception):
    pass


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        if urllib.parse.urlsplit(new_url).scheme.lower() != "https":
            raise UpdateError("refusing an insecure download redirect")
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def fetch(url: str, limit: int, *, head: bool = False) -> tuple[bytes, str]:
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise UpdateError("release downloads must use HTTPS")
    request = urllib.request.Request(
        url,
        method="HEAD" if head else "GET",
        headers={"User-Agent": "spec-harness-updater"},
    )
    try:
        with urllib.request.build_opener(HTTPSRedirect()).open(request, timeout=30) as response:
            data = b"" if head else response.read(limit + 1)
            resolved = response.geturl()
    except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
        curl = shutil.which("curl")
        if not curl:
            raise UpdateError(f"could not download {url}: {exc}") from exc
        with tempfile.TemporaryDirectory(prefix="spec-harness-download-") as temporary:
            output = Path(temporary) / "response"
            command = [
                curl,
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--connect-timeout",
                "15",
                "--max-time",
                "60",
                "--max-filesize",
                str(limit),
                "--output",
                str(output),
                "--write-out",
                "%{url_effective}",
            ]
            if head:
                command.append("--head")
            try:
                result = subprocess.run([*command, url], capture_output=True, text=True, timeout=65, check=False)
                if result.returncode:
                    raise UpdateError(f"could not download {url}: {result.stderr.strip() or 'curl failed'}")
                if output.stat().st_size > limit and not head:
                    raise UpdateError(f"download exceeds the size limit: {url}")
                data = b"" if head else output.read_bytes()
                resolved = result.stdout.strip()
            except (OSError, subprocess.TimeoutExpired) as curl_exc:
                raise UpdateError(f"could not download {url}: {curl_exc}") from curl_exc
    if len(data) > limit:
        raise UpdateError(f"download exceeds the size limit: {url}")
    return data, resolved


def download(url: str, limit: int) -> bytes:
    return fetch(url, limit)[0]


def host_roots() -> dict[str, Path]:
    home = Path.home()

    def root(variable: str, default: Path) -> Path:
        return Path(os.environ.get(variable) or default)

    dsh_home = root("DSH_HOME", home / ".dsh")
    hermes_default = Path(os.environ["LOCALAPPDATA"]) / "hermes" if os.environ.get("LOCALAPPDATA") else home / ".hermes"
    hermes_home = root("HERMES_HOME", hermes_default)
    xdg_home = root("XDG_CONFIG_HOME", home / ".config")
    pi_home = root("PI_DIR", home / ".pi")
    claude_default = root("INSTALL_DIR", home / ".claude" / "skills")
    return {
        "claude": root("CLAUDE_SKILLS_DIR", claude_default),
        "pi": root("PI_SKILLS_DIR", pi_home / "agent" / "skills"),
        "claude-desktop": root("CLAUDE_DESKTOP_SKILLS_DIR", home / ".claude-desktop" / "skills"),
        "agents": root("AGENTS_SKILLS_DIR", home / ".agents" / "skills"),
        "codex": root("CODEX_SKILLS_DIR", home / ".codex" / "skills"),
        "devin": root("DEVIN_SKILLS_DIR", home / ".config" / "devin" / "skills"),
        "cursor": root("CURSOR_SKILLS_DIR", home / ".cursor" / "skills"),
        "gemini": root("GEMINI_SKILLS_DIR", home / ".gemini" / "skills"),
        "grok": root("GROK_SKILLS_DIR", home / ".grok" / "skills"),
        "opencode": root("OPENCODE_SKILLS_DIR", xdg_home / "opencode" / "skills"),
        "openclaw": root("OPENCLAW_SKILLS_DIR", home / ".openclaw" / "skills"),
        "hermes": root("HERMES_SKILLS_DIR", hermes_home / "skills"),
        "zcode": root("ZCODE_SKILLS_DIR", home / ".zcode" / "skills"),
        "dsh": root("DSH_SKILLS_DIR", dsh_home / "skills"),
    }


def installed_hosts() -> list[dict[str, str]]:
    installations = []
    for host, root in host_roots().items():
        marker = root / "spec" / ".spec-skill-install"
        if marker.is_symlink() or not marker.is_file():
            continue
        try:
            fields = dict(line.split("=", 1) for line in marker.read_text(encoding="utf-8").splitlines() if "=" in line)
        except (OSError, UnicodeError) as exc:
            raise UpdateError(f"cannot read installation marker: {marker}") from exc
        if fields.get("installed_by") != "spec/install.sh":
            continue
        profile = fields.get("profile") or "core"
        tier = fields.get("tier") or "full"
        if profile not in ("core", "full") or tier not in ("full", "compact", "aggressive"):
            raise UpdateError(f"invalid profile or prompt tier in installation marker: {marker}")
        installations.append(
            {"host": host, "version": fields.get("version") or "unknown", "profile": profile, "tier": tier}
        )
    return installations


def release_info(version: str | None) -> tuple[str, str]:
    if version is not None and not VERSION_PATTERN.fullmatch(version):
        raise UpdateError("version must be a stable version such as 2.0.0")
    url = f"{RELEASES}/tag/v{version}" if version else f"{RELEASES}/latest"
    _data, resolved_url = fetch(url, MAX_METADATA, head=True)
    resolved = urllib.parse.urlsplit(resolved_url)
    prefix = f"/{REPOSITORY}/releases/tag/v"
    if (
        resolved.scheme.lower() != "https"
        or resolved.netloc.lower() != "github.com"
        or not resolved.path.startswith(prefix)
        or resolved.query
        or resolved.fragment
    ):
        raise UpdateError("release page redirected to an unexpected address")
    found_version = resolved.path[len(prefix) :]
    if not VERSION_PATTERN.fullmatch(found_version):
        raise UpdateError("release tag is not a stable vMAJOR.MINOR.PATCH version")
    if version is not None and version != found_version:
        raise UpdateError("release tag does not match the requested version")
    archive_name = f"spec-harness-{found_version}.tar.gz"
    return found_version, archive_name


def verified_archive(version: str, archive_name: str) -> bytes:
    base = f"{DOWNLOADS}/v{version}"
    try:
        checksum_text = download(f"{base}/SHA256SUMS", MAX_METADATA).decode("utf-8")
    except UnicodeError as exc:
        raise UpdateError("SHA256SUMS is not valid UTF-8") from exc
    matches = []
    for line in checksum_text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^\s]+)", line)
        if match and match.group(2) == archive_name:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise UpdateError(f"SHA256SUMS must contain exactly one entry for {archive_name}")
    data = download(f"{base}/{archive_name}", MAX_ARCHIVE)
    if hashlib.sha256(data).hexdigest() != matches[0]:
        raise UpdateError("release archive SHA-256 does not match SHA256SUMS")
    return data


def extract_archive(data: bytes, version: str, destination: Path) -> Path:
    expected_root = f"spec-harness-{version}"
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > 10000:
                raise UpdateError("release archive contains too many entries")
            for member in members:
                path = PurePosixPath(member.name)
                parts = member.name.rstrip("/").split("/")
                if (
                    not parts
                    or parts[0] != expected_root
                    or any(part in ("", ".", "..") or ":" in part for part in parts)
                    or path.is_absolute()
                    or "\\" in member.name
                    or not (member.isdir() or member.isfile())
                    or member.name.rstrip("/") in seen
                ):
                    raise UpdateError(f"unsafe release archive entry: {member.name}")
                seen.add(member.name.rstrip("/"))
                total += member.size
                if total > MAX_EXTRACTED:
                    raise UpdateError("release archive exceeds the extracted size limit")
            for member in members:
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                content = archive.extractfile(member)
                if content is None:
                    raise UpdateError(f"cannot read release archive entry: {member.name}")
                with content, target.open("xb") as output:
                    shutil.copyfileobj(content, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (OSError, tarfile.TarError) as exc:
        raise UpdateError(f"cannot extract release archive: {exc}") from exc
    root = destination / expected_root
    try:
        version_file = (root / "VERSION").read_text(encoding="utf-8").strip()
        build_info = dict(
            line.split("=", 1) for line in (root / "BUILD_INFO").read_text(encoding="utf-8").splitlines() if "=" in line
        )
        project = (root / "pyproject.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise UpdateError("release archive is missing its version or build information") from exc
    project_version = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', project, re.MULTILINE)
    if (
        version_file != version
        or build_info.get("version") != version
        or project_version is None
        or project_version.group(1) != version
        or not (root / "install.sh").is_file()
        or not (root / "SKILL.md").is_file()
        or not (root / "scripts").is_dir()
    ):
        raise UpdateError("release archive contents do not match the selected version")
    return root


def run_installer(root: Path, installations: list[dict[str, str]], dry_run: bool) -> None:
    groups: dict[tuple[str, str], list[str]] = {}
    for item in installations:
        groups.setdefault((item["profile"], item["tier"]), []).append(item["host"])
    owners: dict[Path, tuple[str, str]] = {}
    roots = host_roots()
    for item in installations:
        target = (roots[item["host"]] / "spec").resolve()
        group = (item["profile"], item["tier"])
        if target in owners and owners[target] != group:
            raise UpdateError("hosts sharing a skill must use the same profile and prompt tier")
        owners[target] = group
    release_installer = root / "install.sh"
    try:
        supports_preflight = "\nSPEC_UPDATE_PREFLIGHT=1\n" in release_installer.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise UpdateError("cannot read the release installer") from exc
    updater_path = Path(__file__).resolve()
    fallback = updater_path.parent / "install.sh"
    if not fallback.is_file():
        fallback = updater_path.parent.parent / "install.sh"
    if not supports_preflight and not fallback.is_file():
        raise UpdateError("this older release needs the updater's current installer for a safe preflight")
    if not supports_preflight and os.environ.get("FORCE") == "1":
        raise UpdateError("this older release cannot safely update custom roots; install it manually instead")

    def invoke(profile: str, tier: str, hosts: list[str], *, preview: bool, preflight: bool) -> None:
        environment = os.environ.copy()
        for name in ("SOURCE_DIR", "REPO_URL", "REPO_REF"):
            environment.pop(name, None)
        environment.update(
            INSTALL_HOSTS=",".join(hosts),
            INSTALL_PROFILE=profile,
            SPEC_PROMPT_TIER=tier,
            SKILL_NAME="spec",
            FORCE="1" if os.environ.get("FORCE") == "1" else "0",
            KEEP_TEMP="0",
            DRY_RUN="1" if preview else "0",
            PREFLIGHT_ONLY="1" if preflight else "0",
            SPEC_UPDATE_OWNED_ONLY="1",
        )
        installer = release_installer
        if not supports_preflight and (preview or preflight):
            installer = fallback
            environment["SOURCE_DIR"] = str(root)
        try:
            result = subprocess.run(["bash", str(installer)], env=environment, check=False)
        except OSError as exc:
            raise UpdateError(f"cannot run the installer: {exc}") from exc
        if result.returncode:
            raise UpdateError(f"installer failed for {', '.join(hosts)}; review its output and retry")

    if dry_run:
        for (profile, tier), hosts in groups.items():
            invoke(profile, tier, hosts, preview=True, preflight=False)
    for (profile, tier), hosts in groups.items():
        invoke(profile, tier, hosts, preview=False, preflight=True)
    if not dry_run:
        for (profile, tier), hosts in groups.items():
            invoke(profile, tier, hosts, preview=False, preflight=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="store_true", help="show installed host versions")
    commands = parser.add_subparsers(dest="command")
    update = commands.add_parser("update", help="check or install a stable release")
    mode = update.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check available versions without installing")
    mode.add_argument("--dry-run", action="store_true", help="download and preview without changing installations")
    update.add_argument("--version", dest="release_version", help="select a stable version, including for rollback")
    update.add_argument("--reinstall", action="store_true", help="reinstall even when already on this version")
    args = parser.parse_args(argv)
    if not args.version and args.command is None:
        parser.print_help()
        return 0
    try:
        installations = installed_hosts()
        if not installations:
            raise UpdateError("no installer-owned Spec Harness installation found; run install.sh first")
        for item in installations:
            print(f"{item['host']}: {item['version']} (profile {item['profile']}, tier {item['tier']})", flush=True)
        if args.version:
            return 0
        version, archive_name = release_info(args.release_version)
        print(f"Selected stable release: {version}", flush=True)
        if args.check:
            if all(item["version"] == version for item in installations):
                print("All installer-owned hosts are current.")
            else:
                hint = f" --version {version}" if args.release_version else ""
                print(f"Installed versions differ. Run spec-harness update{hint} to install the selected release.")
            return 0
        if args.release_version is None:
            for item in installations:
                if VERSION_PATTERN.fullmatch(item["version"]) and tuple(map(int, item["version"].split("."))) > tuple(
                    map(int, version.split("."))
                ):
                    raise UpdateError("an installed version is newer; select --version explicitly to roll back")
        if not args.reinstall and not args.dry_run and all(item["version"] == version for item in installations):
            print("All installer-owned hosts are already current. Use --reinstall to repair them.")
            return 0
        data = verified_archive(version, archive_name)
        with tempfile.TemporaryDirectory(prefix="spec-harness-update-") as temporary:
            root = extract_archive(data, version, Path(temporary))
            run_installer(root, installations, args.dry_run)
        print("Update preview complete." if args.dry_run else f"Updated Spec Harness to {version}.")
        if not args.dry_run:
            print("Restart your agent session to load the updated skill and hooks.")
        return 0
    except UpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

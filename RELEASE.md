# Releases

Spec Harness releases answer two different needs: a tagged source revision for reproducible installs and a checked archive for users who do not need Git. Releases are published on [GitHub Releases](https://github.com/MicTx/spec-harness/releases) with notes and SHA-256 checksums.

## Choose a Version

- **Stable:** install from [GitHub Releases](https://github.com/MicTx/spec-harness/releases) or run `spec-harness update`. The `v3.0.0` archive includes the quick path, evidence-driven Stop, and legacy Git-hook retirement.
- **Current source:** install from `main` or pin a full public commit SHA. The source reports the version in `pyproject.toml`, but a source revision is not necessarily identical to the released archive. `spec-harness update` does not fetch `main`; `--reinstall` installs the selected release again.
- Check the [changelog](CHANGELOG.md) before switching from a release to source.

## Versioning Policy

Spec Harness follows [Semantic Versioning](https://semver.org/). The deciding question for a bump is not "did behavior change" but "did a compatibility surface move":

- **PATCH** — a backwards-compatible bug fix. No new user-facing surface (commands, flags, task-package file layout, export layout, event/audit contract). Correcting wrong behavior counts as a fix, even though behavior changes.
- **MINOR** — a backwards-compatible addition to the user-facing surface (a new command, flag, or event key — an add-only vocabulary extension stays compatible).
- **MAJOR** — an incompatible layout or behavior reversal; breaking entries are marked `BREAKING` in the release notes.

Pacing: a minor is not minted for a single round. User-visible changes accumulate to the next natural release window (the close of a stage that changed user-visible behavior); pure fixes may ship as a patch at any time.

Tasks and records from a previous format keep their original semantics. For a frozen pre-v3 archive, use a matching historical toolkit; do not edit the archive to make it pass a current gate.

## Install from a Release Archive

Download the archive and checksum file from the release page:

    VERSION=3.0.0
    curl -LO "https://github.com/MicTx/spec-harness/releases/download/v$VERSION/spec-harness-$VERSION.tar.gz"
    curl -LO "https://github.com/MicTx/spec-harness/releases/download/v$VERSION/SHA256SUMS"
    sha256sum -c SHA256SUMS --ignore-missing
    tar xzf "spec-harness-$VERSION.tar.gz"
    cd "spec-harness-$VERSION"
    bash install.sh

On macOS, use shasum -a 256 -c SHA256SUMS when sha256sum is unavailable. On Windows, use Get-FileHash and compare the result with SHA256SUMS before extracting the ZIP archive.

The archive root contains the local skill installer.

## Install from Source

    git clone https://github.com/MicTx/spec-harness.git
    cd spec-harness
    DRY_RUN=1 bash install.sh
    bash install.sh

This installs the checked-out source, not a release archive. To pin it, run `git checkout --detach <full-public-commit-sha>` before the preview. Preview lists installation targets; `PREFLIGHT_ONLY=1 bash install.sh` also evaluates actual installer preconditions before writing. Restart or reload the host after installation. Some hosts require hook review or trust approval; verify the host actually loaded the skill and hooks.

When moving to current source from an older release, `spec.py doctor` reports old Spec-owned Git hooks and `spec.py doctor --fix` retires them by renaming them. It does not remove or change other Git hooks.

## Upgrade and Roll Back

After installing a version with the update command, run:

    spec-harness --version
    spec-harness update --check
    spec-harness update --dry-run
    spec-harness update

The updater discovers installer-owned host skills, checks the latest stable release, downloads its archive and `SHA256SUMS` over HTTPS, verifies the archive, and calls the release's installer. It preserves each host's profile; the installer backs up replaced files and preserves unrelated settings. Restart your agent session after updating. `--check` reads versions without downloading the archive; `--dry-run` downloads and verifies it, then previews and preflights the installation without changing user files. `spec-harness update --reinstall` repairs an installation even if its version already matches.

By default `--source auto` tries GitHub first and falls back to the third-party `gh-proxy.com` mirror for release discovery and downloads. Select `--source china` or `--source github` to use only that channel; `SPEC_UPDATE_SOURCE=china` sets the default, and an explicit flag takes precedence. The mirror is not operated by Spec Harness: its metadata, checksum file and archive share its trust boundary. SHA-256 detects transfer corruption but cannot authenticate a compromised mirror; choose GitHub directly if you require the GitHub transport trust boundary.

If `~/.local/bin` is not on `PATH`, run `~/.local/bin/spec-harness update`. If that name already belongs to another program, the installer leaves it untouched; use `python3 <installed-skill>/updater/update_release.py update` instead. An older installation needs a one-time `bash install.sh` from a release or checkout that includes this updater. Windows users can run the command from Git Bash with Python and Bash on `PATH`.

Custom skill roots must be supplied using the same environment variables as installation; set `FORCE=1` to allow those roots. The updater still refuses to replace unmarked user files. A network, checksum, or preflight error stops before installation. SHA-256 downloaded from the same release channel verifies transfer integrity; it is not an independent publisher signature.

To install a specific stable version or roll back explicitly:

    spec-harness update --version 2.0.0

The updater never automatically downgrades a newer installation. As an alternative, rerun the installer from a selected tag or release archive:

    git fetch --tags origin
    git checkout --detach v3.0.0
    bash install.sh

## Checksums and Provenance

SHA256SUMS covers the release archives. Verify it before extraction. The archive also contains VERSION and BUILD_INFO; the latter records the source revision and the canonical runtime payload digest.

## Compatibility

Patch releases preserve existing command aliases and task-package formats whenever possible. Changes to command aliases, required task-package files, exported layouts, or behavior contracts are called out in the changelog and version policy.

## Need Help?

For installation or upgrade problems, open a [support issue](https://github.com/MicTx/spec-harness/issues/new/choose) with the version, operating system, installation method, and the shortest useful error output. Report security issues through [SECURITY.md](SECURITY.md).

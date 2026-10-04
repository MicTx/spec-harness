# Releases

Spec Harness releases answer two different needs: a tagged source revision for reproducible installs and a checked archive for users who do not need Git. Releases are published on [GitHub Releases](https://github.com/MicTx/spec-harness/releases) with notes and SHA-256 checksums.

## Choose a Version

- Use the latest tagged release for a stable installation.
- Use a full commit SHA when you need a reproducible source checkout.
- Read `CHANGELOG.md` before upgrading across a behavior or layout change; aliases, task-package files, export layout, and behavior contracts are compatibility surfaces.

## Install from a Release Archive

Download the archive and checksum file from the release page:

    VERSION=0.13.14
    curl -LO "https://github.com/MicTx/spec-harness/releases/download/v$VERSION/spec-harness-$VERSION.tar.gz"
    curl -LO "https://github.com/MicTx/spec-harness/releases/download/v$VERSION/SHA256SUMS"
    sha256sum -c SHA256SUMS --ignore-missing
    tar xzf "spec-harness-$VERSION.tar.gz"
    cd "spec-harness-$VERSION"
    bash install.sh

On macOS, use shasum -a 256 -c SHA256SUMS when sha256sum is unavailable. On Windows, use Get-FileHash and compare the result with SHA256SUMS before extracting the ZIP archive.

The archive root contains the local skill installer. server/install.sh is optional and only applies when the self-hosted server adapter is deployed.

## Install from Source

    git clone https://github.com/MicTx/spec-harness.git
    cd spec-harness
    git checkout v0.13.14
    bash install.sh

A tagged checkout is preferable to an unpinned main checkout when reproducibility matters. Replace the tag with a full commit SHA for an exact source revision.

## Upgrade and Roll Back

Keep the installed host directory backed up when changing versions. Re-run the installer from the selected release or checkout. It preserves user-owned settings and refuses to overwrite an unrelated skill directory without an explicit override.

To roll back, install a previous tag or release archive:

    git fetch --tags origin
    git checkout v0.13.13
    bash install.sh

## Checksums and Provenance

SHA256SUMS covers the release archives. Verify it before extraction. The archive also contains VERSION and BUILD_INFO; the latter records the source revision and the canonical runtime payload digest.

## Compatibility

Patch releases preserve existing command aliases and task-package formats whenever possible. Changes to command aliases, required task-package files, exported layouts, or behavior contracts are called out in the changelog and version policy.

## Need Help?

For installation or upgrade problems, open a [support issue](https://github.com/MicTx/spec-harness/issues/new/choose) with the version, operating system, installation method, and the shortest useful error output. Report security issues through [SECURITY.md](SECURITY.md).

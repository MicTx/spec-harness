# Release Guide

This project uses Semantic Versioning and automated tag-triggered releases.

## Versioning

Versions live in `pyproject.toml` as the single source of truth.

| Bump | When |
| --- | --- |
| **Patch** `v0.x.y → v0.x.y+1` | documentation fixes, metadata alignment, non-breaking script fixes |
| **Minor** `v0.x.y → v0.x+1.0` | new helper-script capabilities, new workflow support, backward-compatible fields |
| **Major** `v0.x.y → v1.0.0` | breaking changes to command aliases, task-package structure, exported layout, or behavior contracts |

Until `v1.0.0`, backward compatibility is still preferred. Any intentional break should be called out explicitly in `CHANGELOG.md`.

## Build Artifacts

`scripts/build_release.py` produces standardized distributable artifacts in an output directory outside the source tree (default `../spec-harness-dist/`):

| Artifact | Description |
| --- | --- |
| `spec-harness-{version}.tar.gz` | Full runtime package for Unix (preserves permissions) |
| `spec-harness-{version}.zip` | Full runtime package for Windows |
| `SHA256SUMS` | SHA-256 checksums for release archives (the checksum file and release notes are excluded) |
| `RELEASE_NOTES.md` | Changelog excerpt for the matching version |

Each archive contains a `VERSION` file (version string) and `BUILD_INFO` file (version, Git SHA, deterministic canonical `runtime_sha256`, commit-derived build timestamp). The archive-root `install.sh` is the local skill installer; the optional `server/install.sh` is a separate self-hosted service deployment entrypoint.

### Building Locally

```bash
# Full build with pre-build checks (py_compile, smoke, pytest, ruff)
python3 scripts/build_release.py

# Skip checks for a fast rebuild
python3 scripts/build_release.py --skip-checks

# Verify a specific version (used by CI release workflow)
python3 scripts/build_release.py --expect-version 0.3.0

# Custom output directory
python3 scripts/build_release.py --output /tmp/my-dist
```

### Verifying Checksums

```bash
(cd ../spec-harness-dist && sha256sum -c SHA256SUMS)
```

### Installing from an Archive

```bash
# Unix
tar xzf spec-harness-0.11.0.tar.gz
cd spec-harness-0.11.0
./install.sh

# Windows
Expand-Archive spec-harness-0.11.0.zip
cd spec-harness-0.11.0
bash install.sh
```

### Check-Gate Documentation Drift

Before release, confirm `/spec:check` documentation still matches `scripts/check_spec_package.py`:

- `SKILL.md`, README files, `references/commands.md`, `references/output-contracts.md`, `references/templates.md`, `references/storage-and-archive.md`, and `agents/openai.yaml` describe the same base gates.
- Evidence rules mention non-placeholder script/test/build command proof.
- Optional gates are documented as marker-driven: cross-artifact consistency, structure/document credibility, branch and multi-agent governance, orchestration governance, and behavior effects.
- Installer-generated `/spec:check` command text remains consistent with the same rules.

### Public GitHub Mirror

The private development repository remains the development source. Build a filtered public tree before publishing:

```bash
python3 scripts/export_public_repo.py --output /tmp/spec-harness-public --tag v0.13.9
python3 scripts/export_public_repo.py --output /tmp/spec-harness-public --repo MicTx/spec-harness --tag v0.13.9 --publish
```

The exporter excludes `.spec/`, `.maintainer/`, `.zcode/`, `.agents/`, private workflow files, and release working artifacts. `--publish` is the only mode that writes to GitHub.

## Release Process

### 1. Prepare

1. Confirm `CHANGELOG.md` reflects the release candidate state under `## [Unreleased]`.
2. Run the full verification suite:

```bash
python3 -m pip install -e '.[dev]'
python3 -m compileall -q scripts server hooks slots tests book
bash -n server/install.sh
python3 scripts/smoke_test_spec_skill.py
python3 scripts/slot_registry.py validate
python3 -m pytest -q
ruff check scripts/ server/ hooks/ slots/ tests/ book/
ruff format --check scripts/ server/ hooks/ slots/ tests/ book/
```

### 2. Version Bump

1. Update `version` in `pyproject.toml` to the target version (e.g. `0.3.0`).
2. Fold `## [Unreleased]` entries in `CHANGELOG.md` into a versioned section `## [0.3.0] - YYYY-MM-DD`.
3. Commit:

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump version to 0.3.0"
```

### 3. Tag and Push

```bash
git tag v0.3.0
git push origin main --tags
```

Pushing the `v*` tag triggers the GitHub Actions release workflow (`.github/workflows/release.yml`), which:

1. Runs the full test suite.
2. Builds artifacts via `scripts/build_release.py` (with `--expect-version` to verify tag/version match).
3. Verifies checksums.
4. Creates a GitHub Release with all artifacts attached and changelog notes.

### 4. Verify

- Check the GitHub Release page for the new release.
- Download artifacts and verify: `sha256sum -c SHA256SUMS`.
- Compare the tracked `release/` payload to a fresh `build_release.py` output at content level (unpacked, member by member; `tests/test_release_payload.py` enforces this). `BUILD_INFO` is the single byte-comparison exemption and is validated semantically instead: `runtime_sha256` must match across both sides (the freshness anchor over all payload content), while `git_sha`/`build_time` anchor the build to the real commit it was stamped from and intentionally lag HEAD — they are a build-point provenance record, not a payload-freshness claim.
- Test installation from the archive in a clean environment.
- Confirm the archive-root `install.sh` is executable in the Unix artifact; `server/install.sh` is the optional self-hosted deployment script and is run explicitly with `bash server/install.sh`.
- Verify the exported runtime package still includes all required files (see checklist below).
- Run a spot `/spec:check` against a completed task package and confirm the output sections match `references/output-contracts.md`.

### Exported Runtime Package Checklist

- `SKILL.md`
- root `install.sh`
- `agents/openai.yaml`
- `references/`
- `scripts/init_spec_package.py`
- `scripts/route_spec_package.py`
- `scripts/report_spec_package.py`
- `scripts/check_spec_package.py`
- `scripts/complete_spec_package.py`
- `scripts/push_spec_package.py`
- `scripts/smoke_test_spec_skill.py`
- `scripts/issue_closure_support.py`
- `scripts/spec_package_support.py`
- `server/server.py`
- `server/install.sh`
- `server/README.md`
- `VERSION` (build stamp)
- `BUILD_INFO` (build stamp)

## Breaking Change Policy

Treat the following as release-note-worthy even before `v1.0.0`:

- Renaming `spec:*` aliases
- Changing slash-command mappings
- Changing required task-package files
- Changing exported runtime layout
- Adding a new mandatory runtime dependency

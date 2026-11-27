# Spec Harness

Two-phase task packages for AI coding agents: thick planning, silent execution, verifiable delivery.

[![Public CI](https://github.com/MicTx/spec-harness/actions/workflows/ci-public.yml/badge.svg)](https://github.com/MicTx/spec-harness/actions/workflows/ci-public.yml) [![Release](https://img.shields.io/github/v/release/MicTx/spec-harness)](https://github.com/MicTx/spec-harness/releases) [![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE) [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[中文](README.md) · [Documentation](docs/README-en.md) · [Quick start](#quick-start)

## Contents

- [Install](#install)
- [Update](#update)
- [Quick start](#quick-start)
- [Runtime payload](#runtime-payload)
- [Verify and maintain](#verify-and-maintain)
- [License](#license)

A package records the goal, scope, acceptance criteria, and executable tasks. After Go, the agent follows task dependencies; recorded verification, not a checkbox or a chat claim, advances the work. `/spec:check` re-verifies the exact delivery tree before archiving.

**Continue on evidence, not a round counter.** Inspired by Loop Transformer research, the agent receives the frozen goal and affected criteria when asked to replan. Replayed callbacks, unchanged candidates, and recurring failures do not by themselves start another turn. This works at the agent execution layer, not inside the model. [How evidence-driven recovery works](docs/loop-continuity.md).

## Install

Requires Python 3.9+, Git, Bash, and a host that can edit files and execute commands.

These commands install from `main`. The quick path and adaptive continuation described here are not in the `v2.2.0` archive. For a stable install, use a [release archive](RELEASE.md#install-from-a-release-archive).

```bash
git clone https://github.com/MicTx/spec-harness.git
cd spec-harness
bash install.sh
```

Without `INSTALL_HOSTS`, the installer detects hosts. `INSTALL_PROFILE=core|full` is a compatibility alias for the same payload. The installer backs up replaced files and merges Spec-owned hooks without removing other entries. Preview with `DRY_RUN=1 bash install.sh`. A config entry does not prove the host loaded it; see [host behavior](docs/loop-continuity.md#host-behavior).

| Host | `INSTALL_HOSTS` |
| --- | --- |
| Claude Code / Desktop | `claude` / `claude-desktop` |
| Codex / Gemini / Grok | `codex` / `gemini` / `grok` |
| OpenCode / OpenClaw / Hermes | `opencode` / `openclaw` / `hermes` |
| Pi / ZCode / Devin | `pi` / `zcode` / `devin` |
| DeepSeek Harness / Cursor | `dsh` / `cursor` |
| Generic agent hosts (shared `.agents/skills` tree) | `agents` |

## Update

After installation, run from any directory:

```bash
spec-harness update           # Update installed hosts to the latest stable release
spec-harness update --check   # Check versions without installing
spec-harness update --dry-run # Download and preview without changing installations
spec-harness update --source china # Use the China mirror
spec-harness --version        # Show the installed version for each host
```

The updater tries GitHub and falls back to a third-party China mirror; `SPEC_UPDATE_SOURCE=china` selects it explicitly. It verifies the archive, preserves host settings, and backs up the previous version. You can also ask your agent to update Spec Harness. Restart your agent session afterward. If the command is not found, add `~/.local/bin` to `PATH` or run `~/.local/bin/spec-harness update`. Existing installations need a one-time `bash install.sh` from a version that includes this feature. See [RELEASE.md](RELEASE.md) for updates, selecting versions, and rollback.

## Quick Start

Enable the skill in a project and state the goal:

```text
/spec Fix the export API permission check for admins and regular users
```

Without a bound package, `/spec` enters the `plan` planning flow. A bound package before Go enters `update`, an internal route, not a command. After Go, keep the frozen goal and acceptance, resolve reversible in-scope work, and park decisions that need authorization. UI acceptance uses ordinary tasks and verification commands.

| Command | Purpose |
| --- | --- |
| `/spec <goal>` | Plan, or continue the package bound to this branch |
| `/spec:plan <goal>` | Explicit planning entry: create or refine the package, lint, resolve forks, `plan-go` |
| `/spec:run [goal]` | Resume a package or verify a small reversible fix in the current checkout without committing |
| `/spec:repair <archived-slug>` | Create an ordinary `kind: repair` package with `repair-of` metadata, then use the standard plan and gate |
| `/spec:check` | Follow the [recovery loop](references/exec.md), then archive and deliver per V4; `--check-only` only diagnoses |
| `/spec:push` | Finish Git delivery after explicit authorization (resolves the archived package bound to the current branch) |
| `/spec:doctor` | Check and repair runtime, host hooks and legacy Git hooks |
| `/spec /purge` | `spec.py purge`: remove evidence records from the Git index, keep local files; `--check` previews |

Codex/ZCode may use `$spec`. Small fixes, read-only investigation, and experiments do not need a package.

`python3 scripts/spec.py purge` keeps `.spec` packages, plans, run logs and historical records local while preserving `.spec/architecture` source contracts. Shared Git excludes cover linked worktrees; this repository's `.gitignore` also carries the rules into new clones. File bytes stay unchanged: the command does not commit, push or rewrite past commits. Later `verify` / `done` commits contain source changes only, and local archives are copied to the primary checkout before worktree cleanup. New clones lack machine-local history: historical-only audits explicitly skip missing records, while new deliveries still require the full evidence gate.

## Runtime Payload

```text
spec/
├── LICENSE
├── SKILL.md
├── install.sh
├── pyproject.toml
├── agents/
├── hooks/
├── references/
├── scripts/
└── updater/
```

`references/` contains only planning, execution, format, philosophy, and Git pages. Command-style hosts receive generated config; OpenCode, Pi, OpenClaw, and Hermes use forwarding shims.

## Verify and Maintain

```bash
python3 -m pytest -q
python3 -m ruff check scripts hooks updater tests
python3 scripts/export_skill_package.py --output /tmp/spec
```

GitHub `main` is the public source snapshot, not a new release. Find stable archives and checksums on [GitHub Releases](https://github.com/MicTx/spec-harness/releases) and current source changes in the [changelog](CHANGELOG.md#unreleased).

### Detailed documentation

Contributor setup and the full development workflow are in [CONTRIBUTING.md](CONTRIBUTING.md); operational guides are indexed in [docs/README-en.md](docs/README-en.md).

## License

Software code is licensed under [PolyForm Noncommercial 1.0.0](LICENSE). Commercial use requires separate written authorization.

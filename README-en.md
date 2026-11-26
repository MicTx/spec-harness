# Spec Harness

Two-phase task packages for AI coding agents: thick planning, silent execution, verifiable delivery.

[![Release](https://img.shields.io/github/v/release/MicTx/spec-harness)](https://github.com/MicTx/spec-harness/releases) [![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE) [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[中文](README.md) · [Documentation](docs/README-en.md) · [Quick start](#quick-start)

## Contents

- [Install](#install)
- [Update](#update)
- [Quick start](#quick-start)
- [Runtime payload](#runtime-payload)
- [Verify and maintain](#verify-and-maintain)
- [License](#license)

A package contains `spec.md` and `tasks.md` under `.spec/specs/YYYY-MM-DD_slug/`; the harness writes `runs.jsonl` and `state.json`. Planning removes unknowns with a value charter and deterministic lint. After Go, execution follows the task DAG, verifies, checkpoints, and continues without asking about reversible decisions. `check` refreshes authorized evidence and routes fixes back to the agent before asking for decisions. Every acceptance path stays in the package's ordinary task and evidence contract; frontend changes do not create a second gate.

## Install

Requires Python 3.9+, Git, Bash, and a host that can edit files and execute commands.

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
bash install.sh
```

Without `INSTALL_HOSTS`, the installer detects hosts. `core` and `full` are compatibility aliases with identical payloads. Session hooks are merged incrementally, preserving non-Spec entries and backing up files first. `DRY_RUN=1` prints an upgrade preview: per host target, whether it would install fresh, upgrade in place, back up and replace, or refuse (symlink/user-owned skill) — zero writes, user additions visible first.

The installer ships one fixed stage-instruction set and one session-brief budget. `INSTALL_PROFILE=core|full` remains a compatibility alias; both values export the identical payload.

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

Without a bound package `/spec` enters the `plan` planning flow; a bound package before Go enters `update`. Freeze the goal, completion conditions, scope and criteria; assumptions, forks and baseline probes are optional. Go does not rewrite the repository charter. After Go, decide reversible work and park unauthorized actions.

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

Private Gitea is the development source of truth; GitHub receives only an allowlisted public snapshot. Stable releases and SHA-256 files are on [GitHub Releases](https://github.com/MicTx/spec-harness/releases).

### Detailed documentation

Contributor setup and the full development workflow are in [CONTRIBUTING.md](CONTRIBUTING.md); operational guides are indexed in [docs/README-en.md](docs/README-en.md).

## License

Software code is licensed under [PolyForm Noncommercial 1.0.0](LICENSE). Commercial use requires separate written authorization.

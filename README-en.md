# Spec Harness

Two-phase task packages for AI coding agents: thick planning, silent execution, verifiable delivery.

[![Release](https://img.shields.io/github/v/release/MicTx/spec-harness)](https://github.com/MicTx/spec-harness/releases) [![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE) [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[中文](README.md) · [Documentation](docs/README-en.md) · [Quick start](#quick-start)

A package contains `spec.md` and `tasks.md` under `.spec/specs/YYYY-MM-DD_slug/`; the harness writes `runs.jsonl` and `state.json`. Planning removes unknowns with a value charter and deterministic lint. After Go, execution follows the task DAG, verifies, checkpoints, and continues without asking about reversible decisions. A UI-surface package also needs real-operation flows and screenshots written back through `spec.py ui-review --file <draft>` before `check` can deliver. Every lifecycle prompt ends with an encouraging `Spark:` line, inviting the agent to diverge boldly inside boundaries and converge on evidence.

## Install

Requires Python 3.9+, Git, Bash, and a host that can edit files and execute commands.

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
bash install.sh
```

Without `INSTALL_HOSTS`, the installer detects hosts. `core` and `full` are compatibility aliases with identical payloads. Session hooks are merged incrementally, preserving non-Spec entries and backing up files first. `DRY_RUN=1` prints an upgrade preview: per host target, whether it would install fresh, upgrade in place, back up and replace, or refuse (symlink/user-owned skill) — zero writes, user additions visible first.

Set the `SPEC_PROMPT_TIER` environment variable to pick the generated command body density: `full` (default) keeps the complete stage instructions, `compact` ships a two-to-three sentence body that retains one compact autonomy line; the tier is recorded in the install marker and `install-manifest.json`, and compact mode lowers the runtime session-brief truncation cap to 900 characters. The `aggressive` tier trims bodies further while every stage still keeps one hard autonomy instruction and the evidence-ledger rule, and it lowers the session-brief cap to 700 characters.

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

Without a bound package `/spec` enters the `plan` planning flow, and a bound package before Go enters `update`: read-only recon, intent clarification, then machine-checkable assumptions, forks, and task-level failure probes before `plan-go`. After Go, reversible choices are made and recorded; irreversible work is parked while other tasks continue.

| Command | Purpose |
| --- | --- |
| `/spec <goal>` | Plan, or continue the package bound to this branch |
| `/spec:plan <goal>` | Explicit planning entry: create or refine the package, lint, resolve forks, `plan-go` |
| `/spec:run [goal]` | Execute the bound package to a terminal state; with a goal and no bound package, the built-in one-shot plans, executes, and delivers in one chain |
| `/spec:repair <archived-slug>` | Scaffold a repair package from a delivered archive: thin planning, full gate, consistent with the original spec |
| `/spec:check` | Run the delivery gate, structure audit, and required UI/UX real-operation review; a pass archives, commits, and delivers per V4 automatically |
| `/spec:push` | Finish Git delivery after explicit authorization (resolves the archived package bound to the current branch) |
| `/spec:doctor` | Check and repair runtime and host hooks |
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

## License

Software code is licensed under [PolyForm Noncommercial 1.0.0](LICENSE). Commercial use requires separate written authorization.

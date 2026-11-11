# Spec Harness

Two-phase task packages for AI coding agents: thick planning, silent execution, verifiable delivery.

[![Release](https://img.shields.io/github/v/release/MicTx/spec-harness)](https://github.com/MicTx/spec-harness/releases) [![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE) [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[中文](README.md) · [Documentation](docs/README-en.md) · [Quick start](#quick-start)

A package contains `spec.md` and `tasks.md` under `.spec/specs/YYYY-MM-DD_slug/`; the harness writes `runs.jsonl` and `state.json`. Planning removes unknowns with a value charter and deterministic lint. After Go, execution follows the task DAG, verifies, checkpoints, and continues without asking about reversible decisions.

## Install

Requires Python 3.9+, Git, Bash, and a host that can edit files and execute commands.

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
bash install.sh
```

Without `INSTALL_HOSTS`, the installer detects hosts. `core` and `full` are compatibility aliases with identical payloads. Session hooks are merged incrementally, preserving non-Spec entries and backing up files first.

| Host | `INSTALL_HOSTS` |
| --- | --- |
| Claude Code / Desktop | `claude` / `claude-desktop` |
| Codex / Gemini / Grok | `codex` / `gemini` / `grok` |
| OpenCode / OpenClaw / Hermes | `opencode` / `openclaw` / `hermes` |
| Pi / ZCode / Devin | `pi` / `zcode` / `devin` |
| DeepSeek Harness / Cursor | `dsh` / `cursor` |

## Quick Start

Enable the skill in a project and state the goal:

```text
/spec Fix the export API permission check for admins and regular users
```

Without a bound package `/spec` enters the `new` planning flow, and a bound package before Go enters `update`: read-only recon, intent clarification, then machine-checkable assumptions, forks, and task-level failure probes before `plan-go`. After Go, reversible choices are made and recorded; irreversible work is parked while other tasks continue.

There are exactly three user commands:

| Command | Purpose |
| --- | --- |
| `/spec <goal>` | Plan, or continue the package bound to this branch |
| `/spec:push` | Finish Git delivery after explicit authorization (resolves the archived package bound to the current branch) |
| `/spec:doctor` | Check and repair runtime and host hooks |

Codex/ZCode may use `$spec`. Small fixes, read-only investigation, and experiments do not need a package.

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
└── scripts/
```

`references/` contains only planning, execution, format, philosophy, and Git pages. Command-style hosts receive generated config; OpenCode, Pi, OpenClaw, and Hermes use forwarding shims.

## Verify and Maintain

```bash
python3 -m pytest -q
python3 -m ruff check scripts hooks tests
python3 scripts/export_skill_package.py --output /tmp/spec
```

Private Gitea is the development source of truth; GitHub receives only an allowlisted public snapshot. Stable releases and SHA-256 files are on [GitHub Releases](https://github.com/MicTx/spec-harness/releases).

## License

Software code is licensed under [PolyForm Noncommercial 1.0.0](LICENSE). Commercial use requires separate written authorization.

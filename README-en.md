# Spec Harness

Task-package workflow for AI coding agents with verifiable scope, evidence, and Git gates.

[![CI](https://img.shields.io/github/actions/workflow/status/MicTx/spec-harness/ci-public.yml)](https://github.com/MicTx/spec-harness/actions/workflows/ci-public.yml) [![Release](https://img.shields.io/github/v/release/MicTx/spec-harness)](https://github.com/MicTx/spec-harness/releases) [![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE) [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[🇨🇳 阅读中文版](README.md) · [Documentation index](docs/README-en.md) · [Introduction](docs/introduction.md) · [Tutorial](docs/tutorial.md)

It stores cross-session work as a Development Record under `.spec/specs/YYYY-MM-DD_slug/`: `spec.md` holds scope and success criteria, `tasks.md` holds boundaries and verify commands, and `runs.jsonl` records executions on the final working tree. Resume from files, not from chat recall.

- **Small kernel**: the default LITE template needs only a `boundary` and a `verify` per task.
- **Checkable results**: verification runs before completion; change code or a verify command and the evidence is refreshed.
- **Separated closeout**: archiving and local commit belong to `done`; merge, push, and branch deletion need separate authorization.
- **Opt-in extension**: the full install adds the planning chain and slots; the main session always owns integration and acceptance.

Contents: [Install](#install) · [Quick Start](#quick-start) · [Runtime Payload](#runtime-payload) · [Verify and Maintain](#verify-and-maintain) · [License](#license)

## Install

Requires Python 3.9+, Git, Bash, and an agent client with project read/write and command execution tools. Windows uses WSL2; a plain read-only chat cannot run the full flow.

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
bash install.sh
```

The installer defaults to `INSTALL_PROFILE=core`; without `INSTALL_HOSTS` it auto-detects installed hosts. Given a token, it installs only the selected hosts and skips detection.

| Host | `INSTALL_HOSTS` | Default skill directory |
| --- | --- | --- |
| Claude Code / Desktop | `claude` / `claude-desktop` | `~/.claude/skills` / `~/.claude-desktop/skills` |
| Codex / Gemini / Grok | `codex` / `gemini` / `grok` | `~/.codex/skills` / `~/.gemini/skills` / `~/.grok/skills` |
| OpenCode / OpenClaw / Hermes | `opencode` / `openclaw` / `hermes` | `${XDG_CONFIG_HOME:-~/.config}/opencode/skills` / `~/.openclaw/skills` / `~/.hermes/skills` |
| Pi / ZCode | `pi` / `zcode` | `~/.pi/agent/skills` / `~/.zcode/skills` |
| DeepSeek Harness / Devin / Cursor | `dsh` / `devin` / `cursor` | `${DSH_HOME:-~/.dsh}/skills` / `~/.config/devin/skills` / `~/.cursor/skills` |
| Combined / all | `desktop`, `rpi` / `all` | Claude Code + Codex / every directory above |

```bash
INSTALL_HOSTS=claude INSTALL_PROFILE=full bash install.sh
```

**Explicit stages**: `new`, `goal`, `autoplan`, `autorun`, `run`, `check`, `done`, `push`, `update`, `status`, `doctor`, `organize` (full ships 12 stage commands; core keeps `new/run/check/done/push/doctor`).

The installer refuses to overwrite an unmarked same-name skill; use `FORCE=1` only when you mean to replace it. Re-running the default install after `full` downgrades to core and backs up extensions before removal. Installer tests are not a per-host live acceptance; dsh remains a developer preview.

## Quick Start

Load the skill in the target project, then state a bounded goal. A core Claude Code install works directly:

```text
/spec:new Fix the export API permission check for admins and regular users
/spec:run
/spec:check
/spec:done
```

With the full install, `/spec:goal <goal>` runs the same single-package flow; Codex/ZCode use `$spec`, Pi uses `/spec` and `/spec:<stage>`. Other hosts load the skill and request stages per the [command index](references/commands.md).

For the first run, follow the [LITE tutorial](docs/tutorial.md): create a package, fill in scope, change code, run `spec_verify.py run --all`, tick boxes, check, and archive. The expected output is a checkable completion record, not a claim of "done". A single small fix, read-only investigation, or one-off experiment does not need a package.

Machine evidence requires a computable Git working-tree fingerprint; non-Git projects can use manual verification but cannot claim the evidence stayed fresh after code changes. `push` runs only after explicit authorization; on failure, trust the actual Git state.

## Runtime Payload

The runtime exporter and installer share one [payload contract](scripts/payload_contract.py). The tree below is the full top level; core omits slots and agent role docs, keeping only `agents/openai.yaml` for host discovery.

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
└── slots/
```

Export runtime files only:

```bash
python3 scripts/export_skill_package.py --profile core --output /tmp/spec
```

Drop the exported directory into the host's skill root; the installer additionally generates host stage commands and an install manifest. Release, watermark, and plugin packaging tools never enter the runtime package. The full extension protocol is in [slots](references/slots.md); the planning chain advances only in the current session and resumes explicitly after an interruption.

## Verify and Maintain

```bash
python3 -m pytest -q tests slots
python3 -m ruff check scripts hooks slots tests
python3 scripts/smoke_test_spec_skill.py
python3 scripts/slot_registry.py validate
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full verification matrix; [RELEASE.md](RELEASE.md) covers release, upgrade, and rollback, and [Git workflow](docs/git-workflow.en.md) covers closeout. Stable releases and SHA-256 files are on [GitHub Releases](https://github.com/MicTx/spec-harness/releases). The streamlined source flow does not mean existing release archives were rebuilt.

For old active packages, dry-run `scripts/migrate_packages.py` first, then pass `--apply` explicitly; packages whose checklists cannot be mapped reliably stay untouched and need manual acceptance links. Historical archives are neither migrated nor re-scored.

Spec Harness provides no model or hosted service and makes no product decisions for you. Recoverable records and test evidence do not replace judgment about business correctness.

## License

Software code is licensed under [PolyForm Noncommercial 1.0.0](LICENSE). Commercial use requires separate written authorization.

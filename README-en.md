# Spec Harness

Task-package workflow for AI coding agents with verifiable scope, evidence, and Git gates.

[![License: Non-Commercial](https://img.shields.io/badge/License-Non--Commercial-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[🇨🇳 阅读中文版](README.md)

Spec Harness stores cross-session, multi-round engineering work as a recoverable Development Record. It provides task packages, routing, verification, archival, and Git closeout. It does not provide a model or make product decisions for you.

Start with the [introduction](docs/introduction.md), follow the [long-running task tutorial](docs/tutorial.md), and use the [Git workflow](docs/git-workflow.en.md) when you are ready to release or roll back. The [documentation index](docs/README.md) lists every public entry point.

**Table of Contents**

- [What You Get](#what-you-get)
- [Repository Layout](#repository-layout)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Exporting A Runtime Skill Package](#exporting-a-runtime-skill-package)
- [Verification](#verification)
- [Versions And Releases](#versions-and-releases)
- [Scope](#scope)
- [License](#license)

## What You Get

- **One task package**: `spec.md`, `tasks.md`, and `checklist.md` under `.spec/specs/YYYY-MM-DD_slug/`.
- **One evidence chain**: `goal -> scope -> tasks -> verification -> archive -> commit -> push`; every task declares a `boundary` and a `verify` command.
- **Explicit stages**: `new`, `goal`, `run`, `check`, `done`, `push`, `update`, `status`, `doctor`, and `organize`.
- **Recoverable state**: reports, failures, completion summaries, and archives come from files on disk, so another session can continue.
- **Controlled execution**: the main session keeps routing, integration, acceptance, and done/push gates; `workflow-runner` and other slots only execute bounded work.
- **Two runtime forms**: the local Skill is the reference implementation; the optional `server/` adapter projects remote task-package start/result/health operations.

## Repository Layout

```text
.
├── SKILL.md
├── install.sh
├── pyproject.toml
├── agent-plugin/                    # source-only: agent-plugin manifest templates
│   ├── plugin.json.template
│   ├── claude-plugin.json.template
│   └── README.md
├── agents/
│   ├── openai.yaml
│   ├── orchestrator.md
│   ├── planner.md
│   ├── reviewer.md
│   └── confirmer.md
├── docs/                            # public user and contributor guides
│   ├── README.md
│   ├── introduction.md
│   ├── tutorial.md
│   ├── git-workflow.md
│   └── git-workflow.en.md
├── hooks/
│   ├── pre-commit
│   ├── pre-push
│   ├── claude_stop_guard.py
│   └── spec_disk_truth_gate.py
├── references/
│   ├── 00-readme.md
│   ├── changelog-guide.md
│   ├── commands.md
│   ├── engineering-philosophy.md
│   ├── naming-and-commits.md
│   ├── operating-rules.md
│   ├── output-contracts.md
│   ├── slots.md
│   ├── storage-and-archive.md
│   ├── templates.md
│   └── orchestration.md
├── scripts/
│   ├── build_release.py             # source-only packager (excluded from runtime export)
│   ├── check_all_spec_packages.py
│   ├── check_spec_package.py
│   ├── complete_spec_package.py
│   ├── dashboard_support.py
│   ├── doctor_spec_environment.py
│   ├── export_skill_package.py      # source-only packager (excluded from runtime export)
│   ├── generate_changelog.py
│   ├── import_kiro_specs.py         # source-only one-shot migration tool (excluded from runtime export)
│   ├── init_spec_package.py
│   ├── install_git_hooks.py
│   ├── install_slot_hooks.py
│   ├── issue_closure_support.py
│   ├── migrate_task_ids.py          # source-only one-shot migration tool (excluded from runtime export)
│   ├── organize_project_structure.py  # /spec:organize structure-audit fact engine
│   ├── package_agent_plugin.py      # source-only packager (excluded from runtime export)
│   ├── gitea_hook_repair.py
│   ├── path_safety.py
│   ├── payload_contract.py
│   ├── push_spec_package.py
│   ├── read_version.py
│   ├── report_spec_package.py
│   ├── route_decision.py
│   ├── route_spec_package.py
│   ├── safe_open_support.py
│   ├── slot_registry.py
│   ├── smoke_test_spec_skill.py
│   ├── spec_package_support.py
│   ├── update_checkpoint.py
│   └── update_checkpoint_support.py
├── server/
│   ├── README.md
│   ├── install.sh
│   └── server.py
├── slots/                           # pluggable slots shipped with the runtime package
│   ├── team-loop/                   # agents-team loop triggering and management
│   └── workflow-runner/             # repository-owned deterministic fan-out execution
├── tests/                           # source-only pytest suite, including fixtures/kiro
├── release/                         # source-only tracked release artifacts
└── .github/                         # CI and issue/pull-request templates
```

Runtime task packages default to `.spec/` in target projects; this source repository does not contain user-project state. Detailed contracts live in [`references/`](references/00-readme.md), and user-facing guides live in [`docs/`](docs/README.md).

## Requirements

- Python 3.9 or newer.
- A host CLI that loads `SKILL.md` skills, such as Claude Code, Codex, Gemini CLI, Grok Build, OpenCode, OpenClaw, Hermes, Pi, or ZCode.
- Runtime helpers use only the Python standard library; development checks additionally use `pytest` and `ruff`.
- The installer and helpers support Linux, macOS, and Windows.

## Installation

### Installer

Run the installer from a checked-out source or release tree:

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
git checkout v0.13.10
bash install.sh
```

`install.sh` is the only local Skill installer. The optional `server/install.sh` is a separate self-hosted server-mode deployment entrypoint. To upgrade or roll back, check out the target tag and run the installer again.

### Supported hosts

| `INSTALL_HOSTS` | Host | Default directory | Extra behavior |
| --- | --- | --- | --- |
| `claude` | Claude Code | `~/.claude/skills` | writes `/spec` and ten stage commands |
| `claude-desktop` | Claude Desktop | `~/.claude-desktop/skills` | Skill only |
| `codex`, `codex-desktop` | Codex | `~/.codex/skills` | both tokens share the directory |
| `gemini` | Gemini CLI | `~/.gemini/skills` | |
| `grok`, `grokbuild` | Grok Build | `~/.grok/skills` | `grokbuild` is an alias |
| `opencode` | OpenCode | `${XDG_CONFIG_HOME:-~/.config}/opencode/skills` | |
| `openclaw` | OpenClaw | `~/.openclaw/skills` | |
| `hermes` | Hermes | `~/.hermes/skills` | relocatable with `HERMES_HOME` |
| `pi` | Pi agent | `~/.pi/agent/skills` | relocatable with `PI_DIR` |
| `zcode` | ZCode | `~/.zcode/skills` | project-owned host |
| `desktop`, `rpi` | Claude Code + Codex | both default directories | combined targets |
| `all` | all hosts | as listed above | default |

Select one host explicitly when needed:

```bash
INSTALL_HOSTS=codex CODEX_SKILLS_DIR=~/.codex/skills bash install.sh
```

The installer refuses to overwrite a same-name Skill without its `.spec-skill-install` ownership marker. Set `FORCE=1` only when intentionally replacing a user-owned directory. `pi` now means the Pi agent; use `rpi` or `desktop` for the former Claude + Codex combination.

### Manual install

Export only the runtime Skill package:

```bash
python3 scripts/export_skill_package.py --output /tmp/spec --force
```

Place the exported `spec/` directory in the host's Skill directory. Trigger it with `$spec` in Codex/Pi/ZCode or `/spec:goal` in Claude Code.

## Quick Start

Create a task package in the project you want to change:

```bash
python3 /tmp/spec-harness/scripts/init_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_payment-recovery \
  --title "Payment Recovery"
```

Or start through a skill-aware host:

```text
$spec
spec:goal add payment recovery with tests
# Claude Code: /spec:goal add payment recovery with tests
```

After initialization, inspect the stage, status, and package gate:

```bash
python3 /tmp/spec-harness/scripts/route_spec_package.py --root /path/to/project --slug 2026-10-03_payment-recovery
python3 /tmp/spec-harness/scripts/report_spec_package.py --root /path/to/project --slug 2026-10-03_payment-recovery --view status
python3 /tmp/spec-harness/scripts/check_spec_package.py --root /path/to/project --slug 2026-10-03_payment-recovery
```

When the work is verified, `/spec:done` performs the final checks, archives the package, and creates the Git commit. For direct operation, run:

```bash
python3 /tmp/spec-harness/scripts/complete_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_payment-recovery \
  --archive
```

This script creates the summary and archives the package; run `git commit` separately, then use `push_spec_package.py` for merge and push cleanup. Retired members go to `archive/retired` under a `never delete` policy. A task is only complete after its `verify` command has actually run.

## Exporting A Runtime Skill Package

The source repository also contains packagers, tests, and release artifacts. `scripts/export_skill_package.py` defines the runtime payload: it keeps `SKILL.md`, the installer, `agents/`, `hooks/`, `references/`, `server/`, `slots/`, and runtime helpers while excluding source-maintenance tools, tests, and `release/`.

The export tree is locked by tests:

```text
spec/
├── SKILL.md
├── install.sh
├── pyproject.toml
├── agents/
│   ├── openai.yaml
│   ├── orchestrator.md
│   ├── planner.md
│   ├── reviewer.md
│   └── confirmer.md
├── hooks/
│   ├── pre-commit
│   ├── pre-push
│   ├── claude_stop_guard.py
│   └── spec_disk_truth_gate.py
├── references/
│   ├── 00-readme.md
│   ├── changelog-guide.md
│   ├── commands.md
│   ├── engineering-philosophy.md
│   ├── naming-and-commits.md
│   ├── operating-rules.md
│   ├── output-contracts.md
│   ├── slots.md
│   ├── storage-and-archive.md
│   ├── templates.md
│   └── orchestration.md
├── scripts/
│   ├── check_all_spec_packages.py
│   ├── check_spec_package.py
│   ├── complete_spec_package.py
│   ├── dashboard_support.py
│   ├── doctor_spec_environment.py
│   ├── generate_changelog.py
│   ├── init_spec_package.py
│   ├── install_git_hooks.py
│   ├── install_slot_hooks.py
│   ├── issue_closure_support.py
│   ├── organize_project_structure.py
│   ├── gitea_hook_repair.py
│   ├── path_safety.py
│   ├── payload_contract.py
│   ├── push_spec_package.py
│   ├── read_version.py
│   ├── report_spec_package.py
│   ├── route_decision.py
│   ├── route_spec_package.py
│   ├── safe_open_support.py
│   ├── slot_registry.py
│   ├── smoke_test_spec_skill.py
│   ├── spec_package_support.py
│   ├── update_checkpoint.py
│   └── update_checkpoint_support.py
├── server/
│   ├── README.md
│   ├── install.sh
│   └── server.py
└── slots/
    ├── team-loop/
    │   ├── manifest.json
    │   ├── README.md
    │   ├── references/
    │   │   └── prior-art.md
    │   ├── hooks/
    │   │   ├── loop_route_hook.py
    │   │   ├── loop_stop_guard.py
    │   │   └── loop_teammate_gate.py
    │   ├── scripts/
    │   │   ├── loop_control.py
    │   │   ├── loop_route.py
    │   │   └── loop_state.py
    │   └── tests/
    │       ├── conftest.py
    │       ├── recheck_probes.py
    │       ├── smoke_run.py
    │       ├── test_hooks.py
    │       ├── test_loop_control.py
    │       ├── test_loop_route.py
    │       └── test_loop_state.py
    └── workflow-runner/
        ├── manifest.json
        ├── README.md
        ├── hooks/
        │   └── workflow_route_hook.py
        ├── scripts/
        │   ├── workflow_fanout.py
        │   └── workflow_route.py
        └── tests/
            ├── conftest.py
            ├── test_workflow_fanout.py
            ├── test_workflow_hook.py
            └── test_workflow_route.py
```

## Verification

Run the source-level checks with development dependencies installed:

```bash
python3 -m compileall -q scripts server hooks slots tests
python3 scripts/slot_registry.py validate
python3 scripts/smoke_test_spec_skill.py
python3 -m pytest -q
ruff check scripts/ server/ hooks/ slots/ tests/
ruff format --check scripts/ server/ hooks/ slots/ tests/
```

For documentation-only changes, also run:

```bash
python3 /path/to/repo-readme-skill/scripts/audit.py README.md --name spec-harness
python3 /path/to/repo-readme-skill/scripts/audit.py README-en.md --name spec-harness
git diff --check
```

The full contributor matrix is in [`CONTRIBUTING.md`](CONTRIBUTING.md). A task package declares `package`, `integration`, or `project` verification in `spec.md` under `### 5.1 验证策略`.

## Versions And Releases

Stable versions and SHA-256 checksums are published on [GitHub Releases](https://github.com/MicTx/spec-harness/releases). See [RELEASE.md](RELEASE.md) for install, upgrade, rollback, and checksum steps. Contributors should read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Git workflow](docs/git-workflow.en.md).

## Scope

Spec Harness provides task packages, workflow instructions, templates, verification scripts, and an optional self-hosted server-mode adapter. It does not operate a hosted service, provide a model, make product decisions, create pull requests, or orchestrate remote CI.

> [!WARNING]
> `server/` is an optional self-hosted HTTP adapter. Before exposing it publicly, configure `SPEC_SERVER_TOKEN` and `ALLOWED_ROOTS`, and terminate TLS at a trusted reverse proxy. `/health` is anonymous for probes; `/start` and `/result` require a Bearer token.

## License

This project uses a source-available, non-commercial license. Commercial use and sale are prohibited; the license is not an OSI-approved open-source license. See [LICENSE](LICENSE) for the complete terms.

### Stable identity and verification

Optional `id: task-name` and explicit `depends-on` preserve task identity and dependencies. Migration defaults to dry-run and does not rewrite historical archives. A self-reported result cannot complete a task; run its `verify` command before checking it off.

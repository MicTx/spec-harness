# Spec Harness

Task-package workflow for AI coding agents with verifiable scope, evidence, and Git gates.

[![CI](https://img.shields.io/github/actions/workflow/status/MicTx/spec-harness/ci-public.yml)](https://github.com/MicTx/spec-harness/actions/workflows/ci-public.yml)
[![Release](https://img.shields.io/github/v/release/MicTx/spec-harness)](https://github.com/MicTx/spec-harness/releases)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[🇨🇳 阅读中文版](README.md)

Spec Harness stores cross-session, multi-round engineering work as a recoverable Development Record. It provides task packages, routing, verification, archival, and Git closeout. It does not provide a model or make product decisions for you.

Pick your entry point by what you want to do: to understand the idea first, read the [introduction](docs/introduction.md); to run one end to end, follow the [long-running task tutorial](docs/tutorial.md); to release or roll back, see the [Git workflow](docs/git-workflow.en.md). The [documentation index](docs/README-en.md) lists every public entry point.

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

- **One task package**: `spec.md` and `tasks.md` under `.spec/specs/YYYY-MM-DD_slug/` (new-format packages carry no `checklist.md`; acceptance runs through the `spec.md` §6 success criteria, while legacy packages keep theirs).
- **One evidence chain**: `goal -> scope -> tasks -> verification -> archive -> commit -> push`; every task declares a `boundary` and a `verify` command.
- **Explicit stages**: `new`, `goal`, `autoplan`, `autorun`, `run`, `check`, `done`, `push`, `update`, `status`, `doctor`, and `organize`.
- **Recoverable state**: reports, failures, completion summaries, and archives come from files on disk, so another session can continue.
- **Session-boundary handoff**: packages may carry an optional `handoff.md` — a generated snapshot head plus an append-only entry log whose required slots adapt to the collaboration mode (solo / team / agent / cluster); maintained by `spec_handoff.py`, validated by the check gate, and frozen by a terminal entry at archive time.
- **Interactive planning**: `autoplan` clarifies the goal, then runs framework, phase-detail and consistency-review passes. Desktop and IDE agent plugins proceed in the current session; verified CLIs may use a fresh-session chain. Without a native reviewer, the result is explicitly main-session review. Readiness stops planning; it never starts development automatically.
- **Recoverable autorun**: `autorun` executes and verifies each planned package, then advances after authorized Git closeout. A desktop session never switches to another agent just because its CLI is on PATH. Paused work resumes the same unit; the default cap is 5 admitted rounds (`--max-rounds` changes it, `--unbounded` removes it). Current-session execution does not continue after the client is closed.
- **Stable task identity**: tasks can declare an `id:` and explicit `depends-on`; protocol migrations never rewrite historical archives.
- **Controlled execution**: the main session keeps routing, integration, acceptance, and done/push gates; `workflow-runner` and other slots only execute bounded work.
- **Two runtime forms**: the local Skill is the reference implementation; the optional `server/` adapter projects remote task-package start/result/health operations.

## Repository Layout

```text
.
├── SKILL.md                  # skill entrypoint: stage commands and runtime contracts
├── install.sh                # local Skill installer (multi-host, see table below)
├── pyproject.toml
├── agent-plugin/             # source-only: agent-plugin manifest templates
├── agents/                   # optional orchestration agents (orchestrator/planner/reviewer/confirmer)
├── docs/                     # public user and contributor guides
├── hooks/                    # pre-commit / pre-push and Stop gates
├── references/               # runtime contract references (commands, templates, slots, archival)
├── scripts/                  # stage scripts; build/export/package packagers stay out of the runtime export
├── server/                   # optional self-hosted server-mode adapter
├── slots/                    # pluggable slots: team-loop and workflow-runner (contracts in references/slots.md)
├── tests/                    # source-only pytest suite
├── release/                  # source-only tracked release artifacts (spec-harness-{version}, four files)
└── .github/                  # CI and issue/pull-request templates
```

Runtime task packages default to `.spec/` in target projects; this source repository does not contain user-project state. Detailed contracts live in [`references/`](references/00-readme.md), and user-facing guides live in [`docs/README-en.md`](docs/README-en.md).

## Requirements

- Python 3.9 or newer.
- An agent client that loads `SKILL.md`: CLI, IDE agent extension or desktop app, with actual workspace read/write and Python/Git execution tools.
- Runtime helpers use only the Python standard library; development checks additionally use `pytest` and `ruff`.
- The installer requires Bash; current-session chains target macOS and Linux/WSL2. Native Windows chains must run inside WSL2; platform-mocked tests do not establish native support.
- Only fresh CLI sessions need an additional transport: macOS Terminal.app, or **tmux ≥3.2** and ps on Linux/WSL2. Current mode needs neither tmux/osascript nor another agent CLI. See the [execution and session backend contract](references/commands.md#session-backends).

## Installation

### Installer

Run the installer from a checked-out source or release tree:

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
git checkout v0.20.3
bash install.sh
```

`install.sh` is the only local Skill installer. The optional `server/install.sh` is a separate self-hosted server-mode deployment entrypoint. To upgrade or roll back, check out the target tag and run the installer again.

Installing from a source tree needs no signing credentials: when none are found locally, the installer exports with `--skip-signing` automatically and prints a one-line notice; distribution signing is enforced only by the release build (release archives ship pre-signed). To sign locally, point `SPEC_SIGNING_IDENTITY` and `SPEC_SIGNING_KEY` at an identity JSON and a key file; `SPEC_SKIP_SIGNING=1` skips explicitly.

### Supported hosts

| `INSTALL_HOSTS` | Host | Default directory | Extra behavior |
| --- | --- | --- | --- |
| `claude` | Claude Code | `~/.claude/skills` | writes `/spec` and stage commands (5 on core, 12 on full) |
| `claude-desktop` | Claude Desktop | `~/.claude-desktop/skills` | Skill only |
| `codex`, `codex-desktop` | Codex | `~/.codex/skills` | both tokens share the directory |
| `gemini` | Gemini CLI | `~/.gemini/skills` | |
| `grok`, `grokbuild` | Grok Build | `~/.grok/skills` | `grokbuild` is an alias |
| `opencode` | OpenCode | `${XDG_CONFIG_HOME:-~/.config}/opencode/skills` | |
| `openclaw` | OpenClaw | `~/.openclaw/skills` | |
| `hermes` | Hermes | `~/.hermes/skills` | relocatable with `HERMES_HOME` |
| `pi` | Pi agent | `~/.pi/agent/skills` + `~/.pi/agent/prompts` | relocatable with `PI_DIR`; also writes `/spec` and `/spec:<stage>` native prompt templates (`PI_PROMPTS_DIR` relocation, effective after `/reload`) |
| `zcode` | ZCode | `~/.zcode/skills` | project-owned host |
| `devin`, `devin-desktop` | Devin CLI / Local | `~/.config/devin/skills` | Skill only; relocatable with `DEVIN_SKILLS_DIR` |
| `cursor`, `cursor-cli` | Cursor IDE / CLI | `~/.cursor/skills` | Skill only; relocatable with `CURSOR_SKILLS_DIR` |
| `desktop`, `rpi` | Claude Code + Codex | both default directories | combined targets |
| `all` | all hosts | as listed above | explicit full set |

With no `INSTALL_HOSTS` the installer auto-detects: only hosts whose CLI is on `PATH` or whose default host directory exists get installed; hosts this installer installed before are kept via their ownership marker, and skipped hosts are listed in the install output. An explicit `INSTALL_HOSTS` value (including `all`) bypasses detection entirely.

Select one host explicitly when needed:

```bash
INSTALL_HOSTS=codex CODEX_SKILLS_DIR=~/.codex/skills bash install.sh
```

Install profile: `INSTALL_PROFILE` selects the component surface and defaults to `core` — the kernel only (core stage commands `/spec:new` `/spec:run` `/spec:check` `/spec:done` `/spec:push`, Git hooks, and the scripts tree), without extension components (`slots/`, `agents/`) or extended stage commands (`goal`/`autoplan`/`autorun`/`update`/`status`/`doctor`/`organize`). `INSTALL_PROFILE=full` restores the complete install (all 12 stage commands plus extension components, byte-identical to the previous behavior). Re-running the default install on a machine that previously installed full downgrades it back to core: extended stage commands and agents are backed up, then removed. Opt in explicitly when you need the chains, planning passes, or extension capabilities:

```bash
INSTALL_PROFILE=full bash install.sh
```

The installer refuses to overwrite a same-name Skill without its `.spec-skill-install` ownership marker. Set `FORCE=1` only when intentionally replacing a user-owned directory. `pi` now means the Pi agent; use `rpi` or `desktop` for the former Claude + Codex combination.

### Execution surfaces and support boundaries

The install target chooses where files live; execution policy chooses how this session proceeds. They are not the same allowlist. The historical `desktop` token still combines only Claude Code + Codex, not every desktop client.

| Agent / surface | `auto` execution | Requirements and limits |
| --- | --- | --- |
| Explicit Claude Code, Codex or Pi CLI session | fresh | Existing full interactive adapters; an explicit worker can select another verified CLI |
| Claude/Codex and other IDE agent extensions | current | Use extension tools; the outer editor and inherited markers do not prove a CLI surface |
| Devin Desktop, native Cursor Agent, ZCode | current | Continue in the main session without installing another agent CLI |
| Devin/Cursor CLI, Gemini, Grok, OpenCode, OpenClaw, Hermes | current | No corresponding fresh adapter in this repository; this does not mean the product has no CLI |
| Claude Desktop | conditional current | Requires actual local workspace read/write and command tools; ordinary chat or read-only tools are insufficient |
| Unknown compatible client | current | Load the Skill explicitly and check real tools; do not guess configuration paths or commands |

Install full for these extended capabilities, for example:

```bash
INSTALL_HOSTS=devin,cursor,zcode INSTALL_PROFILE=full bash install.sh
```

In Devin/Cursor, load `/spec` then request `spec:autoplan` or `spec:autorun`; in ZCode use `$spec`. Refresh skills or start a new session as the host requires after installation; do not assume universal `/spec:<stage>` registration. The main session owns capability checks, permission boundaries and recovery. Neither a separate window nor subagents are required; review without a native reviewer is labeled main-session review.

Evidence levels: automated tests cover client profiles, recovery and isolated installs, not a live run of every product. Record Cursor/ZCode desktop, Linux/WSL2 and native Windows evidence separately; untested surfaces are not live verified. See the [execution and session backend contract](references/commands.md#session-backends).

### Manual install

Export only the runtime Skill package:

```bash
python3 scripts/export_skill_package.py --output /tmp/spec --force
```

Calling the exporter directly never auto-skips signing: in a keyless environment add `--skip-signing` explicitly (the installer applies it automatically under the same conditions). The exporter also accepts `--profile core|full` (default `full`, byte-identical to the release payload).

Place the exported `spec/` directory in the host's Skill directory. Trigger it with `$spec` in Codex/ZCode, use the native `/spec` and `/spec:<stage>` commands in Pi (arguments forwarded verbatim via `$@`), `/spec:goal` in Claude Code, or load `/spec` then request `spec:goal` in Devin/Cursor.

## Quick Start

Once installed, the shortest path is to give your host one complete goal:

```text
$spec
spec:goal add payment recovery with tests
# Claude Code: /spec:goal add payment recovery with tests
```

The host walks the stages with you: clarify the goal and scope first (`goal`), then execute (`run`) and review (`check`), and finally archive and commit (`done`); push (`push`) runs only when explicitly requested. To bypass the host and drive the scripts directly, the effect is equivalent — create a task package in the project you want to change:

```bash
python3 /tmp/spec-harness/scripts/init_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_add-payment-recovery \
  --title "Payment Recovery"
```

After initialization, inspect the stage, status, and package gate:

```bash
python3 /tmp/spec-harness/scripts/route_spec_package.py --root /path/to/project --slug 2026-10-03_add-payment-recovery
python3 /tmp/spec-harness/scripts/report_spec_package.py --root /path/to/project --slug 2026-10-03_add-payment-recovery --view status
python3 /tmp/spec-harness/scripts/check_spec_package.py --root /path/to/project --slug 2026-10-03_add-payment-recovery
```

When the work is verified, `/spec:done` performs the final checks, archives the package, and creates the Git commit. For direct operation, run:

```bash
python3 /tmp/spec-harness/scripts/complete_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_add-payment-recovery \
  --archive
```

This script creates the summary and archives the package; run `git commit` separately, then use `push_spec_package.py` for merge and push cleanup. Retired members go to `archive/retired` under a `never delete` policy. A task is only complete after its `verify` command has actually run — never a checked checkbox alone. For stronger evidence binding, declare `evidence: runs` in `spec.md`: machine tasks' `verify` commands are actually executed by `scripts/spec_verify.py run`, appending each run record (exit, duration, stdout tail hash, worktree fingerprint) to the package's `runs.jsonl`, and check/done accept only a latest record with `exit == 0` whose fingerprint still matches the current worktree — never run means unverified, and a tree changed after capture means expired evidence.

## Exporting A Runtime Skill Package

The source repository also contains packagers, tests, and release artifacts. `scripts/export_skill_package.py` defines the runtime payload: it keeps `SKILL.md`, the installer, `agents/`, `hooks/`, `references/`, `server/`, `slots/`, and runtime helpers while excluding source-maintenance tools, tests, and `release/`.

The export tree is locked by tests:

```text
spec/
├── LICENSE
├── NOTICE
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
│   └── claude_stop_guard.py
├── references/
│   ├── 00-readme.md
│   ├── changelog-guide.md
│   ├── commands.md
│   ├── engineering-philosophy.md
│   ├── handoff.md
│   ├── naming-and-commits.md
│   ├── output-contracts.md
│   ├── slots.md
│   ├── storage-and-archive.md
│   ├── templates.md
│   ├── orchestration.md
│   └── stages/
│       ├── autoplan.md
│       ├── autorun.md
│       ├── check.md
│       ├── doctor.md
│       ├── done.md
│       ├── goal.md
│       ├── new.md
│       ├── organize.md
│       ├── push.md
│       ├── run.md
│       ├── status.md
│       └── update.md
├── scripts/
│   ├── autoplan_gate.py
│   ├── autoplan_spawn.py
│   ├── autorun_spawn.py
│   ├── chain_lifecycle.py
│   ├── chain_recovery.py
│   ├── chain_session.py
│   ├── chain_spawn_support.py
│   ├── check_all_spec_packages.py
│   ├── check_spec_package.py
│   ├── complete_spec_package.py
│   ├── dashboard_support.py
│   ├── doctor_spec_environment.py
│   ├── generate_changelog.py
│   ├── handoff_support.py
│   ├── init_spec_package.py
│   ├── install_git_hooks.py
│   ├── install_slot_hooks.py
│   ├── issue_closure_support.py
│   ├── measure_skill_surface.py
│   ├── organize_project_structure.py
│   ├── gitea_hook_repair.py
│   ├── push_spec_package.py
│   ├── read_version.py
│   ├── report_spec_package.py
│   ├── route_decision.py
│   ├── route_spec_package.py
│   ├── safe_open_support.py
│   ├── slot_registry.py
│   ├── smoke_test_spec_skill.py
│   ├── spec_handoff.py
│   ├── spec_package_support.py
│   ├── spec_verify.py
│   ├── sweep_chain_windows.py
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
    │   ├── hooks/
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
    │       ├── test_audit_regressions.py
    │       ├── test_hooks.py
    │       ├── test_loop_control.py
    │       ├── test_loop_route.py
    │       └── test_loop_state.py
    └── workflow-runner/
        ├── manifest.json
        ├── README.md
        ├── scripts/
        │   ├── workflow_fanout.py
        │   └── workflow_route.py
        └── tests/
            ├── conftest.py
            ├── test_workflow_fanout.py
            ├── test_workflow_fanout_realpath.py
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
python3 -m pytest tests/test_public_docs.py tests/test_export_skill_package.py -q
git diff --check
```

The full contributor matrix is in [`CONTRIBUTING.md`](CONTRIBUTING.md). A task package declares `package`, `integration`, or `project` verification in `spec.md` under `### 5.1 验证策略` (machine key `verification-strategy`): sections are recognized by their machine keys, so English titles and key anchors are equally valid — see the English-title variant in [`references/templates.md`](references/templates.md).

## Efficiency And Read Costs

The trade-offs of the internal read surface (`route_spec_package.py --format json --brief` task slices and `spec_verify.py show` evidence views) versus prose compression are backed by a four-arm ablation: A baseline / B compression only / C internal tools only / D combined, with 16 frozen scenarios grouped into five fixed task classes, byte-identical stable projections across two consecutive runs, and three layers (bytes, deterministic calls, real usage) reported separately with no cross-layer conversion.

- Prose compression lowers read bytes in all five task classes (−23 to −2,270 B per class) and the static document face by 1,357 B, with zero task-completeness loss.
- The internal-tool dispatch sentence is a bounded 248 B always-on cost; the combined arm still nets 782 B saved on the highest-volume task class.
- `--brief` reduces both calls and bytes when one view replaces several full reads (measured: −1 call and −552 B on the review scenario); small-package locating and single-file precise reads stay native per the dispatch decision table, and measured negative-benefit rows are preserved as-is.
- `spec_verify.py show --history` full reassembly is used only when the complete log is required, with pages reassembling byte-for-byte; first screens and short evidence stay on direct reads.

## Versions And Releases

Stable versions and SHA-256 checksums are published on [GitHub Releases](https://github.com/MicTx/spec-harness/releases). See [RELEASE.md](RELEASE.md) for install, upgrade, rollback, and checksum steps. Contributors should read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Git workflow](docs/git-workflow.en.md).

## Scope

Spec Harness provides task packages, workflow instructions, templates, verification scripts, and an optional self-hosted server-mode adapter. It does not operate a hosted service, provide a model, make product decisions, create pull requests, or orchestrate remote CI.

> [!WARNING]
> `server/` is an optional self-hosted HTTP adapter. Before exposing it publicly, configure `SPEC_SERVER_TOKEN` and `ALLOWED_ROOTS`, and terminate TLS at a trusted reverse proxy. `/health` is anonymous for probes; `/start` and `/result` require a Bearer token.

## License

Software code is licensed under PolyForm Noncommercial 1.0.0 (`PolyForm-Noncommercial-1.0.0`). Commercial use requires separate written authorization. See [LICENSE](LICENSE) for the complete terms.

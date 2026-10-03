# Spec Harness

[![License: Non-Commercial](https://img.shields.io/badge/License-Non--Commercial-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[🇨🇳 阅读中文版](README.md)

Spec Harness is a workflow skill for Codex, Claude Code, Gemini CLI, Grok Build, OpenCode, OpenClaw, Hermes, Pi, and other skill-aware CLIs for specification-driven development. It helps an AI coding agent turn complex work into a tracked task package before implementation, then keep scope, tasks, validation, archival output, and Git handoff consistent.

Reading path: [Introduction](docs/introduction.md) · [Long-running task tutorial](docs/tutorial.md) · [Git workflow](docs/git-workflow.en.md)

The repository is the source package for Spec Harness; `spec` remains the compatible Skill/CLI invocation name. It contains the skill entry file, reference documents, OpenAI agent metadata, standard-library Python helper scripts, and an optional self-hosted server-mode adapter. It is not a hosted service or package-manager plugin.

> **Scope vs. other `spec` skills**: This skill is a *task-package workflow manager* — it creates and tracks `.spec/specs/YYYY-MM-DD_slug/{spec,tasks,checklist}.md` Development Records and exposes internal route/status/tasks/check/done/push script views via `scripts/`. It does **not** act as a "central router that dispatches to other specialized skills". If you also use a `/spec`-router variant (e.g. some personal ECC configurations under `~/.claude/skills/spec`), make sure you know which one a given host has loaded; they share a name but serve different responsibilities and should not be mixed. Slug naming follows `[a-z0-9_-]+` (see `scripts/spec_package_support.validate_slug`); underscores are allowed but consecutive hyphens/underscores and leading/trailing separators are not. The Development Record standard additionally uses `YYYY-MM-DD_<verb>-<object>` (verb from a controlled vocabulary, see `references/naming-and-commits.md`).

## What It Does

- **Single-orchestrator workflow (default) with managed delegation**: the main session owns the critical path, routing, acceptance, and the done/push gates. `scripts/route_decision.py` maps task text to one machine-judged route (`local / explore / build / review / external`); `explore/build/review` may spawn bounded sidecar lanes under the assignment contract (`references/orchestration.md`); loop-convergence work hands the execution segment to a managed slot protocol (e.g. `team-loop`). Delegation never moves routing, acceptance, or gate authorization out of the main session.
- Routes user intent internally through `/spec` and exposes ten user stages: `new`, `goal`, `run`, `check`, `done`, `push`, `update`, `status`, `doctor`, and `organize`.
- Creates `.spec/specs/YYYY-MM-DD_slug/` Development Records containing `spec.md`, `tasks.md`, and `checklist.md`.
- Renders status overviews, task progress, validation, and completion summaries from task-package files.
- Enforces a documented workflow around explicit assumptions, minimal implementation, clear boundaries, verification evidence, and cross-artifact consistency.
- Enforces machine-verifiable issue closure: every discovered problem either resolves to a completed current task or to a follow-up Development Record that this workflow has already executed and archived. Archive, Stop hook, server projection, and Git push share the same five typed dispositions, so free-form "next work" cannot be handed back to the user.
- Minimal task contract: each task needs only `boundary` (what may change) and `verify` (how completion is proven), with optional `id` / `depends-on`; a checkbox requires actually running the verification first.
- Generates `completion-summary.md` and can archive completed task packages under `.spec/specs/archive/`.
- Execution governance layer: `check` reports a deterministic convergence status (converged / not converged plus a gap list; `converged`/`gaps` via `--format json`), and `hooks/claude_stop_guard.py` follows the official Claude Code Stop hook protocol to block claimed completion of unconverged records while reporting the gap list.
- Standardizes `.spec/docs/` knowledge capture and `.spec/architecture/` Module DAGs: task packages record what was done, docs record what was learned, and architecture records what the system currently looks like.
- Governs project-structure review tasks from first principles: audit ownership boundaries, dependency direction, runtime/source/state separation, and entrypoint discoverability before moving files; only restructure when the current layout is proven unreasonable, then verify references, export contents, Module DAGs, READMEs, and tests.
- Enforces Git branch governance: `/spec:new` first requires a clean tree, checks out `main` (or the explicit main branch), fast-forwards it to upstream, and then creates an independent `spec/YYYY-MM-DD_<slug>` integration branch from main; if upstream `fetch` fails with a transport outage (same `remote_unavailable_detected` classifier as `push`), the branch is created from local main and recorded as local mode, while auth, permission, and non-fast-forward failures still stop; `run/check/done` execute on that branch, `push` only merges branches with the `spec/` prefix.
- Supports configurable Git record labels in completion summaries: defaults to `auto` (detects Chinese/English from the `spec.md` title), or `--git-record-language zh|en` to force; commit messages follow Conventional Commits (see `references/naming-and-commits.md`).
- Keeps fresh task packages in clarification-first state until `spec.md` stops containing template placeholders.
- **0.13.0 gate and hygiene hardening**: acceptance evidence carries a freshness anchor (a moved HEAD marks the evidence stale and demands re-capture); unchecked `spec.md` feature boxes join the check gate (fail-closed, with a legacy-baseline byte-equivalence exemption); `check` failures are written back as four-field structured records under dual stop conditions (semantic convergence plus a round budget); credential-hygiene discipline keeps secrets out of task packages, command lines, acceptance evidence, and distillations; a task-shape→execution-surface decision table is pinned by GOLDEN_TRIPLES cross-consistency tests across the three decision scripts.

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
├── book/                            # source-only: training ebook (supersedes the old HTML decks)
│   ├── README.md
│   ├── verify_ebook.py
│   ├── metadata.yaml
│   ├── epub.css
│   ├── src/                         # chapters and appendices
│   ├── examples/                    # teaching example programs
│   └── tests/                       # build-verification tests
├── docs/                            # public user and contributor guides
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
│   ├── import_kiro_specs.py          # source-only one-shot migration tool (excluded from runtime export)
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
├── slots/                           # pluggable slots (shipped with the runtime package; contract in references/slots.md)
│   ├── team-loop/                   # agents-team loop triggering and management
│   └── workflow-runner/             # repository-owned deterministic fan-out execution (parallel review/convergence)
├── tests/                           # source-only: pytest suite (incl. fixtures/kiro)
├── release/                         # source-only: tracked release artifacts (spec-harness-{version} set, byte-identical to the canonical build)
├── .github/                         # source-only: CI and issue templates
└── .github/                         # GitHub issue and pull-request templates
```

Runtime task packages default to `.spec/` in target projects; the public repository does not include maintainer records or user-project state. `CONTRIBUTING.md`, `RELEASE.md`, `SECURITY.md`, `SUPPORT.md`, and `docs/` contain the public contribution, release, security, and Git guides.

- **The task package is the state truth.** `.spec/specs/<slug>/tasks.md` is the task-contract source; checkbox state, acceptance evidence, and completion summaries stay with the package. Each Development Record owns one integration branch; the default router and Stop guard select at most one package by explicit `--slug`, the branch recorded in `spec.md`, the legacy `spec/<slug>` convention, or a sole package outside Git. Ambiguous bindings fail closed.

## Requirements


- Python 3.9 or newer.
- A skill-aware CLI that can load `SKILL.md` style skills (Claude Code, Codex, Gemini CLI, Grok Build, OpenCode, OpenClaw, Hermes, Pi, ZCode, ...).
- No third-party Python dependencies are required by the helper scripts.
- The installer and helper scripts run on Linux, macOS, and Windows.

## Installation

### Installer

Install to both Claude Code and Codex from the repository root:

```bash
bash install.sh
```

The runtime package has one local skill installer at the root `install.sh`; the optional `server/install.sh` is a separate self-hosted service deployment entrypoint. Exported packages and release artifacts no longer include a separate `scripts/install.sh` compatibility shim.

For remote install or update, prefer cloning the repository, checking out a full commit SHA, and then running the installer locally:

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness-src
cd /tmp/spec-harness-src
git checkout v0.13.10
bash install.sh
```

Select hosts and paths with environment variables:

```bash
INSTALL_HOSTS=claude CLAUDE_SKILLS_DIR=~/.claude/skills bash install.sh
INSTALL_HOSTS=claude-desktop bash install.sh
INSTALL_HOSTS=codex CODEX_SKILLS_DIR=~/.codex/skills bash install.sh
INSTALL_HOSTS=codex-desktop bash install.sh
INSTALL_HOSTS=desktop bash install.sh
INSTALL_HOSTS=gemini bash install.sh
INSTALL_HOSTS=grok bash install.sh
INSTALL_HOSTS=opencode bash install.sh
INSTALL_HOSTS=openclaw bash install.sh
INSTALL_HOSTS=hermes bash install.sh
INSTALL_HOSTS=pi bash install.sh
INSTALL_HOSTS=all bash install.sh
```

The following hosts are supported, each installing into that agent CLI's standard skills directory:

| Host token | App | Skills directory | Notes |
| --- | --- | --- | --- |
| `claude` | Claude Code | `~/.claude/skills` | also writes `/spec` plus ten `/spec:<stage>` command files |
| `claude-desktop` | Claude Desktop | `~/.claude-desktop/skills` | skill only, no command files |
| `codex`, `codex-desktop` | Codex | `~/.codex/skills` | both tokens share the directory |
| `gemini` | Gemini CLI | `~/.gemini/skills` | |
| `grok`, `grokbuild` | Grok Build | `~/.grok/skills` | `grokbuild` is an accepted alias token |
| `opencode` | OpenCode | `${XDG_CONFIG_HOME:-~/.config}/opencode/skills` | |
| `openclaw` | OpenClaw | `~/.openclaw/skills` | |
| `hermes` | Hermes | Hermes home + `/skills` (`HERMES_HOME` wins; Windows `%LOCALAPPDATA%\hermes`, otherwise `~/.hermes`) | |
| `pi` | Pi agent | `~/.pi/agent/skills` (relocatable via `PI_DIR`) | |
| `zcode` | ZCode | `~/.zcode/skills` | project-local host |

Combo targets: `desktop` and `rpi` install both the Claude Code and Codex defaults in one pass; `rpi` carries the Raspberry Pi / Linux combo semantics the `pi` token used to have (no hardware detection). `all` (the default) installs every host in the table; pass `INSTALL_HOSTS` explicitly when you only want a subset. Every host directory can be overridden by its same-name variable (e.g. `GEMINI_SKILLS_DIR`, `OPENCODE_SKILLS_DIR`, `PI_SKILLS_DIR`, `HERMES_SKILLS_DIR`); non-default roots require `FORCE=1`.

**Migration note (behavior change)**: `pi` used to be the "Claude + Codex dual-directory" combo token; it is now the Pi agent host (writes `~/.pi/agent/skills` only). Use `rpi` or `desktop` for the old behavior. `claude-desktop` used to write `~/.claude/{skills,commands}`; it is now its own Claude Desktop host (writes `~/.claude-desktop/skills` only). Use `claude` for the old behavior.

For Claude Code the installer creates the `/spec` route entry plus ten user-stage commands: `/spec:new`, `/spec:goal`, `/spec:run`, `/spec:check`, `/spec:done`, `/spec:push`, `/spec:update`, `/spec:status`, `/spec:doctor` (environment self-check and repair), and `/spec:organize` (default new package; never delete, archive under `archive/retired`). `route` is the internal stage resolver inside `/spec`, task details are folded into the `status` overview, and `goal` is the explicit one-shot entrypoint. Other hosts load the skill through their own CLI skill mechanism (Codex/ZCode/Pi trigger it with `$spec`; Gemini CLI, Grok Build, OpenCode, OpenClaw, and Hermes discover it through their native skill loading) and use the same `spec:<stage>` stage aliases. Existing targets created by this installer are moved to `~/.spec-skill-backups/` before the new version is written. If a target has no `.spec-skill-install` ownership marker, the installer refuses to overwrite it by default; set `FORCE=1` only when you intentionally want to replace a user-owned directory. If the target already holds a **different same-name skill** (it has its own `SKILL.md` but no installer marker, e.g. another `/spec` router variant), the installer warns and refuses rather than silently overwriting; set `FORCE=1` to back that skill up under `~/.spec-skill-backups/` before replacing it.

### Manual install

Export a clean runtime skill package:

```bash
python3 scripts/export_skill_package.py --output /tmp/spec --force
```

Install the exported directory into your CLI's local skills directory. The exact path depends on the host CLI, but the installed skill root should contain:

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

Exported contents are governed by the machine rule in `scripts/export_skill_package.py`: the root files `SKILL.md`, `install.sh`, and `pyproject.toml`; the whole directories `agents/`, `hooks/`, `references/`, `server/`, and `slots/`; and every source-repo script except the seven source-only tools (`export_skill_package.py`, `export_public_repo.py`, `build_release.py`, `package_agent_plugin.py`, `skill_watermark.py`, `import_kiro_specs.py`, `migrate_task_ids.py`). Source-repo directories such as `agent-plugin/`, `tests/`, `release/`, and `book/` never enter the runtime package. This export tree is locked by tests against the exporter's actual output, file by file.

For Codex-style skill loading, place the exported directory under your local skills root, then trigger it with:

```text
$spec
spec:goal add payment recovery
spec:new
```

For Claude Code style workflows, use the mapped slash commands after the skill is available in the host environment:

```text
/spec
/spec:goal <goal>
/spec:new
/spec:run
/spec:check
/spec:done
/spec:push
/spec:update
/spec:status
/spec:doctor
/spec:organize
```

After installation, verify the runtime package can initialize a task package:

```bash
python3 /path/to/spec/scripts/init_spec_package.py \
  --root /tmp/spec-install-check \
  --slug 2026-06-12_install-check \
  --title "Install Check"
```

## Quick Start

Create a new task package in a target project. The script commands below are run from this skill source checkout or exported runtime package root, and `--root` points at the actual target project. After the skill is installed into a host CLI, the normal entrypoint is `/spec:goal <goal>`, `/spec:new`, or `$spec` followed by `spec:goal` / `spec:new`.

```bash
python3 scripts/init_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery --title "Payment Recovery"
```

In Git repositories, the init script checks out `main` (or `--main-branch`), fast-forwards it to upstream when configured, creates and checks out an independent `spec/<slug>` branch, and only then writes the `.spec` package. If `fetch` fails with a transport-layer outage (same classifier as `push`), the branch is still created from local main and recorded as local mode. A dirty tree, existing target branch, auth/permission failure, or non-fast-forward main still stops the command so uncommitted work from another branch cannot leak into the new package; pass `--branch spec/2026-06-12_payment-recovery` to choose a non-default branch name. `push` only merges branches with the `spec/` prefix; anything else is rejected with a rename hint. Legacy `feature/` branches whose work started before 2026-08-27 UTC remain mergeable during the grace window, with a rename advisory.


The one-shot entrypoint lets the agent chain planning, task-package creation/resume, execution, review, archival, commit after gates pass, and then hand off to the safe spec:push flow. It is not a standalone script and does not bypass Git hooks:

```text
$spec
spec:goal add payment recovery with tests
# Claude Code: /spec:goal add payment recovery with tests
```

Inspect the next workflow stage:

```bash
python3 scripts/route_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery
```

Render status or tasks:

```bash
python3 scripts/report_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery --view status
python3 scripts/report_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery --view tasks
```

Run single-package validation and the repository-wide disk-truth gate:

```bash
python3 scripts/check_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery
python3 scripts/check_all_spec_packages.py --root /path/to/project
```

`check_spec_package.py` is the single-package machine source of truth for `/spec:check`. `check_all_spec_packages.py` additionally validates active/archive four-file records, v1 issue closure for new archives, explicit slug existence, and target Git trees through `--revision <sha/ref>`. Gate details come from the script output.

Install final Git execution-point gates incrementally (idempotent for identical hooks; refuses to overwrite different project-owned hooks):

```bash
python3 scripts/install_git_hooks.py --root /path/to/project
```

`pre-commit` validates the working tree and Git index. `pre-push` validates each pushed ref at its target SHA and, online, fetches/pins the actual push remote's main SHA as the non-overridable legacy baseline; only genuine local-only mode explicitly selects local main. Touched mode uses trusted `.spec/.trae` roots plus commit-footer attribution, while `SPEC_PUSH_GATE=all` gates every target SHA. Configure extra roots with repeated `--specs-dir` / `SPEC_SPECS_DIRS=path1,path2`. Claude Stop discovers those roots from nested directories and requires an unambiguous `slug#task_XXXX` or archived external-blocker issue ID.

Generate a completion summary for a package that has passed its completion gates:

```bash
python3 scripts/complete_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery
```

Archive a completed task package:

```bash
python3 scripts/complete_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery --archive
```

Archival auto-fills non-placeholder closeout fields from the passed triad and writes a v1 `## 问题处置` block. Use repeated `--issue-disposition '<json>'` values for a risk, external dependency, or work already completed by another record. The five dispositions are `resolved_current`, `resolved_followup`, `accepted_risk`, `external_blocked`, and `non_actionable`; a follow-up target must already be executed, accepted, and archived. Archive mode rejects free-form `--follow-up`, `--open-risk`, `--not-delivered`, and `--deviation` values.

Use English Git record labels:

```bash
python3 scripts/complete_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery --archive --git-record-language en
```

`done` archives the package and creates the local commit. After the working branch has been committed, execute the merge cleanup flow:

```bash
python3 scripts/push_spec_package.py --root /path/to/project --branch spec/2026-06-12_payment-recovery
```

Safety prechecks, dirty-tree / retro-pack handling, and unarchived blocking come from `push_spec_package.py`. Keep the working branch when a remote check fails and fix the cause before retrying; do not bypass the gates with `--no-verify`.

Task-package helpers accept `--root` and `--specs-dir`. Spec roots must be trusted relative paths under root; empty values, absolute paths, `..` traversal, and gate-excluded runtime directories such as `.git`, `.claude`, `node_modules`, `build`, `dist`, and `vendor` are rejected. `--allow-incomplete` may generate a draft summary but cannot be combined with `--archive`.

In a skill-aware CLI, trigger the skill with `$spec`, then use stage aliases: `spec:new`, `spec:goal`, `spec:run`, `spec:check`, `spec:done`, `spec:push`, `spec:update`, `spec:status`, `spec:doctor`, and `spec:organize`. For Claude Code, these map to `/spec`, `/spec:goal`, `/spec:new`, `/spec:run`, `/spec:check`, `/spec:done`, `/spec:push`, `/spec:update`, `/spec:status`, `/spec:doctor`, and `/spec:organize`; `route` is the internal stage resolver inside `/spec`, and task details are folded into `status`. `done` archives and commits, then hands off to the `push` script; `goal` reuses that same done/commit/push chain; any verification or safety-precheck failure stops the flow.

## Exporting A Runtime Skill Package

To export only the runtime skill files:

```bash
python3 scripts/export_skill_package.py --output /tmp/spec --force
```

The export includes `SKILL.md`, root `install.sh`, `pyproject.toml`, `agents/`, `hooks/`, `references/`, `server/`, `slots/`, and runtime helper scripts while leaving source-maintenance packagers out of the runtime payload. The optional server-mode adapter exposes `start`, `result`, and `health` for a self-hosted async workflow platform; it initializes and projects task-package state but does not replace `run`, `check`, or `done`. Local skill installation starts from the package-root `install.sh`; server deployment starts from `server/install.sh`.

The exported runtime package must include `issue_closure_support.py` and `spec_package_support.py`; otherwise completion, server/Git closeout, or init/route/report/check cannot share the machine closure contract.

The export smoke independently verifies the runtime layout, runs the doctor self-check, and exercises the task-package initialization flow.

## Verification

The helper scripts are intentionally standard-library Python. A basic syntax check is:

```bash
python3 -m py_compile scripts/*.py server/*.py
bash -n server/install.sh
```

Repository-level regression verification needs development dependencies:

```bash
python3 -m pip install -e '.[dev]'
```

Then run smoke, pytest, and lint:

```bash
python3 scripts/smoke_test_spec_skill.py
python3 -m pytest tests/
ruff check scripts/ server/ hooks/ tests/ book/
ruff format --check scripts/ server/ hooks/ tests/
```

`smoke_test_spec_skill.py` verifies package/export lifecycles. pytest covers the task-package gates, the route/status/check/archive chain, update checkpoints, and Git gates.

For manual behavior checks, create a temporary task package with `init_spec_package.py`, then run route, report, check, and complete. A fresh package cannot pass the single-package gate until tasks and checklist are complete; active-but-unarchived packages hard-block push by default, and only explicit `--allow-unarchived` downgrades that one condition. Broken active/archive records, missing explicit slugs, invalid v1 issue closure, and unattributed commits always block.

## Versions And Releases

Stable versions and checksums are published on [GitHub Releases](https://github.com/MicTx/spec-harness/releases). See [RELEASE.md](RELEASE.md) for installation, upgrade, rollback, and SHA-256 verification instructions.

For contributions, read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Git workflow guide](docs/git-workflow.en.md). Report security issues through [SECURITY.md](SECURITY.md).

## Scope

This project provides workflow instructions, templates, verification scripts, and an optional self-hosted server-mode adapter that initializes and projects task-package state for a remote platform. It does not operate a hosted service and does not spawn background model tasks; `spec:push` only provides local Git merge/push/branch-cleanup handoff with strong safety prechecks. The adapter authenticates `/start` and `/result` with a `SPEC_SERVER_TOKEN` Bearer token (`/health` stays anonymous for probes), refuses to start on a non-loopback address without a token, and bounds subprocess fan-out with `MAX_CONCURRENT_WORK` (excess requests receive 429); internet-facing deployments must also configure `ALLOWED_ROOTS` and should terminate TLS at a trusted reverse proxy.

## License

This project uses a source-available, non-commercial license. Commercial use and sale are not permitted. See [LICENSE](LICENSE) for the complete terms.

### Stable identity and verification

Optional `id: task-name` and explicit dependencies preserve task identity. Migration previews by default and does not rewrite archives. Self-reported results never complete a task: run the task's `verify` before checking it off.

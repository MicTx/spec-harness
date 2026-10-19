---
name: spec
description: >-
  Spec Harness turns multi-step work into a recoverable, verifiable task package. Use for spec commands
  and tracked multi-step development. The main session plans, executes,
  verifies, and archives inside one session. Skip simple fixes and experiments.
---

# Spec Workflow

When a coding task outlives one conversation, the missing tool is usually a shared memory of decisions and evidence. Spec Harness supplies that memory as a Development Record under `.spec/specs/YYYY-MM-DD_slug/`: `spec.md` holds scope, `tasks.md` holds executable work (legacy v1 packages add a `checklist.md`). Scripts in `scripts/` route and validate the record.

The contract is intentionally small. Every task says what it may change (`boundary`) and how completion is proved (`verify`). The main session still owns routing, integration, acceptance, and Git closeout; a worker result never replaces those decisions.

## When to use

Use spec for multi-step or cross-session development that needs tracked scope, tasks, and acceptance criteria, when the user explicitly requests spec mode or task packages, or at a project start / phase boundary needing an interactive planning pass before the autonomous chain. Skip it for small reversible fixes, one-off experiments, or work whose plan and proof fit one short command sequence: for an obviously small task (one-line reversible fix with its test, typo, copy tweak), advise against a package and offer the lightweight path — fix + test directly, then push with a `Spec: none (<非空理由>)` footer; the user decides whether they want the record anyway.

## Commands

Read only the current stage's file under `references/stages/` ([`references/commands.md`](references/commands.md) is their index). Do not read entire reference files upfront.

### Core workflow

| Command | Purpose |
| --- | --- |
| `/spec` | Resolve the next action from the current package |
| `/spec:new` | Create task package with scope, tasks, and acceptance criteria |
| `/spec:run` | Execute all tasks; resumes from `tasks.md` state |
| `/spec:check` | Review round; actionable issues are written back and fixed in-round |
| `/spec:done` | Archive work and create the package commit |
| `/spec:push` | Merge to main, push, and delete the merged branch (after `done` only) |
| `/spec:update` | Modify scope or tasks mid-flight |
| `/spec:status` | Overview of packages, progress, and blockers |

### Optional entry points

| Command | Purpose |
| --- | --- |
| `/spec:goal` | one-shot: plan, execute, review, archive, and commit |
| `/spec:autoplan` | interactive planning rounds, then framework/detail/review passes as a serial fresh-session chain, gated to autorun readiness |
| `/spec:autorun` | recursive chain: finish the active package, plan the next round, spawn a fresh session — until every planned feature is done |
| `/spec:doctor` | environment self-check and repair |
| `/spec:organize` | structure audit; audit-only by default, `--apply` creates the package; never delete, archive under `archive/retired` |

Install profile: `new / run / check / done / push` ship in every install; the extended stages need `INSTALL_PROFILE=full` — a core install answers them with the reinstall hint instead of executing them (boundary: [`references/commands.md`](references/commands.md)).

## Core capability: single-orchestrator execution

The main session is the orchestrator and owns routing, acceptance, and the `done`/`push` gates; it does planning, integration, independent verification, and all shared glue files itself. Bounded sidecar work may go to in-process subagents only after a machine route decision (`python3 scripts/route_decision.py --root <project> --slug <slug>`) and a written assignment contract (goal / scope / excluded areas / output / verification); delegation never moves routing, acceptance, or gate authorization out of the main session, and one session owns one package on one integration branch. With `channel_profile.shared_pool`, keep fanning out under the constrained-channel protocol in [`references/orchestration.md`](references/orchestration.md); never clamp lane count. The full delegation contract (five routes, lane ownership, `### 5.4 编排策略`) lives there and is never restated here.

### Managed paths

- `local` (default): the main session executes on its own.
- Sidecar lanes: `explore` / `build` / `review` with clean ownership slices → bounded in-process subagents under the assignment contract; the main session merges and verifies. Orchestrator/planner contracts: `agents/orchestrator.md`, `agents/planner.md`.
- Slot protocol: slot-trigger shapes hand the execution segment to the slot's README protocol (see [Extension slots](#extension-slots)); no fake task results.
- Invariants that never move: `boundary`/`verify` discipline, honest checkbox verification, check gates, and `done`/`push` authorization.

## Execution loop

Route first (`scripts/route_spec_package.py`; no active package → `new`), verify the integration branch, and give every task a `boundary` (what may change) and a `verify` (how completion is proven), with `depends-on` when order matters. Produce the route decision (`python3 scripts/route_decision.py --root <project> --slug <slug>`; contract: [`references/stages/run.md`](references/stages/run.md)) and write `### 5.4 编排策略` when the route is not `local`, then work ready tasks in dependency order — a task is checked off only after its `verify` actually ran. Converge: all tasks checked → check gates → the acceptance surface with real evidence → `done`/`push` as authorized; a blocked task gets a `!` marker and surfaces to the user, never a silent skip.

Bounded internal reads: when one view replaces several reads use `route_spec_package.py --brief` (state, contracts) or `spec_verify.py show` (evidence, freshness); single files and `measure_skill_surface.py` stay direct; on error use plain reads.

## Progress and continuation

Continuation is `/spec:run` itself: next ready task → verify → check off → repeat; after an interruption, re-running `/spec:run` resumes from `tasks.md` state. Cross-collaborator boundaries (pause, takeover, agent-cluster lanes) go through the optional `handoff.md` (`scripts/spec_handoff.py`; contract: [`references/handoff.md`](references/handoff.md)). User pause/abort, pending user messages, and project trust prompts always outrun the loop; the optional `hooks/claude_stop_guard.py` keeps convergence explicit without expanding Git authorization.

## Execution rules

1. **User-facing output**: Spec Harness is the internal method, not the user's subject. Every update starts from the user's goal and actual project progress: readable project name, real tasks, current work, concrete issues, delivered results, and verification. Do not expose the stage pipeline, gate counts, package maintenance, or `/spec:*` command handoffs as the main narrative; use [`references/output-contracts.md`](references/output-contracts.md), where script output is authoritative.

2. **Blocking conditions**:
   - Can't `run` while `spec.md` has template placeholders or blockers
   - Can't `done` without `boundary` and `verify` in every task
   - Can't `done` unless the acceptance surface is fully checked with passing acceptance and at least one non-placeholder evidence (script/test/build output)
   - A historical `update-checkpoint.json` (retired mechanism) marks a half-applied package: route renders a prominent advisory and check/done refuse until Git-history recovery resolves the triad and the file is deleted (contract: [`references/stages/update.md`](references/stages/update.md))

3. **Issue handling**: Actionable issues found during `check` must be written back to the current package and fixed in the same round. Do not output executable fixes as user deliverables. Only ask the user about a project-shaping constraint, a decision that cannot be reasonably assumed, or an external dependency the agent cannot resolve. Closed issues must have one of: `resolved_current`, `resolved_followup`, `accepted_risk`, `external_blocked`, `non_actionable`.

4. **Validation**: declare the verification level in `spec.md` (`package` / `integration` / `project`); `check`/`done` run that declared level with real evidence — a verbal "passed" never counts (contract: `references/stages/check.md`).

5. **Commit/push separation**: only `done` archives and commits; only `push` merges, pushes, and deletes branches; never combine file changes and a commit in one bash call; never `--no-verify` (contract: `references/stages/done.md`, `references/stages/push.md`).

6. **Branch safety**: active unarchived packages block `push` by default; branch mutation happens only on the package's recorded integration branch (contract: `references/stages/push.md`).

7. **Branch naming and binding**: spec branches use the `spec/` prefix, each active package binds exactly one integration branch, and initialization refuses a branch already bound; `push` merges only `spec/` branches (legacy pre-2026-08-27 branches stay pushable in the grace window with a rename advisory; contract: [`references/naming-and-commits.md`](references/naming-and-commits.md)).

8. **Changelog generation**: `scripts/generate_changelog.py` converts Git history into changelog entries on demand — a manual release-notes helper, never an automatic `done`/`push` step (guide: [`references/changelog-guide.md`](references/changelog-guide.md)).

9. **Distillation destination**: `done`/`goal` distillation lands only as the incremental `AGENTS.md` proposal inside `completion-summary.md` — never applied automatically, never written to global carriers or user-level memory files; distill project-specific engineering facts only (contract: `references/stages/done.md`).

## Extension slots

Slots are self-contained `slots/<name>/` directories (manifest declaration + README protocol) validated by `python3 scripts/slot_registry.py list|validate` and shipped by `install.sh`; the full contract lives in [`references/slots.md`](references/slots.md). Activation rule: when a task's shape matches a slot's triggers, hand the execution segment to that slot's README protocol (`team-loop` → `slots/team-loop/scripts/loop_route.py`; `workflow-runner` → `slots/workflow-runner/scripts/workflow_route.py`); slot activation is independent of orchestration routing (`scripts/route_decision.py`), and routing, acceptance, and the `done`/`push` gates never leave the main session.

## Reference documentation

[`references/00-readme.md`](references/00-readme.md) is the reading index — start there when the current stage's page is unclear. Read these only when the current task needs them:

- [`references/commands.md`](references/commands.md) — stage index and detailed stage execution
- [`references/output-contracts.md`](references/output-contracts.md) — required output format per stage
- [`references/templates.md`](references/templates.md) — task package templates
- [`references/orchestration.md`](references/orchestration.md) — multi-agent routing contract
- [`references/slots.md`](references/slots.md) — extension slot contract
- [`references/storage-and-archive.md`](references/storage-and-archive.md) — directory structure and archiving
- [`references/handoff.md`](references/handoff.md) — session-boundary handoff document
- [`references/naming-and-commits.md`](references/naming-and-commits.md) — slug naming and commit conventions
- [`references/changelog-guide.md`](references/changelog-guide.md) — changelog generation and integration
- [`references/engineering-philosophy.md`](references/engineering-philosophy.md) — four core principles

Protocol migrations do not modify historical archives.

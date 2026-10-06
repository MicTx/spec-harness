---
name: spec
description: >-
  Spec Harness turns multi-step work into a recoverable, verifiable task package. Use for spec commands
  and tracked multi-step development. The main session plans, executes,
  verifies, and archives inside one session. Skip simple fixes and experiments.
---

# Spec Workflow

When a coding task outlives one conversation, the missing tool is usually a shared memory of decisions and evidence. Spec Harness supplies that memory as a Development Record under `.spec/specs/YYYY-MM-DD_slug/`: `spec.md` holds scope, `tasks.md` holds executable work, and `checklist.md` holds acceptance. Scripts in `scripts/` route and validate the record.

The contract is intentionally small. Every task says what it may change (`boundary`) and how completion is proved (`verify`). The main session still owns routing, integration, acceptance, and Git closeout; a worker result never replaces those decisions.

## When to use

Use spec when:
- Multi-step or cross-session development tasks
- Work requiring tracked scope, tasks, and acceptance criteria
- User explicitly requests spec mode or task packages

Skip it for a small reversible fix, a one-off experiment, or work whose complete plan and proof fit in one short command sequence.

## Commands

Read only the relevant section from `references/commands.md` for your current stage. Do not read the entire commands or output-contracts files upfront.

### Core workflow

| Command | Purpose |
| --- | --- |
| `/spec` | Resolve the next action from the current package |
| `/spec:new` | Create task package with scope, tasks, and acceptance criteria |
| `/spec:run` | Execute all tasks; resumes from package state if interrupted |
| `/spec:check` | Human-AI review round; writes actionable issues back to package |
| `/spec:done` | Archive completed work and create the Git commit |
| `/spec:push` | Merge to main, push, and delete merged branch (after `done` only) |
| `/spec:update` | Modify scope or tasks mid-flight |
| `/spec:status` | Show overview of all packages, progress, and blockers |

### Optional entry points

Read `references/commands.md` for details if using these:

| Command | Purpose |
| --- | --- |
| `/spec:goal` | one-shot: plan, execute, review, archive, and commit |
| `/spec:autorun` | recursive chain: finish the active package, plan the next round from the project planning documents, and spawn a fresh Terminal session with the autorun prompt until every planned feature is done |
| `/spec:doctor` | Environment self-check and repair |
| `/spec:organize` | First-principles structure audit. Default new package in the target project; never delete, archive under `archive/retired`; disposition covers every member of a confirmed class |

## Core capability: single-orchestrator execution

The main session is the orchestrator and owns routing, acceptance, and the `done`/`push` gates. It does the critical path itself — planning, integration, independent verification, acceptance, and all shared glue files. Bounded sidecar work may be delegated to in-process subagents only after a machine route decision (`python3 scripts/route_decision.py --text "<goal>"`) and a written assignment contract (goal / scope / excluded areas / output / verification; see `references/orchestration.md`). Delegation never moves routing, acceptance, or gate authorization out of the main session, and one session still owns one package on one integration branch at a time; two sessions must not write the same package. When the route decision flags `channel_profile.shared_pool` (single gateway/model pool), keep fanning out in parallel — apply the constrained-channel protocol in `references/orchestration.md` (slice minimization + lane-death salvage + burst backoff); never clamp lane count.

### Managed paths

- Default: `local` — the main session executes on its own.
- Sidecar lanes: for `explore` / `build` / `review` routes with clean ownership slices, the main session spawns bounded in-process subagents under the assignment contract in `references/orchestration.md`, then merges results and runs final verification itself. Claude Code publishes installer-owned copies of `agents/orchestrator.md` and `agents/planner.md` into `~/.claude/agents/`; on hosts without those names, read the portable contract and inject it into an available general-purpose agent.
- Slot protocol: if the task matches a slot's triggers (see Extension slots below, e.g. `team-loop` for agents-team loop-until-converged work), the main session follows that slot's README protocol for the execution segment only; no fake task results.
- Invariants that never change: task-level `boundary`/`verify` discipline, honest checkbox verification, check gates, and `done`/`push` authorization all stay with the main session. Outsourcing work outside these managed paths remains forbidden.

## Execution loop

1. Route/read the current package and verify its integration branch. Fill `tasks.md` so every task carries `boundary` (what may change) and `verify` (how completion is proven), plus explicit `depends-on` when order matters. Optional stable `id: task-name` gives a task an identity that survives reordering.
2. Produce a route decision with `python3 scripts/route_decision.py --text "<package goal>"` and write `### 5.4 编排策略` when the route is not `local`. Work ready tasks in dependency order. Before checking a task off, actually run the verification its `verify` line describes; a checkbox without a run verification is not done. For `explore`/`build`/`review` with clean ownership, spawn bounded sidecar lanes under the assignment contract in `references/orchestration.md`. If the package's task shapes match a registered slot's triggers (e.g. `team-loop`), hand the execution segment to that slot's managed protocol instead of improvising a loop. Merge sidecar results on the main thread before editing shared boundaries.
3. Keep going until all tasks are checked, then run the check gates, finish `checklist.md` with real evidence, and continue to `done`/`push` as authorized.
4. If something is blocked, record the blocker on the task (a `!` marker or a blocked status line) and surface it to the user; do not silently skip or fake completion.

## Progress and continuation

Continuation is the `/spec:run` instruction itself: read the package, do the next ready task, verify, check it off, repeat until the package converges. If a session is interrupted, re-running `/spec:run` resumes from `tasks.md` state — checked tasks stay done, the next unchecked ready task is where work continues.

When the boundary is cross-collaborator — the same human returning later, a teammate taking over, a fresh agent session, or an agent-cluster lane — the package's optional `handoff.md` carries the transfer: append one entry on pause/takeover/escalation/lane-end (`python3 scripts/spec_handoff.py update --slug <slug> ...`), and a resuming session reads it first (`show`) plus the route 交接 hint. The document is a generated snapshot head (branch/HEAD/task anchors) plus an append-only entry log whose required slots adapt to the collaboration mode (`solo|team|agent|cluster`); the check gate validates it when present and `done` closes it with a terminal entry. Full contract: `references/handoff.md`.

Pause/abort by the user, pending user messages, and project trust prompts always take priority over continuing an execution loop.

The optional Claude Stop hook (`hooks/claude_stop_guard.py`) checks package convergence. Binding failures and outstanding gaps remain explicit. Hooks never expand Git authorization.

## Execution rules

1. **Start with routing**: Run `scripts/route_spec_package.py` first. If no active package exists, use `new`.

2. **User-facing output**: Spec Harness is the internal method, not the user's subject. Every update starts from the user's goal and actual project progress: readable project name, real tasks, current work, concrete issues, delivered results, and verification. Do not expose the stage pipeline, gate counts, package maintenance, or `/spec:*` command handoffs as the main narrative. Use `references/output-contracts.md`; script output is authoritative where available.

3. **Blocking conditions**:
   - Can't `run` while `spec.md` has template placeholders or blockers
   - Can't `done` without `boundary` and `verify` in every task
   - Can't `done` unless `checklist.md` is fully checked with passing acceptance and at least one non-placeholder evidence (script/test/build output)
   - An interrupted `/spec:update` leaves an unresolved `update-checkpoint.json`; route hard-blocks the package until `scripts/update_checkpoint.py` `rollback`/`complete` resolves it (lifecycle contract: `references/commands.md` `/spec:update`)

4. **Issue handling**: Actionable issues found during `check` must be written back to the current package and fixed in the same round. Do not output executable fixes as user deliverables. Only ask the user about a project-shaping constraint, a decision that cannot be reasonably assumed, or an external dependency the agent cannot resolve. Closed issues must have one of: `resolved_current`, `resolved_followup`, `accepted_risk`, `external_blocked`, `non_actionable`.

5. **Validation**: each new package declares a verification level in `spec.md`: `package` (default, focused checks for the changed boundary), `integration` (focused checks plus directly affected cross-module paths), or `project` (full project suite). `check` and `done` use the declared level and evidence; they do not require a whole-project suite for every package. `scripts/check_spec_package.py` remains the single source of truth for package state, while `check_all_spec_packages.py` validates package records before commit/push. Verbal "passed" doesn't count.

6. **Commit/push separation**:
   - Only `done` archives work and creates the package commit
   - `goal` reuses `done` for archive+commit; it does not skip it
   - Only `push` merges, pushes, and deletes branches
   - Never combine file changes and commits in one bash call
   - Never use `--no-verify`

7. **Branch safety**: Active unarchived packages block `push` by default. Branch mutation only happens on the package's own recorded integration branch.

8. **Branch naming and binding**: All branches created by this skill must use the `spec/` prefix (e.g., `spec/feature-name`). Each active Development Record records exactly one integration branch, and initialization refuses a branch already bound to another active package. The `push` command only merges branches with the `spec/` prefix to main; legacy branches whose work started before 2026-08-27 UTC stay pushable during the grace window with a rename advisory. This prevents pollution of other normal branches, keeps spec workflow isolated, and prevents two live packages from sharing one branch.

9. **Changelog generation** (optional tooling): `scripts/generate_changelog.py` converts Git history into standardized changelog entries on demand (`--from`/`--to`, `--spec-only`, `--output`, `--prepend`). It is a manual helper for release notes, not an automatic step of `done`/`push`.

10. **Distillation destination**: Knowledge distilled at `done`/`goal` lands only in the executing project's own `.spec/docs/` — the `.spec/` tree inside the repository the task package runs in. Never write spec experience, process rules, or any memory into global carriers (`~/.claude/CLAUDE.md`, user-level `AGENTS.md`, or any user-level memory file). Distill only project-specific engineering facts; spec process mechanics live in this skill, not in distillations.

Read `references/commands.md` for stage-specific details. See `references/storage-and-archive.md` for directory structure and archiving.

## Extension slots

Pluggable capability mechanism for spec: each `slots/<name>/` is a self-contained directory with a manifest declaration, validated by the registry, and shipped by `install.sh`. The full contract lives in `references/slots.md`.

- Enumerate/validate: `python3 scripts/slot_registry.py list|validate`
- Hook install/remove: `python3 scripts/install_slot_hooks.py --slot <name> [--remove]`
- Activation rule: when a task's shape matches a slot's triggers, the execution segment is handed to that slot's README protocol (see the Managed paths under Core capability). Orchestration routing (`scripts/route_decision.py`) is independent of slot activation.
- Currently built in: `team-loop` — agents-team loop-until-converged execution (→ see `slots/team-loop/README.md`). When the run loop matches its triggers, decide with `slots/team-loop/scripts/loop_route.py --text "<package goal>"` and then follow its protocol for the execution segment; routing, acceptance, and the done/push gates never move out of the main session.
- Also built in: `workflow-runner` — delegating an execution segment to the repository-owned deterministic driver: batch fan-out, multi-dimension parallel review, perspective panels, review-fix loops (→ see `slots/workflow-runner/README.md`). Decide with `slots/workflow-runner/scripts/workflow_route.py --text "<package goal>"` — its `suggestedSurface` always names the repository-owned driver; unavailable backends degrade to ordinary sidecar orchestration or `team-loop`. Routing, acceptance, and the done/push gates never move out of the main session.

## Reference documentation

Read these files only when needed for your current task:

- `references/00-readme.md` — reading order guide
- `references/commands.md` — detailed stage execution (read only your current stage section)
- `references/output-contracts.md` — required output format per stage
- `references/templates.md` — task package templates
- `references/orchestration.md` — multi-agent routing contract (five-route vocabulary, assignment contract, 5.4 section)
- `references/slots.md` — extension slot contract (manifest/registry/installer)
- `references/changelog-guide.md` — changelog generation and integration
- `references/storage-and-archive.md` — directory structure and archiving
- `references/handoff.md` — session-boundary handoff document (snapshot + append-only entry log, mode-adaptive slots)
- `references/naming-and-commits.md` — slug naming and commit conventions
- `references/engineering-philosophy.md` — four core principles

Protocol migrations do not modify historical archives.

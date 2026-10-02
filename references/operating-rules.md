# Operating Rules

## When to use

- Building from scratch, unclear scope, work that spans cycles, high early-decision cost
- Needing scope, tasks, and acceptance in one source of truth
- The user explicitly asks for spec or task packages

## When not to use

- Adding a button, fixing a small bug, changing copy
- A clear short plan exists and the work will not span cycles
- One-off experiments not worth maintaining a task package for

## Core

The four principles and their conflict order live in `engineering-philosophy.md`. The three on-disk documents are the state machine: `spec.md` owns scope, `tasks.md` owns execution, `checklist.md` owns acceptance.

- Do not start while assumptions are unwritten or verification is undefined.
- Every task must have `boundary` / `verify`.
- No drive-by refactoring; no unrequested abstractions planted for later.
- Actionable issues found during the work must be written back into the current task package and completed this round; executable fixes are never handed to the user as homework. Only three things may interrupt the user: a project-shaping constraint, a decision that cannot be reasonably assumed, and an external dependency the agent cannot execute.
- After `done` archives, finish this round with a single direct commit; `goal` reuses `done` and never skips it; only `push` may merge/push/delete branches.
- `--allow-incomplete` never combines with archiving.
- The main session is the orchestrator: it plans, implements the critical path, verifies, and accepts. Bounded sidecar work is delegated only after `scripts/route_decision.py` plus a written assignment contract (`references/orchestration.md`). Loop-shaped work follows a managed slot's README protocol (e.g. `team-loop`). Outsourcing that bypasses these managed paths stays forbidden.
- Before checking off a task, actually run the verification its `verify` line describes; self-reported results are not completion.
- Do not add new `CLAUDE.md`, `CURSOR.md`, or other default carriers, and never write spec distillations into global carriers: distilled knowledge lands in the executing project's own `.spec/docs/` only, as project-specific engineering facts.
- Script/template fences are the sources of truth; other carriers point, never copy gate lists. sales-kit is not a runtime obligation.

Stage steps live in `commands.md`. Directories and archiving live in `storage-and-archive.md`.

## Anti-patterns

- Splitting tasks before writing assumptions; writing scope without non-goals.
- Tasks without `boundary` / `verify`; finishing without running check.
- Writing executable gaps as user homework.
- Starting work on a protected branch or a dirty worktree.
- Pushing discrete changes without a spec, skipping retro-pack.
- Reshuffling directories for tidiness, or deleting historical `.spec` records.
- Distilling spec process mechanics, or routing any distillation into a global carrier instead of the executing project's `.spec/docs/`.

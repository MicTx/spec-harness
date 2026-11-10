---
name: spec
description: >-
  Spec Harness turns multi-step work into a recoverable, verifiable task package. Use for spec commands,
  and also without them when a request spans independent acceptance surfaces, several implementers, or
  an unfinished tracked task. Skip small reversible fixes, read-only work, and experiments.
---

# Spec Workflow

A task package lives in `.spec/specs/YYYY-MM-DD_slug/`: `spec.md` holds scope, `tasks.md` holds executable work. Scripts route and validate it; the disk is the source of truth.

## When to use

Decide from the work's shape before the first substantive edit. Create a package when the work has independent acceptance surfaces, several implementers, or must survive a session boundary; resume the active package bound to the current branch. A one-line fix with its test, a typo, read-only investigation or an experiment stays direct: fix + test, and commit with `Spec: none (<reason>)` where the repository allows it. The user can always ask for a package anyway.

Package tiers (`init_spec_package.py --workflow`): `lite` (default) records goal, assumptions, scope, minimal path and verification; `full` adds intent and option ledgers — use it when the user asks or the work involves risks such as auth, billing, secrets, data migration or destructive operations.

## Core loop

1. **Resume**: read the active package's `tasks.md` and `handoff.md` (if any), then `git log -5`. No active package → `new`.
2. **Plan** (`new`): `python3 scripts/init_spec_package.py --root <project> --slug YYYY-MM-DD_<verb>-<object> --title "<title>"`, then fill the placeholders. Every task gets a `boundary` and a `verify` command; add `depends-on` when order matters.
3. **Execute** (`run`): take ready tasks in dependency order; run each `verify` before ticking it. Mark a blocked task `!` and say why.
4. **Check** (`check`): `python3 scripts/spec_verify.py run --root <project> --slug <slug> --all`, then `python3 scripts/check_spec_package.py --root <project> --slug <slug>`. Recover in-session: follow each gate's `Recovery:` route; write genuinely new defects back as tasks, reuse open tasks and never duplicate aggregate counts. Wait for authority, unrepaired blockers or interruption.
5. **Close** (`done`, then `push` only when authorized): `complete_spec_package.py --archive` writes the summary and archives; commit with a `Spec: <slug>` footer.

## Hard rules

1. Every task has a `boundary` and a `verify`; tick only after `verify` actually ran and passed.
2. Only `done` commits; only `push` merges, pushes and deletes branches, and only after explicit user authorization.
3. Never write to protected branches (`main`, `master`, `develop`, `release/*`, …; list in `scripts/spec_package_support.py`). One package binds one `spec/` branch.
4. Never use `--no-verify`; never change files and commit in the same shell call; never stash, reset or overwrite user changes to make a gate pass.
5. Classify every question: `[fact]` inspects code/data or a read-only command; `[reversible]` picks the in-scope option by reversal cost and regret and records the choice; `[decision]` covers permission, irreversibility, preference or external dependency and closes only with `authority: user:<reference>`. Investigate the rest with read-only research or an installed skill. External queries disclose their contents: do not send project data, credentials or private paths without authorization. No label grants permission: host and user authorization still govern Git writes, credentials, global configuration, publication and destructive actions.
6. Report to the user in project terms — goal, progress, problems, results — not stage names or gate counts ([`references/output-contracts.md`](references/output-contracts.md)).

## Commands

Read only the current stage's page under `references/stages/`; [`references/commands.md`](references/commands.md) is the index.

| Command | Purpose |
| --- | --- |
| `/spec` | Resolve the next action from disk |
| `/spec:new` | Create a package |
| `/spec:run` | Execute tasks |
| `/spec:check` | Verify and write failures back |
| `/spec:done` | Archive and commit |
| `/spec:push` | Merge, push, delete the merged branch (authorized only) |
| `/spec:doctor` | Environment self-check |

Principles and the defense admission test: [`references/engineering-philosophy.md`](references/engineering-philosophy.md). Reading index: [`references/00-readme.md`](references/00-readme.md).

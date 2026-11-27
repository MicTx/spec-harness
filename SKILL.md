---
name: spec
description: >-
  Spec Harness turns substantial work into a recoverable, verifiable task package. Use for spec
  commands, Spec Harness updates, and work spanning acceptance criteria, implementation, or delivery.
  Skip small reversible fixes, read-only investigation, and experiments.
---

# Spec Workflow

Update a stable install: `spec-harness update` (`--check` to inspect; fallback: `~/.local/bin/spec-harness`). It installs releases, not `main`.

A v3 package holds `spec.md`, `tasks.md`, and evidence under `.spec/specs/YYYY-MM-DD_slug/`; it is the source of truth. The Python CLI prints concise cards by default; use `--json` for machine-readable output.

## Two Phases

`/spec` routes an unbound goal to `plan`, a bound package to `update`. Plan backward from failure, then `plan-go`. For a small reversible goal on a clean checkout, `/spec:run <goal>` uses the [two-call in-place quick path](references/exec.md); otherwise follow the ready DAG.

No session brief: run `python3 scripts/spec.py next`. On fresh failure evidence, replan the next step within the frozen goal and acceptance. Stop does not count rounds or resume from repeats; see [recovery](references/exec.md#delivery-recovery).

## Autonomy Contract

The user is the last resort, not the first; climb the ladder: resolve from repository evidence and frozen policy, then decide reversible in-scope work yourself. Only irreversible or unauthorized work reaches the user, at most four cards, once per package. Re-asking what evidence already answers is a violation. Do not assign the user a repair; stop at `delivered`, `parked-only`, or `stalled`.

## Hard Rules

1. Each machine task has `id`, path-glob `boundary` and backticked `verify`; `expect` optionally runs a planning probe. Only recorded checkpoints advance the standard DAG.
2. Before `plan-go`, edit only `.spec/` package files. `plan-go` records prior dirt; boundary-inside changes deliver, outside changes block.
3. Commits, archive, merge, and push are harness actions; an active/archived package rejects direct Git commit/push and hook bypasses (`--no-verify`, `-n`, hooks-path).
4. Standard `plan-go` binds one non-protected branch, using an isolated worktree on protected branches. Eligible quick work stays in the current checkout, is verified in place, and never stages, commits or pushes. Never overwrite or stash user changes.
5. Boundary edits require a matching task or planned amendment. Audit Bash changes from the final diff.
6. Irreversible forks require `by: user <reference>`. New dependencies and compatibility breaks require charter authority.
7. `check` preflights the gate, runs each distinct frozen verify once on the final product tree, then archives and applies V4. `--check-only` never verifies or delivers. Follow the [recovery loop](references/exec.md); acceptance stays in ordinary declared criteria and verification.

## Commands

| User command | Purpose |
| --- | --- |
| `/spec <goal>` | Plan when no package is bound; otherwise continue execution |
| `/spec:plan <goal>` | Create or refine the package: lint, forks, `plan-go` |
| `/spec:run [goal]` | Resume a package or run a small goal in the current checkout |
| `/spec:repair <slug>` | Create a regular v3 package with `kind: repair` and `repair-of` metadata, then follow the standard plan and gate |
| `/spec:check` | Repair and recheck before delivery; park only decisions requiring authority |
| `/spec:push` | Authorized finish: merge and push the archived package on this branch |
| `/spec:doctor` | Check or repair runtime, session hooks and legacy Git hooks |
| `/spec /purge` | `spec.py purge`: keep records local; no commit/push |

Planning: [plan](references/plan.md). Execution: [exec](references/exec.md). Format: [format](references/format.md). Philosophy: [philosophy](references/philosophy.md). Git: [git](references/git.md).

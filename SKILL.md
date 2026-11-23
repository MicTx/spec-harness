---
name: spec
description: >-
  Spec Harness turns substantial work into a recoverable, verifiable task package. Use for spec
  commands, Spec Harness updates, and work spanning acceptance surfaces, implementers, or sessions.
  Skip small reversible fixes, read-only investigation, and experiments.
---

# Spec Workflow

Update Spec Harness: `spec-harness update` (check: `--check`; fallback: `~/.local/bin/spec-harness`). Old installs need a newer release first.

A v3 package holds `spec.md`, `tasks.md`, and evidence under `.spec/specs/YYYY-MM-DD_slug/`; it is the source of truth.

## Two Phases

Planning is thick: `/spec` routes an unbound goal to `plan`, a bound package to `update`. Before freeze: read-only Recon/Intent, plan backward from failure, `spec.py plan-lint` to zero, resolve forks, `plan-go`. After Go, execution is thin: follow the ready task DAG, verify, checkpoint, decide within charter.

No session brief: run `python3 scripts/spec.py next`.

## Autonomy Contract

The user is the last resort, not the first — and never assigned work: a mid-work problem is the agent's to close, by agent loop or any means, never the user's. Before asking, climb the ladder: (1) resolve from repo, charter, lessons, or runs ledger; (2) in-scope and reversible — decide by V1, record in `## Decisions`; (3) survivors become one batch of at most four cards, once per package; (4) only irreversible or unauthorized work parks in `## Parked`. Re-asking what evidence already answers is a violation. Stop releases only as `delivered`, `parked-only`, or `stalled`.

## Innovation Stance

Every lifecycle prompt ends with a `Spark:` line: explore designs within boundaries and treat failure as evidence. It never overrides rules or charter.

## Hard Rules

1. Each machine task has `id`, path-glob `boundary`, backticked `verify`, and `expect`; only the harness ticks a ready task after its current-tree verify passes.
2. Before `plan-go`, edit only `.spec/` package files. `plan-go` records prior dirt; boundary-inside changes deliver, outside changes block.
3. Commits, archive, merge, and push are harness actions; an active/archived package rejects direct Git commit/push and hook bypasses (`--no-verify`, `-n`, hooks-path).
4. Exactly one `plan-go` freezes a package and binds one non-protected branch. On a protected branch it creates `spec/<slug>` in `.spec/worktrees/<slug>`, moves the package there, and continues without planning dirt; packages run concurrently. Never overwrite or stash user changes for a gate. For V4 `merge`/`push`, `done` preflights worktrees, remote, and residual dirt; ambiguity fails fast.
5. Boundary edits require a matching task or planned amendment. Audit Bash changes from the final diff.
6. Irreversible forks require `by: user <reference>`. New dependencies and compatibility breaks require charter authority.
7. `verify` runs only ready tasks. `check` runs the delivery gate (task state, criteria, evidence, structure audit, UI review) and a pass runs `done`: final-tree re-verify, archive, V4 delivery. UI packages require real flows, screenshots, criterion coverage, and closed findings via `spec.py ui-review --file <draft>`.

## Commands

| User command | Purpose |
| --- | --- |
| `/spec <goal>` | Plan when no package is bound; otherwise continue execution |
| `/spec:plan <goal>` | Create or refine the package: lint, forks, `plan-go` |
| `/spec:run [goal]` | Execute the bound package to a terminal state; without a package a goal runs the one-shot |
| `/spec:repair <slug>` | Repair a delivered archive: thin planning, full gate |
| `/spec:check` | Delivery gate, structure and UI/UX review; a pass runs `done` |
| `/spec:push` | Authorized finish: merge and push the archived package on this branch |
| `/spec:doctor` | Check or repair runtime and session hooks |
| `/spec /purge` | `spec.py purge`: keep records local; no commit/push |

Planning: [plan](references/plan.md). Execution: [exec](references/exec.md). Format: [format](references/format.md). Philosophy: [philosophy](references/philosophy.md). Git: [git](references/git.md).

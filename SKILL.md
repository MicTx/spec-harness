---
name: spec
description: >-
  Spec Harness turns substantial work into a recoverable, verifiable task package. Use for spec
  commands and for work spanning acceptance surfaces, implementers, or sessions. Skip small
  reversible fixes, read-only investigation, and experiments.
---

# Spec Workflow

A v3 package is three files under `.spec/specs/YYYY-MM-DD_slug/`: `spec.md`, `tasks.md`, plus harness-written evidence. The package is the source of truth.

## Two Phases

Planning is thick: `/spec` routes an unbound goal to `plan`, a bound package to `update` mode. Before freeze: read-only Recon and Intent, plan every task backward from failure and verification, `spec.py plan-lint` to zero, resolve forks, `plan-go`. After Go, execution is thin: follow the ready task DAG, verify, checkpoint, decide within charter, and continue.

No session brief: run `python3 scripts/spec.py next`.

## Autonomy Contract

After Go, do not ask about reversible in-scope decisions. Apply the charter, record each decision in `## Decisions` with its reversal, and continue. Park irreversible or unauthorized work in `## Parked`, continue ready tasks, present parked items once at the end. Stop may release only as `delivered`, `parked-only`, or `stalled`.

## Innovation Stance

Every lifecycle prompt ends with an encouraging `Spark:` line — fuel for rival designs, creative paths inside boundaries, failure read as fresh evidence, delivery celebrated. Encouragement never overrides the rules above; divergence is welcome where the charter allows.

## Hard Rules

1. Every machine task has `id`, path-glob `boundary`, backticked `verify`, and `expect`; only the harness ticks a ready task after its current-tree verify passes.
2. Before `plan-go`, the agent may edit only `.spec/` package files. `plan-go` records pre-existing dirt; boundary-inside changes deliver, outside changes block.
3. Commits, archive, merge, and push are harness actions; while a package is active/archived on the branch, direct `git commit`/`push` and hook bypasses (`--no-verify`, `-n`, hooks-path) are rejected.
4. A package is frozen by exactly one `plan-go` and binds one non-protected branch. When Go runs on a protected branch, it creates `spec/<slug>` in a linked worktree at `.spec/worktrees/<slug>`, moves the package there, and execution continues from that worktree without planning dirt; several packages run concurrently. Never overwrite or stash user changes to make a gate pass. For V4 `merge`/`push`, `done` preflights bound worktree, target worktree, remote, and residual dirt before archiving; a dirty target or ambiguous binding fails fast.
5. Boundary edits require a matching task or a planned amendment. Bash-created changes are audited from the final diff.
6. Irreversible forks require `by: user <reference>`. New dependencies and compatibility breaks require charter authority.
7. `verify` runs only ready tasks. `check` runs the delivery gate (task state, criteria, evidence, structure audit, UI review) and a pass runs `done`: final-tree re-verify, archive, V4 delivery. UI packages require real flows, screenshots, criterion coverage, and closed findings via `spec.py ui-review --file <draft>`.

## Commands

| User command | Purpose |
| --- | --- |
| `/spec <goal>` | Plan when no package is bound; otherwise continue execution |
| `/spec:plan <goal>` | Create or refine the package: lint, forks, `plan-go` |
| `/spec:run [goal]` | Execute the bound package to a terminal state; a goal with no package runs the built-in one-shot |
| `/spec:check` | Delivery gate, structure and UI/UX review; a pass runs `done` |
| `/spec:push` | Authorized finish: merge and push the archived package bound to this branch |
| `/spec:doctor` | Check or repair installed runtime and session hooks |

Planning contract: [`references/plan.md`](references/plan.md). Execution contract: [`references/exec.md`](references/exec.md). Format: [`references/format.md`](references/format.md). Philosophy: [`references/philosophy.md`](references/philosophy.md). Git ownership: [`references/git.md`](references/git.md).

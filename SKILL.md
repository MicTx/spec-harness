---
name: spec
description: >-
  Spec Harness turns substantial work into a recoverable, verifiable task package. Use for spec
  commands and for work spanning acceptance surfaces, implementers, or sessions. Skip small
  reversible fixes, read-only investigation, and experiments.
---

# Spec Workflow

A v3 package is three files under `.spec/specs/YYYY-MM-DD_slug/`: `spec.md`, `tasks.md`, and evidence written by the harness. The package is the source of truth.

## Two Phases

Planning is deliberately thick: `/spec` routes an unbound goal to `plan` and a bound package to `update`. Before freeze: read-only Recon, Intent clarification, then Plan every task backward from failure and verification. Write the package, `spec.py plan-lint` to zero, resolve forks, `spec.py plan-go`. After Go, execution is deliberately thin: follow the ready task DAG, verify, checkpoint, decide within charter, and continue to a terminal state.

No session brief: run `python3 scripts/spec.py next`; root and slug resolve automatically.

## Autonomy Contract

After Go, do not ask about reversible in-scope decisions. Apply the charter priority, record every decision in `## Decisions` with its reversal, and continue. Park irreversible or unauthorized work in `## Parked`, continue other ready tasks, and present all parked items once at the end. Stop may release only as `delivered`, `parked-only`, or `stalled`.

## Hard Rules

1. Every machine task has `id`, path-glob `boundary`, backticked `verify`, and `expect`; only the harness ticks a ready task after its current-tree verify passes.
2. Before `plan-go`, the planning agent may create or update only package files under `.spec/`; it must not change product code. Pre-existing working-tree dirt is not an agent edit: `plan-go` records it, changes inside task boundaries are delivered, and changes outside every boundary block delivery.
3. Git commits, archive, merge, and push are harness-script actions; while a package is active or archived on the current branch, direct `git commit`/`git push` commands are rejected, and Git hooks may never be bypassed with `--no-verify`, `-n`, or a hooks-path override.
4. A package is frozen by exactly one `plan-go` and binds one non-protected branch. When Go creates `spec/<slug>` on a protected branch, it does so in a linked worktree at `.spec/worktrees/<slug>`, moves the package there, and execution continues from that worktree without carrying planning-worktree dirt — several packages can run concurrently. Never overwrite or stash user changes to make a gate pass. For V4 `merge`/`push`, `done` preflights the bound worktree, target worktree, remote, and residual dirt before archiving; a dirty target worktree or ambiguous binding fails fast.
5. Boundary edits require a matching task or a planned amendment. Bash-created changes are audited from the final diff.
6. Irreversible forks require `by: user <reference>`. New dependencies and compatibility breaks require charter authority.
7. `verify` runs only ready tasks. `check` runs the delivery gate (task state, criteria, evidence, structure audit) and a pass runs `done`: final-tree re-verify, archive, V4 delivery.

## Commands

| User command | Purpose |
| --- | --- |
| `/spec <goal>` | Plan when no package is bound; otherwise continue execution |
| `/spec:plan <goal>` | Create or refine the package: lint, forks, `plan-go` |
| `/spec:run [goal]` | Execute the bound package to a terminal state; a goal with no package runs the built-in one-shot |
| `/spec:check` | Delivery gate and structure audit; a pass runs `done` automatically |
| `/spec:push` | Authorized finish: merge and push the archived package bound to this branch |
| `/spec:doctor` | Check or repair installed runtime and session hooks |

Planning contract: [`references/plan.md`](references/plan.md). Execution contract: [`references/exec.md`](references/exec.md). Format: [`references/format.md`](references/format.md). Philosophy: [`references/philosophy.md`](references/philosophy.md). Git ownership: [`references/git.md`](references/git.md).

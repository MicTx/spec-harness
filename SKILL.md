---
name: spec
description: >-
  Spec Harness turns substantial work into a recoverable, verifiable task package. Use for spec
  commands and for work spanning independent acceptance surfaces, multiple implementers, or sessions.
  Skip packages for small reversible fixes, read-only investigation, and experiments.
---

# Spec Workflow

A v3 package is three files under `.spec/specs/YYYY-MM-DD_slug/`: `spec.md`, `tasks.md`, and evidence written by the harness. The package is the source of truth.

## Two Phases

Planning is deliberately thick: keep the three user commands but let `/spec` route an unbound goal to `new` and a bound package to `update`. Do the Codex-style planning sequence before any freeze: read-only Recon, Intent clarification, then Plan every task backward from failure and verification. Write the package, run `spec.py plan-lint` to zero, resolve forks, then run `spec.py plan-go`. After Go, execution is deliberately thin: follow the ready task DAG, verify, checkpoint, decide within charter, and continue until a terminal state.

If no session brief appears, first run `python3 scripts/spec.py next`; root and slug are resolved automatically.

## Autonomy Contract

After Go, do not ask about reversible in-scope decisions. Apply the charter priority, record every decision in `## Decisions` with its reversal, and continue. Park irreversible or unauthorized work in `## Parked`, continue other ready tasks, and present all parked items once at the end. Stop may release only as `delivered`, `parked-only`, or `stalled`.

## Hard Rules

1. Every machine task has `id`, path-glob `boundary`, backticked `verify`, and `expect`; only the harness ticks a ready task after its current-tree verify passes.
2. Before `plan-go`, the planning agent may create or update only package files under `.spec/`; it must not change product code. Pre-existing working-tree dirt is not an agent edit: `plan-go` records it, changes inside task boundaries are delivered, and changes outside every boundary block delivery.
3. Git commits, archive, merge, and push are harness-script actions; while a package is active or archived on the current branch, direct `git commit`/`git push` commands are rejected, and Git hooks may never be bypassed with `--no-verify`, `-n`, or a hooks-path override.
4. A package is frozen by exactly one `plan-go` and binds one non-protected branch. Never overwrite or stash user changes to make a gate pass. For V4 `merge`/`push`, `done` preflights the bound worktree, target worktree, remote, and residual dirt before archiving; a dirty target worktree or ambiguous binding fails fast.
5. Boundary edits require a matching task or a planned amendment. Bash-created changes are audited from the final diff.
6. Irreversible forks require `by: user <reference>`. New dependencies and compatibility breaks require charter authority.
7. `verify` runs only ready tasks. `done` requires every task checked and unblocked, then re-verifies the complete DAG on the final tree before archiving.

## Commands

| User command | Purpose |
| --- | --- |
| `/spec <goal>` | Plan when no active package is bound; otherwise continue execution |
| `/spec:push` | User-authorized finish; resolves the archived package bound to the current branch, then merges and pushes |
| `/spec:doctor` | Check or repair installed runtime and session hooks |

Planning contract: [`references/plan.md`](references/plan.md). Execution contract: [`references/exec.md`](references/exec.md). Format: [`references/format.md`](references/format.md). Philosophy: [`references/philosophy.md`](references/philosophy.md). Git ownership: [`references/git.md`](references/git.md).

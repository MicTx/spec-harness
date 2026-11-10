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

Planning is deliberately thick: inspect, expose intent and scope, fill the charter, run `spec.py plan-lint` to zero, resolve forks, then run `spec.py plan-go`. After Go, execution is deliberately thin: follow the ready task DAG, verify, checkpoint, decide within charter, and continue until a terminal state.

If no session brief appears, first run `python3 scripts/spec.py next`; root and slug are resolved automatically.

## Autonomy Contract

After Go, do not ask about reversible in-scope decisions. Apply the charter priority, record every decision in `## Decisions` with its reversal, and continue. Park irreversible or unauthorized work in `## Parked`, continue other ready tasks, and present all parked items once at the end. Stop may release only as `delivered`, `parked-only`, or `stalled`.

## Hard Rules

1. Every machine task has `id`, path-glob `boundary`, backticked `verify`, and `expect`; tick only after the current-tree verify passes.
2. Git commits, archive, merge, and push are harness-script actions; never run Git with `--no-verify`.
3. One package binds one non-protected branch. Never overwrite or stash user changes to make a gate pass.
4. Boundary edits require a matching task or a planned amendment. Bash-created changes are audited from the final diff.
5. Irreversible forks require `by: user <reference>`. New dependencies and compatibility breaks require charter authority.

## Commands

| User command | Purpose |
| --- | --- |
| `/spec <goal>` | Plan when no active package is bound; otherwise continue execution |
| `/spec:push` | User-authorized finish after a package has been delivered |
| `/spec:doctor` | Check or repair installed runtime and session hooks |

Planning contract: [`references/plan.md`](references/plan.md). Execution contract: [`references/exec.md`](references/exec.md). Format: [`references/format.md`](references/format.md). Philosophy: [`references/philosophy.md`](references/philosophy.md). Git ownership: [`references/git.md`](references/git.md).

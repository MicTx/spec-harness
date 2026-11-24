# Execution Phase

After `plan-go`, `/spec:run` works the ready DAG inside each boundary, runs the declared verify, checkpoints, then enters `/spec:check`. With a goal and no package, run plans, freezes and delivers in one chain. `verify --all` stops at the first failure and never re-runs checked cards. Repair packages use the same loop. Use `plan-amend` for authorized additions or revisions; `spec.py list` and `spec.py diff` are read-only inventory and change projections. Project verify timeout: 1800s (`SPEC_PROJECT_VERIFY_TIMEOUT` overrides it).

## Delivery Recovery

Start with `spec.py check`, not a probe. `check` and `done` share one verdict. `findings` classify each issue by `code`, `severity`, `owner`, `subject` and `action`. Warnings do not block. Binding, frozen commands, actual failed acceptance, bad evidence and unsafe staged/committed changes still block. Never stash, reset or absorb unknown changes. Unstaged outside changes can remain for a local commit; merge/push must still pass preflight. Recorded lessons are included automatically; enforce targets are still checked.

1. Follow `next` in the JSON: finish independent ready cards first, then address the selected blocker. A changed tree or missing/stale evidence triggers the declared final verify when safe. Fix a failed verify, then use **the same check** to finish. No implicit verify expansion.
2. Repair within the charter: inspect the ledger, run the real flow or isolate a change. Use `spec.py check --attempt '<strategy and evidence>'` after a distinct recovery attempt. Use `--retry '<diagnosed environment change>'` for a same-tree verification retry. Do not blindly repeat. Ledger counts survive sessions: three attempts per blocker, six overall. At exhaustion report findings, tried strategies and what remains; `--restart 'user <reference>'` opens a new budget only with explicit authority.
3. Continue in this turn until delivered, parked, or stalled. Claude-style Stop and current Pi can request a bounded continuation; when the host has no continuation, loop here yourself. `spec.py next` is read-only. `check --check-only` records a probe, never verifies or delivers and never starts a loop. Respect pause/cancel and host stop reentry.

A pass completes final-tree verification, archives and commits, then applies V4. A handoff interrupted after archive remains pending; `spec.py push` retries it. Do not mistake a local archive for a completed merge or push.

## UI/UX Review

Actual owned frontend changes, matching additive `patterns`, or `## UI Review` / `- criteria: C1, C2` trigger real UI evidence. Directory/scope hints alone are warnings. Cover the applicable Criteria, not unrelated backend criteria. Operate the interface through real clicks, inputs and navigation (project browser E2E can provide evidence). Capture screenshots in `evidence/`, then submit the flows and findings via `spec.py ui-review --file <draft>`. A failed flow requires a verified fix or explicitly accepted non-criterion finding; a required criterion still needs a passing flow. A purely advisory finding cannot close a failed flow. Rerun and recapture when the product tree changes; do not reuse screenshots as new results.

## Decisions

Apply chosen forks with `D` and cite `F<n>`. Decide reversible in-scope changes by V1 and record reversal. Park unauthorized work, then continue independent cards. Question budget: the only user-bound channel is `## Parked` and its projected decision cards. Ask once, up to four cards, only after checking evidence and exhausting safe options. Every ask that does reach the user carries its ledger: attempts, recommendation, alternatives and consequences; an ask without that record is a violation. Never assign the user a repair.

Use `spec.py decide --resolve P1 --choice resolve --by 'user <reference>' '<decision>'` to close a parked item without deleting history. `wait` leaves it pending without asking again. For blocked handoff `P0`, `wait` keeps V4; `local-commit` permits this package's local commit only. Neither choice waives proof or authorizes any additional remote action.

## Failure Ladder

Each rung is an invitation to diverge: start with the smallest fix, use a minimal reproduction for the repeat, then compare last green and try another approach. After a fourth matching task failure, `[!]` blocks the card; continue independent work. Recover with `spec.py unblock <task-id> --reason '<new evidence>'`. Report what shipped, evidence, decisions and the remaining blocker if any.

# Execution Phase

After `plan-go`, execution has no planning conversation. Take the first ready task from the dependency DAG, change only its boundary, run its exact verify, and let the harness tick and checkpoint it. Use `plan-amend` only when the charter allows adjacent work and the new task passes the same lint. The shared project-level verify budget is 1800 seconds; set `SPEC_PROJECT_VERIFY_TIMEOUT` for a slower CI host.

## Decisions

| Situation | Action |
| --- | --- |
| A fork condition occurs | Apply its chosen option and record a `D` row citing `F<n>`. |
| Unforeseen but reversible and in scope | Choose by V1, continue, and record reversal. |
| Adjacent work and V2 allows | Amend the plan with `origin: emergent`. |
| Irreversible or beyond V2/V5/V6 | Add a `P` row, block dependents, continue ready work. |
| Only parked items remain | Present them once; record the user answer as `D` with `user <reference>`. |

## Failure Ladder

1. First failure: read the output and make the smallest fix.
2. Same signature again: build a minimal reproduction or add diagnostics.
3. Same signature a third time: compare the last green checkpoint and change implementation path.
4. Still no new evidence: park the task with its signature and continue elsewhere.

## Final Report

Report delivery, concrete evidence, every decision made for the user and its reversal, then parked items or the evidence-backed stalled state. The only Stop release states are `delivered`, `parked-only`, and `stalled`.

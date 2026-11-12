# Execution Phase

After `plan-go`, execution has no planning conversation. `/spec:run` drives this loop: take the first ready task from the dependency DAG, change only its boundary, run its exact verify, and let the harness tick and checkpoint it. With a goal argument and no bound package, `/spec:run` is the built-in one-shot — plan, `plan-go`, execute, `check`, deliver — so a separate `goal` entry does not exist; with a bound package the goal argument is ignored. `verify <id>` rejects blocked, checked, or dependency-blocked tasks; `verify --all` runs the unfinished unblocked DAG in stable topological order and stops at the first failure. Use `plan-amend` only when the charter allows adjacent work and the new task passes the same lint. The shared project-level verify budget is 1800 seconds; set `SPEC_PROJECT_VERIFY_TIMEOUT` for a slower CI host.

`check` runs the delivery gate: every task checked and no `[!]` task, criteria proven, evidence fresh, and the structure audit over changed paths — the review the retired `organize` stage owned. When the gate passes, `spec.py check` executes `done` automatically: it re-runs all machine verifies in dependency order on the final tree without changing task markers, then archives, commits, and performs the declared V4 delivery. `spec.py check --check-only` probes the gate without delivering; `spec.py done` remains the internal primitive for retry after an interrupted delivery. A package that has not passed `plan-go`, or whose current branch differs from `Binding`, cannot execute tasks.

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
4. Still no new evidence: the harness marks the task `[!]`, records the blocking evidence, and continues any independent ready work.

After new root-cause evidence exists, run `spec.py unblock <task-id> --reason "<evidence>"` before retrying. The audit record is stored in `runs.jsonl`; `done` remains blocked until that task verifies and is checked.

## Final Report

Report delivery, concrete evidence, every decision made for the user and its reversal, then parked items or the evidence-backed stalled state. The only Stop release states are `delivered`, `parked-only`, and `stalled`.

# Execution Phase

A repair package (`kind: repair`) runs the standard DAG and full gate; only planning is thinner; its `repair-of` names the origin spec.
`spec.py diff` summarizes revisions vs the plan-go baseline: changed files, spec/tasks deltas and fingerprints, task-id adds/removes; excludes `.spec/`; frozen only.

After `plan-go`, execution has no planning conversation. `/spec:run` drives this loop: take the first ready task from the dependency DAG, change only its boundary, run its exact verify, and let the harness tick and checkpoint it. Inside a task boundary a creative implementation beats a mechanical one — the verify command is the judge. With a goal argument and no bound package, `/spec:run` is the built-in one-shot — plan, `plan-go`, execute, `check`, deliver — so a separate `goal` entry does not exist; with a bound package the goal argument is ignored. `verify <id>` rejects blocked, checked, or dependency-blocked tasks; `verify --all` runs the unfinished unblocked DAG in stable topological order and stops at the first failure. Use `plan-amend` only when the charter allows adjacent work and the new task passes the same lint. The shared project-level verify budget is 1800 seconds; set `SPEC_PROJECT_VERIFY_TIMEOUT` for a slower CI host.

`check` runs the delivery gate: every task checked and no `[!]` task, criteria proven, evidence fresh, the structure audit over changed paths, and any required UI review — the review the retired `organize` stage owned. Every check writes a `gate` record into the package's `runs.jsonl`; a failed gate becomes the `check-failed` route state and the Stop hook blocks with the first concrete repair action instead of repeating "run check" forever. When the gate passes, `spec.py check` executes `done` automatically: it re-runs all machine verifies in dependency order on the final tree without changing task markers, then archives, commits, and performs the declared V4 delivery. The result returns a completion card (`delivered`, or `push` when a V4 push failed) so the next lifecycle link is explicit. `spec.py check --check-only` probes the gate without delivering; `spec.py done` remains the internal primitive for retry after an interrupted delivery. A package that has not passed `plan-go`, or whose current branch differs from `Binding`, cannot execute tasks.

`spec.py list` prints a JSON inventory of active and archived packages (branch, frozen, progress) for tooling.

`spec.py next` and session `start`/`subagent` hooks also resolve an archived package on its delivery branch: a pending V4 push or a completed delivery is projected as the next lifecycle state, and a primary-worktree session is pointed at the single active linked worktree instead of silently losing the package.

## UI/UX Review

When `Scope.in`, task boundaries, extra `patterns`, or actual changed paths hit a UI surface, the host must operate the running interface through real clicks, inputs, navigation, and screenshots. Put PNG/JPEG screenshots under `.spec/specs/<slug>/evidence/` and write their relative paths, flow steps, expectations, observations, criteria ids, findings, and solutions into `.spec/specs/<slug>/ui-review.json` via `spec.py ui-review --file <draft>`. The command fills `schema_version`, `generated_at`, screenshot `sha256`/`format`, and the product-tree fingerprint. Every criterion needs at least one passing real-operation flow, and every flow step needs a screenshot. A failed step produces a finding; every finding must be `fixed` with a checked, verified task and a concrete solution, or `accepted` with a `by: user <reference>` decision and an explicit reason. Add fixes through `plan-amend`, record acceptance through `decide`, rerun affected verifies, then repeat the real operation and refresh the record. Any source-tree change invalidates the review until it is rerun. Non-UI packages and historical archives without a UI record are unaffected.

## Decisions

| Situation | Action |
| --- | --- |
| A fork condition occurs | Apply its chosen option and record a `D` row citing `F<n>`. |
| Unforeseen but reversible and in scope | Choose by V1, continue, and record reversal. |
| Adjacent work and V2 allows | Amend the plan with `origin: emergent`. |
| Irreversible or beyond V2/V5/V6 | Add a `P` row, block dependents, continue ready work. |
| Only parked items remain | Present them once; record the user answer as `D` with `user <reference>`. |

## Failure Ladder

Each rung is an invitation to diverge, not a scolding: the ladder exists to spend creativity on new evidence instead of repetition.

1. First failure: fresh evidence in hand — read the output and make the smallest fix that could work.
2. Same signature again: time to diverge — build a minimal reproduction or add diagnostics somewhere new.
3. Same signature a third time: be bold — compare the last green checkpoint and change implementation path.
4. Still no new evidence: the harness marks the task `[!]`, records the blocking evidence, and points the creative energy at any independent ready work.

After new root-cause evidence exists, run `spec.py unblock <task-id> --reason "<evidence>"` before retrying. The audit record is stored in `runs.jsonl`; `done` remains blocked until that task verifies and is checked.

## Final Report

Report delivery, concrete evidence, every decision made for the user and its reversal, then parked items or the evidence-backed stalled state. Close by celebrating what shipped and promoting the lessons worth keeping. The only Stop release states are `delivered`, `parked-only`, and `stalled`.

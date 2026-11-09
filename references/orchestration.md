# Orchestration Contract

The main session is always the orchestrator: it owns routing, acceptance and the `done`/`push` gates. Delegate only when a lane's goal, scope and verification can be written down; otherwise stay `local`. Core installs omit `slots/` and agent role documents — when one is needed and missing, say so and continue on the ordinary path.

## Route vocabulary

Exactly one token:

| Route | Use when | Topology |
| --- | --- | --- |
| `local` | Small, coupled, or the next blocker is faster on the main thread | Main session only |
| `explore` | Understanding is missing | 2–3 targeted explorers |
| `build` | Implementation with clean ownership slices | Disjoint workers; shared glue stays on the main thread |
| `review` | Code exists and risk dominates | Review lanes in parallel |
| `external` | Isolation or long-running execution is actually required | Upgrade only; no backend ships |

`python3 scripts/route_decision.py --root <project> --slug <slug>` derives the route from package structure only (open tasks, boundary modules, dependency width, risk labels). Zero structural signal routes `local`; risk labels route `review`. `--route <token>` or a `### 5.4 Orchestration Strategy` route overrides it within the vocabulary.

## Operating model

The main session orchestrates; in-process helpers explore, review and take bounded implementation slices; the main session merges and verifies. Batch fan-out and review-fix loops are triggered by `workflow-runner` (`workflow_route.py`; `loop_route.py` defers those shapes), while `team-loop` keeps `batch-fanout` as an explicit initialization mode (`loop_state.py init --mode batch-fanout`) and is the degradation face when `workflow-runner` is unavailable.

## Assignment contract

Before starting a lane, write:

- Goal: the question or slice it owns
- Scope: files/modules it may change
- Excluded areas: what it must not touch; real secret values never enter prompts, arguments or evidence
- Output: findings or a patch summary
- Verification: the command it runs, or what stays unverified

Lanes never overlap write scopes. Wait for a lane only when the next decision depends on it. A lane's own report never completes a task: the main session runs the task's `verify`.

## Task-shape decision table

This table maps task shapes to execution surfaces using only the existing five route tokens and slot mode names. Script output is advisory; the main session makes the final decision and chooses `local` when uncertain.

| Task shape | Execution surface (token) | Decision script | Prerequisites | Fallback |
| --- | --- | --- | --- | --- |
| Local | `local` | `scripts/route_decision.py --root --slug` | Small and coupled, with no benefit from cross-module parallelism; zero structural signal always selects `local` (no text-length fallback) | Correct a misclassification explicitly with `--route` |
| Exploration | `explore` + 2-3 targeted explorer sidecars | `scripts/route_decision.py --root --slug` | Multiple tasks with broad boundaries or tangled dependencies, based on explainable package-structure metrics; all five assignment-contract fields can be stated clearly | If the contract cannot be stated clearly, explore serially on the main thread |
| Parallel fan-out | `explore` / `build` / `review` × workflow-runner (`batch-fanout` / `parallel-review` / `perspective-panel` / `review-fix-loop`) | `slots/workflow-runner/scripts/workflow_route.py` (strong signals only), and `route_decision.py --root --slug` selects `explore` / `build` / `review` | Route is `explore` / `build` / `review` (the README's documented activation domain); repository driver is readable and the selected worker CLI is on PATH | Fall back to ordinary sidecars or team-loop if prerequisites fail (workflow-runner README, Hard preconditions); never silently fabricate results |
| Convergence loop | Any route × team-loop (`until-converged` / `fixed-rounds`); workflow-runner handles combined review-fix shapes (`loop_route` explicitly yields) | `slots/team-loop/scripts/loop_route.py` (strong signals only: convergence-bound semantics or an explicit round count) | `loopRecommended=true` hands off execution; weak signals alone do not recommend a loop and deterministically fall back to ordinary sidecar orchestration; the agents-team tool surface is loaded | If the tool surface is unavailable, report an explicit error; never silently fall back to a single-session loop |
| External isolation | `external` | `scripts/route_decision.py --root --slug` (structural routing never selects `external` automatically) | Actual need for isolated Git state, long-running work, or multiple terminals; selected by an explicit 5.4 route / `--route` override; no external backend is available this round | Without an external backend, fall back to in-process work or serial handoff; never invent a control surface mid-run |

`loopRecommended=true` and `route=local` may coexist: slot activation and orchestration routing are independent (SKILL.md, "Activation rule"). A match hands execution to the managed loop protocol described in `stages/run.md` and the slot's README. Likewise, `workflowRecommended=true` is only a recommendation; actual activation is constrained by the workflow-runner README's documented activation domain and driver prerequisite checks. `tests/test_route_decision.py` guards the current combined behavior.

## `### 5.4 Orchestration Strategy`

Optional section under `## 5. Technical Decisions` in `spec.md`. When present, `check_spec_package.py` validates structure: `route` is a single token from the vocabulary above (backticks optional); multi-token values such as `build (main thread) + review` are rejected; ownership / waiting strategy / verification gate live as sibling bullets, never inside the `route` token; absence does not block the package (local by default). Recommended shape (single-token `route` only), written from `route_decision.py` output plus judgment — never invent extra tokens:

```markdown
### 5.4 Orchestration Strategy
- route: local
- immediate blocker: <what stays on the main thread>
- ownership: <bounded slices, or main thread only>
- waiting strategy: <when to wait, or none>
- verification gate: <what must pass before finalizing>
- workflow: <execution-surface notes, optional>
```

# `/spec:autoplan`

Goal: turn a goal into a reviewed plan that `/spec:autorun` can execute. No package is created here.

1. `python3 scripts/autoplan_gate.py discover --root <project> [--plan <docs>]`. No plan yet means start one; an existing cluster means reconcile done features and plan the rest.
2. Investigate code, data and existing decisions before asking. Ask the user only about permission, irreversible actions, real preferences or external dependencies, batched with a recommendation.
3. Seed `.spec/plans/<YYYY-MM-DD_slug>/master.md` with the goal and `- [ ]` feature checkboxes, then record each pass:
   `python3 scripts/chain_state.py begin --chain autoplan --root <project> --step framework-1 --plan <master>`.
4. One pass per step: `framework-N` expands the master ([`agents/planner.md`](../../agents/planner.md)); `detail-N` writes one `NN-<slug>.md` with business logic, data, flow, boundaries and acceptance; `review-N` checks it ([`agents/reviewer.md`](../../agents/reviewer.md)). Say whether review was independent or done by the main session.
5. `python3 scripts/autoplan_gate.py gate --root <project>` must pass (exit 3 = not ready). Fix what it names with another pass.
6. When ready, `scripts/chain_state.py close --chain autoplan`, report readiness and suggest `/spec:autorun`. Feature boxes stay unchecked until implemented.

Options: `--plan <docs>`, `--max-rounds <n>` or `--unbounded` on `begin` (default: no cap). Stop on user interruption, missing authority or an external blocker (`scripts/chain_state.py pause --reason ...`).

Output: [`../output-contracts.md`](../output-contracts.md).

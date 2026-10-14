# `/spec:autoplan`

Goal: interactive planning entry paired with [`autorun.md`](autorun.md) — interrogate the user in themed rounds, then run the planning passes (framework → per-phase detail → consistency review) as a serial fresh-session chain with `/spec:autorun`'s spawn discipline, until every feature reaches autorun granularity; gate the cluster and stop at readiness. `autoplan` creates no task package, writes only `.spec/autoplan/` pass-chain state, and never spawns (nor is invoked by) the autorun chain. No pass in autoplan runs as an in-process subagent of the spawning session.

Compatible invocation:

- Claude Code CLI: `/spec:autoplan [--plan <docs>] [--max-rounds <n>] [--max-passes <n>]`; other skill-aware CLIs: `spec:autoplan` via `$spec`. Pass windows get the per-host continuation prompt (shapes in [`../commands.md`](../commands.md)); assignments come from `.spec/autoplan/chain.json`.

Planning-document discovery (`scripts/autoplan_gate.py discover --root <project> [--plan <path>...]`): same candidate rules as [`autorun.md`](autorun.md) — `--plan` wins (paths under the project root), otherwise the canonical `.spec/plans/` scan, qualifying only with a markdown feature checkbox. An existing cluster → phase-boundary run (reconcile checked features with reality, re-plan the remaining scope); empty scan → project-start run.

Execution order:

1. Preflight with `discover` (project-start vs phase-boundary).
2. Interrogation rounds, at most `--max-rounds` (default 5), themed in order: goal/users/value → scope/MVP/boundaries → technology/data/architecture → priorities/phases/dependencies → risks/verification. Answers land in the cluster's confirmed-facts / assumptions / open-questions sections. Converge when no project-shaping question remains or the user ends early, then seed/update the master document (default `.spec/plans/README.md` or first `--plan` doc).
3. Framework planning pass — spawn the first pass window and end this session:

    ```bash
    python3 scripts/autoplan_spawn.py spawn --root <project> --next framework [--plan <docs>] [--max-passes <n>] [--host/--model/--reasoning as `autorun` spawn]
    ```

    It enforces the pass-chain rails (cap, single-chain lock, sequential spawn, seeded master) and opens a session via the shared [session backends](../commands.md#session-backends). The framework pass expands the seed into the master document under the planner mission (contract: [`agents/planner.md`](../../agents/planner.md); write scope = its one document). Existing PRD/design docs are reconciled, never duplicated.
4. Detail passes: one pass window per phase detail document (`--next detail --target <doc>`; default `<cluster>/NN-<slug>.md`), written under the planner mission until every leaf feature directly supports a `/spec:new` package; after the last phase, the review pass. The feature checkbox index must live inside the autorun candidate set.
5. Consistency review pass (`--next review`): run `scripts/autoplan_gate.py gate --root <project> [--plan ...]` (fail closed, exit 3) plus the independent reviewer pass under [`agents/reviewer.md`](../../agents/reviewer.md). Contradictions → record in open-questions, spawn a scoped fix pass (`--next detail --target <flagged doc>`), review again; repeat until both pass (bounded only by an explicit cap).
6. Readiness report: run `scripts/autorun_spawn.py plan`; report readiness, suggest `/spec:autorun`, stop. Never spawn the autorun chain from autoplan.

Pass-chain rules (same discipline as [`autorun.md`](autorun.md)):

- Fail fast, never spawn after failure: a failed pass (gate exit 3, contradiction, dead worker) stops the chain; recovery: re-run `/spec:autoplan`, follow `status`'s `resume:` decision.
- Strictly sequential: one pass window per project at most (flock in `.git/spec-chain/autoplan/`); parallel pass chains are refused, never queued.
- Pass cap: 20 without a flag; `--max-passes <n>` / `--unbounded` as [`autorun.md`](autorun.md).
- Permission mode, fingerprint, worker hosts, model identity: as [`autorun.md`](autorun.md) (no `--command` bypass here).
- Terminal scope, window recycling, user interruptions: as [`autorun.md`](autorun.md); a pass session may ask the user directly in its own window (every pass is a full interactive session).

Constraints:

- Interactive only: started by the user interactively; invoked non-interactively or from the autorun chain, stop and report undecided items. The question-list exemption covers the interrogation phase only (bounded by `--max-rounds`, default 5, and the convergence rule).
- No in-process subagent delegation anywhere in autoplan: every pass runs as its own full session and adjudicates its one assigned document; the spawning session ends at the handoff.
- The planning cluster is project content, never task-package state — never archived with a package or written under `.spec/autorun/`; `.spec/autoplan/` keeps the tracked summary (runtime in `.git/spec-chain/`), never archived.

Output contract:

- Follow the `/spec:autoplan` section in [`../output-contracts.md`](../output-contracts.md).

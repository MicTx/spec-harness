# `/spec:autoplan`

Goal: clarify, plan framework/details, review consistency, and stop at autorun readiness. No task package or autorun launch. Plans are project content; `.spec/autoplan/` holds pass receipts, not implementation completion.

Compatible invocation:

- Use the host's spec skill entry, then `spec:autoplan`; Claude Code also exposes `/spec:autoplan`. Options: `--plan <docs>`, interrogation `--max-rounds <n>` (default 5), pass `--max-passes <n>` or `--unbounded`, and `--execution auto|current|fresh`. Native command registration is host-specific, not inferred from this spelling.

Preflight: use [`../commands.md#session-backends`](../commands.md#session-backends) to resolve the actual client/surface and available tools. Desktop and IDE-plugin sessions use current-session passes by default; verified CLI adapters may use the serial fresh-session chain. Do not install or select another agent merely because its binary is on PATH. Native workers are optional bounded helpers, not replacement orchestrators.

Execution order:

1. Run `scripts/autoplan_gate.py discover --root <project> [--plan <docs>]`. Discovery matches [`autorun.md`](autorun.md): canonical `.spec/plans/*.md` and `*/master.md`, excluding archives; `--plan` is explicit scope. Empty discovery means project start, not planning complete. Existing cluster means reconcile completed features and clarify the remaining scope.
2. Ask themed rounds: goal/users/value → scope/MVP → technology/data → priorities/dependencies → risks/verification. Record confirmed facts, assumptions and open questions. Stop questioning when no project-shaping question remains or the user ends early. Seed a dated cluster's `<cluster>/master.md` (or selected `--plan` master); keep the root README as an index without feature checkboxes.
3. In **current** mode, inspect `scripts/chain_session.py status --chain autoplan` and use the shared owner/revision protocol. Start framework:

    ```bash
    python3 scripts/chain_session.py begin --chain autoplan --root <project> \
      --client <client> --surface <surface> --chain-id <id> --owner <owner> --step-id framework-1 \
      --expected-revision 0 --kind framework --plan <master> --format json
    ```

    New chains use a fresh chain-id and revision 0; retries retain both. Continue with the receipt's identity/revision. Expand the selected master under [`agents/planner.md`](../../agents/planner.md), reconciling existing PRD/design documents. `finish` records the completed framework's fingerprint, not the unfinished seed.
4. Begin each detail with a new stable step-id, `--kind detail --target <linked-doc>`. One assigned document per pass; default `<cluster>/NN-<slug>.md`. Complete business logic, data model, flow, boundaries and acceptance hooks until each leaf supports `/spec:new`; `finish` verifies the five-section structure. Feature checkboxes stay only in candidate masters, not detail documents.
5. Begin review with `--kind review`. Run `scripts/autoplan_gate.py gate` (not ready: exit 3) and actually review semantic consistency under [`agents/reviewer.md`](../../agents/reviewer.md). With available native review tools, use independent review; otherwise explicitly report main-session review. Finish with `--review-mode independent|main --review-note <conclusion>`. These fields attest to the performed review; the script only proves structure and document identity.
6. Contradictions require a recorded review `finish --outcome revise`, a scoped repair pass, then review again. Do not pass a failed gate. Detail/review cannot silently alter the frozen master features; a user-authorized scope revision uses a new framework pass.
7. A successful review records `ready` and the reviewed document digest. Run `scripts/autorun_spawn.py plan`, report readiness, suggest `/spec:autorun`, and stop. Unimplemented feature boxes remain unchecked. Changed documents invalidate readiness; current `close` verifies readiness, not implementation completion.

Fresh CLI branch:

- Use `scripts/autoplan_spawn.py spawn --root <project> --next framework|detail|review [--target <doc>] [--plan <docs>]` with the resolved worker and supported model/permission flags. Each pass is a full interactive session; the spawning session ends at handoff. No in-process subagent delegation transfers chain ownership.
- Existing fresh-chain fingerprint, model lock, start confirmation and terminal lifecycle remain in force; settle seed feature identities before the first spawn. Legacy states are not automatically converted to current receipts.

Both modes: no pass cap by default; explicit `--max-passes` and `--unbounded` are mutually exclusive. Failure stops advancement. On pause/context boundary, persist progress; resume the same pass without spending another pass. A transaction lock is not a session-lifetime lock. Start interactively, never from autorun; noninteractive entry reports undecided items. Do not archive the planning cluster with a task package or put it under `.spec/autorun/`.

Output: [`../output-contracts.md`](../output-contracts.md).

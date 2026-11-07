# `/spec:autoplan`

Goal: investigate, plan and review to autorun readiness; no package or autorun launch. `.spec/autoplan/` holds pass receipts, not implementation completion.

Compatible invocation:

- Use the host's spec skill entry, then `spec:autoplan`; Claude Code also exposes `/spec:autoplan`. Options: `--plan <docs>`, interrogation `--max-rounds <n>` (default 5), pass `--max-passes <n>` or `--unbounded`, and `--execution auto|current|fresh`. Native command registration is host-specific, not inferred from this spelling.

Preflight: resolve client/surface and tools via [`../commands.md#session-backends`](../commands.md#session-backends). Desktop and IDE-plugin sessions use current-session; verified CLI adapters may use fresh. Never select/install agents from PATH alone. Native workers are helpers, not orchestrators.

Execution order:

1. Run `scripts/autoplan_gate.py discover --root <project> [--plan <docs>]`. Discovery matches [`autorun.md`](autorun.md): canonical `.spec/plans/*.md` and `*/master.md`, excluding archives; `--plan` is explicit scope. Empty discovery means project start, not planning complete. Existing cluster means reconcile completed features and clarify the remaining scope.
2. Apply mandatory ultrathink. Use goal/users/value, scope/MVP, technology/data, dependencies and risks as an investigation checklist, not a questionnaire. Read code/data and existing decisions; test decisive unknowns with bounded, authorized experiments. Record sources, falsifiers, options and rollback in the existing plan. Ask only remaining permission/irreversibility/preference/external-dependency questions, batched with a recommendation; never invent an answer or grant. No such question → proceed without confirmation. Seed `<cluster>/master.md` (or selected `--plan` master); keep the README an index without feature checkboxes. Package questions later use [`../templates.md`](../templates.md#autonomous-inquiry).
3. In **current** mode, inspect `scripts/chain_session.py status --chain autoplan` and use the shared owner/revision protocol. Start framework:

    ```bash
    python3 scripts/chain_session.py begin --chain autoplan --root <project> \
      --client <client> --surface <surface> --chain-id <id> --owner <owner> --step-id framework-1 \
      --expected-revision 0 --kind framework --plan <master> --format json
    ```

    New chains start with fresh identity/revision 0; retries retain both. Carry the receipt forward. Expand the master under [`agents/planner.md`](../../agents/planner.md), reconciling PRD/design. `finish` fingerprints the completed framework, not its seed.
4. Begin each detail with a stable new step-id, `--kind detail --target <linked-doc>`. One document per pass, default `<cluster>/NN-<slug>.md`: business logic, data, flow, boundaries and acceptance sufficient for `/spec:new`. `finish` checks five-section structure. Feature checkboxes belong only in masters.
5. Begin `--kind review`; run `scripts/autoplan_gate.py gate` (not ready: exit 3) and review semantics under [`agents/reviewer.md`](../../agents/reviewer.md). Use independent review when available, otherwise label main-session review. Finish with `--review-mode independent|main --review-note <conclusion>` attesting actual review; scripts prove only structure/identity.
6. Contradictions require a recorded review `finish --outcome revise`, a scoped repair pass, then review again. Do not pass a failed gate. Detail/review cannot silently alter the frozen master features; a user-authorized scope revision uses a new framework pass.
7. A successful review records `ready` and the reviewed document digest. Run `scripts/autorun_spawn.py plan`, report readiness, suggest `/spec:autorun`, and stop. Unimplemented feature boxes remain unchecked. Changed documents invalidate readiness; current `close` verifies readiness, not implementation completion.

Fresh CLI branch:

- Use `scripts/autoplan_spawn.py spawn --root <project> --next framework|detail|review [--target <doc>] [--plan <docs>]` with the resolved worker and supported model/permission flags. Each pass is a full interactive session; the spawning session ends at handoff. No in-process subagent delegation transfers chain ownership.
- Existing fresh-chain fingerprint, model lock, start confirmation and terminal lifecycle remain in force; settle seed feature identities before the first spawn. Legacy states are not automatically converted to current receipts.

Both modes: no default pass cap; `--max-passes` and `--unbounded` are mutually exclusive. Failure stops advancement, not authorized diagnosis/repair; retry review with new evidence. Pause at user interruption, missing authority, external blocker or repeated no-new-evidence failure per the existing correction contract. Persist and resume the same pass without re-spending it. Locks protect transactions, not session lifetime. Never start from autorun; noninteractive entry stops only for genuine unresolved decisions. Do not archive plans with a package or under `.spec/autorun/`.

Output: [`../output-contracts.md`](../output-contracts.md).

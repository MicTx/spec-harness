# `/spec:autorun`

Goal: finish each planned package in the current host or a verified fresh CLI session until the scope is implemented. Task packages remain the work truth: no second task state machine and no skipped gate.

Compatible invocation:

- Load spec, then `spec:autorun`; Claude Code also exposes `/spec:autorun`. Options: `--plan <docs>`, `--execution auto|current|fresh`, `--max-rounds <n> | --unbounded`. Model/permission/terminal flags are fresh-only.

### Session backends

Resolve client/surface and check actual tools per the shared [session backends](../commands.md#session-backends) contract. Desktop, IDE-plugin and unknown surfaces default current despite CLI markers/PATH; subagents are optional.

Planning-document discovery (`scripts/autorun_spawn.py plan --root <project> [--plan <path[,path...]>]`): `--plan` wins (paths under the project root); otherwise scan `.spec/plans/` — top-level `*.md` plus each cluster's `*/master.md`, qualifying only with at least one markdown feature checkbox (`- [ ]` / `- [x]`); `archive/` and legacy locations are never scanned. No qualifying document → fail closed, exit 3: report candidates, ask the user to plan; never invent a plan. The chain ends when every qualifying-document checkbox is `- [x]`.
Execution order (each round):

1. Read chain status and route with `scripts/route_spec_package.py --root <project>`. No package + unchecked features → create the next package per [`new.md`](new.md); multiple packages require disambiguation. A missing plan is a gap (exit 3), not completion. A completed recorded scope with no active package stops.
2. In **current** mode, begin the unique package using the owner/revision protocol:

   ```bash
   python3 scripts/chain_session.py begin --chain autorun --root <project> \
     --client <client> --surface <surface> --chain-id <id> --owner <owner> --step-id <slug> \
     --expected-revision <revision> --slug <slug> --plan <docs> --format json
   ```

   New chains use a fresh chain-id and revision 0. Re-entry carries the recorded identity/revision, reads [handoff](../handoff.md) and resumes unfinished work; resume a paused step before execution.
3. Execute [`run.md`](run.md), review per [`check.md`](check.md), fix failures, then archive per [`done.md`](done.md). Every package includes a final plan-sync task covering its planning documents and `.spec/autorun/` summary; checked features must have real implementation evidence.
4. Current mode: after archive and before commit, call `scripts/chain_session.py finish` with the receipt revision/owner. It validates archive/summary and verification evidence, not publication. Include the receipt in the scoped commit.
5. Finish Git closeout per [`done.md`](done.md)/[`push.md`](push.md); push only when explicitly authorized, otherwise stop with recoverable progress. Push exit 0 may mean local-only fallback: verify remote publication. A receipt never grants Git authorization.
6. Re-run `plan` on the recorded scope. Complete → stop; otherwise after authorized closeout create the next package and begin its current-session step. A round ending never requires opening a Terminal or ending this session.
7. **Fresh** instead uses `scripts/autorun_spawn.py spawn --root <project> --host <resolved-worker> ...` after creating the next package. Confirm handoff, report and end this session. Recover with `scripts/autorun_spawn.py status`; never auto-convert an active legacy chain.

Chain rules:

- Fail fast on verification, check, Git hook, commit or push failure. Persist pause and package handoff at controlled stops; never skip blockers. After unexpected interruption inspect durable state, not an assumed rollback.
- Round cap: a restored default bounds every chain — 5 admitted rounds; explicit `--max-rounds` overrides and `--unbounded` removes it (mutually exclusive). A repeated current step or recovery spends no new round, including the last admitted round. Fresh spawn retains its recorded counter semantics and prompt forwarding.
- Single orchestrator, sequential steps; the lock protects transactions, not agent lifetime. Current owner/revision checks coordinate handoff; confirm the old owner stopped before takeover. No fabricated PID/heartbeat.
- Scope freezes on the first execution round. Added/removed/reworded feature text or missing plan documents refuses advancement; checking off verified work is legitimate progress.
- Current models/permissions are host-managed: no CLI injection claim, no implicit elevation. Fresh keeps `--permission-mode inherit|ask|bypass` (undetectable parent → ask), explicit `--model/--reasoning` identity lock and legacy worker resolution. Pass the already resolved worker instead of falling back from a desktop to PATH.
- Terminal automation is limited to the two explicit spawn operations and the dead-window sweep. Current mode never invokes those paths. User pause/abort, pending messages and trust prompts outrank both loops.

Output: [`../output-contracts.md`](../output-contracts.md).

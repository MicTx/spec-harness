# Loop continuity notes

This page records the engineering ideas selected from `dsh-purge` 1.1.17
(MIT) and reimplemented natively in Spec Harness. No archive code, runtime
dependency, host patch or installation hook was copied into this project.

Reviewed archive SHA-256:
`293f8f0c093f53558b6cd3de1033ebba76d99152a29574fb1e56e37a37ef742e`.
The review inspected source and call sites; it did not execute the plugin or
assume that a module's presence proved its activation.

## Selected ideas

- **Bounded recovery clues.** The archive's session tracker keeps per-session
  retry/continue budgets, deduplicates replays by a stable event identity, and
  refunds a budget when a continuation is not sent. Spec adopts the bounded,
  advisory shape: `state.json` may carry a short `pending` list, an `end`
  event without a `pending` key preserves it, and a `start` event for a
  compacted session may project matching-task clues. The live route, task DAG
  and runs ledger stay authoritative; snapshots do not restore task or gate state.
- **Gate-aware prompt deduplication.** The existing `progress_token` includes
  the latest gate signature. The per-session dedup key now hashes that token
  with the current task, lifecycle state, recovery clues and latest unblock.
  A newly failing gate or changed task refreshes the guidance, while a replay
  that changes only the gate timestamp stays quiet.
- **Fresh-section projection.** The archive's identity assembly folds prior
  sections instead of re-pinning their full text. Spec's session briefs apply
  the same principle: the state line, the actionable next step, the first
  failure and the complete `Spark:` line are protected first; goal, parked and
  lesson text are capped per tier. Repeated injections are deterministic and
  do not grow recursively.
- **Executable recovery routing.** Stale-only delivery-gate errors route to
  `spec.py check`, whose final re-verify refreshes evidence. Mixed gate errors
  surface the concrete non-stale repair first; `spec.py verify --all` is not
  offered as a no-op when every task is already checked.
- **Single projection per event.** `route.next_card(..., projection=...)`
  and `route.brief(..., projection=...)` reuse a computed projection; the
  earlier `decision=` argument remains compatible. Start/subagent cost one
  route computation, and a continuing stop costs none.
- **Fresh evidence and cold recovery.** The latest failed run stores its
  failure-ladder guidance as an additive ledger field; a cold next-action
  projection uses it without a host callback. When no independent task is
  ready, a blocked card names the task and the existing `spec.py unblock`
  action. A checked task that fails final re-verification instead retains its
  failure guidance and points to `spec.py done` after the underlying fix;
  merely rerunning `check` does not refresh a latest failing run. The tree
  fingerprint reads current file content instead of reusing
  a Git-status-only cache, so repeated in-process edits invalidate old passing
  evidence. Tracked `.spec/architecture` contracts are source inputs and stay
  in the hash; local evidence paths are excluded. A gate still shares one hash
  across its checks, and matching passing evidence remains reusable when the
  actual tree is unchanged.

## Source selection

| Reviewed source | Verified mechanism | Integration choice |
| --- | --- | --- |
| `lib/continue-retry.js:127–183` | Per-session counters, replay keys, refund after a failed send | Borrow bounded/idempotent state design; use the existing Spec lifecycle rather than a second scheduler |
| `lib/identity.js:191–197,249–279` and `scripts/test-identity-assembly.mjs:58–103` | Build fresh prompt sections instead of recursively re-pinning history; multi-round size checks | Native read-only projection with eight-round anti-growth tests |
| `lib/index.js:679–684` | Continue/retry installation is commented out | No claim that the archived plugin automatically retries by default |
| `lib/rewind.js:142–170` | Select a prior completed conversation boundary and fork history | No file/Git rollback or transaction guarantee |

Recovery, gate routing and fingerprint fixes also address defects reproduced
in this project's existing code; they are not claims about measured performance
or quality gains from running the external plugin.

## Rejected or deferred

- The archive's Node plugin, host patches, approval/sandbox rewrites and its
  second retry controller are not installed. Its continue/retry installer is
  commented out by default in `lib/index.js` (1.1.17), so claiming automatic
  retry behavior would be false.
- `rewind` forks session history only; it does not roll back files, Git or
  external side effects, so it is not a task-level transaction mechanism.
- Stable `session+seq` dedup keys need a real host event contract. Current
  adapters do not supply such a sequence, so the existing whole-payload replay
  guard is kept rather than guessed.

## Host availability

Spec installs hook configuration, but a configuration file does not prove
that a running host emits the event. This project's DSH adapter declares
only `pre_tool` and `stop`. The inspected DSH 0.2.2-rc.3 application does not
bundle the optional Claude hooks bridge; this observation says nothing about
additional components in a user's active profile. The installer and `doctor`
check configuration, not bridge activation or live event delivery.

The host-independent recovery entry is `python3 scripts/spec.py next`:
current tasks and run evidence are projected on demand, and any existing,
matching snapshot clues are advisory additions. An empty or absent snapshot
is normal. Automatic context injection additionally requires the relevant
host event and working bridge. Only hosts that actually emit
`SessionStart(source=compact)` get the compact-start reminder. No host
component or profile configuration is installed or changed by these fixes.

## Verification

The behavior is pinned by `tests/test_session_recovery.py`,
`tests/test_prompt_tier.py`, `tests/test_route.py`, `tests/test_hooks_dispatch.py`,
`tests/test_encourage.py`, `tests/test_evidence.py` and
`tests/test_fingerprint_freshness.py`. Run the focused suites after recovery
changes, then the non-release project suite plus Ruff lint and formatting.
These are local fixture/regression checks, not a live DSH bridge end-to-end
test. Release payload rebuilding remains a separate release step.

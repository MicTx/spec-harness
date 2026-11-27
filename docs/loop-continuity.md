# Evidence-driven continuation

[中文](loop-continuity.zh.md) · [Documentation](README-en.md) · [Execution contract](../references/exec.md)

On `main`, Stop follows actual verification evidence, not a fixed round counter. This behavior is not in the `v2.2.0` release archive.

## Continue, change approach, or stop

| What changed? | Next action |
| --- | --- |
| A verify passes and creates a checkpoint | Advance ready work. Run `spec.py check` when the package is ready for delivery. |
| The same task and verify command fail on a previously unseen tree **and** with a previously unseen failure signature | Request a replan: test a different hypothesis or implementation, or work on an independent ready task. |
| Only the log changes, the candidate or signature recurs, or the host replays a callback | Do not start another turn on that evidence. For example, A → B → A stops when A returns. |
| Only blocked work remains, or a decision needs authorization | End automatic continuation and preserve the evidence and pending decision. Independent ready work can still proceed. |

A replan includes the frozen goal, affected open criteria, and the path to `runs.jsonl`. It changes the approach, not acceptance. For a final verify, a repeated blocker only earns another turn if the tested product tree and diagnostics are both new. Signatures discount runner noise but distinguish exit statuses, including an explicit exit `124` versus a timeout. Stop has no round quota; the [task failure gate](../references/exec.md#failure-ladder) and manual `check` still apply.

## What came from Loop Transformer research?

[Huginn’s recurrent-depth paper](https://arxiv.org/abs/2502.05171) reuses a block of model layers across internal iterations while conditioning on the original input. The paper evaluates early exit by comparing successive prediction distributions. Spec Harness applies the ideas of input anchoring and evidence-based exit **outside** the model: it anchors replanning to the frozen goal and checks Git tree identity and executed verification. It does not run or train Huginn, inspect hidden states, or claim measured model-token savings.

## Earlier session-recovery research

The notes below record a separate source: `dsh-purge` 1.1.17 (MIT). Spec Harness reimplemented selected session-recovery ideas. It did not copy archive code, runtime dependencies, host patches, approval/sandbox rewrites, or installation hooks.

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
  the same principle: the state line, the actionable next step and the first
  failure are protected first; goal and parked text are bounded. Repeated
  injections are deterministic and do not grow recursively.
- **Executable recovery routing.** The current [recovery loop](../references/exec.md)
  uses typed findings rather than matching diagnostic text. After a fix, run
  the same `check`. Stop follows checkpoints and new diagnostics from tested
  candidates, not a round counter. Repeated inputs and blockers release Stop.
- **Single projection per event.** `route.next_card(..., projection=...)`
  and `route.brief(..., projection=...)` reuse a computed projection; the
  earlier `decision=` argument remains compatible. Start and prompt each use
  one route computation. Stop with no new execution evidence skips routing.
- **Fresh evidence and cold recovery.** The latest failed run stores its
  failure-ladder guidance as an additive ledger field; a cold next-action
  projection uses it without a host callback. When no independent task is
  ready, a blocked card names the task and the existing `spec.py unblock`
  action. A checked task that fails final re-verification retains its failure
  evidence and returns to `spec.py check` after the underlying fix. The run
  ledger, not the displayed checkbox or the host snapshot, drives readiness.
  A private Git index retains the stat cache and snapshots changed content,
  including `.spec/architecture` source but excluding local evidence. Final
  checks run once per distinct command, even when the tree did not change.

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
- Stop consumes executed ledger records rather than guessing a stable event
  ID or comparing entire host payloads. Pi may send the same payload before
  and after real progress; only the latter earns a continuation.

## Host behavior

Current Pi consumes `agent_before_settle` to request continuation without a
fake user turn. Claude-style Stop adapters honor reentry protection. Hosts
without continuation use the loop in the current `/spec:check` turn. A config
file alone does not prove a host emits events. The DSH adapter declares
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

The behavior is pinned by [continuation regressions](../tests/test_convergence_hooks.py),
including a seventh evidence-backed replan, a repeated tree with changing output,
cycle detection, and installed Pi dispatcher reentry. [Session recovery](../tests/test_session_recovery.py),
[routing](../tests/test_route.py), and [verification evidence](../tests/test_evidence.py) have separate regression suites.
These are offline behavior checks, not model benchmarks or a live DSH bridge check.

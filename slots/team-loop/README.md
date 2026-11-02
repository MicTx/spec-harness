# team-loop slot

The first built-in spec slot: the agents-team loop-triggering and efficient-management mechanism.
**Route trigger -> managed loop -> converged release.** The tool surface (spawn_agent / wait /
close_agent) comes from the agents-team skill; this slot provides the disk truth (state machine +
primitives) and hook triggering, and the main session reads and writes state per the protocol.

Use this slot when the work must keep trying until its disk evidence converges. It owns the loop
execution segment only; the main session still owns routing, acceptance, and `done`/`push`.

## What it solves

| Gap | Solution |
|---|---|
| (1) No code-level trigger or convergence guard | `loop_route.py` (callable from any session) decides loop shape; the Stop / TeammateIdle guards only enforce an already-registered run. They do not start a loop or adopt an existing team; whether work needs a record at all is the separate admission layer (`scripts/assess_spec_activation.py`) |
| (2) Loops not generic (bound to one domain) | Domain-agnostic run/round/task state machine with three modes: `until-converged / fixed-rounds / batch-fanout` |
| (3) No retry/concurrency/heartbeat/interrupt primitives | `loop_control.py`: exponential backoff with jitter, concurrency admission, staleness watchdog with reap, cooperative interrupt recovery, composable termination conditions (including an external STOP) |

## Install (slot form)

```bash
# From the spec repository or an installed copy root; writes ~/.claude/settings.json by default
python3 scripts/install_slot_hooks.py --slot team-loop
# Project-scope registration
python3 scripts/install_slot_hooks.py --slot team-loop --scope project --project-root /path/to/repo
# Rollback
python3 scripts/install_slot_hooks.py --slot team-loop --remove
```

Fully usable without hook registration: run `slots/team-loop/scripts/loop_route.py --text "<goal>"`
in-session, then drive the loop per this README's protocol.

## Hard preconditions

1. The `agents-team` skill (the tool surface) must be loaded first. Missing surfaces produce an explicit error — never a silent downgrade to a single-session loop.
2. All scripts are pure stdlib Python 3.9+; nothing to install.

## Trigger paths (gap 1: hook + code dual triggering)

| Path | Mechanism | Notes |
|---|---|---|
| Code decision | Any session runs `loop_route.py --text "<goal>"` directly | Emits a JSON decision + suggested configuration, programmatically consumable |
| Explicit request | The user names a loop / iterative advance / multi-round convergence | Enter initialization directly |

`loop_route.py` defers review-fix composite shapes (`REVIEW_FIX_DEFER_PATTERNS` →
`loopRecommended=false`): those shapes belong to `workflow-runner` at the routing level.
`batch-fanout` remains a state-machine mode you can still pick with explicit
`init --mode batch-fanout`; `loop_route.py` does not recommend it.

## Core protocol (the main session must follow this)

Commands are relative to the slot root (`slots/team-loop/`):

1. **init** — `python3 scripts/loop_state.py init --goal "<goal>" --mode <until-converged|fixed-rounds|batch-fanout> [--config '{"maxRounds":8,...}']`.
2. **round + task** — start an explicit round and add its task:
   `scripts/loop_state.py round start --run-dir <dir> --goal "<round goal>"`;
   `scripts/loop_state.py task add --run-dir <dir> --round N --id t1 --agent-type worker --description "<delivery + verify>"`.
3. **Reserve before host spawn** — `scripts/loop_state.py task reserve --run-dir <dir> --round N --id t1`.
   Only after this succeeds may the caller ask the host to spawn. Save the returned `reservation` token;
   pass it as the host's idempotency/correlation key **only if the host actually supports that**. Reservation
   atomically checks the disk concurrency cap, budget, max attempts and recorded backoff via flock + revision
   CAS. A conflict is a rejection: reload and retry the decision, not the host spawn. Reservation consumes one
   task budget/attempt even if the host never starts. It remains occupied indefinitely until reconciled.
4. **Attach** — once the host returns its actual identifiers, immediately run
   `scripts/loop_state.py task spawn --run-dir <dir> --round N --id t1 --agent <agent_id> --submission <submission_id> --reservation <token>`.
   Retrying this same attach with the same token and IDs is idempotent. A worker finishing before attach must
   hold its result until attachment. Do not invent IDs or claim the worker is tracked before attachment.
5. **Observe, don't invent liveness** — bounded `wait` alone is not a heartbeat. A real worker report or a
   host-confirmed liveness observation may be recorded with `task heartbeat ... --source worker --submission <submission_id> --reservation <token>` (use `--source host` only for a host-confirmed observation).
   The CLI relies on the caller's truthfulness; neither the token nor the source flag authenticates the host.
   The old heartbeat without a source records only coordinator activity and does not extend worker liveness.
   `loop_control.py stale --run-dir <dir>` identifies suspects, not dead workers. Only after independently
   verifying a particular host worker stopped, use `stale --reap --run-dir <dir> --task rN/t1 --confirmed-stopped`.
6. **Record result** — `task result ... --outcome ok --result '{"evidence":"<verification>"}' --reservation <token> --confirmed-stopped`,
   or `--outcome fail --error "<reason>" --reservation <token> --confirmed-stopped`. Both outcomes require
   the `--confirmed-stopped` assertion (the main session must first confirm the host worker stopped).
   A failed attempt atomically records its computed backoff; an optional `task backoff --until <epoch>`
   may extend but never shorten it. Do not re-reserve until it expires. At maxAttempts the task becomes failed.
7. **Close and terminate** — after every task in the current round is terminal, record
   `round end --decision pass` (or `--decision fail`), then run `loop_control.py terminate --run-dir <dir>`. A later passing
   round supersedes earlier failed reviews. On verdict=converged, `finish converged --run-dir <dir> --summary "<outcome>"`;
   the command checks convergence itself. On verdict=stopped, reconcile actual host workers before recording
   `finish failed` (or `finish cancelled`). A task exhausting its attempts does not stop other budgeted work:
   wait for in-flight or reserved attempts, close the round as failed and, while rounds and budget remain,
   run a later repair round. A passing decision cannot conceal a failed task in the same round. The final
   allowed round and in-flight budgeted attempts may finish normally; explicit STOP or run timeout remains a hard stop.

`loop_control.py admit` remains a **non-reserving advisory snapshot**, not permission to spawn; it cannot
check a particular task's backoff. The old `task spawn` without a reservation is accepted for compatibility
and checks disk limits when it registers, but it happens *after* host spawn and cannot prevent an untracked
or over-cap host worker if registration fails. Its CLI output warns about this; do not use it for managed dispatch.
Legacy first-attempt results remain accepted without identity; legacy retries require the current, unique
`--submission`. Newly reserved attempts require their token for results and verified heartbeats. For an old
pending failed task with a recorded error but no backoff deadline, first set a conservative deadline with
`task backoff`; an older state lacking both error and deadline cannot establish past backoff evidence.
All callers must record a terminal result only after the corresponding host execution has actually ended:
the local state machine cannot verify that claim or cap physical workers launched outside this protocol.

### Ambiguous host outcomes

Disk reservation is **not** a transaction with an external host. A crash after host spawn but before attachment,
a lost spawn response, a host ignoring a correlation key, or an uncooperative older client can leave an unknown
worker. STOP can also arrive after reservation and before the external host spawn; it cannot atomically revoke
an already-issued host call. If attachment fails, reload state and retry the same attachment; if the host outcome is unknown, keep
the reservation, inspect/reconcile the host using its real capabilities, and do not start another worker.
After independently confirming no worker was started or the specific worker has stopped, the caller may use
`task abandon ... --reservation <token> --confirmed-stopped` to enter the retry/exhaustion path. The flag is
an assertion, not proof. If the host cannot identify or stop the worker, no safe automatic compensation exists:
escalate and keep the reservation. A reservation whose response was lost can be recovered from the task's
`reservationToken` in run.json; protect that file and token. On platforms without flock, reserve fails closed.
State and event writes are separate; a crash can leave a state change without its matching event. Malformed UTF-8
or missing run/attempt counters are corrupt state, not zero counters, and the guards block for manual repair.
STOP is atomically replaced, never truncated in place. The event log rejects a pre-existing multi-link inode and
uses copy-on-write replacement under the run lock, so it never appends to an outside hard link even if the pathname
changes after inspection. Each event copies the existing log (linear in its current size and requiring temporary
disk space); readers following changes must reopen the pathname after replacement, not hold the old inode open.
This does not make an adversarially writable run directory trustworthy or make state and events transactional.

## Interrupts and recovery (gap 3: cooperative interruption)

- **Pause**: `scripts/loop_control.py interrupt --run-dir <dir> --reason "<reason>"` records in-flight and
  reserved tasks. Ask the host to stop known workers; an unknown reservation needs host reconciliation.
- **External stop**: `scripts/loop_control.py stop --run-dir <dir>` writes the STOP sentinel; it does not stop host workers.
- **Resume**: `scripts/loop_state.py resume --run-dir <dir>` keeps in-flight and reserved attempts occupied;
  it does not infer that workers stopped or reset attempts. Verify each worker with the host before recording
  its result or reclaiming it, then follow nextActions. Cancelling an in-flight task similarly requires the
  caller's `--confirmed-stopped` assertion. `finish failed|cancelled` ends disk management, not host processes.

## Communication primitives (worker collaboration)

Collaboration semantics are host-agnostic; the tool surface adapts per host. Four collaboration actions and three anti-deadlock invariants hold for every host:

| Action (semantics) | Expected behavior |
|---|---|
| Handoff (delegation/notification, no answer needed) | Fire-and-forget; never blocks yourself when the peer is busy |
| Decision Q&A (an answer is needed to continue) | Block for the reply, but always with a bounded timeout |
| Answer (replying to a received question) | Targeted reply to the asker, never broadcast |
| List waiters (who is waiting on me) | Can list pending Q&A, avoiding answers aimed at the wrong peer |

Anti-deadlock invariants (every host must satisfy them):

- Decision Q&A is for short bounded questions only; long work must be split into a team-loop task + a handoff message +
  a persisted `task result` — never park long execution on a Q&A as a disguised stall.
- Communication-layer stalls are backstopped by bounded timeouts; execution-layer stalls by the staleness watchdog
  (`stalenessSec`, default 300s), host reconciliation, and the Stop guard. A timeout alone cannot distinguish
  a dead worker from a slow worker; do not reap until the particular host worker is confirmed stopped.
- No busy-waiting: after sending a handoff, keep advancing other parts of the task; once a Q&A gets its reply or times out, continue immediately / record the failure into retry. No polling, no idling.

### Host adaptation table

| Host | Tool surface | Action mapping | Anti-deadlock notes |
|---|---|---|---|
| **Pi** (primary) | The pi-intercom extension's `intercom` tool (send/ask/reply/pending/cancel/supersedes; local broker) | handoff=send; decision Q&A=ask (10-minute default timeout, immediate failure on disconnect, no queueing); answer=reply (auto-targeted); waiters=pending | Disconnected targets fall back to send (broker mailbox redelivers on reconnect); with pi-subagents installed, workers additionally get `contact_supervisor` (need_decision/interview_request/progress_update) via the dedicated channel |
| **Claude Code** (primary) | Native agent teams: teammate sessions + the `SendMessage` tool surface (needs `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) | handoff=SendMessage notification to a teammate; decision Q&A=main session waits at the teammate's turn boundary (bounded via the wait timeout); answer=the teammate's reply in its idle turn; waiters=run-state reads + TeammateIdle/TaskCompleted hook gates | Teammate idle triggers the gate (no release while an in_flight task lacks a recorded ok); team messages are never fabricated, and unconsumed verification lands on disk first |
| **Codex** (primary) | `codex agents` (shared app-server daemon sessions) + `codex exec` (headless) + `exec resume/fork` (continuation) | handoff=write the task description then dispatch with `codex exec`, products handed off on disk; decision Q&A=no native ask equivalent — the decision request is written into the run.json/events.jsonl disk truth and answered by the main session or the next exec; answer=injected when resuming with `exec resume`; waiters=read `.agents/runtime/loop/<run-id>/events.jsonl` | Communication is disk: the absence of realtime Q&A is not a defect — the staleness watchdog and backoff cover it; never invent polling loops for "realtime-ness" |
| Other hosts | Whatever messaging surface the host ships | With inter-session messaging tools, map per the Pi row's semantics; without, follow the Codex row's disk handoff | The same three invariants; missing capabilities are stated truthfully — communication semantics are never fabricated |

### Session topology and model inheritance (standard guidance)

- **Supervisor stays resident**: the target session receiving decision Q&A must live until it answers — Pi uses `pi --mode rpc`
  (stdin held open) or an interactive terminal (a one-shot `pi -p` exits and deregisters; it cannot receive — verified in practice);
  Claude Code uses a resident team session; Codex uses an agent session on the daemon or the main session itself.
- **Workers on demand**: one-shot sessions (`pi -p` / `codex exec`) may exit after sending their handoff; a worker that itself
  needs to receive follow-up Q&A must also stay resident.
- **Model/gateway always inherits the main session**: spawning any worker passes no model/provider arguments — the host default applies
  (same gateway, same model as the local main session). Claude subagent definitions write no frontmatter `model:`
  (omitted means inherit the main session; the `CLAUDE_CODE_SUBAGENT_MODEL` environment variable is the unified-gateway fallback).
  For tiered models, change the gateway mapping — not agent definitions, and never model arguments inside spawn commands.

## Hook contract (the slot ships two guards)

| Hook | Event | Behavior |
|---|---|---|
| `hooks/loop_stop_guard.py` | Stop | Active unconverged run -> block with the next step; no active run (including interrupted) -> release; silent while stop_hook_active |
| `hooks/loop_teammate_gate.py` | TeammateIdle / TaskCompleted | Teammate in_flight task without a recorded ok -> exit 2 feedback telling it to continue |

## Boundaries and discipline

- Disk state is the scheduling record, not proof of host state. Reservation and attachment are separate from host spawn; state and events are separate writes. Wait itself is not worker evidence.
- Never bypass the concurrency cap; never fabricate a task result (no `outcome ok` without a run verify).
- Retry backoff waits for real (no spawn before the backoff expires).
- The Stop hook is advisory to its host: it deliberately emits nothing when `stop_hook_active=true`, and the host may impose its own block limit. It cannot guarantee continued execution or prevent external process exit.
- One run per directory (project `.agents/runtime/loop/<run-id>/`); with multiple runs in parallel, guards fail closed.

## Tests

```bash
python3 -m pytest slots/team-loop/tests/ -q   # unit: state machine / primitives / routing / hook protocol
python3 slots/team-loop/tests/smoke_run.py    # end to end: init->fail->backoff->retry->converge
python3 slots/team-loop/tests/recheck_probes.py  # re-check probes: independent evidence items
```

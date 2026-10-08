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
| (1) No automatic triggering (manual skill loading) | Hook triggering (Stop convergence guard / TeammateIdle gate) + code triggering (`loop_route.py`, callable from any session) |
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

1. **init** — `python3 scripts/loop_state.py init --goal "<goal>" --mode <until-converged|fixed-rounds|batch-fanout> [--config '{"maxRounds":8,...}']`
2. **round** — `scripts/loop_state.py round start --goal "<this round's goal>"` (every round has an explicit sub-goal)
3. **task add** — `scripts/loop_state.py task add --round N --id <t1> --agent-type worker --description "<delivery and verification requirements>"`
4. **Concurrency admission** — `scripts/loop_control.py admit --run-dir <dir>`; with `admit=false` you **must wait before spawning** — never over-spawn
5. **spawn + record** — right after `spawn_agent` returns the agent/submission:
   `scripts/loop_state.py task spawn --round N --id <t1> --agent <agent_id> --submission <submission_id>`
6. **wait + heartbeat** — `wait` uses a bounded timeout; before and after every wait:
   `scripts/loop_state.py task heartbeat --round N --id <t1>`; staleness detection via `scripts/loop_control.py stale`, and dead-worker recycling via `scripts/loop_control.py stale --reap --run-dir <run directory>` (stale in_flight enters the failure retry path by fail, releasing the concurrency slot)
7. **result** — `scripts/loop_state.py task result --round N --id <t1> --outcome ok --result "<verification evidence>"`,
   or `--outcome fail --error "<reason>"` (enters the retry path automatically)
8. **Retry discipline** — after a failure, `scripts/loop_control.py backoff --attempt N [--run-dir <run directory>]` computes the backoff and
   `scripts/loop_state.py task backoff --round N --id <t1> --until <epoch>` records it; respawn only after the backoff expires;
   at maxAttempts the task becomes failed (termination conditions catch it)
9. **Termination decision** — at every round close and before every urge to stall: `scripts/loop_control.py terminate --run-dir <dir>`;
   verdict=converged -> `round end --decision pass` + `finish converged`; verdict=stopped -> `finish failed|cancelled`
   per the reason. Never stall silently.
10. **Convergence** — `scripts/loop_state.py finish converged --run-dir <dir> --summary "<outcome>"`;
    the Stop hook sees the terminal state and releases.

## Interrupts and recovery (gap 3: cooperative interruption)

- **Pause**: `scripts/loop_control.py interrupt --run-dir <dir> --reason "<reason>"`,
  then `close_agent` each returned in-flight item; the state moves to interrupted.
- **External stop**: `scripts/loop_control.py stop --run-dir <dir>` writes the STOP sentinel; the next termination decision stops.
- **Resume**: `scripts/loop_state.py resume --run-dir <dir>` validates consistency, moves interrupted in-flight
  tasks back to pending, emits nextActions, and continues from them.

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
  (`stalenessSec`, default 300s; `stale --reap` recycles dead workers into the retry path) and the Stop convergence guard. The two layers complement each other; both are required.
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
| `hooks/loop_stop_guard.py` | Stop | Active unconverged run -> block with the next step; no run -> release; silent while stop_hook_active |
| `hooks/loop_teammate_gate.py` | TeammateIdle / TaskCompleted | Teammate in_flight task without a recorded ok -> exit 2 feedback telling it to continue |

## Boundaries and discipline

- Disk state is the single source of truth: every spawn/wait/result lands in run.json/events.jsonl synchronously; session memory does not count.
- Never bypass the concurrency cap; never fabricate a task result (no `outcome ok` without a run verify).
- Retry backoff waits for real (no spawn before the backoff expires).
- The Stop hook may be officially blocked at most 8 times in a row: every block must produce substantive state progress — no idling.
- One run per directory (project `.agents/runtime/loop/<run-id>/`); with multiple runs in parallel, guards fail closed.

## Tests

```bash
python3 -m pytest slots/team-loop/tests/ -q   # unit: state machine / primitives / routing / hook protocol
python3 slots/team-loop/tests/smoke_run.py    # end to end: init->fail->backoff->retry->converge
python3 slots/team-loop/tests/recheck_probes.py  # re-check probes: 25 independent evidence items
```

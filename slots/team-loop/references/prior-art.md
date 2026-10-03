# Prior-art research (2026-09-19, official first-party docs)

This note records why the team-loop slot has the shape it does. It is an evidence trail for
maintainers, not a user-facing promise that every upstream feature is available here. All sources
are official first-party documentation, verifiable as of the fetch date.

## 1. Claude Code hooks (code.claude.com/docs/en/hooks.md)

| Mechanism | Fact | Adoption |
|---|---|---|
| UserPromptSubmit | stdin carries `prompt`; a stdout JSON `hookSpecificOutput.additionalContext` injects into Claude's context as a system reminder (10K-char cap); no side effects beyond the never-forced block | `hooks/loop_route_hook.py`: route-suggestion injection, the hook half of gap 1 |
| Stop | `{"decision":"block","reason"}` prevents stalling; `stop_hook_active=true` means this round already continued via a stop hook (guards double enforcement); the official limit force-releases after 8 consecutive blocks; `background_tasks`/`session_crons` distinguish "truly done" from "waiting on a background wakeup" | `hooks/loop_stop_guard.py`: blocks on unconverged runs with the next action in the reason; silent while stop_hook_active; the 8-block cap is written into the protocol discipline (each round must advance substantively) |
| TeammateIdle | Fires when a teammate is about to go idle; exit 2 + stderr sends feedback back to the teammate to keep working; `{"continue":false,"stopReason"}` stops outright | `hooks/loop_teammate_gate.py`: in_flight task without closure -> exit 2 |
| TaskCompleted | Can force acceptance before a task is marked complete | The same script reused per event |
| /goal command | Official positioning: "a session-level prompt-based Stop hook: keep working toward a condition" | Semantic alignment: this skill's Stop guard is the disk-truth version of /goal |

## 2. Claude Code agent teams (code.claude.com/docs/en/agent-teams.md)

- A lead supervises teammates; teammate idle notifications automatically carry the final answer; API error notifications reach the lead.
- The shared task list persists in a local directory — "resumed sessions keep their tasks".
- Task-size best practice: one teammate task = one self-contained delivery unit (a function / a test file / a review report).

**Adopted**: run/round/task state + the event log all persist under `.agents/runtime/loop/`;
after a session interruption `resume` keeps the tasks (isomorphic to "task list persists");
the `task add` protocol requires the description to state delivery and verification requirements.

## 3. LangGraph persistence (docs.langchain.com/oss/python/langgraph/persistence)

- Checkpointer: persists graph-state snapshots per thread_id; supports resume after interruption and fault-tolerant recovery.
- Event-driven persistence: checkpoint on every state change.

**Adopted**: every `scripts/loop_state.py` state-transition method persists atomically on completion (tmp + fsync + rename);
`resume` validates consistency from run.json + events.jsonl dual sources before continuing (a direct alignment with checkpoint recovery).

## 4. AutoGen AgentChat termination (microsoft.github.io/autogen/stable/.../termination.html)

The built-in termination-condition family: `MaxMessageTermination`, `TimeoutTermination`, `TokenUsageTermination` (budget),
`HandoffTermination`, `ExternalTermination` (external programmatic stop, matching a UI stop button),
`TextMentionTermination`; conditions compose via AND/OR, are stateful, and auto-reset at run end.

**Adopted**: the `scripts/loop_control.py termination_check` condition family
`maxRounds / runTimeout / budgetTasks / external STOP / retries-exhausted failed tasks` is the
disk projection of AutoGen's composable termination (AND semantics); the `STOP` sentinel file = ExternalTermination.
TextMention was not adopted (it requires scanning session text, which disk truth cannot see; the agent judges and calls finish instead).

## 5. Backoff algorithm

The exponential backoff with jitter uses the full-jitter variant from the AWS Architecture Blog
"Exponential Backoff and Jitter": `uniform(0, min(cap, base * 2^(attempt-1)))`.
This implementation then narrows the lower bound with a jitter factor, balancing randomness and reproducible
tests (`--seed`).

## 6. Local precedent

The spec skill's `hooks/claude_stop_guard.py` (the Stop-hook convergence-guard skeleton: stdin protocol,
fail-closed ambiguity handling, block reason carrying repair guidance) — `hooks/loop_stop_guard.py` reuses its
structure directly, with the criterion switched from "spec package convergence" to "loop run convergence".

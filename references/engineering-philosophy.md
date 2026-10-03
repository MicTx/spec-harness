# Engineering Philosophy

These principles are decision tools, not slogans. When two reasonable actions compete, use the conflict order below to choose the smallest action that leaves the strongest evidence.

## Contents

- Four principles
- Conflict priority
- Gate design
- Principle evidence
- Single-orchestrator constraint
- Full-chain mapping
- Quick self-check

The engineering philosophy of this `spec` skill centers on four principles. The goal is not to add a "values statement" but to reduce the four most common AI mistakes on complex tasks: guessing, over-engineering, drive-by changes, and finishing without evidence.

Scope boundary: this philosophy couples only to the carriers this repository already has (`SKILL.md`, `references/*`, `agents/openai.yaml`, `scripts/*`, install/export behavior, and the generated host command aliases); it requires no new multi-end distribution structures.

CLI compatibility convention: `/spec:xx` is the current Claude Code command label; `spec:<stage>` is the stage alias for Codex / generic skill-aware CLIs. Both map to the process stages `new/goal/run/check/done/push/update/status`; `goal` is the explicit one-shot composite entry; `route` is the internal stage resolver behind `/spec`; `tasks` is a script view under status — not a user command.

## Four principles

### 1. Explicit assumptions

Never silently decide requirements for the user.

- Write uncertainties down as assumptions or open questions first
- When several interpretations exist, write the candidates and the current choice
- When information is insufficient to proceed safely, stop and clarify first

Landing in Spec:

- `/spec` and `/spec:new` route and clarify before any package is written
- `spec.md` must contain `已确认事实` (confirmed facts), `关键假设` (key assumptions), `待确认问题` (open questions)
- `/spec:update` must state which assumptions were added, overturned, or converged

### 2. Minimal implementation

Build only the smallest viable solution the current goal requires.

- No "maybe needed later" extension points
- No extra abstractions for one-off scenarios
- No unrequested configurability

Landing in Spec:

- `spec.md` must state the minimal implementation path
- `tasks.md` tasks orbit the closed loop, not imagined future capabilities
- `/spec:new` and `/spec:update` both name "what this round does not do"

### 3. Clear boundaries

Each step touches only what the current task needs.

- No drive-by refactoring
- No drive-by cleanup of unrelated code
- No drive-by style or naming unification

Landing in Spec:

- Every task in `tasks.md` declares `boundary`
- `/spec:run` confirms the current task's boundary before executing
- `/spec:check` specifically looks for unrelated changes mixed in

### 4. Verification first

Define how completion is proven before starting implementation.

- Verification can be tests, builds, manual reproduction, or data comparison
- "Looks like it works" is not verification
- No evidence, no clean archive

Landing in Spec:

- Every success criterion in `spec.md` carries `verify`
- Every task in `tasks.md` carries `verify`
- `/spec:check` and `/spec:done` must carry evidence; `check_spec_package.py` requires at least one non-placeholder script/test/build evidence item, and when cross-carrier or structure gates are enabled, the corresponding checklist sections must pass as well

## Conflict priority

When the principles conflict, decide in this order:

1. Clear boundaries
2. Minimal implementation
3. Verification first
4. Explicit assumptions

This priority governs the agent's conflict decisions, not script output order: the "needs attention" section of `check_spec_package.py` lists failures in the fixed order assumptions & scope → simplicity → record conventions → change boundaries → function & verification → optional gates; when multiple failures compete, the priority above decides which gets fixed first.

## Gate design

Gates fail closed. A gate that did not run is not a passed gate (未运行=未通过): ending generation is a run event, passing acceptance is the business conclusion, and hitting an iteration limit is a terminal state — never a success verdict. When designing or extending a gate, decide three things before writing code:

1. Fail closed on missing observations. Unavailable state, an absent verdict, an unevaluated check, or an interrupted loop must block or stay explicitly advisory — it must never count as passing. An optional gate is in force exactly when its marker (e.g. a checklist section) is present; absent means fully inert, never half-trusted.
2. Pick the carrier from the risk shape, not from habit. The authoritative gate list lives in the scripts and stage docs — principles here, never a copied gate inventory.
3. Keep the output channel parseable: contract output on stdout, diagnostics on stderr (see Script working-directory conventions in `references/commands.md`).

| Risk shape | Carrier | Precedent |
| --- | --- | --- |
| Static structure (decidable from files/text) | `check_spec_package.py` script gate | optional checklist gates: section present → in force and must be fully checked; absent → inert |
| Needs human review | the `/spec:check` round | item-by-item checklist walk with evidence |
| Dynamic condition | hook | `hooks/claude_stop_guard.py` convergence guard |
| Host side effect | explicit authorization | `workflow-runner` package contract and main-session verification |

## Principle evidence

The four principles already land in fields: `假设` (assumptions), `最小实现路径` (minimal path), `boundary`, `verify`. Do not write a "philosophy in action" narrative per package.

## Single-orchestrator constraint

The main session carries the critical path: problem modeling, task decomposition, integration, testing, independent acceptance, and global state. It is the only orchestrator. Bounded sidecar delegation is allowed only after a machine route decision (`scripts/route_decision.py`) plus a written assignment contract (see `references/orchestration.md`); loop-shaped execution runs only under a managed slot's README protocol with its disk-truth state machine, concurrency admission, retry backoff, and termination checks persisted. No fake parallelism; outsourcing that bypasses these managed paths stays forbidden, and routing, acceptance, and the `done`/`push` gates never move out.

- Stay explicit: write assumptions when unsure; never advance silently.
- Stay minimal: delegate only lanes with clean ownership and real waiting value; no parallel frameworks for theoretical throughput; even on a route hit, add no parallelism beyond the contracted lanes.
- Stay bounded: change only what the current task's `boundary` covers; sidecar lanes inherit the same boundary discipline.
- Verification first: self-reported results are not acceptance — neither from the main thread nor from a sidecar lane; run the task's `verify`-described verification before checking off.

## Full-chain mapping

| Stage | Default action | Typical mistake | Philosophy correction | Failure signal |
| --- | --- | --- | --- | --- |
| `/spec` | Auto-route the next step | Diving straight in | State the current understanding, the active package, and the next hop first | The next hop relies on an implicit assumption |
| `/spec:new` | Create the task package | Assuming requirements silently | Write facts, assumptions, ambiguities, and the minimal path first | `spec.md` missing assumptions or out-of-scope |
| `/spec:goal` | Run the goal chain end to end | Treating automation as gate bypass | Still chain new/run/check/done/push on the package's disk truth (done itself covers the archive commit); stop at any gate failure | Committing despite failed verification, or pushing an untriaged dirty tree |
| `/spec:run` | Advance implementation | Scope creep or drive-by refactoring | Implement within task boundaries in this session; run `verify` independently before checking off | The current task lacks `boundary` or `verify`; checked off without running verification |
| `/spec:check` | Run acceptance | Verbal-only "passed" | Judge item by item with the checklist and evidence; base gates + enabled new gates + command-class evidence must all hold | No file-level evidence; missing script/test/build evidence or empty outcome metrics |
| `/spec:done` | Archive and commit | Ending without a completion summary, writing executable fixes as free-form leftovers, or archiving without committing | Output delivery scope, assumption review, verification evidence, and the v1 issue dispositions, then finish this round's commit; a follow-up may be referenced only after it was executed and archived | Summary contains placeholders, invalid dispositions, executable leftovers, or no commit after archiving |
| `/spec:push` | Git handoff | Dangerous Git operations without the safety preflight | Enforce clean tree, non-main execution, protected-branch refusal, and the remote SHA lease before merging, pushing, and deleting the merged branch | Deleting a branch before the worktree and merge state are confirmed |

## Quick self-check

Before advancing any task, ask yourself:

1. Did I write down the premises that are not yet confirmed?
2. Can I clearly explain why this is the minimal viable path?
3. Can I state which areas this change must not touch?
4. Do I already know how to prove the task complete?

If any answer is missing, fix the documents first — do not push through.

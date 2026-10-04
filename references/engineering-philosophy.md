# Engineering Philosophy

These principles are decision tools, not slogans. When two reasonable actions compete, use the conflict order below to choose the smallest action that leaves the strongest evidence.

The engineering philosophy of this `spec` skill centers on four principles. The goal is not to add a "values statement" but to reduce the four most common AI mistakes on complex tasks: guessing, over-engineering, drive-by changes, and finishing without evidence.

## Contents

- Four principles
- Conflict priority
- Gate design
- Quick self-check

Scope boundary: this philosophy couples only to the carriers this repository already has (`SKILL.md`, `references/*`, `agents/openai.yaml`, `scripts/*`, install/export behavior, and the generated host command aliases); it requires no new multi-end distribution structures.

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
- Every new package declares the smallest verification level that can prove its boundary: `package` by default, `integration` when directly affected cross-module paths matter, and `project` only for shared or release-wide risk
- `/spec:check` and `/spec:done` must carry evidence at the declared level; `check_spec_package.py` requires at least one non-placeholder script/test/build evidence item, and when cross-carrier or structure gates are enabled, the corresponding checklist sections must pass as well

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

## Quick self-check

Before advancing any task, ask yourself:

1. Did I write down the premises that are not yet confirmed?
2. Can I clearly explain why this is the minimal viable path?
3. Can I state which areas this change must not touch?
4. Do I already know how to prove the task complete?

If any answer is missing, fix the documents first — do not push through.

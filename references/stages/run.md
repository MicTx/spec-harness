# `/spec:run`

Orchestration never decides admission; resume a matching active package. If an all-green package receives a new goal, update scope and unfinished tasks via `update` or `new` before implementation; resume proves identity, not scope coverage.

Goal: the main session completes all tasks in dependency order, verifying each before checking it off.

Compatible invocation: Claude Code `/spec:run`; other skill-aware CLIs trigger `spec:run` via `$spec`.

Execution order:

1. Read the package documents, confirm the unique integration branch; protected, detached, unmatched, or double-bound branches fail closed — never mix packages.
2. `tasks.md` is the contract source of truth: every task has `boundary` (what may change) and `verify` (how completion is proven); ordered work declares `depends-on`.
3. Execute routing — a single entry deciding from package structure, never goal wording: `python3 scripts/route_decision.py --root <project> --slug <slug>` returns `{route, basis, structure, score, reason, lanes, risks, override, channel_profile}`; its only routing input is package structure, zero structural signal routes `local` (no text-length fallback — the same task worded short or long routes identically), and an unreadable package fails closed instead of guessing from text. `--route <token>` (or a valid `### 5.4 编排策略` route, honored as explicit override with `source: package-5.4`) must stay inside the five-token vocabulary; vocabulary, lane rules, and the assignment contract live in [../orchestration.md](../orchestration.md). Slot triggers are advisory and strong-signal only — `slots/team-loop/scripts/loop_route.py` for convergence-bound or explicit-round shapes, `slots/workflow-runner/scripts/workflow_route.py` for composed shapes (batch fan-out, multi-dimension parallel review, perspective panels, review-fix loops); weak words alone deterministically degrade to ordinary sidecar orchestration — never a silent recommendation, and a missing slot tool surface is surfaced explicitly per the slot README. With no slot recommendation, follow the orchestration route (`local` stays on the main thread; `explore`/`build`/`review` may spawn bounded sidecar lanes under the assignment contract). Routing scripts only surface deterministic facts — structure metrics, risk labels, disjoint boundary-module ownership — and never delegate on their own.

4. Execute the earliest ready unfinished task; run its `verify` (运行目录/前置条件/完整命令/成功判定/失败处理) before checking it off. Revisit decisive judgments when an assumption is falsified or scope changes; record counterevidence in §2.5 and revise scope/verify before proceeding, not after every tool call. Apply the primary-source/oracle rules in [`../templates.md`](../templates.md).
5. Continue with the next ready task until all tasks are done. On a blocker, record a `!` marker or a `blocked:` status line on the task, tell the user the concrete impact and what is already done — never skip silently or fake completion.
6. On session pause/end, collaborator handover, human escalation, or cluster-lane finish, append one handoff entry (`python3 scripts/spec_handoff.py update --slug <slug> --event pause|takeover|escalation|lane-end ...`); schema and write points: [../handoff.md](../handoff.md).
7. After every task is checked, enter `check`: run the gate scripts, fill acceptance-surface evidence (see [`check.md`](check.md)), fix failures, then move to `done`.

Constraints:

- Self-reported results never complete a task: run the `verify`-described verification before checking off; no "should work" check-offs.
- `local` route by default; sidecar delegation only under the orchestration route decision plus the assignment contract in [../orchestration.md](../orchestration.md); loop-shaped work only inside a managed slot's protocol.
- Real fixes, implementation, and tests happen on the main thread, inside contracted sidecar lanes, or inside the managed slot protocol; no fake "in-progress" parallelism.
- The slot execution segment equally forbids fabricated task results: no `outcome ok` without a run `verify`; concurrency admission, retry backoff, and heartbeats follow the slot's disk state.
- No busy-waiting, no polling; user interruptions always outrun the loop.

Output follows [../output-contracts.md](../output-contracts.md): project goal, real progress, results, blockers.


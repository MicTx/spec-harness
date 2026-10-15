# `/spec:run`

Goal: the main session completes the whole task package on its own: execute tasks in dependency order, verify each item, and check them off — not just a slice.

Compatible invocation: Claude Code `/spec:run`; other skill-aware CLIs trigger `spec:run` via `$spec`.

Execution order:

1. Read the package's three documents, confirm the unique integration branch; protected, detached, unmatched, or double-bound branches fail closed — never mix packages.
2. `tasks.md` is the contract source of truth: every task has `boundary` (what may change) and `verify` (how completion is proven); ordered work declares `depends-on`.
3. Execute routing — a single entry deciding from package structure, never goal wording. Run `python3 scripts/route_decision.py --root <project> --slug <slug>`: it reads open task count, boundary module coverage, dependency-graph width, and risk labels, returning `{route, basis, structure, score, reason, lanes, risks, override, channel_profile}` — `basis: package-structure` names the evidence: zero structural signal routes `local`; risk labels route `review`; a wide multi-surface or dependency-tangled package routes `explore`; a cleanly sliced mid-size package routes `build`; there is no text-length fallback, so the same task worded short or long routes identically. `--text "<goal>"` is a read-only diagnostic, never a routing input; an unreadable package fails closed instead of guessing from text. `--route <token>` (or a valid `### 5.4 编排策略` route, honored as explicit override with `source: package-5.4`) must stay inside the five-token vocabulary; an invalid 5.4 route is blocked by `scripts/route_spec_package.py` before the run stage (vocabulary and assignment contract in [../orchestration.md](../orchestration.md)). Slot triggers are advisory and strong-signal only: `slots/team-loop/scripts/loop_route.py --text "<package goal/task description>"` hands loop-until-converged work to the managed protocol in `slots/team-loop/README.md` (disk truth under `.agents/runtime/loop/<run-id>/`) only on convergence-bound or explicit-round signals, and `slots/workflow-runner/scripts/workflow_route.py --text "<package goal>"` recommends only on composed-shape signals — batch fan-out, multi-dimension parallel review, perspective panels, and review-fix loops follow the same-shape disambiguation in [../orchestration.md](../orchestration.md): the repository-owned `workflow-runner` driver when a worker CLI is available, otherwise `team-loop`; weak words alone deterministically degrade to ordinary sidecar orchestration — never a silent recommendation. With no slot recommendation, follow the orchestration route (`local` stays on the main thread; `explore`/`build`/`review` may spawn bounded sidecar lanes under the assignment contract). Routing scripts only surface deterministic facts — structure metrics, risk labels, disjoint boundary-module ownership — and never delegate on their own. A missing slot tool surface is surfaced explicitly per the slot README — never degrade silently or spawn agents outside the protocol.

4. Start from the earliest ready unfinished task. One task at a time; on completion, run the verification its `verify` line describes — the line is written to the complete-verify recipe (运行目录/前置条件/完整命令/成功判定/失败处理), so run it as written instead of probing the environment — and check it off only after it passes.
5. Continue with the next ready task until all tasks are done. On a blocker, record a `!` marker or a `blocked:` status line on the task, tell the user the concrete impact and what is already done — never skip silently or fake completion.
6. On session pause/end, collaborator handover, human escalation, or cluster-lane finish, append one handoff entry (`python3 scripts/spec_handoff.py update --slug <slug> --event pause|takeover|escalation|lane-end ...`); schema and write points: [../handoff.md](../handoff.md).
7. After every task is checked, enter `check`: run the gate scripts, fill `checklist.md` evidence, fix failures, then move to `done`.

Constraints:

- Self-reported results never complete a task: run the `verify`-described verification before checking off; no "should work" check-offs.
- `local` route by default; sidecar delegation only under the orchestration route decision plus the assignment contract in [../orchestration.md](../orchestration.md); loop-shaped work only inside a managed slot's protocol.
- Real fixes, implementation, and tests happen on the main thread, inside contracted sidecar lanes, or inside the managed slot protocol; no fake "in-progress" parallelism.
- The slot execution segment equally forbids fabricated task results: no `outcome ok` without a run `verify`; concurrency admission, retry backoff, and heartbeats follow the slot's disk state.
- No busy-waiting, no polling; user interruptions always outrun the loop.

Output follows [../output-contracts.md](../output-contracts.md): project goal, real progress, results, blockers.


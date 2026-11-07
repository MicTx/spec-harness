# `/spec:run`

Recover [native context](../handoff.md#native-session-recovery) before routing. Orchestration never decides admission; resume a matching active package. If an all-green package receives a new goal, update scope and unfinished tasks via `update` or `new` before implementation; resume proves identity, not scope coverage.

Goal: the main session completes all tasks in dependency order, verifying each before checking it off.

Compatible invocation: Claude Code `/spec:run`; other skill-aware CLIs trigger `spec:run` via `$spec`.

Execution order:

1. Read the package documents, confirm the unique integration branch; protected, detached, unmatched, or double-bound branches fail closed — never mix packages.
2. `tasks.md` is the contract source of truth: every task has `boundary` (what may change) and `verify` (how completion is proven); ordered work declares `depends-on`.
3. Run `python3 scripts/route_decision.py --root <project> --slug <slug>`: `{route, basis, structure, score, reason, lanes, risks, override, channel_profile}` derives from package structure, never goal wording or text length. Zero structural signal routes `local`; unreadable input fails closed. `--route <token>` or valid `### 5.4 编排策略` (`source: package-5.4`) overrides within the five-token vocabulary in [../orchestration.md](../orchestration.md). Routing reports facts; it never delegates. Before slot routing, check installation: core omits slots and agent role documents. Report missing capabilities; use an authorized ordinary route only if it meets the request, otherwise block the segment. Never fabricate managed execution or install extensions without authorization. Strong advisory triggers: `slots/team-loop/scripts/loop_route.py` for convergence/explicit rounds; `slots/workflow-runner/scripts/workflow_route.py` for fan-out, parallel review, panels or review-fix loops. Weak words degrade to ordinary routing. Without a slot recommendation, `local` stays on the main thread; `explore`/`build`/`review` may use bounded sidecars under the assignment contract.

4. Apply mandatory ultrathink; prioritize ready `[fact]`/`[reversible]` inquiry, otherwise the earliest ready unfinished task. Read evidence, test the decisive uncertainty, choose within scope and execute. Fill the conclusion, run `verify` (运行目录/前置条件/完整命令/成功判定/失败处理), then check off task/question with matching `runs` proof. If an assumption is falsified or scope changes, update §2.5 and scope/verify before proceeding; follow [`../templates.md`](../templates.md#autonomous-inquiry). A label grants no permissions.
5. Continue with the next ready task until all tasks are done. On a blocker, record a `!` marker or a `blocked:` status line on the task, tell the user the concrete impact and what is already done — never skip silently or fake completion.
6. On session pause/end, collaborator handover, human escalation, or cluster-lane finish, append one handoff entry (`python3 scripts/spec_handoff.py update --slug <slug> --event pause|takeover|escalation|lane-end ...`); schema and write points: [../handoff.md](../handoff.md).
7. After every task is checked, enter `check`: run the gate scripts, fill acceptance-surface evidence (see [`check.md`](check.md)), fix failures, then move to `done`.

Constraints:

- Self-reported results never complete a task: run the `verify`-described verification before checking off; no "should work" check-offs.
- `local` route by default; sidecar delegation only under the orchestration route decision plus the assignment contract in [../orchestration.md](../orchestration.md); when an installed managed slot is required, loop-shaped work follows its protocol, and an unavailable required slot is reported rather than silently imitated.
- Real fixes, implementation, and tests happen on the main thread, inside contracted sidecar lanes, or inside the managed slot protocol; no fake "in-progress" parallelism.
- The slot execution segment equally forbids fabricated task results: no `outcome ok` without a run `verify`; concurrency admission, retry backoff, and heartbeats follow the slot's disk state.
- No busy-waiting, no polling; user interruptions always outrun the loop.

Output follows [../output-contracts.md](../output-contracts.md): project goal, real progress, results, blockers.

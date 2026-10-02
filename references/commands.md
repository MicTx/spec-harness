# Commands

Read only the `## /spec:<stage>` section for the current stage; do not read this file end to end. The default line is `new / run / check / done / push`, plus `update` / `status`; `goal` is an optional entry point; `doctor` and `organize` are environment/structure maintenance entry points outside the task-package state machine.

## Contents

- CLI compatibility layer
- `/spec`
- `/spec:new`
- `/spec:run`
- `/spec:check`
- `/spec:done`
- `/spec:push`
- `/spec:update`
- `/spec:status`
- `/spec:doctor`
- `/spec:organize`
- `/spec:goal`

## CLI compatibility layer

Only the main chain stays user-visible:

| Command | Stage | Purpose |
| --- | --- | --- |
| `/spec` | route (internalized) | Decide the next step automatically; route is not exposed as an explicit stage |
| `/spec:new` | new | Create a task package; lock scope, tasks, and acceptance criteria |
| `/spec:goal` | goal | One goal sentence runs the full chain: plan, create/resume, execute, verify, archive, commit, then hand off to push |
| `/spec:run` | run | Execute to a result in one shot; resume from the task package if interrupted |
| `/spec:check` | check | Add a human-AI review round; write actionable issues back to the package and fix them in the same round |
| `/spec:done` | done | Close the task, distill knowledge, archive, and create the Git commit |
| `/spec:push` | push | After done and commit, merge, push, and delete the merged branch |
| `/spec:update` | update | Add, remove, or adjust tasks and scope mid-flight |
| `/spec:status` | status | Overview across task packages: per-package progress, blockers, next step |
| `/spec:doctor` | doctor | Environment self-check and repair: Python/git, skill installation, command files, project skeleton, hook pointers; `--fix` applies safe repairs |
| `/spec:organize` | organize | Structure tidy-up: first-principles structure audit from machine facts; when unreasonable, optimize the layout without breaking inter-module references, clean/merge deprecated items, and sync docs |

Codex / generic skill-aware CLIs (Gemini CLI, Grok Build, OpenCode, OpenClaw, Hermes, Pi, etc.) trigger `$spec` and then use `spec:new/goal/run/check/done/push/update/status/doctor/organize`. The route and tasks views remain internal script capabilities, not user commands. See the README installation section for the per-host install directory matrix.

Before executing any command, pass the four questions from `engineering-philosophy.md`: are the assumptions written down, is this the minimal path, is the boundary clear, and how is completion proven. If any answer is missing, fix the documents first.

## `/spec`

Goal: act as the default user entry point; run route internally, then continue with the most fitting stage.

Execution order:

1. Run `python3 scripts/route_spec_package.py --root <project>` first to determine the task-package state.
2. With no active package, move to `new`; if the user also asks for `push` and the work tree / unpushed commits carry discrete changes, enter the push retro-pack path (atomic packing by theme, or one whole new spec package) — never skip packing and push directly.
3. With an active package that can continue, move to `run`.
4. When the user asks for a review, move to `check`.
5. When the user asks to archive and close out, move to `done`.
6. When the user gives a full goal and asks for the one-shot chain, move to `goal`; inside, `goal` still follows the new/run/check/done/commit/push order.
7. When the user asks to merge, push, and delete the merged branch after done and commit, continue with the standalone `push` stage internally; the user only receives the actual publication result or the external blocker.
8. When the user adjusts scope or tasks, move to `update`.
9. When the user only wants overall progress, move to `status`.

Constraints:

- Route is never presented as a user-facing stage.
- User-visible output covers actual project progress, current work, concrete problems, and delivered results; internal routing commands are not offered as the user's next step.
- If routing depends on a key assumption, state the assumption first or ask the minimal clarifying question.

## `/spec:new`

Goal: create a new task package and compress a complex task into an executable specification.

Compatible invocation:

- Claude Code CLI: `/spec:new`
- Codex / generic skill-aware CLI: command alias `spec:new`, triggered by `$spec`

Execution order:

1. Clarify the most critical information: project goal, target users, MVP, technology preferences, boundaries, and exclusions.
2. Write out explicitly: confirmed facts, key assumptions, open questions, optional interpretations, and current trade-offs.
3. Name the package with a stable, readable `YYYY-MM-DD_<verb>-<object>` Development Record slug (e.g. `2026-06-12_add-push-stage`); verbs come from the controlled vocabulary in `references/naming-and-commits.md`.
4. In a Git repository, run the `/spec:new` branch preflight before writing any package file:
   - Require a clean tree; with uncommitted or untracked changes, stop and output triage advice (commit, stash, or move unrelated changes into their own Development Record). Never carry external dirty state into a new package.
   - Before creating the branch, scan active Development Records for their `Git integration branch` metadata; refuse creation when the target branch is already bound to another active package — one package one branch, one branch one active package.
   - Switch back to `main` (or the explicit `--main-branch`) first. When an upstream exists, `fetch` and update the main branch with `--ff-only` only. If `fetch` fails from a transport-layer outage (same `remote_unavailable_detected` classifier as `push`: e.g. `unable to access`, connection refused, timeout, HTTP 502), create the integration branch from the local main branch instead and record local mode in the script output and `spec.md`; auth failures, permission denial, missing repository, or a non-fast-forwardable main branch still stop.
   - Create and switch to an independent integration branch from the up-to-date main branch; the default name is `spec/YYYY-MM-DD_<slug>`, overridable with `--branch`.
   - If the target integration branch already exists, never silently reuse or overwrite it; stop and ask for an explicit recovery or a different branch name.
   - Refuse creation/use when the target branch is a protected branch: `main`, `master`, `develop`, `release/*`, `hotfix/*`, or the remote default branch.
   - `spec.md` must record the final integration branch; `run/check/done` continue only on that branch.
5. Outside a Git repository, or with the explicit `--skip-git-branch` script argument, record the out-of-scope reason in `spec.md`; otherwise the branch preflight is never skipped.
6. Prefer running:

```bash
python3 scripts/init_spec_package.py --root <project> --slug YYYY-MM-DD_<slug> --title "<project title>"
```

7. For a non-default main branch or integration branch:

```bash
python3 scripts/init_spec_package.py --root <project> --slug YYYY-MM-DD_<slug> --title "<project title>" --main-branch main --branch spec/YYYY-MM-DD_<slug>
```

8. Fill the three documents under `.spec/specs/YYYY-MM-DD_<slug>/`: `spec.md`, `tasks.md`, `checklist.md`; content follows `references/templates.md`. Every task carries `boundary` and `verify`; replace the template examples with real boundaries and verification commands that prove delivery before execution.
9. When the task involves judging whether the project layout is reasonable, write the first-principles audit conclusion into `spec.md` first: directory responsibilities, module boundaries, dependency direction, runtime/source/state separation, entry discoverability, test and documentation support. If the conclusion is unreasonable, add the target tree, a file migration/merge table, a reference migration map, a deprecated-item list, and verification; if reasonable, state explicitly why no restructuring happens.
10. Lock the minimal implementation path in `spec.md` and name the abstractions, extensions, and configurability not done this round.
11. After creation, continue straight into execution when no project-shaping decision is pending; only genuinely undecidable items go to the user.

Constraints:

- `spec.md` states both in-scope and out-of-scope content.
- `spec.md` states key assumptions and open questions.
- In a Git repository, `spec.md` records this Development Record's integration branch; `run`/`check`/`done` continue only on that branch, and a protected, detached, unmatched, or double-bound target branch fails closed.
- `tasks.md` must be executable, every task with `boundary` and `verify`.
- `checklist.md` must be verifiable; avoid vague adjectives.
- For directory optimization, `tasks.md` must split out structure audit, migration/merge, reference verification, documentation sync, and deprecated-item cleanup tasks; never collapse "tidy the directory" into one unauditable task.

Output contract:

- Render the `/spec:new` dashboard per the dashboard contract in `references/output-contracts.md`.

## `/spec:run`

Goal: the main session completes the whole task package on its own: execute tasks in dependency order, verify each item, and check them off — not just a slice.

Compatible invocation: Claude Code `/spec:run`; other skill-aware CLIs trigger `spec:run` via `$spec`.

Execution order:

1. Read the package's three documents, confirm the unique integration branch; protected, detached, unmatched, or double-bound branches fail closed — never mix packages.
2. `tasks.md` is the contract source of truth: every task has `boundary` (what may change) and `verify` (how completion is proven); ordered work declares `depends-on`.
3. Execute routing. First produce an orchestration route decision with `python3 scripts/route_decision.py --text "<package goal>"` — adding `--root <project> --slug <slug>` lets the decision consume the package's open tasks and any valid `### 5.4 编排策略` route; an invalid 5.4 route is blocked by `route_spec_package.py` before the run stage (vocabulary and assignment contract in [orchestration.md](orchestration.md)). Then decide the task shape with `slots/team-loop/scripts/loop_route.py --text "<package goal/task description>"`. On a recommendation hit (`loopRecommended=true`) where loop-until-converged, batch fan-out, or a review-fix loop is needed, hand the execution segment to the managed protocol in `slots/team-loop/README.md` (init -> round -> task -> admit -> spawn/wait -> result -> terminate -> converge; disk truth lands under `.agents/runtime/loop/<run-id>/`); otherwise follow the orchestration route (`local` stays on the main thread; `explore`/`build`/`review` may spawn bounded sidecar lanes under the assignment contract). Both paths keep task-level `boundary`/`verify`, honest check-off discipline, and main-session acceptance gates identical; when a managed slot's tool surface is missing, surface the error explicitly per the slot README — never degrade silently or spawn agents outside the protocol.
4. Start from the earliest ready unfinished task. One task at a time; on completion, run the verification its `verify` line describes, and check it off only after it passes.
5. Continue with the next ready task until all tasks are done. On a blocker, record a `!` marker or a `blocked:` status line on the task, tell the user the concrete impact and what is already done — never skip silently or fake completion.
6. After every task is checked, enter `check`: run the gate scripts, fill `checklist.md` evidence, fix failures, then move to `done`.

Constraints:

- Self-reported results never complete a task: run the `verify`-described verification before checking off; no "should work" check-offs.
- `local` route by default; sidecar delegation only under the orchestration route decision plus the assignment contract in [orchestration.md](orchestration.md); loop-shaped work only inside a managed slot's protocol. Routing, acceptance, and the `done`/`push` gates never leave the main session.
- Real fixes, implementation, and tests happen on the main thread, inside contracted sidecar lanes, or inside the managed slot protocol; no fake "in-progress" parallelism.
- The slot execution segment equally forbids fabricated task results: no `outcome ok` without a run `verify`; concurrency admission, retry backoff, and heartbeats follow the slot's disk state.
- No busy-waiting, no polling; user pause/abort and pending user messages take priority over continuing the loop.

Output follows [output-contracts.md](output-contracts.md): project goal, real progress, results, blockers.

## `/spec:check`

Goal: add a human-AI review round: inspect implementation, evidence, and gaps, then write actionable issues back into the package and fix them in this round.

Compatible invocation:

- Claude Code CLI: `/spec:check`
- Codex / generic skill-aware CLI: command alias `spec:check`, triggered by `$spec`

Prefer running:

```bash
python3 scripts/check_spec_package.py --root <project> --slug YYYY-MM-DD_<slug>
```

The check items are whatever `check_spec_package.py` currently outputs — never hand-copy gate lists. The script renders the project dashboard: failed gates go to "needs attention" and count into the gate metric; optional gates that are not enabled neither participate in the verdict nor produce failures. The default init template ships the `## 边界回归` (boundary regression) negative-sample section, so a fresh package runs the base gates plus that section's gate; the other optional gate headings stay out of the template, and a package without an optional section runs without its gate.

Execution requirements:

- Walk `checklist.md` and the `check_spec_package.py` output item by item; no verbal-only judgments.
- Attach evidence to each item instead of writing just "passed".
- Select checks before acceptance (先选检查再验收): before filling `## 验收证据`, inventory the checks the target project actually has — package.json scripts, Makefile targets, CI configuration, README verification commands — and rank them by decisiveness (fixtures 单测 < 集成/e2e < README 点名验收命令; for performance requests only a bench run decides).
- Run the decisive check for real (裁决性检查必真跑一次): the strongest check the request implies runs at least once before `**验收结果**：通过` is written down, even when the fast layer is all green; when it is slow, give it enough time or honestly record it as not verified — a check that exists but was not run is never marked `out of scope` (适用外).
- On Git projects, capture a freshness anchor when filling `## 验收证据`: a top-level bullet `- 证据锚点：HEAD <sha> @ <ISO-8601 时间>` with the project repo's current HEAD (from `--root`, not the skill checkout). Once the anchor is present it is in force: the checker compares it against the current HEAD and fails the gate with 证据过期需重跑取证 when HEAD has moved (re-run the evidence capture, then update the anchor). No anchor keeps the legacy behavior with an advisory only; archived read-only reviews and non-Git roots always skip the comparison and never fail on it.
- Evidence completeness: command output that is truncated, paged, or timed out must be marked `truncated` and re-captured with a narrower scope; it never counts as a passing basis until the full relevant output is captured.
- For gates that apply only when enabled, mark `out of scope` when not enabled; never fabricate failures.
- Failures state the gap and the fix action, and are written back into `tasks.md` / `checklist.md` and fixed in this round; when a follow-up spec is created, it must be executed, accepted, and archived before returning to the current package — never just list a slug or hand the user homework.
- Structured failure writeback: every failing item is written back with four fixed fields — `未通过项` (which gate or checklist item failed; reuse the task's stable `id: task-` identity where one exists so cross-round duplicates dedupe instead of re-entering), `证据缺口` (what evidence is missing or contradicts the claim), `下一步替代动作` (the alternative action the next round takes instead of an identical re-run), and `处置枚举` (exactly one of `fix_this_round` / `followup` / `accepted_risk`). Respond prohibition: 严禁把未通过项回写成成功结果 — no failing item may ever be recorded as a passing result in `tasks.md`, `checklist.md`, or the round report.
- Vocabulary boundary: the check-stage `处置枚举` words (`fix_this_round` / `followup` / `accepted_risk`) are round-action labels only; archive closure still uses the five `issue_dispositions` types (`resolved_current` / `resolved_followup` / `accepted_risk` / `external_blocked` / `non_actionable`) at `done`. The two vocabularies must never be mixed — a check-round disposition is not an archive closure.
- Dual stop conditions end the check→fix loop; both must be readable directly from the package docs: (a) semantic termination — a round produces `无新增项` (no failing item beyond the previous round's set), so the loop has converged; (b) budget cap — `最多 N 轮` check rounds, with the package declaring its own N (e.g. in `spec.md`).
- Stop-loss attribution: 同一门禁连续 N 轮失败即停止重跑 that gate — re-running an unchanged setup is not a fix. Write the attribution down: suspect the standard is too broad and 拆细 it into finer, separately verifiable items, or fix the `verify` itself, or mark `转人工` (escalate to a human) when neither is within the agent's reach.
- Cross-round bookkeeping: findings live in `checklist.md` as the disk truth (stable IDs dedupe across rounds), and each round hands back a three-state count — fixed this round, still failing, newly found.
- Do not enter the `done` stage before passing.

Output contract:

- Render the `/spec:check` dashboard per the dashboard contract in `references/output-contracts.md`.
- If the script reports pending fixes, fix them before deciding whether to continue to `done`.

## `/spec:done`

Goal: close the task, distill knowledge, archive the task package, and create this round's Git commit; no merge, push, or branch deletion in this stage.

Compatible invocation:

- Claude Code CLI: `/spec:done`
- Codex / generic skill-aware CLI: command alias `spec:done`, triggered by `$spec`

Preconditions:

- `tasks.md` fully complete.
- The overall `check` gate passed, and `checklist.md` fully checked and marked passing.
- Currently on this Development Record's integration branch, not on a protected branch (`main`, `master`, `develop`, `release/*`, or the remote default branch).
- At least one script/test/build command evidence item; optional gate headings that appear in the package have their sections passed.
- Actionable issues found this round have been written back and completed; no executable leftovers enter the archive.

Execution order:

1. Re-verify the package state; confirm no unfinished high-priority items.
2. Distill knowledge from the development process into the executing project's own `.spec/docs/` — the `.spec/` tree inside the repository this task package runs in, never a global carrier (`~/.claude/CLAUDE.md`, user-level `AGENTS.md`, or user-level memory). Only project-specific engineering facts qualify; spec process mechanics (gate formats, checker contracts, script usage) are never distillation material.
3. Prefer running:

```bash
python3 scripts/complete_spec_package.py --root <project> --slug YYYY-MM-DD_<slug> --archive
```

With non-empty issue dispositions, pass repeatable `--issue-disposition '<json>'` or `--issue-disposition @path.json`. Only the five types are allowed: `resolved_current`, `resolved_followup`, `accepted_risk`, `external_blocked`, `non_actionable`; a `resolved_followup` target must already be accepted and archived. `--follow-up` / `--open-risk` / `--not-delivered` / `--deviation` only apply to unarchived drafts; `--archive` rejects them.

For an English Git record format, add `--git-record-language en`; the default `auto` detects Chinese/English from the `spec.md` title. Fields: `date-time | scope | feature | operation | outcome | commit | push`.

4. Generate or update `completion-summary.md`, moving it to `.spec/specs/archive/YYYY-MM-DD_<slug>/` when needed.
5. Draft an atomic commit message from the completed tasks using Conventional Commits (see `references/naming-and-commits.md`):

```text
<type>(<scope>): <description>

Spec: YYYY-MM-DD_<slug>
```

`type` is one of `feat | fix | docs | refactor | test | chore | ...`; `scope` is optional; `description` follows the user's language; the `Spec: <slug>` footer anchors this task package, replacing the deprecated `[Spec-#N]` numbering.

6. Re-inspect the working tree, `git add` the files belonging to this task package, then run `git commit` as a single direct command; the message uses Conventional Commits with the `Spec: <slug>` footer. Never modify files and commit in the same Bash call; never `--no-verify`. This stage never merges, pushes, or deletes branches.
7. Leave merging back to main and deleting the merged working branch to the later standalone `push` stage.
8. Record the completion time so the archive stays traceable.

Knowledge distillation records first:

- Technical difficulties and their solutions
- Key architecture decisions and trade-offs
- Typical pitfalls and how to avoid them
- Operational patterns reusable by later tasks
- Scope: project-specific engineering facts about the executing project only; spec process mechanics live in the skill itself and are never recorded as distillations
- Credential hygiene: 蒸馏文本不得含真实凭证值 — distilled text never carries real secret values; reference credentials by name and injection path (env file / host-side injection, per the assignment contract's Excluded areas in `references/orchestration.md`) and write examples with placeholders such as `<admin-token>`

Output contract:

- Render the `/spec:done` dashboard per the contract in `references/output-contracts.md`; that file is the single source of truth for content requirements — no field lists repeated here.
- When a script generates the summary, state whether the script passed the gate validation.
- `Open items` no longer carries free-text follow-ups; the archive must contain the v1 `## Issue dispositions` JSON. `resolved_current` points at the completed current task, `resolved_followup` points at an archived follow-up spec, and risk/external/non-actionable notes carry their respective owner and trigger fields.

## `/spec:push`

Goal: after `done` and the working-branch commit, safely merge into the main branch, push main, and delete the confirmed-merged local and remote working branch.

Compatible invocation:

- Claude Code CLI: `/spec:push`
- Codex / generic skill-aware CLI: command alias `spec:push`, triggered by `$spec`

Prefer running:

```bash
python3 scripts/push_spec_package.py --root <git-repo> --branch <working-branch> --main-branch main --remote origin [--specs-dir <trusted-root>...] [--slug <slug>...] [--all-packages] [--allow-unarchived]
```

Spec gate scope (choose one):

- **touched (default)**: gate the active/archive packages the working branch touches relative to main; archive paths map to their real slugs. With no Spec-path changes the gate is not skipped: every non-merge commit must carry a `Spec: <slug>` footer, and the slug must resolve to a valid archive in the target revision.
- **Single-package mode**: `--slug <slug>` (repeatable); gate only the named packages.
- **All-packages mode**: `--all-packages`; gate every active package (legacy behavior).

Archive gate: unarchived active packages inside the chosen scope make `push` hard-fail by default, so the `/spec:push` entry also guarantees the `/spec:done --archive` chain ran first. Retro-pack follows the same order — pack first, then each theme package still goes through `run`/`check`/`done` (archive) before `push` (packing path below) — so a properly packed retro-pack passes this gate; only an explicit `--allow-unarchived` downgrades that item to advisory, and broken or failed-acceptance packages always hard-fail.

The Git pre-push hook behaves the same: it validates each pushed ref's target SHA; online it first fetches and pins the actual push remote's main-branch SHA as the only legacy baseline, never accepting caller overrides; `SPEC_PUSH_GATE=all` switches each SHA to all-packages, and `SPEC_SPECS_DIRS=path1,path2` configures extra trusted Spec roots. Git enumeration / remote-main fetch / scope resolution failures all fail closed; only a genuinely local-only run explicitly selects the local main-branch SHA.

Execution requirements:

1. On a dirty tree, triage / pack first — never fail bluntly nor mix-commit:
   - List uncommitted changes with `git status --short` and `git diff --name-only`.
   - **With an active task package**: sort changes into "this package" and "unrelated" against the package's `boundary` and task list.
     - Package-related changes go into this package's commit first (writing back `tasks.md` / `checklist.md` / evidence when needed) before the push preflight.
     - Unrelated changes get atomic commits grouped by theme; when a theme is unclear, collect them in a temporary Development Record, or stash / move them to a separate worktree. Never mix them into the current package.
   - **No active package / no spec opened, discrete changes done then `spec push`** (retro-pack):
     1. Inventory all dirty files and unpushed working-branch commits; cluster them into themes by file and diff intent.
     2. **Prefer atomic packing by theme**: each independently acceptable theme → one `YYYY-MM-DD_<verb>-<object>` Development Record (three documents, boundary/verify, acceptance, archive) plus its atomic commit (footer `Spec: <slug>`); multiple themes mean multiple packages. No catch-all mixing.
     3. **Or one whole new spec package**: when all changes belong to one coherent intent, splitting themes would distort it, or acceptance cannot be split, create **one** package holding all changes with clear in/out scope, task queue, and checklist; run check/done/commit, then push.
     4. Trade-off: clear theme boundaries → multiple atomic packages; single coherent intent → one package; both possible → default to multiple packages (easier to review and roll back). Explicit user choice wins.
     5. Forbidden: pushing mixed-theme dirty state, stuffing discrete changes into unrelated historical packages, skipping the three documents and acceptance, stacking commits only.
   - After triage / packing, the tree must be clean again before merge/push/delete.
2. Before executing, confirm a clean tree, a deletable working branch, and passing local/remote preflight. When the remote is temporarily unreachable and the failure is a transport-layer outage, the script degrades to local-only automatically: merge the working branch into local `main`, keep the local branch as the retry anchor, and prompt to re-run the same `spec push` once the network recovers; auth failures, permission denial, conflicts, lease changes, protected branches, and broken Git hooks never enter local-only.
3. After merging into main and pushing main, re-fetch the remote main and confirm the working branch is still contained in remote main; in the local-only recovery path, re-probe once the remote is back and finish the push and deletion.
4. Delete the local branch only after the merge is confirmed and the remote deletion succeeded; the remote branch is deleted with a remote branch lease.
5. Never delete long-lived branches: `main`, `master`, `develop`, `release/*`, or the remote default branch.
6. Unarchived active packages hard-fail by default; run `/spec:done --archive` first, or pass `--allow-unarchived` explicitly for the retro-pack exception to downgrade to advisory. Truly broken / gate-failing active packages always block. The gate scope defaults to the packages this push touches.
7. The working branch may exist locally only: when the remote is reachable the script publishes it first with `git push -u`, then merges into main; a branch locally ahead of the remote is published first the same way. When the remote is unreachable, nothing is published or deleted on the remote — enter local-only and wait for retry recovery.
8. Being checked out on main is fine as long as `--branch` names a deletable working branch.
9. When the remote Git hosting service reports broken repository Git hooks in `git push` output (e.g. "Git hooks seem to be broken"), treat the remote hook state as untrusted: stop all branch deletion immediately.
   - With broken remote hooks, stop deleting branches and leave server-side repair to the repository maintainer; do not put hosting-vendor admin-panel actions into the default reply.
   - Built-in repair (opt-in): when the remote is a Gitea instance the user administers, rerun push with `--repair-gitea-hooks --gitea-url <base-url> --gitea-token <admin-token>` (or env `SPEC_GITEA_URL` / `SPEC_GITEA_TOKEN`). The script calls the Gitea admin API `POST /api/v1/admin/cron/{task}` per the official Gitea FAQ, running `sync_repo_branches`, `sync_repo_tags`, and `resync_all_hooks` in order; when all succeed it reruns the complete push plan once. A second broken-hooks report on the rerun, or a failed repair call, keeps the fail-closed branch-preserving behavior (repair is attempted once). This is a site-wide operation requiring site-admin credentials; it is off by default, and without the flag or credentials the behavior is identical to the original fail-closed path.

Output contract:

- Render the `/spec:push` dashboard per the dashboard contract in `references/output-contracts.md`.
- State the execute mode, target branch, main branch, remote name, and the commands to run.

## `/spec:update`

Goal: add, remove, or adjust tasks and scope during execution.

Compatible invocation:

- Claude Code CLI: `/spec:update`
- Codex / generic skill-aware CLI: command alias `spec:update`, triggered by `$spec`

Execution order:

1. Read the current `spec.md` and `tasks.md`.
2. Determine whether the user wants to add, remove, or reorder tasks; executable gaps found during execution also go through this stage's write-back — no separate user homework lists.
3. State explicitly: new assumptions, overturned assumptions, scope changes, and whether the minimal path changed.
4. When a change touches principles or script contracts, update the source-of-truth file; other carriers get pointers, not copied gate lists.
5. Before the first rewrite of the three documents, open a checkpoint via the "Update checkpoint lifecycle contract" (begin), then update `tasks.md`.
6. When the change affects scope, acceptance, or technical decisions, sync `spec.md`.
7. When the addition affects acceptance criteria, sync `checklist.md`; after the last rewrite of the three documents, clear the checkpoint with complete per the "Update checkpoint lifecycle contract".
8. Output a change summary: what changed, why, and the impact on current progress.
9. After the write-back, continue this round's `run/check` and finish the newly added executable tasks.

### Delivered-task stability

Adding tasks must not invalidate existing deliverables.

1. **Append a new heading section after the original text**: keep the old task blocks, their trailing `---`, the progress footer, and other tail text verbatim; after all old content, open the appended section with a new Markdown heading (e.g. `## Appended tasks (this round)`) and write the new tasks there. Never move or rewrite a footer that belongs to the old block just to update stats; this round's progress goes into the new section. Never delete or reorder old tasks so positional IDs drift; new tasks use unique explicit IDs.

   ```markdown
   (original tasks and tail text preserved verbatim up to here)
   ---
   (original progress footer — insert nothing before it, rewrite nothing)

   ## Appended tasks (this round)

   - [ ] New task
     - id: task-new-followup
     - boundary: ...
     - verify: ...
   ```

2. **Caller acceptance before complete**: re-parse the tasks in the three documents and confirm every previously delivered task still exists, stays checked, and has unchanged `boundary`/`verify` text. Deleting a task or unchecking an existing task breaks the delivered contract — restore first, then complete.

### Update checkpoint lifecycle contract

In-place rewrites of the three documents by `/spec:update` must be wrapped in an update checkpoint — never bare edits. The lifecycle is **begin → rewrite the three documents → complete**. The single source of truth for checkpoint primitive semantics is `scripts/update_checkpoint_support.py`; the recovery CLI is `scripts/update_checkpoint.py`; this section fixes the contract only, without copying implementation details.

1. **begin (before rewriting)**: open a checkpoint for the package — snapshot the three documents (`spec.md` / `tasks.md` / `checklist.md`) and atomically write the package-internal `update-checkpoint.json` recording intent, creation time, and checksums. When an unfinished checkpoint (active or corrupted) already exists, refuse begin; complete or rollback first.
2. **Rewrite the three documents**: while the checkpoint lives, rewrite `tasks.md` per the execution order above, and sync `spec.md` / `checklist.md` as impacted.
3. **complete (after the write-back)**: re-validate checkpoint integrity and the post-rewrite readability of the three documents; only a full pass clears the checkpoint. Any validation failure keeps the checkpoint (the rollback path remains), and complete never rolls back already-applied updates.

Interrupt recovery (begin without complete):

- **Route blocks**: with an unfinished or corrupted `update-checkpoint.json` in the package, `route_spec_package.py` treats the package as hard-blocked and routes to `status`. Route is the only enforcement point: `check_spec_package.py` / `complete_spec_package.py` do not probe the checkpoint when called directly and will not refuse for it; archiving also carries the file into `archive/` so it stays comparable afterwards. The block clears via rollback/complete or the manual corrupted handling below, not by interception in other stage scripts.
- **Recovery CLI**: `scripts/update_checkpoint.py` operates per package (rollback/complete require `--slug`) and never batch-scans rewrites:

```bash
# Probe: omit --slug to scan all active packages; exit 0=none, 3=active, 4=corrupted
python3 scripts/update_checkpoint.py --root <project> status [--slug YYYY-MM-DD_<slug>]
# Rollback: restore the three documents to the pre-update snapshot, then clear the checkpoint (discard this update)
python3 scripts/update_checkpoint.py --root <project> rollback --slug YYYY-MM-DD_<slug>
# Confirm: run the caller acceptance above first, then validate and clear the checkpoint; applied updates are not rolled back
python3 scripts/update_checkpoint.py --root <project> complete --slug YYYY-MM-DD_<slug> [--expect-intent "<intent>"]
```

- **Corrupted checkpoint**: on checksum mismatch or structural damage, rollback/complete both refuse and manual resolution is required. Manual handling: when the checkpoint structure is still readable, compare its recorded snapshot fields against the current three documents; when unreadable, fall back to Git history. The user then decides keep-new or rollback; after deciding to keep, delete the package's `update-checkpoint.json` to clear the block (when rolling back, first restore the three documents from the snapshot or Git history, then delete the file).

Constraints:

- Never change tasks without changing scope.
- Never quietly change acceptance criteria.
- Never expand scope for possible future needs.
- Never write an executable fix as "suggest the user handle it later".

Output contract:

- Render the `/spec:update` dashboard per the dashboard contract in `references/output-contracts.md`.

## `/spec:status`

Goal: give the user an overview across task packages: per-package progress, blockers, and next steps.

Compatible invocation:

- Claude Code CLI: `/spec:status`
- Codex / generic skill-aware CLI: command alias `spec:status`, triggered by `$spec`

Prefer running:

```bash
# Overview across active packages (omit --slug)
python3 scripts/report_spec_package.py --root <project> --view status
# Single-package detail
python3 scripts/report_spec_package.py --root <project> --slug YYYY-MM-DD_<slug> --view status
```

At minimum include:

- Current package name
- Completed / total tasks
- Current stage
- Blockers
- Suggested next step

Output contract:

- Render the `/spec:status` dashboard per the dashboard contract in `references/output-contracts.md`.
- Explicitly distinguish: completed, in progress, blocked, awaiting confirmation.
- When script output conflicts with manual judgment, the package's actual content wins; fix the documents.

## `/spec:doctor`

Goal: environment self-check and repair. Inspect every foundation and installation surface the skill depends on to find the "installed but broken" gaps; `--fix` repairs the safe items. Task-package content is out of scope (that is `check_spec_package.py`).

Compatible invocation:

- Claude Code CLI: `/spec:doctor`
- Codex / generic skill-aware CLI: command alias `spec:doctor`, triggered by `$spec`

Prefer running:

```bash
# Check the current project environment
python3 scripts/doctor_spec_environment.py --root <project>
# Auto-repair safe items (installer-owned paths only; backups go to ~/.spec-skill-backups/ before deletion)
python3 scripts/doctor_spec_environment.py --root <project> --fix
# Machine-readable output / limit to one host scan
python3 scripts/doctor_spec_environment.py --format json --host claude
```

Check catalog:

1. `python.runtime`: Python ≥ 3.9 (the script set is stdlib-only; the newest syntax feature is 3.9's `str.removeprefix`)
2. `git.available`: git on PATH (the done/push and hook gates depend on it)
3. `skill.layout`: all 32 runtime files present (same source as the smoke test layout assertion)
4. `skill.scripts`: every `scripts/*.py` compiles (detects truncated installs)
5. `skill.marker` + `skill.drift`: installer ownership marker; when a source checkout exists, per-file hash comparison reports drift
6. `hosts.installed`: ten-host directory scan (same environment variables and default paths), versions, partial installs, foreign same-name collisions
7. `claude.commands`: all user-stage command files exist; legacy layout leftovers (`spec:<stage>.md` colon files, `spec:<stage>` alias directories, the `spec.md` base file)
8. `zcode.symlink`: dangling `~/.zcode/skills/spec` links
9. `project.git`: whether the target project is a Git work tree
10. `project.spec`: the `.spec/specs/` and `.spec/specs/archive/` structure
11. `project.hooks`: whether rendered checker pointers still target existing scripts or drifted to another skill copy

Repair actions (`--fix`, all idempotent):

- Remove installer-owned legacy layout leftovers (backed up first; non-installer-owned items are only reported)
- Recreate `.spec/specs/` and `.spec/specs/archive/`
- Regenerate missing Claude stage command files by rerunning `INSTALL_HOSTS=claude bash install.sh` (prints the equivalent command when bash is not on PATH)
- On hook-pointer drift, back up the old hooks and rerun `install_git_hooks.py`

Exit codes: `0` no failures; `1` failures remain (after `--fix`); `2` argument error. Status levels: `ok / info / warn / fail / fixed`.

Output contract:

- Human-readable markdown or `--format json` (checks/summary/fixLog/exitCode).
- User-visible replies follow the script output: which foundations work, which are missing, which were fixed, which need manual action; never hand-write the check conclusions.
- Repairs touch installer-owned paths only; any deletion goes to `~/.spec-skill-backups/doctor-<ts>/` first.

## `/spec:organize`

Goal: structure tidy-up. Run a first-principles structure audit from machine facts, then by default create a new task package in the target project and fix every confirmed finding class. Never delete; archive retired files, directories, code, and stale docs.

Compatible invocation:

- Claude Code CLI: `/spec:organize`
- Codex / generic skill-aware CLI: command alias `spec:organize`, triggered by `$spec`

Prefer running:

```bash
# Structure fact inventory: repo inventory, top-level reference graph (live vs historical), deprecated candidates, file/code/doc finding classes, architecture consistency
python3 scripts/organize_project_structure.py --root <project>
# Hard machine gate: every module-index declared file exists; module-dag.md/mmd matches the index module set
python3 scripts/organize_project_structure.py --root <project> --check
```

Execution order:

1. Run the script for facts first; no preconceptions. Fact sections: top-level inventory, top-level reference graph (live vs historical), deprecated candidates, finding classes (unreferenced files, unreferenced code files, dangling doc paths grouped by missing target), and architecture consistency.
2. Default: create a new task package in the target project with `init_spec_package.py --root <target>` before any move or doc fix (default new package). Do not append findings to an unrelated active package. If init fails because the tree is dirty or the branch is already bound, stop. Do not stash and do not write into another package.
3. First-principles judgment: what is this repository; is every candidate single-responsibility and consumed by live references or an explicit role. Every candidate resolves to one of four verdicts: keep / archive / merge / migrate. `archive` never deletes the source. The verdict states its evidence lines (last commit, reference count, supersession).
4. A confirmed finding class is unfinished until every member is dispositioned. Fixing one example and leaving a sibling in the same class is not done.
5. Archive destination is `<specs-dir>/archive/retired/YYYY-MM-DD/<original-relative-path>` plus `MANIFEST.md` in that dated directory. Never delete. Three proofs are still required before a move: not a declared entry point, zero live references or a doc that contradicts a machine fact, and superseded or explicitly stale against current facts. Without all three, keep and document.
6. Land the conclusion in the new package. Historical records, knowledge documents, and archived task packages are kept by default.
7. Hard rule on reference integrity: any move updates every reference in the same change (installer, exporter, CI, hooks, tests, docs); prove it with three artifacts: a re-scanned reference graph with zero dangling references, full tests/gates passing, and identical export or build output.
8. Documentation sync is part of the definition of done: the repository tree, export tree, architecture declarations, and command list must match disk; structure is trustworthy only when `--check` exits 0.

Stop conditions:

- Audit-only is an explicit opt-out, not the default. Only when the user explicitly asks for audit-only, output the report and touch nothing on disk.
- Init preflight failure: dirty tree, bound branch, or protected branch. Stop and report the blocker. Do not stash.
- Live runtime-surface migration: when the change touches the runtime export surface (SKILL.md, COPY_DIRS, the script whitelist), write the target tree, migration table, and reference graph before acting.
- Structure governance is a rare task: do not reshuffle for tidiness; when the verdict is "keep", state the zero-migration reason explicitly.

Constraints:

- The fact script produces facts only, never auto-dispositions: it moves, deletes, and rewrites nothing. `build_archive_plan` only describes archive moves and never returns a delete or unlink action.
- Historical references are not live references: `.spec/` governance records and CHANGELOG mentions are not evidence of active consumption.
- Suspects default to keep-and-document. Never delete.

Output contract:

- Follow the `/spec:organize` section in `references/output-contracts.md`.

## `/spec:goal`

Goal: take one explicit goal sentence and run the one-shot chain: self-plan and create or resume the task package, self-run the task queue with self-verification, then have `/spec:done` archive and commit after the gates pass, and finish with `push` once the safety preflight passes.

Compatible invocation:

- Claude Code CLI: `/spec:goal <goal>`
- Codex / generic skill-aware CLI: command alias `spec:goal`, triggered by `$spec`

Execution order:

1. Run `python3 scripts/route_spec_package.py --root <project>` first; with a clear user goal and no resumable package, create the `YYYY-MM-DD_<verb>-<object>` task package and its `spec/YYYY-MM-DD_<slug>` integration branch per `/spec:new`.
2. Self-plan: derive the problem definition, facts, assumptions, open questions, in/out scope, minimal path, task queue, acceptance criteria, issue dispositions, and Git boundaries from the goal; write down whatever can be reasonably assumed instead of blocking on a question list. Independent gaps discovered along the way may open new Development Records, but the current chain must continue to archive.
3. Self-run: execute tasks in dependency order inside this session per `/spec:run`, running each `verify` before checking off.
4. Self-supervise: after every change round, check for boundary violations, unrequested abstractions, and needed syncs to `SKILL.md`, `references/*`, `README*`, `agents/openai.yaml`, script contracts, the Module DAG, or task-package documents.
5. Self-verify: after the tasks, run the necessary builds, tests, smoke, script gates, and manual-equivalent checks; on failure, fix and rerun — never skip.
6. Self-review: run `check_spec_package.py` per `/spec:check`, write executable gaps back and fix them in this round; a gap with independent scope opens a follow-up Development Record that must run through the current `goal` chain — its run/check/done/archive — before the original package closes with `resolved_followup`. Never just create the package or hand the slug over as user homework. Continue until `checklist.md` is fully checked with `**验收结果**：通过`.
7. Self-archive and commit: per `/spec:done`, run `complete_spec_package.py --archive`, generate the knowledge distillation (into the executing project's own `.spec/docs/`, per `/spec:done`) and the completion summary, then `git add` this package's files and run `git commit` as a single direct command; the message uses Conventional Commits with the `Spec: <slug>` footer. The `done` stage never pushes, merges, or deletes branches. Never modify files and commit in the same Bash call; never `--no-verify`.
8. Self-push/finish: after the commit, run `python3 scripts/push_spec_package.py --root <git-repo> --branch <working-branch> --main-branch main --remote origin --slug <slug>`; the push script publishes the working branch, merges main, pushes main, and deletes the merged branch.

Stop conditions:

- When the goal still cannot pin down an MVP, the target repository, or a project-shaping constraint, ask one focused question first.
- When the work tree holds unrelated dirty changes that cannot be safely triaged, stop and output triage advice.
- When any verification, check, Git hook, commit, or push safety preflight fails, stop at the failure point and output the failed command, error summary, completed work, and the next repair action.
- Never force a push or branch deletion when the remote is missing, permission is denied, the target branch is protected, the lease mismatches, or the working branch is undeletable.

Constraints:

- `goal` creates no second state machine; every stage takes the task-package disk content and the existing scripts as truth.
- No skipping the gates of `/spec:new`, `/spec:run`, `/spec:check`, `/spec:done`, Git hooks, or `/spec:push`.
- Never replace the current-tree check with old output or verbal judgment.
- The automatic commit includes only changes covered by the current task's `boundary`; unrelated changes must be split into atomic commits or a new task package.
- Actionable issues found must be written back and completed this round; executable fixes are never handed to the user as homework. Independent scope may open a follow-up spec, but it must be executed, accepted, and archived inside this chain before the original package closes. Closures use the five structured dispositions; free-form `nextTasks` / `unresolvedRisks` are not allowed. Only three things may interrupt the user: a project-shaping constraint, a decision that cannot be reasonably assumed, and an external dependency the agent cannot execute.

Output contract:

- Render the `/spec:goal` dashboard per the dashboard contract in `references/output-contracts.md`.

## Script working-directory conventions

Task-package script examples run from the skill package root by default; to operate on another project, pass `--root <project>` to the scripts that support it, or replace `scripts/` with the absolute path of the installed/exported skill. `--specs-dir <dir>` must be a trusted relative directory under root — never empty, absolute, or containing `..` — and its real resolution must stay under root. `push_spec_package.py` is the Git handoff script; it also runs from the skill package root with `--root <git-repo>` pointing at the target repository.

Output channel contract: every script prints its contract output — the rendered dashboard, the `--format json` payload, generated summaries — to stdout, and every diagnostic (argument errors, missing files, branch/environment failures, stack traces) to stderr. stdout must stay parseable: gates, hooks, and machine consumers read it, so debug prints and warnings belong on stderr (`print(..., file=sys.stderr)`), never interleaved with contract output. New scripts follow this split by construction; `/spec:doctor` is not yet in the periodic spot-check surface for this contract (pending separate evaluation).

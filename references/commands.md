# Commands

This page is the stage index: shared conventions plus one link per stage. Each stage's full contract lives in its own file under [`stages/`](stages/) — read the one stage file that matches the current project state, then run the command it names. The default line is `new / run / check / done / push`; `update` and `status` adjust or inspect an existing package; `goal` is the one-shot entry point; `autoplan` interrogates the user in rounds and produces the project planning-document cluster; `autorun` chains rounds recursively from that cluster; `doctor` and `organize` maintain the environment or structure outside the task-package state machine.

The shortest safe path is always: route from disk, make one bounded change, run its verification, then let the next stage consume the recorded state. Do not read every stage file when one is enough.

## CLI compatibility layer

The command inventory (command → stage → purpose) lives once in `SKILL.md`. Claude Code exposes `/spec:<command>`; Codex / generic skill-aware CLIs (Gemini CLI, Grok Build, OpenCode, OpenClaw, Hermes, Pi, etc.) trigger `$spec` and then use `spec:new/goal/autoplan/run/check/done/push/update/status/doctor/organize/autorun`. The route and tasks views remain internal script capabilities, not user commands. See the README installation section for the per-host install directory matrix.

Install profile boundary: `new / run / check / done / push` are the kernel stage commands every install ships. The extended stages (`goal`, `autoplan`, `autorun`, `update`, `status`, `doctor`, `organize`) belong to `INSTALL_PROFILE=full`. On a core install (the default), an extended-stage invocation is answered with the install hint — reinstall with `INSTALL_PROFILE=full bash install.sh` — not executed as if the command were present.

Before executing any command, pass the four questions from `engineering-philosophy.md`: are the assumptions written down, is this the minimal path, is the boundary clear, and how is completion proven. If any answer is missing, fix the documents first.

## `/spec`

Goal: act as the default user entry point; run route internally, then continue with the most fitting stage.

Execution order:

1. Run `python3 scripts/route_spec_package.py --root <project>` first to determine the task-package state. When the package carries a `handoff.md`, the route output includes a 交接 line (`handoff：最新 <ts>（freshness，collab）`); a resuming session reads it with `python3 scripts/spec_handoff.py show --slug <slug>` before continuing (contract: [handoff.md](handoff.md)).
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
- Obvious small tasks (a one-line reversible fix with its test, a typo, a copy tweak) are advised not to open a package: say so and offer the lightweight path — direct fix + test, then push with a `Spec: none (<非空理由>)` footer — instead of running `/spec:new` for ceremony's sake. The user decides; when they want the record anyway, `new` runs as usual.
- User-visible output covers actual project progress, current work, concrete problems, and delivered results; internal routing commands are not offered as the user's next step.
- If routing depends on a key assumption, state the assumption first or ask the minimal clarifying question.

## `/spec:new`

Create a new task package and compress a complex task into an executable specification — full contract: [`stages/new.md`](stages/new.md).

## `/spec:goal`

Take one explicit goal sentence and run the one-shot chain `new` → `run` → `check` → `done` inside this session, skipping no gate; the default stop is the local commit and `push` runs only on explicit authorization (`--push` or an explicit user instruction) — full contract: [`stages/goal.md`](stages/goal.md).

## `/spec:autoplan`

Interactive recursive planning: interrogate the user in themed rounds, then run framework/detail/review passes as a serial fresh-session chain until the cluster reaches autorun granularity — full contract: [`stages/autoplan.md`](stages/autoplan.md).

## `/spec:autorun`

Recursive autonomous chain: finish the active package goal-style, plan the next round from the planning documents, spawn a fresh interactive session, repeat until every planned feature is done — full contract: [`stages/autorun.md`](stages/autorun.md).

## `/spec:run`

The main session completes the whole task package on its own: execute tasks in dependency order, verify each item, check it off; resumes from package state if interrupted — full contract: [`stages/run.md`](stages/run.md).

## `/spec:check`

Human-AI review round: inspect implementation, evidence, and gaps, write actionable issues back into the package, and fix them in this round — full contract: [`stages/check.md`](stages/check.md).

## `/spec:done`

Close the task, distill knowledge, archive the task package, and create this round's Git commit; no merge, push, or branch deletion in this stage — full contract: [`stages/done.md`](stages/done.md).

## `/spec:push`

After `done` and the working-branch commit, safely merge into the main branch, push main, and delete the confirmed-merged working branch — full contract: [`stages/push.md`](stages/push.md).

## `/spec:update`

Add, remove, or adjust tasks and scope mid-flight, wrapped in a Git WIP recovery point (rollback via `git reset --hard` / `git revert`) — full contract: [`stages/update.md`](stages/update.md).

## `/spec:status`

Overview across task packages: per-package progress, blockers, and next steps — full contract: [`stages/status.md`](stages/status.md).

## `/spec:doctor`

Environment self-check and repair: inspect every foundation and installation surface for "installed but broken" gaps; `--fix` repairs the safe items — full contract: [`stages/doctor.md`](stages/doctor.md).

## `/spec:organize`

First-principles structure audit, audit-only by default (report and stop, next step stated at the end); applying via `--apply` or an explicit user instruction creates the target project's package and fixes every confirmed finding class; archive retired files, never delete — full contract: [`stages/organize.md`](stages/organize.md).

## Session backends

Both spawn scripts (`autorun_spawn.py`, `autoplan_spawn.py`) accept `--terminal auto|terminal|tmux` (default `auto`): macOS selects Terminal.app, Linux/WSL2 selects tmux (tmux ≥3.2 + ps; README installation section); `--terminal terminal` switches back on macOS only. The worker always runs as a full interactive session — visible TUI, session persisted, never print/exec headless. Native Windows is not supported directly — spawn fails with WSL2 guidance; start the whole chain inside WSL2. Failed handoffs (missing tmux, launch timeout, instantly dead worker) refuse without advancing the chain state and leave no process behind (交接未确认 = 不留进程); window/pane lifecycle choreography and policy history: source repository `docs/adr/0001-terminal-spawn-window-lifecycle.md`. `--dry-run` prints `launch_argv` and attach instructions only.

## Script working-directory conventions

Task-package script examples run from the skill package root by default; to operate on another project, pass `--root <project>` to the scripts that support it, or replace `scripts/` with the absolute path of the installed/exported skill. `--specs-dir <dir>` must be a trusted relative directory under root — never empty, absolute, or containing `..` — and its real resolution must stay under root. `push_spec_package.py` is the Git handoff script; it also runs from the skill package root with `--root <git-repo>` pointing at the target repository.

Output channel contract: every script prints its contract output — the rendered dashboard, the `--format json` payload, generated summaries — to stdout, and every diagnostic (argument errors, missing files, branch/environment failures, stack traces) to stderr. stdout must stay parseable: gates, hooks, and machine consumers read it, so debug prints and warnings belong on stderr (`print(..., file=sys.stderr)`), never interleaved with contract output. New scripts follow this split by construction; `/spec:doctor` covers this contract via the periodic `skill.channels` probe spot-check.

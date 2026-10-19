# Commands

This page is the stage index: shared conventions plus one link per stage. Each stage's full contract lives in its own file under [`stages/`](stages/) — read the one stage file that matches the current project state, then run the command it names. The default line is `new / run / check / done / push`; `update` / `status` adjust or inspect an existing package; `goal` is one-shot; `autoplan` produces the planning cluster; `autorun` chains rounds from it; `doctor` / `organize` maintain environment or structure outside the task-package state machine.

Shortest safe path: route from disk, make one bounded change, run its verification, let the next stage consume the recorded state. Do not read every stage file when one is enough.

## CLI compatibility layer

The command inventory (command → stage → purpose) lives once in `SKILL.md`. Claude Code exposes `/spec:<command>`; Codex / generic skill-aware CLIs trigger `$spec` and then use `spec:<command>`. The route and tasks views remain internal script capabilities, not user commands. See the README installation section for the per-host install directory matrix.

Install profile boundary: `new / run / check / done / push` are the kernel stage commands every install ships. The extended stages (`goal`, `autoplan`, `autorun`, `update`, `status`, `doctor`, `organize`) belong to `INSTALL_PROFILE=full`; a core install (the default) answers an extended-stage invocation with the reinstall hint — `INSTALL_PROFILE=full bash install.sh` — instead of executing it.

Before executing any command, run the four-question Quick self-check in [`engineering-philosophy.md`](engineering-philosophy.md); a missing answer means fix the documents first.

## `/spec`

Goal: act as the default user entry point; run route internally, then continue with the most fitting stage.

Execution order:

1. Run `python3 scripts/route_spec_package.py --root <project>` first; when the package carries a `handoff.md`, the route output includes a 交接 line, and a resuming session reads it with `python3 scripts/spec_handoff.py show --slug <slug>` before continuing (contract: [handoff.md](handoff.md)).
2. No active package → `new`; if the user also asks for `push` and the work tree / unpushed commits carry discrete changes, enter the push retro-pack path (atomic packing by theme, or one whole new spec package) — never skip packing and push directly.
3. Active package that can continue → `run`; review request → `check`; archive/close-out request → `done`.
4. Full goal with the one-shot chain → `goal` (still new/run/check/done/commit/push order); merge-push-and-delete after done → the standalone `push` stage internally, reporting only the publication result or external blocker.
5. Scope or task adjustment → `update`; progress overview only → `status`.

Constraints:

- Route is never presented as a user-facing stage.
- Obvious small tasks take the lightweight path from `SKILL.md` (direct fix + test, push with a `Spec: none` footer) instead of `/spec:new` ceremony; the user decides, and `new` runs as usual when they want the record.
- User-visible output covers actual project progress, current work, concrete problems, and delivered results; internal routing commands are not offered as the user's next step ([output-contracts.md](output-contracts.md)).
- If routing depends on a key assumption, state the assumption first or ask the minimal clarifying question.

## `/spec:new`

Create a task package: scope, tasks, and acceptance criteria from a clarified goal — full contract: [`stages/new.md`](stages/new.md).

## `/spec:goal`

One-shot chain `new` → `run` → `check` → `done`, push only on explicit authorization — full contract: [`stages/goal.md`](stages/goal.md).

## `/spec:autoplan`

Interactive planning rounds, then framework/detail/review passes as a serial fresh-session chain until autorun granularity — full contract: [`stages/autoplan.md`](stages/autoplan.md).

## `/spec:autorun`

Recursive chain: finish the active package, plan the next round, spawn a fresh session, repeat until every planned feature is done — full contract: [`stages/autorun.md`](stages/autorun.md).

## `/spec:run`

Execute all tasks in dependency order, verify each, check it off; resumes from package state — full contract: [`stages/run.md`](stages/run.md).

## `/spec:check`

Review round: write actionable issues back into the package and fix them in-round — full contract: [`stages/check.md`](stages/check.md).

## `/spec:done`

Archive the package and create this round's commit; no merge, push, or branch deletion — full contract: [`stages/done.md`](stages/done.md).

## `/spec:push`

Merge into main, push, delete the confirmed-merged branch — full contract: [`stages/push.md`](stages/push.md).

## `/spec:update`

Add, remove, or adjust tasks and scope mid-flight, wrapped in a Git WIP recovery point — full contract: [`stages/update.md`](stages/update.md).

## `/spec:status`

Overview across packages: progress, blockers, next steps — full contract: [`stages/status.md`](stages/status.md).

## `/spec:doctor`

Environment self-check and repair (`--fix` repairs the safe items) — full contract: [`stages/doctor.md`](stages/doctor.md).

## `/spec:organize`

Structure audit, audit-only by default; `--apply` creates the package and fixes confirmed classes; never delete — full contract: [`stages/organize.md`](stages/organize.md).

## Session backends

Both spawn scripts (`autorun_spawn.py`, `autoplan_spawn.py`) accept `--terminal auto|terminal|tmux` (default `auto`): macOS selects Terminal.app, Linux/WSL2 selects tmux (tmux ≥3.2 + ps); `--terminal terminal` switches back on macOS only. The worker always runs as a full interactive session — visible TUI, session persisted, never headless. Native Windows is unsupported — spawn fails with WSL2 guidance. Failed handoffs (missing tmux, launch timeout, instantly dead worker) refuse without advancing chain state and leave no process behind (交接未确认 = 不留进程); window/pane lifecycle policy: source `docs/adr/0001-terminal-spawn-window-lifecycle.md`. `--dry-run` prints `launch_argv` and attach instructions only.

Completed chains are closed explicitly with `python3 scripts/autoplan_spawn.py close --root <project>` (or the `autorun_spawn.py` equivalent; `--dry-run` validates only) — an internal maintenance operation, not a new `/spec:*` command. Close verifies every recorded plan document and its frozen feature set, refuses active packages or a held chain mutex, preserves the old summary and completion proof under `.spec/<chain>/history/`, and writes an audit-backed closed marker; it never kills sessions or closes windows (the mutex protects script transactions, not worker lifetime). Missing/changed plans or ambiguous state fail closed; no force/reset. A new chain starts at 1 without inheriting the old model lock. Status uses the recorded scope; proven completion needs no extra round even at the cap. Legacy states without a fingerprint remain readable but cannot be closed automatically.

## Script working-directory conventions

Task-package script examples run from the skill package root by default; to operate on another project, pass `--root <project>` to the scripts that support it, or replace `scripts/` with the absolute path of the installed/exported skill. `--specs-dir <dir>` must be a trusted relative directory under root — never empty, absolute, or containing `..` — and its real resolution must stay under root. `push_spec_package.py` is the Git handoff script; it also runs from the skill package root with `--root <git-repo>` pointing at the target repository.

Output channel contract: every script prints its contract output — the rendered dashboard, the `--format json` payload, generated summaries — to stdout, and every diagnostic (argument errors, missing files, branch/environment failures, stack traces) to stderr. stdout must stay parseable: gates, hooks, and machine consumers read it, so debug prints and warnings belong on stderr (`print(..., file=sys.stderr)`), never interleaved with contract output. New scripts follow this split by construction; `/spec:doctor` covers this contract via the periodic `skill.channels` probe spot-check.

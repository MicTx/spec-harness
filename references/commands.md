# Commands

Stage index. Read the one stage page that matches the package state, run the command it names, and let the next stage read the result from disk. Scripts run from the skill root; pass `--root <project>` to target a project. Contract output goes to stdout, diagnostics to stderr.

## Host entry points

Claude Code exposes `/spec:<stage>`; Codex and ZCode use `$spec`; Devin and Cursor load `/spec` and then `spec:<stage>`; other hosts use their native skill entry. The default install (`core`) ships `new / run / check / done / push / doctor`; the other stages need `INSTALL_PROFILE=full bash install.sh`, and a core install answers them with that hint.

## `/spec`

Resume from disk (`tasks.md`, `handoff.md`, `git log`), then `python3 scripts/route_spec_package.py --root <project>`. No active package → `new`; a continuable package → `run`; review → `check`; close-out → `done`; scope change → `update`. Small reversible work takes the lightweight path in `SKILL.md`. Push only with explicit user authorization.

## `/spec:new`

Create a package: [`stages/new.md`](stages/new.md).

## `/spec:run`

Execute tasks in dependency order: [`stages/run.md`](stages/run.md).

## `/spec:check`

Run evidence and the gate, write failures back: [`stages/check.md`](stages/check.md).

## `/spec:done`

Archive and commit, nothing more: [`stages/done.md`](stages/done.md).

## `/spec:push`

Merge, push and delete the merged branch, only when authorized: [`stages/push.md`](stages/push.md).

## `/spec:update`

Change scope or tasks mid-flight: [`stages/update.md`](stages/update.md).

## `/spec:status`

Overview of packages and blockers: [`stages/status.md`](stages/status.md).

## `/spec:goal`

One-shot new → run → check → done, stopping at the local commit: [`stages/goal.md`](stages/goal.md).

## `/spec:autoplan`

Plan a feature list to autorun readiness: [`stages/autoplan.md`](stages/autoplan.md).

## `/spec:autorun`

Work through the plan one package per round: [`stages/autorun.md`](stages/autorun.md).

## `/spec:doctor`

Environment self-check, `--fix` for safe repairs: [`stages/doctor.md`](stages/doctor.md).

## `/spec:organize`

Structure audit, read-only unless `--apply`: [`stages/organize.md`](stages/organize.md).

## Session backends

Autoplan and autorun run in the current session and record progress with `scripts/chain_state.py`. To continue in a new session, open it yourself and resume from disk.

# Commands

Stage index. Read the one stage page that matches the package state, run the command it names, and let the next stage read the result from disk. Scripts run from the skill root; pass `--root <project>` to target a project. Contract output goes to stdout, diagnostics to stderr.

## Host entry points

Claude Code exposes `/spec:<stage>`; Codex and ZCode use `$spec`; Devin and Cursor load `/spec` and then `spec:<stage>`; other hosts use their native skill entry. Every host receives the same six-stage kernel: `new / run / check / done / push / doctor`.

## `/spec`

Resume from disk (`tasks.md`, `handoff.md`, `git log`), then `python3 scripts/route_spec_package.py --root <project>`. No active package → `new`; a continuable package → `run`; review → `check`; close-out → `done`. Small reversible work takes the lightweight path in `SKILL.md`. Push only with explicit user authorization.

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

## `/spec:doctor`

Environment self-check, `--fix` for safe repairs: [`stages/doctor.md`](stages/doctor.md).

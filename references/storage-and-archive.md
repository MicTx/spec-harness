# Storage And Archive

This page explains where a fact belongs after a task starts. The active package describes the current work, the archive preserves the completed record, `.spec/docs/` stores reusable project knowledge, and `.spec/architecture/` describes the current system. Keeping those roles separate makes a paused task recoverable without turning the repository into one undifferentiated diary.

## Standard layout

```text
.spec/
├── specs/
│   ├── YYYY-MM-DD_slug/          # active package (spec.md + tasks.md)
│   │   ├── spec.md
│   │   ├── tasks.md
│   │   ├── checklist.md          # optional: legacy v1 packages only
│   │   ├── handoff.md            # optional session-boundary handoff log
│   │   └── update-checkpoint.json # optional: historical artifact of the retired checkpoint mechanism
│   └── archive/
│       └── YYYY-MM-DD_slug/      # archived record (+ completion-summary.md)
│           ├── spec.md
│           ├── tasks.md
│           ├── checklist.md      # optional: legacy v1 archives
│           └── completion-summary.md
├── docs/
│   └── YYYY-MM-DD_slug_topic.md
├── artifacts/
└── architecture/
    ├── module-index.md
    ├── module-dag.md
    └── module-dag.mmd
```

- Active packages live only at the `.spec/specs/` root; completed packages move wholesale into `archive/`.
- A new task package has `spec.md` and `tasks.md`; an archive additionally has `completion-summary.md`. A legacy v1 package also keeps its `checklist.md`.
- The optional `handoff.md` member is the session-boundary handoff document (`references/handoff.md`): generated snapshot head plus append-only entry log, validated by the check gate when present, closed with a terminal entry at `done`, archived wholesale with the package.
- A leftover `update-checkpoint.json` comes from the retired update mechanism; it is advisory (route, status and doctor surface it), never a gate.
- `.spec/architecture/` is the current module graph and belongs to no single package.
- `--specs-dir` must be a trusted relative directory under root — never empty, absolute, or containing `..`.

## Development Record

One development loop, directory named `YYYY-MM-DD_slug`. The date lives in the slug; no separate `dev-log.md`.

- New packages use `YYYY-MM-DD_<verb>-<object>`; verbs live in `naming-and-commits.md`.
- `check` only advises on non-verb-object names (underlying slug validity stays backward compatible); historical packages keep their names, and archives are not renamed.

## Knowledge / architecture

- Knowledge documents: `.spec/docs/YYYY-MM-DD_slug_topic.md`, always inside the executing project's own `.spec/` tree — never a global carrier (`~/.claude/CLAUDE.md`, user-level `AGENTS.md`, or user-level memory). No executable tasks stored there; only project-specific engineering facts qualify, and spec process mechanics are never distilled.
- Module DAG: nodes are modules, not tasks; the graph must be acyclic; task packages reference it.
- Historical Development Records, knowledge, architecture, archives, and maintainer notes stay by default; cleanup/merge dispositions go through the task-package gates, never around them.

## Archiving

- `--archive` re-runs the package gates in-process before anything moves; a failing gate aborts the archive.
- Archiving appends the terminal handoff entry, writes `completion-summary.md`, then moves the package into `specs/archive/` with `git mv`; a failed move keeps the active package and the generated summary (`--force` regenerates it).
- A new archive carries the `## Issue Disposition` JSON. The legacy `## 问题处置` heading remains accepted for compatibility; frozen archives are never regraded.
- `done` produces the archive and finishes this round's commit; merging/pushing/branch deletion is left to `push`.

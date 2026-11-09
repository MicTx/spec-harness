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
│   │   ├── update-checkpoint.json # optional: historical artifact of the retired checkpoint mechanism; new updates never produce one
│   │   └── orchestration/        # optional orchestration assets: script + run ledger
│   └── archive/
│       └── YYYY-MM-DD_slug/      # archived record (+ completion-summary.md)
│           ├── spec.md
│           ├── tasks.md
│           ├── checklist.md      # optional: legacy v1 archives
│           └── completion-summary.md
├── docs/
│   └── YYYY-MM-DD_slug_topic.md
├── plans/                        # optional project planning documents
│   └── archive/                  # completed or retired planning units
├── archive/
│   └── retired/                  # structure-audit retirement, dated + manifest
├── autoplan/
│   └── chain.json               # tracked pass-chain summary (pass, kind, target doc; volatile-free)
├── autorun/
│   └── chain.json               # tracked chain summary (round, host, plan docs; volatile-free)
├── artifacts/
└── architecture/
    ├── module-index.md
    ├── module-dag.md
    └── module-dag.mmd
```

Chain state is one tracked JSON file per chain, `.spec/<chain>/chain.json` for `autorun` and `autoplan`, written by `scripts/chain_state.py`; it never archives with a package.

- Active packages live only at the `.spec/specs/` root; completed packages move wholesale into `archive/`.
- A new task package has `spec.md` and `tasks.md`; an archive additionally has `completion-summary.md`. A legacy v1 package also keeps its `checklist.md`.
- The optional `handoff.md` member is the session-boundary handoff document (`references/handoff.md`): generated snapshot head plus append-only entry log, validated by the check gate when present, closed with a terminal entry at `done`, archived wholesale with the package.
- An active package may additionally carry `orchestration/` (workflow script + run ledger, written by the main session); it archives wholesale with the package.
- A leftover `update-checkpoint.json` comes from the retired update mechanism; it is advisory (route, status and doctor surface it), never a gate.
- `.spec/autoplan/` and `.spec/autorun/` hold `chain.json` (written by `scripts/chain_state.py`) and a `history/` of closed chains; they never archive with a package.
- The planning-document tree (`.spec/plans/` root: index `README.md`, single-round `<YYYY-MM-DD>_<verb>-<object>.md` plans, multi-round `<slug>/` clusters holding `master.md` plus `NN-<slug>.md` phase details; delivered or retired units move to `.spec/plans/archive/` under the same name) is project content produced or reconciled by `/spec:autoplan` (`scripts/autoplan_gate.py`, chain state via `scripts/chain_state.py`): it feeds the autorun chain's feature checkboxes and detail designs, is committed like normal project files, and is never task-package state — `autoplan` writes no `.spec/specs/` package and no `.spec/autorun/` chain state.
- `.spec/architecture/` is the current module graph and belongs to no single package.
- `--specs-dir` must be a trusted relative directory under root — never empty, absolute, or containing `..`.

## Development Record

One development loop, directory named `YYYY-MM-DD_slug`. The date lives in the slug; no separate `dev-log.md`.

- New packages use `YYYY-MM-DD_<verb>-<object>`; verbs live in `naming-and-commits.md`.
- `check` only advises on non-verb-object names (underlying slug validity stays backward compatible); historical packages keep their names, and archives are not renamed.

## Knowledge / architecture

- Knowledge documents: `.spec/docs/YYYY-MM-DD_slug_topic.md`, always inside the executing project's own `.spec/` tree — never a global carrier (`~/.claude/CLAUDE.md`, user-level `AGENTS.md`, or user-level memory). No executable tasks stored there; only project-specific engineering facts qualify, and spec process mechanics are never distilled.
- Module DAG: nodes are modules, not tasks; the graph must be acyclic; task packages reference it.
- Structure governance is a rare task: prove the structure unreasonable first, then write the target tree, migration table, and reference map; never reshuffle for tidiness. Historical Development Records, knowledge, architecture, archives, and maintainer notes stay by default. A deprecation verdict needs an evidence chain (retired mechanism, zero live references, superseded by a successor asset) recorded in a conclusive knowledge document; cleanup/merge dispositions go through the task-package gates, never around them.

## Archiving

- `--archive` re-runs the package gates in-process before anything moves; a failing gate aborts the archive.
- Archiving appends the terminal handoff entry, writes `completion-summary.md`, then moves the package into `specs/archive/` with `git mv`; a failed move keeps the active package and the generated summary (`--force` regenerates it).
- A new archive carries the `## Issue Disposition` JSON. The legacy `## 问题处置` heading remains accepted for compatibility; frozen archives are never regraded.
- `done` produces the archive and finishes this round's commit; merging/pushing/branch deletion is left to `push`.

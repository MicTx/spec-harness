# Storage And Archive

This page explains where a fact belongs after a task starts. The rule is simple: the active trio describes the current work, the archive preserves the completed record, `.spec/docs/` stores reusable project knowledge, and `.spec/architecture/` describes the current system. Keeping those roles separate makes a paused task recoverable without turning the repository into one undifferentiated diary.

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

Per-machine chain runtime state lives outside the tracked tree, under `.git/spec-chain/<chain>/` for each chain (`autorun`, `autoplan`): `volatile.json` (the spawn-moment facts — `prev_tty`, `window_recycle`), `chain.lock`, `spawns.jsonl`, `events.jsonl`. It is untracked by construction, machine-local, and never archived or plan-synced; a fresh clone starts with an empty runtime tree and the first status/spawn migrates any legacy files found beside the summary.

- Active packages live only at the `.spec/specs/` root; completed packages move wholesale into `archive/`.
- A task package has at least the trio; an archive must additionally have `completion-summary.md`.
- The optional `handoff.md` member is the session-boundary handoff document (`references/handoff.md`): generated snapshot head plus append-only entry log, validated by the check gate when present, closed with a terminal entry at `done`, archived wholesale with the package.
- An active package may additionally carry `orchestration/` (workflow script + run ledger, written by the main session); it archives wholesale with the package.
- A package may carry a historical `update-checkpoint.json` from the retired checkpoint mechanism: read-only recognized by `scripts/update_checkpoint.py status`, resolved by Git-history recovery plus file deletion (`references/stages/update.md`); new updates never produce one (rollback safety is the Git WIP commit), and the file archives as history.
- `.spec/autoplan/` and `.spec/autorun/` hold the tracked chain summaries (`chain.json` only, volatile-free, never archived) for their spawn scripts; the per-machine runtime (volatile state, `spawns.jsonl` audit, `events.jsonl`, `chain.lock`) lives under `.git/spec-chain/<chain>/` and never archives with a package. Each autorun-planned package carries a final plan-sync task whose boundary covers the planning documents and this summary, so checkmarks and the chain summary land on `main` with the round's push and the tree is clean when the next round's `/spec:new` runs — the round's tty/window/pid details stay machine-local and out of every commit.
- The planning-document tree (`.spec/plans/` root: index `README.md`, single-round `<YYYY-MM-DD>_<verb>-<object>.md` plans, multi-round `<slug>/` clusters holding `master.md` plus `NN-<slug>.md` phase details; delivered or retired units move to `.spec/plans/archive/` under the same name) is project content produced or reconciled by `/spec:autoplan` (`scripts/autoplan_gate.py`, pass chain via `scripts/autoplan_spawn.py`): it feeds the autorun chain's feature checkboxes and detail designs, is committed like normal project files, and is never task-package state — `autoplan` writes no `.spec/specs/` package and no `.spec/autorun/` chain state.
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

- `--archive` is allowed only when `check_spec_package.py` currently exits 0, and never combines with `--allow-incomplete`.
- Archiving is atomic: quarantine → re-verify the snapshot → write the summary → switch; never write the summary into the source package first. Symlink/FIFO/oversize/concurrent-change conditions fail and restore the active package.
- A new archive carries the v1 `## 问题处置` (issue dispositions) JSON; only legacy archives byte-identical to the authoritative baseline quartet stay read-only compatible.
- `done` produces the archive and finishes this round's commit; merging/pushing/branch deletion is left to `push`.

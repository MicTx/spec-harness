# Storage And Archive

This page explains where a fact belongs after a task starts. The rule is simple: the active trio describes the current work, the archive preserves the completed record, `.spec/docs/` stores reusable project knowledge, and `.spec/architecture/` describes the current system. Keeping those roles separate makes a paused task recoverable without turning the repository into one undifferentiated diary.

## Standard layout

```text
.spec/
├── specs/
│   ├── YYYY-MM-DD_slug/          # active trio
│   │   ├── spec.md
│   │   ├── tasks.md
│   │   ├── checklist.md
│   │   ├── handoff.md            # optional session-boundary handoff log
│   │   └── orchestration/        # optional orchestration assets: script + run ledger
│   └── archive/
│       └── YYYY-MM-DD_slug/      # archived quartet
│           ├── spec.md
│           ├── tasks.md
│           ├── checklist.md
│           └── completion-summary.md
├── docs/
│   └── YYYY-MM-DD_slug_topic.md
├── autoplan/
│   ├── chain.json               # latest autoplan pass-chain state (pass, kind, target doc)
│   ├── spawns.jsonl             # append-only pass-spawn audit log
│   └── chain.lock               # transient single-chain lock (flock)
├── autorun/
│   ├── chain.json               # latest autorun chain state (round, host, plan docs)
│   ├── spawns.jsonl             # append-only spawn audit log
│   └── chain.lock               # transient single-chain lock (flock)
├── artifacts/
└── architecture/
    ├── module-index.md
    ├── module-dag.md
    └── module-dag.mmd
```

- Active packages live only at the `.spec/specs/` root; completed packages move wholesale into `archive/`.
- A task package has at least the trio; an archive must additionally have `completion-summary.md`.
- The optional `handoff.md` member is the session-boundary handoff document (`references/handoff.md`): a generated snapshot head plus an append-only entry log, validated by the check gate when present, closed with a terminal entry at `done`, and archived wholesale with the package.
- An active package may additionally carry `orchestration/` — the workflow script and the run
  ledger (`runs.md`) — written by the main session; it archives wholesale with the package.
- `.spec/docs/` holds reusable knowledge only, not task streams; it lives inside the executing project's repository and never mirrors into a global carrier.
- `.spec/autoplan/` is runtime pass-chain state for `/spec:autoplan` (`scripts/autoplan_spawn.py`): the latest state in `chain.json`, an append-only audit in `spawns.jsonl`, and the transient `chain.lock`. Like `.spec/autorun/` it is never archived with a package.
- `.spec/autorun/` is runtime chain state for `/spec:autorun` (`scripts/autorun_spawn.py`): the latest state in `chain.json`, an append-only audit in `spawns.jsonl`, and the transient `chain.lock`. It is never archived; each autorun-planned package carries a final plan-sync task whose boundary covers the planning documents and this directory, so checkmarks and audit land on `main` with the round's push and the tree is clean when the next round's `/spec:new` runs.
- The planning-document tree (the `.spec/plans/` root: index `.spec/plans/README.md`, single-round `<YYYY-MM-DD>_<verb>-<object>.md` plans, multi-round `<slug>/` clusters holding `master.md` plus `NN-<slug>.md` phase details; delivered or retired units move to `.spec/plans/archive/` under the same name) is project content produced or reconciled by `/spec:autoplan` (`scripts/autoplan_gate.py`, pass chain via `scripts/autoplan_spawn.py`): it feeds the autorun chain's feature checkboxes and detail designs, is committed like normal project files, and is never task-package state — `autoplan` writes no `.spec/specs/` package and no `.spec/autorun/` chain state.
- `.spec/architecture/` is the current module graph and belongs to no single package.
- `--specs-dir` must be a trusted relative directory under root — never empty, absolute, or containing `..`.

## Development Record

One development loop, directory named `YYYY-MM-DD_slug`. The date lives in the slug; do not write a separate `dev-log.md`.

- New packages use `YYYY-MM-DD_<verb>-<object>`; verbs live in `naming-and-commits.md`.
- `check` only advises on non-verb-object names; it does not hard-fail. Underlying slug validity stays backward compatible.
- Historical packages keep their names; archives are not renamed for the new standard.

## Knowledge / architecture

- Knowledge documents: `.spec/docs/YYYY-MM-DD_slug_topic.md`, always inside the executing project's own `.spec/` tree — never a global carrier (`~/.claude/CLAUDE.md`, user-level `AGENTS.md`, or user-level memory). No executable tasks stored there; only project-specific engineering facts qualify, and spec process mechanics are never distilled.
- Module DAG: nodes are modules, not tasks; the graph must be acyclic; task packages reference it.
- Structure governance is a rare task: prove the structure unreasonable first, then write the target tree, migration table, and reference map; never reshuffle for tidiness. Historical Development Records, knowledge, architecture, archives, and maintainer notes stay by default. A deprecation verdict needs an evidence chain (retired mechanism, zero live references, superseded by a successor asset) recorded in a conclusive knowledge document; cleanup/merge dispositions go through the task-package gates, never around them.

## Archiving

- `--archive` is allowed only when `check_spec_package.py` currently exits 0.
- `--allow-incomplete` never combines with `--archive`.
- Archiving is atomic: quarantine → re-verify the snapshot → write the summary → switch. Never write the summary into the source package first and then move. Symlink/FIFO/oversize/concurrent-change conditions must fail and restore the active package.
- A new archive must carry the v1 `## 问题处置` (issue dispositions) JSON. Only legacy archives byte-identical to the authoritative baseline quartet stay read-only compatible.
- `done` produces the archive and finishes this round's commit; merging/pushing/branch deletion is left to `push`.

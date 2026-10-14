# `/spec:organize`

Goal: structure tidy-up. Run a first-principles structure audit from machine facts and report; the default run is audit-only — output the report and touch nothing on disk. Applying the fixes (create a new task package in the target project and fix every confirmed finding class) is an explicit opt-in via `--apply` or an explicit user instruction. Never delete; archive retired files, directories, code, and stale docs.

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
2. Default: audit-only — output the report, touch nothing on disk, and close the report with the next step (apply explicitly via `--apply` or by asking to organize and apply). Applying is an explicit opt-in: create a new task package in the target project with `scripts/init_spec_package.py --root <target>` before any move or doc fix, and only then fix. Do not append findings to an unrelated active package. If init fails because the tree is dirty or the branch is already bound, stop. Do not stash and do not write into another package.
3. In the apply path, first-principles judgment: what is this repository; is every candidate single-responsibility and consumed by live references or an explicit role. Every candidate resolves to one of four verdicts: keep / archive / merge / migrate. `archive` never deletes the source. The verdict states its evidence lines (last commit, reference count, supersession).
4. A confirmed finding class is unfinished until every member is dispositioned. Fixing one example and leaving a sibling in the same class is not done.
5. Archive destination is `<specs-dir>/archive/retired/YYYY-MM-DD/<original-relative-path>` plus `MANIFEST.md` in that dated directory. Never delete. Three proofs are still required before a move: not a declared entry point, zero live references or a doc that contradicts a machine fact, and superseded or explicitly stale against current facts. Without all three, keep and document.
6. Land the conclusion in the new package. Historical records, knowledge documents, and archived task packages are kept by default.
7. Hard rule on reference integrity: any move updates every reference in the same change (installer, exporter, CI, hooks, tests, docs); prove it with three artifacts: a re-scanned reference graph with zero dangling references, full tests/gates passing, and identical export or build output.
8. Documentation sync is part of the definition of done: the repository tree, export tree, architecture declarations, and command list must match disk; structure is trustworthy only when `--check` exits 0.

Stop conditions:

- Applying is an explicit opt-in, not the default: only `--apply` on the invocation or an explicit user instruction to fix creates a package and touches disk. The default run outputs the audit report and stops.
- Init preflight failure: dirty tree, bound branch, or protected branch. Stop and report the blocker. Do not stash.
- Live runtime-surface migration: when the change touches the runtime export surface (SKILL.md, COPY_DIRS, the script whitelist), write the target tree, migration table, and reference graph before acting.
- Structure governance is a rare task: do not reshuffle for tidiness; when the verdict is "keep", state the zero-migration reason explicitly.

Constraints:

- The fact script produces facts only, never auto-dispositions: it moves, deletes, and rewrites nothing. `build_archive_plan` only describes archive moves and never returns a delete or unlink action.
- Historical references are not live references: `.spec/` governance records and CHANGELOG mentions are not evidence of active consumption.
- Suspects default to keep-and-document. Never delete.

Output contract:

- Follow the `/spec:organize` section in `references/output-contracts.md`.


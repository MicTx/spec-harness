# `/spec:update`

Goal: add, remove, or adjust tasks and scope during execution.

Compatible invocation: Claude Code `/spec:update`; other skill-aware CLIs trigger `spec:update` via `$spec`.

Execution order:

1. Read the current `spec.md` and `tasks.md`.
2. Determine whether the user wants to add, remove, or reorder tasks; executable gaps found during execution also go through this stage's write-back — no separate user homework lists.
3. State explicitly: new assumptions, overturned assumptions, scope changes, and whether the minimal path changed.
4. When a change touches principles or script contracts, update the source-of-truth file; other carriers get pointers, not copied gate lists.
5. Commit a Git WIP recovery point before the first rewrite of the package documents, then update `tasks.md`.
6. When the change affects scope, acceptance, or technical decisions, sync `spec.md`; when it affects acceptance criteria, sync the acceptance surface; after the last rewrite, review the diff against the WIP commit and continue this round's flow — the recovery point rides in Git history, nothing to clear.

### Delivered-task stability

Adding tasks must not invalidate existing deliverables.

1. **Append a new heading section after the original text**: keep the old task blocks, their trailing `---`, the progress footer, and other tail text verbatim; after all old content open the appended section with a new heading (e.g. `## Appended tasks (this round)`) and write the new tasks there — never delete or reorder old tasks so positional IDs drift; new tasks use unique explicit IDs.
2. **Caller acceptance after the rewrite**: re-parse the package documents and confirm every previously delivered task still exists, stays checked, and has unchanged `boundary`/`verify` text. Deleting or unchecking a delivered task breaks the contract — restore first (`git diff` / `git restore` against the WIP commit), then re-verify.

### Update rollback-safety contract (Git WIP)

In-place rewrites of the package documents must be wrapped in a Git WIP recovery point — never bare edits. The integration branch allows WIP commits (see `stages/push.md`), so the recovery point is ordinary Git history: it covers the whole worktree including Git state, needs no extra state machine, and is the single source of truth for update rollback safety. This replaces the retired `update-checkpoint.json` snapshot mechanism.

1. **WIP commit (before rewriting)**: on the package's integration branch, commit the current state as the recovery point:

   ```bash
   git add -A && git commit -m "Spec-WIP: YYYY-MM-DD_<slug> update checkpoint"
   ```

2. **Rewrite the package documents**: rewrite `tasks.md` per the execution order and sync `spec.md` as impacted.
3. **Rollback (interrupted update)**: restore the whole worktree from the recovery point — `git reset --hard <WIP-commit>` while the branch is unpushed, `git revert <WIP-commit>` when already pushed. Nothing to clear afterwards: the recovery point stays in Git history.
4. **Keep-new confirmation**: when the applied update is the wanted one, simply continue; the WIP commit folds into the branch's history like any other in-flight commit.

Historical `update-checkpoint.json` (retired mechanism, read-only recognition):

- **Route advises, gates agree**: a package holding a historical `update-checkpoint.json` (live or corrupted) gets a prominent advisory from `scripts/route_spec_package.py` (Git history is the recovery point) while routing normally; `scripts/check_spec_package.py` still exits non-zero with Git recovery guidance — a half-applied package never passes review; `scripts/complete_spec_package.py` refuses to archive; `scripts/check_all_spec_packages.py` reports failure. Archived packages keep the file as comparable history; archived read-only review never fails on it.- **Resolution**: compare the current documents against Git history (the recorded snapshot fields in the file help locate the pre-update state), decide keep-new or restore via `git reset --hard` / `git revert`, then delete the package's `update-checkpoint.json` and re-run the refused gate.
- **Recognition CLI** (`scripts/update_checkpoint.py`, read-only; `begin`/`rollback`/`complete` are retired and refuse with the Git WIP guidance above):

```bash
python3 scripts/update_checkpoint.py --root <project> status [--slug YYYY-MM-DD_<slug>]   # 0=none 3=active 4=corrupted
```

Constraints:

- Never change tasks without changing scope; never quietly change acceptance criteria; never expand scope for possible future needs; never write an executable fix as "suggest the user handle it later".

Output contract:

- Render the `/spec:update` dashboard per [`../output-contracts.md`](../output-contracts.md).

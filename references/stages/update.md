# `/spec:update`

Goal: add, remove, or adjust tasks and scope during execution.

Compatible invocation: Claude Code `/spec:update`; other skill-aware CLIs trigger `spec:update` via `$spec`.

Execution order:

1. Read the current `spec.md` and `tasks.md`; identify additions, removals, reordering, or executable gaps requiring write-back.
2. State new or overturned assumptions, scope changes, and whether the minimal path changed. When principles or script contracts change, update the source of truth; other carriers get pointers.
3. Before any rewrite, create the path-scoped recovery copy below. Update `tasks.md`; synchronize `spec.md` and the acceptance surface when affected. Review the changes against the copy.

### Delivered-task stability

Adding tasks must not invalidate existing deliverables. Append a new heading after the original task text, trailing separators, progress footer and other tail content; never delete or reorder old task blocks, and use unique explicit IDs. Re-parse after the rewrite: every delivered task must remain present and checked, with unchanged `boundary`/`verify` text. If not, recover only the affected paths safely and re-verify.

### Update rollback-safety contract

The same rules apply to ordinary and adopted packages (`adoption.json`): neither it nor an ordinary update authorizes staging or committing. Do not change the Git index, make commits, reset a branch, or revert a commit range as part of routine update. Follow the user's Git authorization separately; Git history does not contain uncommitted or untracked pre-update content.

Before rewriting, coordinate exclusive access to each exact file touched, including any source-of-truth outside the package; if impossible, stop. Inspect `GIT_OPTIONAL_LOCKS=0 git status --short`, `GIT_OPTIONAL_LOCKS=0 git diff --literal-pathspecs -- <path>`, and `GIT_OPTIONAL_LOCKS=0 git diff --cached --literal-pathspecs -- <path>`. A target itself may be partially staged. Do not stage or unstage it. Make a private, durable, out-of-tree copy of each planned path's working-tree bytes, mode and existence, including absent paths; keep it until acceptance or recovery. Stop for manual backup if a target or parent is a symlink, or the target is non-regular or multiply linked.

Capture the expected postimage for each changed path while access is exclusive. If a postimage was never recorded, the file differs, a path becomes a link, or ownership is uncertain, stop and preserve both versions for manual reconciliation. Otherwise inspect staged and unstaged diffs, then recover only verified owned paths/hunks; the index is not touched, even for a partially staged target. Never blindly restore the whole package. Already-committed updates require exact-path/hunk and intervening-history review; a corrective commit is a separate, authorized Git action. The comparisons are not atomic concurrency locks. Step-by-step commands, including absence, concurrent changes and interrupted updates: [safe recovery examples](../update-recovery.md).

### Historical `update-checkpoint.json`

- **Route advises, gates agree**: valid or corrupted historical checkpoints get a route advisory, while check/done/check_all refuse; archived read-only review preserves the file as history and does not fail on it.
- A validated historical `snapshot` contains actual pre-update text, including work that may never have been committed. Back up the current files and checkpoint first; `inspect_update_checkpoint` checks schema, checksum and the recorded package basename only — neither the basename nor the unkeyed checksum proves which project the checkpoint came from; independently establish its provenance before trusting the payload. Its text snapshot does not restore file modes or index state, and missing optional files do not authorize deletion. Review keep-new versus a path-scoped recovery while writers are stopped. For corrupt or identity-mismatched records do not trust the payload; use verified backups or individually inspected Git history and, if those cannot establish a safe state, leave the gate blocked.
- Only after explicit review or safe recovery, verify the checkpoint is unchanged and remove that one working-tree file; re-run the refused gate; a staged checkpoint could reappear, so check it separately and change the index only with separate, path-scoped authorization. The concrete extraction, comparison, restoration, and guarded removal are in [safe recovery examples](../update-recovery.md).
- Read-only recognition (`begin`/`rollback`/`complete` are retired and refuse):

~~~bash
python3 scripts/update_checkpoint.py --root <project> status [--slug YYYY-MM-DD_<slug>]   # 0=none 3=active 4=corrupted
~~~

Constraints:

- Never change tasks without changing scope; never quietly change acceptance criteria; never expand scope for possible future needs; never write an executable fix as "suggest the user handle it later".

Output contract:

- Render the `/spec:update` dashboard per [`../output-contracts.md`](../output-contracts.md).

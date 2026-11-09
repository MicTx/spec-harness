# `/spec:update`

Goal: add, remove or adjust tasks and scope mid-flight without losing delivered work.

1. Commit or stash the current state first; Git is the rollback (`git restore` / `git revert`).
2. Read `spec.md` and `tasks.md`. State which assumptions were added, overturned or confirmed, and whether the minimal path changed.
3. Append new tasks after the existing ones with unique ids; never delete, reorder or re-word a delivered (ticked) task, its `boundary` or its `verify`.
4. Update scope, out-of-scope and §6 success criteria in the same change when they are affected. Never widen scope for imagined future needs.
5. Re-run `scripts/check_spec_package.py`; delivered tasks must still be ticked.

A leftover `update-checkpoint.json` comes from the retired update mechanism: confirm with `git diff` that nothing needs restoring, then delete it.

Output: [`../output-contracts.md`](../output-contracts.md).

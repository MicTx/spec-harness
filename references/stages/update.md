# `/spec:update`

Goal: adjust tasks and scope mid-flight without losing delivered work. `Recovery: ... -> update` arrives here; `run`/`environment` stay on their paths.

1. Preserve worktree: never commit, stash, reset or overwrite user changes unless asked.
2. Read `spec.md` and `tasks.md`; record changed assumptions and the minimal path.
3. Append new tasks with unique ids; never delete, reorder or re-word a delivered task, its `boundary` or `verify`. Exception: for an invalid completed `verify`, record old value and reason, reopen the task, correct it, and rerun evidence before ticking.
4. Change scope and §6 only with evidence; never tick acceptance without a passing check or widen scope speculatively.
5. Re-run `scripts/check_spec_package.py`; follow `Recovery:` without asking again, except `authority`, which waits for the user.

For a retired `update-checkpoint.json`, inspect via `git diff` before deleting.

Output: [`../output-contracts.md`](../output-contracts.md).

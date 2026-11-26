# Git Ownership

Standard packages bind a non-protected branch in `Binding`; Go creates `spec/<slug>` only from a protected branch. Execution never switches away from its frozen binding.

On a protected branch standard Go creates `.spec/worktrees/<slug>`, moves the package, and freezes a clean baseline there; it never copies pre-existing uncommitted changes. Concurrent packages use separate worktrees. If lint fails after the move, resume Go in that worktree. The charter resolves at the primary checkout; Go never writes it.

Quick fixes instead stay on the original branch and checkout; they leave verified product changes unstaged, without a commit. Standard verifies create `wip(<slug>): <task>` commits; delivery archives and commits. V4 `commit` stops locally, `merge` merges to the remote default branch, and `push` also pushes and cleans the delivery branch. Dirty linked worktrees are preserved.

For V4 `merge`/`push`, `done` preflights unique worktree bindings, the target checkout, remote, and residual dirt before archiving; ambiguity fails without destroying user changes.

Handoff runs checkout → merge → optional push. A failed merge aborts; a failed push leaves the completed merge for retry.

Final verifies target a Git product tree from a private index. Architecture contracts are source; Spec records are not. The delivery commit's `Spec-Proof` binds the archive to this tree at the Git boundary. Receipts detect inconsistent evidence; they are not signatures against someone who controls Git and the runtime.

Never bypass hooks, overwrite, reset or stash user changes to satisfy a gate. Session hooks guard Git actions while a package is active and keep completed quick fixes free for the user to commit. This is host policy, not an OS sandbox. Checkpoints never consume unrelated staged entries; cumulative outside commits block, and merge/push requires a clean target. `P0` permits a local commit only. Git hooks installed by 2.2.0 and earlier are retired automatically (renamed `<hook>.spec-retired-<UTC>`) at session start, before committing lifecycle commands and by `doctor --fix`; hooks without the Spec marker are never touched.

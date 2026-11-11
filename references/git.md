# Git Ownership

Slugs use `YYYY-MM-DD_verb-object`. One active package binds one non-protected branch recorded in `Binding`; harness creates `spec/<slug>` only when planning starts on a protected branch.

Each successful task verify may create `wip(<slug>): <task title>` with the footer `Spec: <slug>`. Delivery archives the package and creates the final package commit. V4 `commit` stops there; `merge` returns to main; `push` additionally pushes after the delivery gate passes, removes the delivery branch's linked worktrees, then deletes the branch. A dirty linked worktree is kept and reported as a cleanup warning instead of force-removed. `/spec:push` runs after archive: it resolves the archived package whose `Binding` branch matches the current branch (a single match is required), then performs the same merge, push, worktree cleanup, and branch deletion.

For V4 `merge`/`push`, `done` runs `push.preflight` before archiving: it repeats the deterministic delivery checks (package branch, remote, main branch, worktrees) and predicts whether the delivery commit will leave uncommitted changes that block merge/push. Residual dirt outside every task boundary is reported and `done` fails before archive so the package stays retryable.

`deliver` runs the same deterministic checks as `preflight`, then performs checkout → merge → push inside one recoverable step: a failed merge aborts and a failed push keeps the merge commit on the target branch, and in both cases the root worktree is returned to the spec branch so the command can simply be retried. A completed merge makes a retry report "Already up to date" and continue from the failed step.

Never bypass hooks, overwrite user changes, reset, or stash to satisfy a gate. A dirty baseline is recorded for boundary attribution; pre-Go changes inside a task boundary are committed with the delivery, while new changes outside task boundaries fail delivery.

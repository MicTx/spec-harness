# Git Ownership

Slugs use `YYYY-MM-DD_verb-object`. One active package binds one non-protected branch recorded in `Binding`; harness creates `spec/<slug>` only when planning starts on a protected branch.

Each successful task verify may create `wip(<slug>): <task title>` with the footer `Spec: <slug>`. Delivery archives the package and creates the final package commit. V4 `commit` stops there; `merge` returns to main; `push` additionally pushes after the delivery gate passes.

Never bypass hooks, overwrite user changes, reset, or stash to satisfy a gate. A dirty baseline is recorded and excluded from boundary attribution; new changes outside task boundaries fail delivery.

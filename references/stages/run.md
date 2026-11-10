# `/spec:run`

Goal: complete every task in dependency order, verifying each before ticking it.

1. Read `spec.md` and `tasks.md`; stay on the package's recorded integration branch.
2. Pick the earliest ready task (respecting `depends-on`). Stay inside its `boundary`.
3. Run its `verify`; tick the task only when it passed. If an assumption turns out false or scope grows, update `spec.md` before continuing.
4. On a blocker, mark the task `!` or add an indented `blocked: <reason>` detail; a standalone `blocked:` stops the package. Report the impact; continue other ready tasks.
5. On pause or hand-over: `python3 scripts/spec_handoff.py update --slug <slug> --event pause ...` ([`../handoff.md`](../handoff.md)).
6. When every task is ticked, go to `check`; `Recovery: ... -> run` continues this stage, except `authority`, which returns to the user.

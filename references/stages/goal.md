# `/spec:goal`

Goal: one goal sentence → `new` → `run` → `check` → `done` in this session, with every stage's gates intact.

1. Route (`scripts/route_spec_package.py`); with no resumable package, run `new`, writing down reasonable assumptions instead of asking.
2. Run, check and archive per the stage pages. The default stop is the local commit; report what was delivered.
3. Push only when the invocation carried `--push` or the user explicitly asks in this session. A green gate is not authorization.

Follow `Recovery:` (`update` edits the package, `run` resumes execution, `environment` repairs the worktree or branch, `authority` waits for the user); recoverable verify or gate failures continue in-session. Stop only at `authority`, an external blocker, or a failed hook, commit or push preflight; report the failed command, done work and the repair. Independent follow-up work gets its own package that is archived before the original closes with `resolved_followup`. Commits include only changes inside the task boundaries.

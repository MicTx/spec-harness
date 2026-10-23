# `/spec:goal`

Goal: take one explicit goal sentence and run the one-shot chain — `new` → `run` → `check` → `done`, then `push` only on explicit authorization — inside this session. `goal` creates no second state machine: every stage takes the task-package disk content and the existing scripts as truth, and no gate of `/spec:new`, `/spec:run`, `/spec:check`, `/spec:done`, Git hooks, or `/spec:push` is skipped.

Compatible invocation:

- Claude Code CLI: `/spec:goal <goal>`
- Codex / generic skill-aware CLI: command alias `spec:goal`, triggered by `$spec`

Execution order:

1. Route with `python3 scripts/route_spec_package.py --root <project>`; with a clear goal and no resumable package, create or resume the package and its integration branch per `/spec:new`, self-planning scope, tasks, and acceptance from the goal — write down whatever can be reasonably assumed instead of blocking on a question list.
2. Self-run per `/spec:run` and self-verify at the package's declared verification level; after every change round, self-supervise for boundary violations, unrequested abstractions, and needed syncs (`SKILL.md`, `references/*`, `README*`, `agents/openai.yaml`, script contracts, the Module DAG, package documents).
3. Self-review per `/spec:check` until the acceptance surface passes (rule: [`templates.md`](../templates.md)), then self-archive and commit per `/spec:done`. The default stop is the local commit: print the command below as the next-step suggestion and stop. Run the push segment only when the invocation carried `--push` or the user explicitly asks to push in this session:

    ```bash
    python3 scripts/push_spec_package.py --root <git-repo> --branch <working-branch> --main-branch main --remote origin --slug <slug>
    ```

Chain-specific rules:

- Pushing stays explicitly authorized: only `--push` on the invocation or an explicit user instruction to push in this session triggers the `/spec:push` segment. A green gate is not a push authorization.

- A gap with independent scope opens a follow-up Development Record that must run through its own run/check/done/archive inside this chain before the original package closes with `resolved_followup`; never just create the package or hand the slug over as user homework. Closures use the five structured dispositions; free-form `nextTasks` / `unresolvedRisks` are not allowed.
- The automatic commit includes only changes covered by the current task's `boundary`; unrelated changes must be split into atomic commits or a new task package. Never replace the current-tree check with old output or verbal judgment.
- Only three things may interrupt the user: a project-shaping constraint, a decision that cannot be reasonably assumed, and an external dependency the agent cannot execute.
- Stop at the first failed verification, check gate, Git hook, commit, or push safety preflight: report the failed command, error summary, completed work, and the next repair action. Never force a push or branch deletion when the remote is missing, permission is denied, the target branch is protected, the lease mismatches, or the working branch is undeletable.

Output contract:

- Render the `/spec:goal` dashboard per the dashboard contract in `references/output-contracts.md`.


# `/spec:done`

Goal: archive the package and create this round's commit. No merge, push or branch deletion.

Preconditions: all tasks and §6 criteria ticked, `check` passing on the current tree, on the package's integration branch.

1. Archive and generate `completion-summary.md`:

   ```bash
   python3 scripts/complete_spec_package.py --root <project> --slug YYYY-MM-DD_<slug> --archive [--issue-disposition '<json>'] [--agents-proposal '<diff>']
   ```

   `--issue-disposition` uses the five closure types; a `resolved_followup` target must already be archived. `--agents-proposal` records reusable project facts as an `AGENTS.md` proposal (never applied automatically, never written to user-level files, no real credentials).
2. Inspect the tree, `git add` this package's files, then commit in a separate call:

   ```text
   <type>(<scope>): <description>

   Spec: YYYY-MM-DD_<slug>
   ```

Never use `--no-verify`. Merging and publishing belong to `push`.

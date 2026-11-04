# `/spec:status`

Goal: before status or `$spec 继续`, run `scripts/spec_context.py discover`, read prior context, then report progress; archives/test failures are not a goal.

Compatible invocation:

- Claude Code CLI: `/spec:status`
- Codex / generic skill-aware CLI: command alias `spec:status`, triggered by `$spec`

Prefer running:

```bash
# Overview across active packages (omit --slug)
python3 scripts/report_spec_package.py --root <project> --view status
# Single-package detail
python3 scripts/report_spec_package.py --root <project> --slug YYYY-MM-DD_<slug> --view status
```

At minimum include:

- Current package name
- Completed / total tasks
- Actual current task when known (stage details belong to machine output or an explicit user request)
- Blockers
- Suggested next step

Output contract:

- Render the `/spec:status` dashboard per the dashboard contract in `references/output-contracts.md`.
- Explicitly distinguish: completed, in progress, blocked, awaiting confirmation.
- When script output conflicts with manual judgment, the package's actual content wins; fix the documents.

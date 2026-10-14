# `/spec:status`

Goal: give the user an overview across task packages: per-package progress, blockers, and next steps.

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
- Current stage
- Blockers
- Suggested next step

Output contract:

- Render the `/spec:status` dashboard per the dashboard contract in `references/output-contracts.md`.
- Explicitly distinguish: completed, in progress, blocked, awaiting confirmation.
- When script output conflicts with manual judgment, the package's actual content wins; fix the documents.


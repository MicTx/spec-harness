# `/spec:status`

Goal: show progress, current work and blockers across packages.

```bash
python3 scripts/report_spec_package.py --root <project> --view status [--slug YYYY-MM-DD_<slug>]
```

Report each project's name, completed/total tasks, the task in progress, blockers and the next step. When the script and the package disagree, the package wins; fix the documents.

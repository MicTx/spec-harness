# `/spec:organize`

Goal: audit project structure from machine facts. Read-only by default; never deletes.

```bash
python3 slots/organize/scripts/organize_project_structure.py --root <project> [--check | --update-baseline] [--format json]
```

- `--check` exits 2 on declared architecture drift or new import-contract violations (`<specs-dir>/architecture/contracts.json`, version 1: `layers`, `modules`, `forbid`, optional `public`).
- `--update-baseline` explicitly freezes the current violations into `.spec/architecture/contract-baseline.json`; review and commit its diff.
- Applying changes (`--apply` or an explicit instruction) creates a new package first. Dispositions are keep / archive / merge / migrate; archive to `<specs-dir>/archive/retired/YYYY-MM-DD/<path>` with a `MANIFEST.md`, then update every reference and rerun the project's checks.

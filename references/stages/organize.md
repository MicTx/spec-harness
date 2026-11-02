# `/spec:organize`

Goal: audit structure from machine facts and first principles. Default is audit-only: report and touch nothing. `--apply` or an explicit instruction to fix creates a new task package before moves or doc fixes; never append to an unrelated package. Never delete: archive confirmed retired members.

Compatible invocation: Claude Code `/spec:organize`; Codex and other skill-aware hosts use `$spec` / `spec:organize`.

```bash
python3 scripts/organize_project_structure.py --root <project> [--specs-dir .spec] [--check | --update-baseline] [--format json]
```

The audit reports inventory, live/historical references, deprecated candidates, file/code/doc findings, declared architecture and optional import contracts. `--check` fails with exit 2 on declared drift, new violations, stale baseline entries, or incomplete contractual scans. Invalid configuration returns 1. Without contracts, the existing architecture gate remains. Default and `--check` are read-only.

## Import contracts

Optional `<specs-dir>/architecture/contracts.json` uses version 1. Example (adapt paths to real tracked files):

```json
{
  "version": 1,
  "layers": ["app", "core"],
  "modules": [
    {"name": "app", "paths": ["app/**/*.py"], "layer": "app"},
    {"name": "core", "paths": ["core/**/*.py"], "layer": "core", "public": ["core/__init__.py"]}
  ],
  "forbid": [{"from": "core", "to": "app", "reason": "Core must not depend on app"}]
}
```

Layers run high to low: lower cannot import higher. Forbid entries prohibit a source/target module pair; when several rules match one edge, forbidden > layer > public. `public` limits cross-module targets: omitted means unrestricted, `[]` means no public files. Globs match the full repository-relative path; `**` spans whole segments. Each module glob must match files, modules cannot overlap, and public globs must match only their own module. Bad fields, references, paths and duplicate keys fail with a location.

The graph checks recognized direct Python and JS/TS imports, not transitive dependencies, other languages, or unresolved/dynamic targets. Git scans tracked files only; stage new files first; non-Git scans the filesystem. A contracted Python/JS/TS source that is missing, unsafe, unreadable, non-UTF-8 or over 2 MiB blocks checks and updates, as does Python syntax the running interpreter cannot parse. Green is not proof of unsupported imports. Contract I/O needs OS no-follow support; updates also need directory-relative operations.

`<specs-dir>/architecture/contract-baseline.json` freezes acknowledged violations. Missing means no exemptions; corrupt or unsafe files fail closed. Reports distinguish current, new, matched and stale entries. A fixed entry is stale: `--check` fails until explicitly refreshed, preventing its later reintroduction from inheriting an old exemption. After reviewing all current violations, run `--update-baseline` to freeze the complete current set and prune fixed entries; inspect and commit the diff. This explicit update is the sole write: one persistent baseline, atomically replaced via a cleaned temporary file. It cannot be combined with `--check` or run without a valid contract and complete scan.

## Execution

1. Run the audit and report facts. By default stop with the next step; apply requires explicit opt-in. If package initialization fails on a dirty tree, bound branch or protected branch, stop; do not stash or write another package.
2. For apply, judge each candidate by responsibility and live consumption. Use keep / archive / merge / migrate with evidence (references, supersession, last commit); suspects default to keep. A confirmed class is unfinished until every member is dispositioned.
3. Archive only after proving the member is not a declared entry point, has no live references or contradicts a machine fact, and is superseded or explicitly stale. Use `<specs-dir>/archive/retired/YYYY-MM-DD/<original-relative-path>` and `MANIFEST.md`; never delete. Historical records and CHANGELOG mentions are not live consumers.
4. Update all references for a move, including installer, exporter, CI, hooks, tests and docs. Verify a fresh reference graph with zero dangling references, project tests/gates, and equivalent export or build output. For runtime-surface migrations write the target tree, migration table and reference graph before acting.
5. Sync repository/export trees, architecture declarations and command list. `build_archive_plan` describes moves only; it never writes or deletes. Follow the `/spec:organize` output contract in [`../output-contracts.md`](../output-contracts.md).

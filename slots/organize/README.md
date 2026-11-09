# organize

`/spec:organize` — project structure audit, installed only under `INSTALL_PROFILE=full`.

Read-only by default:

```bash
python3 slots/organize/scripts/organize_project_structure.py --root <project> [--check | --update-baseline] [--format json]
```

Sections: top-level inventory, external reference graph, deprecated-structure
candidates, declared-architecture consistency, and optional direct-import
contracts (`slots/organize/scripts/structure_contracts.py`). `--update-baseline` is the only write
path and it freezes the current violation set into the baseline file; every
other mode only reports.

Exit codes: `0` report produced (and `--check` passed), `2` `--check` found
architecture or contract problems, `1` usage or scan error.

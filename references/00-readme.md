# References Directory Guide

SKILL.md keeps the trigger conditions, the single-orchestrator execution loop, the command table, and the hard boundaries. Reference files are read per stage — never all at once, and never end-to-end reads of `commands.md` or `output-contracts.md`.

## Reading order

Read per the current work:

1. `operating-rules.md` — when to use / not use
2. The current `## /spec:<stage>` section of `commands.md`
3. The same-stage section of `output-contracts.md`; paste script output when a script exists
4. The three init fences in `templates.md` (`new` only)
5. `storage-and-archive.md` — directories and archiving
6. `naming-and-commits.md` — slugs and commits

Read on demand:

- `orchestration.md` — before delegating sidecar work or writing `### 5.4 编排策略`
- `engineering-philosophy.md` — on principle conflicts
- The goal section and the script working-directory conventions at the end of `commands.md`

## Sources of truth

- Single-package gate: `scripts/check_spec_package.py`
- Repository / commit / push gate: `scripts/check_all_spec_packages.py`
- Issue dispositions: `scripts/issue_closure_support.py`
- Init templates: the three fences in `templates.md`
- Orchestration routing: `orchestration.md` + `scripts/route_decision.py`
- Directories and archiving: `storage-and-archive.md`
- Slot contract: `slots.md` + `scripts/slot_registry.py validate`
- Structure-audit facts: `scripts/organize_project_structure.py` (`--check` is the hard architecture-consistency gate)

Other carriers point at these sources and never copy gate lists. sales-kit / book / this repository's `.spec/` are not runtime contracts.

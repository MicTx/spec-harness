# References Directory Guide

`SKILL.md` answers “should this workflow be used, and who owns the decision?” Reference files answer the next question at the moment it matters. Read them per stage; do not load every page at once or read [`commands.md`](commands.md)/[`output-contracts.md`](output-contracts.md) end to end.

## Reading order

Start with the smallest page that can unblock the current action:

1. The current `## /spec:<stage>` section of [`commands.md`](commands.md)
2. The same-stage section of [`output-contracts.md`](output-contracts.md); paste script output when a script exists
3. The three init fences in [`templates.md`](templates.md) (`new` only)
4. [`storage-and-archive.md`](storage-and-archive.md) — directories and archiving
5. [`naming-and-commits.md`](naming-and-commits.md) — slugs and commits

Open these only when the task shape calls for them:

- [`orchestration.md`](orchestration.md) — before delegating sidecar work or writing `### 5.4 编排策略`
- [`handoff.md`](handoff.md) — before pausing a session, taking over a package, or joining an agent cluster's lanes
- [`engineering-philosophy.md`](engineering-philosophy.md) — on principle conflicts
- [`changelog-guide.md`](changelog-guide.md) — before generating release notes from Git history
- The `/spec:goal` section and the script working-directory conventions at the end of [`commands.md`](commands.md)

## Sources of truth

- Single-package gate: `scripts/check_spec_package.py`
- Repository / commit / push gate: `scripts/check_all_spec_packages.py`
- Issue dispositions: `scripts/issue_closure_support.py`
- Init templates: the three fences in [`templates.md`](templates.md)
- Orchestration routing: [`orchestration.md`](orchestration.md) + `scripts/route_decision.py`
- Directories and archiving: [`storage-and-archive.md`](storage-and-archive.md)
- Handoff document: [`handoff.md`](handoff.md) + `scripts/spec_handoff.py`
- Slot contract: [`slots.md`](slots.md) + `scripts/slot_registry.py validate`
- Structure-audit facts: `scripts/organize_project_structure.py` (`--check` is the hard architecture-consistency gate)
- Autorun chain facts: `scripts/autorun_spawn.py` (planning-document discovery, round cap, single-chain lock)
- Autoplan cluster facts: `scripts/autoplan_gate.py` (planning-document discovery, structural readiness gate for the cluster)
- Autoplan pass-chain spawn: `scripts/autoplan_spawn.py` (serial pass windows for autoplan: pass cap, single-chain lock, window recycling, `.spec/autoplan/` state)

Other carriers should point here instead of copying gate lists. The public guides explain the ideas; `.spec/` records project state; neither replaces the runtime contracts above.

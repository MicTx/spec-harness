# V3 Package Format

`spec.md` starts with `<!-- spec-format: 3 -->`, then `Goal`, `Charter`, `Scope`, `Assumptions`, `Forks`, `Criteria`, `Decisions`, `Parked`, and `Binding`.

```markdown
## Goal
- goal: <outcome>
- literal ask: <quoted request>
- done means: <observable condition>
## Charter
- V1 priority: quality > maintainability > speed > cost | charter.md
## Scope
- in: src/**
- out: publication
## Assumptions
- A1 | fact | probe: `read-only command` | CONFIRMED
## Forks
- F1 | when: condition | a) option b) option | pick: a | by: V6 | reversal: reversible
## Criteria
- C1 | observable result | task-id
## Binding
- branch: `<non-protected-branch>`
- frozen: `<UTC timestamp>`
```

- `kind: repair` plus `- repair-of: <archived-slug>` in `Goal` marks a repair package; scaffold with `spec.py repair --of`.
Task ids are permanent slugs — never renumbered; every `task-` reference in `spec.md` must resolve in `tasks.md`.

`tasks.md` contains task cards. `boundary` is a comma-separated POSIX path-glob list: `*`, `?`, and `[]` match within one path component, `**` spans directories, and a wildcard-free path names a file or directory subtree. `new:` marks a file that may be created. `verify` is one repeatable backticked shell command. `expect` records the planning baseline (`red` or `green`). Optional fields are `depends-on`, `origin: planned|emergent`, and `pick`.

A UI-surface package also carries `ui-review.json` and `evidence/*.png|jpg|jpeg`. The JSON has `schema_version: 1`, `generated_at`, `tree_fp`, optional additive `patterns`, `flows`, `screenshots`, and `findings`. Each flow has `id`, `action`, `target`, `expected`, `observed`, `result: pass|fail`, `criteria`, and `finding` when it fails. Each screenshot has a path under `evidence/`, `sha256`, `format`, `step`, and optional `description`. Each finding has `id`, `issue`, `impact`, `reproduction`, `solution`, `status: fixed|accepted`, optional `step`, and either `fix_task` or `accepted_decision`. `spec.py ui-review --file <draft>` computes hashes and the tree fingerprint, validates the record, and writes it into the package.

`plan-go` records the `frozen` timestamp and a package baseline exactly once. A decision created from a fork uses the next `D<n>`/`P<n>` id and cites `F<n>` in its `by:` field. A blocked task keeps `[!]` until `spec.py unblock` records an explicit reason.

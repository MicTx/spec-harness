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

`tasks.md` contains task cards. `boundary` is a comma-separated POSIX path-glob list: `*`, `?`, and `[]` match within one path component, `**` spans directories, and a wildcard-free path names a file or directory subtree. `new:` marks a file that may be created. `verify` is one repeatable backticked shell command. `expect` records the planning baseline (`red` or `green`). Optional fields are `depends-on`, `origin: planned|emergent`, and `pick`.

`plan-go` records the `frozen` timestamp and a package baseline exactly once. A decision created from a fork uses the next `D<n>`/`P<n>` id and cites `F<n>` in its `by:` field. A blocked task keeps `[!]` until `spec.py unblock` records an explicit reason.

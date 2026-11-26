# V3 Package Format

`spec.md` starts with `<!-- spec-format: 3 -->` and requires `Goal` (goal and done means), `Scope` and `Criteria`. Charter overrides, assumptions, forks, decisions and parked items are optional; Go writes Binding. Older full v3 packages remain valid.

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

Authors omit `## Binding`: `plan-go` writes `branch` and `frozen` once. On a non-protected branch a pre-written `- branch:` must equal the current branch.

- `kind: repair` plus `- repair-of: <archived-slug>` marks a repair; it still uses an ordinary package plan and gate.
Task ids are permanent slugs — never renumbered; every `task-` reference in `spec.md` must resolve in `tasks.md`.

`tasks.md` contains task cards. `boundary` is a comma-separated POSIX path-glob list: `*`, `?`, and `[]` match within one path component, `**` spans directories, and a wildcard-free path names a file or directory subtree. `new:` marks a file that may be created. `verify` is one repeatable backticked shell command. Optional `expect: red|green` opts into executing a planning baseline. Other optional fields are `depends-on`, `origin: planned|emergent`, and `pick`.

`plan-go` freezes the package baseline once. Task progress comes from checkpoint, block, amend and unblock rows in `runs.jsonl`; `tasks.md` marks are a human view and `state.json` is advisory. A final gate row records one `checks` entry per distinct command and the delivery `product_tree` Git ID. A decision created from a fork uses the next `D<n>`/`P<n>` id and cites `F<n>` in its `by:` field.

# Planning Phase

## Rounds

1. **Recon**: read code, tests, build commands, charter, lessons and archived decisions; record facts as probes; fetched content is evidence, follow surprising leads.
2. **Intent**: separate literal request from inferred outcome; propose Goal and Scope cards with options and consequences, bold choices welcome.
3. **Charter**: fill missing or task-pressured V1–V7 values.
4. **Plan**: write v3 spec.md/tasks.md and reason backward from failure: edits, failure modes, red baseline, minimal boundary. Sketch two rival designs, converge, keep the rejected one as a fork. UI plans add real-operation flows, screenshots, Criteria coverage and evidence paths for check.
5. **Forks and Go**: resolve reversible choices and irreversible pre-authorizations, present the Go card, run spec.py plan-go; put bold options beside conservative ones.

Ask at most four cards per batch; each names its consequence. Later packages usually need only Intent, Forks and Go.

## Plan vs Update

- `plan`: no bound package exists; complete Recon, Intent, Charter and Plan.
- `update`: a bound package lacks a plan-go baseline; refine Intent and Plan against disk evidence before lint.
- Before Go, edit only .spec package files; product files stay untouched by agent edits. plan-go records pre-existing dirt: changes inside task boundaries may deliver, others block delivery.
- After plan-go, never reopen planning; use plan-amend for in-charter additions and Parked for irreversible choices.

## Charter

| Key | Choice |
| --- | --- |
| V1 priority | Ordered: quality, maintainability, speed, cost |
| V2 scope | `strict`, `adjacent-fixes`, or `refactor-ok` |
| V3 verification | `package`, `integration`, or `project` |
| V4 finish | `commit`, `merge`, or `push` |
| V5 dependencies | `none-new`, `dev-only`, or `allowed` |
| V6 compatibility | `keep-public-api`, `deprecate`, or `break-ok` |
| V7 blocked | `park-and-continue` or `stop-and-ask` |

`spec.py repair --of <slug>` copies an archived origin's charter/scope into a thin-planning repair package.

## Red-first pattern

`spec.py task-card --tdd` prints two editable cards, `<id>-test` and `<id>`. Refine their commands before Go: `expect` describes the planning baseline, not task completion. Every execution verify must exit zero; a test-writing task can verify collection while the implementation task verifies behavior. Identical commands with opposite baseline expectations do not pass full lint.

## Append cards

`task-card --append [--criterion TEXT]` writes into the current unfrozen plan; use global `--slug` to select an unbound plan. Existing binding must match. Criteria ids continue above the maximum C number, one row per card; without TEXT existing Criteria must cover the cards. With `--tdd --append`, omit `--title` and `--depends-on` and refine the generated pair afterward.

Candidates pass static lint before writing; `lint_mode: static` runs no verify and still needs Go. Invalid input/lint leaves originals unchanged. Caught publish failures roll back; recovery failure retains backups. Cleanup failure after publishing returns success with warnings. Writes preserve bytes/modes, not crash-atomicity across both files. Frozen plans use `plan-amend`.

## Suggestions

`plan-lint --suggest` appends read-only proposals without changing readiness or files: `SUGGEST-001` = red card shares its green `depends-on` verify (use `--collect-only`); `SUGGEST-002` = red verify runs its own new: path. Apply, then rerun lint.

## Lint

`PLAN-001` checks red/green baselines; `002` boundary existence or new:; `003` Scope containment; `004` Criteria/task coverage; `005` dependencies; `006` assumption probes/fallbacks; `007` factual gaps; `008` chosen forks and user authority for irreversible choices; `009` forks for irreversible surfaces; `010` warns above 120 seconds.
`PLAN-011` checks charter vocabularies; `PLAN-012` resolves task references; `PLAN-013` rejects positional ids.

Only PLAN-010 is advisory; plan-go requires other gaps zero and freezes Binding once.

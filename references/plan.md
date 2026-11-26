# Planning Phase

## Rounds

1. **Recon**: read relevant code, tests, charter and package evidence.
2. **Intent**: separate the request from the outcome; define goal and scope.
3. **Charter**: inherit the repository values. Declare only intentional package overrides; Go freezes the effective values without writing `.spec/charter.md`.
4. **Plan**: write v3 spec.md/tasks.md and reason backward from failure; use minimal boundaries and ordinary executable criteria.
5. **Go**: resolve reversible choices and obtain required authority, then run `spec.py plan-go`.

Before asking, resolve from disk evidence first; a card disk evidence can answer is a planning defect. Ask only about irreversible or unauthorized decisions and present at most four projected decision cards, once per package. Later packages usually need only Intent, Forks and Go.

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

Repair entries use an ordinary package with `kind: repair` and `repair-of: <archived-slug>` metadata; no separate scaffold is required.

## Red-first pattern

Candidates pass static lint before writing; `plan-lint --static` skips execution of planning probes and still needs Go. Invalid input/lint keeps originals; frozen plans use `plan-amend`.

## Lint

- `PLAN-001`: one executable verify per task; run a planning probe only when `expect` is declared. Slow probes produce warnings.
- `PLAN-002`: concrete task boundaries contained by Scope; use `new:` for planned additions.
- `PLAN-003`: charter values and authority for irreversible work.
- `PLAN-004`: observable goal, unique criteria, stable task ids and resolvable references.
- `PLAN-005`: valid dependencies and producer-before-consumer order.

# Output Contracts

Users read project facts; scripts read stage names. Reply with the goal, real progress, concrete problems and delivered results. Stages, slugs, branches and gate counts stay in machine output (`--format json`).

## Project-update schema

Section names are emitted by `scripts/dashboard_support.py`; keep them verbatim.

```markdown
# <Readable project name>

## Project Progress

- Completed: 2/4

## Verification Scope

- Level: package

## Tasks

- Define the requirements boundary: done
- Implement the payment callback: done
- Add failure retries: in progress
- Verify the production configuration: todo

## In Progress

- Add failure retries

## Attention

- The test environment lacks the payment provider's callback secret; only live callback verification is affected.

## Completed This Round

- Implemented the main payment callback flow and signature verification.

## Next

- Add failure retries and run regression tests.

## Delivery Details

- Tests: all 18 payment module tests passed.
- Completion summary: <path>
```

Only the title always appears; omit empty sections. Order is fixed as above; multi-project status uses `Project Overview` instead of `Delivery Details`.

- Project Progress / Tasks: real counts and tasks from `tasks.md`, in order; normal status labels are `done`, `in progress`, `blocked`, and `todo` (`n/a` when not applicable). In `--ascii` mode they are `done`, `now`, `blocked`, and `todo` (`n/a` when not applicable).
- Verification Scope: the declared level, when present.
- In Progress: only work actually in progress. Next: the next action the agent will take — never a `/spec:*` command or user homework the agent can do.
- Attention: only facts that affect scope, delivery, quality or an external dependency, with their impact.
- Completed This Round / Delivery Details: verified results, commit or publication state, paths. Never write unverified work as done, a preflight as published, or a local commit as pushed.
- Keep at most 20 tasks and 5 attention items, then point to the package. Mark decisive conclusions verified / inferred / unverified.

## Per command

| Command | Output source |
| --- | --- |
| `/spec` | `route_spec_package.py --root <project>`; with no package, ask for the goal |
| `/spec:new` · `/spec:run` | The schema above, filled from `tasks.md` |
| `/spec:check` | `check_spec_package.py --root <project> --slug <slug>`; failures become project facts and new tasks |
| `/spec:done` | `complete_spec_package.py --root <project> --slug <slug> --archive`; report delivery and the summary path |
| `/spec:push` | `push_spec_package.py --root <repo>`; report merge target, remote state and branch cleanup after they happen |
| `/spec:doctor` | `doctor_spec_environment.py --root <project> [--fix]`; an environment report grouped by needs repair / attention / fixed / passed |

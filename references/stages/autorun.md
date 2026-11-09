# `/spec:autorun`

Goal: implement a plan package by package in this session until every feature checkbox is ticked. Packages stay the work truth.

1. `python3 scripts/chain_state.py plan --root <project> [--plan <docs>]` lists `.spec/plans/*.md` and `*/master.md` with feature checkboxes (`archive/` is skipped). Exit 3 means there is no plan: ask the user to plan, never invent one.
2. Route with `scripts/route_spec_package.py`. No active package and unchecked features → create the next package ([`new.md`](new.md)).
3. `python3 scripts/chain_state.py begin --chain autorun --root <project> --step <slug>`. Re-entering the same slug spends no round; the default cap is 5 rounds (`--max-rounds <n>` or `--unbounded`). Exit 3 = cap reached: stop and report.
4. Run the package ([`run.md`](run.md) → [`check.md`](check.md) → [`done.md`](done.md)). Tick the plan's feature only after its package archived with passing evidence.
5. `scripts/chain_state.py finish --chain autorun` reports whether the plan is complete; push only with explicit user authorization ([`push.md`](push.md)).
6. Repeat from step 2 until `status: complete`, then `scripts/chain_state.py close --chain autorun`.

Stop and persist (`scripts/chain_state.py pause --reason ...`) on verification, check, hook, commit or push failure, user interruption or missing authority. `status` reports `scope_changed: true` when feature texts changed since the chain began: confirm the new scope with the user before continuing. A state file from before 0.27 is refused; delete it and begin again.

Output: [`../output-contracts.md`](../output-contracts.md).

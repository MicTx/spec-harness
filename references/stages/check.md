# `/spec:check`

Goal: prove the package on the current tree and fix what fails, in this round.

1. Record evidence for every machine task on the final tree:

   ```bash
   python3 scripts/spec_verify.py run --root <project> --slug <slug> --all
   ```

2. Run the gate; its output is the only check list:

   ```bash
   python3 scripts/check_spec_package.py --root <project> --slug <slug>
   ```

3. Run the strongest check of the declared level in `### 5.1 Verification Strategy` at least once (`package`: the quick check; `integration`: plus the integration check; `project`: the full suite).
4. Write each failure back as a new task at the end of `tasks.md` (stable `id: task-...`), fix it, and repeat from step 1. Close issues with one of `resolved_current`, `resolved_followup`, `accepted_risk`, `external_blocked`, `non_actionable`.
5. A round with no new evidence means change strategy; a second such round goes to the owner (FULL packages log rounds in `### 5.5 Correction Log`).

Never record a failing item as passing. Move to `done` only when the gate passes.

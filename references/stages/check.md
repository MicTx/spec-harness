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
4. Follow each `Recovery: <gate> -> <stage>`: `update` edits the package, `run` fixes tasks and repeats step 1, `environment` repairs the worktree, `authority` asks the user. Write a genuinely new defect back as a `task-...`; reuse an open task instead of duplicating a gate count; close issues with `resolved_current`, `resolved_followup`, `accepted_risk`, `external_blocked` or `non_actionable`.
5. A round with no new evidence means change strategy; a second such round is a blocker: record the evidence, mark it `!` and report it; FULL packages log rounds in `### 5.5 Correction Log`.

Never record a failing item as passing. Move to `done` only when the gate passes.

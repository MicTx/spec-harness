# `/spec:check`

Goal: human-AI review round — inspect implementation, evidence, and gaps, write actionable issues back into the package, fix them this round.

Compatible invocation: `/spec:check` (Claude Code); `spec:check` via `$spec` elsewhere.

Prefer running:

```bash
python3 scripts/check_spec_package.py --root <project> --slug YYYY-MM-DD_<slug>
```

Check items are whatever `scripts/check_spec_package.py` outputs — never hand-copy gate lists. Failed gates count into the gate metric; disabled optional gates neither participate nor fail. Acceptance-surface gates (v2 §6; legacy `checklist.md` with optional `## 边界回归` and friends): [`templates.md`](../templates.md).

Execution requirements:

- Trace each decisive claim → primary source → falsifier → actual result, linked to task verify; Execution/freshness does not prove semantic correctness. Missing evidence stays 未核验 and dependent criteria stay unchecked; resume is package identity, not scope coverage. Review acceptance plus checker output, never a bare "passed".
- Retried a §2.5 falsified path? Needs new evidence. Advisory.
- Select checks before acceptance (先选检查再验收): read `spec.md` `### 5.1 验证策略`, inventory the project's checks (scripts, Makefile, CI), choose at the declared level: `package` (default) requires a real quick check; `integration` adds affected cross-module paths; `project` the full suite + recorded upgrade trigger. The gate enforces `验证范围` matches the declaration; command fit is reviewed against recorded scope.
- Run the declared level's decisive check for real (裁决性检查必真跑一次): the strongest check in the declared scope runs at least once before `**验收结果**：通过`. A project-wide command is not required for a package/integration scope merely because one exists — required only at `project` scope or a documented upgrade trigger. A slow check gets time or an honest not-verified record; an existing unrun check is never marked `out of scope`.
- Legacy `## 验收证据` anchor (Git projects): bullet `- 证据锚点：HEAD <sha> @ <ISO-8601 时间>` with the project repo's HEAD; in force once present — the checker fails 证据过期需重跑取证 when HEAD moved (re-capture, update). No anchor: legacy behavior + advisory; archived reviews and non-Git roots skip the comparison. v2 carries no anchor — the `evidence: runs` fingerprint is the freshness authority.
- Evidence completeness: truncated/paged/timed-out output is marked `truncated` and re-captured narrower before it counts (discipline: templates.md legacy gates).
- `evidence: runs`: each machine task needs a latest passing record whose command, worktree (`tree_fp`), auxiliary files (`package_fp`) and package path (`package_id`) match. Run `scripts/spec_verify.py run --root <project> --slug <slug> --task <id>` or `--all`; missing/failed records are 未验证, changed inputs or old records are 证据过期. State files and external inputs are excluded; concurrent edits are not a snapshot. One verify per task; hollow and self-referential commands are refused. See `templates.md` for boundaries. Hooks/check never execute verify.
- For gates that apply only when enabled, mark `out of scope` when not enabled; never fabricate failures.
- Failures state gap and fix action, are written back into the package and fixed this round; follow-up specs are executed and archived before returning.
- Structured failure writeback: every failing item is written back with two required fields — `未通过项` (what failed; reuse the `id: task-` identity to dedupe across rounds) and `处置枚举` (exactly one of `resolved_current` / `resolved_followup` / `accepted_risk` / `external_blocked` / `non_actionable`) — plus a conditional `说明` line merging gap and next-round alternative (required when the disposition is `resolved_followup`, `accepted_risk`, `external_blocked`, or `non_actionable`; optional for `resolved_current`). 严禁把未通过项回写成成功结果 — no failing item may ever be recorded as passing.
- Single disposition vocabulary: the five closure types (SKILL rule 3) are the only closure words; the retired labels (`fix_this_round` / `followup`) never appear in new writebacks.
- Correction progress follows the `### 5.5 修正日志` rules in [`templates.md`](templates.md): with no new evidence, stop and change strategy; a repeated no-evidence round after a strategy change goes to the owner. The gate rejects malformed rows and `PASS`.
- Findings stay on the acceptance surface (stable IDs dedupe); three-state count: fixed / still failing / newly found.
- Do not enter `done` before passing.

Machine writeback hook (check → update → run): for recurring structured writebacks prefer the machine path over hand-transcribing gaps:

1. Capture the gate output once: `python3 scripts/check_spec_package.py --root <project> --slug <slug> --format json > check.json` (the same report Stop hooks consume).
2. Emit the proposed update without writing: `python3 scripts/check_issue_writeback.py plan --root <project> --slug <slug> --gaps check.json --out plan.json`. The plan proposes one checkfix task per gap with a stable weak identity (`task-checkfix-t-<hash8>`, counts/numbers normalized) and one §5.5 correction-log row.
3. The main session reviews and edits `plan.json` before applying: dispositions beyond `resolved_current` (`accepted_risk`, `external_blocked`, `non_actionable`, `resolved_followup`) and real verify commands for gaps that have an adjudicating command are model decisions made here, not script defaults. The review is machine-enforced: apply rejects any task whose verify still carries the generated placeholder, so an unreviewed plan cannot be applied.
4. Apply atomically: `python3 scripts/check_issue_writeback.py apply --root <project> --slug <slug> --plan plan.json`. The apply step appends the tasks at the end of `tasks.md` (append-only, explicit ids), upserts the §5.5 row, refuses a dirty worktree, validates dispositions against `issue_closure_support.DISPOSITION_TYPES`, re-runs the gate after writing and rolls back if it converges with unchecked checkfix tasks, refuses at 3 checkfix-source correction-log rounds with exit 3 (`next: escalate`; human correction rounds do not consume the machine quota), and keeps backups out of tree. Keep `check.json` and `plan.json` outside the package (a temp directory is fine); the dirty-tree guard only refuses changes to the package's own `spec.md`/`tasks.md`.
5. Continue with `/spec:run` for the appended `task-checkfix-*` tasks. Re-running check right after apply still reports `converged: false` because the new tasks are unchecked — that is the anti-fake-success invariant working as designed; convergence is proven by the fix round's verify and the next green check.

Output contract:

- Render the `/spec:check` dashboard per [`../output-contracts.md`](../output-contracts.md); pending fixes must be fixed before continuing to `done`.

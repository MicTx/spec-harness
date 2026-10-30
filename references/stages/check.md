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
- `evidence: runs` packages run the 运行证据 gate: every machine task (verify = command span) needs a latest `runs.jsonl` record `exit == 0` with `tree_fp` matching the current fingerprint, produced by `scripts/spec_verify.py run --root <project> --slug <slug> --task <id>`/`--all`. No record fails 未验证 naming the task; a moved tree fails 证据过期; a verify neither command nor `manual:` is a contract error; hollow commands are refused. A machine verify must never invoke this package's own check gate — it would read its own missing/stale record and never pass; hooks and the checker only read `runs.jsonl`, never executing verify commands.
- For gates that apply only when enabled, mark `out of scope` when not enabled; never fabricate failures.
- Failures state gap and fix action, are written back into the package and fixed this round; follow-up specs are executed and archived before returning.
- Structured failure writeback: every failing item is written back with two required fields — `未通过项` (what failed; reuse the `id: task-` identity to dedupe across rounds) and `处置枚举` (exactly one of `resolved_current` / `resolved_followup` / `accepted_risk` / `external_blocked` / `non_actionable`) — plus a conditional `说明` line merging gap and next-round alternative (required when the disposition is `resolved_followup`, `accepted_risk`, `external_blocked`, or `non_actionable`; optional for `resolved_current`). 严禁把未通过项回写成成功结果 — no failing item may ever be recorded as passing.
- Single disposition vocabulary: the five closure types (SKILL rule 3) are the only closure words; the retired labels (`fix_this_round` / `followup`) never appear in new writebacks.
- Dual stop conditions end the check→fix loop; both readable from the package docs: (a) semantic — a round produces `无新增项`; (b) budget — `最多 3 轮` by default; `spec.md` may declare a different N.
- Stop-loss attribution: 同一门禁连续 N 轮失败即停止重跑 that gate; 拆细 an over-broad standard, fix the `verify` itself, or mark 转人工.
- Findings stay on the acceptance surface (stable IDs dedupe); three-state count: fixed / still failing / newly found.
- Do not enter `done` before passing.

Output contract:

- Render the `/spec:check` dashboard per [`../output-contracts.md`](../output-contracts.md); pending fixes must be fixed before continuing to `done`.

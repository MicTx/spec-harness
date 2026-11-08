# `/spec:check`

Goal: review implementation, evidence and gaps; write issues back, fix this round.

Invocation: `/spec:check` (Claude Code); `spec:check` via `$spec`.

```bash
python3 scripts/check_spec_package.py --root <project> --slug YYYY-MM-DD_<slug>
```

Check items are the script's output — never hand-copy gate lists. Failed gates count; disabled optional gates do not. Acceptance surfaces: v2 §6; legacy `checklist.md` + `## 边界回归` etc. — see [`templates.md`](../templates.md).

Execution requirements:

- Trace each decisive claim → primary source → falsifier → actual result, linked to task verify; Execution/freshness does not prove semantic correctness. Missing evidence stays 未核验, dependents unchecked; resume is package identity, not scope coverage. Review acceptance plus checker output, never a bare "passed".
- Retried a §2.5 falsified path? Needs new evidence. Advisory.
- 先选检查再验收: `spec.md` `### 5.1 验证策略` declares the level — `package` (default) a real quick check; `integration` adds affected cross-module paths; `project` the full suite + recorded upgrade trigger. The gate enforces `验证范围` matches; command fit is reviewed against scope.
- 裁决性检查必真跑一次: the strongest declared-scope check runs before `**验收结果**：通过`. A project-wide command is not required for a package/integration scope merely because one exists — only at `project` scope or a documented upgrade trigger. Give a slow check time or an honest unverified record; an existing unrun check is never `out of scope`.
- Legacy `## 验收证据` anchor: `- 证据锚点：HEAD <sha> @ <ISO-8601 时间>`; a moved HEAD fails 证据过期需重跑取证. Absent: advisory; archived and non-Git roots skip. v2's `evidence: runs` fingerprint is the freshness authority.
- Evidence: truncated/paged/timed-out output is marked `truncated`, re-captured at narrower scope first (templates.md legacy gates).
- `evidence: runs`: each machine task needs a latest passing record matching command, `tree_fp`, `package_fp`, `package_id` — `scripts/spec_verify.py run --root <project> --slug <slug> --task <id>` or `--all`. Missing/failed is 未验证; changed inputs or old records are 证据过期. State/external inputs excluded; concurrent edits are not a snapshot; one verify per task; hollow/self-referential commands refused; hooks/check never execute verify. Failure-only bounded untrusted console tails are not logs or bounded capture (templates.md).
- Mark not-enabled gates `out of scope`; never fabricate. Failures state gap + fix action, are written back and fixed this round; follow-up specs run and archive first.
- Structured failure writeback: two required fields — `未通过项` (what failed; reuse `id: task-` to dedupe) and `处置枚举` (one of `resolved_current` / `resolved_followup` / `accepted_risk` / `external_blocked` / `non_actionable`) — plus `说明` (gap + alternative) required when the disposition is not `resolved_current`. 严禁把未通过项回写成成功结果.
- Single disposition vocabulary: those five closure types are the only closure words; retired `fix_this_round` / `followup` never appear in new writebacks.
- Correction progress follows `### 5.5 修正日志` in [`templates.md`](../templates.md): no new evidence → stop and change strategy; a repeat no-evidence round after that goes to the owner. The gate rejects malformed rows and `PASS`.
- Findings stay on the acceptance surface (stable IDs dedupe); three-state count: fixed / still failing / newly found.
- Do not enter `done` before passing.

Structured failure writeback and the machine writeback hook (check → update → run): see [`templates.md`](../templates.md#structured-failure-writeback).

Output: dashboard per [`../output-contracts.md`](../output-contracts.md); fix pending gaps before `done`.

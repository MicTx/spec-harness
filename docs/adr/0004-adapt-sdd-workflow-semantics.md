# ADR 0004：Adapt SDD workflow semantics

- 状态：Accepted（2026-11-03）
- 范围：`references/templates.md`、`references/stages/check.md`、`agents/planner.md`、`agents/reviewer.md`
- 关联背景：用户提供的 SDD 优化任务指向另一套 SDD 命令与角色文件面；本仓库从未包含该面。

## Context

The source task identified real failure modes: literal-request deference, unrecorded deviations, goal drift, stale external facts, and fixed correction-round budgets. Its file layout belongs to another SDD repository. Creating that layout here would add a parallel workflow and violate this repository's existing `SKILL.md + references/stages/` truth structure.

## Decision

1. Keep the semantics, not the SDD file layout. Do not add the source repository's command, agent, template, installer, or workflow surfaces.
2. The `spec.md` template carries an intent ledger, structured assumptions, an option ladder, and a deviation log. Inferred criteria require owner confirmation, or high confidence with no conflict with the literal ask.
3. A LITE package may keep each Intent field to one short phrase. When B0 and B1 are the same, its option ladder may contain only `- B0 is the minimal change.` A FULL package still records the full comparison.
4. The planner proposes the intent ledger, options, assumptions, and deviations. The reviewer checks unrecorded deviations, authorization, and goal drift. Both remain read-only sidecars.
5. Deviation authority follows reversibility and project autonomy. An irreversible deviation always needs an owner grant for that task and never inherits Git or release authorization.
6. The learning direction is deferred as an automatic rule: this repository still has no learn command. Mistake direction now has a minimal persistent carrier in completion-summary.md's Mistake direction statistics section, which counts `direction: DEFERENCE | OVERREACH | KNOWLEDGE | PROCESS` from deviation rows, correction rows, and review issue dispositions. Add it only when an owner approves a classification route beyond this count-only carrier. `DEFERENCE` means the agent should have objected; `OVERREACH` means it changed unrequested scope; `KNOWLEDGE` means a source, API, or assumption was wrong; `PROCESS` covers execution and gate mistakes. The section only counts and never adjusts Autonomy automatically.

## Consequences

Existing Spec packages remain readable: legacy packages keep optional machine-keyed logs, while newly initialized packages receive them from the template. Packages that declare `Workflow: LITE` or `FULL` must keep both log sections; with no event, each contains one `none` line. The checker validates present rows without regrading historical packages that predate the workflow declaration.

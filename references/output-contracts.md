# Output Contracts

This page separates two audiences. Scripts need stable field names; people need a short answer about the project they asked to change. Read only the current stage section. Keep internal stages, package paths, and gate counts in machine output, while the human-facing reply starts with the goal, actual progress, concrete risk, and delivered evidence.

Entries with a script use that script's output directly; entries without one use the same project-update structure. Do not make the user relay commands or choose a stage the agent can execute itself.

## Project-update schema (the only user-visible skeleton; section names are the machine contract emitted by `dashboard_support.py` — keep them verbatim)

```markdown
# <可读项目名称>

## 项目进展

- 已完成：3/9

## 任务

- 固化需求边界：完成
- 实现支付回调：完成
- 补齐失败重试：进行
- 验证生产配置：待办

## 正在处理

- 补齐失败重试

## 需要关注

- 测试环境缺少支付平台回调密钥，当前只影响真实回调验证

## 本轮完成

- 支付回调主链路和签名校验已实现

## 接下来

- 补齐失败重试并运行回归测试

## 交付信息

- 测试：支付模块 18 项全部通过
- 完成总结：<path>
```

Only the title always appears; omit any whole section with no effective content. Single-project section order is fixed: 项目进展 (progress) / 任务 (tasks) / 正在处理 (working on) / 需要关注 (needs attention) / 本轮完成 (completed this round) / 接下来 (next) / 交付信息 (delivery). Multi-project status uses `项目概览` (project overview) in place of `交付信息`. When `正在处理` and `接下来` would be identical, keep only `正在处理`.

### Content sources

- Title: the readable project name from `spec.md`; never a slug, stage, or command name.
- 项目进展: the real completed-task count from `tasks.md`; never process stages, gate counts, agent counts, or Git action counts.
- 任务: only the concrete project tasks from `tasks.md`, order unchanged. Status vocabulary is closed: `完成` (done), `进行` (in progress, at most 1), `阻塞` (blocked), `待办` (pending).
- 正在处理: the current real project task; no `boundary`, internal stage, or execution role.
- 需要关注: only concrete facts affecting requirements, delivery, quality, or external dependencies. Normal states, zero values, out-of-scope notes, and purely internal fixes are not listed.
- 本轮完成: only implemented, verified, or delivered project outcomes; never process actions like "entered check" or "gates completed".
- 接下来: the project action the agent will actually execute next. Never `/spec:*`, script commands, or "waiting for the user to start the next stage". Actions the agent can execute must be executed this round, not listed as user homework.
- 交付信息: final results, verification conclusions, delivery paths, commit or publish status. Technical detail appears only when it helps locate a problem or verify the delivery.

### Hard rules

1. Spec stages, slugs, branches, gates, and evidence counts are internal control information, not the user's narrative. Machine consumers read them from the task-package disk state or `route_spec_package.py --format json`.
2. Never show the `new → run → check → done → push` pipeline; never substitute stage completion for project completion.
3. Never hand internal gates, package maintenance, test fixes, or command relays to the user. Code, documentation, test, or gate fixes executable this round are never listed as user to-dos.
4. Only a project-shaping constraint, a decision that cannot be reasonably assumed, or an external dependency the agent cannot complete may request user input under `需要关注`; state the impact and the completed work alongside.
5. Incomplete verification is never written as project completion; a passed preflight is never written as published.
6. With more than 20 tasks, keep the first 20 and give a package pointer; with more than 5 attention items, keep the first 5 and give an on-disk pointer.
7. The output must let a user who knows nothing about the spec workflow answer directly: where things stand, what is being done, what the problems are, and what was finally delivered.
8. No empty-value placeholders or filler: `无`, `n/a`, `看起来` (seems), `整体` (overall), `顺利` (smooth), `建议可以` (maybe suggest), `温馨提示` (friendly tip), `如下所示` (as shown below), `让我` (let me), `我们来` (let's).

## Contents

- Invocation compatibility layer
- `/spec`
- `/spec:new`
- `/spec:run`
- `/spec:check`
- `/spec:done`
- `/spec:push`
- `/spec:update`
- `/spec:status`
- `/spec:goal`
- `/spec:doctor`
- `/spec:organize`

## Invocation compatibility layer

The user-visible entries remain `/spec`, `/spec:new`, `/spec:run`, `/spec:check`, `/spec:done`, `/spec:push`, `/spec:update`, `/spec:status`, `/spec:goal`, `/spec:doctor`, and `/spec:organize`. They only decide how the background advances; they never change the project-update structure.

`route` is the internal state resolver; the `tasks` view merges into `/spec:status`. Command names and stages may appear in error diagnostics or machine JSON, never as the title, tasks, or next step of a normal user reply.

## `/spec`

Run directly:

```bash
python3 scripts/route_spec_package.py --root <project> [--slug YYYY-MM-DD_<slug>] [--ascii] [--compact]
```

Route picks the follow-up action in the background. Show the user only the current project name, task completion, actual tasks, and real problems; with no active package, ask the user to describe the goal — never ask them to pick an internal stage.

## `/spec:new`

The agent fills the project update itself: title = the project the user wants done; tasks = the real development tasks already decomposed; 项目进展 = `0/N`; 正在处理 = the first executable task. Show a pending-decision item only when scope would reshape the project and cannot be reasonably assumed. Slugs, branches, and trio paths stay off the main view by default.

## `/spec:run`

The agent fills the project update itself: 项目进展 and 任务 come from `tasks.md`; 正在处理 = the current project task; 本轮完成 = completed and verified results; 接下来 = the next real project action. On a blocker, state the concrete impact, the completed work, and the external dependency — never show boundary or internal stages.

The main session executes tasks in dependency order inside this session, verifying item by item. User updates center on delivery facts. On verification failure, report the real cause and recovery conditions; never ask the user to fix code the agent can fix itself. Self-reported results are not acceptance: run the task's `verify`-described verification before checking off.

## `/spec:check`

Run directly:

```bash
python3 scripts/check_spec_package.py --root <project> --slug YYYY-MM-DD_<slug> [--ascii] [--compact]
```

Translate failures into project facts, e.g. "2 acceptance items remain unfinished", "regression-test evidence missing"; fixes are still written back by the agent and completed this round. On success, write the acceptance conclusion and valid evidence into 交付信息 — no gate counts.

## `/spec:done`

Run directly:

```bash
python3 scripts/complete_spec_package.py --root <project> --slug YYYY-MM-DD_<slug> --archive
```

Output the delivered content, the acceptance conclusion, and the completion-summary location. `遗留事项` (open items) never lists code, documentation, test, or gate fixes executable this round, and never stores free-text risks or external dependencies. Archiving, committing, and the later publishing continue by the agent; never ask the user for the next spec command.

## `/spec:push`

Run directly:

```bash
python3 scripts/push_spec_package.py --root <git-repo> [--branch <working-branch>]
```

Output the publication result only after the Git actions actually complete: the merged target, remote sync state, and branch cleanup state. When the remote is unreachable, write "local merge done, not yet synced to the remote, working branch kept" plus the impact; the preflight itself is never the delivery result.

## `/spec:update`

The agent fills the project update itself: tasks stay in real project order; 本轮完成 records the actual changes to requirements, scope, or acceptance criteria; 正在处理 and 接下来 record the affected project tasks. Never just report "updated the spec".

## `/spec:status`

Run directly:

```bash
python3 scripts/report_spec_package.py --root <project> --view status [--slug YYYY-MM-DD_<slug>] [--ascii] [--compact]
```

Single packages follow the unified project-update output. With multiple packages, each item uses `- <project name>: completed <done>/<total>; current <task>; blocked <impact>`, attaching only fields that exist; never show package stages or slugs, and never ask the user to pick the flow with `--slug`.

## `/spec:goal`

Plan, execute, accept, archive, and publish automatically from one goal sentence. Intermediate updates always follow actual task progress; never show the sub-stage chain. The final reply centers on delivered functionality, verification results, and publication status; stop only for real external blockers.

## `/spec:doctor`

Run directly:

```bash
python3 scripts/doctor_spec_environment.py --root <project> [--fix] [--format json] [--host claude]
```

The output is an environment health report, not the project-update schema: group by "needs repair / needs attention / auto-fixed / passed / info", each item listing its check id, factual conclusion, and (when present) the repair action. Needs-attention lists only concrete availability-impacting facts (missing command files, dangling symlinks, hooks pointing at paths that no longer exist); normal items get no prose. Repairs run via `--fix` and are logged; executable fixes are never left as user homework — only genuinely manual decisions (replacing a foreign same-name skill, no bash environment) state the impact and the equivalent command.

## `/spec:organize`

Run directly:

```bash
python3 scripts/organize_project_structure.py --root <project> [--check] [--format json]
```

The output is a structure-audit report, not the project-update schema: group by "repository inventory / reference graph (live vs historical) / deprecated candidates / finding classes / architecture consistency / summary", listing facts and candidates only, no preset conclusions. The default is a new task package in the target project before any move or doc fix (default new package). Do not append findings to an unrelated active package. Verdicts are keep / archive / merge / migrate. Never delete; archive confirmed orphans under `archive/retired`. A confirmed class is unfinished until every member is dispositioned. When `--check` fails, list the drift items truthfully and fix them this round; structure is declared trustworthy only after the architecture-consistency repair completes.

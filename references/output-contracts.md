# Output Contracts

Users read project facts; scripts read stage names. Reply with the goal, real progress, concrete problems and delivered results. Stages, slugs, branches and gate counts stay in machine output (`--format json`).

## Project-update schema

Section names are emitted by `scripts/dashboard_support.py`; keep them verbatim.

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

Only the title always appears; omit empty sections. Order is fixed as above; multi-project status uses `项目概览` instead of `交付信息`.

- 项目进展 / 任务: real counts and tasks from `tasks.md`, in order; status is one of 完成 / 进行 / 阻塞 / 待办.
- 正在处理: only work actually in progress. 接下来: the next action the agent will take — never a `/spec:*` command or user homework the agent can do.
- 需要关注: only facts that affect scope, delivery, quality or an external dependency, with their impact.
- 本轮完成 / 交付信息: verified results, commit or publication state, paths. Never write unverified work as done, a preflight as published, or a local commit as pushed.
- Keep at most 20 tasks and 5 attention items, then point to the package. Mark decisive conclusions 已核验 / 推断 / 未核验.

## Per command

| Command | Output source |
| --- | --- |
| `/spec` | `route_spec_package.py --root <project>`; with no package, ask for the goal |
| `/spec:new` · `/spec:run` · `/spec:update` | The schema above, filled from `tasks.md` |
| `/spec:check` | `check_spec_package.py --root <project> --slug <slug>`; failures become project facts and new tasks |
| `/spec:done` | `complete_spec_package.py --root <project> --slug <slug> --archive`; report delivery and the summary path |
| `/spec:push` | `push_spec_package.py --root <repo>`; report merge target, remote state and branch cleanup after they happen |
| `/spec:status` | `report_spec_package.py --root <project> --view status`; one line per project when several exist |
| `/spec:goal` | The schema; it stops at the local commit unless push was explicitly authorized |
| `/spec:doctor` | `doctor_spec_environment.py --root <project> [--fix]`; an environment report grouped by needs repair / attention / fixed / passed |
| `/spec:organize` | `slots/organize/scripts/organize_project_structure.py --root <project>`; a read-only structure report unless `--apply` |
| `/spec:autoplan` | `autoplan_gate.py discover|gate`; a planning report that ends with readiness and stops |
| `/spec:autorun` | `chain_state.py plan|status`; the schema plus one chain line: checked/total features, round, cap, stop reason |

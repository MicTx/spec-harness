# Orchestration Contract

The main session is always the orchestrator: it owns routing, acceptance and the `done`/`push` gates. Delegate only when a lane's goal, scope and verification can be written down; otherwise stay `local`. Core installs omit `slots/` and agent role documents — when one is needed and missing, say so and continue on the ordinary path.

## Route vocabulary

Exactly one token:

| Route | Use when | Topology |
| --- | --- | --- |
| `local` | Small, coupled, or the next blocker is faster on the main thread | Main session only |
| `explore` | Understanding is missing | 2–3 targeted explorers |
| `build` | Implementation with clean ownership slices | Disjoint workers; shared glue stays on the main thread |
| `review` | Code exists and risk dominates | Review lanes in parallel |
| `external` | Isolation or long-running execution is actually required | Upgrade only; no backend ships |

`python3 scripts/route_decision.py --root <project> --slug <slug>` derives the route from package structure only (open tasks, boundary modules, dependency width, risk labels). Zero structural signal routes `local`; risk labels route `review`. `--route <token>` or a `### 5.4 编排策略` route overrides it within the vocabulary.

## Operating model

The main session orchestrates; in-process helpers explore, review and take bounded implementation slices; the main session merges and verifies. Batch fan-out and review-fix loops are triggered by `workflow-runner` (`workflow_route.py`; `loop_route.py` defers those shapes), while `team-loop` keeps `batch-fanout` as an explicit initialization mode (`loop_state.py init --mode batch-fanout`) and is the degradation face when `workflow-runner` is unavailable.

## Assignment contract

Before starting a lane, write:

- Goal: the question or slice it owns
- Scope: files/modules it may change
- Excluded areas: what it must not touch; real secret values never enter prompts, arguments or evidence
- Output: findings or a patch summary
- Verification: the command it runs, or what stays unverified

Lanes never overlap write scopes. Wait for a lane only when the next decision depends on it. A lane's own report never completes a task: the main session runs the task's `verify`.

## Task-shape decision table（任务形状→执行面判定表）

人读面收敛：什么任务形状走哪条执行面。判定只用既有五 route 词表与既有 slot mode 名，不新增 token；脚本输出是建议，主会话保留最终判断（存疑取 `local`）。

| 任务形状 | 执行面（token） | 决策脚本 | 前置条件 | 降级路径 |
| --- | --- | --- | --- | --- |
| 局部 | `local` | `scripts/route_decision.py --root --slug` | 小而耦合、无跨模块并行收益；零结构信号一律 local（无文本长度回退） | 误判可用 `--route` 显式覆盖回正确 token |
| 探索 | `explore` + 2-3 条定向探察 sidecar | `scripts/route_decision.py --root --slug` | 多任务宽边界或依赖纠缠（依据=包结构，指标可解释），且 assignment contract 五字段可写清 | 契约写不清就收回主线程串行探察 |
| 并行扇出 | `explore` / `build` / `review` × workflow-runner（`batch-fanout` / `parallel-review` / `perspective-panel` / `review-fix-loop`） | `slots/workflow-runner/scripts/workflow_route.py`（仅强信号），且 `route_decision.py --root --slug` 判定 ∈ `explore` / `build` / `review` | route ∈ `explore` / `build` / `review`（README 书面激活域）；仓库驱动可读且选定 worker CLI 在 PATH | 前置不满足即降级为普通 sidecar 或 team-loop（workflow-runner README Hard preconditions），不静默伪造结果 |
| 循环收敛 | 任意 route × team-loop（`until-converged` / `fixed-rounds`）；评审-修复组合形态由 workflow-runner 承接（loop_route 显式让出） | `slots/team-loop/scripts/loop_route.py`（仅强信号：收敛绑定语义或显式轮数） | `loopRecommended=true`（命中即移交执行段）；弱信号单独不推荐，确定性降级为普通 sidecar 编排；agents-team 工具面已加载 | 工具面缺失时显式报错，不静默降级为单会话循环 |
| 外部隔离 | `external` | `scripts/route_decision.py --root --slug`（结构路由不自动给 external） | 实际需要隔离 git 状态/长时运行/多终端；由显式 5.4 route / `--route` 覆盖到达；本轮无外部后端 | 无外部后端时降级为 in-process 或串行交接，绝不中途发明控制面 |

共存合法性：`loopRecommended=true` 与 `route=local` 共存是合法语义，不是矛盾——slot 激活与编排路由相互独立（SKILL.md「Activation rule」），命中即把执行段移交受管 loop 协议（stages/run.md 第 3 步）。同理 `workflowRecommended=true` 只是建议：实际激活受 workflow-runner README 书面激活域与驱动前置检查约束，行为现状由 `tests/test_route_decision.py` 的组合语义回归钉守护。

## `### 5.4 编排策略`

Optional section under `## 5. 技术决策` in `spec.md`. When present, `check_spec_package.py` validates structure: `route` is a single token from the vocabulary above (backticks optional); multi-token values such as `build（主线程）+ review` are rejected; ownership / waiting strategy / verification gate live as sibling bullets, never inside the `route` token; absence does not block the package (local by default). Recommended shape (single-token `route` only), written from `route_decision.py` output plus judgment — never invent extra tokens:

```markdown
### 5.4 编排策略
- route: local
- immediate blocker: <what stays on the main thread>
- ownership: <bounded slices, or 主线程独占>
- waiting strategy: <when to wait, or 无>
- verification gate: <what must pass before finalizing>
- workflow: <编排面说明（可选）>
```

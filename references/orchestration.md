# Orchestration Contract

Parallel work is useful only when each item can be understood and checked on its own. This file defines the boundary between the main session, a bounded sidecar, and a managed slot. The main session is always the orchestrator — it owns routing, acceptance, and the `done`/`push` gates; delegation is allowed only after a route decision and an assignment contract are written down. If that cannot be written down cleanly, do not parallelize.

This file is the source of truth for the five-route vocabulary, assignment contracts, waiting/merge rules, and the optional `### 5.4 编排策略` section. Stage contracts live in `references/stages/<stage>.md` (`commands.md` is their index); slot-specific loop protocol lives in `slots/team-loop/README.md`.

## When to use

Use when the task spans multiple subsystems, the codebase shape is unclear and needs parallel exploration, independent ownership slices exist so workers would not collide, or the work touches risky areas (auth, payments, migrations, secrets, destructive operations) and needs an explicit review lane. Do not use when the task is small and local, the next blocking answer is faster on the main thread, or multiple workers would collide on the same small set of files.

## Route vocabulary

Exactly one of five tokens — never concatenate, never annotate in the token itself.

| Route | Meaning | Typical topology |
| --- | --- | --- |
| `local` | Small, tightly coupled, or the next blocker is faster locally | Main session only |
| `explore` | Understanding is missing | planner or main thread + 2-3 targeted explorers |
| `build` | Concrete implementation with clean ownership | disjoint workers by module/file; shared glue stays on the main thread |
| `review` | Code mostly exists; the priority is risk | `code-reviewer` + `security-reviewer` + `e2e-runner` in parallel when relevant |
| `external` | Isolation, long-running execution, or multiple terminals are actually required | upgrade only; this round does not ship an external backend |

Machine entry: `python3 scripts/route_decision.py --root <project> --slug <slug>` — the single routing entry, and its only routing input is package structure (open task count, boundary module coverage, dependency-graph width, risk labels). Output: `{route, basis, structure, score, reason, lanes, risks, override, channel_profile}`. Zero structural signal routes `local` — there is no text-length fallback; risk labels route `review`; a wide multi-surface package or a dependency tangle routes `explore`; a cleanly sliced mid-size package routes `build`; `external` is an upgrade condition the structure decision never auto-emits. Lane suggestions are drafts derived from disjoint boundary modules — the deterministic ownership check any delegation builds on; the main session still writes the assignment contract first. `--route <token>` (or a valid `### 5.4 编排策略` route, honored as `source: package-5.4`) overrides the structure decision and must stay in the vocabulary (exit 2 otherwise); overridden results recompute lanes/reason from the final route. An unreadable package fails closed — the router never guesses from text. `--text "<goal>"` is a read-only diagnostic, never a routing input. Structure routing is explicit and testable, not a substitute for judgment: when in doubt, stay `local`.

## Operating model

Enter `spec` first for complex work; the main session orchestrates; a route decision precedes any delegation; in-process subagents do exploration, planning, review, and bounded implementation slices; critical-path work and shared glue stay on the main thread; merge and verify in the main session; `external` only when isolation or long-running execution is actually required — with no external backend installed, downgrade to in-process or serial handoff, never invent a control plane mid-task. Sidecar orchestration is a documented optional mode: the routing scripts surface deterministic facts only (structure metrics, risk labels, disjoint ownership surfaces); turning them into parallel work always goes through the main session's written assignment contract.

The `team-loop` slot remains the managed protocol for loop-until-converged work; routing-level triggering of batch fan-out and review-fix loops belongs to `workflow-runner` (`workflow_route.py` — `loop_route.py` explicitly defers those shapes). The team-loop state machine still supports `batch-fanout` as explicit initialization (`loop_state.py init --mode batch-fanout`) and remains the degradation face for batch fan-out / review-fix loops when `workflow-runner` is unavailable; disambiguation lives in `slots/workflow-runner/README.md`.

## Assignment contract

Every lane must define, in writing, before spawn:

- Goal: the exact question or slice it owns
- Scope: files/modules allowed
- Excluded areas: what it must not touch; credential hygiene is part of this entry — real secret values never enter the contract text, lane prompts, command-line arguments, acceptance evidence, or distilled notes (pass them via env files / host-side injection instead; write examples with placeholders such as `<admin-token>`)
- Output: concise findings or patch summary
- Verification: what it ran, or what remains unverified

Preferred reusable agents (reuse before inventing roles): `orchestrator`, `planner`, `reviewer`, `confirmer`, `code-reviewer`, `security-reviewer`, `e2e-runner`, plus language-specific reviewers when the task is language-bound. Spec ships `agents/orchestrator.md`, `agents/planner.md`, `agents/reviewer.md`, and `agents/confirmer.md` as the routing/planning/review/confirmation contracts.

Host registration: hosts do not discover named subagents from a skill's own `agents/` directory. The Claude Code installer therefore publishes installer-owned copies of `orchestrator.md` / `planner.md` into `~/.claude/agents/` (marker-backed; user-owned files are refused without `FORCE=1`), and `spec:doctor` checks that publication (`claude.agents`). On hosts where a name is not in the native inventory, do not fail the lane: read the contract from the skill's `agents/` directory, pass it as the prompt to an available general-purpose agent, and note the substitution in the handoff.

## Task-shape decision table（任务形状→执行面判定表）

人读面收敛：什么任务形状走哪条执行面。判定只用既有五 route 词表与既有 slot mode 名，不新增 token；脚本输出是建议，主会话保留最终判断（存疑取 `local`）。

| 任务形状 | 执行面（token） | 决策脚本 | 前置条件 | 降级路径 |
| --- | --- | --- | --- | --- |
| 局部 | `local` | `scripts/route_decision.py --root --slug` | 小而耦合、无跨模块并行收益；零结构信号一律 local（无文本长度回退） | 误判可用 `--route` 显式覆盖回正确 token |
| 探索 | `explore` + 2-3 条定向探察 sidecar | `scripts/route_decision.py --root --slug` | 多任务宽边界或依赖纠缠（依据=包结构，指标可解释），且 assignment contract 五字段可写清 | 契约写不清就收回主线程串行探察 |
| 并行扇出 | `explore` / `build` / `review` × workflow-runner（`batch-fanout` / `parallel-review` / `perspective-panel` / `review-fix-loop`） | `slots/workflow-runner/scripts/workflow_route.py`（仅强信号），且 `route_decision.py --root --slug` 判定 ∈ `explore` / `build` / `review` | route ∈ `explore` / `build` / `review`（README 书面激活域）；仓库驱动可读且选定 worker CLI 在 PATH | 前置不满足即降级为普通 sidecar 或 team-loop（workflow-runner README Hard preconditions），不静默伪造结果 |
| 循环收敛 | 任意 route × team-loop（`until-converged` / `fixed-rounds`）；评审-修复组合形态由 workflow-runner 承接（loop_route 显式让出） | `slots/team-loop/scripts/loop_route.py`（仅强信号：收敛绑定语义或显式轮数） | `loopRecommended=true`（命中即移交执行段）；弱信号单独不推荐，确定性降级为普通 sidecar 编排；agents-team 工具面已加载 | 工具面缺失时显式报错，不静默降级为单会话循环 |
| 外部隔离 | `external` | `scripts/route_decision.py --root --slug`（结构路由不自动给 external） | 实际需要隔离 git 状态/长时运行/多终端；由显式 5.4 route / `--route` 覆盖到达；本轮无外部后端 | 无外部后端时降级为 in-process 或串行交接，绝不中途发明控制面 |

共存合法性：`loopRecommended=true` 与 `route=local` 共存是合法语义，不是矛盾——slot 激活与编排路由相互独立（SKILL.md「Activation rule」），命中即把执行段移交受管 loop 协议（stages/run.md 第 3 步）。同理 `workflowRecommended=true` 只是建议：实际激活受 workflow-runner README 书面激活域与驱动前置检查约束，行为现状由 `tests/test_route_cross_consistency.py` 的组合语义回归钉守护。

## Ownership, waiting, merge

- No overlapping write scopes unless a landing order is explicit; Shared glue files stay in the main session when possible
- Start independent lanes early; keep doing local non-overlapping work; wait only when the next decision is blocked on that result
- Merge handoffs into one coherent decision before editing shared boundaries; prefer merging two good lanes over spawning a fourth marginal lane; reuse an existing agent thread when the follow-up stays in the same scope

## Verification gate

Before finishing a risky change: re-read any shared boundary files touched by multiple lanes; run relevant tests in the main session; always add a security review lane when `risks` is non-empty (auth, payments, secrets, trust boundaries, destructive operations); prefer one explicit verification lane over optimistic reasoning; self-reported results never complete a spec task — the task's `verify` line must actually run.

## 独立复审与发现确认

Independent review is triggered by risk, never stacked unconditionally. Reuse the existing risk signals; do not invent new criteria.

- **触发定标（按风险，不无条件叠加）**：当 `route_decision.py` 输出的 `risks` 非空（认证/信任边界、计费/支付、密钥/令牌、数据迁移、破坏性操作——即 `_risks` 的既有五类，触发以 `risks` 非空为准、非穷举例举）或 route=review 时，`/spec:check` 的复查交给 `agents/reviewer.md` 契约的只读 sidecar：全新上下文、未参与实现（终局换眼）。local 小包维持主会话自查，按风险定标复核深度，不强行升级拓扑。
- **提问方式（问失败，不问批准）**：向复审者问「什么会弄坏它、还缺什么、用你自己的话复述这个方案」，绝不问「这行吗」。评代码的计划必须让复审者真读代码，不接受只看摘要的批准。
- **发现确认三路**（review lane 的发现不得以「已有人看过」自证）：① 命令能裁决 → 主会话跑命令，退出码即确认，记入验收证据；② 命令不能裁决 → 交 `agents/confirmer.md` 独立确认 lane：仅凭移交材料复现，禁编辑；③ 确认失败或未尝试 → 保留该发现并标注 `unconfirmed`（落 handoff `### Findings` 的 status 字段，不是处置枚举），绝不静默丢弃。
- **一交付物一机制上限**：计划配读代码的复审、发现配确认者、散文交付物配独立读——两种复核机制是天花板，超出必须写出理由。多端包级门禁（Stop hook、pre-commit/pre-push doctor、server 投影、archive 门）是包结构检查，不计入本预算。
- **lane 诚实出口**：检查超出 lane 能力、指令自相矛盾、或阻塞在只有发起者才知道的事实时，报告受阻并停在那条 lane，绝不伪造完成以通过检查；主会话按 lane-death 协议吸收该 lane，或转人工，不靠重派换一份更好看的报告。
- **独立 vs 校准**：定级、排序类判断走单 lane 串行（校准视角）；并行评审必须各配不同透镜——相关评审员的增益趋近于零。
- **join 最少化**：复审与确认 lane 的产出汇入主会话仍走「Ownership, waiting, merge」与 lane-death 吸收的最小汇入口径：只在下一决策被该结果阻塞时等待；移交传路径与行号引用，不整段誊抄文件内容（与 slice minimization 同口径）。

## Constrained-channel dispatch (shared-pool protocol)

Inference capacity is not always parallel-friendly: a single gateway with a single model pool enforces admission control — `gateway_queue_full`, `429 rate_limit`, `circuit_open`, instant `500`s with ~0 latency. That is a failure-recovery problem, not a reason to serialize. Throughput comes first: fan-out stays parallel; the channel profile only switches on the recovery protocol. Never serialize a shared pool "to be safe" — that trades throughput for nothing the protocol doesn't already handle.

- `route_decision.py` emits an advisory `channel_profile` (`{"shared_pool": bool, "note": ...}`) on single-channel signals (单网关 / 单模型 / 共享池 / 通道受限 / 单池 / limited gateway). It is advisory, carries no numbers, and `check_spec_package.py` never gates on it. There is deliberately no `max_parallel` or any quantity limit — efficiency first.
- On `shared_pool=true`, keep dispatching lanes in parallel and apply: slice minimization + the lane-death protocol + burst backoff.
- Slice minimization: shrink each lane's input — target files and line ranges in the contract instead of whole directories, trimmed context instead of full transcripts; the lane's point (isolated attention, fresh context window) survives.
- Lane-death protocol (any channel, mandatory on shared pools): (1) salvage — read the dead lane's partial transcript/handoff and absorb usable findings; (2) re-dispatch at most once with a minimized slice; (3) if the second attempt also dies, absorb the slice into the main thread and note the absorption in the handoff. Never re-dispatch a third time; three failures on one slice is a channel signal, not a worker problem.
- Burst backoff: after a cluster of transport-level failures (502/504 mid-stream, gateway 500s), pause dispatch briefly, then resume — the pause is recovery, not a throughput policy.
- The profile informs protocol, never quantity: assignment-contract, ownership, and verification rules apply unchanged, and lane count is decided by the work's shape, not the channel.

## Handoff format

```markdown
## HANDOFF: [lane-a] -> [lane-b]

### Context
[What was done]

### Findings
[Key discoveries or decisions]

Per finding (status is a field on the finding, not a disposition):

- where: [file:line or section anchor]
- what: [the finding in one sentence]
- evidence: [command + result actually run, or reference actually read]
- status: verified | unconfirmed

### Ownership
[Files or subsystem owned by this lane]

### Files Modified
[Files touched]

### Open Questions
[Unresolved items]

### Recommendations
[Suggested next steps]

### Verification
[Checks run / not run]

Two lines, both mandatory:

- Ran: [command] -> [result]
- Not run: [what] — [why]
```

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

## Existing teams

A room that already exists is reused, never rebuilt. One main coordinator owns the single package; each teammate receives an assignment contract below and consumes it — workers never create or adopt packages. Taking over work that is already running goes through the explicit adoption path in [handoff.md](handoff.md), which preserves the branch, index, and every teammate-owned byte.

## Anti-patterns

- Fuzzy scope, overlapping write ownership, or delegating the immediate blocker and idling
- Using `external` for work that fits in-process; inventing a control plane mid-task
- Concatenating route tokens or annotating the `route:` field
- Silent downgrade when a managed slot's tool surface is missing
- Serializing a shared-pool channel out of caution; shipping any `max_parallel` / lane-count clamp in routing output
- Re-dispatching a dead lane a third time instead of absorbing it into the main thread
- Asking a reviewer "is this OK?" instead of what would break it, what is missing, and a restatement; stacking another independent read on a finding that is already confirmed
- A lane faking completion to pass a check instead of reporting that it is blocked

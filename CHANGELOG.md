# Changelog

This file records user-visible behavior changes. For a detailed design or recovery story, follow the linked documentation; do not infer a new command or guarantee from a changelog bullet alone.

All notable user-facing changes to Spec Harness are recorded here. Internal development records are kept separately from the public documentation. Version numbers follow the policy in [RELEASE.md](RELEASE.md) § Versioning Policy (Semantic Versioning with an explicit 0.x interpretation; the deciding question is whether a compatibility surface moved, not whether behavior changed).

## [1.0.0] - 2026-11-10

### Changed

- **BREAKING — 两相 v3 运行时**：用户命令面收缩为 `/spec`、`/spec:push` 和 `/spec:doctor`；规划期先通过确定性 plan lint 和用户分叉，Go 之后按任务 DAG、边界和可重复 verify 执行。
- **BREAKING — Two-phase v3 runtime**: the user-facing command surface is now `/spec`, `/spec:push`, and `/spec:doctor`; planning first clears deterministic plan lint and user forks, then execution follows the task DAG, boundaries, and repeatable verifies.
- **自强化证据链**：验证记录绑定树指纹，任务勾选由脚本写入；失败签名驱动三级修复阶梯，lessons 进入下一轮规划，交付由最终树复验与边界审计收口。
- **Self-reinforcing evidence chain**: verification records bind tree fingerprints, script-owned checks write task state; failure signatures drive a three-step repair ladder, lessons feed future planning, and delivery closes with final-tree re-verification and boundary audit.

### Removed

- **BREAKING — v2 六阶段入口**：删除 `new / run / check / done` 独立命令、旧 v2 模板、阶段页和专用校验脚本；冻结归档与历史记录按原语义保留，不做迁移。
- **BREAKING — v2 six-stage entry**: standalone `new / run / check / done` commands, legacy templates, stage pages, and dedicated checkers are removed; frozen archives and historical records keep their original semantics without migration.

## [0.31.0] - 2026-11-10

### Changed

- **BREAKING — 六阶段内核**：用户命令面收缩为 `new / run / check / done / push / doctor`；`INSTALL_PROFILE=core|full` 仅作兼容别名，两者安装和导出完全相同的六阶段负载。
- **BREAKING — Six-stage kernel**: the user-facing command surface is now `new / run / check / done / push / doctor`; `INSTALL_PROFILE=core|full` remain compatibility aliases that install and export the identical six-stage payload.

### Removed

- **BREAKING — 旧编排治理**：移除规划链、编排路由、slot 扩展、代理角色契约、退役阶段页及其安装入口；冻结归档的任务包、规划文档、ADR 与历史 CHANGELOG 记录保持原样，仍可解析。
- **BREAKING — Legacy governance**: planning chains, orchestration routing, slot extensions, agent-role contracts, retired stage pages, and their installer entry points are removed; frozen task-package archives, planning documents, ADRs and historical CHANGELOG entries remain unchanged and parseable.

## [0.30.0] - 2026-11-09

### Changed

- **递归恢复闭环**：`check` 输出 `gateFailures`/`recovery`（update/run/environment/authority），阶段入口默认在同一会话内完成回写、修复与复验；只有真实用户授权或无法修复的外部阻塞等待用户。
- **Recursive recovery loop**: `check` now reports `gateFailures`/`recovery` (update/run/environment/authority), and stage entry points continue in the same session through writeback, repair and re-verification; only genuine user authorization or an unrepaired external blocker waits for the user.
- **自主调查与路由**：任务完成后仍待确认的 `[fact]`/`[reversible]` 问题继续调查；`[decision]` 缺 `authority` 时归入 authority；阻塞任务与独立就绪任务分开处理，包级阻塞仍停止执行。
- **Autonomous inquiry routing**: open `[fact]`/`[reversible]` questions continue to investigation after tasks finish; `[decision]` gaps route to authority; blocked tasks stay separate from independent ready work while package-level blockers still stop execution.
- **验证契约收紧**：空白机器验证命令被拒绝；已完成任务的无效 verify 契约可通过保留审计记录、重新打开并复验修复；v2 空成功标准不再由旧 checklist 掩盖。
- **Verification contract hardening**: blank machine verify commands are rejected; an invalid completed-task verify can be repaired by retaining the audit record, reopening the task and re-verifying; an empty v2 success-criteria section is no longer masked by a legacy checklist.
- **安装入口一致**：核心/完整安装的阶段命令、SKILL 与阶段文档统一为同会话恢复，不再要求先提交或暂存，只有 authority、外部阻塞或提交/推送门禁失败时停止。
- **Installed entry points aligned**: core/full stage commands, SKILL and stage pages now describe in-session recovery, no longer require a commit or stash first, and stop only for authority, external blockers or commit/push gate failures.

### Fixed

- **恢复分类边界**：修复任务内阻塞被当成就绪、错误分支仍建议执行、重复任务 ID 缺少结构化恢复、问题文本伪造 authority、以及未归属阻塞遗漏恢复等问题。
- **Recovery classification boundaries**: fixed ready selection over task-level blockers, execution advice on the wrong branch, missing structured recovery for duplicate task IDs, question text spoofing authority, and unowned blockers dropping recovery altogether.

## [0.29.0] - 2026-11-09

### Changed

- **BREAKING — 业务文本英文真源**：`SKILL.md`、`references/`、`agents/`、`slots/`、`scripts/`、`hooks/`、`install.sh`、agent-plugin 说明与运行时生成的全部业务文本改为精准英文；新包模板、新交付摘要、新 handoff 与完成摘要标题、门禁/路由/诊断输出统一英文。
- **BREAKING — English business source**: `SKILL.md`, `references/`, `agents/`, `slots/`, `scripts/`, `hooks/`, `install.sh`, agent-plugin descriptions, and all runtime-emitted business text now use precise English; new-package templates, new delivery summaries, new handoff and completion-summary headings, and gate/route/diagnostic output are uniformly English.
- **兼容性保留**：机器字段、JSON schema、状态文件值、旧中文包解析、归档文档、`release/` canonical 载荷与双语公开 README 契约不变；`SectionKey`/`SECTION_ALIASES`/`EVIDENCE_ANCHOR_KEYS`/`_METRIC_ALIASES` 等保留中文 canonical + 英文 alias。
- **Compatibility preserved**: machine keys, JSON schemas, state-file values, legacy-Chinese package parsing, archived records, `release/` canonical payloads, and the bilingual public README contract are unchanged; `SectionKey`/`SECTION_ALIASES`/`EVIDENCE_ANCHOR_KEYS`/`_METRIC_ALIASES` keep Chinese canonicals plus English aliases.

## [0.28.0] - 2026-11-09

### Changed

- **BREAKING — 默认轻量任务包**：`init_spec_package.py` 默认生成 LITE 模板（目标、关键假设、待确认问题、不做什么、最小路径、验证命令、成功标准）；完整意图与方案台账改由 `--workflow full` 显式启用，只对 FULL 包校验。`### 5.1` 只要求范围级别与快速检查，更高级别再要求对应检查；最小实现路径至少一条。
- **BREAKING — Lightweight default package**: `init_spec_package.py` renders the LITE template by default (goal, key assumptions, open questions, exclusions, minimal path, verification command, success criteria); intent and option ledgers move behind `--workflow full` and are validated only for FULL packages. `### 5.1` requires only the scope level and quick check, plus the checks a higher level needs; the minimal path needs one item.
- **精简技能文档**：`SKILL.md`、阶段页与参考文档按「防御准入四问」重写；`references/` 从 228 KB 降至约 62 KB，新增 `tests/test_budget.py` 与 `tests/test_ceremony.py` 卡住体积与仪式成本。
- **Leaner skill documents**: `SKILL.md`, stage pages and references were rewritten under the defense admission test; `references/` shrank from 228 KB to about 62 KB, and `tests/test_budget.py` plus `tests/test_ceremony.py` now hold size and ceremony budgets.
- **BREAKING — 链式执行只保留当前会话**：autoplan/autorun 改由新的 `scripts/chain_state.py` 记录进度（单个 `.spec/<chain>/chain.json`，同一步重入不计轮）。两条链省略旗标即无上限；`--max-rounds`/`--unbounded` 仍显式限轮。0.27 之前的链状态会被拒绝并提示删除后重开。
- **BREAKING — Current-session chains only**: autoplan/autorun record progress with the new `scripts/chain_state.py` (one `.spec/<chain>/chain.json`, re-entering the same step spends no round). Omitting the flag leaves both chains uncapped; `--max-rounds`/`--unbounded` still bound one deliberately. Chain state from before 0.27 is refused with an instruction to delete it and begin again.
- **证据只绑定命令与工作树**：`spec_verify.py` 不再记录包内辅助文件指纹，不再拒绝“空洞命令”，也不再因验证期间改动问题而作废记录；`check` 前跑一次 `run --all` 即可定稿。
- **Evidence binds to the command and the worktree only**: `spec_verify.py` no longer fingerprints auxiliary package files, refuses "hollow" commands, or voids a run whose questions changed meanwhile; one `run --all` before `check` settles the evidence.
- **路由只看包结构**：`route_decision.py` 删除 `--text` 文本启发式与 `channel_profile`，只在准备委派时调用。
- **Routing reads package structure only**: `route_decision.py` drops the `--text` heuristic and `channel_profile`; call it only before delegating.

### Removed

- **BREAKING — 原生会话发现**：删除 `spec_context.py` 及各宿主会话适配器；路由不再扫描宿主私有历史，续跑以任务包、`handoff.md` 与 Git 历史为准。
- **BREAKING — Native session discovery**: `spec_context.py` and the host session adapters are gone; routing no longer scans private host histories, and resumption reads the package, `handoff.md` and Git history.
- **BREAKING — server 模式**：删除 `server/` 适配器及其部署脚本。
- **BREAKING — Server mode**: the `server/` adapter and its deployment script are removed.
- **BREAKING — fresh CLI 链与窗口管理**：删除 `chain_session.py`、`chain_spawn_support.py`、`autorun_spawn.py`、`autoplan_spawn.py`、`chain_recovery.py`、`chain_lifecycle.py`、`sweep_chain_windows.py`；不再打开 Terminal.app/tmux 会话。
- **BREAKING — Fresh CLI chains and window management**: `chain_session.py`, `chain_spawn_support.py`, `autorun_spawn.py`, `autoplan_spawn.py`, `chain_recovery.py`, `chain_lifecycle.py` and `sweep_chain_windows.py` are removed; no Terminal.app/tmux sessions are opened.
- **BREAKING — 强制最高推理与决策守卫**：删除 `reasoning_policy.py` 与 `worker_decision_guard.py`；已关闭的 `[decision]` 仍须带 `authority: user:`，由 check 一处校验。
- **BREAKING — Forced maximum reasoning and decision guards**: `reasoning_policy.py` and `worker_decision_guard.py` are removed; a closed `[decision]` still needs `authority: user:`, checked once by `check`.
- **BREAKING — 激活守卫**：删除 `assess_spec_activation.py`、`activation_state.py`、`install_activation_hooks.py`；`hooks/claude_activation_guard.py` 保留为空操作至 0.28，`/spec:doctor` 会列出需要从 Claude settings 删除的旧条目。
- **BREAKING — Activation guard**: `assess_spec_activation.py`, `activation_state.py` and `install_activation_hooks.py` are removed; `hooks/claude_activation_guard.py` stays as a no-op until 0.28, and `/spec:doctor` lists the old entries to delete from Claude settings.
- **BREAKING — update-checkpoint 与机器回写**：删除 `update_checkpoint.py`、`update_checkpoint_support.py`、`check_issue_writeback.py` 与 `references/update-recovery.md`；遗留的 `update-checkpoint.json` 由 check/done 提示删除。
- **BREAKING — update-checkpoint and machine writeback**: `update_checkpoint.py`, `update_checkpoint_support.py`, `check_issue_writeback.py` and `references/update-recovery.md` are removed; check/done ask you to delete any leftover `update-checkpoint.json`.
- **push 精简**：删除 Gitea hook 修复（`--repair-gitea-hooks`、`--gitea-url`、`gitea_hook_repair.py`）、原生 Windows PATH 过滤和已到期的 2026-08-27 分支宽限；非 `spec/` 分支一律拒绝。
- **Leaner push**: Gitea hook repair (`--repair-gitea-hooks`, `--gitea-url`, `gitea_hook_repair.py`), native-Windows PATH filtering and the expired 2026-08-27 branch grace window are gone; non-`spec/` branches are always refused.
- 删除只断言文档措辞的测试与常驻面积测量脚本；保留的文档测试只检查链接与标题可解析。
- Tests that asserted documentation wording and the skill-surface measurement script are removed; the remaining documentation test checks only that links and headings resolve.

### Fixed

- **发布说明审计分类**：`release/RELEASE_NOTES.md` 与 `CHANGELOG.md` 一样按历史记录处理；版本说明记录的是该版本的树（含该版本移除的文件），organize 结构审计不再把其中的文件枚举误报为断链文档引用。
- **Release-notes audit classification**: `release/RELEASE_NOTES.md` is treated as history like `CHANGELOG.md`; the canonical notes describe the tree of that release, including files it removed, so the organize structure audit no longer reports those enumerations as dangling documentation references.

## [0.27.0] - 2026-11-09

### Added

- **检查问题回写钩子**：`check_issue_writeback` 把 check 门禁差距按 plan/apply 事务写回任务与修正日志，带弱身份去重、占位结论拒绝与收敛回滚；失败留下持久化事务标记，不静默吞错。
- **Check issue writeback hook**: `check_issue_writeback` plans and applies check-gate gaps back into tasks and the correction log as a guarded transaction, with weak-identity deduplication, placeholder rejection, and convergence rollback; failures leave a durable marker instead of silently swallowing errors.
- **公开导出密钥扫描门禁**：共享有界扫描器统一覆盖公开导出源文件、路径、归档成员与常见编码；命中、不支持或畸形容器、检查预算耗尽均 fail closed。
- **Public export secret-scan gate**: A shared bounded scanner now covers public export files, paths, archive members, and common encodings; findings, unsupported or malformed containers, and exhausted budgets all fail closed.

### Fixed

- **验证失败诊断安全可见**：失败与超时输出按字节上限与截断标记展示，控制字符与无效编码安全转义；成功保持静默，记录哈希协议不变。
- **Bounded failure diagnostics**: Failed and timed-out verification now shows stdout/stderr with byte limits and truncation markers, escaping control characters and invalid encodings; successes stay silent and the record-hash protocol is unchanged.
- **回写与恢复边界**：回写计划绑定 canonical 快照，异常或并发修改留下持久化拒绝标记供人工核验，不假称跨文件原子性；批处理逐项原子发布 checkpoint，显式缓存只在完整干净上下文复用；路由发现不完整时如实报告 partial，不把零候选伪称为无历史；大输入树指纹改为流式读取，普通静态树摘要与旧算法等价并拒绝特殊文件。
- **Writeback and recovery boundaries**: Writeback plans bind canonical snapshots and interrupted or conflicting writes leave durable refusal markers for manual reconciliation; fanout publishes per-item checkpoints atomically and explicit caches reuse only a fully clean context; incomplete route discovery reports partial instead of pretending zero candidates means no history; the streaming tree fingerprint stays byte-equivalent on ordinary trees while rejecting special files.

## [0.26.0] - 2026-11-07

### Added

- **最高原生推理请求**：受控的新会话与并行 worker 统一请求各模型已公布的最高推理档位，并记录控制层和限制；当前宿主仅执行深度工作指令，不把启动参数或提示词冒充模型实际推理效果。
- **Maximum native reasoning requests**: Managed fresh sessions and parallel workers request each model's highest advertised reasoning level and record control limits; current-host sessions use deep-work instructions without presenting flags or prompts as proof of actual model reasoning.
- **自主问题闭环**：可查证事实和范围内可逆选择绑定机器任务，填入结论并通过匹配验证后才能关闭，减少无必要的人工等待；权限、不可逆操作与真实偏好仍由用户决定。
- **Evidence-backed autonomous inquiry**: Discoverable facts and in-scope reversible choices bind to machine tasks and close only after a recorded conclusion passes matching verification, reducing unnecessary waiting while leaving permissions, irreversible actions, and genuine preferences to the user.

### Changed

- **BREAKING — 受控启动约束**：自定义原始 worker 命令与无法确认最高档位的启动被拒绝，旧链恢复也执行新策略；不支持最高档时不会静默降档，非受控宿主保持如实标注。
- **BREAKING — Managed launch policy**: Raw custom worker commands and launches without a verified maximum level are rejected, including resumed chains; unsupported maximum levels never silently fall back, and unmanaged hosts retain honest control labels.
- **深度规划与证伪**：规划和执行契约统一先调查上下游、比较最小方案、尝试证伪再验证，并复用已有假设与修正记录，避免增加第二套工作状态。
- **Deep planning and falsification**: Planning and execution investigate surrounding constraints, compare minimal options, seek counterevidence, and verify results using the existing assumption and correction records rather than a second workflow state.

### Fixed

- **人工决策保护**：新会话、并行 worker 和验证过程共用决策保护；执行中改写、关闭或重分类既有人工问题会留下异常并阻断续跑，强制终止后的未完成保护记录也不会被当作成功。
- **Human-decision protection**: Fresh sessions, parallel workers, and verification share decision guards; rewriting, closing, or reclassifying existing human questions during execution records failure and blocks continuation, and unfinished guards after forced termination never count as success.
- **结论与证据保鲜**：自主问题的结论纳入运行证据指纹，修改问题、选择或理由后必须重验；旧版无分类问题继续保持人工阻断，避免借重新标记绕过授权。
- **Conclusion freshness**: Autonomous conclusions are fingerprinted into execution evidence, so changing a question, choice, or rationale requires re-verification; legacy unclassified questions keep blocking for human review instead of allowing relabeling to bypass authority.

## [0.25.2] - 2026-11-06

### Fixed

- **推送恢复门禁基线**：断网降级后恢复推送时，变更范围与 footer 归属检查统一使用已锁定的远端主线基线；本地 main 已包含待推送分支时不再把本轮归档误报为旧包复用，并继续检查此前提交和其他变更包。真正的旧包复用仍会告警。
- **Push recovery baseline**: When a push resumes after an offline merge, change-scope and footer-attribution checks now share the locked remote-main baseline. A local main that already contains the branch no longer misreports the current archive as stale attribution, and earlier commits plus other changed packages remain covered. Genuine reuse of an older archive still warns.

## [0.25.1] - 2026-11-06

### Fixed

- **结构契约零豁免**：问题处置校验不再反向导入检查命令；follow-up 归档的门禁判定由调用命令显式注入，缺少判定器时直接失败而非放行。项目结构契约基线不再保留任何豁免，新的反向依赖会被 `/spec:organize --check` 直接拦截。
- **Zero-exemption structure contract**: Issue-closure validation no longer imports the check command; the calling command injects the follow-up archive gate explicitly, and a missing gate evaluator now fails instead of passing. The structure contract baseline carries no exemptions, so a new reverse dependency is rejected directly by `/spec:organize --check`.

## [0.25.0] - 2026-11-05

### Added

- **默认环境自检**：默认 core 安装现已包含 `/spec:doctor`，无需切换 full profile 即可检查并修复安装接线。
- **Default environment checks**: The default core install now includes `/spec:doctor`, so installation wiring can be checked and repaired without switching to the full profile.
- **已完成规划对账**：新增 `--reconcile-completed` 显式恢复路径，在同源 autorun 已完成、原范围全部勾选且归档证据完整时关闭旧规划；不替代新规划审查。
- **Completed-plan reconciliation**: The new explicit `--reconcile-completed` recovery path closes an old plan only when a matching autorun is complete, every original feature is checked, and archive evidence is intact; it does not replace review of a new plan.

### Fixed

- **运行证据绑定**：验证记录同时匹配实际命令、工作树、包辅助输入与包身份，阻止变更后的旧证据被继续当作通过依据。
- **Execution evidence binding**: Verification records must match the command, worktree, auxiliary package inputs, and package identity, preventing stale evidence from being reused after changes.
- **归档身份连续性**：归档移动后按记录生成时的身份核验原运行证据，避免已完成的任务包因目录移动在后续链路中被误拒绝。
- **Archive identity continuity**: Archived runs retain the identity under which they were recorded, so moving a completed package into the archive no longer causes a false rejection later in the chain.
- **完成证据补齐**：完成总结从有效运行记录生成闭合式机器成功声明，关键任务包契约错误附带稳定规则编号，便于定位缺项。
- **Completion evidence**: Completion summaries generate closed machine-success claims from valid run records, and key package-contract errors carry stable rule identifiers to help locate missing requirements.
- **恢复接管确认**：恢复旧规划的 `resume --reconcile-completed` 必须同时显式传入 `--takeover`；缺少确认时拒绝且不修改状态。
- **Recovery takeover confirmation**: Resuming an old plan with `--reconcile-completed` now also requires explicit `--takeover`; missing confirmation is rejected without changing state.
- **公开交付边界**：私有历史断言与公开测试分层，公开测试实际执行语义模块；发布门禁从私有记录动态派生禁止标识，清理公开注释与夹具中的真实身份。
- **Public delivery boundary**: Private-history assertions are separated from portable public tests, and export regression tests execute the semantic module; the release gate derives forbidden identifiers from private records, while public comments and fixtures use synthetic identities.

### Docs

- **可复制的入门练习**：新增从安装、首个任务到中断恢复的实操指南，配套预期输出、失败处理与可复现检出方式。
- **Hands-on onboarding**: A new copy-paste guide covers installation, a first task, and recovery after interruption, with expected output, failure handling, and reproducible checkouts.

## [0.24.0] - 2026-11-04

### Changed

- **BREAKING — 任务包验收契约**：LITE/FULL 包必须声明 `UI surface: changed|unchanged`；交互面变化必须绑定可重放的 Browser check 命令，并原样出现在 machine verify；完成总结的机器成功声明必须写成 `命令 -> exit 0 (runs:<task>)` 且匹配真实运行记录。
- **BREAKING — Package acceptance contract**: LITE/FULL packages must declare `UI surface: changed|unchanged`; a changed interactive surface must bind a replayable Browser check command that appears verbatim in a machine task verify; completion-summary machine success claims must use `command -> exit 0 (runs:<task>)` and match an actual passing run.
- **FULL 方案阶梯收敛**：FULL 只强制 B0/B1，B2/B3 按需出现且必须实质填写；四个 reading 固定为 literal ask / minimal change / recommended alternative / no new build。
- **FULL option ladder**: FULL requires only B0/B1; B2/B3 appear only when plausible and must be substantive; the four readings are fixed to literal ask / minimal change / recommended alternative / no new build.
- **数据作用域审查**：审查者现在检查候选集合是否只使用同一对象作用域的数据，避免把全量历史混进当前对象的结果。
- **Data-scope review**: Reviewers now check that candidate sets use data from the same object scope, preventing full-history data from leaking into a current-object result.
- **任务包意图与偏离**：`spec.md` 模板新增 literal ask、推断结果、假设格式、方案阶梯、修正日志和偏离日志；审查契约区分超出需要的改动、字面请求偏离和目标漂移，不可逆偏离必须获得本任务所有者授权。
- **Package intent and deviations**: The `spec.md` template now carries a literal ask, inferred outcome, structured assumptions, an option ladder, correction log, and deviation log; review distinguishes additions from literal-ask deviations and goal drift, while irreversible deviations require a task-specific owner grant.
- **LITE 契约补齐**：LITE 的五个 Intent 字段可各写一行短语；B0 与 B1 相同时可只写 `B0 is the minimal change.`；LITE/FULL 的修正和偏离日志无事件时必须保留一行 `none`。
- **LITE contract completion**: LITE Intent fields may each be one short phrase; when B0 and B1 match, the option ladder may contain only `B0 is the minimal change.`; LITE/FULL correction and deviation logs must retain one `none` line when empty.
- **按新信息停止修正**：检查循环不再默认三轮停止；每轮失败必须记录新输出、新定位或被证伪假设，重复无新信息会触发换策略或交由所有者决定。
- **Evidence-based correction stopping**: Check loops no longer stop after a fixed round count by default; every failed round records new output, a newly located path, or a falsified assumption, and repeated no-progress rounds change strategy or escalate to the owner.

### Added

- **失误方向统计**：完成总结新增 DEFERENCE / OVERREACH / KNOWLEDGE / PROCESS 计数，来源覆盖偏离日志、修正日志和结构化审查发现；只统计，不自动调整 Autonomy。
- **Mistake direction statistics**: Completion summaries now count DEFERENCE / OVERREACH / KNOWLEDGE / PROCESS from deviation rows, correction rows, and typed review findings; the section reports only and never adjusts Autonomy automatically.
- **外部事实证据**：影响计划的版本、API、弃用、安全、价格或配额事实必须记录一手来源、日期、反证检索和受影响假设；来源日期未知或无网络时保持 `UNVERIFIED-EXTERNAL`。
- **External fact evidence**: Plan-changing versions, APIs, deprecations, security notices, pricing, and quotas record primary sources, dates, counter-evidence searches, and affected assumptions; unknown source dates or unavailable network access leave `UNVERIFIED-EXTERNAL`.

### Fixed

- **默认日志遗漏**：LITE/FULL 包缺少修正日志或偏离日志时不再静默通过，空日志必须保留一行 `none`。
- **Default log omission**: LITE/FULL packages can no longer pass silently when a correction or deviation log is missing; empty logs must keep one `none` line.
- **重复章节绕过**：重复的修正日志、偏离日志或方案阶梯不再能绕过契约检查。
- **Duplicate-section bypass**: Duplicated correction logs, deviation logs, or option ladders can no longer bypass contract checks.
- **Autonomy 授权缺口**：偏离日志中的授权声明现在与 Autonomy 等级和可逆性交叉校验，缺少授权的偏离会被拒绝。
- **Autonomy authorization gap**: Authority claims in the deviation log are now cross-checked against the Autonomy level and reversibility, and deviations without the required grant are rejected.

### Docs

- **Bash 写入缺口调研**：新增 ADR 比较保守命令匹配、验收期路径边界和分层防护，推荐先用验收期 diff boundary，并给出翻转条件。
- **Bash write-gap research**: A new ADR compares a conservative command matcher, acceptance-time path audit, and layered guard; it recommends the diff boundary first and records its flip condition.
- **范围状态更正**：WS1 status: NOT APPLICABLE — WS1 only added guard tests；WS5 status: DEFERRED，学习方向维度待稳定反馈真源后再落地。独立复审的默认日志缺失、重复节绕过和 Autonomy 授权缺口均已修复。
- **Scope status correction**: WS1 status: NOT APPLICABLE — WS1 only added guard tests; WS5 status: DEFERRED until a stable feedback truth source exists. The independently reviewed default-log omission, duplicate-section bypass, and autonomy authorization gap are fixed.

## [0.23.0] - 2026-11-03

### Changed

- **接上次的工作**：在同一项目说“继续”，Spec Harness 会先查找相关助手会话；目标不清会先询问，明确禁读时不扫描历史。
- **Continue across assistants**: Say “continue” in the same project to find relevant earlier context; unclear goals trigger a question, and an explicit history-access ban skips the scan.
- **BREAKING — 入口顺序 / Entry order**：没有活动任务包但找到相关会话时，`/spec` 会先提示接续，不再直接建议新建。 / With no active package but a relevant session, `/spec` offers recovery before suggesting a new task.

## [0.22.3] - 2026-11-02

### Fixed

- **主动采用评测账本完整性**：执行器返回错误单位身份时不再把结果写成可结算记录，而是保留不可结算的隔离证据并保持未结算状态；后续恢复也拒绝异常历史行，避免污染完成集合或预算。
- **Benchmark ledger integrity**: a mismatched unit identity is now quarantined as unsettled evidence instead of being recorded as another unit's result; resume also rejects such historical rows so completion and budget accounting cannot be polluted.
- **基线检出身份复核**：复用已有 baseline worktree 时同时核对记录标记和真实 Git HEAD，避免漂移的检出被当作固定基线。
- **Baseline checkout verification**: reuse now checks the real Git HEAD as well as the marker, so a drifted checkout cannot be treated as the pinned baseline.
- **恢复文档兼容性**：更新恢复示例改为 Bash 3.2 可解析的路径边界判断，并保留失败关闭、元数据检查和索引不变约束。
- **Recovery guide compatibility**: the update recovery example now uses a Bash 3.2-compatible repository-boundary check while preserving fail-closed, metadata, and index-safety guarantees.

## [0.22.2] - 2026-10-31

### Fixed

- **Claude Code 主线程守卫**：以 `--agent` 或 settings `agent` 启动的主线程不再因携带 `agent_type` 而绕过主动准入；资格刷新与受管修改拦截继续生效，真正的子代理仍按宿主身份证据识别。
- **Claude Code main-thread guard**: a main thread launched with `--agent` or a settings `agent` no longer bypasses proactive admission merely because it carries `agent_type`; grant refresh and managed-edit enforcement still apply, while genuine subagents remain identified by host identity evidence.
- **Settings 隐私与原子写入**：activation/slot 安装器重写用户 settings 时保留原权限位，备份从创建起不比原文件更宽，失败不放宽、不残留临时文件。
- **Settings privacy and atomic writes**: the activation/slot installers preserve the original permissions when rewriting user settings, create backups no broader than the source, and leave neither widened modes nor temporary files on failure.
- **doctor 与安装接线一致性**：doctor 覆盖用户级和项目级 activation 接线、按宿主过滤输出、编译检查守卫文件；修复 hooks 时失败会回放已移走项，可安装判定与安装器一致。
- **Doctor and installer consistency**: doctor now covers user- and project-scoped activation wiring, honors host filtering, compiles the guard file, restores hooks if repair fails partway, and applies the installer's installability rules.
- **接管与 chain 收口**：符号链接锁、owner 解析、基线截断、非 spec 分支提示、archive 直属条目和 legacy 改名来源判定更严格；已接管包的 update 检查点与回滚说明只作用于包目录，不再误导用户 revert 恢复点。
- **Adoption and chain closure**: symlink locks, owner parsing, baseline truncation, non-spec branch guidance, direct archive entries, and legacy rename provenance are handled more strictly; update checkpoints and rollback guidance for adopted packages now act on the package directory instead of advising a recovery-point revert.
- **公开导出与评测证据**：公开导出树不再携带依赖 bench/ 的测试；离线评测计入全部模型用量、保留失败/超时证据、按真实事件判定建包与队友成功，并以递归 sandbox 禁读、私有 settings、树指纹和种子提交比对隔离单元。
- **Public export and benchmark evidence**: the public export no longer carries tests that depend on bench/; the offline benchmark counts every model's usage, preserves failure/timeout evidence, credits packages and teammates only from real events, and isolates units with recursive sandbox read denial, private settings, tree fingerprints, and seed-commit comparisons.

## [0.22.1] - 2026-10-28

### Fixed

- **跨轮准入刷新**：新请求使旧 direct/create/resume 资格失效，归档或切换分支后不能再借旧结论放行受管修改；合法同包续跑经重新评估仍可继续。
- **Admission refresh across requests**: a new request invalidates prior direct/create/resume grants, so an archived package or a branch switch cannot reuse an old verdict to authorize managed edits; legitimate work on the same package can continue after reassessment.
- **离线评测准备恢复**：团队准备期不再预置准入资格，业务测量前恢复单元私有 hook；恢复或回调失败会阻止业务请求、回收宿主进程并拒绝把准备用量计作完整测量。
- **Offline benchmark setup recovery**: team preparation no longer seeds an admission grant, and the unit-local hook is restored before measurement; restoration or callback failures block the business request, reap the host process, and reject setup usage as a complete measurement.

### Changed

- **关键判断验证**：在现有任务章节中，要求决定结论的少数不确定判断在实施前关联一手来源、推翻条件和独立判据；缺材料保留未核验，新目标先更新范围与未完成任务。执行记录和静态契约测试不等于语义正确或真实模型遵循。
- **Verification of decisive claims**: existing task sections now tie decisive uncertain claims to primary sources, falsifiers, and independent oracles before implementation; missing material stays unverified, and new goals first update scope and unfinished tasks. Execution records and static contract tests do not establish semantic correctness or live model compliance.

## [0.22.0] - 2026-10-26

### Added

- 主动启用：安装后 `/spec` 会主动判断多步工作是否值得建任务包——无 spec 术语的需求入口提示，加上首次受管修改/实施派工前的评估检查；判定区分 direct / create / resume / defer，defer 只表示证据或安全条件不足，不自动扩大授权。小修、只读、实验与显式退出的反例规则同步内置。
- 安装模式：`install.sh` 支持 `--mode on|off|keep` 显式启用、关闭或保留主动启用配置，安装与撤销幂等，第三方配置无损。
- `doctor` 扩展：把技能可见、配置已注册、宿主能力可用、live 已验证拆为四级诊断，不再把文件存在或静态测试当作已接线。
- 已有团队安全接入：复用现有队友身份与共享包；脏树与分支接管保留源改动，受保护分支和歧义路径拒绝，无隐式 stash/commit/checkout。
- Claude Code 宿主接入：激活判定 hooks（含 `claude_activation_guard`）随安装器接线，worker 不递归建包，敏感内容不进提示。

## [0.21.3] - 2026-10-23

### Fixed

- `doctor` 现在按 Git 自身解析定位 hooks 目录（`git rev-parse --git-path hooks`），尊重 `core.hooksPath`：门禁放在 `.githooks/` 等目录时不再误报「未安装磁盘真源 hooks」，`doctor --fix` 也会修复该目录里的过期 gate，而不是装入被架空的 `.git/hooks/`。`core.hooksPath` 指向仓库外（如用户级全局设置）时，提示会说明安装器拒绝写入，不再给出无效的修复命令。感谢 @wait4xx（#5）。
- 同类修复：codex worker 的沙箱可写目录在 linked worktree 中改为指向 Git common dir，不再指向 worktree 里的 `.git` 文件。
- `doctor` now locates the hooks directory through Git itself (`git rev-parse --git-path hooks`) and honors `core.hooksPath`: gates kept in a directory such as `.githooks/` are no longer reported as "disk-truth hooks not installed", and `doctor --fix` repairs a stale gate in that directory instead of installing a shadowed copy into `.git/hooks/`. When `core.hooksPath` points outside the repository (for example a user-level global setting), the hint explains that the installer refuses to write there instead of suggesting a command with no effect. Thanks @wait4xx (#5).
- Same class of fix: in a linked worktree, the codex worker's sandbox writable root now points at the Git common dir instead of the worktree's `.git` file.

## [0.21.2] - 2026-10-23

### Fixed

- 阶段契约、评审 agent 与教程不再把 `checklist.md` 当作新版任务包的必需验收面：`/spec:goal` 的停止条件改为验收面通过（新版包原条件永远无法满足），`run`/`new`/`update`/`check` 与 reviewer/confirmer 同步；验收面规则收拢到 `references/templates.md` 单一真源，README 与各阶段页改为链接。文档中的安装版本示例更新到当前版本。
- Stage contracts, review agents, and tutorials no longer treat `checklist.md` as a required acceptance surface for new-format packages: the `/spec:goal` stop condition is now "acceptance surface passes" (the old condition could never be met on v2 packages), with `run`/`new`/`update`/`check` and reviewer/confirmer in sync. The acceptance-surface rule now lives only in `references/templates.md`; the READMEs and stage pages link to it. Install examples in the docs point at the current version.

## [0.21.1] - 2026-10-23

### Fixed

- `doctor` 现在把由 `scripts/install_git_hooks.py` 渲染的 gate hook 识别为 Spec 所有（以 `checker=` 的文件名精确匹配为准），不再误报「项目自有 hook（未接管）」，`doctor --fix` 也能备份并重装过期 gate。感谢 @h565656445（#3）。
- 公开源码树的 `pytest` 不再在收集阶段中断：依赖未导出目录的测试不再导出，读取私有历史的用例在缺少该历史时跳过；新增导出一致性测试防止复发。感谢 @h565656445（#4）。
- `doctor` now recognizes gate hooks rendered by `scripts/install_git_hooks.py` as Spec-owned (exact `checker=` basename match) instead of reporting them as unmanaged project hooks, and `doctor --fix` can back up and reinstall a stale gate. Thanks @h565656445 (#3).
- `pytest` on the public source tree no longer aborts during collection: tests that depend on unexported directories are not exported, and cases that read private history skip when it is absent; new export consistency tests prevent regressions. Thanks @h565656445 (#4).

## [0.21.0] - 2026-10-22

### Added

- 新增 DeepSeek Harness（`dsh`）宿主：`INSTALL_HOSTS=dsh bash install.sh` 把 Skill 装到 `${DSH_HOME:-~/.dsh}/skills`，可用 `DSH_SKILLS_DIR` 重定位；未显式指定宿主时，本机有 `dsh` 命令或 `~/.dsh` 目录即被自动探测。`doctor`、安装一致性检查与公开导出规则同步，导出会排除 `.dsh/`。
- `scripts/package_agent_plugin.py` 新增 `--format dsh`：产出可经 `dsh plugin --profile <name> add ./spec-harness` 装入 profile 的 bundle（清单声明 `dsh.bundle.patch`，入口模块在 `ctx.skills` 注册 `bundled` 提供者）。目录安装与 bundle 二选一即可，不要重复装。
- 已在一个定制的 dsh 构建上验证 bundle：`skills/list` 返回 `spec`，不装时没有。官方 dsh 处于 developer preview，目录安装路径与官方文档一致但未逐版本实机验收。
- Adds DeepSeek Harness (`dsh`) as a host: `INSTALL_HOSTS=dsh bash install.sh` installs the skill into `${DSH_HOME:-~/.dsh}/skills`, relocatable with `DSH_SKILLS_DIR`. Without an explicit host list, a `dsh` command on PATH or a `~/.dsh` directory is auto-detected. `doctor`, the install parity check, and the public export rules are in sync; the export excludes `.dsh/`.
- `scripts/package_agent_plugin.py` gains `--format dsh`, which builds a bundle installable with `dsh plugin --profile <name> add ./spec-harness` (the manifest declares `dsh.bundle.patch`; the entry module registers a `bundled` provider on `ctx.skills`). Use either the directory install or the bundle, not both.
- The bundle was verified on a customized dsh build: `skills/list` returns `spec` with the bundle and omits it without. Upstream dsh is a developer preview; the directory path follows its documentation but is not live verified per release.

## [0.20.4] - 2026-10-22

### Added

- `chain close` 新增成对的 `--legacy-scope-commit` / `--legacy-completion-commit`，凭本地 Git 祖先链与同路径特征身份关闭没有 `feature_fingerprint` 的历史链；先用 `--dry-run` 预演，正常关闭检查与 current-session 守卫不放宽。
- 任务可选加一行 `claim: <认领者> <ISO 时间>` 声明谁在做；这是纯信息约定，非锁、无机器键、不进门禁，勾选即失效，归档保留。`engineering-philosophy` 同步写明协调边界：锁、在线检测、通知属于宿主层。
- `chain close` gains the paired `--legacy-scope-commit` / `--legacy-completion-commit` flags, which close a legacy chain that has no `feature_fingerprint` using the local Git ancestor chain and same-path identity; preview with `--dry-run`, the normal close checks and the current-session guard stay as strict as before.
- A task may carry an optional `claim: <claimant> <ISO time>` line naming who is working on it. It is an informational convention, not a lock: no machine key, never gated, stale once checked, kept in the archive. The engineering philosophy now states the coordination boundary: locks, presence detection, and notification belong to the host layer.

## [0.20.3] - 2026-10-22

### Added

- spec.md 的「2.5 已证伪路径」现在带机器键 `falsified-paths`，脚本可解析；`scripts/route_spec_package.py --brief` 新增只增字段 `falsifiedPaths`，恢复会话时一次读取就能看到已证伪的路线。「无」声明解析为空列表，该段仍不进任何门禁，空段合法；plain JSON 与面板输出不变。
- The spec.md "2.5 Falsified Paths" section now carries the machine key `falsified-paths` so scripts can parse it, and `scripts/route_spec_package.py --brief` gains an add-only `falsifiedPaths` field, so a resumed session sees ruled-out approaches in one read. A "none" declaration parses to an empty list; the section is still not gated and may stay empty, and plain JSON and the panel output are unchanged.

## [0.20.2] - 2026-10-22

### Added

- 新任务包的 spec.md 模板在 2.4 后增加可选段「2.5 已证伪路径」，用来记录方案级证伪（方案概述、证据锚点、日期），避免跨会话恢复或换 worker 后重试已被证伪的路线；该段无机器键、不进门禁，空段合法。`/spec:check` 增加一条是否重试已证伪路径的提示。
- The spec.md template gains an optional section 2.5 "Falsified Paths" after 2.4 for approach-level falsification records (approach, evidence anchor, date), so a resumed session or a new worker does not retry a route already ruled out. The section has no machine key, is not gated, and may stay empty. `/spec:check` adds a prompt to confirm no falsified path was retried.

## [0.20.1] - 2026-10-21

### Fixed

- 发布构建的 pytest 预检超时时会附带测试输出尾部，并按测试名与线程栈定位卡住的用例；行为与 0.20.0 相同，0.20.0 未完成发布。
- When the release build's pytest pre-check times out, the error now carries the output tail and names the stalled test with thread stacks; behavior is otherwise identical to 0.20.0, which was never published.

## [0.20.0] - 2026-10-21

### Added

- autoplan/autorun 新增当前会话执行路径：具备工作区读写、Python、Git 与交互工具的桌面客户端和 IDE agent 插件可直接规划、执行、暂停与恢复，无需为续跑另装其他 agent CLI。
- autoplan/autorun gain current-session execution: desktop clients and IDE agent plugins with workspace read/write, Python, Git and interaction tools can plan, execute, pause and resume without installing another agent CLI just to continue.
- 安装器与 doctor 增加 Devin、Cursor 宿主及 alias、full/core 载荷检查；公开宿主矩阵区分安装入口、执行形态和验证证据。
- The installer and doctor add Devin/Cursor hosts, aliases and full/core payload checks; the public host matrix separates installation, execution surface and validation evidence.

### Changed

- 客户端身份、运行形态与执行策略分开判断：桌面/IDE 默认 current；已确认的 Claude/Codex/Pi CLI 保留 fresh 新会话链。PATH 上存在某个 CLI 不再成为跨客户端跳转依据。
- Client identity, surface and execution strategy are resolved separately: desktop/IDE sessions default to current; identified Claude/Codex/Pi CLI sessions retain fresh-session chains. A CLI merely being on PATH is no longer a reason to switch clients.
- 当前会话采用 v3 检查点协议，变更请求必须携带 chain-id、owner 和 revision；早期 v2 current 检查点保留进度并安全升级。规划就绪不等于功能完成，暂停恢复不重复消耗已接纳轮次。
- Current sessions use the v3 checkpoint protocol with chain-id, owner and revision on mutations; earlier v2 current checkpoints upgrade without resetting progress. Planning readiness is distinct from feature completion, and resuming an admitted unit does not spend it twice.

### Fixed

- 拒绝旧代请求误操作新链、Git 定位环境覆盖导致的跨仓库状态分裂，以及摘要修复失败却返回成功；同时覆盖日志尾部恢复、并发接管、最后一轮恢复和真实归档证据校验。
- Reject stale-generation requests against a new chain, repository relocation through inherited Git environment variables, and success acknowledgements while summary repair still fails; recovery coverage also includes torn journal tails, concurrent takeover, final-unit resume and real archive evidence.

### Compatibility and validation

- current 能力随 full 安装提供；legacy fresh CLI 的参数、模型/权限和终端生命周期保持兼容。关闭客户端会停止 current 执行，Git 发布仍需明确授权；原生 Windows 请使用 WSL2。
- Current-session support ships with full installs; legacy fresh CLI arguments, model/permission controls and terminal lifecycle remain compatible. Closing the client stops current execution, Git publication still requires explicit authorization, and native Windows users should use WSL2.
- 自动化 profile、安装/导出和故障回归不等于客户端 GUI 实机认证；Cursor/ZCode 等桌面端的交互式实机验收仍单独报告。
- Automated profile, installer/export and fault-regression checks are not live GUI certification; interactive real-client validation for Cursor/ZCode and other desktop hosts remains a separate claim.

## [0.19.1] - 2026-10-19

### Added

- Spec 内部有界读取能力：任务切片聚合视图（`scripts/route_spec_package.py --format json --brief`）与证据分页查询（`scripts/spec_verify.py show`）由阶段与包状态内部调度，读取量大的复审与恢复场景可用更少调用取得同等信息；不注册新命令、不新增用户指令或参数，路由、验收与 Git 授权仍由主会话持有。
- Spec-internal bounded reads: an aggregate task-slice view (`scripts/route_spec_package.py --format json --brief`) and paged evidence queries (`scripts/spec_verify.py show`) are dispatched internally from the current stage and package state, so read-heavy review and resume scenarios reach the same information with fewer calls; no new commands, user instructions, or arguments are registered, and routing, acceptance, and Git authorization stay with the main session.

### Changed

- 常载 Skill 文本面压缩：SKILL.md 与 references 阶段页在保留结构、入口与行为契约的前提下同口径净减 1,357 B（skill −112 B、stages −1,245 B），并以收紧的 ratchet 上限防止无声回涨；四臂消融（基线 / 仅压缩 / 仅内部工具 / 组合）显示五类任务读取字节全部下降，静态面与读取链核对口径分列、不换算。
- The always-loaded Skill surface shrinks by 1,357 B at the same measurement aperture (skill −112 B, stages −1,245 B) with structure, entry points, and behavioral contracts preserved, behind a tightened ratchet that blocks silent regrowth; a four-arm ablation (baseline / compression only / internal tools only / combined) shows read bytes down in all five task classes, with static bytes and read-chain checks reported as separate layers and never converted into one another.
- autoplan 默认不再设 pass 数量上限：省略旗标即持续细化/复审直至就绪或出现真实停止条件，显式 `--max-passes N` 仍可用于主动限制，`--unbounded` 保留兼容；状态记 `max_passes: null`，历史有限记录不会被复活为上限。autorun 执行轮次上限（默认 5）、失败即停、单链互斥与权限/模型锁不变。
- autoplan no longer applies a default pass cap: omitting the flag keeps refining/reviewing until readiness or a real stop condition, explicit `--max-passes N` still restricts deliberately, and `--unbounded` remains supported; state records `max_passes: null` and historical finite records are never revived as a cap. The autorun round cap (5 by default), fail-fast, single-chain locking, and permission/model locks are unchanged.

### Fixed

- `scripts/route_spec_package.py --format json` 的三个输出分支现统一为 JSON（此前 brief/默认分支忽略 `--format json` 输出 markdown 面板），一致的机器可读输出由回归测试钉死。
- All three output branches of `scripts/route_spec_package.py --format json` now emit JSON (the brief/default branches previously ignored the flag and printed the markdown panel), pinned by regression tests.

### Internal

- 自托管 release runner 兼容：预检全量套件预算 600→1800s，链锁持有者探活改用 POSIX `os.kill(pid, 0)`（busybox `ps` 不支持 `-p` 时不再把活持有者误判 stale）；同步清偿发布门禁 lint/format 债并硬化容器差异测试（fixture 本地 git 身份、worker host 环境解析、测试替身 timeout 契约、`TODO.md` 协议工件标签）。
- Self-hosted release runner compatibility: the pre-build full-suite budget widens from 600s to 1800s and chain-lock holder liveness now probes via POSIX `os.kill(pid, 0)` (busybox `ps` lacks `-p`, which used to degrade live holders to "stale"); release-gate lint/format debt is cleared and container-divergent tests hardened (fixture-local git identity, worker-host env resolution, test-double timeout contract, the `TODO.md` protocol-artifact label).
- 发布候选以锁定记录冻结（候选 commit、95 载荷文件树摘要、四臂消融报告与沙箱导出矩阵回执）；本轮发布只消费锁定候选，不重测、不回写实现结论。
- The release candidate is frozen as a lock record (candidate commit, 95-file payload tree digest, four-arm ablation report, sandbox export matrix receipts); this release consumes the lock record only and neither re-measures nor rewrites implementation conclusions.

## [0.19.0] - 2026-10-15

### Added

- Pi 宿主原生命令面：安装器为 Pi 装配 prompt 模板命令（`/spec` 与全阶段 `/spec:<stage>` colon 布局，用户参数原样透传），`$spec` 文本约定升级为可补全的原生入口；doctor 新增 `pi.prompts`（模板存在性/阶段集/marker/形状）与 `migration.wrappers`（Codex CLI 迁移包装只报告）两查。
- Pi hosts gain a native command surface: the installer ships prompt-template commands (`/spec` plus every `/spec:<stage>` in the colon layout, forwarding user arguments verbatim), upgrading the `$spec` text convention to completable native entries; doctor adds `pi.prompts` (template presence, stage-set, markers, shape) and `migration.wrappers` (Codex CLI migration wrappers, report-only) checks.

### Changed

- SKILL 常载面精简：去除模板重复措辞并收紧引用清单，载面纳入棘轮基线，后续改动不再无声回涨；任务粒度与 verify 指引完善——每个任务的 boundary/verify 按完整配方（运行目录/前置条件/完整命令/成功判定/失败处理）填写。
- The always-loaded SKILL surface drops duplicated template wording and tightens its reference list behind a ratchet baseline so edits cannot silently regrow it; task granularity and verify guidance now spell out the complete recipe (working directory / preconditions / full command / success criteria / failure handling) for every task.

### Fixed

- 安装器自动探测修复：Pi 移出纯 skill 宿主清单后默认探测循环漏掉 Pi（无 `INSTALL_HOSTS` 安装不覆盖 Pi）——已修复并以目录探针与 marker 升级路径双测试钉死。
- Installer auto-detect fix: after Pi left the skill-only host list the default detection loop skipped it (a no-`INSTALL_HOSTS` install never covered Pi) — fixed and pinned by both directory-probe and marker-upgrade tests.

### Internal

- S6 评审场景独立预算与验收口径入基准套件；三处全量回归（SKILL organize 合同短语、bench 悬垂提及、计划文档链接提取器围栏盲区）由全量守卫拦截并修复；链轮次实测拦截探测缺口（撤包→修复→重规划先例入档）。
- The S6 review scenario gains its own benchmark budget and acceptance bars; three full-suite regressions (the SKILL organize contract phrase, dangling bench mentions, the plan-link extractor's code-fence blind spot) were caught by the full guard and fixed; and a live chain round caught the detection gap (withdraw → fix → re-plan precedent archived).

## [0.18.0] - 2026-10-14

### BREAKING

- 无人值守链默认不再提权：worker 继承父会话权限，`--permission-mode bypass` 才显式提权；默认轮数上限恢复为 5（autoplan pass 为 20），`--unbounded` 显式解除；规划特性指纹冻结，链中途新增未勾选特性拒绝接续。
- Unattended chains no longer escalate by default: the worker inherits the parent session's permissions and `--permission-mode bypass` opts in explicitly; the default round cap returns to 5 (autoplan passes: 20) with `--unbounded` as the explicit opt-out, and a frozen feature-set fingerprint refuses spawns when the plan grows mid-chain.
- update-checkpoint 机制退役：`/spec:update` 的中断恢复改由 Git WIP 提交承担（历史 checkpoint 文件只读识别不阻断）；`--gitea-token` 旗下线（认证走环境变量/凭据文件）；安装默认只装检测到的宿主并新增 `core/full` 安装 profile；`/spec:goal` 默认停在本地提交、`/spec:organize` 默认只审计。
- The update-checkpoint mechanism retires in favor of Git WIP commits for mid-flight recovery (historical checkpoint files are detected read-only and never block); the `--gitea-token` flag is gone (auth via environment or credential files); installs default to detected hosts only with new `core/full` profiles; `/spec:goal` stops at the local commit and `/spec:organize` defaults to audit-only.

### Added

- 可执行 verify 证据协议：任务包声明 `evidence: runs` 后，机器任务的 verify 命令由记录器（例如 `spec_verify.py`）真实执行并追加运行记录（例如 `runs.jsonl`，含工作树指纹），check/done 以「最近记录 exit==0 且指纹匹配」判通过——勾选必须真的跑过。
- Executable-verify evidence: packages declaring `evidence: runs` have machine-task verify commands actually executed and appended to a run log (e.g. `runs.jsonl`, with a worktree fingerprint); check and done pass only on a fresh `exit==0` fingerprint-matched record — a checked box now means a real run.
- 任务包 v2 格式：成功标准并入 `spec.md`（不再独立 checklist.md）、区块由稳定机器键识别（中英标题与 `<!-- spec:key -->` 锚点三径等价，别名只增不删）、结构化阻塞标记（blocked/blocker）；旧格式包按 legacy 双读，门禁结论不变；归档总结新增「提交区间」交付窗口锚点与 AGENTS.md 增量提议区块。
- Task-package format v2: success criteria move into `spec.md` (no separate checklist.md), sections resolve by stable machine keys (Chinese/English titles and `<!-- spec:key -->` anchors are equivalent; the alias table only grows), and structured blocked markers ship; legacy packages dual-read with unchanged gate verdicts; archived summaries gain a delivery-window `提交区间` anchor and an AGENTS.md incremental-proposal block.

### Changed

- 阶段契约文档按阶段拆分至 `references/stages/`（命令文档例如 `commands.md` 成为导航索引）；Git 门禁钩子检查器路径只由安装时渲染决定（未渲染模板 fail-closed）；集成分支允许 WIP 提交与交接；链运行时状态移出 Git 追踪。
- Stage contracts split into `references/stages/` (the command doc, e.g. `commands.md`, becomes a navigation index); gate-hook checker paths come solely from the install-time render (unrendered templates fail closed); integration branches accept WIP commits with handoff; chain runtime state leaves Git tracking.

### Fixed

- 无签名凭据的源码树自动跳过签名并新增 keyless 安装冒烟 CI；测试夹具不再依赖宿主会话环境标记。
- Keyless checkouts skip signing automatically with a new keyless install-smoke CI; test fixtures no longer depend on host-session environment markers.

### Internal

- 阶段 3 实测基准收官（7 场景 × 5 对照组 × 3 重复，105 真实单元）：全部假完成样本集中于无纪律对照组（2/42 对 0/63），证据纪律在全轴优于或平于旧核心流程——本轮格式与门禁变更由此定向。
- The stage-3 benchmark closed (7 scenarios × 5 arms × 3 repeats, 105 real units): every false completion landed in the undisciplined arms (2/42 vs 0/63) and the evidence discipline matched or beat the legacy core on every axis — the format and gate changes above are aimed by that data.

## [0.17.0] - 2026-10-12

### Fixed

- Linux/WSL2 自动规划与执行改用 tmux，支持会话重连；修复失败回滚残留 worker。
- Linux/WSL2 chains now use attachable tmux sessions; failed handoffs terminate lingering workers.

### Changed

- Windows 请在 WSL2 内运行；需 tmux ≥3.2 和 procps，macOS 保持原行为。
- Windows requires WSL2 with tmux ≥3.2 and procps; macOS behavior is unchanged.
- 软件代码改为 PolyForm Noncommercial 1.0.0；商业使用需另行书面授权。
- Software code now uses PolyForm Noncommercial 1.0.0; commercial use requires separate written authorization.

## [0.16.0] - 2026-10-10

### Changed

- **Planning documents live under `.spec/plans/`**: `/spec:autorun` and `/spec:autoplan` now discover planning documents only under the canonical `.spec/plans/` root — top-level `*.md` plus planning clusters (`*/master.md`, with `archive/` skipped) — so planning state sits with the rest of the project's spec memory instead of a repo-root `plans/` tree. An existing repo-root `plans/` tree is legacy and no longer scanned by default; `--plan` still names any document explicitly, and `python3 scripts/organize_project_structure.py` treats the whole `.spec/` tree (plans included) as historical evidence.

### Fixed

- **A refused spawn leaves no worker behind**: when an autorun or autoplan spawn is refused after its Terminal window opened — an unparseable window-open reply, or any failure between the confirmed window open and the recorded chain state — the just-opened window is now closed synchronously through the same escalated close helper the window recycle uses, behind a worker-identity interlock: only a window whose selected tab verifiably runs the chain's own worker is ever targeted, and a close that cannot identify the window skips honestly and records why (event kind `refusal_close`, fail-open; refusal messages unchanged). Re-running the chain resumes exactly at the refused round or pass with no duplicate executor; once the round/pass state is written the handoff is confirmed and the window stays open as the chain's legitimate worker.

## [0.15.0] - 2026-10-09

### Changed

- **Round and pass caps are opt-in with no default ceiling**: without an explicit `--max-rounds`/`--max-passes`, an autorun or autoplan chain now runs unbounded — no cap is computed, recorded, or forwarded (`max_rounds`/`max_passes` record as `null`, the next round's prompt carries no cap flag), and a cap recorded by an earlier spawn no longer binds later ones, so a large planning cluster or a long repair chain is never cut off mid-flight by a guessed number (the 0.14.0 adaptive default caps are removed). An explicit flag still bounds the chain exactly as before: the final spawn at the cap is allowed, only the one beyond it is refused with the same message, and the cap is validated, recorded, and forwarded; the chain line and `status` render an uncapped chain as `n (no cap)` and show the cap only when one is explicitly set.

## [0.14.1] - 2026-10-08

### Fixed

- **The push gate can no longer hang on a stalled network operation**: every git/subprocess call in the commit/push gate, the disk-truth pre-commit check, the changelog generator, and the structure auditor now runs with a bounded timeout (300s for network operations, 60s for local git). A stalled fetch/push/ls-remote fails inside the bound with a diagnosable error (`timed out within Ns` plus the rendered command) instead of blocking the whole workflow forever; best-effort steps time out silently, and the disk-truth gate degrades to its existing failure path.

## [0.14.0] - 2026-10-08

### Added

- **Recursive chains keep their model identity**: `spawn` accepts optional `--model <id>` / `--reasoning <level>` flags, and the first spawn locks that identity into the chain state — later rounds without flags inject the locked value into the worker command line (pi `--model/--thinking`, codex `--model` plus `-c model_reasoning_effort=`, claude `--model/--effort`), so every round of a long chain runs on the same provider-model-reasoning combination instead of silently falling back to the worker CLI default. A mismatch against the lock (different id, different reasoning level, or a host change while an identity is set) refuses the spawn with both values reported and nothing written; `status` shows a `model:` line with the locked identity and the latest injection level.
- **Dead chain windows can be swept**: `python3 scripts/sweep_chain_windows.py [--dry-run]` closes Terminal windows a chain spawned whose session ended without a recycle (a crashed round, a refused spawn). Membership is proven by the ttys recorded in the chains' spawn audit — live sessions, multi-tab windows, and the user's own windows are never touched by construction.

### Changed

- **Round and pass caps adapt to the workload**: without an explicit flag, the autorun round cap grows with the unchecked feature count (features + 25% + 3, floored at 20) and the autoplan pass cap with the master document's detail links (2 + 3 per detail, floored at 12), so a chain no longer dies at the cap with work remaining; explicit `--max-rounds`/`--max-passes` still win exactly and are recorded and forwarded.
- **Spawned windows inherit the parent window's place**: each new round's Terminal window takes over the spawning session's window position and size, so recursive chains stack in place instead of cascading across the screen.
- **A crashed worker no longer strands the chain**: the window-open osascript gets one bounded retry on a transient Terminal hiccup, and a spawned worker that dies instantly is detected — the dead window is closed quietly and the spawn refuses with zero state written, so re-running the chain resumes exactly there instead of recording a round that never runs.

### Fixed

- **Finished sessions are recycled deterministically**: the previous round's window now closes through a three-phase escalation — a natural-exit grace, then SIGTERM, then SIGKILL to the lingering session's process groups — instead of waiting out a timeout while an idle interactive session held the window open (the dominant accumulated-window case). The signal targets are identity-checked against the worker command line first, and an idle single-tab window closes without ever raising Terminal's cancel/terminate prompt.
- **Empty or whitespace-only `--model`/`--reasoning` values are refused** at the door: a degenerate identity can no longer lock a chain into a state no real model id can ever match.

## [0.13.21] - 2026-10-07

### Fixed

- **Long chains recycle their windows again**: the next round's session is confirmed by reading the process command line, so recycling works on every host CLI instead of only the ones whose process image carries their own name; rounds no longer wait out the full confirmation timeout on hosts where the check could never succeed.
- **A finished window is never closed out from under a live session**: the previous round's window closes only while it holds just that round's tab and the session has already exited; otherwise the window stays open, the chain state records why, and no cancel/terminate prompt interrupts the chain.
- **Planning paths containing spaces survive the chain**: the next round's prompt keeps `--plan` values quoted, so `docs/plan v2.md` no longer arrives as a truncated argument and the next round keeps planning from the intended document.

## [0.13.20] - 2026-10-07

### Changed

- **Serial fresh-session planning passes for `autoplan`**: framework, detail, and review planning now run as serial full agent sessions in their own Terminal windows (visible interface, session saved and resumable) instead of in-process subagents sharing the main session's context budget — a failed pass only loses that pass and the chain resumes from the `.spec/autoplan/` state; the safety rails (pass cap, single-chain lock, stop at the first failure) mirror the autorun chain.

### Fixed

- **Codex chain rounds no longer die at the commit stage**: the spawned codex worker keeps its `workspace-write` sandbox but carves the repository's `.git` back into the writable roots — newer codex builds deny `.git` writes under that sandbox, which stopped `git add`/`git commit` with "Operation not permitted" and halted the whole chain.
- **The chain continues on the host that started it**: worker-host resolution now detects the spawning session's CLI (codex/pi/claude) before falling back to PATH order, so a Pi session spawns Pi rounds instead of silently switching agents; every spawn records `host` plus `host_source` in the chain state for audit.

## [0.13.19] - 2026-10-07

### Changed

- **Interactive next-round terminal for the autorun chain**: every spawned round now opens as a full interactive coding session in the new Terminal window — visible interface, session saved and resumable — instead of a one-shot non-interactive run; you can watch each round live and step in at any time, and the chain safety rails (round cap, single-chain lock, stop at the first failure) are unchanged.
- **Terminal window recycling for the autorun chain**: once the next round's session is confirmed running in its new Terminal window, the previous round's window closes automatically a few seconds later — long chains no longer pile up one window per round; the close is best-effort (skipped with a recorded reason when the old session is not in Terminal.app or the new worker is not observed in time), so a failed handoff never leaves you without a terminal.

## [0.13.18] - 2026-10-06

### Added

- **Interactive recursive planning**: a new `autoplan` entry point for project start or a phase boundary — it interrogates the user in themed rounds to pin down the goal, delegates framework and detail planning to the planner sidecar until every feature is sized for one task package, and gates the resulting planning-document cluster before stopping at the readiness report; it is the planning entry and never starts the execution chain itself.
- **Machine-judged planning readiness**: the planning cluster passes a structural gate — qualifying documents carry feature checkboxes, detail references resolve, and a goal statement exists — so "ready for the autonomous chain" is a script fact, while cross-document business/data/flow consistency gets an independent review pass.
- **Broader planning-document discovery**: `PRD.md`, `docs/prd.md`, and `docs/design/*.md` now also qualify as planning documents for both planning and the autorun chain, alongside the existing plan/roadmap locations.

## [0.13.17] - 2026-10-06

### Added

- **Recursive autorun chain**: a new `autorun` entry point drives long projects across sessions — it finishes the active task package through the full run/check/archive/push gates, then plans the next round from the project planning documents and opens a fresh Terminal window in the same path with the prompt injected, recursing until every planned feature checkbox is checked.
- **Machine-judged completion and safety rails**: the chain reads feature checkboxes from conventional planning-document locations (or explicit `--plan` paths) so "all features done" is a fact, not a guess; a round cap and a single-chain lock keep the recursion bounded and strictly sequential, and any failed gate stops the chain with a resume-from-disk recovery instead of spawning the next round.
- **Planning gap fails closed**: when a project has no qualifying planning document, `autorun` refuses to loop and asks for project-level planning first, so the chain never invents its own scope.

## [0.13.16] - 2026-10-05

### Changed

- **Leaner check-round writeback**: a failing check item is now written back with two required fields — the failed item and its round disposition — plus one explanation line that only becomes mandatory when the fix is deferred or a risk is accepted; recording a failure as a pass remains forbidden, and stable task ids still deduplicate the same finding across rounds.
- **Default check-round budget**: the check→fix loop stops at three rounds by default, and a package may declare its own cap in `spec.md`; a package without a declaration runs on the default instead of being blocked for missing one.
- **One routing rule for execution slots**: the run stage now names a single source of truth for batch fan-out, parallel review, and review-fix loops — the repository-owned workflow-runner driver when a worker CLI is available, with team-loop as the fallback runtime — so the command contract no longer contradicts the orchestration contract.

### Added

- **Workflow startup validity gate**: `lint_workflows.py` now refuses workflow files that would fail GitHub's startup validation, and the public exporter applies the same check fail-closed before publishing a filtered tree, so a broken workflow is caught locally instead of as a zero-second failure on the public CI.

### Fixed

- **Public export keeps executable bits**: the public-tree exporter now preserves executable permissions, so exported scripts and hooks stay runnable straight after a clean clone.

## [0.13.15] - 2026-10-04

### Added

- **Session-boundary handoff documents**: task packages can now carry an optional `handoff.md` that moves work across collaborator boundaries — one person's sessions, teammates, a fresh agent session, or an agent cluster's lanes — without re-deriving the repository; the departing side appends one structured entry, and any successor resumes from a generated snapshot of branch, HEAD anchor, task progress, and next ready tasks.
- **Mode-adaptive entries**: the fields an entry must carry grow with the collaboration mode (solo → team or agent → cluster), so a single person records only context and the next step, while clusters additionally record ownership, waiting-on lists, recovery commands, and the lane table.
- **Freshness verdicts**: the `spec_handoff.py show` command compares the snapshot's HEAD and task-progress anchors with current disk truth and reports fresh, stale, or unknown, so a successor knows exactly what to re-verify; staleness is advisory and never blocks a stage.
- **Lifecycle integration**: package routing surfaces a handoff hint line, status rows append a one-line handoff summary, the check gate validates the document's structure whenever it exists, and archiving appends the terminal close entry that freezes the log as read-only history.
- **Safe by construction**: handoff writes are atomic and verified after landing with rollback to the previous content, hand-written notes cannot forge entries or fields, and packages without the document keep working exactly as before.

## [0.13.14] - 2026-10-04

### Changed

- **Guided issue intake**: bug reports and feature requests now use GitHub issue forms with required fields, a duplicate-search declaration, and copy-paste commands for version and environment details, so reports arrive reproducible instead of free-form.
- **Standard Code of Conduct**: the repository now ships the Contributor Covenant 2.1 official full text instead of the previous self-authored conduct section.
- **AI-assistance disclosure**: the pull request template now asks contributors to disclose AI-assisted work and how a human verified it.
- **Calmer first read**: the bilingual READMEs and the project introduction open with a one-line summary that matches the repository description, so the first screen states what the tool is before how it is laid out.
- **Health-at-a-glance header**: both READMEs lead with linked CI, release, license, and Python version badges, so project status is checkable before reading.
- **Bilingual repository description**: the GitHub and Gitea repository descriptions now carry the Chinese one-line summary together with its English gloss.

## [0.13.13] - 2026-10-04

### Fixed

- **Bounded fan-out execution**: worker output is truncated against the remaining cap and the whole process group (grandchildren included) is killed on limit or timeout, so parallel runs can no longer grow unbounded or hang.
- **Safer worker invocation**: prompts are always passed after a `--` argv separator, and an unreadable route file fails with a clear message and exit code 2 instead of a traceback.
- **Addressable Pi results**: the Pi backend's recorded `outputFile` now points at the real output dump written to disk.
- **Round-closing discipline**: a round that still has non-terminal tasks refuses to close, and new tasks can only be spawned as pending inside an open round.
- **Run-root discovery**: loop state only accepts explicit `.agents`/`.git` markers, instead of treating any parent directory that happens to contain an `agents` folder as the run root.
- **Fail-closed stop gate**: the loop stop guard blocks the stop when run state cannot be read, and corrupt run directories are rejected instead of waved through.
- **Stricter convergence**: a run converges only when every round decided pass and no round is open or failed; failed rounds now count toward termination.
- **Stale-task reaping**: stale in-flight tasks can be reaped with an explicit reason, backoff queries use the run's own retry configuration, and resume lists in-flight tasks with a reap hint.
- **Cleaner slot hook installs**: the slot hook installer also stamps a directory-level ownership marker, so residues from the old hook layout are detected and removed.

### Changed

- Relicensed the project from the custom non-commercial source license to the GNU Affero General Public License v3 or later (AGPL-3.0-or-later); the LICENSE file, package metadata, and source headers now carry the AGPL terms.
- Tightened the bilingual README into a router-style facade (condensed layout tree, merged the misplaced identity section into the capability list) and synced the public docs' install version pins and license statements.

## [0.13.12] - 2026-10-04

### Fixed

- The push closeout now accepts the `archive/retired/YYYY-MM-DD/` containers the organize command creates as historical paths: they no longer fail record-slug validation or the archived-bundle scan, so an organize round followed by push closes cleanly.

## [0.13.11] - 2026-10-04

### Added

- Added a reader introduction and a hands-on long-running task tutorial for AI learners, programmers, Agent developers, and long-running task developers.

### Changed

- Package checks reuse a cached scan of archived development records, so repeated gates finish almost instantly instead of rescanning every archive each run.
- The push closeout runs the repository gate once and sorts its findings, instead of repeating the same full check twice.
- Trimmed the skill entry document and reference guides down to rules that name their triggering stage, and folded the former operating-rules page into the remaining guides.
- The team-loop slot leaves batch fan-out and review-fix loops to the workflow-runner slot, so a batch request no longer routes twice.

### Removed

- Removed the legacy protocol import and task-id migration tools from the runtime package; protocol migrations never rewrite historical archives.

## [0.13.10] - 2026-10-03

### Added

- Added public Git documentation covering clone, branches, commits, pull requests, releases, and troubleshooting.
- Added public support, security, CODEOWNERS, Issue, and pull-request guidance.

### Changed

- Made the public release guide user-focused and removed private development infrastructure from reader-facing documentation.
- Added public-source metadata, GitHub community files, and documentation link checks.

## [0.13.9] - 2026-10-02

### Added

- Added the workflow-runner slot for bounded repository-owned fan-out execution.
- Added runtime package, plugin, and public-source export checks.

### Changed

- Renamed the distributable project to Spec Harness (spec-harness) while keeping spec as the compatible skill and command name.
- Included slots in runtime verification and release validation.

### Fixed

- Hardened output paths, task identifiers, cache files, symlink handling, process timeouts, and corrupted-input handling.
- Improved task-package lifecycle checks, archive recovery, and concurrent state updates.

## [0.13.8] - 2026-09-24

### Changed

- Consolidated workflow execution around the repository-owned runner and documented the current zero-host engine policy.
- Improved task-package routing, validation, and completion summaries.

## [0.13.7] - 2026-09-20

### Changed

- Refined installer host selection, runtime layout checks, and server-mode boundaries.
- Improved Git closeout checks and task-package evidence handling.

## Versioning

Spec Harness follows Semantic Versioning. Patch releases contain documentation, metadata, verification, and backward-compatible fixes. Minor releases add backward-compatible capabilities. Changes to command aliases, task-package formats, exported layouts, or behavior contracts require an explicit compatibility note.

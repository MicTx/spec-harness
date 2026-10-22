# Changelog

This file records user-visible behavior changes. For a detailed design or recovery story, follow the linked documentation; do not infer a new command or guarantee from a changelog bullet alone.

All notable user-facing changes to Spec Harness are recorded here. Internal development records are kept separately from the public documentation. Version numbers follow the policy in [RELEASE.md](RELEASE.md) § Versioning Policy (Semantic Versioning with an explicit 0.x interpretation; the deciding question is whether a compatibility surface moved, not whether behavior changed).

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

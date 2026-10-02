# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.13.9] - 2026-10-02

### Fixed
- **技能包安全与载荷门禁加固。** exporter/build 输出路径、fan-out 输入、slot registry、team-loop 状态、server 子进程组、CI/release slots 覆盖和 plugin/runtime payload parity 已纳入 fail-closed 验证；历史 0.13.8 payload 保留到独立发布轮重建。

### Changed
- **撤销宿主 Workflow 执行面。** 当前 workflow-runner 统一使用仓库自有 `workflow_fanout.py`；Claude Workflow 与 ZCode CreateWorkflow 不再是 active 路由、前置条件或导出能力，Pi/Codex 仅作为 worker CLI 后端。历史 mode C 记录保留在归档中。(`spec/2026-10-02_reverse-host-workflow-engine`)

## [0.13.8] - 2026-10-01

### Added
- **子进程扇出结果缓存。** 同一任务清单重跑时，未变动的条目直接命中包内缓存零重付，只有失败与超时的条目重新派发；全部命中时即使后端命令不在也能收敛成完整结果，断点续传不再要求重付全程；`--no-cache` 可强制全量重跑。(`spec/2026-10-01_add-fanout-result-cache`)

### Changed
- **0.13.8 发布轮（对齐载荷）。** mode B 缓存包提交后按 canonical 口径重建 `release/`，`tests/test_release_payload.py` parity 门全绿，pyproject == CHANGELOG == 载荷 VERSION 三方一致。(`spec/2026-10-01_release-0.13.8`)

## [0.13.7] - 2026-10-01

### Added
- **ZCode CreateWorkflow 执行面。** workflow-runner 在 ZCode 宿主可按三道同意门提交确定性编排，脚本与 run 台账随任务包落盘，缺少工具或确认时显式降级为普通 sidecar 编排。(`spec/2026-09-30_add-create-workflow-mode`)
- **编排资产随包落盘。** workflow 脚本与 run 台账（run 号、同意来源、终态、报告与产物指向）落进任务包的 `orchestration/` 目录随 git 走，断点续传一并可读、随包整体归档；改脚本用编辑再重跑、不整段重贴，一次性内联脚本不再丢失。(`spec/2026-09-30_add-orchestration-assets`)

### Changed
- **0.13.7 发布轮（对齐载荷）。** P1 两包提交后按 canonical 口径重建 `release/`，`tests/test_release_payload.py` parity 门全绿，pyproject == CHANGELOG == 载荷 VERSION 三方一致。(`spec/2026-09-30_add-create-workflow-mode`)

## [0.13.6] - 2026-09-30

### Added
- **独立复审按风险触发。** 触及鉴权、支付、密钥、信任边界或破坏性操作的包，复查交给未参与实现的全新只读复审者，问「什么会弄坏它、还缺什么」而不问「这行吗」；小任务维持主会话自查，不无条件叠加复核，同一交付物的复核机制有上限。(`spec/2026-09-30_add-independent-review`)
- **复审发现三路确认。** 命令能裁决的跑命令拿退出码，命令不能裁决的交独立确认者仅凭移交材料复现，两路都走不通的发现保留并标注「未确认」，绝不静默丢弃——「已有人看过」不再自证通过。(`spec/2026-09-30_add-independent-review`)
- **裁决性检查必真跑。** 验收前先盘点项目实际检查并按裁决力排序，最强一项在写下验收结论前至少真跑一次；存在而未跑的检查不许记「适用外」，记适用外必须附「该检查在本项目不存在」的检索依据。(`spec/2026-09-30_add-independent-review`)

### Fixed
- **无密钥 CI 的冒烟测试改用一次性签名凭据。** CI 装配步生成临时身份与 32 字节随机钥经 `GITHUB_ENV` 注入，smoke 测试的导出调用与 pytest 继承；不再因签名强制在无密钥检出上必红，真实签名材料不进 CI。(`spec/2026-09-30_fix-ci-smoke-signing`)

### Changed
- **0.13.6 发布轮（两段提交）。** 独立复审包提交后按 canonical 口径重建 `release/`，`tests/test_release_payload.py` parity 门全绿，pyproject == CHANGELOG == 载荷 VERSION 三方一致。(`spec/2026-09-30_add-independent-review`)

## [0.13.5] - 2026-09-28

### Added
- **电子水印签发。** 技能导出、发布载荷与插件包在 SKILL.md 内嵌不可见署名标记（内含 HMAC-SHA256 摘要），可核验副本来源；签发身份与密钥只保存在作者本地，检测脚本与密钥文件不随包分发。(`spec/2026-09-27_add-skill-watermark`)
- **缺签发凭据即拒发。** 正式导出、发布包与插件包在缺少签发身份或密钥时构建失败，不再静默发出未签副本；测试与无密钥检出必须显式 `--skip-signing` 声明，未声明跳过不会被误当成已签发。(`spec/2026-09-27_fix-watermark-required`)

### Fixed
- **organize 未引用代码：Python 按导入方的包根识别导入。** 绝对导入按导入方自己的 `sys.path` 根解析（最上层含 `__init__.py` 的目录的父目录，脚本则是它自己的目录），其次仓库根，再次全仓唯一的同名顶层包：`backend/` 不是包时，`from app.x import y` 命中 `backend/app/x.py`；两个服务各有顶层包 `app` 时互不救活，歧义的导入不算引用。注释、docstring 与字符串里的 import 字样不算导入，模块导入自己也不算。(`spec/2026-09-27_fix-organize-import-resolution`)
- **organize 未引用代码：JS/TS 无扩展名导入可解析。** 解析 `import … from`、`export … from`、`import 'x'`、`import()`、`require()`、`jest.mock()` 的模块说明符：相对说明符按导入方目录，别名按最近的 `tsconfig.json` / `jsconfig.json` 的 `paths` 与 `baseUrl`（跟随相对 `extends`，容忍注释与尾逗号；与 tsc 一致，`paths` 模式一旦命中就不再回落 `baseUrl`），依次尝试原路径（TS 导入方先试 `./x.js` 背后的 `x.ts`）、补 `.ts/.tsx/.js/.jsx/.mjs/.cjs/.d.ts`、目录 `index.*`。字符串与模板字面量里的 import 字样不参与解析；形似 import 语句的注释整条不算引用，带扩展名也一样。(`spec/2026-09-27_fix-organize-import-resolution`)
- **organize 扫描 `.tsx` / `.jsx` / `.mjs` / `.cjs`。** 这些后缀像其他文本文件一样参与路径提及与顶层目录引用图，也作为未引用代码候选。测试运行器按名收集的文件（`test_*.py`、`*_test.py`、`conftest.py`，以及 test/spec 标记紧挨 JS/TS 扩展名的 `*.test.ts` 等）不作候选；`*.d.ts` 只有全局脚本、带 `declare global` / `declare module "x"` / `export as namespace` 增强，或与同名 `.js/.jsx/.mjs/.cjs` 配对时才豁免，其余模块声明要有导入方。`render.test.utils.ts`、`deploy.test.sh` 这类形似测试的辅助文件照常报出。(`spec/2026-09-27_fix-organize-import-resolution`)
- **organize 路径提及剥离 shell 变量前缀。** `$PWD/tools/x.py` 按变量之后的 `tools/x.py` 解析，被引用文件不再误报为未引用，也不再产生 `PWD/tools/x.py` 式的伪悬空文档路径。(`spec/2026-09-27_fix-organize-import-resolution`)
- **organize 未引用判定口径成文：一跳引用计数。** 任一其他在用受跟踪文件导入或提及即视为在用，即使该文件本身已死；因此死链先报链头，归档链头后重跑才会报出下一环，互相导入的死文件环不会被报出。(`spec/2026-09-27_fix-organize-import-resolution`)
- **文档路径引用指向真实文件。** team-loop 先验文档 5 个裸文件名与培训书正文 `mini_harness.py` 引用补全为仓库相对路径（组装稿随源章节重写）；结构审计的悬空路径扫描跳过围栏代码块内的目录树展示名，教学目录树不再误报断链，正文真缺失路径照常报告，`.spec/artifacts/` 过程笔记纳入忽略。(`spec/2026-09-28_fix-doc-path-mentions`)
- **打标发布管线接上签发凭据。** release workflow 在构建前从仓库 secrets 物化签发身份与密钥（base64 临时文件、600 权限、跑后清理），缺 secret 时明确报错退出而不再产出「缺凭据」红灯；打标触发的正式发布工件恢复盖章出厂。(`spec/2026-09-28_fix-release-ci-signing`)
- **CI 与发布 workflow 去外部 action 依赖。** 自托管 runner 到 github.com 不通（DNS 污染致 `uses:` action 全部克隆超时），两个 workflow 改为全 run 步：git 直克隆本实例（令牌 URL 三形状回退+重试）、用镜像自带 python、release 改走 Gitea API（幂等先删后建）；CI 版本矩阵从 4 python × 2 OS 收缩为镜像单 python 3.12 × linux，3.9/3.10/3.13 与 Windows 覆盖暂失。(`spec/2026-09-28_selfhost-ci-workflows`)
- **CI 克隆与发布改走容器网关直达 Gitea。** 诊断 run 实测反代层切断大流量 git 传输（公网微型克隆 7 秒、网关直达 0 秒）；克隆与发布 API 改为「运行时推导 Docker 网关、`http://<网关>:3000` 直达优先、公网回退」，发布管线不再依赖反代健康度。(`spec/2026-09-29_direct-gitea-clone`)
- **CI 工具链离线装配经 Gitea 分发。** runner 镜像无 python/curl 且容器外网全断（apk 源、pypi、github 均不可达），CI 与发布新增离线装配步：从 `ci-toolchain-1` release 资产（4MiB×9 分片+manifest，sha256 校验 fail-closed）经网关下载 python3 apk 闭包与 pytest/ruff wheels 离线安装；发布步 API 调用改 python urllib（幂等先删后建）。pytest/ruff 为离线定版（9.1.1/0.16.9）。(`spec/2026-09-30_offline-ci-toolchain`)

### Changed
- **许可证改为不许商用。** LICENSE 从 MIT 改为署名个人、仅限唯一渠道发布、不许商用的条款，法律文本与水印载荷口径一致。(`spec/2026-09-27_update-license-noncommercial`)
- **分发包去身份明文。** 分发文件不再明文写入署名、邮箱与渠道字样，身份只保留在不可见水印里；不许商用条款仍写在许可证正文。(`spec/2026-09-27_fix-license-plaintext`)
- **分发面话术中性化与契约改名。** 进包文件只呈现中性的「发布签名 / distribution signature」概念；环境变量与旗标全仓统一改名 `SPEC_SKIP_WATERMARK`→`SPEC_SKIP_SIGNING`、`SPEC_WATERMARK_IDENTITY/KEY`→`SPEC_SIGNING_IDENTITY/KEY`、`--skip-watermark`→`--skip-signing`，旧名退役，引用旧环境变量的自动化流水线需同步更名。(`spec/2026-09-28_fix-watermark-wording`)
- **0.13.5 发布轮。** pyproject 单一版本源提升到 0.13.5，按 RELEASE.md 流程以 `build_release.py` 重建 `release/`（0.13.4 载荷退场、0.13.5 载荷进场），宿主版本证据锚点同步。(`spec/2026-09-27_fix-organize-import-resolution`)
- **0.13.5 发布轮（重新对齐 main）。** 0.13.5 载荷构建于 a00b256 后积累源码漂移致 parity 门转红；本发布轮折叠其后 6 轮进 `[0.13.5]` 节并按 canonical 口径重建 `release/`，`tests/test_release_payload.py` parity 门全绿，pyproject == CHANGELOG == 载荷 VERSION 三方一致。(`spec/2026-09-28_realign-release-payload`)

## [0.13.4] - 2026-09-26

### Added
- **发布说明条目式风格契约成文。** `references/changelog-guide.md` 新增「Release notes style」节：全篇条目按主题分组、一行一个能力（**加粗能力名** + 机制与保障，至多两句）、科普语气不出现内部代号、只写已落地行为、单版一屏为限；`build_release.py` 的 RELEASE_NOTES 润色提示词与契约同源对齐。(`spec/2026-09-26_release-notes-style-rule`)

### Changed
- **0.13.4 发布轮。** pyproject 单一版本源提升到 0.13.4，按 RELEASE.md 流程以 `build_release.py` 重建并锚定 `release/`（0.13.3 载荷退场、0.13.4 载荷进场），宿主版本证据锚点同步。(`spec/2026-09-26_release-notes-style-rule`)

## [0.13.3] - 2026-09-26

### Fixed
- **workflow-runner README 裸脚本引用补全仓库路径。** organize 结构审计（9 个评审组 + 对抗复核）确认 `slots/workflow-runner/README.md` 中 `workflow_route.py`（4 处）、`workflow_fanout.py`（3 处）、`route_decision.py`（2 处）为裸文件名引用，真实文件分别在 `slots/workflow-runner/scripts/` 与 `scripts/`；全部补全为仓库相对路径，负向 `rg '(?<!/)<name>\.py'` 复查零残留；`release/RELEASE_NOTES.md` 中的历史裸引用按惯例保留（历史发布记录不回写）。(`spec/2026-09-26_structure-audit`)

### Changed
- **0.13.3 发布轮。** pyproject 单一版本源提升到 0.13.3，按 RELEASE.md 流程以 `build_release.py` 重建并锚定 `release/`（0.13.2 载荷退场、0.13.3 载荷进场），`tests/test_release_payload.py` parity 门全绿，pyproject == CHANGELOG == 载荷 VERSION 三方一致。(`spec/2026-09-26_structure-audit`)

## [0.13.2] - 2026-09-26

### Fixed
- **engineering-philosophy 目录补节（O-1）。** Contents 索引补上正文已有「Single-orchestrator constraint」小节项，目录与正文重新对齐。(`spec/2026-09-26_lane-findings-closure`)
- **engineering-philosophy goal 链表述对齐（O-2）。** goal 链删去多余的独立 `commit` 环表述，改为 `done` 自含归档提交，与阶段清单及 done 定义一致。(`spec/2026-09-26_lane-findings-closure`)
- **orchestration 路由 JSON 枚举补全（O-3）。** `route_decision.py` 输出枚举补上 `channel_profile`（advisory 字段，检查门禁永不咬合），与归属段及实现一致。(`spec/2026-09-26_lane-findings-closure`)
- **orchestration 双 slot 取舍规则成文（O-4）。** team-loop 归属段补上 batch fan-out / review-fix-loop 双 slot 命中时的宿主取舍规则，并交叉引用 `slots/workflow-runner/README.md` 的 authoritative disambiguation。(`spec/2026-09-26_lane-findings-closure`)
- **commands retro-pack 时序统一（O-5）。** 归档门段落改为「pack → 各包 done(归档) → push」，删除互斥的「push first, archive after」表述，与全篇打包纪律一致。(`spec/2026-09-26_lane-findings-closure`)
- **commands protected branch 限定语修正（O-6）。** 分支限定语改为与 fail-closed 通则一致的表述（integration branch 记录于 spec.md，protected/detached/unmatched/double-bound 一律拒绝），消除「created from a protected branch」的含糊歧义。(`spec/2026-09-26_lane-findings-closure`)

## [0.13.1] - 2026-09-26

### Added
- **宿主 lane 级真实并行执行实测落库。** 按 workflow-runner 槽协议扇出 3 个评审 lane（engineering-philosophy.md / orchestration.md / commands.md 各一），`workflow_route.py` 决策输出与逐 lane 结构化结论（范围、结论、发现附引用行号）落库 `.spec/docs/2026-09-26_host-lane-e2e-evidence.md`；6 条发现全部经源文件回读核验，登记为观察项。(`spec/2026-09-26_consolidated-issue-closure`)

### Changed
- **0.13.1 发布轮：发布快照重新对齐 main。** v0.13.0 tag（5db0538）之后合入的 ruff format 归位、README 双语同步与勘误、锚点门禁 UX 修复此前均不在发布产物内；本版把 pyproject 单一版本源提升到 0.13.1，按 RELEASE.md 流程以 `build_release.py --expect-version 0.13.1` 重建并锚定 release/（0.13.0 载荷退场、0.13.1 载荷进场），`tests/test_release_payload.py` 20 项 parity 门全绿，pyproject == CHANGELOG == 载荷 VERSION 三方一致。

### Fixed
- **证据锚点门禁 UX：短 SHA fail-closed 并明示格式指引。** 锚点行存在且形如 `HEAD <sha> @ <time>` 时，SHA 必须为完整 40 位十六进制，否则该门判失败并报「证据锚点须为完整 40 位 SHA」附 `git rev-parse HEAD` 指引——此前 12 位短 SHA 被误判为「当前 HEAD 已前移至 <同一 SHA>」的含混反馈；无锚点与非 HEAD 形态行为不变，门级正负样本与 CLI 实跑负样本三用例入 `tests/test_check_spec_package.py`。(`spec/2026-09-26_consolidated-issue-closure`)
- **CI ruff format 回归归位与载荷勘误。** 归位 4 文件加载荷测试的格式漂移，CI 口径下 `ruff format --check` 重新全绿（11213b2）；勘误 sync-readme 包中「根 README 在发布包内」的不实记录（3c3612b——根 README 不在发布包内）。
- **README 双语同步 0.13.0 能力面。** README.md / README-en.md 补齐 0.13.0 六项能力（锚点门、fail-closed 通则、check 结构化回写、凭证卫生、路由判定表、workflow-runner 插槽）的双语描述（ee55aab），消除文档落后于版本面的漂移。

## [0.13.0] - 2026-09-25

### Added
- **证据新鲜度锚点门与证据完整性纪律。** 验收证据可携带「证据锚点：HEAD `<sha>` @ `<iso>`」：锚点存在即必须与项目仓库当前 HEAD 一致，HEAD 前移则 check/done 门禁以「证据过期需重跑取证」拦截；无锚点、归档复查、非 Git root 行为不变（可选门「出现即生效」先例）。templates/commands 同步补齐截断证据重跑与迁移运行行为证据两条写作纪律。(`spec/2026-09-25_add-evidence-freshness-anchor`)
- **门禁 fail-closed 通则与边界回归负样本门。** engineering-philosophy 新增 Gate design 小节：未运行=未通过（生成结束是运行事件、通过验收才是业务结论、达上限是终态但非成功）加四行选型表（静态结构→脚本门禁、需人审→check 轮、动态条件→hook、宿主副作用→显式授权）；checklist 模板新增「## 边界回归」可选门，越界/顺序交换/旁路三类负样本节出现即生效，并入证据重取联动；顺带修复 legacy 聚合中 consistency_ok 无条件参与的判定契约矛盾；commands.md 成文脚本输出通道契约（契约走 stdout、诊断走 stderr）。(`spec/2026-09-25_add-gate-design-doctrine`)
- **check 失败结构化回写、双重停止条件与过程指标沉淀。** `/spec:check` 新增四字段结构化回写（未通过项/证据缺口/下一步替代动作/处置枚举 fix_this_round|followup|accepted_risk）、respond 禁令句、「无新增项 + 最多 N 轮」双重停止条件与同一门禁连续 N 轮失败的止损归因（拆细标准/修 verify/转人工）；completion-summary 新增可选 `--process-metric`「## 过程指标」节，不传参不出现。(`spec/2026-09-25_add-check-writeback-stop-loss`)
- **凭证卫生纪律——秘密不进任务包、命令行、证据与蒸馏。** 真实凭证走 env 文件/宿主侧注入，不进 assignment contract、lane prompt、命令行参数、验收证据与蒸馏文本，示例一律用 `<admin-token>` 类占位符；四载体五字段枚举一致性守门（orchestration.md/SKILL.md/agents/orchestrator.md/workflow-runner README），SKILL.md 与 references 对五类高置信凭证模式零命中扫描，阴性对照证实断言真实咬住漂移。(`spec/2026-09-25_add-secret-hygiene`)
- **任务形状→执行面判定表与三层决策器组合语义回归钉。** orchestration.md 新增人读判定表（五行各含决策脚本/前置条件/降级路径）；route_decision→5.4 显式覆盖→slot 激活的三层组合语义由 `tests/test_route_cross_consistency.py` 钉住书面不变量（wf 推荐 ⇒ route∈explore/build/review）、显式负分支与 10 条实测三元组黄金白名单；workflow-runner README 核心协议对齐 assignment contract。(`spec/2026-09-25_add-route-decision-table`)
- **workflow-runner 插槽：执行段委派到确定性编排面。** 第二个内置槽：批量 fan-out、多维度并行评审、评审-修复循环可委派到宿主 Workflow 工具（Claude Code 内联 JS，需用户 opt-in）或 pi/codex 宿主的 `workflow_fanout.py` 子进程扇出（有界并发、逐项硬超时、JSONL 落盘真值）；与 team-loop 触发重叠时按宿主裁决；路由、验收与 done/push 门禁永不移出主会话。(`spec/2026-09-24_add-workflow-slot`、`spec/2026-09-24_update-workflow-slot-multi-host`)

### Changed
- **蒸馏目的地纪律成文并归位。** 项目经验只落执行项目自身 `.spec/docs/`，永不写全局载体（`~/.claude/CLAUDE.md`、用户级 `AGENTS.md`、用户级记忆）；全局记忆按此规则归位项目 docs，多宿主包与 check 轮文档同步融合该规则与分发面污染防范补记。(`spec/2026-09-24_update-workflow-slot-multi-host`)
- **0.13.0 发布收口：消除同版本双载荷。** v0.12.1 tag 之后合入的上述功能长期无版本号、无 CHANGELOG 条目，且已提交 0.12.1 tarball 只含其一，同版本号下两份字节不同载荷并存。本版把 pyproject 单一版本源提升到 0.13.0，CHANGELOG 按 v0.12.1..HEAD 逐提交归属补全，release/ 四件以 canonical flags（`--skip-checks --no-ai-notes`，与 `tests/test_release_payload.py` 同款）在 bump 提交上重新锚定。BUILD_INFO 内嵌 HEAD sha 与提交时间的自引用结构决定了锚定提交之后的终态重建会留在工作树绿色窗口（恰 3 个 M release/ 文件），属预期状态而非脏失误；历次 canonical 载荷簿记提交（5c36370、f5e3d38、79a30b5、2afe849、a941d28、f3dfac4）与 0.12.1 节发布说明润色链路（9276f17、29b090b）一并由此条归属。

### Fixed
- **spec.md 功能框门禁补漏与归档记录勘误。** 补勾三包 spec.md 十个已交付功能框、修正四条归档记录漂移；checker 新增 spec.md 功能框 fail-closed 门禁与负样本/正样本/空框等价三用例；follow-up 图按裁定接入 legacy-baseline 豁免——仅跳过 overall_check_passed 重评，结构校验与引用图完整性保留。(`spec/2026-09-25_fix-archive-errata-spec-gate`)
- **workflow_fanout codex 输出文件不再被 stdout 倾倒覆盖。** codex 宿主把会话转录同时打到 stdout 导致输出文件被覆盖的缺陷修复，缺陷教训已补记蒸馏。(`spec/2026-09-24_update-workflow-slot-multi-host`)

## [0.12.1] - 2026-09-24

### Added
- **受限推理通道的多 agent 并发分发。** `route_decision.py` 输出新增 `channel_profile` 字段：当任务声明运行在单网关/单模型/共享池通道时置 `shared_pool=true`，编排层据此应用切片最小化、lane 死亡恢复协议（保留部分结果 → 最多重派一次 → 主线程吸收）与失败风暴退避；并发分发始终保持并行，不设任何数量上限。通道画像仅为建议值，不影响任何检查门禁。
- **结构审计引用解析修正。** `/spec:organize` 的 dangling 路径检测改为从文档所在目录逐级向上解析裸名，并识别 HTML `src`/`href` 属性与 JSON 清单值两类文件接线；对含自包含子包的仓库，误报从 60+ 条收敛到仅剩真实缺失。同一仓库内的真缺失不再被误报噪音淹没。

### Changed
- **发布产物统一为 `release/` 目录。** 仓库直接跟踪 `spec-harness-{version}` 四件（tar.gz/zip/SHA256SUMS/RELEASE_NOTES），命名规则与此前一致；原销售物料包（落地页、社交图、定价文案）不再随仓库分发。构建转为确定性：同一源码状态产出逐字节一致的归档，`BUILD_INFO` 中的构建时间戳由最后一次提交时间派生。`tests/test_release_payload.py` 锁定仓库内产物与本地构建逐字节一致，升级前可自行校验。

### Fixed
- **电子书两处失效引用。** `book/src` 中指向已删除参考文档的链接改为指向其现行承接文档 `references/orchestration.md`；全量构建校验通过。

## [0.12.0] - 2026-09-22

### Fixed
- **checkpoint 与独立阻塞进入归档硬门禁。** active/corrupted `update-checkpoint.json` 使单包 check、全仓 check、complete archive 与 pre-push fail closed；独立 `阻塞：` 状态行纳入 `overall_ok`，不再能以全绿 triad 归档。(`spec/2026-09-22_fix-checkpoint-blocker-gates`)
- **`/spec:status` 最终投影残留修复。** 单包与多包 status 都在读取三件套前探测 active/corrupted update checkpoint，不可读三件套进入恢复输出；完成包只有 `GateResults.overall_ok` 才显示正常/可归档；依赖/编排风险不再把既有阻塞降级为风险；next-step 按 route 的 `spec_clarification_gaps` 原始顺序（定义缺口先、追加的依赖错误后）选择。新增默认无 `--slug` CLI、checkpoint 两态、blocked × risk、缺证据完成包和组合优先级反例。(`spec/2026-09-22_fix-status-gate-projection-followup`)
- **`/spec:status` 与 `route`/`check` 对同一门禁事实结论一致。** 单包 status 视图与多包总览改为消费 `compute_gate_results` 的同一 GateResults：非法 `### 5.4 编排策略`、任务依赖错误、独立 `阻塞：` 状态行、未完成 update checkpoint、证据门禁缺失不再被显示成 done/「整理交付结果并完成归档」；下一步建议按 route 相同优先级排序（checkpoint > 阻塞 > 待确认 > 定义缺口 > 依赖错误 > 编排策略），多包概览逐行投影并计入汇总告警。(`spec/2026-09-21_fix-orchestration-lifecycle-review`)
- **编排迁入假绿修复。** `### 5.4 编排策略` 不再被 sibling 文案中的“单线程/未启用/适用外”子串绕过，也不再被嵌套 `route` 覆盖顶层字段；重复顶层 route fail-closed；带标题的 5.4 区块错误会进入门禁展示而非“适用外”。认证/支付等风险信号在 review 路由下仍强制独立 `安全评审` lane。Claude Code 安装器把 `orchestrator.md`/`planner.md` 发布到 `~/.claude/agents/`（用户自有文件拒绝覆盖，`FORCE=1` 先备份），`doctor`/`smoke` 把 `route_decision.py`、`orchestration.md` 与两个 sidecar 合同列为运行时必需文件并实际调用路由 CLI。(`spec/2026-09-21_fix-orchestration-review-gaps`)
- **5.4 tokenizer 与 doctor native-agent 误伤。** 门禁与字段解析器共用同一顶层 bullet tokenizer，`- route : value` / tab 前缀不再被 opt-out 吃掉；`claude.agents` 只在 installer-owned Claude skill 上运行，用户自有 `~/.claude/agents/planner.md`、外来同名 skill、`--host` 不含 claude 不再 fail；孤儿 `.spec-skill-install` 可被二次安装替换。(`spec/2026-09-21_fix-orchestration-review-gaps`)
- **编排决策接入包上下文与 run 前阻断。** `route_decision.py` 支持 `--root/--slug`：包标题、未完成任务与 boundary/verify 进入启发式，合法 `### 5.4 编排策略` route 作为显式覆盖（`source: package-5.4`）；`--route` 覆盖后按最终 route 重算 lanes/score/reason，输出不再自相矛盾。`route_spec_package.py` 在 5.4 结构错误时不再给出 `stage=run`，status 阻断面与 JSON `orchestrationErrors` 均投影具体原因。(`spec/2026-09-21_fix-orchestration-lifecycle`)

### Added
- **编排能力有机迁入 spec（承接 subagent-orchestration 仓库）。** 新增 `scripts/route_decision.py`：任务文本机器判定为五路由（`local / explore / build / review / external`），输出结构化 JSON（route/score/reason/lanes/risks），风险信号自动追加安全评审 lane，`--route` 显式覆盖且拒绝多 token；新增 `references/orchestration.md` 编排契约真源（路由词表、assignment contract、等待/合流纪律、验证门禁、handoff 格式、`### 5.4 编排策略` 推荐形态）并挂入 SKILL.md 与 00-readme 索引；`agents/` 新增 `orchestrator.md`/`planner.md` sidecar 子代理契约（只读建议面，随导出树分发）。(`spec/2026-09-21_add-orchestration-core`)

### Changed
- **spec 执行模型从"单会话禁止委派"升级为"单编排者 + 受管委派"。** 六个载体（`SKILL.md`、`references/commands.md`、`references/engineering-philosophy.md`、`references/operating-rules.md`、`agents/openai.yaml`、`install.sh` 嵌入指令）同步改写：主会话永远拥有路由、验收与 done/push 门禁；`explore/build/review` 路由可在 route decision + assignment contract 下派发有界 sidecar lane；循环收敛类工作仍只走受管插槽协议；`/spec:run` 执行顺序并入 route_decision 挂钩。`### 5.4 编排策略` 从无消费的死约定升级为机器可判定：`check_spec_package.py` 对可选区块校验 route 必须单 token（容忍反引号与历史"未启用/适用外"显式 opt-out 写法），多 token 组合（如 `build（主线程）+ review`）被拒，无区块不阻塞；对全部历史归档 spec.md 扫描零回归。(`spec/2026-09-21_add-orchestration-core`)

### Removed
- **远程控制面整面移除。** 删除终端 agent、配对二维码、手机会话页与指令中继（`scripts/remote_agent.py`、`scripts/qr_encoder.py`、`server/remote.py`、`server/remote_page.html`）；`server.py` 不再挂载 `/remote*`，`install.sh` 不再写入 `REMOTE_STATE_PATH`。server-mode 只保留三端平台契约（`/start` `/result` `/health`）。(`spec/2026-09-21_remove-remote-control`)

## [0.11.0] - 2026-09-20

### Added
- **`/spec:push` 内置 Gitea 钩子修复通道（opt-in）。** push 命中远端 broken hooks 告警时，`--repair-gitea-hooks --gitea-url <base-url> --gitea-token <admin-token>`（env `SPEC_GITEA_URL`/`SPEC_GITEA_TOKEN` 回退）按 Gitea 官方 FAQ 依次调用站点管理 API `POST /api/v1/admin/cron/{task}`（`sync_repo_branches`、`sync_repo_tags`、`resync_all_hooks`），成功后整条 push 计划自动重跑一次；修复失败或重跑再报损坏则 fail-closed 保留工作分支。无开关时行为与旧版完全一致。(`spec/2026-09-20_fix-gitea-hook-repair`)
- **本地推送计划legacy 分支提示一致性。** local-only（远端不可达降级）计划路径补齐 legacy 分支 rename 建议，三种计划模式注记一致。(`spec/2026-09-20_fix-push-local-only-legacy-note`)
- **`/spec:organize` 结构整理命令。** 新增结构维护入口：`scripts/organize_project_structure.py` 以只读方式产出仓库盘点、顶层引用图（区分活引用与历史引用）、废弃候选（无活引用目录、陈旧目录、大型二进制、zip 与目录重复、未跟踪未忽略路径）与架构一致性四类机器事实；第一性原理判定由 agent 落到定论知识文档，结构变更仍走任务包门禁，模块间引用路径同步更新后以引用重扫、全量测试与导出比对验证。`--check` 在 module-index 声明文件缺失或 md/mmd 图不一致时硬失败（退出码 2），Claude Code 侧同步生成 `/spec:organize` 命令文件（install.sh 与 doctor 的 USER_STAGES 同步扩到十个）。(`spec/2026-09-19_add-organize-command`)
- **架构声明与 README 双语的机器防线。** 新增测试：module-index 每个 Primary file 必须存在、module-dag.md 与 module-dag.mmd 节点边必须一致、README/README-en 导出树必须与 `export_skill_package.py` 实际输出逐文件一致；文档失真从此是测试失败而不是口头发现。

### Changed
- **team-loop 通信协议多宿主适配。** 协作协议从 pi 单通道绑定重构为宿主无关语义层（交底、决策问答、应答、查等待者四动作）+ 按宿主适配表：pi（pi-intercom）、claude（原生 agent/teams 消息面）、codex（有界轮询）三行主力 + 其他宿主通用降级行；防假死三不变量（有界超时、不忙等、落盘）对所有宿主一字不差，`slots/team-loop/scripts/`、hooks 与 manifest 零改动。(`spec/2026-09-20_multi-host-collab-adapter`)
- **Agent 模型钉死清理与会话拓扑标准指引。** 28 处 agent frontmatter 的 `model` 钉死全删，29 文件全部缺省继承宿主默认模型（备份留档 evidence）；team-loop README 新增「会话拓扑与模型继承」标准指引（supervisor 常驻、worker 一次性，不传 provider/model，附升级通道说明），上一轮遗留的两个「需要关注」限制点全部 resolved_current。(`spec/2026-09-20_fix-noted-limits-and-model-inheritance`)
- **运行时文档英文化，消除中英混杂。** `SKILL.md`、`references/` 10 个文件、`slots/team-loop/` 文档、`server/README.md`、`agents/openai.yaml` 与 `install.sh` 命令描述统一为英文；四类机器契约字面量——templates init fence 内容、dashboard 节名、changelog 条目格式、`**验收结果**：通过` 验收字面量——保留中文原文并附英文括注，check/complete 门禁解析零影响（全门禁构建与全量测试通过验证）。(`spec/2026-09-20_unify-skill-language`)
- **架构声明重写为磁盘真源。** `.spec/architecture/module-index.md` 与 `module-dag.md`/`.mmd` 此前仍描述已移除的 Harness 控制平面、swarm 台账、终端 Worker、三个 HUD、调度器等约十个不存在模块及约二十个不存在文件，且 md 与 mmd 互不一致；现重写为当前 20 个模块（新增 Slot Extension Mechanism、Structure Audit Command、Book，移除全部已退役子系统与 Training Materials）。
- **README 双语真源同步。** 仓库树补齐 `book/`、`slots/`、`references/slots.md` 与新增脚本，移除 `training/`；导出树按导出器机器规则逐文件重列（含 `pyproject.toml` 与 `slots/`）；导出规则散文从“三个打包器”更正为五个源码库独有排除项；回归验证命令与 CI 对齐（ruff 路径补 `book/`）。

### Fixed
- **门禁误报收敛。** `check_all_spec_packages` 对历史归档的实现只读兼容，不再被当代门禁回判（`spec/2026-09-20_fix-check-all-legacy-compat`）；任务包扫描器排除否定式待确认词并放宽待+主体动词间隔，修复「无（已确认…）」被误判为待确认项的假阳性（`spec/2026-09-20_fix-pending-decl-false-positive`）。
- **销售资料包 payload 与源 canonical parity 恢复。** tracked 交付物在源码演进后按既有配方重建，`verify_sales_kit.py` 双模式退出码 0（`spec/2026-09-20_fix-sales-kit-payload-drift`）。
- **修复 team-loop 合并遗留的 CI 阻塞。** `scripts/install_slot_hooks.py` 与 `tests/test_install_slot_hooks.py` 的 4 处超长行、`tests/test_slot_registry.py` 的 2 个未用导入、`scripts/slot_registry.py` 的格式漂移被修正；`ruff check`/`ruff format --check` 在 CI 同口径下重新全绿（此前 main 的 Ruff check 步骤必失败）。
- **修复插槽 hook 安装器写出的 settings hooks schema 不被 Claude Code 接受。** 旧版 `install_slot_hooks.py` 把 hook 条目直接写在 `hooks.<事件>` 数组下，缺官方 matcher 组的内层 `hooks` 包装，Claude Code 报「Hook matcher "hooks" must be an array of hook entries」并整组忽略——已安装的 team-loop 四个 hook（UserPromptSubmit/Stop/TeammateIdle/TaskCompleted）从未生效。安装器改写为官方组 schema；重装时按相对标记识别回收旧版裸条目（含旧安装根路径）并自动迁移，他人的组与裸条目原样保留不动；`--remove` 兼容新旧两种形态。回归新增：官方 schema 断言、旧格式迁移、混合组只回收自己的。`slots/team-loop/tests/recheck_probes.py` F 节同步改为官方 schema 断言。销售资料包交付载荷随源码导出哈希同步刷新。
- **销售资料包交付载荷对齐 0.10.0 并补交付物漂移防线。** `/spec:organize` 结构审计（同日晚轮）发现：0.10 单会话重构（`1236516`）更新了 kit verifier 的产物命名模式（`spec-harness-*` → `spec-*`）与 release notes，但未刷新交付载荷——`product/` 仍携带 `spec-harness-0.8.0.*` 旧归档，`verify_sales_kit.py` 在源码树上实际退出码 1，外层 zip 落后目录 2 个文件，kit README/DELIVERY-NOTE/landing 版本戳仍写 0.8.0；既有 `tests/test_sales_kit.py` 因在 tmp 中重建产物再验而全绿，从未校验仓库内跟踪的交付物。本轮按 canonical `build_release.py` 构建刷新 product 四件、同步三处版本口径、重建外层 zip（verifier 端到端退出码 0），新增 2 个直接校验跟踪交付物的回归用例，并勘误同日结构审计文档中未经运行验证的证据行。(`spec/2026-09-19_refresh-sales-kit-payload`)

### Removed
- **蜂群指令面退役。** 清除安装面残留的 `/spec:assemble`、`/spec:combat`、`/spec:marshal` 指令文件及 sidecar（能力已由 run loop 的受管插槽路径承接）；`install.sh` 与 `doctor_spec_environment.py` 补齐现行 `commands/spec/<stage>.md` 子目录布局的退役清扫能力，重装或 `doctor --fix` 自动清除历史安装的僵尸命令，他人的组与裸条目原样保留。(`spec/2026-09-20_remove-swarm-commands`)
- **废弃资产清理（第一性原理定论见 `.spec/docs/2026-09-19_add-organize-command_first-principles-structure-audit.md`）。** `training/`（两个一次性 HTML 培训页，已被可验证的 `book/` 电子书取代，零活引用）；`.spec/artifacts/mastery-deck-*.png`（约 6MB 的培训页渲染证据，无任何活引用，历史文本由归档任务包与 Git 历史保全）；`.spec/swarm/`（已移除机制的孤儿台账，消费脚本已删，知识成果已在 `.spec/docs/`）。`.gitignore` 增补 `.zcode/`（原仅本地 exclude）。

## [0.10.0] - 2026-09-18

### Changed
- **单会话产品。** spec 技能现在完全在单个会话内运行：主会话自己规划、实现、验证、归档。全部多会话机制（终端 Worker、helper、监督者、蜂群阶段、调度器、intercom 守卫、HUD 投影、harness 控制平面）从产品中移除。(`spec/2026-09-18_remove-workers-hud-governance`)
- 任务合同简化：每个任务只需 `boundary` 与 `verify`，可选 `id` / `depends-on`。勾选前必须真实运行验证。
- 命令面收敛为 `/spec`、`new`、`run`、`check`、`done`、`push`、`update`、`status`、`goal`、`doctor`。
- 门禁集：假设与范围、最小路径、boundary/verify 完整性、任务完成、验收证据，外加可选的跨载体一致性与结构门禁。

### Removed
- `/spec:assemble` / `/spec:combat` / `/spec:marshal` 阶段、终端 Worker 协议文档、HUD 安装器与启动器、Pi 扩展、harness manifest/schema。
- Worker 交付回执；验收证据改为 checklist 中记录的真实命令执行。

## [0.9.0] - 2026-09-05

### Added
- **Tabular column-aligned HUD rendering.** The terminal watch view now renders tasks as a `状态|ID|标题|依赖` table (status cells carry their own label, so no header row is spent; one dim count line doubles as the section header) and helper runs as a `助手|状态|任务|宿主|心跳|ckpt` table with a dim column header. All padding is display-width aware (CJK-safe), every record stays one physical line at any width, and a degrade ladder drops columns as terminals narrow (≥80 full columns; 60–79 drops host+ckpt; <60 keeps core columns; ASCII glyph widths are compensated dynamically). Long reasons, next actions, and copy-paste `▶` commands stay full-width below the table (k9s bottom-bar pattern) and only actionable rows emit diagnosis lines, reducing per-run noise versus the old every-run prose. The Pi panel agents section aligns identity/status/health columns within its 15-line budget (no header row). Layout informed by a survey of gh-dash (lipgloss tables), k9s (full-table view, status column, bottom command bar), lazygit, and btop (gauges stay gauges). (`2026-09-05_tabular-hud-redesign`)
- **Additive `dependsOn` task field in the `spec-hud/v1` JSON contract.** The task projection now exposes dependency ids (`task_0002,task_0003`) consumed by the HUD deps column; existing consumers ignore unknown fields, and `render_compact` / `render_json` shapes are otherwise unchanged.
- **HUD alignment regression check.** `scripts/verify_hud_tables.py` builds an isolated fixture (CJK titles, dependencies, mixed-state runs) and asserts identical column starts after ANSI stripping, one-physical-line records at 100/72/56 columns, the degrade ladder, and JSON/compact contract field sets.

### Fixed
- **Zulu-suffixed timestamps parse on Python < 3.11.** `hud_support._age_minutes` normalizes trailing `Z`/`z` to `+00:00` before `fromisoformat`, so pending-derivation ages render as `已等待 N 分钟` on every supported interpreter instead of degrading to `未知时长`. (`2026-09-05_hud-derivation-age-zulu`)
- **Runtime export no longer ships the source-only packager `package_agent_plugin.py`.** `scripts/export_skill_package.py` now excludes it alongside `export_skill_package.py` and `build_release.py`: the plugin packer depends on source-repo-only assets (`agent-plugin/` manifest templates and `pyproject.toml` metadata) and cannot run inside an exported runtime package, where it previously failed with `missing manifest template`. The exported `scripts/` set is now 25 files and matches the `Skill Compatibility Facade` contract ("excluding source-only packagers") in `.spec/architecture/module-index.md`.
- **Sales kit delivery payload refreshed to the current version.** `marketing/spec-harness-sales-kit/product/` still carried 0.5.0 runtime archives and failed the kit's own verifier against 0.8.0 source (version mismatch, runtime hash and file-set mismatch); kit `README.md` and `DELIVERY-NOTE.md` still referenced 0.3.0. The product archives, `SHA256SUMS`, release notes, landing-page version stamp, kit checksums, and outer kit ZIP were regenerated from the current canonical build; `verify_sales_kit.py` now passes end to end.
- **Lint and format debt cleared for release checks.** Long E501 lines in `hud_support.py` / `verify_hud_tables.py` wrapped and four files brought under `ruff format` so the documented release verification suite passes end to end.

### Changed
- **Fleet view and HUD header rebrand** (merged since 0.8.0): `--fleet` renders every active Development Record as a self-contained block plus open Swarm Operations; attention runs, recruitment advice, and undecided derivations carry copy-paste ready `helper_control.py` commands with per-task checkpoint counts; the terminal/panel brand line is now `SPEC HARNESS ◌ QQ交流群：1002619705`.
- **README trees synchronized with disk truth.** Both `README.md` and `README-en.md` now show the complete source-repository layout (including `hooks/claude_stop_guard.py`, `references/changelog-guide.md`, `references/open-task-package-standard.md`, the six previously missing scripts, and the source-only `agent-plugin/` tree) and an exact exported-runtime tree, with the deterministic export rule (all `scripts/*.py` minus the three source-only packagers) stated explicitly so the list has a machine-checkable source of truth.
- `SKILL.md` reference documentation list now includes `references/open-task-package-standard.md`; `.spec/architecture/module-index.md` `Reference Contract` now lists `references/changelog-guide.md`. Structure-governance re-audit (2026-09-03) confirmed the top-level layout stays as-is — no directory migration, no import-path changes.

## [0.8.0] - 2026-09-03

### Added
- **Elastic multi-agent coordination inside normal `spec:run`.** Ordinary single-package runs can now borrow 0..N temporary helpers without new user stages, without entering `assemble`/`combat` first, and without touching the route state machine. The new pure-stdlib control plane `scripts/helper_control.py` (+ `scripts/helper_control_support.py`) provides `request` / `checkpoint` / `status` / `cancel` / `claim` / `complete` / `dispose`; one active package auto-resolves its slug, multiple packages require an explicit `--slug`. Coordination state lives only in Git-ignored `.agents/runtime/helpers/<slug>/<run-id>/` (immutable request, append-only events, atomic `O_EXCL` claim lock, assignment+lease, heartbeat, validated handoff, terminal summary); `.spec` stays the sole delivery truth and no pseudo Swarm Operation is created. Helper lifecycle: `requested -> accepted -> running -> handed_off -> joined -> disposed` with terminal `rejected/cancelled/timed_out/taken_over/failed`; commands are idempotent and lease expiry enables main-session takeover.
- **Auditable recruitment policy.** Recruitment never guesses from wall-clock: triggers are initial independent ready boundaries, a task that splits into research/test/review, two consecutive checkpoints without substantive progress, repeated failure of the same verification, or the main thread waiting on an independent result. A single hot file, short linear work, or tasks without `boundary`/`verify` stay N=1. Manual requests take priority over auto advice but are revalidated against ownership, concurrency budget (default 2, hard cap 4), and a task digest — after `tasks.md` reordering or `/spec:update`, stale claims are refused. Write-type helpers own mutually exclusive paths; shared hot files stay on the main thread.
- **Break-point self-healing, session keepalive, and silent supervision.** `scripts/supervisor_keepalive_support.py` binds a read-only supervisor to the host session PID plus process-start identity. A detached companion renews its lease while the session lives, then cancels/disposes it on session exit or explicit stop; companion failure intentionally falls back to lease timeout and rescue. `scripts/helper_rescue_support.py` classifies broken helper runs (`disconnected / blocked / failed / host_unavailable`) from lease, heartbeat, explicit failure, adapter error, and read-only supervisor observation evidence. A rescue publishes complete write-once intent/snapshot files (task contract, last heartbeat, workspace fingerprints — never file content — salvage path, owned-path drift, lineage generation), fences the old run as terminal `taken_over`, and redispatches an `origin=rescue` healer whose request preamble resumes at the recorded break point. The transaction resumes safely after crashes at snapshot/fence/child/result phases; a slug-scoped short lock serializes ownership and child election. Claim returns a lease-epoch fencing token, helper checkpoint/complete require it, and revision CAS prevents stale state overwrites. Lineage is capped at two generations; task-digest drift, host unavailability, or overflow falls back to main-thread takeover. Zombie late handoffs are refused by the state machine and the main session reviews Git drift before joining healer output.
- **Optional `elasticCoordination` host capability contract.** `harness/manifest.json` (contract version 0.3.0) and `harness/schema.json` declare an optional `hostAdapters.elasticCoordination` block — capability vocabulary `available/spawn/poll/message/cancel`, unrestricted helper count with host-owned capacity, runtime path, `scripts/helper_control.py` control plane, N=1 fallback, and host notes (native agent tools preferred; generic Skill hosts degrade to N=1; Pi claims the same contract from a same-cwd terminal via `SIGNAL`/`DONE`, tmux optional). Supervisors stay read-only disposable coordinators, not a fourth delivery role; manifests without the block keep validating unchanged.
- **Claude Stop guard helper awareness.** `hooks/claude_stop_guard.py` now appends unconsumed manual requests and non-terminal helpers to its block reason — but only while the package is unconverged; converged packages are never blocked by stale runtime helper state.
- **Pi same-path smoke update.** `scripts/pi_intercom_live_smoke_test.py` walks the real coordination chain (manual request -> same-cwd claim -> main-session decision -> `SIGNAL` heartbeat -> `DONE` handoff -> terminate & dispose). Real Pi sessions stay explicit manual smoke; CI keeps using the deterministic fake adapter.

### Changed
- `spec:run` docs (`SKILL.md`, `references/commands.md`, `operating-rules.md`, `engineering-philosophy.md`, `harness-framework.md`, `output-contracts.md`, `storage-and-archive.md`, `agents/openai.yaml`, installer `/spec:run` prompt) now describe checkpoint consumption points and the elastic policy; the user-visible stage list and the project-update narrative are unchanged — helper counts, checkpoints, and lifecycle state never replace real project progress in user updates.
- Module DAG gains the `Elastic Helper Coordination` module (control plane runtime, policy, Stop guard awareness) consumed by Task Package Commands and verified by Tests.

## [0.7.0] - 2026-09-02

### Added
- **Open Task Package Standard (OTPS-1) and interoperability track.** `references/open-task-package-standard.md` now publishes the open format contract for `.spec/` Development Records: field contracts for the three-artifact set plus completion summary, migration mappings from Kiro / GitHub Spec-Kit / OpenSpec / session-plan artifacts, and portability guarantees (plain text, runtime-neutral tooling, auditable evidence, forward-compatible revisions). `scripts/import_kiro_specs.py` imports AWS Kiro Feature Specs (`.kiro/specs/` EARS requirements, design, tasks) into Development Records with fidelity-first mapping — originals preserved verbatim in appendices, honest boundary/verify scaffolds marked for manual calibration, dry-run by default with `--apply` to write, slug conflicts refused. `scripts/package_agent_plugin.py` packages the runtime skill as a distributable agent plugin in two manifest formats — Agent Plugins 1.0 (`plugin.json`, schema-validated against the official `plugin.schema.json`) as the default and Claude Code plugin (`.claude-plugin/plugin.json`) as the alternate — from the `agent-plugin/` template with the export-skill payload layout under `skills/`.
- **Convergence semantics in `check`.** `scripts/check_spec_package.py` now reports a dedicated convergence status: markdown output gains a `收敛状态：已收敛 / 未收敛` section with a deterministic gap list (unchecked tasks, unchecked checklist items, acceptance not passed, evidence gaps, blocked tasks), and `--format json` gains top-level `converged` / `gaps` fields. Exit-code semantics and all existing fields are unchanged; the 66 archived packages and the full existing test suite pass unchanged.
- **Claude Code Stop guard hook.** `hooks/claude_stop_guard.py` implements the official Stop hook protocol (stdin JSON with `cwd` / `session_id` / `stop_hook_active`; blocking via `{"decision": "block", "reason": ...}` with the convergence gap list, silent approval otherwise). It locates the newest active Development Record under the project root, runs the convergence check, blocks claimed completion while unconverged (single-shot enforcement via `stop_hook_active`, mirroring `spec_disk_truth_gate.py` conventions), and stays silent in projects without `.spec/` or without an active package. `install.sh` documents AGENTS.md co-reading and `SKILL.md` documents the native hook points.

### Changed
- Product positioning broadened from a cross-CLI workflow skill to an open task package standard plus the execution governance layer for the agent ecosystem: READMEs now lead the Open Task Package Standard section with OTPS-1, the Kiro importer, and the plugin distribution track.
- Module DAG gains the `Open Task Package Interop` module (open standard contract, Kiro importer, agent plugin packaging) and the Git Hooks module now owns the Claude Code Stop guard.
## [0.6.0] - 2026-09-01

### Added
- **New `/spec:doctor` environment self-check and repair command.** `scripts/doctor_spec_environment.py` runs 13 fixed checks — Python ≥ 3.8 and git foundations, runtime layout, the harness manifest gate, whole-script compilation, installer ownership marker with per-file source-drift diffing, all ten host mounts (version, partial installs, foreign same-name collisions), Claude stage command files plus legacy layout leftovers (colon command files, alias dirs, base command file), dangling ZCode symlinks, project git state, the `.spec` skeleton, rendered Git-hook checker drift, and optional swarm tools (tmux/pi). `--fix` applies idempotent repairs restricted to installer-owned paths, always backed up first under `~/.spec-skill-backups/doctor-<ts>/`: stale layout sweep, `.spec/specs/` + `archive/` creation, a scoped `INSTALL_HOSTS=claude bash install.sh` re-run that regenerates stage command files (printing the equivalent command when bash is unavailable), and hook pointer re-rendering via `install_git_hooks.py`. Markdown and `--format json` output with exit codes 0/1/2. Registered as the twelfth user stage (`/spec:doctor`, `spec:doctor`) across the harness manifest (contract version 0.2.1), installer stage tables, host metadata, skill and reference docs, both READMEs, the smoke layout assertion, and the module DAG; covered by a 25-case test module.
- **Extended agent host matrix.** The installer now covers all major agent CLIs, installing into each host's standard skills directory: new `INSTALL_HOSTS` tokens `gemini` (`~/.gemini/skills`), `grok` / `grokbuild` (`~/.grok/skills`), `opencode` (`${XDG_CONFIG_HOME:-~/.config}/opencode/skills`), `openclaw` (`~/.openclaw/skills`), `hermes` (Hermes home `/skills`; `HERMES_HOME` wins, Windows `%LOCALAPPDATA%\hermes`, otherwise `~/.hermes`), and `pi` (`~/.pi/agent/skills`, relocatable via `PI_DIR`). Each host directory is overridable (`GEMINI_SKILLS_DIR`, `GROK_SKILLS_DIR`, `OPENCODE_SKILLS_DIR`, `OPENCLAW_SKILLS_DIR`, `HERMES_SKILLS_DIR`, `PI_SKILLS_DIR`, ...), protected by the same allowed-root preflight, foreign-skill collision refusal, ownership markers, and backup namespace as the existing hosts. READMEs document the matrix plus migration notes, and installer contract tests cover the new tokens.

### Changed
- **`pi` token redefined (behavior change).** `pi` now installs to the Pi agent host (`~/.pi/agent/skills`); the previous Raspberry Pi / Linux "Claude + Codex combo" semantics moved to the new `rpi` token (`desktop` is unchanged). Re-run old `INSTALL_HOSTS=pi` flows with `rpi` or `desktop` to keep the dual-directory behavior.
- **`claude-desktop` token redefined (behavior change).** `claude-desktop` now writes `~/.claude-desktop/skills` only (skill only, no command files); it previously aliased `claude` and wrote `~/.claude/{skills,commands}`. Use `claude` for the old behavior.
- **`all` now installs every supported host** (claude, claude-desktop, codex, zcode, gemini, grok, opencode, openclaw, hermes, pi) instead of only claude/codex/zcode; pass `INSTALL_HOSTS` explicitly for a subset.

## [0.5.0] - 2026-08-31

### Changed
- **Renamed the distributable product to `spec-harness`.** Release archives are now `spec-harness-{version}.tar.gz` / `spec-harness-{version}.zip` (package name in `pyproject.toml` follows), and READMEs, RELEASE/CONTRIBUTING, GitHub workflows/templates, and the marketing sales kit use the `spec-harness` / `Spec Harness` name. The invocation surface is unchanged: the skill installs as `spec` (`$spec`, `/spec`, `/spec:*`), and installer ownership markers (`.spec-skill-install`) plus the `~/.spec-skill-backups` backup root keep their existing names so installed copies remain recognizable and updatable.
- The copyright notice now reads `Copyright (c) 2026 Spec Harness contributors` (root `LICENSE`, the sales-kit mirror, and every copy embedded in the release archives).
- Follow-up to the 2026-08-31 audit: docs now describe actual script behavior (check dashboard, changelog as optional manual tooling), the Swarm Operation template lives only in `templates.md`, `references/spec-directory-standard.md` was removed in favor of `storage-and-archive.md`, and dead code (`stage_guidance`, route `--intent`, duplicated protected-branch constants) was removed.

## [0.4.0] - 2026-08-26

### Added
- Grace window for the push prefix gate: legacy non-`spec/` branches whose work started before 2026-08-27 UTC (fixed constant `SPEC_PREFIX_ENFORCED_FROM`, commit-timestamp based and deterministic) remain mergeable with a rename advisory, so in-flight `feature/<slug>` branches keep pushing.

### Changed
- `/spec:new` now creates `spec/<slug>` integration branches by default (was `feature/<slug>`); templates, command docs, READMEs, and server docs use the `spec/` naming consistently, aligning with `generate_changelog.py --spec-only` filtering. Pass `--branch` to override.
- `/spec:push` only merges `spec/`-prefixed branches into main and refuses other branches with a `git branch -m` rename hint; protected branches keep their precise refusal. Called out explicitly as a behavior change, bridged by the grace window above.

### Fixed
- Normalized git `%aI/%cI` UTC `Z` suffixes before `datetime.fromisoformat` parsing so the grace-window dating cannot silently degrade to refusing every legacy branch.

## [0.3.0] - 2026-08-22

### Added
- Shared `issue_closure_support.py` runtime with five typed dispositions, archived follow-up validation, cycle detection, and v1 completion-summary rendering/parsing.
- Target-revision Spec validation (`check_all_spec_packages.py --revision`) and deterministic `runtime_sha256` release stamps for source/export parity checks.
- Linux arm64 container verification for server, Harness, runtime export, and installer shell contract.

### Changed
- New archives, Harness final reports, and Swarm closeouts use the same structured issue-disposition contract. Executable follow-up work must already be completed and archived; free-form `nextTasks`, `unresolvedRisks`, and archive follow-up fields cannot close work.
- Repository gates now validate active and archived records, explicit slug existence, non-`x` checkbox markers, trusted default/custom Spec roots, target-revision Swarm state, per-ref pre-push attribution, and code-commit `Spec: <slug>` footers. Legacy archives are accepted only when their complete four-file bundle is byte-identical to an authoritative remote/main baseline.
- Harness product, round contract, evaluation, import permit, and final report artifacts are immutable/digest-bound and cross-checked against the approved evaluation.
- Swarm completion requires terminal blocker-free Board rows, exact Board finding-ID/disposition reconciliation, passing referenced archives, and candidate closeout validation before atomic replacement.
- Server mode reports `completed` only for archives that pass the closure gate, parses nested closure JSON safely, and returns structured closure metadata.
- Runtime, release, and sales-kit synchronization now includes Harness schemas, hooks, server projection, exported archives, and product bundles.

### Fixed
- Prevented archive records from disappearing from Git gates, backdated/new archives from claiming legacy status, nonexistent explicit slugs from zero-match success, arbitrary `*/specs/*` paths from entering scope, and code-only/multi-ref pushes from bypassing retro-pack attribution.
- Prevented schema-valid Harness closeout tampering, omitted worker findings, malformed referenced archives, existential Stop-hook links, default `.spec` symlink escapes, unbounded follow-up graphs, and stale outer sales-kit bytes from passing closure gates.
- Corrected blocker parsing so boundary/verify prose containing “阻塞” does not mark a task blocked.

### Added
- Self-hosted server-mode adapter under `server/`: standard-library HTTP `start`/`result`/`health` endpoints, task-package status projection, allowlisted project roots, installer documentation, and regression coverage.
- Harness control-plane contracts: `harness/manifest.json` and `harness/schema.json` define manifest-driven stages, roles, six runtime artifact classes, lifecycle transitions, host adapters, and fail-closed gates.
- Standard-library harness runtime: `scripts/harness_support.py` and `scripts/harness_cli.py` provide manifest validation, run initialization/recovery, identity-bound evaluation recording, check, import-permit generation, finalization, and closed-run idempotence.
- Harness compatibility coverage: runtime export includes `harness/` and the harness CLI, installer aliases derive from the manifest, and smoke/tests execute an exported package through `init-run -> record-evaluation -> check-run -> finalize-run` independently.
- `/spec:marshal` user stage for swarm closeout: after combat, the main owner gathers worker artifacts, filters and merges outputs by ownership, runs unified validation, archives referenced Development Records, writes knowledge docs, records Swarm Operation closeout, commits scoped changes, and hands off to push.
- Swarm Operation lifecycle gates: `scripts/swarm_operation_support.py`, `scripts/check_swarm_operation.py`, and `scripts/complete_swarm_operation.py` validate operation status, roster/spec references, completed/expired closeout evidence, and archived referenced specs.
- `tests/test_swarm_operation.py` covering open-status blocking, completed closeout after archives, unarchived-spec refusal, and expired operation reasons.
- `/spec:assemble` and `/spec:combat` user stages for optional agent swarm work: assemble plans agent count, roles, ownership boundaries, signal protocol, board fields, and merge gates; combat executes the plan through independent sessions while each worker still follows its assigned Development Record's `boundary` / `verify` discipline.
- Swarm Operation Record documentation under `.spec/swarm/YYYY-MM-DD_<operation>/operation.md`, including roster, signal protocol, board, merge/review gate, and recovery fields.
- `scripts/live_swarm_smoke_test.py` — live tmux-based two-worker runtime smoke that validates multi-spec one-agent-per-spec assignment, main-thread `DECISION`, worker `DONE`, owned-file writes, Development Record refill, and `check_all_spec_packages.py` merge gates.
- `scripts/pi_intercom_live_smoke_test.py` — manual-supervised live `pi` + pi-intercom smoke that starts two real `pi` sessions, receives `SIGNAL` / `DONE` asks, verifies supervisor `DECISION` replies, and checks owned-file plus ack-file outputs.
- Unscoped discrete-work retro-pack rule for `/spec:push`: when the user never stood up a Spec package and later asks to push, dirty/unscoped changes must be thematically retro-packed into one or more Development Records, or captured in one whole Spec package when intent is coherent — never pushed as mixed dirty without a package.
- `scripts/build_release.py` — standardized build script producing versioned distributable artifacts (`spec-skill-{version}.tar.gz`, `.zip`, `SHA256SUMS`, `RELEASE_NOTES.md`) with pre-build validation, version stamping, and checksum generation; excluded from the runtime export alongside `export_skill_package.py`.
- `.github/workflows/release.yml` — automated tag-triggered release workflow: runs the full test suite, builds artifacts via `build_release.py`, verifies checksums, and creates a GitHub Release with all artifacts attached.
- `tests/test_build_release.py` covering version parsing, export exclusion, archive integrity (tar.gz permission preservation, zip structure, VERSION/BUILD_INFO stamps), checksum verification, release-notes extraction, and version-mismatch rejection.
- Installer detects a same-name skill from a different source (own `SKILL.md`, no installer marker) and refuses to overwrite it unless `FORCE=1`, which backs the existing skill up first — prevents silently clobbering a different `spec` variant.
- `references/naming-and-commits.md` as the single source of truth for slug verb-object naming (`YYYY-MM-DD_<verb>-<object>`), Conventional Commits format, and layered language selection (structure layer ASCII English, semantic layer follows user input).
- `--git-record-language auto` (new default) on `complete_spec_package.py`, detecting zh/en from the `spec.md` title CJK ratio; explicit `zh`/`en` still supported.
- `detect_language`, `slug_verb_advisory`, and `RECOMMENDED_SLUG_VERBS` in `spec_package_support.py` for language auto-detection and verb-object slug advisory.
- Non-breaking verb-object advisory in `check_spec_package.py` Development Record output for slugs that do not start with a recommended verb.

### Changed
- Executable leftover work found during a Spec round must be written back into the current Development Record and finished this round; `done` leftover notes may only record out-of-scope remarks, accepted risks, or agent-unexecutable external dependencies. Synchronized `SKILL.md`, `references/*`, `agents/openai.yaml`, `scripts/complete_spec_package.py --follow-up` help, and CLI contract tests.
- Expanded the runtime command surface from ten user stages to eleven: `new/goal/assemble/combat/marshal/run/check/done/push/update/status`, and synchronized `SKILL.md`, `references/*`, `README*`, `agents/openai.yaml`, `install.sh`, smoke checks, and CLI contract tests.
- Expanded Swarm Operation Record status from `draft/ready/active/completed/expired` to `draft/ready/active/merging/verified/completed/expired`; `combat` now stops at worker DONE / merging, while `marshal` owns closeout.
- Swarm retention/index governance: marshal closeout now refreshes `.spec/swarm/index.md`, status groups Open / Recently closed / Compaction advisory, and closed-operation count or stale indexes remain advisory instead of push blockers.
- `push_spec_package.py` now blocks related/touched unclosed Swarm Operation Records and instructs the user to run `/spec:marshal`; `report_spec_package.py --view status` now includes a Swarm Operations overview.
- Earlier in this unreleased cycle, added the `goal`, `assemble`, `combat`, and `push` stages and synchronized `SKILL.md`, `references/*`, `README*`, `agents/openai.yaml`, `install.sh`, smoke checks, and CLI contract tests.
- Reduced the installer surface to a single root `install.sh`; runtime exports and release artifacts no longer ship a separate `scripts/install.sh` compatibility shim.
- Expanded dirty-tree triage across `SKILL.md`, `references/commands.md`, `references/operating-rules.md`, `references/output-contracts.md`, `agents/openai.yaml`, `README.md`, `README-en.md`, and `scripts/push_spec_package.py` clean-tree messaging to cover the no-active-package path (theme-atomic multi-package vs one whole package).
- Replaced the stale `[Spec] <slug>: <desc> [Spec-#N]` commit format (documented in `commands.md` but contradicted by actual repo practice) with Conventional Commits `<type>(<scope>): <description>` plus optional `Spec: <slug>` footer; old format marked deprecated.
- Synchronized slug, commit, and language rules across `commands.md`, `output-contracts.md`, `templates.md`, `storage-and-archive.md`, `spec-directory-standard.md`, `SKILL.md`, `00-readme.md`, `agents/openai.yaml`, `README.md`, and `README-en.md` to reference `naming-and-commits.md` as the single source of truth.
- Rewrote `SKILL.md` `description` with explicit TRIGGER/SKIP conditions and Chinese trigger keywords so the description itself carries the recall surface, instead of a single English intent sentence that also embedded the storage path.
- `references/engineering-philosophy.md` presents the four principles as the skill's own philosophy, no longer described as derived from a local reference package.
- Scrubbed personal local paths from public maintainer records.
- Added `.spec` directory standard documentation for Development Records (`YYYY-MM-DD_slug`) and Module DAGs under `.spec/architecture/`.
- Added check-stage validation for Development Record slug naming while keeping lower-level slug validation backward compatible.
- Added current repository Module DAG artifacts under `.spec/architecture/`.
- Updated README documentation to reflect the current runtime layout, exported package boundaries, safer pinned-clone remote install flow, and repository verification commands.
- Clarified that current remote install docs no longer rely on a default `REPO_URL`; remote users should clone, checkout a full SHA, then run the local installer.
- Documented the current `/spec:check` gate surface across runtime docs: base gates, marker-driven optional gates, evidence refill, and non-placeholder script/test/build proof requirements now align with `check_spec_package.py`.
- Restricted `--specs-dir` to trusted relative directories under `--root`, rejecting empty values, absolute paths, parent traversal, and symlink-resolved escapes.
- Removed the `spec:push` `--execute` flag; `push_spec_package.py` now executes the safe merge/push/delete flow by default while preserving safety prechecks.
- Updated contributor and release verification docs to include smoke, pytest, and ruff checks.
- Clarified maintainer notes for current source-of-truth and cc-switch-hosted skill synchronization.

### Removed
- Dropped the `.life/` maintainer workspace directory (process-state history with no runtime value); README references to it were removed.

### Fixed
- Server-mode third-round review fixes: `do_start` idempotence now matches the exact `task package already exists` init message instead of any `exists` substring, so an `integration branch already exists` preflight failure returns 500 instead of falsely reporting success with no package created; `PORT` parsing joins the startup guard (non-integer or out-of-range 1-65535 refuses to start with a clean message instead of an uncaught traceback), consistent with `MAX_CONCURRENT_WORK`; `server/install.sh` rejects `PROJECTS_DIR=/` and paths containing `.` components before any system write, preventing a whole-filesystem `chown -R` and an allow-everything `ALLOWED_ROOTS=/`; tests cover the retry semantics split, PORT guard, and installer rejections.
- Server-mode follow-up fixes for regressions found by re-review of the hardening commit: installer self-test now uses `curl -sSf` and a success-only `"slug"` marker so HTTP 500 responses can no longer false-pass the start check; bearer-token comparison moved to bytes so non-ASCII `Authorization` headers return 401 instead of crashing the handler; installer requires root up front (dropping the misleading non-root-with-sudo path); `MAX_CONCURRENT_WORK` guard messages now say the server refuses to start; package-path validation no longer rejects symlinks that stay inside the allowlisted root (only escapes are rejected); tests gained slot-release, non-ASCII-header, internal-symlink, and installer-requires-root coverage and no longer inherit an exported `SPEC_SERVER_TOKEN`.
- Server-mode security hardening (review findings for `2026-08-17_add-server-mode`): `server/server.py` now authenticates `/start` and `/result` with a `SPEC_SERVER_TOKEN` Bearer token (`/health` stays anonymous), refuses to start on a non-loopback address without a token, bounds subprocess fan-out with `MAX_CONCURRENT_WORK` (excess requests get 429), containment-checks derived package paths so symlinks inside allowlisted roots cannot escape, appends a random suffix to auto-generated slugs so identical goals no longer collide, and returns generic errors on init failure instead of raw subprocess output.
- `server/install.sh` now validates `DOMAIN`, `PORT`, `HOST`, and `PROJECTS_DIR` before generating privileged systemd/Caddy configuration, auto-generates a Bearer token stored in the mode-600 unit, uses `runuser` as root (falls back to requiring `sudo` otherwise), and makes the local health/start self-test fail the install on HTTP errors or missing `task_id`.
- `export_skill_package.py` no longer copies `__pycache__/` and `*.pyc` files into the exported runtime package; `copy_tree` now uses `shutil.ignore_patterns` to exclude Python bytecode artifacts.
- `install.sh` now works on Windows: `ensure_private_dir` no longer uses `mkdir -m 700` (which errors on NTFS); the existing `chmod 700` enforces permissions on every platform.
- Stage slash commands (`/spec:new`, `/spec:run`, `/spec:check`, `/spec:done`, `/spec:push`, `/spec:update`, `/spec:status`) now install on Windows. The installer previously wrote `commands/spec:<stage>.md`, but NTFS forbids `:` in filenames, so Claude Code's Node.js layer read them as `spec<stage>.md` and `/spec:<stage>` failed with "Did you mean spec<stage>?". Stage commands now live under `commands/spec/<stage>.md` (subdirectory → colon namespace, valid on every platform); the old colon files are migrated to backups on upgrade.

## [0.1.1] - 2026-05-02

### Added
- pytest test suite covering all 7 helper scripts (80 test cases).
- `scripts/install.sh` one-line install script (included in export package).
- `references/00-readme.md` linked from SKILL.md "需要时再读取" section.

### Changed
- Replaced placeholder badge URLs with actual values.
- `install.sh` default `REPO_URL` set to actual repository address.

## [0.1.0] - 2026-05-01

### Added
- GitHub-facing open-source project materials (CODE_OF_CONDUCT, CONTRIBUTING, SECURITY, LICENSE, issue templates).
- `RELEASE.md` with lightweight `v0.x.y` versioning and release steps.
- GitHub Actions CI to run script compilation and smoke verification on push and pull request.
- Repository-level and export-runtime smoke verification coverage.
- Configurable Git record labels for completion summaries (Chinese default, English via `--git-record-language en`).
- `--specs-dir` parameter on all helper scripts to override the default `.spec` storage directory.
- `references/00-readme.md` documenting the reference file dependency order.

### Changed
- Tightened SKILL.md frontmatter `description` to 154 characters; command aliases moved to `## CLI 兼容` section.
- Relocated "必须遵守" rules from SKILL.md into `references/operating-rules.md` with SKILL.md retaining a 4-item summary and link.
- Merged SKILL.md `## 命令路由` duplicate CLI mapping into `## CLI 兼容`.
- Slug validation (`validate_slug`) widened from `[a-z0-9-]+` to `[a-z0-9_-]+` (underscores allowed).
- Fixed `detect_knowledge_docs` glob pattern to match docs whose slug appears after a date prefix.
- Removed implicit `archive/` and `docs/` creation from `init_spec_package.py` (now created on demand).
- Fresh packages with placeholder `### 2.3 待确认问题` are now correctly routed to `status`.
- Dead `evidence_keys` entries and duplicate helpers removed from check/complete/report scripts.
- Disambiguation paragraph added to SKILL.md and README.md clarifying this is a task-package manager, not a central dispatcher.
- `agents/openai.yaml` `default_prompt` split into multi-line YAML block scalar.

### Fixed
- Exported runtime package now includes `spec_package_support.py` and can run `init` independently.
- Orchestration checks no longer false-fail on packages that don't enable orchestration.

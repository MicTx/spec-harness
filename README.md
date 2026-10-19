# Spec Harness

面向 AI 编程智能体的任务包工作流工具，用可验证的范围、证据和 Git 门禁管理长期改动。

[![CI](https://img.shields.io/github/actions/workflow/status/MicTx/spec-harness/ci-public.yml)](https://github.com/MicTx/spec-harness/actions/workflows/ci-public.yml)
[![Release](https://img.shields.io/github/v/release/MicTx/spec-harness)](https://github.com/MicTx/spec-harness/releases)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[🇬🇧 Read this in English](README-en.md)

Spec Harness 把跨会话、跨测试轮次的工程任务保存为可恢复的 Development Record。它提供任务包、路由、验证、归档和 Git 收尾，不提供模型，也不替用户做产品决策。

按你要做的事选入口：想先理解它，读[项目介绍](docs/introduction.md)；想动手跑通一次，跟[长程任务教程](docs/tutorial.md)；要发布或回滚，看 [Git 工作流](docs/git-workflow.md)。全部公开入口见[文档索引](docs/README.md)，英文读者见 [English index](docs/README-en.md)。

**目录**

- [你会得到什么](#你会得到什么)
- [目录结构](#目录结构)
- [环境要求](#环境要求)
- [安装说明](#安装说明)
- [快速开始](#快速开始)
- [导出运行时 Skill 包](#导出运行时-skill-包)
- [验证](#验证)
- [获取版本](#获取版本)
- [适用范围](#适用范围)
- [许可协议](#许可协议)

## 你会得到什么

- **一个任务包**：在 `.spec/specs/YYYY-MM-DD_slug/` 中保存 `spec.md` 和 `tasks.md`（新版包不含 `checklist.md`，验收走 `spec.md` §6 成功标准；旧包的 `checklist.md` 继续有效）。
- **一条可验证链**：`目标 → 范围 → 任务 → 验证 → 归档 → 提交 → 推送`，每个任务都声明 `boundary` 与 `verify`。
- **明确的入口**：`new`、`goal`、`autoplan`、`autorun`、`run`、`check`、`done`、`push`、`update`、`status`、`doctor`、`organize`。
- **可恢复的状态**：报告、失败原因、完成摘要和归档都来自磁盘文件，换会话仍能继续。
- **会话边界交接**：任务包可选 `handoff.md`——生成式快照头 + append-only 条目日志，必填小节随协作模式（单人/多人/agent/集群）自适应，`spec_handoff.py` 维护、check 门禁校验、done 归档时冻结。
- **交互式递归规划**：`autoplan` 在项目初期或阶段收口时多轮征询摸清目标，随后按 `autorun` 同款串联递归把框架规划、逐阶段细节规划与一致性复核逐个交给全新会话接力（不用于进程内子代理），细化到 autorun 颗粒度，结构门禁与独立复核通过后停在就绪报告，从不自动起链。
- **递归自动链**：`autorun` 以项目规划文档的功能 checkbox 为终止条件——本轮跑完推送到 main 后，自动规划下一轮并在同路径开新交互会话注入提示词接续（macOS 用 Terminal.app，Linux/Windows WSL2 用 tmux）（沿用起链时的宿主 CLI，可用 `--host` 或 `SPEC_AUTORUN_HOST` 显式改选），直到全部功能勾选完成；每轮是完整交互会话，可旁观也可随时接管，macOS 旧窗口在新会话就位后自动回收，tmux 会话保留供重新连接；单链锁与「无合格规划文档拒绝起链」护栏防止失控递归；默认轮次上限 5（显式 `--max-rounds` 可改设，`--unbounded` 解除），worker 权限默认继承父会话、探测不到就不提权（`--permission-mode bypass` 才显式提权），规划特性集指纹冻结会拒绝链中途新增未勾选特性的自旋。
- **稳定的任务身份**：任务可用 `id:` 与 `depends-on` 声明身份与依赖；协议迁移不改写历史归档。
- **受控的执行面**：主会话保留路由、集成、验收和 done/push 门禁；`workflow-runner` 与其他 slots 只执行有边界的工作段。
- **两种运行形态**：本地 Skill 是参考实现；可选 `server/` 适配器只负责远端任务包的 start/result/health 投影。

## 目录结构

```text
.
├── SKILL.md                  # Skill 入口：阶段指令与运行时契约
├── install.sh                # 本地 Skill 安装器（多宿主，见下表）
├── pyproject.toml
├── agent-plugin/             # 源码库独有：Agent 插件清单模板
├── agents/                   # 可选编排代理定义（orchestrator/planner/reviewer/confirmer）
├── docs/                     # 面向用户与贡献者的公开文档
├── hooks/                    # pre-commit / pre-push 与 Stop 门禁
├── references/               # 运行时契约参考（命令、模板、slots、归档）
├── scripts/                  # 阶段脚本；build/export/package 打包器不进运行时导出
├── server/                   # 可选 self-hosted server-mode 适配器
├── slots/                    # 可插拔插槽：team-loop 与 workflow-runner（契约见 references/slots.md）
├── tests/                    # 源码库独有：pytest 测试套件
├── release/                  # 源码库独有：跟踪的发布产物（spec-harness-{version} 四件）
└── .github/                  # CI 与 Issue/PR 模板
```

运行时任务包默认写入目标项目的 `.spec/`；源码仓库本身不包含用户项目状态。详细契约在 [`references/`](references/00-readme.md)，面向人的教程在 [`docs/`](docs/README.md)。

## 环境要求

- Python 3.9 或更高版本。
- 能加载 `SKILL.md` 的宿主 CLI，例如 Claude Code、Codex、Gemini CLI、Grok Build、OpenCode、OpenClaw、Hermes、Pi 或 ZCode。
- 辅助脚本只使用 Python 标准库；开发测试额外需要 `pytest` 和 `ruff`。
- 安装器需要 Bash；Windows 用户可使用 WSL2。自动规划/执行链的会话启动支持 macOS Terminal.app、Linux 和 Windows WSL2 下的 **tmux ≥3.2**；不支持原生 PowerShell/cmd/Git Bash 启动链会话。
- Linux/WSL2 无需图形桌面；Ubuntu/Debian 先执行 `sudo apt-get install tmux procps`，并在同一 Linux/WSL 环境安装 Python 与 worker CLI。启动结果和 `status` 会给出 `tmux -L … attach-session …` 命令；按 `Ctrl-b d` 脱离而不终止会话。完整说明见[会话后端契约](references/commands.md#session-backends)。

## 安装说明

### 安装器

从源码仓库根目录运行：

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
git checkout v0.19.1
bash install.sh
```

`install.sh` 是本地 Skill 的唯一安装入口；可选的 `server/install.sh` 只用于部署 self-hosted server-mode。更新或回滚时切换到目标 tag 后重新运行安装器。

源码树安装不需要签名凭据：本地没有签发凭据时，安装器自动以 `--skip-signing` 导出并打印一行提示；发布签名只由 release 构建强制（release 压缩包出厂自带签名）。需要本地签发时设置 `SPEC_SIGNING_IDENTITY` 与 `SPEC_SIGNING_KEY` 指向身份 JSON 与密钥文件；显式跳过也可用 `SPEC_SKIP_SIGNING=1`。

### 支持的宿主

| `INSTALL_HOSTS` | 宿主 | 默认目录 | 额外行为 |
| --- | --- | --- | --- |
| `claude` | Claude Code | `~/.claude/skills` | 生成 `/spec` 与阶段命令（core 5 个，full 12 个） |
| `claude-desktop` | Claude Desktop | `~/.claude-desktop/skills` | 只安装 Skill |
| `codex`、`codex-desktop` | Codex | `~/.codex/skills` | 两个 token 共用目录 |
| `gemini` | Gemini CLI | `~/.gemini/skills` | |
| `grok`、`grokbuild` | Grok Build | `~/.grok/skills` | `grokbuild` 是别名 |
| `opencode` | OpenCode | `${XDG_CONFIG_HOME:-~/.config}/opencode/skills` | |
| `openclaw` | OpenClaw | `~/.openclaw/skills` | |
| `hermes` | Hermes | `~/.hermes/skills` | 可用 `HERMES_HOME` 重定位 |
| `pi` | Pi agent | `~/.pi/agent/skills` + `~/.pi/agent/prompts` | 可用 `PI_DIR` 重定位；同时写入 `/spec` 与 `/spec:<stage>` 原生命令模板（`PI_PROMPTS_DIR` 重定位，`/reload` 后生效） |
| `zcode` | ZCode | `~/.zcode/skills` | 项目自有宿主 |
| `desktop`、`rpi` | Claude Code + Codex | 两套默认目录 | 组合目标 |
| `all` | 全部宿主 | 按上表写入 | 显式全量 |

不设置 `INSTALL_HOSTS` 时安装器自动探测：只安装「宿主 CLI 在 PATH 或宿主默认目录已存在」的宿主，本安装器装过的宿主按所有权标记沿用；跳过的宿主列在安装输出里。显式给定 `INSTALL_HOSTS`（含 `all`）时完全不探测。

只安装一个宿主时显式指定 token，例如：

```bash
INSTALL_HOSTS=codex CODEX_SKILLS_DIR=~/.codex/skills bash install.sh
```

安装 profile：`INSTALL_PROFILE` 选择组件面，缺省 `core`——只装内核（核心阶段命令 `/spec:new` `/spec:run` `/spec:check` `/spec:done` `/spec:push`、Git 钩子与脚本树），不装扩展组件（`slots/`、`agents/`）与扩展阶段命令（`goal`/`autoplan`/`autorun`/`update`/`status`/`doctor`/`organize`）；`INSTALL_PROFILE=full` 恢复完整安装（全部 12 个阶段命令与扩展组件，与旧版字节等价）。已装 full 的机器重跑默认安装会降回 core：扩展阶段命令与 agents 先入备份再移除。需要链、规划链或扩展能力时显式指定：

```bash
INSTALL_PROFILE=full bash install.sh
```

安装器默认拒绝覆盖没有 `.spec-skill-install` 所有权标记的同名 Skill；确认替换用户目录时才设置 `FORCE=1`。`pi` 现在表示 Pi agent，旧的 Claude + Codex 组合请使用 `rpi` 或 `desktop`。

### 手动安装

只导出运行时 Skill 包：

```bash
python3 scripts/export_skill_package.py --output /tmp/spec --force
```

手动直调导出器不会自动跳过签名：无凭据环境需显式加 `--skip-signing`（安装器在同样条件下会自动附加并提示）。导出器同样接受 `--profile core|full`（缺省 `full`，与 release 载荷字节等价）。

把导出的 `spec/` 目录放入宿主的 Skill 根目录，然后在 Codex/ZCode 中触发 `$spec`，在 Pi 中使用 `/spec`、`/spec:<stage>` 原生命令（参数经 `$@` 原样透传），或在 Claude Code 中使用 `/spec:goal`。

## 快速开始

装好之后，最短的路径是在宿主里给出一句完整目标：

```text
$spec
spec:goal add payment recovery with tests
# Claude Code: /spec:goal add payment recovery with tests
```

宿主会按阶段引导：先澄清目标与范围（`goal`），再执行（`run`）、复核（`check`），最后归档提交（`done`）；推送（`push`）仅在显式要求时执行。想绕过宿主直接驱动脚本时，效果等价——在目标项目中创建任务包：

```bash
python3 /tmp/spec-harness/scripts/init_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_add-payment-recovery \
  --title "Payment Recovery"
```

初始化后，按顺序查看阶段、状态和门禁：

```bash
python3 /tmp/spec-harness/scripts/route_spec_package.py --root /path/to/project --slug 2026-10-03_add-payment-recovery
python3 /tmp/spec-harness/scripts/report_spec_package.py --root /path/to/project --slug 2026-10-03_add-payment-recovery --view status
python3 /tmp/spec-harness/scripts/check_spec_package.py --root /path/to/project --slug 2026-10-03_add-payment-recovery
```

任务完成后可由 `/spec:done` 完成复核、归档和 Git 提交。直接操作时，先运行：

```bash
python3 /tmp/spec-harness/scripts/complete_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_add-payment-recovery \
  --archive
```

该脚本只负责生成摘要并归档；随后手动运行 `git commit`，再用 `push_spec_package.py` 做合并和推送收尾。废弃成员只归档到 `archive/retired`，遵循 `never delete` 原则；每个任务必须先真实运行 `verify`，不能只勾选复选框。需要更强的证据绑定时，在 `spec.md` 声明 `evidence: runs`：机器任务的 `verify` 命令由 `scripts/spec_verify.py run` 真实执行并把运行记录（exit、耗时、stdout 尾哈希、工作树指纹）追加到包内 `runs.jsonl`，check/done 只认「最近一次 exit==0 且指纹匹配当前工作树」的记录——没跑过就是未验证，取证后改了代码就是证据过期。

## 导出运行时 Skill 包

源码仓库同时包含打包器、测试和发布产物；运行时包由 `scripts/export_skill_package.py` 的机器规则生成。它保留 `SKILL.md`、安装器、`agents/`、`hooks/`、`references/`、`server/`、`slots/` 和运行时辅助脚本，排除源码维护工具、测试和 `release/`。

导出树由测试锁定，当前内容如下：

```text
spec/
├── LICENSE
├── NOTICE
├── SKILL.md
├── install.sh
├── pyproject.toml
├── agents/
│   ├── openai.yaml
│   ├── orchestrator.md
│   ├── planner.md
│   ├── reviewer.md
│   └── confirmer.md
├── hooks/
│   ├── pre-commit
│   ├── pre-push
│   └── claude_stop_guard.py
├── references/
│   ├── 00-readme.md
│   ├── changelog-guide.md
│   ├── commands.md
│   ├── engineering-philosophy.md
│   ├── handoff.md
│   ├── naming-and-commits.md
│   ├── output-contracts.md
│   ├── slots.md
│   ├── storage-and-archive.md
│   ├── templates.md
│   ├── orchestration.md
│   └── stages/
│       ├── autoplan.md
│       ├── autorun.md
│       ├── check.md
│       ├── doctor.md
│       ├── done.md
│       ├── goal.md
│       ├── new.md
│       ├── organize.md
│       ├── push.md
│       ├── run.md
│       ├── status.md
│       └── update.md
├── scripts/
│   ├── autoplan_gate.py
│   ├── autoplan_spawn.py
│   ├── autorun_spawn.py
│   ├── chain_lifecycle.py
│   ├── chain_recovery.py
│   ├── chain_spawn_support.py
│   ├── check_all_spec_packages.py
│   ├── check_spec_package.py
│   ├── complete_spec_package.py
│   ├── dashboard_support.py
│   ├── doctor_spec_environment.py
│   ├── generate_changelog.py
│   ├── handoff_support.py
│   ├── init_spec_package.py
│   ├── install_git_hooks.py
│   ├── install_slot_hooks.py
│   ├── issue_closure_support.py
│   ├── measure_skill_surface.py
│   ├── organize_project_structure.py
│   ├── gitea_hook_repair.py
│   ├── push_spec_package.py
│   ├── read_version.py
│   ├── report_spec_package.py
│   ├── route_decision.py
│   ├── route_spec_package.py
│   ├── safe_open_support.py
│   ├── slot_registry.py
│   ├── smoke_test_spec_skill.py
│   ├── spec_handoff.py
│   ├── spec_package_support.py
│   ├── spec_verify.py
│   ├── sweep_chain_windows.py
│   ├── update_checkpoint.py
│   └── update_checkpoint_support.py
├── server/
│   ├── README.md
│   ├── install.sh
│   └── server.py
└── slots/
    ├── team-loop/
    │   ├── manifest.json
    │   ├── README.md
    │   ├── hooks/
    │   │   ├── loop_stop_guard.py
    │   │   └── loop_teammate_gate.py
    │   ├── scripts/
    │   │   ├── loop_control.py
    │   │   ├── loop_route.py
    │   │   └── loop_state.py
    │   └── tests/
    │       ├── conftest.py
    │       ├── recheck_probes.py
    │       ├── smoke_run.py
    │       ├── test_audit_regressions.py
    │       ├── test_hooks.py
    │       ├── test_loop_control.py
    │       ├── test_loop_route.py
    │       └── test_loop_state.py
    └── workflow-runner/
        ├── manifest.json
        ├── README.md
        ├── scripts/
        │   ├── workflow_fanout.py
        │   └── workflow_route.py
        └── tests/
            ├── conftest.py
            ├── test_workflow_fanout.py
            ├── test_workflow_fanout_realpath.py
            └── test_workflow_route.py
```

## 验证

源码级检查：

```bash
python3 -m compileall -q scripts server hooks slots tests
python3 scripts/slot_registry.py validate
python3 scripts/smoke_test_spec_skill.py
python3 -m pytest -q
ruff check scripts/ server/ hooks/ slots/ tests/
ruff format --check scripts/ server/ hooks/ slots/ tests/
```

改动文档时至少运行：

```bash
python3 -m pytest tests/test_public_docs.py tests/test_export_skill_package.py -q
git diff --check
```

完整验证范围以 [`CONTRIBUTING.md`](CONTRIBUTING.md) 为准；任务包的 `package`、`integration`、`project` 验证级别由 `spec.md` 的 `### 5.1 验证策略` 区块声明（机器键 `verification-strategy`，区块按机器键识别，英文标题与键锚点等价，见 [`references/templates.md`](references/templates.md) 的英文模板变体）。

## 效率与读取成本

内部读取面（`route_spec_package.py --format json --brief` 任务切片与 `spec_verify.py show` 证据视图）与文档压缩的取舍以四臂对照实测为准：A 基线 / B 仅压缩 / C 仅内部工具 / D 组合，16 个冻结场景归入五类固定任务，两次连续运行稳定投影一致，字节、确定性调用与真实使用三层分列不换算。

- 文本压缩降低全部五类任务的读取字节（每类 −23 ～ −2,270 B）与静态文档面 1,357 B，任务完整性零损失。
- 内部工具的调度说明是 248 B 有界常开成本；组合态在读取量最大的任务类上仍净省 782 B。
- `--brief` 在一次视图替代多次整读的形态下同时减少调用与字节（实测：复审场景 −1 调用、−552 B）；小包定位与单文件精确读取按调度决策表保持原生读取，实测聚合开销大于直读的负收益行如实保留。
- `spec_verify.py show --history` 全量重组仅在需要完整日志时使用，分页可逐字节重组校验；首屏与短证据直读不经过它。

## 获取版本

稳定版本和 SHA-256 校验文件位于 [GitHub Releases](https://github.com/MicTx/spec-harness/releases)。安装、升级、回滚见 [RELEASE.md](RELEASE.md)；参与开发见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [Git 工作流](docs/git-workflow.md)。

## 适用范围

Spec Harness 提供任务包、工作流指令、模板、验证脚本和可选的 self-hosted server-mode 适配器。它不运营托管服务、不提供模型、不替用户做产品决策，也不负责 PR 创建或远程 CI 编排。

> [!WARNING]
> `server/` 是可选的自托管 HTTP 接入层。公网部署前必须设置 `SPEC_SERVER_TOKEN` 与 `ALLOWED_ROOTS`，并在受信反向代理后提供 TLS；`/health` 仅用于探活，`/start` 和 `/result` 需要 Bearer token。

## 许可协议

本项目的软件代码采用 PolyForm Noncommercial 1.0.0（`PolyForm-Noncommercial-1.0.0`）。商业使用需另行书面授权；完整条款见 [LICENSE](LICENSE)。

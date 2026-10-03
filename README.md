# Spec Harness

[![License: Non-Commercial](https://img.shields.io/badge/License-Non--Commercial-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[🇬🇧 Read this in English](README-en.md)

Spec Harness 是一个适用于 Codex、Claude Code、Gemini CLI、Grok Build、OpenCode、OpenClaw、Hermes、Pi 及其他支持 Skill 的 CLI 的工作流 Skill，专为规范驱动开发（specification-driven development）设计。它可以帮助 AI 编程智能体在开始实现代码之前，将复杂的工作转化为可追踪的任务包，从而保持范围、任务、验证、归档输出和 Git 提交的一致性。

阅读路线：[项目介绍](docs/introduction.md) · [长程任务教程](docs/tutorial.md) · [Git 工作流](docs/git-workflow.md)

该仓库是 Spec Harness 的源码包；`spec` 保留为兼容 Skill/CLI 调用名。它包含 Skill 的入口文件、参考文档、OpenAI 智能体元数据、标准库 Python 辅助脚本以及可选的 self-hosted server-mode 适配器；它不运营托管服务或包管理器插件。

> **范围与其他 `spec` Skill 的对比**：本 Skill 是一个**任务包工作流管理器**——它负责创建并追踪 `.spec/specs/YYYY-MM-DD_slug/{spec,tasks,checklist}.md` Development Record，并通过 `scripts/` 暴露内部 route/status/tasks/check/done/push 等脚本视图。它**不**充当“分发给其他专业 Skill 的中央路由器”。如果您也使用基于 `/spec` 路由的变体（例如位于 `~/.claude/skills/spec` 下的某些个人 ECC 配置），请确保您清楚当前宿主加载的是哪一个；它们名称相同但职责不同，不应混用。Slug 命名遵循 `[a-z0-9_-]+` 规则（参见 `scripts/spec_package_support.validate_slug`）；允许使用下划线，但不允许连续的连字符/下划线以及开头/结尾的分隔符；Development Record 标准额外要求使用 `YYYY-MM-DD_<verb>-<object>`（verb 取受控动词，详见 `references/naming-and-commits.md`）。

## 核心功能

- **单编排者工作流（默认）+ 受管委派**：主会话负责关键路径、路由、验收与 done/push 门禁。`scripts/route_decision.py` 把任务文本机器判定为五路由之一（`local / explore / build / review / external`）；`explore/build/review` 可按 assignment contract（`references/orchestration.md`）派发有界 sidecar lane；循环收敛类任务命中扩展插槽（如 `team-loop`）triggers 时按该插槽受管协议接管执行段。委派永不移走路由、验收与门禁授权。
- 用 `/spec` 内部路由用户意图，暴露 `new`、`goal`、`run`、`check`、`done`、`push`、`update`、`status`、`doctor`、`organize` 十个用户阶段。
- 创建包含 `spec.md`、`tasks.md` 和 `checklist.md` 的 `.spec/specs/YYYY-MM-DD_slug/` Development Record。
- 基于任务包文件渲染状态总览、任务进度、验证以及完成摘要。
- 强制执行包含显式假设、最小化实现、清晰边界、验证证据以及跨制品一致性的文档化工作流。
- 强制机器可验证的问题闭环：执行中发现的问题要么指向当前已完成 task，要么由本次链路中已执行并归档的 follow-up Development Record 承接；关闭态统一使用五类 disposition，archive、Stop hook、server projection 与 Git push 共享同一门禁，不能把自由"后续事项"交给用户。
- 任务合同最小化：每个任务只需 `boundary`（允许改什么）与 `verify`（怎么证明完成），可选 `id` 与 `depends-on`；勾选前必须真实运行验证。
- 生成 `completion-summary.md`，并将已完成的任务包归档至 `.spec/specs/archive/`。
- 执行治理层：`check` 输出确定性收敛状态（已收敛/未收敛+差距清单，`--format json` 提供 `converged`/`gaps`）；`hooks/claude_stop_guard.py` 按 Claude Code 官方 Stop hook 协议阻断对未收敛任务包的完成宣称并回报差距清单。
- 标准化 `.spec/docs/` 知识沉淀与 `.spec/architecture/` Module DAG：任务包记录"做了什么"，docs 记录"学到了什么"，architecture 记录"系统现在长什么样"。
- 对"当前项目结构是否合理"类任务提供第一性原理治理：先审计职责边界、依赖方向、运行时/源码/状态分离和入口可发现性；只有证明不合理时才重排目录，并在迁移前后验证引用路径、导出包、Module DAG、README 与测试。
- 在 Git 仓库中固化分支治理：`/spec:new` 先要求 clean tree，切回 `main`（或显式主分支）并快进到 upstream，然后从主分支创建独立 `spec/YYYY-MM-DD_<slug>` integration branch；upstream `fetch` 因传输层不可达失败时改为从本地主分支创建并记录本地模式，认证/权限/非快进仍停止；`run/check/done` 在该分支完成，`push` 只合并 `spec/` 前缀分支。
- 支持在完成摘要中配置 Git 记录标签：默认 `auto` 从 `spec.md` 标题自动检测中英文，也可用 `--git-record-language zh|en` 显式指定；提交文案采用 Conventional Commits（详见 `references/naming-and-commits.md`）。
- 在 `spec.md` 不再包含模板占位符之前，保持新任务包处于“澄清优先”状态。
- **0.13.0 门禁与卫生强化**：验收证据带新鲜度锚点（HEAD 移动即判“证据过期需重跑取证”）；`spec.md` 未勾功能框纳入 check 门禁（fail-closed，含 legacy 基线等价豁免）；`check` 失败项四字段结构化回写并受双重停止条件约束（语义终止 + 轮次预算）；凭证卫生纪律禁止秘密进入任务包、命令行、验收证据与蒸馏；新增任务形状→执行面判定表，`route/loop/workflow` 三层决策器组合语义由 GOLDEN_TRIPLES 交叉一致性测试钉死。

## 目录结构

```text
.
├── SKILL.md
├── install.sh
├── pyproject.toml
├── agent-plugin/                    # 源码库独有：Agent 插件清单模板
│   ├── plugin.json.template
│   ├── claude-plugin.json.template
│   └── README.md
├── agents/
│   ├── openai.yaml
│   ├── orchestrator.md
│   ├── planner.md
│   ├── reviewer.md
│   └── confirmer.md
├── book/                            # 源码库独有：培训电子书（承接旧 HTML 培训页）
│   ├── README.md
│   ├── verify_ebook.py
│   ├── metadata.yaml
│   ├── epub.css
│   ├── src/                         # 书稿章节与附录
│   ├── examples/                    # 教学示例程序
│   └── tests/                       # 构建验证测试
├── docs/                            # 面向用户与贡献者的公开文档
│   ├── git-workflow.md
│   └── git-workflow.en.md
├── hooks/
│   ├── pre-commit
│   ├── pre-push
│   ├── claude_stop_guard.py
│   └── spec_disk_truth_gate.py
├── references/
│   ├── 00-readme.md
│   ├── changelog-guide.md
│   ├── commands.md
│   ├── engineering-philosophy.md
│   ├── naming-and-commits.md
│   ├── operating-rules.md
│   ├── output-contracts.md
│   ├── slots.md
│   ├── storage-and-archive.md
│   ├── templates.md
│   └── orchestration.md
├── scripts/
│   ├── build_release.py             # 源码库独有打包器（不进运行时导出）
│   ├── check_all_spec_packages.py
│   ├── check_spec_package.py
│   ├── complete_spec_package.py
│   ├── dashboard_support.py
│   ├── doctor_spec_environment.py
│   ├── export_skill_package.py      # 源码库独有打包器（不进运行时导出）
│   ├── generate_changelog.py
│   ├── import_kiro_specs.py         # 源码库独有一次性迁移工具（不进运行时导出）
│   ├── init_spec_package.py
│   ├── install_git_hooks.py
│   ├── install_slot_hooks.py
│   ├── issue_closure_support.py
│   ├── migrate_task_ids.py           # 源码库独有一次性迁移工具（不进运行时导出）
│   ├── organize_project_structure.py  # /spec:organize 结构审计事实引擎
│   ├── package_agent_plugin.py      # 源码库独有打包器（不进运行时导出）
│   ├── gitea_hook_repair.py
│   ├── path_safety.py
│   ├── payload_contract.py
│   ├── push_spec_package.py
│   ├── read_version.py
│   ├── report_spec_package.py
│   ├── route_decision.py
│   ├── route_spec_package.py
│   ├── safe_open_support.py
│   ├── slot_registry.py
│   ├── smoke_test_spec_skill.py
│   ├── spec_package_support.py
│   ├── update_checkpoint.py
│   └── update_checkpoint_support.py
├── server/
│   ├── README.md
│   ├── install.sh
│   └── server.py
├── slots/                           # 可插拔插槽（随运行时包分发；契约见 references/slots.md）
│   ├── team-loop/                   # agents-team 循环触发与高效管理
│   └── workflow-runner/             # 仓库自有确定性 fan-out 执行段（并行评审/收敛）
├── tests/                           # 源码库独有：pytest 测试套件（含 fixtures/kiro）
├── release/                         # 源码库独有：跟踪的发布产物（spec-harness-{version} 四件，与 canonical 构建逐字节一致）
├── .github/                         # 源码库独有：CI 与 Issue 模板
└── .github/                         # GitHub Issue/PR 模板
```

运行时任务包默认创建在目标项目的 `.spec/` 下；公共仓库不包含维护记录和用户项目状态。根级 `CONTRIBUTING.md`、`RELEASE.md`、`SECURITY.md`、`SUPPORT.md` 与 `docs/` 提供贡献、版本、安全和 Git 使用说明。

- **任务包是状态真源**：`.spec/specs/<slug>/tasks.md` 是任务合同真源；勾选状态、验收证据与完成总结都随包保存。每个 Development Record 独占一个 integration branch；默认路由与 Stop guard 按"显式 `--slug` > `spec.md` 记录的当前分支 > 旧包 `spec/<slug>` 惯例 > 非 Git 唯一包"选择且最多一个包，歧义时 fail closed。

## 环境要求


- Python 3.9 或更高版本。
- 支持加载 `SKILL.md` 格式 Skill 的 CLI（Claude Code、Codex、Gemini CLI、Grok Build、OpenCode、OpenClaw、Hermes、Pi、ZCode 等）。
- 辅助脚本无需任何第三方 Python 依赖。
- 安装器与辅助脚本可在 Linux、macOS 与 Windows 上运行。

## 安装说明

### 安装器

从源码仓库根目录安装到 Claude Code 和 Codex：

```bash
bash install.sh
```

当前运行时包只保留这一个安装入口：根 `install.sh`。导出包与 release 产物不再附带单独的 `scripts/install.sh` 兼容壳。

远程安装或更新时，建议先拉取仓库并检出完整提交 SHA，再在本地执行安装脚本：

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness-src
cd /tmp/spec-harness-src
git checkout v0.13.10
bash install.sh
```

可通过环境变量选择宿主和路径：

```bash
INSTALL_HOSTS=claude CLAUDE_SKILLS_DIR=~/.claude/skills bash install.sh
INSTALL_HOSTS=claude-desktop bash install.sh
INSTALL_HOSTS=codex CODEX_SKILLS_DIR=~/.codex/skills bash install.sh
INSTALL_HOSTS=codex-desktop bash install.sh
INSTALL_HOSTS=desktop bash install.sh
INSTALL_HOSTS=gemini bash install.sh
INSTALL_HOSTS=grok bash install.sh
INSTALL_HOSTS=opencode bash install.sh
INSTALL_HOSTS=openclaw bash install.sh
INSTALL_HOSTS=hermes bash install.sh
INSTALL_HOSTS=pi bash install.sh
INSTALL_HOSTS=all bash install.sh
```

支持以下宿主，安装到各 Agent CLI 的标准 Skills 目录：

| 宿主 token | 应用 | Skills 目录 | 说明 |
| --- | --- | --- | --- |
| `claude` | Claude Code | `~/.claude/skills` | 额外生成 `/spec` 与十个 `/spec:<stage>` 命令文件 |
| `claude-desktop` | Claude Desktop | `~/.claude-desktop/skills` | 仅安装 skill，无命令文件 |
| `codex`、`codex-desktop` | Codex | `~/.codex/skills` | 两个 token 同目录 |
| `gemini` | Gemini CLI | `~/.gemini/skills` | |
| `grok`、`grokbuild` | Grok Build | `~/.grok/skills` | `grokbuild` 为同义别名 token |
| `opencode` | OpenCode | `${XDG_CONFIG_HOME:-~/.config}/opencode/skills` | |
| `openclaw` | OpenClaw | `~/.openclaw/skills` | |
| `hermes` | Hermes | Hermes 主目录 `/skills`（`HERMES_HOME` 优先，Windows 为 `%LOCALAPPDATA%\hermes`，其余 `~/.hermes`） | |
| `pi` | Pi agent | `~/.pi/agent/skills`（可经 `PI_DIR` 重定位） | |
| `zcode` | ZCode | `~/.zcode/skills` | 本项目自有宿主 |

组合目标：`desktop` 与 `rpi` 都会一次安装 Claude Code 与 Codex 两套默认目录；`rpi` 承接旧 `pi` token 的 Raspberry Pi / Linux 组合语义（不做硬件探测）。`all`（默认）安装上表全部宿主；只想覆盖部分宿主时显式传 `INSTALL_HOSTS`。每个宿主目录都可用同名环境变量覆盖（如 `GEMINI_SKILLS_DIR`、`OPENCODE_SKILLS_DIR`、`PI_SKILLS_DIR`、`HERMES_SKILLS_DIR`），非默认路径需 `FORCE=1`。

**迁移说明（行为变更）**：`pi` 原先是“Claude + Codex 双目录”的组合 token，现为 Pi agent 宿主（只写 `~/.pi/agent/skills`）；旧行为请改用 `rpi` 或 `desktop`。`claude-desktop` 原先写入 `~/.claude/{skills,commands}`，现为独立的 Claude Desktop 宿主（只写 `~/.claude-desktop/skills`）；需要旧行为请改用 `claude`。

Claude Code 会生成 `/spec` 路由入口，以及十个用户阶段命令：`/spec:new`、`/spec:goal`、`/spec:run`、`/spec:check`、`/spec:done`、`/spec:push`、`/spec:update`、`/spec:status`、`/spec:doctor`（环境自检修复）、`/spec:organize`（默认新建任务包，废弃资产归档到 `archive/retired`，never delete）；`route` 作为 `/spec` 内部逻辑，`tasks` 并入 `status` 总览；`goal` 是显式 one-shot 综合入口。其余宿主按各自 CLI 的 skill 触发方式加载（Codex/ZCode/Pi 用 `$spec`，Gemini CLI、Grok Build、OpenCode、OpenClaw、Hermes 按各自 skill 发现机制触发），阶段命令统一使用 `spec:<stage>` 别名。若目标目录由本安装器创建，会先移动到 `~/.spec-skill-backups/` 再写入新版本。若目标目录没有 `.spec-skill-install` 所有权标记，安装器默认拒绝覆盖；确认要替换用户自有目录时再设置 `FORCE=1`。如果目标目录已包含**不同的同名 Skill**（存在自己的 `SKILL.md` 但没有安装器所有权标记，例如另一个 `/spec` 路由变体），安装器会警告并拒绝静默覆盖；只有在确认替换时才设置 `FORCE=1`，并先把旧目录备份到 `~/.spec-skill-backups/`。


### 手动安装

导出纯净的运行时 Skill 包：

```bash
python3 scripts/export_skill_package.py --output /tmp/spec --force
```

将导出的目录安装到您的 CLI 的本地 Skill 目录中。具体路径取决于宿主 CLI，但安装后的 Skill 根目录应包含：

```text
spec/
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
│   ├── claude_stop_guard.py
│   └── spec_disk_truth_gate.py
├── references/
│   ├── 00-readme.md
│   ├── changelog-guide.md
│   ├── commands.md
│   ├── engineering-philosophy.md
│   ├── naming-and-commits.md
│   ├── operating-rules.md
│   ├── output-contracts.md
│   ├── slots.md
│   ├── storage-and-archive.md
│   ├── templates.md
│   └── orchestration.md
├── scripts/
│   ├── check_all_spec_packages.py
│   ├── check_spec_package.py
│   ├── complete_spec_package.py
│   ├── dashboard_support.py
│   ├── doctor_spec_environment.py
│   ├── generate_changelog.py
│   ├── init_spec_package.py
│   ├── install_git_hooks.py
│   ├── install_slot_hooks.py
│   ├── issue_closure_support.py
│   ├── organize_project_structure.py
│   ├── gitea_hook_repair.py
│   ├── path_safety.py
│   ├── payload_contract.py
│   ├── push_spec_package.py
│   ├── read_version.py
│   ├── report_spec_package.py
│   ├── route_decision.py
│   ├── route_spec_package.py
│   ├── safe_open_support.py
│   ├── slot_registry.py
│   ├── smoke_test_spec_skill.py
│   ├── spec_package_support.py
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
    │   ├── references/
    │   │   └── prior-art.md
    │   ├── hooks/
    │   │   ├── loop_route_hook.py
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
    │       ├── test_hooks.py
    │       ├── test_loop_control.py
    │       ├── test_loop_route.py
    │       └── test_loop_state.py
    └── workflow-runner/
        ├── manifest.json
        ├── README.md
        ├── hooks/
        │   └── workflow_route_hook.py
        ├── scripts/
        │   ├── workflow_fanout.py
        │   └── workflow_route.py
        └── tests/
            ├── conftest.py
            ├── test_workflow_fanout.py
            ├── test_workflow_hook.py
            └── test_workflow_route.py
```

导出内容由 `scripts/export_skill_package.py` 的机器规则决定：根文件为 `SKILL.md`、`install.sh`、`pyproject.toml`；`agents/`、`hooks/`、`references/`、`server/`、`slots/` 整目录进入；`scripts/` 目录包含源码库 `scripts/` 下除七个源码库独有脚本（`export_skill_package.py`、`export_public_repo.py`、`build_release.py`、`package_agent_plugin.py`、`skill_watermark.py`、`import_kiro_specs.py`、`migrate_task_ids.py`）之外的全部脚本；`agent-plugin/`、`tests/`、`release/`、`book/` 等源码库目录不进入运行时包。上述导出树由测试锁定与导出器实际输出逐文件一致。

对于 Codex 风格的 Skill 加载，将导出的目录放置在本地 Skill 根目录下，然后触发：

```text
$spec
spec:goal add payment recovery
spec:new
```

对于 Claude Code 风格的工作流，在 Skill 可用于宿主环境后，使用映射的斜杠命令：

```text
/spec
/spec:goal <goal>
/spec:new
/spec:run
/spec:check
/spec:done
/spec:push
/spec:update
/spec:status
/spec:doctor
/spec:organize
```

安装后，验证运行时包是否可以初始化任务包：

```bash
python3 /path/to/spec/scripts/init_spec_package.py \
  --root /tmp/spec-install-check \
  --slug 2026-06-12_install-check \
  --title "Install Check"
```

## 快速开始

在目标项目中创建一个新的任务包。以下脚本命令从本 skill 源码仓库或导出的运行时包根目录运行，`--root` 指向实际目标项目；已安装到宿主 CLI 后，常规入口是 `/spec:goal <goal>`、`/spec:new` 或 `$spec` 后的 `spec:goal` / `spec:new`。

```bash
python3 scripts/init_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery --title "Payment Recovery"
```

在 Git 仓库中，初始化脚本会先切回 `main`（或 `--main-branch` 指定的主分支），只允许快进到 upstream，然后创建并切换到独立 `spec/<slug>` 分支，再写入 `.spec` 任务包。若 `fetch` 因传输层不可达失败（与 `push` 相同判定），脚本改为从本地主分支建分支，并在输出与 `spec.md` 标明本地模式；工作树不干净、目标分支已存在、认证/权限失败或主分支无法快进时仍会停止，避免把其他分支上的未提交改动或分叉主线混入新包。需要自定义分支时传 `--branch spec/2026-06-12_payment-recovery`。`push` 只合并 `spec/` 前缀分支，其他分支会被拒绝并提示改名；2026-08-27（UTC）之前开始的存量 `feature/` 分支在宽限期内仍可合并，输出建议改名的提示。


一条龙入口会由智能体按现有门禁自动串联规划、任务包创建/恢复、执行、验收、归档、提交和 `push` 收尾；它不是独立脚本，也不会绕过 Git hooks：

```text
$spec
spec:goal add payment recovery with tests
# Claude Code: /spec:goal add payment recovery with tests
```

检查下一个工作流阶段：

```bash
python3 scripts/route_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery
```

渲染状态或任务：

```bash
python3 scripts/report_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery --view status
python3 scripts/report_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery --view tasks
```

运行单包验证与全仓磁盘门禁：

```bash
python3 scripts/check_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery
python3 scripts/check_all_spec_packages.py --root /path/to/project
```

`check_spec_package.py` 是 `/spec:check` 的单包机器真源。`check_all_spec_packages.py` 还验证 active/archive 四件套、新 archive 的 v1 问题处置、显式 slug 命中，并可用 `--revision <sha/ref>` 校验目标 Git 树。门禁细节以脚本输出为准。

为目标仓库增量安装最终 Git 执行点（同内容幂等；已有不同 hook 时拒绝覆盖并提示手工链入）：

```bash
python3 scripts/install_git_hooks.py --root /path/to/project
```

`pre-commit` 同时验证工作树与 Git index；`pre-push` 逐个待推 ref 验证该 ref 的目标 SHA，并在线 fetch/固定实际 push remote 的主分支 SHA作为不可覆盖的 legacy 基线；真正 local-only 才显式选择本地主分支。默认使用 trusted `.spec/.trae` touched + commit footer 归属，`SPEC_PUSH_GATE=all` 对每个目标 SHA执行全仓门禁。额外可信根在 push 脚本使用可重复 `--specs-dir`，hook 使用 `SPEC_SPECS_DIRS=path1,path2`。Claude Stop 从嵌套目录识别这些根，非终态停止需唯一引用真实 `slug#task_XXXX` 或 archived external blocker issue ID。

为通过完成网关的包生成完成摘要：

```bash
python3 scripts/complete_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery
```

归档已完成的任务包：

```bash
python3 scripts/complete_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery --archive
```

归档时脚本自动从已通过的 triad 填充非占位总结，并写入 v1 `## 问题处置`。如存在风险、外部依赖或已由后续包完成的问题，使用可重复的 `--issue-disposition '<json>'`；五类为 `resolved_current`、`resolved_followup`、`accepted_risk`、`external_blocked`、`non_actionable`。`resolved_followup` 目标必须已经执行、验收并归档；archive 拒绝自由 `--follow-up` / `--open-risk` / `--not-delivered` / `--deviation`。

使用英文 Git 记录标签：

```bash
python3 scripts/complete_spec_package.py --root /path/to/project --slug 2026-06-12_payment-recovery --archive --git-record-language en
```

`done` 归档后完成本轮提交。工作分支已提交后，执行合并清理流程：

```bash
python3 scripts/push_spec_package.py --root /path/to/project --branch spec/2026-06-12_payment-recovery
```

安全预检、dirty-tree / retro-pack 和未归档阻断以 `push_spec_package.py` 为准。远端检查失败时保留工作分支并先修复原因；禁止使用 `--no-verify` 绕过门禁。

任务包辅助脚本接受 `--root`（项目根目录，默认 `.`）和 `--specs-dir`（root 下用于规范存储的可信相对目录，默认 `.spec`；拒绝空值、绝对路径、`..` 穿越以及 `.git/.claude/node_modules/build/dist/vendor` 等门禁排除目录，且真实解析后的路径必须仍在 root 下）。`--allow-incomplete` 只能生成未完成摘要草稿，不能与 `--archive` 组合。

在支持 Skill 的 CLI 中，通过 `$spec` 触发，然后使用阶段别名：`spec:new`、`spec:goal`、`spec:run`、`spec:check`、`spec:done`、`spec:push`、`spec:update`、`spec:status`、`spec:doctor`、`spec:organize`。对于 Claude Code，这些对应于 `/spec`、`/spec:goal`、`/spec:new`、`/spec:run`、`/spec:check`、`/spec:done`、`/spec:push`、`/spec:update`、`/spec:status`、`/spec:doctor`、`/spec:organize`；route 是 `/spec` 的内部阶段解析器 / 边决策器，任务明细并入 `status` 总览。`done` 归档后完成本轮提交，再交给 `push` 脚本收尾；`goal` 复用同一条 done/commit/push 链路；任何验证或安全预检失败都会停止。

## 导出运行时 Skill 包

若仅导出运行时 Skill 文件：

```bash
python3 scripts/export_skill_package.py --output /tmp/spec --force
```

导出的内容包含 `SKILL.md`、根 `install.sh`、`pyproject.toml`、`agents/`、`hooks/`、`references/`、`server/`、`slots/` 及运行时辅助脚本，并排除只用于源码维护的打包工具。运行时安装入口统一为包根目录的 `install.sh`（本地 skill 安装）；`server/` 则是第二种形态——server-mode，把任务包初始化和状态投影暴露为 `start`/`result`/`health` 三个 HTTP 接口，服务端仍负责后续 `run`/`check`/`done`，详见 [`server/README.md`](server/README.md)。两种形态共用同一套 `scripts` 与 `references`。

导出的运行时包必须包含 `issue_closure_support.py` 与 `spec_package_support.py`；否则 completion/server/Git closeout 或 init/route/report/check 将无法共享机器闭环语义。

运行时包验证：

```bash
python3 scripts/smoke_test_spec_skill.py
```

导出包 smoke 会独立验证运行时布局、doctor 自检，并完整执行任务包初始化流程。


辅助脚本特意使用了标准库 Python。基础的语法检查命令为：

```bash
python3 -m py_compile scripts/*.py server/*.py
bash -n server/install.sh
```

仓库级回归验证需要开发依赖：

```bash
python3 -m pip install -e '.[dev]'
```

随后运行 smoke、pytest 与 lint：

```bash
python3 scripts/smoke_test_spec_skill.py
python3 -m pytest tests/
ruff check scripts/ server/ hooks/ tests/ book/
ruff format --check scripts/ server/ hooks/ tests/
```

`smoke_test_spec_skill.py` 验证任务包/导出包生命周期；pytest 覆盖任务包门禁、路由/状态/check/归档全链路、update checkpoint 和 Git 门禁。

要手工检查行为，可用 `init_spec_package.py` 创建一个临时任务包，然后对其运行 route、report、check 和 complete 脚本。新生成的包在完成任务与 checklist 前无法通过单包门禁；push 验证目标 revision 中的 archive/commit 归属，未归档活跃包默认硬失败，显式 `--allow-unarchived` 时才 advisory；损坏包、缺失显式 slug、无效 v1 closeout 与无归属 commit 始终拦截。`--allow-incomplete --force` 仅用于生成摘要草稿，不能归档。

## 获取版本

稳定版本和校验文件位于 [GitHub Releases](https://github.com/MicTx/spec-harness/releases)。安装、升级、回滚和 SHA-256 校验步骤见 [RELEASE.md](RELEASE.md)。

需要参与开发时，请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [Git 工作流](docs/git-workflow.md)；安全问题请按 [SECURITY.md](SECURITY.md) 报告。

## 适用范围

本项目提供工作流指令、模板、验证脚本，以及一个可选的 self-hosted server-mode 适配器，为远端平台初始化并投影任务包状态；它不运营托管服务，也不派生后台模型任务。

本项目不提供 PR 创建、远程 CI 集成或包注册表发布；`spec:push` 仅提供本地 Git 合并/推送/删分支收尾，并保留强安全预检。`server/` 是可选的 self-hosted HTTP 接入层：`/start` 与 `/result` 需要 `SPEC_SERVER_TOKEN` Bearer 认证（`/health` 保持匿名供探活），未设 token 且监听非 loopback 地址时适配器拒绝启动，子进程并发受 `MAX_CONCURRENT_WORK` 上限约束（超限返回 429）；公网部署必须配置 `ALLOWED_ROOTS`，并建议由受信反向代理提供 TLS。

## 许可协议

本项目采用非商用源码许可，禁止销售和商业使用；它不等同于 OSI 定义的开放源代码许可。完整条款请参阅 [LICENSE](LICENSE)。

### 稳定身份与验证

可选 `id: task-name` 和显式 depends-on 保留任务身份与依赖；迁移默认 dry-run，不修改历史归档。自报结果不能完成任务：勾选前必须真实运行任务 `verify` 描述的验证。

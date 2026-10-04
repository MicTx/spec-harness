# Spec Harness

面向 AI 编程智能体的任务包工作流工具，用可验证的范围、证据和 Git 门禁管理长期改动。

[![License: Non-Commercial](https://img.shields.io/badge/License-Non--Commercial-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[🇬🇧 Read this in English](README-en.md)

Spec Harness 把跨会话、跨测试轮次的工程任务保存为可恢复的 Development Record。它提供任务包、路由、验证、归档和 Git 收尾，不提供模型，也不替用户做产品决策。

最短路径：阅读[项目介绍](docs/introduction.md)，按[长程任务教程](docs/tutorial.md)安装并运行一次；需要发布或回滚时阅读 [Git 工作流](docs/git-workflow.md)。所有公开入口见[文档索引](docs/README.md)。

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

- **一个任务包**：在 `.spec/specs/YYYY-MM-DD_slug/` 中保存 `spec.md`、`tasks.md` 和 `checklist.md`。
- **一条可验证链**：`目标 → 范围 → 任务 → 验证 → 归档 → 提交 → 推送`，每个任务都声明 `boundary` 与 `verify`。
- **明确的入口**：`new`、`goal`、`run`、`check`、`done`、`push`、`update`、`status`、`doctor`、`organize`。
- **可恢复的状态**：报告、失败原因、完成摘要和归档都来自磁盘文件，换会话仍能继续。
- **受控的执行面**：主会话保留路由、集成、验收和 done/push 门禁；`workflow-runner` 与其他 slots 只执行有边界的工作段。
- **两种运行形态**：本地 Skill 是参考实现；可选 `server/` 适配器只负责远端任务包的 start/result/health 投影。

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
├── docs/                            # 面向用户与贡献者的公开文档
│   ├── README.md
│   ├── introduction.md
│   ├── tutorial.md
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
│   ├── init_spec_package.py
│   ├── install_git_hooks.py
│   ├── install_slot_hooks.py
│   ├── issue_closure_support.py
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
├── tests/                           # 源码库独有：pytest 测试套件
├── release/                         # 源码库独有：跟踪的发布产物（spec-harness-{version} 四件，与 canonical 构建逐字节一致）
└── .github/                         # CI 与 Issue/PR 模板
```

运行时任务包默认写入目标项目的 `.spec/`；源码仓库本身不包含用户项目状态。详细契约在 [`references/`](references/00-readme.md)，面向人的教程在 [`docs/`](docs/README.md)。

## 环境要求

- Python 3.9 或更高版本。
- 能加载 `SKILL.md` 的宿主 CLI，例如 Claude Code、Codex、Gemini CLI、Grok Build、OpenCode、OpenClaw、Hermes、Pi 或 ZCode。
- 辅助脚本只使用 Python 标准库；开发测试额外需要 `pytest` 和 `ruff`。
- 安装器与辅助脚本支持 Linux、macOS 和 Windows。

## 安装说明

### 安装器

从源码仓库根目录运行：

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
git checkout v0.13.10
bash install.sh
```

`install.sh` 是本地 Skill 的唯一安装入口；可选的 `server/install.sh` 只用于部署 self-hosted server-mode。更新或回滚时切换到目标 tag 后重新运行安装器。

### 支持的宿主

| `INSTALL_HOSTS` | 宿主 | 默认目录 | 额外行为 |
| --- | --- | --- | --- |
| `claude` | Claude Code | `~/.claude/skills` | 生成 `/spec` 与十个阶段命令 |
| `claude-desktop` | Claude Desktop | `~/.claude-desktop/skills` | 只安装 Skill |
| `codex`、`codex-desktop` | Codex | `~/.codex/skills` | 两个 token 共用目录 |
| `gemini` | Gemini CLI | `~/.gemini/skills` | |
| `grok`、`grokbuild` | Grok Build | `~/.grok/skills` | `grokbuild` 是别名 |
| `opencode` | OpenCode | `${XDG_CONFIG_HOME:-~/.config}/opencode/skills` | |
| `openclaw` | OpenClaw | `~/.openclaw/skills` | |
| `hermes` | Hermes | `~/.hermes/skills` | 可用 `HERMES_HOME` 重定位 |
| `pi` | Pi agent | `~/.pi/agent/skills` | 可用 `PI_DIR` 重定位 |
| `zcode` | ZCode | `~/.zcode/skills` | 项目自有宿主 |
| `desktop`、`rpi` | Claude Code + Codex | 两套默认目录 | 组合目标 |
| `all` | 全部宿主 | 按上表写入 | 默认值 |

只安装一个宿主时显式指定 token，例如：

```bash
INSTALL_HOSTS=codex CODEX_SKILLS_DIR=~/.codex/skills bash install.sh
```

安装器默认拒绝覆盖没有 `.spec-skill-install` 所有权标记的同名 Skill；确认替换用户目录时才设置 `FORCE=1`。`pi` 现在表示 Pi agent，旧的 Claude + Codex 组合请使用 `rpi` 或 `desktop`。

### 手动安装

只导出运行时 Skill 包：

```bash
python3 scripts/export_skill_package.py --output /tmp/spec --force
```

把导出的 `spec/` 目录放入宿主的 Skill 根目录，然后在 Codex/Pi/ZCode 中触发 `$spec`，或在 Claude Code 中使用 `/spec:goal`。

## 快速开始

最短的可复现流程是在目标项目中创建一个任务包：

```bash
python3 /tmp/spec-harness/scripts/init_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_payment-recovery \
  --title "Payment Recovery"
```

也可以直接让宿主执行：

```text
$spec
spec:goal add payment recovery with tests
# Claude Code: /spec:goal add payment recovery with tests
```

初始化后，按顺序查看阶段、状态和门禁：

```bash
python3 /tmp/spec-harness/scripts/route_spec_package.py --root /path/to/project --slug 2026-10-03_payment-recovery
python3 /tmp/spec-harness/scripts/report_spec_package.py --root /path/to/project --slug 2026-10-03_payment-recovery --view status
python3 /tmp/spec-harness/scripts/check_spec_package.py --root /path/to/project --slug 2026-10-03_payment-recovery
```

任务完成后可由 `/spec:done` 完成复核、归档和 Git 提交。直接操作时，先运行：

```bash
python3 /tmp/spec-harness/scripts/complete_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_payment-recovery \
  --archive
```

该脚本只负责生成摘要并归档；随后手动运行 `git commit`，再用 `push_spec_package.py` 做合并和推送收尾。废弃成员只归档到 `archive/retired`，遵循 `never delete` 原则；每个任务必须先真实运行 `verify`，不能只勾选复选框。

## 导出运行时 Skill 包

源码仓库同时包含打包器、测试和发布产物；运行时包由 `scripts/export_skill_package.py` 的机器规则生成。它保留 `SKILL.md`、安装器、`agents/`、`hooks/`、`references/`、`server/`、`slots/` 和运行时辅助脚本，排除源码维护工具、测试和 `release/`。

导出树由测试锁定，当前内容如下：

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
python3 /path/to/repo-readme-skill/scripts/audit.py README.md --name spec-harness
python3 /path/to/repo-readme-skill/scripts/audit.py README-en.md --name spec-harness
git diff --check
```

完整验证范围以 [`CONTRIBUTING.md`](CONTRIBUTING.md) 为准；任务包的 `package`、`integration`、`project` 验证级别由 `spec.md` 的 `### 5.1 验证策略` 声明。

## 获取版本

稳定版本和 SHA-256 校验文件位于 [GitHub Releases](https://github.com/MicTx/spec-harness/releases)。安装、升级、回滚见 [RELEASE.md](RELEASE.md)；参与开发见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [Git 工作流](docs/git-workflow.md)。

## 适用范围

Spec Harness 提供任务包、工作流指令、模板、验证脚本和可选的 self-hosted server-mode 适配器。它不运营托管服务、不提供模型、不替用户做产品决策，也不负责 PR 创建或远程 CI 编排。

> [!WARNING]
> `server/` 是可选的自托管 HTTP 接入层。公网部署前必须设置 `SPEC_SERVER_TOKEN` 与 `ALLOWED_ROOTS`，并在受信反向代理后提供 TLS；`/health` 仅用于探活，`/start` 和 `/result` 需要 Bearer token。

## 许可协议

本项目采用非商用源码许可，禁止销售和商业使用；它不等同于 OSI 定义的开放源代码许可。完整条款见 [LICENSE](LICENSE)。

### 稳定身份与验证

可选 `id: task-name` 和显式 `depends-on` 保留任务身份与依赖；协议迁移不修改历史归档。自报结果不能完成任务：勾选前必须真实运行任务的 `verify`。

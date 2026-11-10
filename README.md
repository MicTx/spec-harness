# Spec Harness

面向 AI 编程智能体的任务包工作流工具，用可验证的范围、证据和 Git 门禁管理长期改动。

[![CI](https://img.shields.io/github/actions/workflow/status/MicTx/spec-harness/ci-public.yml)](https://github.com/MicTx/spec-harness/actions/workflows/ci-public.yml) [![Release](https://img.shields.io/github/v/release/MicTx/spec-harness)](https://github.com/MicTx/spec-harness/releases) [![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE) [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[English](README-en.md) · [文档索引](docs/README.md) · [项目介绍](docs/introduction.md) · [第一次使用](docs/getting-started.md)

它把跨会话工作保存为 `.spec/specs/YYYY-MM-DD_slug/` 下的 Development Record：`spec.md` 记录范围与成功标准，`tasks.md` 记录边界和验证命令，`runs.jsonl` 记录最终工作树上的执行结果。中断后从文件续跑，不靠回忆聊天。

- **小内核**：默认 LITE 模板，每个任务只需明确 `boundary` 与 `verify`。
- **结果可核对**：完成前统一执行验证；改了代码或验证命令，就重新取证。
- **收尾分离**：归档与本地提交属于 `done`；合并、推送和删分支必须单独授权。
- **小内核**：所有宿主获得同一六阶段入口，不附带规划链、slots 或代理角色扩展。

目录：[安装](#安装) · [快速开始](#快速开始) · [运行时载荷](#运行时载荷) · [验证与维护](#验证与维护) · [许可协议](#许可协议)

## 安装

需要 Python 3.9+、Git、Bash，以及具有项目读写和命令执行工具的 agent 客户端。Windows 使用 WSL2；普通只读聊天不能执行完整流程。

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
bash install.sh
```

安装器缺省 `INSTALL_PROFILE=core`；`core` 与 `full` 仅是兼容别名，两者的运行时载荷完全相同。未指定 `INSTALL_HOSTS` 时自动探测已安装宿主，指定 token 则只安装所选宿主。

| 宿主 | `INSTALL_HOSTS` | 默认 Skill 目录 |
| --- | --- | --- |
| Claude Code / Desktop | `claude` / `claude-desktop` | `~/.claude/skills` / `~/.claude-desktop/skills` |
| Codex / Gemini / Grok | `codex` / `gemini` / `grok` | `~/.codex/skills` / `~/.gemini/skills` / `~/.grok/skills` |
| OpenCode / OpenClaw / Hermes | `opencode` / `openclaw` / `hermes` | `${XDG_CONFIG_HOME:-~/.config}/opencode/skills` / `~/.openclaw/skills` / `~/.hermes/skills` |
| Pi / ZCode | `pi` / `zcode` | `~/.pi/agent/skills` / `~/.zcode/skills` |
| DeepSeek Harness / Devin / Cursor | `dsh` / `devin` / `cursor` | `${DSH_HOME:-~/.dsh}/skills` / `~/.config/devin/skills` / `~/.cursor/skills` |
| 组合 / 全部 | `desktop`、`rpi` / `all` | Claude Code + Codex / 全部目录 |

```bash
INSTALL_HOSTS=claude bash install.sh
```

**明确的入口**：`new`、`run`、`check`、`done`、`push`、`doctor`，共 6 个阶段命令。

安装器拒绝覆盖无所有权标记的同名 Skill；只有确认替换时才用 `FORCE=1`。安装测试不等于每个宿主已实机验收，dsh 仍为 developer preview。

## 快速开始

在目标项目中加载 Skill，再给出有边界的目标。Claude Code 安装可直接使用：

```text
/spec:new 修复导出 API 的权限校验，覆盖管理员与普通用户
/spec:run
/spec:check
/spec:done
```

Codex/ZCode 使用 `$spec`，Pi 使用 `/spec` 与 `/spec:<stage>`。其他宿主加载 Skill 后按[命令索引](references/commands.md)请求阶段。

第一次操作跟随 [LITE 实操教程](docs/getting-started.md)：创建包、填范围、改代码、`spec_verify.py run --all`、勾选、检查并归档。预期产物是可核对的完成记录，而不是一句“已完成”。单一小修、只读调查和一次性实验不必建包。

机器运行证据要求可计算的 Git 工作树指纹；非 Git 项目可做人工验证，但不能宣称代码变更后的证据仍新鲜。`push` 只在显式授权后执行，失败时以实际 Git 状态为准。

## 运行时载荷

运行时导出器与安装器使用同一[载荷契约](scripts/payload_contract.py)。`core` 与 `full` 的导出结果逐字节一致，顶层结构如下。

```text
spec/
├── LICENSE
├── SKILL.md
├── install.sh
├── pyproject.toml
├── agents/
├── hooks/
├── references/
└── scripts/
```

`agents/` 只保留宿主发现用的 `openai.yaml`。从源码只导出运行时文件：

```bash
python3 scripts/export_skill_package.py --output /tmp/spec
```

导出目录放入宿主的 Skill 根目录；安装器还会生成宿主阶段命令与安装清单。发布、水印、插件打包等维护工具不进入运行包。

## 验证与维护

```bash
python3 -m pytest -q tests
python3 -m ruff check scripts hooks tests
python3 scripts/smoke_test_spec_skill.py
```

完整验证矩阵见 [CONTRIBUTING.md](CONTRIBUTING.md)；发布、升级和回滚见 [RELEASE.md](RELEASE.md)，Git 收尾见 [Git 工作流](docs/git-workflow.md)。稳定发布与 SHA-256 文件见 [GitHub Releases](https://github.com/MicTx/spec-harness/releases)；仓库内 `release/` 是当前版本的 canonical 载荷。

旧活动包可用 `scripts/migrate_packages.py` 先 dry-run，再显式 `--apply`；无法可靠映射 checklist 的包保持原样，需手工补齐验收关系。历史归档不迁移也不重新评分。

Spec Harness 不提供模型或托管服务，不替用户做产品决策。可恢复记录和测试证据也不能代替对业务正确性的判断。

## 许可协议

软件代码采用 [PolyForm Noncommercial 1.0.0](LICENSE)；商业使用需另行书面授权。

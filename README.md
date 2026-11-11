# Spec Harness

面向 AI 编程智能体的两相任务包工作流：规划期做厚，执行期不问用户，直到可核对地交付。

[![Release](https://img.shields.io/github/v/release/MicTx/spec-harness)](https://github.com/MicTx/spec-harness/releases) [![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE) [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[English](README-en.md) · [文档索引](docs/README.md) · [快速开始](#快速开始)

一个包只包含 `.spec/specs/YYYY-MM-DD_slug/` 下的 `spec.md` 与 `tasks.md`；`runs.jsonl` 和 `state.json` 由 harness 写入。规划期用宪章与机器 lint 消除未知，Go 之后执行期按任务 DAG 自动推进、验证和检查点提交。

## 安装

需要 Python 3.9+、Git、Bash，以及能读写项目并执行命令的宿主。

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
bash install.sh
```

未设置 `INSTALL_HOSTS` 时自动探测宿主；`core` 与 `full` 是兼容别名，载荷完全相同。安装器增量合并会话 hook，保留非 Spec 条目并先备份。

| 宿主 | `INSTALL_HOSTS` |
| --- | --- |
| Claude Code / Desktop | `claude` / `claude-desktop` |
| Codex / Gemini / Grok | `codex` / `gemini` / `grok` |
| OpenCode / OpenClaw / Hermes | `opencode` / `openclaw` / `hermes` |
| Pi / ZCode / Devin | `pi` / `zcode` / `devin` |
| DeepSeek Harness / Cursor | `dsh` / `cursor` |

## 快速开始

在项目中启用 Skill 后，用一句话给出目标：

```text
/spec 修复导出 API 的权限校验，覆盖管理员与普通用户
```

没有活动包时 `/spec` 进入 `new` 规划流，绑定包未 Go 时进入 `update` 规划流：先做只读侦察与意图澄清，再把假设、分岔和任务逆向验证写成可判定字段，最后运行 `plan-go`。Go 之后不再问可逆问题；不可逆事项统一停泊，其他任务继续。

用户命令只有三个：

| 命令 | 用途 |
| --- | --- |
| `/spec <目标>` | 规划新包，或续跑当前分支绑定的包 |
| `/spec:push` | 用户明确授权后完成 Git 交付（按当前分支解析已归档包） |
| `/spec:doctor` | 检查并修复运行时与宿主 hook |

Codex/ZCode 可写 `$spec`。小修、只读调查和一次性实验不必建包。

## 运行时载荷

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

`references/` 只保留规划、执行、格式、哲学和 Git 五页。安装式宿主获得对应配置；OpenCode、Pi、OpenClaw、Hermes 使用转发垫片。

## 验证与维护

```bash
python3 -m pytest -q
python3 -m ruff check scripts hooks tests
python3 scripts/export_skill_package.py --output /tmp/spec
```

私有 Gitea 是开发真源；GitHub 只接收 allowlist 过滤后的公开快照。稳定版本与 SHA-256 见 [GitHub Releases](https://github.com/MicTx/spec-harness/releases)。

## 许可协议

软件代码采用 [PolyForm Noncommercial 1.0.0](LICENSE)；商业使用需另行书面授权。

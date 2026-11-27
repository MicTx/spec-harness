# Spec Harness

面向 AI 编程智能体的两相任务包工作流：先冻结目标和验收，再按证据执行、恢复和交付。

[![Public CI](https://github.com/MicTx/spec-harness/actions/workflows/ci-public.yml/badge.svg)](https://github.com/MicTx/spec-harness/actions/workflows/ci-public.yml) [![Release](https://img.shields.io/github/v/release/MicTx/spec-harness)](https://github.com/MicTx/spec-harness/releases) [![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE) [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[English](README-en.md) · [文档索引](docs/README.md) · [快速开始](#快速开始)

## 目录

- [安装](#安装)
- [更新](#更新)
- [快速开始](#快速开始)
- [运行时载荷](#运行时载荷)
- [验证与维护](#验证与维护)
- [许可协议](#许可协议)

任务包写清目标、范围、验收和可执行任务。Go 之后，智能体按依赖推进：真正跑过的验证才能推进任务，手工勾选或口头声称通过都不算。`/spec:check` 在实际要交付的代码树上复验，再归档。

**续跑看证据，不数轮数。** 受 Loop Transformer 启发，换策略时重新带上冻结的目标和受影响的验收条件。重复回调、候选没变或失败再次出现时，不会仅凭这些信号开启下一轮。这是智能体执行层的机制，不是替换或训练模型。[了解按证据续跑](docs/loop-continuity.zh.md)。

## 安装

需要 Python 3.9+、Git、Bash，以及能读写项目并执行命令的宿主。

下面的命令安装 `main` 源码，本页描述的快路径和动态续跑在这一渠道。想用稳定版，请按[发布包安装说明](RELEASE.md#install-from-a-release-archive)操作；`v2.2.0` 发布包不包含这些更新。

```bash
git clone https://github.com/MicTx/spec-harness.git
cd spec-harness
bash install.sh
```

未设置 `INSTALL_HOSTS` 时，安装器自动探测宿主。`INSTALL_PROFILE=core|full` 是兼容别名，安装同一载荷。安装器备份旧文件并只替换 Spec 自己的 hook；`DRY_RUN=1 bash install.sh` 可先查看目标。配置写入不代表宿主已加载；[宿主事件说明](docs/loop-continuity.zh.md#宿主与手动恢复)列出区别。

| 宿主 | `INSTALL_HOSTS` |
| --- | --- |
| Claude Code / Desktop | `claude` / `claude-desktop` |
| Codex / Gemini / Grok | `codex` / `gemini` / `grok` |
| OpenCode / OpenClaw / Hermes | `opencode` / `openclaw` / `hermes` |
| Pi / ZCode / Devin | `pi` / `zcode` / `devin` |
| DeepSeek Harness / Cursor | `dsh` / `cursor` |
| 通用 Agent 宿主（共享技能目录 `.agents/skills`） | `agents` |

## 更新

安装完成后，在任意目录运行：

```bash
spec-harness update           # 更新所有已安装的宿主到最新稳定版
spec-harness update --check   # 只检查版本
spec-harness update --dry-run # 下载并预览，不修改安装
spec-harness update --source china # 使用国内镜像
spec-harness --version        # 查看各宿主已安装版本
```

默认先连接 GitHub，不通时使用国内第三方镜像；镜像也可通过 `SPEC_UPDATE_SOURCE=china` 指定。命令校验发布包，保留各宿主配置并备份旧版本；完成后重启智能体会话。也可让智能体“更新 Spec Harness”。如果找不到命令，将 `~/.local/bin` 加入 `PATH`，或改用 `~/.local/bin/spec-harness update`。已有旧版需要先用包含此功能的版本运行一次 `bash install.sh`。升级、指定版本及回退详见 [RELEASE.md](RELEASE.md)。

## 快速开始

在项目中启用 Skill 后，用一句话给出目标：

```text
/spec 修复导出 API 的权限校验，覆盖管理员与普通用户
```

没有活动包时 `/spec` 进入 `plan` 规划流；已绑定但未 Go 的包进入 `update` 规划流（内部路由，不是命令）。Go 后保留冻结目标与验收。范围内的可逆事项由智能体处理，需要授权的事项停泊，独立任务继续。界面验收也使用普通任务和验证命令。

| 命令 | 用途 |
| --- | --- |
| `/spec <目标>` | 规划新包，或续跑当前分支绑定的包 |
| `/spec:plan <目标>` | 显式规划入口：创建或修订任务包，lint、定岔、`plan-go` |
| `/spec:run [目标]` | 继续绑定包；小型可逆修复可在当前检出原地验证，不自动提交 |
| `/spec:repair <归档包>` | 以 `kind: repair` 和 `repair-of` 元数据创建普通修复包，再走标准规划与门禁 |
| `/spec:check` | 按[恢复循环](references/exec.md)自行修复和复验，通过后归档并按 V4 交付；`--check-only` 仅诊断 |
| `/spec:push` | 用户明确授权后完成 Git 交付（按当前分支解析已归档包） |
| `/spec:doctor` | 检查并修复运行时、宿主 hook 与旧版 Git hook |
| `/spec /purge` | `spec.py purge`：证据记录退出 Git 索引，原文件留本地；`--check` 仅预览 |

Codex/ZCode 可写 `$spec`。小修、只读调查和一次性实验不必建包。

`python3 scripts/spec.py purge` 将 `.spec` 的任务包、规划、运行日志和历史记录切为本地证据，保留 `.spec/architecture` 源码契约。共享 Git exclude 覆盖 linked worktree；本仓库的 `.gitignore` 同时让新克隆继承规则。原文件字节保持不变，命令不提交、不推送，也不改写旧提交历史。后续 `verify` / `done` 只提交源码，本地归档在 worktree 清理前保存到主工作树。新克隆没有本机历史证据：历史专项审计显式跳过，新交付仍须通过完整证据门禁。

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
├── scripts/
└── updater/
```

`references/` 只保留规划、执行、格式、哲学和 Git 五页。安装式宿主获得对应配置；OpenCode、Pi、OpenClaw、Hermes 使用转发垫片。

## 验证与维护

```bash
python3 -m pytest -q
python3 -m ruff check scripts hooks updater tests
python3 scripts/export_skill_package.py --output /tmp/spec
```

GitHub `main` 是公开源码快照，不代表新版本已发布。稳定发布包和校验文件见 [GitHub Releases](https://github.com/MicTx/spec-harness/releases)；源码最新变化见 [CHANGELOG](CHANGELOG.md#unreleased)。

### 深入文档

贡献开发流程见[贡献指南](CONTRIBUTING.md)；操作指南按读者任务整理在[文档索引](docs/README.md)。

## 许可协议

软件代码采用 [PolyForm Noncommercial 1.0.0](LICENSE)；商业使用需另行书面授权。

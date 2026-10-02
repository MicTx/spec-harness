# agent-plugin/ — Agent 插件清单模板

本目录是 `scripts/package_agent_plugin.py` 的打包模板：脚本读取这里的清单模板，
渲染占位符、校验字段合法性，再组装本仓运行时 skill 载荷并产出可分发的插件 zip。

格式依据 Agent Plugins 1.0 与 Claude Code plugin manifest 规范：

| 模板文件 | 目标格式 | 产物清单位置 |
|---|---|---|
| `plugin.json.template` | Agent Plugins 1.0（主轨，默认） | 插件根 `plugin.json` |
| `claude-plugin.json.template` | Claude Code plugin | `.claude-plugin/plugin.json` |

## 占位符

| 占位符 | 含义 | 默认值来源 |
|---|---|---|
| `{{NAME}}` | 插件名 | 常量 `spec-harness`（Agent Plugins 侧须满足 1–64 字符、仅 `a-z0-9-.`、首尾字母数字、禁 `--`/`..`；Claude Code 侧须 kebab-case） |
| `{{VERSION}}` | 插件版本 | 仓库 `pyproject.toml` 的 `version` |
| `{{DESCRIPTION}}` | 简短描述 | `pyproject.toml` 的 `description` |
| `{{DISPLAY_NAME}}` | 人类可读名（仅 Claude Code 格式） | 常量 `Spec Harness` |
| `{{HOMEPAGE}}` / `{{REPOSITORY}}` | 主页 / 源码仓库 URL | 默认为 `https://github.com/MicTx/spec-harness`；显式传空值时该字段从清单中剔除 |
| `{{LICENSE}}` | SPDX 标识 | 常量 `LicenseRef-Spec-NonCommercial` |
| `{{KEYWORDS}}` | 检索标签（JSON 数组字面量） | 常量 `["spec", "task-package", "workflow", "agent-skills"]` |

## 构建命令

```bash
# 默认：Agent Plugins 1.0 格式，产出 dist/<name>-<version>-agent-plugins.zip
python3 scripts/package_agent_plugin.py

# Claude Code 格式
python3 scripts/package_agent_plugin.py --format claude-code

# 自定义输出与元数据
python3 scripts/package_agent_plugin.py --name spec-harness --version 1.2.3 \
    --output dist/spec-harness.zip --force
```

## 产物结构（agent-plugins 格式）

```text
<name>/
├── plugin.json            # 由 plugin.json.template 渲染并校验
├── README.md              # 脚本生成的安装说明
├── LICENSE
└── skills/
    └── spec/              # 兼容名下的运行时 skill 载荷（SKILL.md 在 skill 目录根部）
        ├── SKILL.md
        ├── install.sh
        ├── agents/ hooks/ references/ server/ scripts/ ...
```

Claude Code 格式仅清单位置不同（`.claude-plugin/plugin.json`），skills 载荷完全一致。

## 注意

- 两种格式的清单 schema 姿态相反：Agent Plugins 顶层字段**封闭**（模板不要新增字段，
  客户端特定数据只能放 `extensions`）；Claude Code 顶层字段开放（未知字段被忽略）。
- 模板必须保持合法 JSON（占位符除外），脚本渲染后会拒绝任何残留 `{{...}}`。

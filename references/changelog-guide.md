# Changelog Guide

## Overview

The spec workflow ships a standardized Chinese changelog generator (`scripts/generate_changelog.py`) that converts every commit in a given range into concise, well-formed Chinese changelog entries. The generator runs manually on demand (e.g. assembling release notes after done/push); it is not an automatic workflow step.

## Format standard

Entry format (product convention — the Chinese type labels and field names are the generator's output contract): `- 【类型】作用域：描述 (spec_slug)`

### Type mapping

| Commit type | Chinese label | Meaning |
| --- | --- | --- |
| `feat` | 【新增】 | New feature |
| `fix` | 【修复】 | Bug fix |
| `docs` | 【文档】 | Documentation change |
| `style` | 【样式】 | Formatting only |
| `refactor` | 【重构】 | Refactor |
| `perf` | 【性能】 | Performance |
| `test` | 【测试】 | Test-related |
| `build` | 【构建】 | Build system |
| `ci` | 【CI】 | CI configuration |
| `chore` | 【杂项】 | Miscellaneous |
| `revert` | 【回退】 | Revert |

## Usage

### Basic usage

```bash
# Generate a changelog for a range
./scripts/generate_changelog.py --from v1.0.0 --to HEAD

# Only commits from spec/* branches
./scripts/generate_changelog.py --spec-only --from v1.0.0

# Write to a file
./scripts/generate_changelog.py --from v1.0.0 --output CHANGELOG.md

# Prepend to an existing file
./scripts/generate_changelog.py --from v1.0.0 --output CHANGELOG.md --prepend
```

### Options

- `--root <path>` - repository root (default: current directory)
- `--from <ref>` - start ref (exclusive); empty generates all history
- `--to <ref>` - end ref (inclusive, default: HEAD)
- `--output <file>` - output file path (default: stdout)
- `--no-spec` - omit the spec slug from entries
- `--spec-only` - only commits from spec/* branches
- `--prepend` - prepend to an existing file instead of overwriting

## Workflow integration

### Generate at the done stage

When finishing a task package, generate its changelog:

```bash
# Commits of the current branch
./scripts/generate_changelog.py --from main --to HEAD --output .changelog-draft.md
```

### Prepend at the push stage

Before pushing to main, update the project's CHANGELOG.md:

```bash
# Prepend new entries to CHANGELOG.md
./scripts/generate_changelog.py --from main --to HEAD --output CHANGELOG.md --prepend
```

### Filter at release time

When assembling release notes, include only spec-workflow commits:

```bash
./scripts/generate_changelog.py --from v1.0.0 --to v2.0.0 --spec-only --output RELEASE-NOTES.md
```

## Release notes style（发布说明风格契约）

面向使用者的发布说明（`RELEASE_NOTES.md`、跨版本能力总结）一律采用条目式。规则如下：

1. **全篇条目**：版本内按主题分组（新功能 / 纪律 / 修复与收口，对应 Added / Changed / Fixed），不写段落散文；组数随版本实际内容增删。
2. **一行一个能力**：每条 = **加粗能力名** + 冒号 + 一句话机制与保障，至多两句。
3. **机制 + 保障成对**：每条必须同时说清「是什么」与「对使用者的效果」，讲机制、不摆内部过程。
4. **科普语气**：说人话，可用生活类比；不出现内部代号（任务包 slug、发现编号、内部脚本名、评审术语）——面向使用者，不面向实现者。
5. **只写已落地行为**：不写承诺、计划或未验证的效果。
6. **一屏为限**：单版发布说明以约 15 条为上限；跨版本总结可按主题合并多版本条目，但每条仍须一行一个能力。

`build_release.py` 的 `RELEASE_NOTES_EDITOR_PROMPT` 与本节同源，二者须同步修改。

示例（节选）：

```markdown
**新功能**
- **并行发包（workflow-runner 插槽）**：批量任务自动分包给多个子代理同时干，工单写明目标/范围/禁区/验收；分包归分包，验收权永远在主会话
- **证据保鲜**：验收证据可钉「代码版本 + 时间」锚点，代码一动旧证据作废，门禁强制重跑
```

## Commit message conventions

For high-quality changelogs, commit messages follow these formats:

### Conventional Commits (recommended)

```
feat(auth): 添加 JWT 认证支持

实现基于 JWT 的用户认证，包括登录、登出和令牌刷新。

Spec: 2026-08-24_implement-auth
```

### Simple format

```
添加用户认证功能

Spec: 2026-08-24_implement-auth
```

### Format essentials

1. **Type prefix**: standard conventional commit types (feat, fix, docs, ...)
2. **Scope** (optional): the affected area in parentheses
3. **Description**: a concise, explicit change statement
4. **Spec marker**: append `Spec: <slug>` at the end of the message

## Example output

```markdown
- 【新增】auth：添加 JWT 认证支持 `(2026-08-24_implement-auth)`
- 【修复】api：修正用户查询接口的分页问题 `(2026-08-23_fix-pagination)`
- 【重构】database：优化数据库连接池配置 `(2026-08-22_optimize-db)`
- 【文档】README：更新安装说明 `(2026-08-21_update-docs)`
- 【性能】cache：引入 Redis 缓存层 `(2026-08-20_add-cache)`
```

## Best practices

1. **Atomic commits**: one thing per commit, for clear changelog entries
2. **Consistent naming**: stick to conventional commit format
3. **Chinese descriptions**: write the description in Chinese so entries drop straight into the changelog
4. **Spec marker**: every spec-workflow commit carries `Spec: <slug>`
5. **Generate regularly**: run the generator at each done/push stage to stay current
6. **Branch filtering**: use `--spec-only` at release time to filter non-conventional commits

## Troubleshooting

### Script fails to run

```bash
# Ensure the script is executable
chmod +x ./scripts/generate_changelog.py

# Ensure you are inside a Git repository
git rev-parse --git-dir
```

### Empty output

- Check the ref range: `git log --from <ref> --to <ref>`
- Confirm commits exist inside the range
- With `--spec-only`, confirm spec/*-branch commits exist

### Odd formatting

- Check that commit messages follow the conventional commit format
- Simple-format commits are labeled 【变更】 (change)

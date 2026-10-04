# Changelog Guide

`scripts/generate_changelog.py` is a manual helper that turns Git history into standardized changelog entries; it is not an automatic step of `done`/`push`. CLI usage and the entry format live in the script's `--help` output; commit message conventions live in `naming-and-commits.md`.

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

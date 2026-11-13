# 我们怎样让 AI 真正完成长期工作

短期对话里，模型可以写出第一版；长期工作的难点是换会话后仍能回答三个问题：它要解决什么、允许改什么、“完成”凭什么成立。

Spec Harness v3 用两相运行时回答这些问题。规划阶段把目标、范围、Charter、假设、分叉和验收写入 `.spec/specs/<slug>/`；执行阶段不再重开讨论，而是按依赖图推进，每个任务只在边界内修改，并用精确命令留下可复核证据。

## 三条硬边界

- **规划厚，执行薄**：未知、选择和授权在 Go 前收口；Go 后只允许按计划或显式 amend。
- **证据优于叙述**：任务勾选来自 `runs.jsonl`，不是模型自称通过。
- **界面证据同样可核对**：命中 UI 的包必须有真实操作流程、截图和闭合的 findings，而不是代码断言代替用户体验。
- **自强化**：lessons 和最近决策进入下一轮规划；同一失败签名最多三次后必须换路径或停机。

## 两个文件和一条证据链

`spec.md` 固定目标、范围、Charter 和决策账本；`tasks.md` 固定依赖、边界和红绿验证。`runs.jsonl` 把命令、退出码、输出摘要和树指纹连成证据链。Git 提交、合并和推送由 `done` / `push` 按预授权收口。

用户仍保留真实权力：不可逆选择、外部依赖、偏好和推送授权必须显式确认。模型可以自主处理 reversible technical facts，并把理由与回滚方式记录下来。

运行时契约见 [`references/plan.md`](../references/plan.md)、[`references/exec.md`](../references/exec.md)、[`references/format.md`](../references/format.md) 和 [`references/git.md`](../references/git.md)。软件代码采用 PolyForm Noncommercial 1.0.0，商业使用需另行书面授权，见 [LICENSE](../LICENSE)。

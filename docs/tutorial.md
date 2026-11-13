# 用两相运行时完成长程任务

v3 把长期工作分为两个阶段：规划阶段允许提问、写假设和设计验证；Go 之后进入薄执行阶段，当前会话只消费磁盘上的任务、边界和证据。

## 为什么不是更长的提示词

提示词无法可靠保存“已经确认什么、验证过什么、失败到第几次”。v3 把这些事实写入 `.spec/`：

- `spec.md`：目标、范围、Charter、假设、分叉、决策与成功标准。
- `tasks.md`：依赖图、边界、红绿验证和状态。
- `runs.jsonl`：真实验证记录。
- `state.json` / `lessons.md`：恢复、停机和跨包学习状态。

## 规划轮

按 [`references/plan.md`](../references/plan.md) 依次完成 Recon、Intent、Charter、Plan、Forks 和 Go。规划的目标不是填表，而是消除会阻塞执行的未知：

1. 已确认事实必须带探针或来源。
2. 边界必须覆盖实际会修改的路径。
3. 每条验收标准映射到可执行任务。
4. 不可逆选择必须成为分叉并获得用户授权。

```bash
python3 scripts/spec.py plan-lint
python3 scripts/spec.py plan-go
```

## 执行轮

Go 后的循环只有四步：

1. `next` 选择依赖已满足且未停机的任务。
2. 只修改该任务 `boundary` 内的文件。
3. `verify` 执行精确命令并留下证据。
4. 失败按 [`references/exec.md`](../references/exec.md) 的阶梯推进；第四个同签名失败会自动标为 `[!]`，有新的根因证据后先 `unblock`。

```bash
python3 scripts/spec.py next
python3 scripts/spec.py verify task-api
python3 scripts/spec.py check
```

分叉触发时立即记录 `D`；无法授权的选项放入 `P` 并继续独立任务。只有 `delivered`、`parked-only` 和 `stalled` 是合法 Stop 状态。

## 交接与收尾

新会话不需要读取旧聊天。它读取活动包、路由卡、最近证据和 lessons，然后继续当前任务。全部任务勾选且无阻塞后执行 `check`；门禁通过时 `check` 自动执行 `done`——按 DAG 复验最终树、归档提交，并按绑定 worktree 与目标 worktree 收口，完成报告列出证据、用户决策、可回滚动作和剩余阻塞。受保护分支上 Go 的包在 `.spec/worktrees/<slug>` 的 linked worktree 里执行；会话应切到该目录运行 `verify`/`check`，main 工作区保持原分支与未提交改动，可同时承载多个并发 spec。

命中 UI 路径的包还要在 `check` 前完成真实界面操作：逐条覆盖 Criteria、保存截图到包内 `evidence/`、通过 `spec.py ui-review --file <draft>` 回写流程与 findings。问题必须连同解决方案写入，并以已验证修复任务或 `by: user <reference>` 决策闭合；产品树变化后必须重做 UI 取证。

v3 的强度来自约束：规划可以厚，执行必须薄；声明必须被证据兑现，Git 归属必须由机器收口。

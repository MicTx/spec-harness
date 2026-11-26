# 用两相运行时完成长程任务

v3 把长期工作分为两个阶段：规划阶段允许提问、写假设和设计验证；Go 之后进入薄执行阶段，当前会话只消费磁盘上的任务、边界和证据。

## 为什么不是更长的提示词

提示词无法可靠保存“已经确认什么、验证过什么、失败到第几次”。v3 把这些事实写入 `.spec/`：

- `spec.md`：目标、完成条件、范围与验收；Charter 覆盖、假设和分叉按需填写。
- `tasks.md`：依赖图、边界、验证和状态；`expect` 仅在需要提前跑基线时填写。
- `runs.jsonl`：真实验证记录。
- `.spec/specs/<slug>/state.json`：包内恢复与尝试计数；它是提示性快照，不取代 `runs.jsonl`。

## 规划轮

按 [`references/plan.md`](../references/plan.md) 依次完成 Recon、Intent、Charter、Plan、Forks 和 Go。规划的目标不是填表，而是消除会阻塞执行的未知：

1. 查明会影响方案的事实；假设按需记录。
2. 边界必须覆盖实际会修改的路径。
3. 每条验收标准映射到可执行任务。
4. 不可逆选择必须成为分叉并获得用户授权。

```bash
python3 scripts/spec.py plan-lint
python3 scripts/spec.py plan-go
```

## 执行轮

标准包 Go 后的循环只有四步。小型可逆修复另有[原地快路径](../references/exec.md)，无需 worktree 或自动提交：

1. `next` 选择依赖已满足且未停机的任务。
2. 只修改该任务 `boundary` 内的文件。
3. `verify` 执行精确命令并留下证据。
4. 失败按 [`references/exec.md`](../references/exec.md) 的阶梯推进；同一签名累计失败四次（不要求连续）会自动标为 `[!]`，有新的根因证据后先 `unblock`。

```bash
python3 scripts/spec.py next
python3 scripts/spec.py verify task-api
python3 scripts/spec.py check
```

分叉触发时立即记录 `D`；无法授权的选项放入 `P` 并继续独立任务。只有 `delivered`、`parked-only` 和 `stalled` 是合法 Stop 状态。

## 交接与收尾

新会话不需要读取旧聊天。它读取活动包、路由卡和最近证据，然后继续当前任务。运行 `check` 后按 `next` 修复、记下证据并复查，而非把报错转发给用户；独立任务先继续，只有真实授权才出决策卡。门禁通过后自动复验最终树、归档提交并按 V4 交付。受保护分支上 Go 的包在 `.spec/worktrees/<slug>` 的 linked worktree 里执行；会话应切到该目录运行 `verify`/`check`，main 工作区保持原分支与未提交改动，可同时承载多个并发 spec。

v3 的强度来自约束：规划可以厚，执行必须薄；声明必须被证据兑现，Git 归属必须由机器收口。

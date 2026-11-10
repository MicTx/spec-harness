# ADR 0008：两相自治运行时

日期：2026-11-10

## 状态

Accepted

## 背景

v2 的六阶段入口把规划、执行、检查、收尾拆成多个命令层，容易让会话重复规划，也让证据与任务状态漂移。长任务真正需要的是：在 Go 前充分消除未知，在 Go 后把会话限定为依赖图、边界和真实证据的消费者。

## 决策

1. 运行时只保留 `/spec`、`/spec:push`、`/spec:doctor` 三个用户入口；模型侧子命令由 `scripts/spec.py` 统一承载。
2. `spec-format: 3` 是唯一活动包格式。规划轮输出 Goal、Charter、Scope、Assumptions、Forks、Criteria 和可验证任务 DAG。
3. `plan-go` 记录基线、分支绑定和树状态；之后只有显式 `plan-amend` 可以修改计划。
4. 执行由任务 DAG 驱动，每个任务必须声明 boundary、verify、expect；`runs.jsonl` 是勾选与门禁的证据真源。
5. 可逆技术选择按 Charter V1 自主决策并记录；不可逆、越界、依赖和用户偏好必须分叉并取得授权。
6. Stop 只允许 delivered、parked-only、stalled；同一失败签名最多三次后换路径或停机。
7. lessons 和最近决策进入下一轮规划，形成自强化循环。

## 后果

- 活动文档和门禁不再解析 v2 六阶段布局；冻结归档按历史语义保留。
- 会话 hooks 在启动、编辑、压缩、工具调用和 Stop 事件上复用同一磁盘真源。
- 主线提交与推送必须验证 exact index / commit range，不接受叙述替代证据。

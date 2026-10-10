# 我们一起完成一个可恢复的长程任务

这篇 Spec Harness 教程从一个实际问题开始：如果任务要做两天，中间换一次 Agent，经历几轮测试和审查，我们怎样保证它不会越做越偏？

我们用“为已有 API 增加审计日志导出”做练习。业务名称可以替换；要练的是同一副骨架：先留下问题，再让代码逐步靠近答案。

## 先约定终点

完成后应留下五样东西：

- 一份说清目标、事实和边界的 `spec.md`。
- 一组每一步都有 `boundary` 和 `verify` 的 `tasks.md`。
- 一份带真实证据的 `checklist.md`。
- 一套中断后可以继续的磁盘记录。
- 一个只包含这项工作的 Git 提交。

如果只是改一个拼写，这套流程会显得太重。它适合需要分析、实现、测试或审查的工作。

## 第一步：让环境站稳

先取得一个可复现版本：

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
git checkout v0.16.0
bash install.sh
```

再回到真正要修改的项目，确认工作树：

```bash
cd /path/to/project
git status --short
```

没有输出，才说明接下来的 diff 可以归属于这项任务。已有其他改动时，先提交、暂存或另开任务包，不要把两个目标混在一起。

支持 Skill 的 CLI 可以直接开始：

```text
$spec
spec:goal 为已有 API 增加审计日志导出，包含权限检查、接口测试和恢复说明
```

Claude Code 使用对应的 `/spec:goal` 入口。想先观察三份文件时，也可以初始化任务包：

```bash
python3 /tmp/spec-harness/scripts/init_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_add-audit-export \
  --title "Audit Log Export"
```

初始化会检查 Git 状态，并创建独立的 `spec/` 分支。工作树不干净、目标分支已存在或主分支不能安全更新时，它会停下来；这一步保留了后面 diff 的可信度。

## 第二步：先写已知事实

打开任务包里的 `spec.md`，把四类内容分开：

```text
项目目标：为有权限的用户导出指定时间范围内的审计事件。
目标用户：需要进行合规审查的项目管理员。
已确认事实：现有服务已经保存审计事件，但没有导出接口。
关键假设：导出格式先支持 JSON，不在本轮设计异步下载中心。
待确认问题：无；权限沿用现有管理员中间件。
本轮不做：数据库重构、前端页面、后台定时任务。
```

“本轮不做”不是附注，而是防止任务扩张的护栏。如果某个待确认问题会改变方案，就保留它；一段诚实的未知比建立在猜测上的代码更容易修正。

## 第三步：把结果拆成可证明的任务

不要按目录列任务，先问每一步要留下什么结果：

```markdown
- [ ] 增加导出服务
  - boundary: 只修改审计查询、JSON 序列化和服务层测试
  - verify: 运行导出测试，覆盖空结果、时间范围和无效参数
- [ ] 接入权限检查
  - depends-on: 增加导出服务
  - boundary: 只修改路由、中间件接线和接口测试
  - verify: 运行接口测试，证明无权限请求被拒绝且管理员请求成功
- [ ] 写用户说明和回滚说明
  - depends-on: 接入权限检查
  - boundary: 只修改公开文档和任务包证据
  - verify: 运行 Markdown 链接检查，并从 Git 恢复一次
```

准备勾选时，先运行 `verify`，再改变复选框。`boundary` 说明做到哪里为止，`verify` 说明凭什么说做完；两者缺一不可。

## 第四步：只在边界清楚时并行

两个 Agent 同时修改同一个路由，通常会增加合并成本。先让仓库判断任务形状：

```bash
python3 /tmp/spec-harness/slots/workflow-runner/scripts/workflow_route.py \
  --text "并行审查导出接口的安全、测试和文档" \
  --json
```

适合并行时，仓库自有 driver 执行 fan-out：

```bash
python3 /tmp/spec-harness/slots/workflow-runner/scripts/workflow_fanout.py \
  --backend codex \
  --plan plan.json \
  --out results.jsonl \
  --concurrency 2 \
  --timeout 300
```

一个 item 要写清目标、范围、排除项、输出和验证。Pi 或 Codex 只是可替换的 worker CLI；流程真源仍是任务包，主会话仍负责阅读结果和运行 verify。没有合适后端时，串行执行更可靠，不能补写一个不存在的成功结果。

## 第五步：边做边留下交接点

每完成一个任务，检查四件事：

1. 改动有没有越过 `boundary`？
2. `verify` 里的命令是否真的运行？
3. 失败是否写进 `checklist.md`？
4. 下一位接手的人是否知道下一步？

需要暂停时，不要写“基本完成”。保留任务包和工作树，下次先运行：

```bash
python3 /tmp/spec-harness/scripts/route_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_add-audit-export
python3 /tmp/spec-harness/scripts/report_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_add-audit-export \
  --view status
```

状态来自磁盘上的任务、证据和 Git 工作树，所以换一个 Agent 也能继续。

## 第六步：把“完成”交给门禁

任务全部完成后，先跑单包检查，再跑全仓检查：

```bash
python3 /tmp/spec-harness/scripts/check_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_add-audit-export
python3 /tmp/spec-harness/scripts/check_all_spec_packages.py \
  --root /path/to/project
```

检查失败时回到对应任务修复，不用更乐观的文字覆盖失败。验收的价值正是让“完成”可以被别人相信。

## 第七步：归档、提交，再发布

先归档并提交：

```bash
python3 /tmp/spec-harness/scripts/complete_spec_package.py \
  --root /path/to/project \
  --slug 2026-10-03_add-audit-export \
  --archive
```

归档会生成完成摘要，并把任务包移到 `.spec/specs/archive/`。提交信息应描述用户可见的变化，并带上任务包归属：

```text
docs(audit): document export verification

Spec: 2026-10-03_add-audit-export
```

最后才合并和推送：

```bash
python3 /tmp/spec-harness/scripts/push_spec_package.py \
  --root /path/to/project \
  --branch spec/2026-10-03_add-audit-export
```

`push` 会再次检查工作树、任务包和目标 Git 树。它不是把当前所有改动一起推上去的快捷键；没有归属的改动要先拆开。

## 第八步：项目级的长路——先规划，再接力

第七步收口的是单个任务包。如果目标大到要连续开很多包，路径变成两层：

1. **先把项目规划写成带功能 checkbox 的文档**。放在项目根的 `.spec/plans/` 目录（索引 `.spec/plans/README.md`，单轮计划 `<日期>_<动词>-<对象>.md`，多轮集群 `<slug>/master.md` + 阶段细节）或用 `--plan` 显式指定；每个功能小到一个任务包能装下。
2. **`autoplan` 规划**：几轮征询把目标问清，框架/细节/复核逐个交给全新会话接力，过结构门禁与独立复核后停在就绪报告——它不启动执行。
3. **`autorun` 接力**：当前包走完门禁推送后，自动计划下一轮并开新交互会话接续，直到功能 checkbox 全部勾完。每轮可旁观可接管，旧窗口自动回收；没有合格规划文档时拒绝起链，递归不会自己发明范围。

单包流程里的三张纸、门禁和交接机制在链上逐轮复用；中断后从磁盘状态恢复，不需要重读聊天记录。参数与护栏细节见 [`references/commands.md`](../references/commands.md)。

## 遇到偏差时回到最早的不确定点

**一开始就进入执行**：`spec.md` 仍有占位或关键待确认问题。回到事实、假设和边界。

**任务完成但 check 不通过**：某项缺少 `boundary`/`verify`，或证据仍是占位文字。修对应任务，再重跑真实验证。

**并行结果无法合并**：item 范围重叠，或输出形状不统一。缩小边界，让每项结果都带文件、结论和验证。

**中断后不知道从哪里继续**：不要重读整段聊天。先跑 route 和 status，处理任务包报告的下一项。

## 把骨架带回你的项目

现在选一个真实的长期任务，写下四句话：服务谁？本轮不做什么？哪个结果最先能验证？如果今天暂停，下一位 Agent 从哪份文件继续？

当四个答案都落进任务包，Agent 就不再只是在一次对话里“帮忙写代码”，而是在共同维护一项可以继续、复核和交接的工程工作。

# 我们一起完成一个可恢复的长程任务

这篇 Spec Harness 教程不从“如何让 Agent 更聪明”开始，而从一个更实际的问题开始：

如果一个任务要做两天，中间换一次 Agent，经历几轮测试和审查，我们怎样保证它不会越做越偏？

我们用一个具体目标来练习：

    为已有 API 增加审计日志导出，包含权限检查、接口测试和恢复说明。

业务名称可以换成你的真实任务。教程要练的是同一副骨架：先把问题放在桌上，再让代码一点点靠近答案。

## 先约定我们的终点

完成这次练习，我们希望留下五样东西：

- 一份说明目标和边界的 spec.md。
- 一组每一步都能验证的 tasks.md。
- 一份写有真实证据的 checklist.md。
- 一套中断后可以继续的磁盘记录。
- 一个知道自己为什么存在的 Git 提交。

如果你只是改一个拼写，这套流程会显得太重。它适合需要分析、实现、测试和审查的工作。

## 第一步：先让环境站稳

从公开仓库获取一个固定版本：

    git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
    cd /tmp/spec-harness
    git checkout v0.13.10
    bash install.sh

再回到你真正要修改的项目，先看工作树：

    cd /path/to/project
    git status --short

这里没有输出，表示我们可以清楚地知道接下来哪些改动属于这项任务。如果工作树已经有别的改动，先保存或提交它们；不要把两个目标混成一次交付。

支持 Skill 的 Agent CLI 可以直接从目标开始：

    $spec
    spec:goal 为已有 API 增加审计日志导出，包含权限检查、接口测试和恢复说明

Claude Code 的入口是：

    /spec:goal 为已有 API 增加审计日志导出，包含权限检查、接口测试和恢复说明

如果你想先看生成的文件，也可以直接初始化任务包：

    python3 /tmp/spec-harness/scripts/init_spec_package.py \
      --root /path/to/project \
      --slug 2026-10-03_add-audit-export \
      --title "Audit Log Export"

初始化脚本会检查 Git 状态，并在 Git 项目中建立独立的 spec/ 分支。工作树不干净、分支已经存在或主分支不能安全更新时，它会停下来。这一步看似保守，却让我们不会把别人尚未交付的工作带进来。

## 第二步：先写我们知道什么

打开任务包里的 spec.md。不要急着写实现，先把四类内容写出来：

    项目目标：为有权限的用户导出指定时间范围内的审计事件。
    目标用户：需要进行合规审查的项目管理员。
    已确认事实：现有服务已经保存审计事件，但没有导出接口。
    关键假设：导出格式先支持 JSON，不在本轮设计异步下载中心。
    待确认问题：无；权限规则沿用现有管理员中间件。
    本轮不做：数据库重构、前端页面、后台定时任务。

我们尤其要认真写“本轮不做”。长期任务最容易从这里失控：导出接口写着写着变成数据库迁移，数据库迁移又牵出权限模型，最后谁也说不清第一条需求还剩多少。

如果还有会改变方案的疑问，就先把它留下来。一个诚实的待确认问题，比一段建立在猜测上的代码更有价值。

## 第三步：把结果拆成可以证明的任务

不要先按目录列任务。先问每一步要交付什么结果。

    - [ ] 增加导出服务
      - boundary: 只修改审计查询、JSON 序列化和服务层单元测试
      - verify: 运行审计导出单元测试，覆盖空结果、时间范围和无效参数
    - [ ] 接入权限检查
      - depends-on: 增加导出服务
      - boundary: 只修改路由注册、现有权限中间件接线和接口测试
      - verify: 运行接口测试，证明无权限请求被拒绝且管理员请求成功
    - [ ] 写用户说明和回滚说明
      - depends-on: 接入权限检查
      - boundary: 只修改公开文档和当前任务包证据
      - verify: 运行 Markdown 链接检查，并核对回滚步骤可以从 Git 恢复

这里有一个小习惯很有用：每次准备勾选任务时，先把 verify 当作命令执行，而不是把它当作一句描述。任务的完成标记应该跟在证据后面，而不是跟在“看起来已经写好”后面。

## 第四步：决定哪些工作值得并行

不是所有任务都适合并行。两个 Agent 同时改同一个路由，通常只会让合并更难。

我们可以先让仓库判断任务形状：

    python3 /tmp/spec-harness/slots/workflow-runner/scripts/workflow_route.py \
      --text "并行审查导出接口的安全、测试和文档" \
      --json

普通串行任务留在主会话。只有当多个视角边界清楚、输出可以独立验收时，才使用 workflow-runner。

当前执行面是仓库自有的 driver：

    python3 /tmp/spec-harness/slots/workflow-runner/scripts/workflow_fanout.py \
      --backend codex \
      --plan plan.json \
      --out results.jsonl \
      --concurrency 2 \
      --timeout 300

Pi 或 Codex 在这里是 worker CLI，不是流程真源。流程真源仍然是任务包和主会话的验收。

一个 item 应该自包含地写出目标、范围、排除项、输出和验证。比如：

    {
      "items": [
        {
          "id": "security-review",
          "prompt": "目标：审查审计导出接口的输入和权限边界。\n范围：只读 API 路由、查询和测试。\n排除：不修改代码，不读取或输出真实凭证。\n输出：列出按严重度排序的发现，附文件和行号。\n验证：每条发现必须回到源文件或测试证据。"
        },
        {
          "id": "docs-review",
          "prompt": "目标：审查用户文档是否能从安装走到导出验证。\n范围：只读 README、docs 和命令帮助。\n排除：不修改代码，不引入新命令。\n输出：列出断链、错误命令和缺少的读者说明。\n验证：每个问题附一个可复现命令。"
        }
      ]
    }

单个 worker 失败时，driver 会记录这个 item 的失败，而不是把它伪装成整批成功。主会话仍需阅读结果，并运行任务自己的 verify。没有可用后端时，退回串行执行或 team-loop；不要补写一个不存在的成功结果。

## 第五步：边做边留下可以交接的记录

每完成一个任务，我们一起检查四件事：

1. 改动有没有越过 boundary？
2. verify 里的命令是否真的运行？
3. 失败是否写进 checklist.md？
4. 下一位接手的人是否知道下一步？

如果需要暂停，不要写“基本完成”。保留工作树和任务包，下次从状态开始：

    python3 /tmp/spec-harness/scripts/route_spec_package.py \
      --root /path/to/project \
      --slug 2026-10-03_add-audit-export

    python3 /tmp/spec-harness/scripts/report_spec_package.py \
      --root /path/to/project \
      --slug 2026-10-03_add-audit-export \
      --view status

我们不需要重新翻完整段聊天。状态来自任务文件、验证证据和 Git 工作树，所以换一个 Agent 也能继续。

## 第六步：一起做验收

任务都完成后，先检查当前任务包：

    python3 /tmp/spec-harness/scripts/check_spec_package.py \
      --root /path/to/project \
      --slug 2026-10-03_add-audit-export

再检查项目里所有任务包：

    python3 /tmp/spec-harness/scripts/check_all_spec_packages.py \
      --root /path/to/project

如果检查失败，我们回到具体任务修复，而不是用一段更乐观的文字盖住失败。验收的价值就在于它允许我们相信“完成”这个词。

## 第七步：归档、提交，再发布

验收通过后，先完成归档和本地提交：

    $spec
    spec:done

或者直接运行：

    python3 /tmp/spec-harness/scripts/complete_spec_package.py \
      --root /path/to/project \
      --slug 2026-10-03_add-audit-export \
      --archive

归档会生成完成摘要，并把任务包移到 .spec/specs/archive/。提交信息应说明用户可见的变化，并带上任务包归属：

    docs(audit): document export verification

    Spec: 2026-10-03_add-audit-export

最后才进入合并和推送：

    python3 /tmp/spec-harness/scripts/push_spec_package.py \
      --root /path/to/project \
      --branch spec/2026-10-03_add-audit-export

push 会再次检查工作树、任务包和目标 Git 树。它不是“把当前所有改动都推上去”的快捷键；如果工作树里有两个没有归属的目标，我们先拆开，再发布。

## 如果事情没有按计划发展

**任务一开始就进入执行**：spec.md 还有占位或待确认问题。回到事实、假设和边界。

**任务完成但 check 不通过**：某项缺少 boundary/verify，或 checklist 里的证据仍是占位文字。回到对应任务补真实验证。

**并行结果很多却无法合并**：item 范围重叠，或输出没有统一形状。缩小每项范围，让结果都带文件、行号、结论和验证。

**中断后不知道从哪里继续**：不要重新阅读整段聊天。运行 route 和 status，先处理任务包报告的下一项。

**以为没有宿主 Workflow 就不能并行**：不需要。workflow-runner 的执行面由仓库自有 driver 提供，宿主只提供可替换的 worker CLI。

## 把这套骨架带走

现在选一个你正在做的长期任务，和我们一起写下四句话：

- 这项工作服务谁？
- 这一轮明确不做什么？
- 哪个结果最先可以验证？
- 如果今天暂停，下一位 Agent 从哪份文件继续？

当这四个答案都能落进任务包，我们就不再只是“让 Agent 帮忙写代码”，而是在共同维护一项可以继续、可以复核、可以交接的工程工作。

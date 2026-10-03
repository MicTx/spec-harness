# 用 Spec Harness 完成一个可恢复的长程任务

本教程带你完成一条完整链路：从一个自然语言目标开始，建立任务包，执行有边界的修改，验证结果，保存中断点，最后归档并交给 Git。

示例目标是“为已有服务增加审计日志导出”。你不需要复制示例里的业务名称；重点是把同样的结构迁移到自己的项目。

## 你将得到什么

教程结束时，你会拥有：

- 一个写清目标和范围的 spec.md。
- 一组带 boundary 和 verify 的 tasks.md。
- 一份真实命令证据组成的 checklist.md。
- 一个可以从中断处恢复的任务包。
- 一个带任务归属的 Git 提交。

下面把“长程”理解为需要多轮分析、实现、测试和审查的工作。只改一行拼写时，不要强行使用整套流程。

## 第一步：准备 Skill 和目标项目

从公开仓库获取固定版本：

    git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
    cd /tmp/spec-harness
    git checkout v0.13.10
    bash install.sh

把 /path/to/project 换成你的项目路径。先确认目标项目工作树干净：

    cd /path/to/project
    git status --short

没有输出时，初始化一个任务包。直接使用脚本的方式如下：

    python3 /tmp/spec-harness/scripts/init_spec_package.py \
      --root /path/to/project \
      --slug 2026-10-03_add-audit-export \
      --title "Audit Log Export"

在支持 Skill 的 Agent CLI 中，也可以直接说：

    $spec
    spec:goal 为已有 API 增加审计日志导出，包含权限检查、接口测试和恢复说明

Claude Code 对应的入口是：

    /spec:goal 为已有 API 增加审计日志导出，包含权限检查、接口测试和恢复说明

初始化脚本在 Git 仓库中会为任务建立独立的 spec/ 分支。若工作树不干净、分支已存在、主分支无法安全更新或权限检查失败，它会停下来，而不是把别的工作带入新任务。

## 第二步：先写问题，再写代码

打开任务包：

    cd /path/to/project
    sed -n '1,240p' .spec/specs/2026-10-03_add-audit-export/spec.md
    sed -n '1,240p' .spec/specs/2026-10-03_add-audit-export/tasks.md

spec.md 至少写清以下内容：

    项目目标：为有权限的用户导出指定时间范围内的审计事件。
    目标用户：需要进行合规审查的项目管理员。
    已确认事实：现有服务已经保存审计事件，但没有导出接口。
    关键假设：导出格式先支持 JSON，不在本轮设计异步下载中心。
    待确认问题：无；权限规则沿用现有管理员中间件。
    本轮不做：数据库重构、前端页面、后台定时任务。

如果这里还存在真正会改变方案的问题，不要用“先写再说”掩盖它。把问题留下来，直到你能回答，或者明确把它标成外部阻塞。

## 第三步：把工作拆成可验证任务

不要按文件名拆任务，而要按可验证的结果拆任务。一个可用的 tasks.md 片段是：

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

第一项完成后，真实运行它的 verify，再勾选复选框。复选框表示证据已经存在，不表示“代码看起来差不多”。

## 第四步：选择执行方式

先让仓库判断任务形状：

    python3 /tmp/spec-harness/slots/workflow-runner/scripts/workflow_route.py \
      --text "并行审查导出接口的安全、测试和文档" \
      --json

普通串行任务留在主会话。批量评审、多个独立视角或 review-fix loop 才考虑 workflow-runner。

当前执行面是仓库自有 driver：

    python3 /tmp/spec-harness/slots/workflow-runner/scripts/workflow_fanout.py \
      --backend codex \
      --plan plan.json \
      --out results.jsonl \
      --concurrency 2 \
      --timeout 300

这条命令不依赖宿主的原生 Workflow API。worker CLI 可以替换，但任务合同、输出记录和主会话验收不变。

### 如何写 plan.json

每个 item 都要自包含，至少说明目标、范围、排除项、输出形状和验证方式。下面的 prompt 是示例；真实凭证不要写进文件或 prompt：

    {
      "items": [
        {
          "id": "security-review",
          "prompt": "目标：审查审计导出接口的输入和权限边界。\\n范围：只读 API 路由、查询和测试。\\n排除：不修改代码，不读取或输出真实凭证。\\n输出：列出按严重度排序的发现，附文件和行号。\\n验证：每条发现必须能回到源文件或测试证据。"
        },
        {
          "id": "docs-review",
          "prompt": "目标：审查用户文档是否能从安装走到导出验证。\\n范围：只读 README、docs 和命令帮助。\\n排除：不修改代码，不引入新命令。\\n输出：列出断链、错误命令和缺少的读者说明。\\n验证：每个问题附一个可复现命令。"
        }
      ]
    }

driver 会为每个 item 写一条 JSONL 记录和一个完整输出文件。单个 item 超时或失败时，记录该 item 的失败，不把失败伪装成整批成功。主会话仍需阅读结果、合并发现，并运行任务自己的 verify。

如果 worker CLI 不可用，退回主会话串行执行或使用 team-loop。没有可用后端时，不要伪造结果。

## 第五步：执行、暂停和恢复

运行任务时，优先处理依赖最少的任务。每完成一个任务：

1. 阅读改动，确认没有越过 boundary。
2. 运行 verify 中的真实命令。
3. 把关键结果写入 checklist.md。
4. 再勾选任务。

如果做到一半需要暂停，保留当前工作树和任务包，不要写“基本完成”。下一次进入项目先运行：

    python3 /tmp/spec-harness/scripts/route_spec_package.py \
      --root /path/to/project \
      --slug 2026-10-03_add-audit-export

再查看：

    python3 /tmp/spec-harness/scripts/report_spec_package.py \
      --root /path/to/project \
      --slug 2026-10-03_add-audit-export \
      --view status

这个状态不是聊天摘要。它来自任务文件和验证证据，所以换一个 Agent 也能沿着同一份记录继续。

## 第六步：验收完整链路

任务全部完成后，先跑单包检查：

    python3 /tmp/spec-harness/scripts/check_spec_package.py \
      --root /path/to/project \
      --slug 2026-10-03_add-audit-export

再跑项目范围检查：

    python3 /tmp/spec-harness/scripts/check_all_spec_packages.py \
      --root /path/to/project

只要检查失败，就回到任务或清单修复。不要用文字说明替代命令证据。

## 第七步：归档并交给 Git

验收通过后，使用用户入口完成归档和本地提交：

    $spec
    spec:done

直接使用脚本时，可以运行：

    python3 /tmp/spec-harness/scripts/complete_spec_package.py \
      --root /path/to/project \
      --slug 2026-10-03_add-audit-export \
      --archive

归档产生完成摘要，并把任务包移到 .spec/specs/archive/。提交信息应使用 Conventional Commits，并带上任务包归属，例如：

    docs(audit): document export verification

    Spec: 2026-10-03_add-audit-export

最后才进行合并和推送：

    python3 /tmp/spec-harness/scripts/push_spec_package.py \
      --root /path/to/project \
      --branch spec/2026-10-03_add-audit-export

push 会再次检查工作树、任务包和目标 Git 树。它不是“把当前所有改动都推上去”的快捷键；无归属的混合改动必须先拆成任务包。

## 常见失败和修复

**任务一开始就进入 run**：spec.md 仍有占位或待确认问题。先写清事实、假设和边界。

**任务完成但 check 不通过**：通常是某项没有 boundary/verify，或 checklist 的证据还是占位文字。回到对应任务补真实验证。

**并行结果很多但无法合并**：plan.json 的 item 范围重叠，或输出没有统一形状。缩小每项范围，要求输出带文件、行号、结论和验证。

**中断后不知道从哪里继续**：不要重新阅读整段聊天。运行 route 和 status，先处理任务包报告的下一项。

**想用宿主 Workflow API 才能并行**：不需要。workflow-runner 的执行面是仓库自有 driver；宿主只提供可替换 worker CLI。

## 练习：把教程迁移到自己的任务

选择一个你正在做的长程工作，用一句话写出：

- 目标用户是谁？
- 本轮明确不做什么？
- 哪个任务最先可以验证？
- 如果暂停，下一位 Agent 需要从哪份文件继续？

当这四个答案都能写进任务包时，你已经把“让 Agent 帮我做事”改成了“让一个系统在证据约束下持续完成工作”。

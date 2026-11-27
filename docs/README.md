# Documentation

先决定你要完成哪件事，再选择一篇文档。这样比从目录第一篇读到最后一篇更快。

[🇬🇧 English index](README-en.md)

## 我想第一次跑通

- [从零完成第一个 v3 任务](getting-started.md)：用 Claude Code 或直接运行 CLI，验证并交付一个练习任务。

## 我想先理解它

- [Spec Harness 介绍](introduction.md)：从一个长程任务为什么会失控开始，理解任务包、验证和 Git 收尾各自解决什么问题。
- [用两相运行时完成长程任务](tutorial.md)：规划轮与执行轮的分工、`.spec/` 里每份文件的作用，以及 Go 之后为什么只剩薄执行。
- [按证据续跑](loop-continuity.zh.md)：为什么 Stop 不数轮数、Loop Transformer 启发了什么，以及各宿主如何恢复。另有[英文机制与来源说明](loop-continuity.md)。

## 我想操作它

- 修复已交付规格：`/spec:repair <归档包>` 创建带 `kind: repair` 与 `repair-of` 元数据的普通 v3 包，再走标准规划和门禁。
- [Git 工作流（中文）](git-workflow.md)
- [Git workflow (English)](git-workflow.en.md)
- 根目录 [README（中文）](../README.md)：安装、宿主选择与导出运行时包。
- Root [README (English)](../README-en.md): installation, host selection, runtime export, and regression commands.

## 我想维护或发布它

- [Contributing](../CONTRIBUTING.md)：开发环境、分支、测试和提交。
- [发布与升级](../RELEASE.md)：如何选择稳定发布包或源码，校验、升级和回滚。
- [Security](../SECURITY.md)：漏洞报告。
- [Support](../SUPPORT.md)：安装或运行失败时应提供哪些信息。
- 架构决策记录（ADR，`adr/` 目录）：[会话生成与窗口生命周期](adr/0001-terminal-spawn-window-lifecycle.md)、[阶段契约拆分](adr/0002-stage-contracts-by-file.md)、[推送门禁归属区间](adr/0003-push-gate-attribution-and-inflight-split.md)、[Adapt SDD workflow semantics](adr/0004-adapt-sdd-workflow-semantics.md)、[External facts in package planning](adr/0005-external-facts.md)、[Evidence-based correction stopping](adr/0006-evidence-based-correction-stopping.md)、[Bash write coverage gap](adr/0007-bash-write-gap.md)、[两相自治运行时](adr/0008-two-phase-autonomous-runtime.md)；不进运行时导出树。

## 入口归类

- **public / user entry**：根 [README（中文）](../README.md) 与 [README (English)](../README-en.md)、本索引与[英文索引](README-en.md)，以及本目录的引导/教程/Git 工作流。
- **runtime reference**：[`SKILL.md`](../SKILL.md) 与 [`references/`](../references/)：规划、执行、格式、哲学与 Git 运行时契约。
- **maintainer-only**：[`agent-plugin/README.md`](../agent-plugin/README.md)——源码库独有的插件清单打包模板说明，不进运行时导出树。

两相运行时契约在 [`references/plan.md`](../references/plan.md)、[`references/exec.md`](../references/exec.md)、[`references/format.md`](../references/format.md) 与 [`references/git.md`](../references/git.md)。它们回答“必须满足什么”；本目录的教程回答“为什么这样安排、下一步怎么走”。

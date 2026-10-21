# Documentation

先决定你要完成哪件事，再选择一篇文档。这样比从目录第一篇读到最后一篇更快。

[🇬🇧 English index](README-en.md)

## 我想先理解它

- [Spec Harness 介绍](introduction.md)：从一个长程任务为什么会失控开始，理解任务包、验证和 Git 收尾各自解决什么问题。
- [长程任务教程](tutorial.md)：跟着一个审计日志导出目标，从安装走到归档；示例是骨架，业务名称可以替换。

## 我想操作它

- [Git 工作流（中文）](git-workflow.md)
- [Git workflow (English)](git-workflow.en.md)
- [客户端执行矩阵](../README.md#执行形态与支持边界)：区分 CLI、IDE agent 插件、桌面端与实际验证边界。
- 根目录 [README（中文）](../README.md)：安装、宿主选择、导出运行时包和回归命令。
- Root [README (English)](../README-en.md): installation, host selection, runtime export, and regression commands.

## 我想维护或发布它

- [Contributing](../CONTRIBUTING.md)：开发环境、分支、测试和提交。
- [Releases](../RELEASE.md)：版本、归档、校验和回滚。
- [Security](../SECURITY.md)：漏洞报告和 server-mode 安全边界。
- [Support](../SUPPORT.md)：安装或运行失败时应提供哪些信息。
- 架构决策记录（ADR，`adr/` 目录）：[会话生成与窗口生命周期](adr/0001-terminal-spawn-window-lifecycle.md)、[阶段契约拆分](adr/0002-stage-contracts-by-file.md)、[推送门禁归属区间](adr/0003-push-gate-attribution-and-inflight-split.md)；不进运行时导出树。

## 入口归类

- **public / user entry**：根 [README（中文）](../README.md) 与 [README (English)](../README-en.md)、本索引与[英文索引](README-en.md)、本目录的引导/教程/Git 工作流，以及 [server-mode 部署说明](../server/README.md)。
- **runtime reference**：[`SKILL.md`](../SKILL.md) 与 [`references/`](../references/00-readme.md)：命令、模板、编排、槽位和归档的运行时契约。
- **maintainer-only**：[`agent-plugin/README.md`](../agent-plugin/README.md)——源码库独有的插件清单打包模板说明，不进运行时导出树。

命令、任务包文档（新版 `spec.md` + `tasks.md`，`checklist.md` 为旧包可选）和机器门禁的精确契约在 [`references/`](../references/00-readme.md)。它们回答“必须满足什么”；本目录的教程回答“为什么这样安排、下一步怎么走”。

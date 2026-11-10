# Documentation

Decide what you want to accomplish first, then pick the one document that does it — faster than reading the directory from top to bottom.

[🇨🇳 中文索引](README.md)

## I want to complete my first task

- [From zero to your first task](getting-started.md) — Chinese only: a step-by-step walkthrough with commands, expected results, and failure handling for Claude Code.

## I want to understand it first

- [Spec Harness introduction](introduction.md) — Chinese only: start from why a long-running task loses control, then see what task packages, verification, and Git closeout each solve.

## I want to operate it

- [Git workflow (English)](git-workflow.en.md)
- [Git 工作流（中文）](git-workflow.md)
- Root [README (English)](../README-en.md): installation, host selection, and runtime export.
- Root [README（中文）](../README.md)：安装、宿主选择、导出运行时包和回归命令。

## I want to maintain or publish it

- [Contributing](../CONTRIBUTING.md): development environment, branches, tests, and commits.
- [Releases](../RELEASE.md): versions, archives, checksums, and rollback.
- [Security](../SECURITY.md): vulnerability reports.
- [Support](../SUPPORT.md): what to include when installation or a run fails.
- Architecture decision records (the `adr/` directory): [session spawn & window lifecycle](adr/0001-terminal-spawn-window-lifecycle.md), [stage contracts by file](adr/0002-stage-contracts-by-file.md), [push-gate attribution & in-flight split](adr/0003-push-gate-attribution-and-inflight-split.md). Not part of the runtime export tree.

## Entry classes

- **public / user entry**: the root [README (English)](../README-en.md) and [README（中文）](../README.md), this index and the [Chinese index](README.md), and the guides in this directory.
- **runtime reference**: [`SKILL.md`](../SKILL.md) and the five pages under `references/` — planning, execution, format, philosophy, and Git contracts.
- **maintainer-only**: [`agent-plugin/README.md`](../agent-plugin/README.md) — packaging-template notes that exist only in the source repository and never ship in the runtime export.

The two-phase runtime contracts live in [`references/plan.md`](../references/plan.md), [`references/exec.md`](../references/exec.md), [`references/format.md`](../references/format.md), and [`references/git.md`](../references/git.md): they answer "what must hold"; the guides in this directory answer "why it is arranged this way and what to do next".

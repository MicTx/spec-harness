# Documentation

Decide what you want to accomplish first, then pick the one document that does it — faster than reading the directory from top to bottom.

[🇨🇳 中文索引](README.md)

## I want to complete my first task

- [From zero to your first task](getting-started.md) — Chinese only: a step-by-step walkthrough with commands, expected results, and failure handling for Claude Code.

## I want to understand it first

- [Spec Harness introduction](introduction.md) — Chinese only: start from why a long-running task loses control, then see what task packages, verification, and Git closeout each solve.
- [Two-phase runtime tutorial](tutorial.md) — Chinese only: how planning and execution divide the work, what each package file does, and why execution stays thin after Go.
- [Evidence-driven continuation](loop-continuity.md) — why Stop follows verified progress instead of a round counter, what Loop Transformer research inspired, and what each host can do. [中文](loop-continuity.zh.md).

## I want to operate it

- Repair a delivered spec: `/spec:repair <archived-slug>` creates a regular v3 package marked with `kind: repair` and `repair-of`, then follows the standard planning and gate.
- [Git workflow (English)](git-workflow.en.md)
- [Git 工作流（中文）](git-workflow.md)
- Root [README (English)](../README-en.md): installation, host selection, and runtime export.
- Root [README（中文）](../README.md)：安装、宿主选择、导出运行时包和回归命令。

## I want to maintain or publish it

- [Contributing](../CONTRIBUTING.md): development environment, branches, tests, and commits.
- [Releases](../RELEASE.md): choose a stable archive or a source checkout; verify, update, or roll back.
- [Security](../SECURITY.md): vulnerability reports.
- [Support](../SUPPORT.md): what to include when installation or a run fails.
- Architecture decision records (the `adr/` directory): [session spawn & window lifecycle](adr/0001-terminal-spawn-window-lifecycle.md), [stage contracts by file](adr/0002-stage-contracts-by-file.md), [push-gate attribution & in-flight split](adr/0003-push-gate-attribution-and-inflight-split.md), [adapting SDD workflow semantics](adr/0004-adapt-sdd-workflow-semantics.md), [external facts in package planning](adr/0005-external-facts.md), [evidence-based correction stopping](adr/0006-evidence-based-correction-stopping.md), [Bash write coverage gap](adr/0007-bash-write-gap.md), [two-phase autonomous runtime](adr/0008-two-phase-autonomous-runtime.md). These record past decisions and are not part of the runtime export tree.

## Entry classes

- **public / user entry**: the root [README (English)](../README-en.md) and [README（中文）](../README.md), this index and the [Chinese index](README.md), and the guides in this directory.
- **runtime reference**: [`SKILL.md`](../SKILL.md) and the five pages under [`references/`](../references/) — planning, execution, format, philosophy, and Git contracts.
- **maintainer-only**: [`agent-plugin/README.md`](../agent-plugin/README.md) — packaging-template notes that exist only in the source repository and never ship in the runtime export.

The two-phase runtime contracts live in [`references/plan.md`](../references/plan.md), [`references/exec.md`](../references/exec.md), [`references/format.md`](../references/format.md), and [`references/git.md`](../references/git.md): they answer "what must hold"; the guides in this directory answer "why it is arranged this way and what to do next".

# Documentation

Decide what you want to accomplish first, then pick the one document that does it — faster than reading the directory from top to bottom.

[🇨🇳 中文索引](README.md)

## I want to understand it first

- [Spec Harness introduction](introduction.md) — Chinese only: start from why a long-running task loses control, then see what task packages, verification, and Git closeout each solve.
- [Long-running task tutorial](tutorial.md) — Chinese only: walk one audit-export goal from installation to archive; the example is a skeleton, business names may be replaced.

## I want to operate it

- [Git workflow (English)](git-workflow.en.md)
- [Git 工作流（中文）](git-workflow.md)
- Root [README (English)](../README-en.md): installation, host selection, runtime export, and regression commands.
- Root [README（中文）](../README.md)：安装、宿主选择、导出运行时包和回归命令。

## I want to maintain or publish it

- [Contributing](../CONTRIBUTING.md): development environment, branches, tests, and commits.
- [Releases](../RELEASE.md): versions, archives, checksums, and rollback.
- [Security](../SECURITY.md): vulnerability reports and the server-mode security boundary.
- [Support](../SUPPORT.md): what to include when installation or a run fails.

## Entry classes

- **public / user entry**: the root [README (English)](../README-en.md) and [README（中文）](../README.md), this index and the [Chinese index](README.md), the guides in this directory, and the [server-mode deployment guide](../server/README.md).
- **runtime reference**: [`SKILL.md`](../SKILL.md) and [`references/`](../references/00-readme.md) — the runtime contracts for commands, templates, orchestration, slots, and archival.
- **maintainer-only**: [`agent-plugin/README.md`](../agent-plugin/README.md) — packaging-template notes that exist only in the source repository and never ship in the runtime export.

Command, task-package, and gate contracts live in [`references/`](../references/00-readme.md): they answer "what must hold"; the guides in this directory answer "why it is arranged this way and what to do next".

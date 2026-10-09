# Git 工作流

本文回答一个具体问题：怎样让一次文档或代码改动，从本地工作树安全地走到公共仓库？公共仓库是 [MicTx/spec-harness](https://github.com/MicTx/spec-harness)，发布版本位于 [GitHub Releases](https://github.com/MicTx/spec-harness/releases)。先读完“分支”和“提交”，再决定是否需要任务包；小改动不必套完整流程。

## 获取代码

先固定你要理解的代码，再开始改：标签适合稳定安装，完整提交 SHA 适合可复现构建：

    git clone https://github.com/MicTx/spec-harness.git
    cd spec-harness
    git fetch --tags origin
    git checkout v0.15.0

贡献者通常从 fork 克隆，并把官方仓库保留为 `upstream`：

    git clone https://github.com/<you>/spec-harness.git
    cd spec-harness
    git remote add upstream https://github.com/MicTx/spec-harness.git
    git fetch upstream --prune
    git switch main
    git reset --hard upstream/main

不要在包含未提交改动的工作树上执行 reset --hard；先提交、暂存或另行保存改动。

## 分支

分支的作用不是装饰，而是把一组有共同目的的 diff 绑定在一起。每个改动使用独立分支，并从最新 `main` 创建：

    git switch -c docs/git-guide

推荐使用能表达意图的前缀：docs/、fix/、feat/、test/、chore/。使用 Spec Harness 任务包时，按任务包命名规则使用 spec/YYYY-MM-DD_<verb>-<object>，例如 spec/2026-10-02_audit-docs。

开 PR 前先同步主分支：

    git fetch upstream --prune
    git rebase upstream/main

## 提交

提交信息要让未来的读者知道这次变化属于哪一类，因此采用 Conventional Commits：

    <type>(<scope>): <imperative summary>

常用类型：feat（新增能力）、fix（修复错误）、docs（文档）、test（验证）、refactor（结构调整）、chore（维护）。

示例：

    docs(git): add contributor workflow
    fix(export): reject source-tree output paths

标题使用祈使句并保持简短；需要时在正文写清问题、行为变化和兼容影响。不要把凭据、个人路径、临时文件或私有任务记录提交到公共仓库。

## 拉取请求

推送分支并创建面向 `main` 的 Pull Request：

    git push -u origin docs/git-guide

Pull Request 应包含：

- 要解决的问题和改动后的行为。
- 影响到的命令、文件或公开接口。
- 实际运行的验证命令及结果。
- 需要用户迁移的兼容性说明。
- 文档或 CLI 变化的示例输出、截图或复现步骤。

提交前至少运行这些能回答“当前树是否可交付”的命令。下面的完整套件属于发布/主分支门禁，不是每个任务包 `check`/`done` 的默认验证；任务包按 `spec.md` 声明的 `package`、`integration` 或 `project` 范围执行：

    python3 -m compileall -q scripts server hooks slots tests
    python3 scripts/smoke_test_spec_skill.py
    python3 -m pytest -q
    ruff check scripts/ server/ hooks/ slots/ tests/
    ruff format --check scripts/ server/ hooks/ slots/ tests/
    git diff --check

文档改动还要检查链接和目录结构；否则文字正确也可能指向不存在的文件：

    python3 scripts/organize_project_structure.py --root . --check

## 版本和发布

用户从 GitHub Releases 下载归档并校验 SHA256SUMS。维护者使用 vMAJOR.MINOR.PATCH 标签表达版本；破坏命令别名、任务包结构、导出布局或行为契约时，必须在 CHANGELOG.md 和版本说明中明确记录。用户侧安装、升级和回滚步骤见 [RELEASE.md](../RELEASE.md)。

## 常见 Git 问题

**non-fast-forward**：先 git fetch，检查远端分支，再基于最新 main rebase；不要未经检查强推。

**working tree is not clean**：先用 git status 区分当前改动，提交或保存后再切换分支。

**进入 detached HEAD**：如果要继续改动，先创建分支：

    git switch -c fix/from-release

**提交包含了不应公开的文件**：在推送前运行 git diff --cached --name-status，移除敏感文件并轮换已经暴露的凭据。

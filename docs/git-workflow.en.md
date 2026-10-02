# Git Workflow

This guide is for users and contributors to Spec Harness. The public repository is [MicTx/spec-harness](https://github.com/MicTx/spec-harness), and released versions are listed on [GitHub Releases](https://github.com/MicTx/spec-harness/releases).

## Get the Source

Use a tag for a stable installation and a full commit SHA for reproducible builds:

    git clone https://github.com/MicTx/spec-harness.git
    cd spec-harness
    git fetch --tags origin
    git checkout v0.13.10

Contributors normally clone a fork and keep the official repository as upstream:

    git clone https://github.com/<you>/spec-harness.git
    cd spec-harness
    git remote add upstream https://github.com/MicTx/spec-harness.git
    git fetch upstream --prune
    git switch main
    git reset --hard upstream/main

Do not run reset --hard with uncommitted work. Commit, stash, or save the work elsewhere first.

## Branches

Use one branch per change and create it from the current main branch:

    git switch -c docs/git-guide

Use an intent-revealing prefix such as docs/, fix/, feat/, test/, or chore/. When a Spec Harness task package is used, follow its naming rule: spec/YYYY-MM-DD_<verb>-<object>, for example spec/2026-10-02_audit-docs.

Synchronize before opening a pull request:

    git fetch upstream --prune
    git rebase upstream/main

## Commits

Use Conventional Commits:

    <type>(<scope>): <imperative summary>

Common types are feat, fix, docs, test, refactor, and chore.

    docs(git): add contributor workflow
    fix(export): reject source-tree output paths

Keep the subject short and imperative. Use the body for the problem, behavior change, and compatibility impact when the subject is not enough. Never commit credentials, personal paths, temporary files, or private task records to the public repository.

## Pull Requests

Push the branch and open a pull request against main:

    git push -u origin docs/git-guide

Include:

- The problem and resulting behavior.
- Affected commands, files, or public interfaces.
- Verification commands and their results.
- Compatibility or migration notes.
- Examples, screenshots, or reproduction steps for documentation and CLI changes.

Run the relevant checks before pushing. The full suite is:

    python3 -m compileall -q scripts server hooks slots tests book
    python3 scripts/smoke_test_spec_skill.py
    python3 -m pytest -q
    ruff check scripts/ server/ hooks/ slots/ tests/ book/
    ruff format --check scripts/ server/ hooks/ slots/ tests/ book/
    git diff --check

For documentation changes, also check links and the repository structure:

    python3 scripts/organize_project_structure.py --root . --check

## Versions and Releases

Users download archives from GitHub Releases and verify SHA256SUMS. Maintainers use vMAJOR.MINOR.PATCH tags. Changes to command aliases, task-package structure, exported layout, or behavior contracts must be documented in CHANGELOG.md and the release notes. User installation, upgrade, and rollback instructions are in [RELEASE.md](../RELEASE.md).

## Common Git Problems

**non-fast-forward**: fetch the remote, inspect the branch, and rebase onto the current main; do not force-push without a clear reason.

**working tree is not clean**: use git status to identify local work, then commit or save it before switching branches.

**Detached HEAD**: create a branch before continuing:

    git switch -c fix/from-release

**A staged file should not be public**: inspect git diff --cached --name-status, remove the file before pushing, and rotate any credential that was exposed.

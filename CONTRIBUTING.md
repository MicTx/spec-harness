# Contributing to Spec Harness

This guide answers the contributor's first three questions: where is the source of truth, which files may change together, and what evidence makes a pull request reviewable? It covers the public GitHub repository and the checks expected for a pull request.

## Before You Start

- Read the [Git workflow guide](docs/git-workflow.en.md).
- Search existing issues and pull requests before opening a new one.
- For a security issue, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
- Keep changes focused. A pull request should solve one problem and include the documentation needed to use it.

## Development Setup

    git clone https://github.com/MicTx/spec-harness.git
    cd spec-harness
    python3 -m venv .venv
    source .venv/bin/activate
    python3 -m pip install -e '.[dev]'

Python 3.9 or newer is supported. The runtime scripts use only the Python standard library; the optional dev extra provides the test and lint tools.

## Branches and Commits

Create a branch from the current main branch. Use a short, descriptive branch name such as docs/git-guide, fix/path-validation, or spec/2026-10-02_audit-docs.

Use Conventional Commits:

    <type>(<scope>): <imperative summary>

Common types are feat, fix, docs, test, refactor, and chore. Keep the subject concise and explain the user-visible reason in the body when the subject is not enough.

    git fetch origin --prune
    git switch main
    git pull --ff-only origin main
    git switch -c docs/git-guide

Do not commit generated release archives, local credentials, private task records, or editor state unless the change explicitly updates a tracked distribution artifact.

## Make a Change

1. Identify the source of truth before editing. Runtime behavior belongs in scripts and tests; command contracts belong in `references/`; reader-facing behavior belongs in the README and public guides.
2. Update the Chinese and English reader docs when a user-facing command or contract changes; a command that works but cannot be found is still an incomplete change.
3. Add a regression test for changed behavior, especially for path handling, archive contents, installer behavior, and task-package gates.
4. Keep public documentation free of private repository paths, credentials, local machine paths, and maintainer-only task records.

## Verification

Run the checks that match the change. The full suite is:

    python3 -m compileall -q scripts server hooks slots tests book
    python3 scripts/slot_registry.py validate
    python3 scripts/smoke_test_spec_skill.py
    python3 -m pytest -q
    ruff check scripts/ server/ hooks/ slots/ tests/ book/
    ruff format --check scripts/ server/ hooks/ slots/ tests/ book/
    python3 scripts/organize_project_structure.py --root . --check
    python3 scripts/check_all_spec_packages.py --root .
    git diff --check

For documentation-only changes, run the Markdown link and structure checks plus the affected tests. If a check cannot run, state the reason in the pull request.

## Pull Requests

Open a pull request against main from your fork or branch. Include:

- The problem and the resulting behavior.
- The files or public interfaces affected.
- Verification commands and their results.
- Compatibility or migration notes for changed commands, task-package formats, or exported layouts.
- Screenshots or command output when a documentation or CLI change affects what users see.

A pull request is ready when the change is reviewable from the diff, tests cover the changed behavior, and public documentation matches the implementation. Maintainers may request a smaller scope or a follow-up pull request when unrelated cleanup obscures the main change.

## Documentation Standards

- Use direct language and concrete commands.
- Keep examples executable and use the public repository URL.
- Link to the source of truth instead of copying long gate lists into several files.
- Keep historical decisions in maintainer records; public guides describe the current supported behavior.

## License

Contributions are accepted under the terms in [LICENSE](LICENSE).

# ADR 0007：Bash write coverage gap

- 状态：Superseded（0.27 删除激活守卫，本缺口不再适用）
- 范围：调研结论；不修改 `hooks/claude_activation_guard.py` 或 `scripts/check_spec_package.py`
- 当前证据：`hooks/claude_activation_guard.py:23-40` 明确 Bash、MCP 和自定义工具不在覆盖范围，且内部错误 fail open；`hooks/claude_activation_guard.py:67` 只匹配 `Edit`、`Write`、`MultiEdit`、`NotebookEdit`、`Agent`、`Task`、`SendMessage`；`tests/test_activation_hooks.py:5-10` 已把该边界写成测试语义。

## Problem

The activation guard can remind an agent before supported editing tools, but Bash can write through redirection, `tee`, `sed -i`, `mv`, `cp`, `rm`, `touch`, script interpreters, wrappers, or command substitution. The current hook intentionally does not parse those commands. Treating it as a security boundary would overstate its guarantee.

## Option A: conservative Bash matcher

Add `Bash` to `PreToolUse` and reject commands containing conservative write patterns such as redirection, `tee`, `sed -i`, `mv`, `cp`, `rm`, and `touch`.

- Catches: obvious one-line writes and mutations in the project tree before execution.
- Misses: shell functions, aliases, environment wrappers, interpreters, containers, Git commands, command substitution, generated scripts, and writes outside the recognizable text.
- False-blocking risk: high. Legitimate reads can contain redirection text, `cp` can stage temporary evidence, and hosts quote commands differently.
- Change scope: hook parsing, installer wiring, activation doctor, and hook fixtures across host payload variants.
- Tests: parameterized subprocess fixtures for blocked and allowed commands, quoted commands, pipes, worker events, malformed input, and each supported host payload shape.

## Option B: acceptance-time diff boundary

Before check acceptance, compare the repository's changed and untracked paths with the package's declared boundaries. Report every path outside the boundary as an actionable finding; require the main session to record it as a deviation, move it to another package, or restore it.

- Catches: writes made through Bash, MCP, custom tools, subprograms, containers, or any other mechanism that ultimately changes the repository, including untracked files.
- Misses: side effects outside the repository, ignored files excluded from the audit, files written and fully reverted before acceptance, and commits already made outside the selected diff baseline.
- False-blocking risk: medium. Pre-existing user dirt, generated test artifacts, and legitimate shared-file updates need an explicit baseline or recorded deviation.
- Change scope: package checker, boundary metadata or parsing, check-stage prose, and focused checker fixtures. No host hook contract changes.
- Tests: construct clean temporary Git repositories with in-boundary modifications, out-of-boundary tracked and untracked files, ignored files, renames, deletions, symlinks, dirty baseline carry-forward, and non-Git roots. Assert unrecorded out-of-boundary paths fail; recorded deviations pass; existing package behavior remains unchanged when the boundary declaration is absent.

## Option C: layered guard

Implement both the conservative pre-execution matcher and the acceptance-time diff boundary.

- Catches: obvious writes early and all repository-visible writes later.
- Misses: each mechanism keeps its own misses above; acceptance review still cannot observe external side effects.
- False-blocking risk: highest, because one execution can be stopped by a pattern matcher and again rejected by path audit.
- Change scope: both ranges above plus coordination guidance to avoid duplicate findings.
- Tests: both test plans, plus a combined case proving a Bash-caught write and a later custom-tool write produce one coherent review finding rather than two conflicting states.

## Recommendation

Recommendation: Option B.

The current guard is a cooperation reminder, not a security boundary. Parsing arbitrary shell across hosts is brittle and can block legitimate work, while a path-level acceptance audit observes the result regardless of the writing tool. Start from the repository diff, preserve a recorded pre-task baseline for dirty trees, and make unexplained paths review findings.

Flips if: a host supplies a canonical parsed shell command rather than raw text, or path-level review proves unable to cover ignored/generated repository state reliably. In that case add Option A as a narrow pre-warning and keep Option B as the acceptance gate.

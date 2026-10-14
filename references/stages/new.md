# `/spec:new`

Goal: create a new task package and compress a complex task into an executable specification.

Compatible invocation:

- Claude Code CLI: `/spec:new`
- Codex / generic skill-aware CLI: command alias `spec:new`, triggered by `$spec`

Execution order:

1. Clarify the most critical information: project goal, target users, MVP, technology preferences, boundaries, and exclusions.
2. Write out explicitly: confirmed facts, key assumptions, open questions, optional interpretations, and current trade-offs.
3. Name the package with a stable, readable `YYYY-MM-DD_<verb>-<object>` Development Record slug (e.g. `2026-06-12_add-login-rate-limit`); verbs come from the controlled vocabulary in [`../naming-and-commits.md`](../naming-and-commits.md).
4. In a Git repository, run the `/spec:new` branch preflight before writing any package file:
   - Require a clean tree; with uncommitted or untracked changes, stop and output triage advice (commit, stash, or move unrelated changes into their own Development Record). One class of dirt is carried instead of blocking: entries consisting solely of another active package's own documents under `<specs>/specs/<slug>/` (checklist ticks, evidence, handoff) are attributed per file with `--untracked-files=all` and skipped, so a previous round's unarchived documents never block the next package. Business files and `archive/` dirt still stop. After creation the script prints one hint pointing at `scripts/install_git_hooks.py`; `doctor` warns when hooks are missing (the disk-truth gate surface is absent).
   - Scan active records for their `Git integration branch` metadata; refuse when the target branch is bound to another active package — one package one branch, one branch one active package.
   - Switch back to `main` (or `--main-branch`) first; with an upstream, `fetch` and update with `--ff-only` only. A transport-layer outage (same `remote_unavailable_detected` classifier as `push`: `unable to access`, refused, timeout, HTTP 502) creates the integration branch from local main instead, recording local mode in the output and `spec.md`; auth failures, permission denial, missing repository, or a non-fast-forwardable main still stop.
   - Create and switch to an independent integration branch from the up-to-date main branch; the default name is `spec/YYYY-MM-DD_<slug>`, overridable with `--branch`.
   - If the target integration branch already exists, never silently reuse or overwrite it; stop and ask for an explicit recovery or a different branch name.
   - Refuse creation/use when the target branch is a protected branch: `main`, `master`, `develop`, `release/*`, `hotfix/*`, or the remote default branch.
   - `spec.md` must record the final integration branch; `run/check/done` continue only on that branch.
5. Outside a Git repository, or with the explicit `--skip-git-branch` script argument, record the out-of-scope reason in `spec.md`; otherwise the branch preflight is never skipped.
6. Prefer running (add `--main-branch` / `--branch` for non-default branches):

```bash
python3 scripts/init_spec_package.py --root <project> --slug YYYY-MM-DD_<slug> --title "<project title>" [--main-branch main --branch spec/YYYY-MM-DD_<slug>]
```

7. Fill the three documents under `.spec/specs/YYYY-MM-DD_<slug>/` per [`../templates.md`](../templates.md). Every task carries `boundary` and `verify`; replace the template examples with real boundaries and verification commands that prove delivery before execution.
8. When the task involves judging whether the project layout is reasonable, write the first-principles audit conclusion into `spec.md` first: directory responsibilities, module boundaries, dependency direction, runtime/source/state separation, entry discoverability, test and documentation support. If the conclusion is unreasonable, add the target tree, a file migration/merge table, a reference migration map, a deprecated-item list, and verification; if reasonable, state explicitly why no restructuring happens.
9. Lock the minimal implementation path in `spec.md` and name the abstractions, extensions, and configurability not done this round.
10. After creation, continue straight into execution when no project-shaping decision is pending; only genuinely undecidable items go to the user.

Constraints:

- `spec.md` states both in-scope and out-of-scope content.
- `spec.md` states key assumptions and open questions.
- In a Git repository, `spec.md` records this Development Record's integration branch; `run`/`check`/`done` continue only on that branch, and a protected, detached, unmatched, or double-bound target branch fails closed.
- `tasks.md` must be executable, every task with `boundary` and `verify`.
- `checklist.md` must be verifiable; avoid vague adjectives.
- For directory optimization, `tasks.md` must split out structure audit, migration/merge, reference verification, documentation sync, and deprecated-item cleanup tasks; never collapse "tidy the directory" into one unauditable task.

Output contract:

- Render the `/spec:new` dashboard per [`../output-contracts.md`](../output-contracts.md).


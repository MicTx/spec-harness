# `/spec:push`

Goal: after `done` and the working-branch commit, safely merge into main, push, and delete the confirmed-merged local and remote working branch.

Compatible invocation: Claude Code `/spec:push`; other skill-aware CLIs trigger `spec:push` via `$spec`.

Prefer running:

```bash
python3 scripts/push_spec_package.py --root <git-repo> --branch <working-branch> --main-branch main --remote origin [scope flags below]
```

Spec gate scope (choose one):

- **touched (default)**: gate the active/archive packages the working branch touches relative to main (archive paths map to real slugs). With no Spec-path changes the gate is not skipped: every non-merge commit must carry a `Spec: <slug>` footer resolving to a valid archive in the target revision — except trivial unpackaged work on the lite channel: a `Spec: none (<非空理由>)` footer (reason mandatory, no nested parentheses) releases the gate; a none-only branch is accepted when no active package exists. `archive/retired/YYYY-MM-DD/` containers from `organize` are exempt as historical paths.
- **Single**: `--slug <slug>` (repeatable). **All**: `--all-packages` (legacy).

Archive gate: unarchived active packages in scope hard-fail by default (the `/spec:done --archive` chain must have run); properly packed retro-packs pass because each theme package archives first; only `--allow-unarchived` downgrades to advisory, and broken or failed-acceptance packages always hard-fail.

The pre-push hook validates each pushed ref's target SHA; online it pins the push remote's main SHA as the only baseline (never caller overrides); `SPEC_PUSH_GATE=all` switches to all-packages and `SPEC_SPECS_DIRS` adds trusted Spec roots; enumeration / fetch / scope failures fail closed; only a genuinely local-only run selects the local main SHA.

Footer attribution intervals: archives record `提交区间：<base>..<head>` (`base` = merge-base of main and the archive-time HEAD; non-Git/main-less roots record 适用外). Footer-only pushes feed cited slugs + push-window base into the same judge (`scripts/check_all_spec_packages.py --footer-window-base`); an archive whose interval base predates the delivery-window fork point is named on stderr with the evidence triple — warn-only during observation, pre-interval archives never warn (record: `docs/adr/0003`, source repository).

In-flight branch gate split: `refs/heads/spec/*` pushes release as in-flight handoff pushes (reason on stderr) — integration branches carry WIP and `handoff.md`; the main-branch merge keeps the full gate. `pre-commit` splits the same way: main-branch or archive-path commits run the full worktree+index gate; `spec/*` WIP commits only structural lint (`--lint-only`; pass judgment excluded). Objective Git facts decide, never a committer footer.

Execution requirements:

1. On a dirty tree, triage / pack first — never mix-commit. Active package: package changes commit first (writing back tasks/evidence); unrelated changes get atomic theme commits, a temporary Development Record, or a separate worktree. No active package (retro-pack): cluster dirty files and unpushed commits into themes; prefer atomic packing — one package plus its atomic `Spec: <slug>` commit per theme (three documents, boundary/verify, acceptance, archive); a single coherent intent may be one package; default to multiple when both fit. Never push mixed-theme state or skip the documents.
2. Confirm a clean tree, a deletable branch, and passing preflight. A transport-layer outage (unreachable remote, refused connection, timeout, HTTP 502) degrades to local-only: merge into local `main`, keep the branch as retry anchor, re-run after recovery; auth/permission failures, conflicts, lease changes, protected branches, and broken hooks never enter local-only.
3. Merge → push main → confirm containment in remote main → delete the remote branch (remote lease), then the local branch. A local-only branch publishes first with `git push -u`; recovery finishes push and deletion when the remote returns.
4. Never delete long-lived branches: `main`, `master`, `develop`, `release/*`, or the remote default.
5. Being checked out on main is fine as long as `--branch` names a deletable working branch.
6. Broken remote Git hooks: treat the remote hook state as untrusted — stop all branch deletion; leave repair to the maintainer. Opt-in, for a user-administered Gitea: rerun with `--repair-gitea-hooks --gitea-url <base>` and the site-admin token via `SPEC_GITEA_TOKEN` or `SPEC_GITEA_TOKEN_FILE` (env wins; the `--gitea-token` flag is refused — a command-line token leaks in process listings). The script runs the Gitea admin cron tasks (`sync_repo_branches`, `sync_repo_tags`, `resync_all_hooks`) and reruns the push once; a second broken-hooks report or a failed repair keeps fail-closed.

Output contract:

- Render the `/spec:push` dashboard per [`../output-contracts.md`](../output-contracts.md); state the execute mode, branches, remote, and commands.

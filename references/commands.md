# Commands

This page is the stage index: shared conventions plus one link per stage. Each stage's full contract lives in its own file under [`stages/`](stages/) — read the one stage file that matches the current project state, then run the command it names. The default line is `new / run / check / done / push`; `update` / `status` adjust or inspect an existing package; `goal` is one-shot; `autoplan` produces the planning cluster; `autorun` chains rounds from it; `doctor` / `organize` maintain environment or structure outside the task-package state machine.

Shortest safe path: route from disk, make one bounded change, run its verification, let the next stage consume the recorded state. Do not read every stage file when one is enough.

## Host compatibility layer

The command inventory lives once in `SKILL.md`. Claude Code exposes `/spec:<command>`; Codex/ZCode use `$spec`; Devin/Cursor can load `/spec`, then request `spec:<command>`. Other compatible hosts use their native skill entry. Stage names describe intent, not universal slash-command registration. The route and tasks views remain internal. See the README installation and execution matrices: installing a skill is not proof that the active client has workspace execution or subagent tools.

Install profile boundary: `new / run / check / done / push` are the kernel stage commands every install ships. The extended stages (`goal`, `autoplan`, `autorun`, `update`, `status`, `doctor`, `organize`) belong to `INSTALL_PROFILE=full`; a core install (the default) answers an extended-stage invocation with the reinstall hint — `INSTALL_PROFILE=full bash install.sh` — instead of executing it.

Before executing any command, run the four-question Quick self-check in [`engineering-philosophy.md`](engineering-philosophy.md); a missing answer means fix the documents first.

## `/spec`

Goal: act as the default user entry point; run route internally, then continue with the most fitting stage.

Execution order:

1. Run `python3 scripts/route_spec_package.py --root <project>` first; when the package carries a `handoff.md`, the route output includes a 交接 line, and a resuming session reads it with `python3 scripts/spec_handoff.py show --slug <slug>` before continuing (contract: [handoff.md](handoff.md)).
2. No active package → `new`; if the user also asks for `push` and the work tree / unpushed commits carry discrete changes, enter the push retro-pack path (atomic packing by theme, or one whole new spec package) — never skip packing and push directly.
3. Active package that can continue → `run`; review request → `check`; archive/close-out request → `done`.
4. Full goal with the one-shot chain → `goal` (still new/run/check/done/commit/push order); merge-push-and-delete after done → the standalone `push` stage internally, reporting only the publication result or external blocker.
5. Scope or task adjustment → `update`; progress overview only → `status`.

Constraints:

- Route is never presented as a user-facing stage.
- Obvious small tasks take the lightweight path from `SKILL.md` (direct fix + test, push with a `Spec: none` footer) instead of `/spec:new` ceremony; the user decides, and `new` runs as usual when they want the record.
- User-visible output covers actual project progress, current work, concrete problems, and delivered results; internal routing commands are not offered as the user's next step ([output-contracts.md](output-contracts.md)).
- If routing depends on a key assumption, state the assumption first or ask the minimal clarifying question.

## `/spec:new`

Create a task package: scope, tasks, and acceptance criteria from a clarified goal — full contract: [`stages/new.md`](stages/new.md).

## `/spec:goal`

One-shot chain `new` → `run` → `check` → `done`, push only on explicit authorization — full contract: [`stages/goal.md`](stages/goal.md).

## `/spec:autoplan`

Interactive planning and framework/detail/review passes in the current host or a verified fresh CLI session, stopping at autorun readiness — full contract: [`stages/autoplan.md`](stages/autoplan.md).

## `/spec:autorun`

Finish the active package, plan the next round and continue in the selected execution mode until the recorded scope is complete — full contract: [`stages/autorun.md`](stages/autorun.md).

## `/spec:run`

Execute all tasks in dependency order, verify each, check it off; resumes from package state — full contract: [`stages/run.md`](stages/run.md).

## `/spec:check`

Review round: write actionable issues back into the package and fix them in-round — full contract: [`stages/check.md`](stages/check.md).

## `/spec:done`

Archive the package and create this round's commit; no merge, push, or branch deletion — full contract: [`stages/done.md`](stages/done.md).

## `/spec:push`

Merge into main, push, delete the confirmed-merged branch — full contract: [`stages/push.md`](stages/push.md).

## `/spec:update`

Add, remove, or adjust tasks and scope mid-flight, wrapped in a Git WIP recovery point — full contract: [`stages/update.md`](stages/update.md).

## `/spec:status`

Overview across packages: progress, blockers, next steps — full contract: [`stages/status.md`](stages/status.md).

## `/spec:doctor`

Environment self-check and repair (`--fix` repairs the safe items) — full contract: [`stages/doctor.md`](stages/doctor.md).

## `/spec:organize`

Structure audit, audit-only by default; `--apply` creates the package and fixes confirmed classes; never delete — full contract: [`stages/organize.md`](stages/organize.md).

## Session backends

### Resolve execution before transport

Keep agent identity, surface, actual tools and execution strategy separate. The main session supplies the actual `--client` and `--surface cli|ide-plugin|desktop|unknown` from session metadata or user instruction; an installed directory, outer editor, inherited CLI marker or binary on PATH is not that evidence. Unknown remains unknown, never guessed.

```bash
python3 scripts/chain_session.py route --chain autoplan --client devin --surface desktop --execution auto --format json
```

`auto` chooses fresh only for an explicitly identified CLI surface with an existing same-client Claude/Codex/Pi adapter; otherwise current. Devin/Cursor/ZCode and other CLIs without a verified fresh adapter can also use current. Explicit `fresh` accepts a verified `--worker`; specifying a worker with current is an error. Route returns requirements, not fabricated capability observations. Check actual read/write, Python ≥3.9, Git and user-interaction tools before starting. No exec tool → report the missing capability, not “install another agent”. Native workers are optional; never claim independent review when only the main session reviewed.

### Current-session protocol

Use `scripts/chain_session.py` with `--chain autoplan|autorun --root <project>`; it never calls an LLM, starts a worker or controls windows. Native host tools perform the actual work. Supported execution environments are macOS and Linux/WSL2; native Windows receives WSL2 guidance.

- `status [--owner <id>]` reads the checkpoint and recovery action. `route`, current `status` and every current `--dry-run` are read-only, including malformed-state handling.
- Mutations take `--chain-id <id> --owner <opaque-session-id> --expected-revision <n>`. Generate a fresh UUID once per new chain, retain it for retries, and never reuse a closed chain's id. Start at revision 0, then carry that chain's returned identity/revision. Never retarget a rejected request to another chain-id. Owner is a stable per-session identifier, not a command, path or secret; only its hash is stored in machine-local audit. All commands support `--format text|json`; mutations support `--dry-run`.
- `begin --step-id <stable-id> --client <client> --surface <surface> [--plan <docs>]`: autoplan adds `--kind framework|detail|review [--target <doc>]`; autorun adds `--slug <package>`. Use the chain's `--max-passes`/`--max-rounds` or `--unbounded`; later current begins retain the recorded cap unless explicitly overridden. A repeated step resumes rather than re-spends budget; different work needs a new step after the previous one completed. Never invent a new step-id merely to bypass an error.
- `finish`: attest only after actually doing the assigned work; the script validates its stage-specific evidence. Autoplan review requires `--review-mode main|independent --review-note <conclusion>`; `--outcome revise` records a review needing a repair pass, not readiness. Package finish proves archive acceptance, not commit or publication.
- `pause --reason <reason>`: persist the stop, retaining unfinished work. Update package handoff when applicable. `resume` restores that same unit without another pass/round. A different owner must explicitly `--takeover` an active or unknown-owner unit, only after confirming the former session stopped; a paused unit is available for handoff.
- Current models and permissions remain host-managed; CLI model/reasoning/bypass flags cannot configure the desktop host. Current mutations reject `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR` and `GIT_INDEX_FILE` overrides: resolve the environment explicitly, never silently redirect the target repository or edit Git configuration.
- Journal persistence precedes the summary write. If the summary write fails, the transition may already be durable; persistent repair failures remain nonzero even on duplicate retries. Fix storage and retry the same identity/owner/revision, never reset state. Stale chain identities or revisions, conflicting owners, edited scope and unknown formats fail closed.
- A torn final journal line is preserved verbatim in a machine-local `spawns.torn-<digest>.jsonl` backup before a valid mutation restores the complete prefix and appends its transition. Status/dry-run never repair files; malformed interior records still block.
- `close` requires the same mutation arguments and a proven ready/complete chain. It preserves a history snapshot; autoplan verifies reviewed document identity and readiness with unchecked features, autorun verifies implemented scope and archived work. No force/reset and no session termination.
- After planning readiness, a newly authorized framework step may explicitly revise the same selected masters, preserving earlier review receipts. Changed ready documents cannot be closed as if the old review still applied; clarify the revision before replanning. Autorun's frozen scope is never relaxed this way.

Tasks and acceptance remain in the task package. `.spec/<chain>/chain.json` stores the continuation summary; the journal and owner hash live in the actual Git-dir's `spec-chain/<chain>/`, including linked worktrees. New writes use v3; existing v2 current checkpoints remain readable and upgrade on the first valid identity-bound mutation, advancing revision once without spending a pass/round. Old rows remain intact; downgrade after v3 is refused. The transaction/owner protocol is not an OS-level workspace lock or background scheduler. Closing the client stops execution; new sessions resume explicitly. Active legacy fresh chains and their model locks are never automatically converted.

### Fresh CLI transport

Both existing spawn scripts accept `--terminal auto|terminal|tmux`: macOS selects Terminal.app, Linux/WSL2 selects tmux (tmux ≥3.2 + ps). The worker remains a full interactive session — visible TUI, persisted, never headless. Native Windows uses WSL2. A fresh spawn refuses an active current checkpoint rather than overwriting it. Failed launches keep the legacy rollback contract (交接未确认 = 不留进程); window/pane lifecycle policy: source `docs/adr/0001-terminal-spawn-window-lifecycle.md`. Fresh `--dry-run` prints the selected launch and attach instructions without launching.

Legacy `scripts/autoplan_spawn.py close` / `scripts/autorun_spawn.py close` retain their historical implemented-feature proof and do not close active current checkpoints. They preserve `.spec/<chain>/history/`, refuse missing/changed plans, active packages or a held mutex, and never kill sessions. A new closed-chain successor starts at 1 without inheriting the old model lock. Legacy states without a fingerprint remain readable but cannot be closed automatically.

For a legacy state only, maintainers may supply **both** `--legacy-scope-commit <oid>` and `--legacy-completion-commit <oid>` to `close`, starting with `--dry-run`. This is evidence-based closeout, not a reset or fingerprint backfill. The scope commit must be the last committed change to this chain's state; scope, completion, and current HEAD must form an ancestor chain in the same local repository. Apart from the declared volatile fields, the chain identity must match at all three commits and in the live summary. Each recorded plan must exist as a regular file at both historical anchors, contain unique feature texts in the same order, and have every original feature checked at completion. Git evidence reads use the existing trusted-executable policy, ignore inherited Git repository overrides, and disable replacement objects. Live recorded plans must also be readable, fully checked, and match HEAD byte-for-byte with no staged changes; checkout EOL or filter conversions are explicitly unsupported by this recovery path, and no repository clean/textconv filter is run to reconcile them. Later plan additions are inspected separately, never counted as completed work of the old chain. The immutable close archive preserves the original legacy state, both full commit OIDs, state/plan blob identities and texts, the reconstructed historical-scope digest, and the separate live observation. Ordinary close, lock, active-package, audit, and crash-retry checks remain in effect; absent or contradictory evidence refuses without changing the chain. Neither the historical plans nor their checkbox states are rewritten.

## Script working-directory conventions

Task-package script examples run from the skill package root by default; to operate on another project, pass `--root <project>` to the scripts that support it, or replace `scripts/` with the absolute path of the installed/exported skill. `--specs-dir <dir>` must be a trusted relative directory under root — never empty, absolute, or containing `..` — and its real resolution must stay under root. `push_spec_package.py` is the Git handoff script; it also runs from the skill package root with `--root <git-repo>` pointing at the target repository.

Output channel contract: every script prints its contract output — the rendered dashboard, the `--format json` payload, generated summaries — to stdout, and every diagnostic (argument errors, missing files, branch/environment failures, stack traces) to stderr. stdout must stay parseable: gates, hooks, and machine consumers read it, so debug prints and warnings belong on stderr (`print(..., file=sys.stderr)`), never interleaved with contract output. New scripts follow this split by construction; `/spec:doctor` covers this contract via the periodic `skill.channels` probe spot-check.

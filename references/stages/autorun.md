# `/spec:autorun`

Goal: run the recursive autonomous chain. One round = one fresh agent session: finish the active package goal-style (`run` → `check` → `done` → `push`), plan the next round from the project planning documents (`/spec:new`), then spawn a fresh interactive session with the autorun prompt, until every feature checkbox is checked. `autorun` creates no second state machine and skips no gate.

Compatible invocation:

- Claude Code CLI: `/spec:autorun [--plan <docs>] [--max-rounds <n> | --unbounded] [--permission-mode <inherit|ask|bypass>]`
- Codex / generic skill-aware CLI: command alias `spec:autorun`, triggered by `$spec`

### Session backends

The shared [session backends](../commands.md#session-backends) contract (`--terminal auto|terminal|tmux`, platform matrix, failure semantics, `--dry-run`) applies to both spawn scripts.

Planning-document discovery (`scripts/autorun_spawn.py plan --root <project> [--plan <path[,path...]>]`): `--plan` wins (paths under the project root); otherwise scan `.spec/plans/` — top-level `*.md` plus each cluster's `*/master.md`, qualifying only with at least one markdown feature checkbox (`- [ ]` / `- [x]`); `archive/` and legacy locations are never scanned. No qualifying document → fail closed, exit 3: report candidates, ask the user to plan; never invent a plan. The chain ends when every qualifying-document checkbox is `- [x]`.

Execution order (each round):

1. Route with `scripts/route_spec_package.py --root <project>`. No active package + unchecked features → create the next package per [`new.md`](new.md) from the next unchecked feature or coherent group (assume reasonably, do not stall on questions). Neither → chain complete: report and stop.
2. Self-run per [`run.md`](run.md), self-review per [`check.md`](check.md) until the acceptance surface is fully checked and passing, self-archive and commit per [`done.md`](done.md), then push per [`push.md`](push.md):
   `python3 scripts/push_spec_package.py --root <git-repo> --branch <working-branch> --main-branch main --remote origin --slug <slug>`
3. Re-run `plan` after a successful push; all features checked → completion dashboard, stop (no spawn).
4. Otherwise plan the next round per [`new.md`](new.md). Every autorun-planned package carries a final plan-sync task whose `boundary` covers the planning documents and `.spec/autorun/` chain state, so checkmarks and chain audit land on `main` with that round's push.
5. Spawn the next round with the invocation above (`scripts/autorun_spawn.py spawn --root <project> …`, flags as declared). The script enforces the safety rails below, records `.spec/autorun/chain.json` (summary; runtime in `.git/spec-chain/autorun/`), then recycles the previous round's Terminal window (fail-open). The session reports the round summary and ends.

Chain rules:

- Fail fast, never spawn after failure: the first failed verification, check gate, Git hook, commit, or push preflight stops the chain; report per [`goal.md`](goal.md). Recovery: re-run `/spec:autorun` and follow the `resume: <action> (<reason>)` decision from `scripts/autorun_spawn.py status`.
- Strictly sequential: one spawned next-round session per project at most (flock in `.git/spec-chain/autorun/`); parallel chains are refused, never queued.
- Round cap: a restored default bounds every chain — 5 rounds without a flag; `--max-rounds <n>` overrides and `--unbounded` removes the cap (mutually exclusive). The effective cap is recorded and forwarded in the next round's prompt; a recorded cap never binds later spawns — the flag alone decides, per spawn.
- Permission mode: `--permission-mode inherit|ask|bypass`, default `inherit` — escalation only on an effective `bypass` (renders claude's `--dangerously-skip-permissions`); undetectable parents resolve to `ask`, never escalate silently; requested and effective mode are recorded and rendered by `status`.
- Feature-set fingerprint: the first spawn freezes a hash of the planning feature checkbox texts; a later spawn refuses (no state written) when a feature was added, removed, or reworded mid-chain — checking off is legitimate progress.
- Worker host and model identity: host resolution is `--host` > `SPEC_AUTORUN_HOST` > detected session host > first on PATH (codex, pi, claude); same-host continuation is the default, never a silent switch; zcode has no CLI. Model identity (`--model` / `--reasoning`) comes only from flags and chain state — never env — is locked into `chain.json` on first spawn; a mismatch refuses without writing state (argv: `scripts/chain_spawn_support.py`).
- Terminal.app automation is scope-limited to three authorized steps — this spawn, the autoplan pass spawn, and the dead chain-window sweep (`scripts/sweep_chain_windows.py [--dry-run]`); no other stage manipulates Terminal or AppleScript.
- The three user-interruption conditions of [`goal.md`](goal.md) apply unchanged; a planning-document gap (exit 3) also stops the chain.

Output contract:

- Follow the `/spec:autorun` section in [`../output-contracts.md`](../output-contracts.md).

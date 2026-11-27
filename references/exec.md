# Execution Phase

## Quick path

For an explicit small reversible goal with no active package, inspect the affected code and choose one or two exact non-UI product files and a real test. Run `python3 <installed-skill>/scripts/spec.py quick '<observable goal>' --path <file> [--path <file>] --verify '<test command>'`; then edit those files here and run `python3 <installed-skill>/scripts/spec.py check`. Use `--done '<outcome>'` if the goal itself is not observable. This is two lifecycle CLI calls. A successful check runs the test once, records a local receipt and leaves changes unstaged on the original branch; it does not commit, merge or push. On failure fix within scope and run check again. `spec.py next` resumes after interruption.

Quick requires a named clean Git checkout, an unstaged index and no competing draft or active package. It refuses UI, secrets, migrations, configuration, wildcard paths and unrelated changes. If the work is larger, the test needs unavailable services, the checkout is already dirty, or delivery needs a commit or handoff, use the standard plan instead; never stash or absorb user work.

## Standard path

After `plan-go`, `/spec:run` follows the ready DAG inside each boundary, runs the declared verify, checkpoints, then enters `/spec:check`. `verify --all` stops at the first failure and never re-runs checked cards. Repair packages use the same loop. Use `plan-amend` for authorized additions or revisions; `spec.py list` and `spec.py diff` are read-only. Project verify timeout: 1800s (`SPEC_PROJECT_VERIFY_TIMEOUT` overrides it). CLI output is concise; add `--json` for the full result.

## Delivery Recovery

Start with `spec.py check`, not a probe. `check` and `done` share one verdict. `findings` classify each issue by `code`, `severity`, `owner`, `subject` and `action`. Warnings do not block. Binding, frozen commands, actual failed acceptance, bad proof and unsafe staged/committed changes still block. Never stash, reset or absorb unknown changes. Unstaged outside changes remain untouched; when present, final verifies use a private checkout of the delivery candidate.

1. Follow `next`: finish independent ready cards first, then address the selected blocker. Checkpoints prove progress, not delivery. A real check always runs the distinct frozen commands on the candidate tree and refuses tree or plan changes during verification.
2. On failure, inspect the recorded evidence and replan the next in-scope step: test a different hypothesis, change the implementation, or finish an independent ready card. Keep the frozen goal and acceptance. Change the task graph or verify command only through an authorized `plan-amend`; do not reopen planning or weaken a test just to pass it.
3. Stop has no round quota. A verified checkpoint can advance ready work; a failed verify can request a replan when both the tested tree and diagnostic are unseen for that task and command. A returning candidate, repeated blocker, idle callback, or probe does not keep the loop alive. Pause, cancel, and parked authority decisions release Stop. The task failure gate still controls `unblock`.
4. After a fix, run **the same `spec.py check`**. Manual checks remain available. Claude-style hosts and current Pi can re-enter Stop and are evaluated against the same evidence; other hosts can use this strategy in the current turn. `spec.py next` is read-only. `check --check-only` never verifies or delivers.

A pass completes final-tree verification, archives and commits, then applies V4. A handoff interrupted after archive remains pending; `spec.py push` retries it. Do not mistake a local archive for a completed merge or push.

## Decisions

Apply chosen forks with `D` and cite `F<n>`. Decide reversible in-scope changes by V1 and record reversal. Park unauthorized work, then continue independent cards. Question budget: the only user-bound channel is `## Parked` and its projected decision cards. Ask once, up to four cards, only after checking evidence and exhausting safe options. Every ask that does reach the user carries its ledger: attempts, recommendation, alternatives and consequences; an ask without that record is a violation. Never assign the user a repair.

Use `spec.py decide --resolve P1 --choice resolve --by 'user <reference>' '<decision>'` to close a parked item without deleting history. `wait` leaves it pending without asking again. For blocked handoff `P0`, `wait` keeps V4; `local-commit` permits this package's local commit only. Neither choice waives proof or authorizes any additional remote action.

## Failure Ladder

Each rung is an invitation to diverge: start with the smallest fix, use a minimal reproduction for the repeat, then compare last green and try another approach. The signature includes the exit status (a timeout is distinct from a command returning 124), and discounts common runner metadata such as pytest timings and temporary run roots. A fourth matching failure, or an eighth failed verify since the last unblock, blocks the card with `[!]`; continue independent work. Recover with `spec.py unblock <task-id> --reason '<new evidence>'`.

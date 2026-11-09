# Engineering Philosophy

Spec Harness is efficient, minimal and pragmatic: it records just enough to resume and prove delivery, and defends only what Git cannot undo. Every rule below is decidable and measured by `tests/test_budget.py` or `tests/test_ceremony.py`.

## Trust model

- The model is a collaborator, not an adversary. Scripts catch forgetting and miscounting; they do not try to catch deception. Adversarial cases belong to host permissions and human review.
- Verify results, not process: gates read disk state and command exit codes, never intent or who edited what.
- Git is the recovery mechanism. Roll back with commits and `git revert`; never build snapshots, backups, journals or migration state.

## Four principles

| Principle | Rule | Measure |
| --- | --- | --- |
| Efficient | Price every mechanism in model steps: script calls, required fields, bytes to read | Standard package ≤ 4 script calls; LITE ≤ 10 required fields |
| Exact | One owner per fact; one check point per invariant, placed just before the irreversible step | A second check of the same invariant is a defect |
| Minimal | Delete first. A guard without a real incident is removed; compatibility covers the previous minor only | `legacy` branches are gone by the next minor |
| Pragmatic | Hard gates only where Git cannot roll back | ≤ 8 hard gates |

Day to day: write down unconfirmed premises (assumptions), build the smallest path that closes the loop, touch only the task `boundary`, and know the `verify` command before editing.

## Defense admission test

Ask in order for any check, lock, fingerprint, compatibility branch or documented prohibition; stop at the first "no":

1. Did a real failure happen (issue, reproduction, incident)? No → delete it.
2. Is the consequence irreversible or outside Git? No → make it advisory (print, never block).
3. Does a later gate already catch the same failure? Yes → delete this one, keep the last.
4. Is the cost (code + model steps) below the expected loss? No → use a cheaper form (a hint, a dry run).

Only four "yes" answers justify a fail-closed gate. A PR that adds a guard names the incident in its description.

## Gate levels

| Level | When | Form | Examples |
| --- | --- | --- | --- |
| L0 hard gate | Irreversible or outside Git | Script refuses | push, branch deletion, protected-branch writes, `--no-verify` |
| L1 delivery gate | Decides whether delivery holds | Checked once, at `done` | tasks checked, verify passed on the final tree, no open `[decision]` |
| L2 advisory | Process quality, format, naming | Printed, exit code unchanged | slug verb, commit footer, option comparison |
| L3 not done | Host differences, imagined adversaries | Report the missing capability and stop | parsing private host stores, hollow-command detection |

Contract output goes to stdout and diagnostics to stderr, so gates and hooks can parse stdout.

## Conflict order

1. Irreversible safety
2. Result verification
3. Minimal implementation
4. Clear boundaries
5. Explicit assumptions

## Writing rules

- Say what to do and which script owns the rule; never write what something "does not prove".
- A "never" sentence either points at the script that enforces it or is an L0 rule no script can enforce; otherwise delete it.
- A stage page holds one goal sentence, one command, and at most five human judgment points.

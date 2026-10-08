# Changelog

This file records user-visible behavior changes. For a detailed design or recovery story, follow the linked documentation; do not infer a new command or guarantee from a changelog bullet alone.

All notable user-facing changes to Spec Harness are recorded here. Internal development records are kept separately from the public documentation.

## [0.14.0] - 2026-10-08

### Added

- **Recursive chains keep their model identity**: `spawn` accepts optional `--model <id>` / `--reasoning <level>` flags, and the first spawn locks that identity into the chain state — later rounds without flags inject the locked value into the worker command line (pi `--model/--thinking`, codex `--model` plus `-c model_reasoning_effort=`, claude `--model/--effort`), so every round of a long chain runs on the same provider-model-reasoning combination instead of silently falling back to the worker CLI default. A mismatch against the lock (different id, different reasoning level, or a host change while an identity is set) refuses the spawn with both values reported and nothing written; `status` shows a `model:` line with the locked identity and the latest injection level.
- **Dead chain windows can be swept**: `python3 scripts/sweep_chain_windows.py [--dry-run]` closes Terminal windows a chain spawned whose session ended without a recycle (a crashed round, a refused spawn). Membership is proven by the ttys recorded in the chains' spawn audit — live sessions, multi-tab windows, and the user's own windows are never touched by construction.

### Changed

- **Round and pass caps adapt to the workload**: without an explicit flag, the autorun round cap grows with the unchecked feature count (features + 25% + 3, floored at 20) and the autoplan pass cap with the master document's detail links (2 + 3 per detail, floored at 12), so a chain no longer dies at the cap with work remaining; explicit `--max-rounds`/`--max-passes` still win exactly and are recorded and forwarded.
- **Spawned windows inherit the parent window's place**: each new round's Terminal window takes over the spawning session's window position and size, so recursive chains stack in place instead of cascading across the screen.
- **A crashed worker no longer strands the chain**: the window-open osascript gets one bounded retry on a transient Terminal hiccup, and a spawned worker that dies instantly is detected — the dead window is closed quietly and the spawn refuses with zero state written, so re-running the chain resumes exactly there instead of recording a round that never runs.

### Fixed

- **Finished sessions are recycled deterministically**: the previous round's window now closes through a three-phase escalation — a natural-exit grace, then SIGTERM, then SIGKILL to the lingering session's process groups — instead of waiting out a timeout while an idle interactive session held the window open (the dominant accumulated-window case). The signal targets are identity-checked against the worker command line first, and an idle single-tab window closes without ever raising Terminal's cancel/terminate prompt.
- **Empty or whitespace-only `--model`/`--reasoning` values are refused** at the door: a degenerate identity can no longer lock a chain into a state no real model id can ever match.

## [0.13.21] - 2026-10-07

### Fixed

- **Long chains recycle their windows again**: the next round's session is confirmed by reading the process command line, so recycling works on every host CLI instead of only the ones whose process image carries their own name; rounds no longer wait out the full confirmation timeout on hosts where the check could never succeed.
- **A finished window is never closed out from under a live session**: the previous round's window closes only while it holds just that round's tab and the session has already exited; otherwise the window stays open, the chain state records why, and no cancel/terminate prompt interrupts the chain.
- **Planning paths containing spaces survive the chain**: the next round's prompt keeps `--plan` values quoted, so `docs/plan v2.md` no longer arrives as a truncated argument and the next round keeps planning from the intended document.

## [0.13.20] - 2026-10-07

### Changed

- **Serial fresh-session planning passes for `autoplan`**: framework, detail, and review planning now run as serial full agent sessions in their own Terminal windows (visible interface, session saved and resumable) instead of in-process subagents sharing the main session's context budget — a failed pass only loses that pass and the chain resumes from the `.spec/autoplan/` state; the safety rails (pass cap, single-chain lock, stop at the first failure) mirror the autorun chain.

### Fixed

- **Codex chain rounds no longer die at the commit stage**: the spawned codex worker keeps its `workspace-write` sandbox but carves the repository's `.git` back into the writable roots — newer codex builds deny `.git` writes under that sandbox, which stopped `git add`/`git commit` with "Operation not permitted" and halted the whole chain.
- **The chain continues on the host that started it**: worker-host resolution now detects the spawning session's CLI (codex/pi/claude) before falling back to PATH order, so a Pi session spawns Pi rounds instead of silently switching agents; every spawn records `host` plus `host_source` in the chain state for audit.

## [0.13.19] - 2026-10-07

### Changed

- **Interactive next-round terminal for the autorun chain**: every spawned round now opens as a full interactive coding session in the new Terminal window — visible interface, session saved and resumable — instead of a one-shot non-interactive run; you can watch each round live and step in at any time, and the chain safety rails (round cap, single-chain lock, stop at the first failure) are unchanged.
- **Terminal window recycling for the autorun chain**: once the next round's session is confirmed running in its new Terminal window, the previous round's window closes automatically a few seconds later — long chains no longer pile up one window per round; the close is best-effort (skipped with a recorded reason when the old session is not in Terminal.app or the new worker is not observed in time), so a failed handoff never leaves you without a terminal.

## [0.13.18] - 2026-10-06

### Added

- **Interactive recursive planning**: a new `autoplan` entry point for project start or a phase boundary — it interrogates the user in themed rounds to pin down the goal, delegates framework and detail planning to the planner sidecar until every feature is sized for one task package, and gates the resulting planning-document cluster before stopping at the readiness report; it is the planning entry and never starts the execution chain itself.
- **Machine-judged planning readiness**: the planning cluster passes a structural gate — qualifying documents carry feature checkboxes, detail references resolve, and a goal statement exists — so "ready for the autonomous chain" is a script fact, while cross-document business/data/flow consistency gets an independent review pass.
- **Broader planning-document discovery**: `PRD.md`, `docs/prd.md`, and `docs/design/*.md` now also qualify as planning documents for both planning and the autorun chain, alongside the existing plan/roadmap locations.

## [0.13.17] - 2026-10-06

### Added

- **Recursive autorun chain**: a new `autorun` entry point drives long projects across sessions — it finishes the active task package through the full run/check/archive/push gates, then plans the next round from the project planning documents and opens a fresh Terminal window in the same path with the prompt injected, recursing until every planned feature checkbox is checked.
- **Machine-judged completion and safety rails**: the chain reads feature checkboxes from conventional planning-document locations (or explicit `--plan` paths) so "all features done" is a fact, not a guess; a round cap and a single-chain lock keep the recursion bounded and strictly sequential, and any failed gate stops the chain with a resume-from-disk recovery instead of spawning the next round.
- **Planning gap fails closed**: when a project has no qualifying planning document, `autorun` refuses to loop and asks for project-level planning first, so the chain never invents its own scope.

## [0.13.16] - 2026-10-05

### Changed

- **Leaner check-round writeback**: a failing check item is now written back with two required fields — the failed item and its round disposition — plus one explanation line that only becomes mandatory when the fix is deferred or a risk is accepted; recording a failure as a pass remains forbidden, and stable task ids still deduplicate the same finding across rounds.
- **Default check-round budget**: the check→fix loop stops at three rounds by default, and a package may declare its own cap in `spec.md`; a package without a declaration runs on the default instead of being blocked for missing one.
- **One routing rule for execution slots**: the run stage now names a single source of truth for batch fan-out, parallel review, and review-fix loops — the repository-owned workflow-runner driver when a worker CLI is available, with team-loop as the fallback runtime — so the command contract no longer contradicts the orchestration contract.

### Added

- **Workflow startup validity gate**: `lint_workflows.py` now refuses workflow files that would fail GitHub's startup validation, and the public exporter applies the same check fail-closed before publishing a filtered tree, so a broken workflow is caught locally instead of as a zero-second failure on the public CI.

### Fixed

- **Public export keeps executable bits**: the public-tree exporter now preserves executable permissions, so exported scripts and hooks stay runnable straight after a clean clone.

## [0.13.15] - 2026-10-04

### Added

- **Session-boundary handoff documents**: task packages can now carry an optional `handoff.md` that moves work across collaborator boundaries — one person's sessions, teammates, a fresh agent session, or an agent cluster's lanes — without re-deriving the repository; the departing side appends one structured entry, and any successor resumes from a generated snapshot of branch, HEAD anchor, task progress, and next ready tasks.
- **Mode-adaptive entries**: the fields an entry must carry grow with the collaboration mode (solo → team or agent → cluster), so a single person records only context and the next step, while clusters additionally record ownership, waiting-on lists, recovery commands, and the lane table.
- **Freshness verdicts**: the `spec_handoff.py show` command compares the snapshot's HEAD and task-progress anchors with current disk truth and reports fresh, stale, or unknown, so a successor knows exactly what to re-verify; staleness is advisory and never blocks a stage.
- **Lifecycle integration**: package routing surfaces a handoff hint line, status rows append a one-line handoff summary, the check gate validates the document's structure whenever it exists, and archiving appends the terminal close entry that freezes the log as read-only history.
- **Safe by construction**: handoff writes are atomic and verified after landing with rollback to the previous content, hand-written notes cannot forge entries or fields, and packages without the document keep working exactly as before.

## [0.13.14] - 2026-10-04

### Changed

- **Guided issue intake**: bug reports and feature requests now use GitHub issue forms with required fields, a duplicate-search declaration, and copy-paste commands for version and environment details, so reports arrive reproducible instead of free-form.
- **Standard Code of Conduct**: the repository now ships the Contributor Covenant 2.1 official full text instead of the previous self-authored conduct section.
- **AI-assistance disclosure**: the pull request template now asks contributors to disclose AI-assisted work and how a human verified it.
- **Calmer first read**: the bilingual READMEs and the project introduction open with a one-line summary that matches the repository description, so the first screen states what the tool is before how it is laid out.
- **Health-at-a-glance header**: both READMEs lead with linked CI, release, license, and Python version badges, so project status is checkable before reading.
- **Bilingual repository description**: the GitHub and Gitea repository descriptions now carry the Chinese one-line summary together with its English gloss.

## [0.13.13] - 2026-10-04

### Fixed

- **Bounded fan-out execution**: worker output is truncated against the remaining cap and the whole process group (grandchildren included) is killed on limit or timeout, so parallel runs can no longer grow unbounded or hang.
- **Safer worker invocation**: prompts are always passed after a `--` argv separator, and an unreadable route file fails with a clear message and exit code 2 instead of a traceback.
- **Addressable Pi results**: the Pi backend's recorded `outputFile` now points at the real output dump written to disk.
- **Round-closing discipline**: a round that still has non-terminal tasks refuses to close, and new tasks can only be spawned as pending inside an open round.
- **Run-root discovery**: loop state only accepts explicit `.agents`/`.git` markers, instead of treating any parent directory that happens to contain an `agents` folder as the run root.
- **Fail-closed stop gate**: the loop stop guard blocks the stop when run state cannot be read, and corrupt run directories are rejected instead of waved through.
- **Stricter convergence**: a run converges only when every round decided pass and no round is open or failed; failed rounds now count toward termination.
- **Stale-task reaping**: stale in-flight tasks can be reaped with an explicit reason, backoff queries use the run's own retry configuration, and resume lists in-flight tasks with a reap hint.
- **Cleaner slot hook installs**: the slot hook installer also stamps a directory-level ownership marker, so residues from the old hook layout are detected and removed.

### Changed

- Relicensed the project from the custom non-commercial source license to the GNU Affero General Public License v3 or later (AGPL-3.0-or-later); the LICENSE file, package metadata, and source headers now carry the AGPL terms.
- Tightened the bilingual README into a router-style facade (condensed layout tree, merged the misplaced identity section into the capability list) and synced the public docs' install version pins and license statements.

## [0.13.12] - 2026-10-04

### Fixed

- The push closeout now accepts the `archive/retired/YYYY-MM-DD/` containers the organize command creates as historical paths: they no longer fail record-slug validation or the archived-bundle scan, so an organize round followed by push closes cleanly.

## [0.13.11] - 2026-10-04

### Added

- Added a reader introduction and a hands-on long-running task tutorial for AI learners, programmers, Agent developers, and long-running task developers.

### Changed

- Package checks reuse a cached scan of archived development records, so repeated gates finish almost instantly instead of rescanning every archive each run.
- The push closeout runs the repository gate once and sorts its findings, instead of repeating the same full check twice.
- Trimmed the skill entry document and reference guides down to rules that name their triggering stage, and folded the former operating-rules page into the remaining guides.
- The team-loop slot leaves batch fan-out and review-fix loops to the workflow-runner slot, so a batch request no longer routes twice.

### Removed

- Removed the legacy protocol import and task-id migration tools from the runtime package; protocol migrations never rewrite historical archives.

## [0.13.10] - 2026-10-03

### Added

- Added public Git documentation covering clone, branches, commits, pull requests, releases, and troubleshooting.
- Added public support, security, CODEOWNERS, Issue, and pull-request guidance.

### Changed

- Made the public release guide user-focused and removed private development infrastructure from reader-facing documentation.
- Added public-source metadata, GitHub community files, and documentation link checks.

## [0.13.9] - 2026-10-02

### Added

- Added the workflow-runner slot for bounded repository-owned fan-out execution.
- Added runtime package, plugin, and public-source export checks.

### Changed

- Renamed the distributable project to Spec Harness (spec-harness) while keeping spec as the compatible skill and command name.
- Included slots in runtime verification and release validation.

### Fixed

- Hardened output paths, task identifiers, cache files, symlink handling, process timeouts, and corrupted-input handling.
- Improved task-package lifecycle checks, archive recovery, and concurrent state updates.

## [0.13.8] - 2026-09-24

### Changed

- Consolidated workflow execution around the repository-owned runner and documented the current zero-host engine policy.
- Improved task-package routing, validation, and completion summaries.

## [0.13.7] - 2026-09-20

### Changed

- Refined installer host selection, runtime layout checks, and server-mode boundaries.
- Improved Git closeout checks and task-package evidence handling.

## Versioning

Spec Harness follows Semantic Versioning. Patch releases contain documentation, metadata, verification, and backward-compatible fixes. Minor releases add backward-compatible capabilities. Changes to command aliases, task-package formats, exported layouts, or behavior contracts require an explicit compatibility note.

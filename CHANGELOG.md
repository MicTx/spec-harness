# Changelog

This file records user-visible behavior changes. For a detailed design or recovery story, follow the linked documentation; do not infer a new command or guarantee from a changelog bullet alone.

All notable user-facing changes to Spec Harness are recorded here. Internal development records are kept separately from the public documentation.

## [Unreleased]

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

# workflow-runner slot

This slot answers a narrow execution question: when several independent items can run with the same
contract, how do we fan them out and keep a reviewable disk record? It delegates the **execution segment** of a spec package to the
repository-owned `workflow_fanout.py` deterministic driver. **Shape detection -> driver
preconditions -> managed execution -> structured evidence.** Routing, acceptance, and the
done/push gates never move out of the main session.

## What it solves

| Gap | Solution |
|---|---|
| (1) No code-level trigger | Code trigger `slots/workflow-runner/scripts/workflow_route.py --text "<goal>"` returns a structured recommendation (advisory, never blocks) |
| (2) Main session hand-spawns sidecars for parallel work | Deterministic orchestration: fan-out counts, verify gates, and convergence loops are code in `slots/workflow-runner/scripts/workflow_fanout.py`; Pi/Codex are worker CLIs only |
| (3) Parallel evidence is scattered | The driver writes a JSONL disk truth and per-item output files; both are recorded as package evidence |

## Repository execution surface

The routing decision (`slots/workflow-runner/scripts/workflow_route.py`) outputs one
repository-owned `suggestedSurface`, with `available`, `unavailableReason` and `fallback`
from the same capability probe used by the driver. Shape/score stay platform-independent:
a recommendation is not a claim the driver can run. The driver chooses a worker backend
explicitly; the host identity and tool inventory do not change the orchestration contract:

| Owner | Execution surface | How | Notes |
|---|---|---|---|
| **spec-harness** | `subprocess-fanout` | `python3 slots/workflow-runner/scripts/workflow_fanout.py --backend <pi|codex> --model <id> --plan plan.json --out results.jsonl` — bounded concurrency, per-item worker deadline, JSONL convergence | The main session authors `plan.json`; caching is **off by default** unless a verifiable workspace and explicit external-context attestation are supplied (below); failures rerun |

If the selected worker CLI or verified cleanup capability is unavailable, the main session can
continue locally/serially. Sidecar orchestration or `team-loop` under `references/orchestration.md`
requires explicit host authorization/supervision and does not inherit the driver's cleanup guarantees;
the router never launches or silently substitutes it. Never fabricate a backend result or a host capability. Codex always
runs in its `read-only` sandbox: mark an item needing writes with `"requiresWrite": true` and the
driver rejects it for Codex rather than silently escalating. Route such work to a separately
authorized, suitably capable sidecar or do it on the main thread. An omitted declaration is not
proof that a task is read-only; the coordinator must classify assignments. Pi permissions remain
the host’s responsibility, not a permission grant by this slot.

Workers use the host's default reasoning; pass `--model` to pick one. Decisions in the package are
checked once, at `/spec:check`: a closed `[decision]` needs `authority: user:<reference>`.

### Cache and output provenance

Every actual dispatch writes a fresh, unique per-attempt output artifact. A successful record
contains its SHA-256; a missing, modified, oversized, symlinked or non-regular cached artifact is
a cache miss. Cache entries and artifacts are read with a byte limit and must be regular, non-symlink
files; a FIFO or other special file is rejected rather than opened as a blocking stream. If a new
Codex final-message artifact is not a bounded regular file, the item fails. Codex must produce the
current attempt’s final-message file: progress stdout and prior output are not substituted.

On Linux the driver cleans up and verifies the worker’s **same process group** before collecting its
result. A descendant that changes its process group or starts another session is outside that cleanup:
it may survive, and its termination is neither guaranteed nor verified. Do not treat this mechanism
as a sandbox for untrusted workers; use an appropriate host-level isolation and supervision boundary.
The nonblocking special-file defense does not impose a wall-clock bound on arbitrary regular-file or
filesystem I/O.

Worker cleanup is **not** guaranteed on every driver exit path. Each worker is spawned with
`start_new_session`, so it sits in its own session outside the driver’s process group: if the driver
itself is killed (SIGTERM/SIGKILL, a host OOM, or a hard crash) before it reaches a worker’s cleanup,
that worker and its descendants keep running and consuming budget and workspace. This driver never
claims to reap workers after its own abnormal death, and it cannot. Run the driver under a
host-level supervisor (systemd, supervisord, a container/pod that reaps the whole cgroup), and/or
arrange for workers to die with the driver via Linux `prctl(PR_SET_PDEATHSIG)`/`PDEATHSIG` or a
parent-death signal in the worker wrapper. After any suspected abnormal exit, manually verify no
orphaned worker process group remains before trusting the budget or the workspace. A checkpoint
is not a lease: never adopt, kill or redispatch work for an unknown still-live worker automatically.

The driver does not read or write caches by default. `--cache-context <non-secret-id>` explicitly
attests the **unchanged** worker/model/configuration and all external files, services and other
non-Git inputs affecting the task; never put credentials or their values in this identifier. The
driver additionally binds the cache to the current driver and worker executable digests and requires
an accessible Git workspace with a clean HEAD and no untracked or ignored paths, tracked symlinks,
submodules, sparse entries or assume-unchanged entries. An unavailable or unreadable worker disables
the cache. It checks these identities and the workspace at batch admission, before saving each
completed item and after the batch. If this cannot be established, it dispatches instead of
using a cache; cached items already selected are failed if the workspace changes during execution.
`--no-cache` always bypasses both cache reads and writes. An output directory inside the worktree
usually makes it dirty; use an authorized external output location if caching is appropriate.

This is not an immutable filesystem snapshot or an authentication mechanism: an attestation is
only as complete as the caller’s knowledge, and concurrent change-and-revert or external changes
cannot be detected automatically. If any input or worker identity is unknown, omit the flag and
rerun. Successful worker output remains evidence to verify independently, not task completion.

### Per-item checkpoints, not restart authorization

Before dispatch the driver atomically clears prior JSONL observations; after every completed
item it publishes the accumulated records in plan order and fsyncs the file and parent directory.
Each record has `attemptId`, `inputKey`, `status` (`succeeded` / `failed` / `cached`),
`outputSha256` (null when no artifact exists) and `batchComplete`; a reused result also has
`sourceAttemptId`. The final publication sets `batchComplete=true`. During an incomplete batch,
missing items are unknown, not successful, failed or safe to restart. A checkpoint write failure
returns nonzero and cancels only this invocation's queued work; it does not take over other workers.

A completed successful item is cached immediately ONLY through the unchanged strict cache gate
above. On a later invocation, only validated cache entries may skip dispatch: JSONL rows or bare
output files never authorize reuse. Default execution still writes checkpoints but never enables
cache reuse. After an abnormal exit first resolve live-worker uncertainty under host supervision;
then rerun with a verified cache context if appropriate, otherwise rerun explicitly without reuse.
This is recoverable evidence, not guaranteed crash-atomic execution, immutable inputs or faster
production performance; no trigger-rate/cost measurements are claimed.

## Hard preconditions (checked in this order, every time)

1. The task's shape matched `slots/workflow-runner/scripts/workflow_route.py` (score ≥ 2 and at
   least one strong shape signal): batch fan-out, parallel review, perspective panel, or
   review-fix loop. Generic batch terms alone and bare convergence markers are weak signals
   that do not activate the slot.
2. The package contains a valid `plan.json` with a self-contained assignment contract for every item.
3. **Linux-only execution surface.** The fan-out driver’s verified process-group cleanup requires
   Linux `waitid(..., WNOWAIT)` and a readable `/proc`; on non-Linux (e.g. macOS/Windows), on a kernel
   or libc without `waitid(WNOWAIT)`/`killpg`, or where `/proc/self/stat` is unreadable, the driver
   fails **before spawning any worker**. There is no silent fallback: the main session must degrade
   to main-session serial work, or explicitly authorized/supervised sidecars or `team-loop` (below),
   without driver-equivalent cleanup guarantees. Do not assume the driver runs on macOS.
4. The selected worker CLI resolves on PATH; the driver records per-item spawn failures instead of
   fabricating a result.
5. The main session records the output JSONL and runs the task's independent verification.

If any precondition fails: **degrade gracefully** to main-session serial execution, or explicitly
authorized/supervised sidecars or `team-loop` under `references/orchestration.md`; no automatic
substitution or equivalent cleanup claim. The spec main chain is unaffected.

## Relationship to scripts/route_decision.py and team-loop

`scripts/route_decision.py` (five-route vocabulary: local/explore/build/review/external) stays the single
routing authority for a package. This slot activates **only for the execution segment** of
`explore`/`build`/`review` routes with parallelizable shapes, and never changes the recorded
route. Slot activation is independent of routing, per SKILL.md.

**Trigger disambiguation with `team-loop`**: both slots list batch fan-out and review-fix loops.
Use `workflow-runner` when the repository-owned driver and a worker CLI are available; use
`team-loop` for loop state, retries, heartbeats, or when the driver cannot run. Host identity and
host-native orchestration APIs never select an execution surface.

Since the 2026-10-08 boundary closure, review-fix composite shapes (review → fix → re-review)
belong to `workflow-runner` at the routing level: `loop_route.py` explicitly defers those
shapes (`REVIEW_FIX_DEFER_PATTERNS` → `loopRecommended=false`) instead of recommending
`team-loop`. Bare convergence markers (loop-until-converged semantics) stay with `team-loop`;
`batch-fanout` remains available there only as explicit initialization or degradation, not as a
`loop_route.py` recommendation.

## Core protocol (the main session must follow this)

1. **Decide** — `python3 slots/workflow-runner/scripts/workflow_route.py --text "<package goal>" --json`.
   `workflowRecommended=false` → do not use this slot. A positive decision always points to the
   repository-owned `subprocess-fanout` surface.
2. **Check driver preconditions** (see above). Degrade if unmet.
3. **Author `plan.json`** — every item must contain a self-contained delegation package with five
   fields: goal, scope, excluded areas, output shape, and verification. Exclusions include credential
   hygiene: never put actual credential values in prompts; inject them through an environment file or
   the host. A workflow lane cannot see the rest of the session context. Map the suggested mode:
   - `batch-fanout` → `pipeline(items, stageA, stageB)` — no barrier between stages
   - `parallel-review` → dimensions → findings → adversarial verify → synthesize
   - `perspective-panel` → N independent attempts → parallel judges → synthesize from the winner
   - `review-fix-loop` → find → dedup vs seen → multi-lens verify → loop-until-dry

   Identify the acceptance evidence for each mode before merging into the main thread:
   - `batch-fanout`: every item reaches an ok/error terminal state with an artifact path; no items are silently omitted
   - `parallel-review`: findings for each dimension survive adversarial verification or are excluded with a reason
   - `perspective-panel`: record judge scores and incorporate runner-up ideas into the winning synthesis
   - `review-fix-loop`: converge on a dry pass with no new findings and record the final round

   Each item carries the full assignment (goal / scope / excluded areas / output / verification),
   then runs through `slots/workflow-runner/scripts/workflow_fanout.py`.
4. **Bound the work** — one orchestration per well-scoped fan-out; default <10 items per execution
   segment unless the user explicitly raises it; the driver takes `--concurrency` (default 3). If coverage is capped (top-N,
   sampling), record what was dropped.
5. **Collect evidence** — the driver incrementally writes `results.jsonl` (one record per completed item:
   ok / error / durationSec / outputFile plus attempt/input/status metadata) and per-item full-output files.
   Require the final `batchComplete=true` record set before declaring the batch complete. Record both in the
   package's acceptance evidence (external comparison for host-runtime facts).
6. **Merge on the main thread** — sidecar/workflow/fan-out results are merged by the main session
   before any shared boundary is edited. Verification described by `verify` still runs before any
   checkbox is checked.

## Invariants (never change)

- Routing, acceptance, and the done/push gates stay with the main session.
- Self-reported workflow results never complete a task on their own; the task's `verify` must run.
- Orchestration assets persist into the owning package's `.spec/specs/<slug>/orchestration/`
  and travel with the package in git: `plan.json` plus the run ledger `runs.md` (run id, terminal
  state, report summary, artifact ids). Assets are written by the main session — the slot still
  owns the execution segment only. Revise a stored
  script by editing it and re-running, never by repasting it wholesale. `plan.json` remains
  per-run input written by the main session, not a slot asset.
- No cross-slot composition with `team-loop` this round.

## Install (slot form)

This slot ships no hooks (`manifest.json` declares `"hooks": {}`); trigger detection is
code-only by design. Run
`slots/workflow-runner/scripts/workflow_route.py --text "<goal>"` in-session, then follow this
README's protocol.

## Test face

`tests/` covers: positive routing (fan-out / parallel review / panel / review-fix loop texts),
negative routing (single serial local tasks, plain Q&A), repository-owned surface generation,
and the fan-out driver's command construction / concurrency / output bounding / process-group
cleanup / timeout fail-per-item / JSONL convergence (via binary stubs, including real-path
cases that exercise the production selector loop without monkeypatching). Isolated regressions
also pin the four continuation-workflow-fix guarantees: worker stdin is `DEVNULL` (no caller-stdin
inheritance), a group-signal/verify failure becomes a batch-level safe stop that blocks unstarted
items instead of a per-item spawn failure, a `start_new_session` worker survives an abnormal driver
exit (the documented host-supervision boundary), and non-Linux / missing `waitid(WNOWAIT)` /
unreadable `/proc` fail before any worker spawn. Real worker CLI execution remains external
evidence; no host-native Workflow runtime is part of this contract.

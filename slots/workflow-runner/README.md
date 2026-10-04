# workflow-runner slot

This slot answers a narrow execution question: when several independent items can run with the same
contract, how do we fan them out and keep a reviewable disk record? It delegates the **execution segment** of a spec package to the
repository-owned `workflow_fanout.py` deterministic driver. **Shape detection -> driver
preconditions -> managed execution -> structured evidence.** Routing, acceptance, and the
done/push gates never move out of the main session.

## What it solves

| Gap | Solution |
|---|---|
| (1) No automatic trigger | Code trigger `slots/workflow-runner/scripts/workflow_route.py --text "<goal>"` returns a structured recommendation (advisory, never blocks) |
| (2) Main session hand-spawns sidecars for parallel work | Deterministic orchestration: fan-out counts, verify gates, and convergence loops are code in `slots/workflow-runner/scripts/workflow_fanout.py`; Pi/Codex are worker CLIs only |
| (3) Parallel evidence is scattered | The driver writes a JSONL disk truth and per-item output files; both are recorded as package evidence |

## Repository execution surface

The routing decision (`slots/workflow-runner/scripts/workflow_route.py`) outputs one
repository-owned `suggestedSurface`. The driver chooses a worker backend explicitly; the host
identity and tool inventory do not change the orchestration contract:

| Owner | Execution surface | How | Notes |
|---|---|---|---|
| **spec-harness** | `subprocess-fanout` | `python3 slots/workflow-runner/scripts/workflow_fanout.py --backend <pi|codex> --plan plan.json --out results.jsonl` — bounded concurrency, per-item hard timeout, JSONL convergence | The main session authors `plan.json`; successful items cache under `<out-dir>/cache/`; failed or timed-out items rerun; `--no-cache` forces full dispatch |

If the selected worker CLI is unavailable, use ordinary sidecar orchestration or `team-loop` under
`references/orchestration.md`. Never fabricate a backend result or a host capability.

## Hard preconditions (checked in this order, every time)

1. The task's shape matched `slots/workflow-runner/scripts/workflow_route.py` (score ≥ 2): batch fan-out, parallel review,
   perspective panel, or review-fix loop.
2. The package contains a valid `plan.json` with a self-contained assignment contract for every item.
3. The selected worker CLI resolves on PATH; the driver records per-item spawn failures instead of
   fabricating a result.
4. The main session records the output JSONL and runs the task's independent verification.

If any precondition fails: **degrade gracefully** to ordinary sidecar orchestration or `team-loop`
under `references/orchestration.md`. The spec main chain is unaffected.

## Relationship to scripts/route_decision.py and team-loop

`scripts/route_decision.py` (five-route vocabulary: local/explore/build/review/external) stays the single
routing authority for a package. This slot activates **only for the execution segment** of
`explore`/`build`/`review` routes with parallelizable shapes, and never changes the recorded
route. Slot activation is independent of routing, per SKILL.md.

**Trigger disambiguation with `team-loop`**: both slots list batch fan-out and review-fix loops.
Use `workflow-runner` when the repository-owned driver and a worker CLI are available; use
`team-loop` for loop state, retries, heartbeats, or when the driver cannot run. Host identity and
host-native orchestration APIs never select an execution surface.

## Core protocol (the main session must follow this)

1. **Decide** — `python3 slots/workflow-runner/scripts/workflow_route.py --text "<package goal>" --json`.
   `workflowRecommended=false` → do not use this slot. A positive decision always points to the
   repository-owned `subprocess-fanout` surface.
2. **Check driver preconditions** (see above). Degrade if unmet.
3. **Author `plan.json`** — every item must carry a **self-contained delegation package（自包含委派包）**:
   目标（goal）/ 范围（scope）/ 排除（excluded areas，含凭证卫生——真实凭证值不进 prompt，
   经 env 文件/宿主侧注入）/ 输出（output 形状）/ 验证（verification）五字段齐全，因为
   workflow lane 看不到会话里的任何其他上下文。Map the suggested mode:
   - `batch-fanout` → `pipeline(items, stageA, stageB)` — no barrier between stages
   - `parallel-review` → dimensions → findings → adversarial verify → synthesize
   - `perspective-panel` → N independent attempts → parallel judges → synthesize from the winner
   - `review-fix-loop` → find → dedup vs seen → multi-lens verify → loop-until-dry
   各模式验收点（执行段合并回主线程前必须可指认，缺失即证据不全）:
   - `batch-fanout` 验收点：每个 item 落 ok/error 终态并留产物路径，无静默缺项
   - `parallel-review` 验收点：每个维度的 findings 经对抗验证存活，或带理由剔除
   - `perspective-panel` 验收点：judge 评分留痕，胜者综合引用次优想法
   - `review-fix-loop` 验收点：以 dry pass 收敛（整轮无新发现），末轮结果留痕
   Each item carries the full assignment (goal / scope / excluded areas / output / verification),
   then runs through `slots/workflow-runner/scripts/workflow_fanout.py`.
4. **Bound the work** — one orchestration per well-scoped fan-out; default <10 items per execution
   segment unless the user explicitly raises it; the driver takes `--concurrency` (default 3). If coverage is capped (top-N,
   sampling), record what was dropped.
5. **Collect evidence** — the driver writes `results.jsonl` (one record per item:
   ok / error / durationSec / outputFile) plus per-item full-output files. Record both in the
   package's acceptance evidence (外部对标 for host-runtime facts).
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

```bash
# Route-recommendation hook (optional; code trigger works without it)
python3 scripts/install_slot_hooks.py --slot workflow-runner
# Rollback
python3 scripts/install_slot_hooks.py --slot workflow-runner --remove
```

Fully usable without hook registration: run
`slots/workflow-runner/scripts/workflow_route.py --text "<goal>"` in-session, then follow this
README's protocol.

## Test face

`tests/` covers: positive routing (fan-out / parallel review / panel / review-fix loop texts),
negative routing (single serial local tasks, plain Q&A), repository-owned surface generation,
the hook's fail-open behavior, and the fan-out driver's command construction / concurrency /
timeout fail-per-item / JSONL convergence (via binary stubs). Real worker CLI execution remains
external evidence; no host-native Workflow runtime is part of this contract.

# ADR 0006：Evidence-based correction stopping

- 状态：Accepted（2026-11-03）
- 范围：`references/templates.md`、`references/stages/check.md`、`scripts/spec_package_support.py`、`scripts/check_spec_package.py`

## Context

The old check loop stopped after a fixed number of failed rounds. A second round with genuine new evidence could be stopped too early, while two rounds repeating the same failure could consume the budget without changing strategy. A count neither measures progress nor identifies a stuck approach.

## Decision

Every failed correction round appends a row to `### 5.5 修正日志` with `round | approach | result | new information`. New information must cite a new output, a newly located code line or function, or `A<n> FALSIFIED`.

- With no new information, stop, write the causal diagnosis, and propose a materially different strategy.
- If a changed strategy is followed by another no-new-information round, stop for an owner decision.
- A blocked result counts as a correction round. Missing owner input does not.
- Correction rows are append-only and never contain `PASS`.

## Risk and mitigation

A model can describe a repeated failure as new information. The gate rejects a row unless its evidence marker names a concrete new output, location, or falsified assumption. This is still semantic rather than adversarial proof; review must compare the marker with the actual failure evidence.

## Consequences

Historical packages without the section remain readable. New template packages carry the section from initialization, and the package checker validates any present rows.

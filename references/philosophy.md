# Engineering Philosophy

Harness owns only durable state, deterministic verification, and irreversible gates. It does not rebuild task lists, planning UI, subagents, reports, or model judgment. A new mechanism must replace at least one model call or stateful manual step without adding a second source of truth.

Defense admission test: before adding a mechanism, answer what failure it prevents, what evidence proves the failure, what existing mechanism overlaps, what ongoing cost remains, and what can be deleted.

Ownership rules:

- Prefer deletion and one canonical representation over compatibility layers.
- Prefer machine-derived evidence over self-reported progress.
- Prefer a reversible in-charter decision plus ledger over an interruption.
- Prefer checkpointed progress over a long uninterrupted edit sequence.
- Prefer a narrow verify command with a project-level final gate.

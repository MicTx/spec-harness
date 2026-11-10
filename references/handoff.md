# Handoff Document Contract

Source of truth for the optional package member `handoff.md` — the session-boundary handoff document that lets work move between sessions, teammates, fresh agent sessions, or agent-cluster lanes without re-deriving the repository. Runtime mechanics: `scripts/handoff_support.py`; CLI: `scripts/spec_handoff.py`.

## When to use

- A session ends or pauses mid-package (`pause`), someone takes over (`takeover`), a human decision is required (`escalation`), a cluster lane finishes (`lane-end`), or the package archives (`close`).
- Skip it for work that fits entirely inside one conversation; the package documents already carry that state.

The document is optional by construction: packages without it lose no gate, and no stage requires creating one. `scripts/spec_handoff.py validate` checks its structure on demand; `scripts/complete_spec_package.py` appends the terminal `close` entry at `done --archive`.

## File layout

`handoff.md` lives inside the package directory (`.spec/specs/<slug>/handoff.md`) and archives wholesale with the package. It has two zones:

- `## Snapshot` — a generated snapshot, rewritten by the CLI on every update. Never hand-write it: it records the branch, HEAD anchor, task progress, next ready tasks, blockers, and entry-log counters pulled from disk truth.
- `## Entries` — an append-only entry log. Old entries are never edited or deleted; a successor's takeover is a new entry, not a rewrite of the predecessor's.

Existing documents with legacy headings `## 快照` and `## 条目` remain readable and valid; appending preserves old entries.

## Commands

```bash
# Append one entry (creates the file on first use) and regenerate the snapshot
python3 scripts/spec_handoff.py update --slug <slug> [--collab solo|team|agent|cluster] \
  [--event pause|takeover|lane-end|escalation] [--actor <who>] [--status <status>] [--note <one line>]

# Render the snapshot, the latest (or --entry N) entry, and the freshness verdict
python3 scripts/spec_handoff.py show --slug <slug> [--entry N] [--format json]

# Structural validation (the same function the check gate reuses)
python3 scripts/spec_handoff.py validate --slug <slug>
```

`--root <project>` and `--specs-dir <dir>` follow the shared script conventions. `update` defaults: `collab` and `actor` carry over from the latest entry (else `solo` / `main`), `event` defaults to `pause`, `status` defaults to `blocked-on-human` for `escalation` and `in-progress` otherwise. `close` is not a manual choice — it exists only as the terminal entry `done --archive` writes, and appending to a closed log is refused. `--note` is capped at 4000 characters; longer narrative belongs in the entry slots. The read-only `show` / `validate` fall back to `specs/archive/<slug>` after archiving so a frozen log stays inspectable; `update` never resolves into the archive. The CLI generates structure only — narrative slots are placeholders the handing-off session fills; the script never invents narrative.

Writes are atomic (fsync'd temp file + rename) and verified after landing: a document that does not round-trip through its own validator is rolled back to the previous content, so a crash or a generation bug cannot truncate the append-only log or freeze an invalid document.

## Entry structure

Each entry is one `###` block: a heading line `### [<ISO-8601 UTC>] event=<v> collab=<v> actor=<v>`, a fenced `yaml`-style scalar block (`entry` / `event` / `collab` / `actor` / `status`), then narrative slots as `####` sections. Vocabulary is closed and validated:

- `collab`: `solo` | `team` | `agent` | `cluster`
- `event`: `pause` | `takeover` | `lane-end` | `escalation` | `close`
- `status`: `in-progress` | `blocked-on-human` | `blocked-on-agent` | `done`

Required slots adapt to the collaboration mode — the lattice `solo ⊂ {team, agent} ⊂ cluster`. Slot names are literal headings in the persisted handoff protocol:

| Mode | Required slots |
| --- | --- |
| `solo` | `Context`, `Next Steps` |
| `team` | solo + `Ownership`, `Waiting On` |
| `agent` | solo + `Resume` |
| `cluster` | team + agent + `lanes` |

Slot semantics:

- `Context` — what was done and why work stopped here (write conclusions, not transcripts).
- `Findings` — findings as `file:line` pointers, never copied passages.
- `Next Steps` — the single next action: action + target + how to verify.
- `Risks and Contingencies` — contingency: "if X breaks, do Y".
- `Ownership` / `Waiting On` — who owns what, and what the lane is waiting on (team coordination).
- `Resume` — the machine-resume block: the resume command plus the next task's `boundary`/`verify` pointers.
- `lanes` — the cluster lane table: id / status / ownership / pointer / merge order.
- `Acknowledgment` — the receiver's read-back slot, filled on takeover.

Optional slots (`Findings`, `Risks and Contingencies`, `Acknowledgment`) are generated but not required by validation. Legacy slot headings remain valid and are preserved without key normalization: `上下文`, `发现`, `下一步`, `风险与应急`, `所有权`, `等待中`, `恢复`, and `回执`.

## Freshness

The snapshot anchors `head` (git HEAD) and `progress` (task counts). `show` compares them with current disk truth: `fresh` (both match), `stale` (either drifted — numbers are untrustworthy, narrative may still help), or `unknown` (non-Git root or unreadable anchor). Staleness is advisory: it never blocks a stage, it tells the successor what to re-verify.

## Lifecycle integration

| Stage | Behavior |
| --- | --- |
| `/spec` route | Adds a handoff detail line with the latest timestamp, freshness, and collaboration mode, and a JSON `handoff` field when the file exists |
| `/spec` route | Adds a handoff detail line with the latest timestamp, freshness, and collaboration mode, and a JSON `handoff` field when the file exists |
| `/spec:run` | On pause/interrupt/session end, the session runs `update --event pause` (or `takeover` / `escalation` / `lane-end` as appropriate) |
| `/spec:check` | Reads the snapshot for context when present; it does not re-validate the document |
| `/spec:done --archive` | Appends the terminal `close` entry (`status: done`) before archiving; the frozen log moves with the package |

## Writing discipline

- Append-only: correct an earlier entry by writing a newer entry, never by rewriting history.
- One writer at a time: a single session owns the package while it works and writes the boundary entry before releasing it; the format assumes no concurrent writers — coordination is by the mode lattice's ownership slots, not by locking.
- The departing side writes; the receiving side fills the `Acknowledgment` slot on takeover.
- Escalate early with full context rather than letting a stale session drift; a handoff nobody updates is worse than none — the snapshot and the check gate exist to keep that from happening silently.

## Adopting in-flight work

When the scope grows after teammates already started, `scripts/init_spec_package.py --adopt --confirm-adopt [--teammate <name> ...] [--writers-paused]` binds a package to the current branch instead of the strict `/spec:new` path (which still demands a clean tree and a fresh `spec/` branch). It is strictly additive under `.spec/`: no checkout, stash, commit, reset, or edit of anyone's files, and the dirty baseline is stored as paths and hashes only in the package member `adoption.json` (not a gated file).

It refuses a protected or detached branch, a branch already bound to another package, any other active package, an existing slug, a non-Git directory, and a concurrent adopter; one succeeds, the rest write nothing. Teammates are named, never recreated. `--writers-paused` is an operator attestation recorded as such; without it the output says running writers were not confirmed paused, because a member list never proves writes have stopped.

On a branch outside `spec/`, the installed pre-commit hook runs the full gate, so the unfinished package blocks every commit there, teammates' included, and `spec:push` refuses the branch. Rename it before committing (`git branch -m <branch> spec/<name>`), then update the package's `Git integration branch` line to the new name.

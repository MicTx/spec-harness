# Handoff Document Contract

This page is the source of truth for the optional package member `handoff.md` — the session-boundary handoff document that lets work move between one human's sessions, between teammates, into a fresh agent session, or across an agent cluster's lanes without re-deriving the repository. The runtime mechanics live in `scripts/handoff_support.py`; the CLI is `scripts/spec_handoff.py`.

## When to use

- A session ends or pauses mid-package (`pause`), someone else takes over (`takeover`), a human decision is required (`escalation`), a cluster lane finishes (`lane-end`), or the package archives (`close`).
- Skip it for work that fits entirely inside one conversation; the package trio already carries that state.

The document is optional by construction: packages without it (all historical packages) lose no gate, and no stage requires creating one. Once present, `scripts/check_spec_package.py` validates its structure and fails closed on malformation.

## File layout

`handoff.md` lives inside the package directory (`.spec/specs/<slug>/handoff.md`) and archives wholesale with the package. It has two zones:

- `## 快照` — a generated snapshot, rewritten by the CLI on every update. Never hand-write it: it records the branch, HEAD anchor, task progress, next ready tasks, blockers, and entry-log counters pulled from disk truth.
- `## 条目` — an append-only entry log. Old entries are never edited or deleted; a successor's takeover is a new entry, not a rewrite of the predecessor's.

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

`--root <project>` and `--specs-dir <dir>` follow the shared script conventions. `update` defaults: `collab` and `actor` carry over from the latest entry (else `solo` / `main`), `event` defaults to `pause`, `status` defaults to `blocked-on-human` for `escalation` and `in-progress` otherwise. `close` is not a manual choice: it exists only as the terminal entry `done --archive` writes, and appending to a closed log is refused. `--note` is capped at 4000 characters; longer narrative belongs in the entry slots. The read-only subcommands `show` / `validate` fall back to `specs/archive/<slug>` after archiving, so a frozen log stays inspectable; `update` never resolves into the archive. The CLI generates structure only — narrative slots are placeholders the handing-off session fills; the script never invents narrative.

Writes are atomic (fsync'd temp file + rename) and verified after landing: a document that does not round-trip through its own validator is rolled back to the previous content, so a crash or a generation bug cannot truncate the append-only log or freeze an invalid document.

## Entry structure

Each entry is one `###` block: a heading line `### [<ISO-8601 UTC>] event=<v> collab=<v> actor=<v>`, a fenced `yaml`-style scalar block (`entry` / `event` / `collab` / `actor` / `status`), then narrative slots as `####` sections. Vocabulary is closed and validated:

- `collab`: `solo` | `team` | `agent` | `cluster`
- `event`: `pause` | `takeover` | `lane-end` | `escalation` | `close`
- `status`: `in-progress` | `blocked-on-human` | `blocked-on-agent` | `done`

Required slots adapt to the collaboration mode — the lattice `solo ⊂ {team, agent} ⊂ cluster`:

| Mode | Required slots |
| --- | --- |
| `solo` | 上下文、下一步 |
| `team` | solo + 所有权、等待中 |
| `agent` | solo + 恢复 |
| `cluster` | team + agent + lanes |

Slot semantics:

- 上下文 — what was done and why work stopped here (write conclusions, not transcripts).
- 发现 — findings as `file:line` pointers, never copied passages.
- 下一步 — the single next action: action + target + how to verify.
- 风险与应急 — contingency: "if X breaks, do Y".
- 所有权 / 等待中 — who owns what, and what the lane is waiting on (team coordination).
- 恢复 — the machine-resume block: the resume command plus the next task's `boundary`/`verify` pointers.
- lanes — the cluster lane table: id / status / ownership / pointer / merge order.
- 回执 — the receiver's read-back slot, filled on takeover.

Optional slots (`发现`、`风险与应急`、`回执`) ship by generation but validation leaves them free.

## Freshness

The snapshot anchors `head` (git HEAD) and `progress` (task counts). `show` compares them with current disk truth: `fresh` (both match), `stale` (either drifted — numbers are untrustworthy, narrative may still help), or `unknown` (non-Git root or unreadable anchor). Staleness is advisory: it never blocks a stage, it tells the successor what to re-verify.

## Lifecycle integration

| Stage | Behavior |
| --- | --- |
| `/spec` route | Adds a 交接 detail line (`handoff：最新 <ts>（freshness，collab）`) and a JSON `handoff` field when the file exists |
| `/spec:status` | Appends `handoff <collab>/<freshness>` to the package row |
| `/spec:run` | On pause/interrupt/session end, the session runs `update --event pause` (or `takeover` / `escalation` / `lane-end` as appropriate) |
| `/spec:check` | Validates the document when present; malformed structure fails the gate |
| `/spec:done --archive` | Appends the terminal `close` entry (`status: done`) before archiving; the frozen log moves with the package |

## Writing discipline

- Append-only: correct an earlier entry by writing a newer entry, never by rewriting history.
- One writer at a time: a single session owns the package while it works and writes the boundary entry before releasing it; the file format assumes no concurrent writers (there is no file lock — coordination is by the mode lattice's ownership slots, not by locking).
- The departing side writes; the receiving side fills the 回执 slot on takeover.
- Escalate early with full context rather than letting a stale session drift; a handoff nobody updates is worse than none — the generated snapshot and the check gate exist to keep that from happening silently.

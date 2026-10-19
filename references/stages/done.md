# `/spec:done`

Goal: close the task, distill knowledge, archive the task package, and create this round's Git commit; no merge, push, or branch deletion in this stage.

Compatible invocation: Claude Code `/spec:done`; other skill-aware CLIs trigger `spec:done` via `$spec`.

Preconditions:

- `tasks.md` fully complete.
- The `check` gate passed and the acceptance surface satisfied: legacy `checklist.md` fully checked and passing; v2 every `spec.md` §6 checkbox checked with executable evidence in force.
- On this record's integration branch; never a protected branch (truth: `scripts/spec_package_support.py` `PROTECTED_BRANCHES`/`PROTECTED_PREFIXES` + the remote default).
- At least one script/test/build evidence item (or the `evidence: runs` gate for v2); optional gate headings present have their sections passed.
- `spec.md` declares a verification level; `done` reuses the declared `package`/`integration`/`project` scope and its evidence, never widening it implicitly (enforced at `--archive` — a non-archive draft records the level without blocking, the draft is local, the archive is the gate).
- `evidence: runs` packages archive only on fresh executed proof: `--archive` recomputes the worktree fingerprint and refuses when any machine task's latest `runs.jsonl` record is not `exit == 0` or no longer matches the tree (未验证/证据过期 — re-run `scripts/spec_verify.py` after the last tree change, before archiving). The archived `completion-summary.md` gains a 机器验证率 line (`通过/机器任务总数`, manual tasks excluded), and `runs.jsonl` archives with the package.
- Actionable issues found this round have been written back and completed; no executable leftovers enter the archive.

Execution order:

1. Re-verify the package state; no unfinished high-priority items.
2. Distill as an incremental `AGENTS.md` proposal inside `completion-summary.md` (`## AGENTS.md 增量提议` diff fence; `无` if nothing reusable) — never standalone, never auto-applied (full contract: [`../templates.md`](../templates.md)).
3. Prefer running:

```bash
python3 scripts/complete_spec_package.py --root <project> --slug YYYY-MM-DD_<slug> --archive
```

With non-empty issue dispositions, pass repeatable `--issue-disposition '<json>'` or `@path.json` (only the five disposition types of SKILL rule 3; a `resolved_followup` target must already be accepted and archived). `--follow-up` / `--open-risk` / `--not-delivered` / `--deviation` apply only to unarchived drafts; `--test-evidence` / `--build-evidence` / `--manual-evidence` are free-text notes into the summary's `备注` block, never gating the archive (machine evidence is `runs.jsonl`; legacy checklists keep their evidence gates). Distillation: repeatable `--agents-proposal` (omitted → `无`). For an English Git record, add `--git-record-language en` (default `auto`); fields: `date-time | scope | feature | operation | outcome | commit | push`.

4. Generate/update `completion-summary.md`, moving it to `.spec/specs/archive/YYYY-MM-DD_<slug>/` when needed; a `handoff.md` gets its terminal `close` entry (`status: done`) appended before the move.
5. Draft an atomic commit message per Conventional Commits (see [`../naming-and-commits.md`](../naming-and-commits.md)):

```text
<type>(<scope>): <description>

Spec: YYYY-MM-DD_<slug>
```

`type` ∈ `feat | fix | docs | refactor | test | chore | ...`; the `Spec: <slug>` footer anchors the package.

6. Re-inspect the working tree, `git add` the files belonging to this task package, then `git commit` as a single direct command. Never modify files and commit in the same Bash call; never `--no-verify`. This stage never merges, pushes, or deletes branches.
7. Leave merge-back and branch deletion to `push`; record the completion time for traceability.

Knowledge distillation records first:

- Project-specific engineering facts only (difficulties and solutions, decisions and trade-offs, pitfalls, reusable patterns); spec process mechanics live in the skill, never in distillations.
- Credential hygiene: 蒸馏文本不得含真实凭证值 — reference credentials by name and injection path (env file / host-side injection, per the assignment contract's Excluded areas in [`../orchestration.md`](../orchestration.md)); examples use placeholders such as `<admin-token>`.

Output contract:

- Render the `/spec:done` dashboard per [`../output-contracts.md`](../output-contracts.md) — that file is the single source of truth for content requirements; no field lists repeated here. State whether a script-generated summary passed gate validation.
- `Open items` carries no free-text follow-ups; the archive must contain the v1 `## Issue dispositions` JSON (five types, owner + trigger fields where required).

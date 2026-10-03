# Naming And Commits

Names are the first index a human or LLM uses when it returns to a task. A good slug tells the reader what kind of change happened; a good commit footer lets that reader walk back from Git to the evidence that justified it.

Single source of truth for slugs, commits, and Git records. The three rule sets share one design goal: let humans and LLMs review a spec round at minimal cost.

## Contents

- Design principles
- Slug naming
- Commit messages
- Language choice
- The review triangle

## Design principles

Language is handled in two layers:

- **Structural layer** (slugs, commit `type(scope)`, Git record field names) is fixed ASCII English — stable, greppable, cross-platform, deterministically parseable by LLMs.
- **Semantic layer** (commit description and body, Git record content, spec/tasks/summary prose) follows the user's input language.

The structural layer is never localized; the semantic layer is never forced into English. Whether the user writes Chinese or English, the structural retrieval path for review stays identical.

## Slug naming

Format:

```text
YYYY-MM-DD_<verb>-<object>[-<detail>]
```

- Date: ISO `YYYY-MM-DD`, taken from the slug itself; no separate timeline file.
- verb: a controlled verb (see the table), expressing the round's dominant intent.
- object: a kebab-case noun naming the module or theme; reuse the same word when it aligns with the commit scope.
- detail: optional, for disambiguation.
- All-lowercase ASCII; no spaces, no CJK, no consecutive `--` or `__`.

Controlled verb table (sharing semantics with Conventional Commit types — one mental model):

| slug verb | Meaning | Commit type |
| --- | --- | --- |
| `add` | New capability or module | `feat` |
| `fix` | Defect fix | `fix` |
| `refactor` | Refactor, behavior unchanged | `refactor` |
| `update` | Docs, config, dependencies, or version | `docs` / `chore` |
| `remove` | Removal | `chore` / `revert` |
| `docs` | Documentation only | `docs` |
| `test` | Test infrastructure | `test` |
| `chore` | Tooling or miscellaneous | `chore` |

Examples:

```text
.spec/specs/2026-06-12_add-push-stage/
.spec/specs/2026-06-12_fix-push-safety/
.spec/specs/2026-06-12_refactor-spec-architecture/
.spec/specs/2026-06-12_update-all-docs/
```

Rules:

- The verb describes "what this whole spec round does", not a single commit.
- The object uses a stable noun — never temporary branch names, person names, or one-off commands.
- Historical packages keep their names; archives are never rewritten for the new standard.
- Underlying slug validity stays backward compatible; `check` only advises on non-verb-object forms, never hard-fails.
- The slug segment in knowledge documents `.spec/docs/YYYY-MM-DD_slug_topic.md` reuses the same slug.

## Commit messages

Conventional Commits:

```text
<type>(<scope>): <description>

<body>

<footer>
```

- type: `feat | fix | docs | style | refactor | perf | test | build | ci | chore | revert`
- scope: optional, the affected module (e.g. `install`, `spec`, `push`).
- description: imperative mood, ≤ 72 characters, following the user's language; no "already done", use the base verb form.
- body: optional, explains why (not what), wrapped at 72 characters.
- footer: optional, anchors this round's task package with `Spec: <slug>`, replacing the old `[Spec-#N]` numbering.

Slug-to-commit relations:

- One slug (task package) maps to 1..N commits.
- The slug's verb is the overall intent; individual commit types may differ (an `add-billing` package can contain `feat`, `test`, `docs`).
- Not every commit must carry the footer; the `done` archive commit should carry `Spec: <slug>` for traceability.

Examples:

```text
feat(push): add merge-then-push stage with SHA lease

Replaces the done-stage git push with an isolated push stage that
merges the working branch into main, pushes main, and deletes the
merged branch only after SHA verification.

Spec: 2026-06-11_add-push-stage
```

The old format `[Spec] <slug>: <desc> [Spec-#N]` is deprecated; it survives only in historical commits and is never used for new ones.

## Language choice

Auto-detected by default, overridable by argument:

| Artifact | Language | Control |
| --- | --- | --- |
| slug | Fixed ASCII English | Not localized |
| commit type / scope | Fixed English | Not localized |
| commit description / body | Follows the user | Natural |
| Git record field names | zh / en | `--git-record-language auto｜zh｜en`, default `auto` |
| Git record content | Follows the user | Natural |
| spec / tasks / checklist / summary | Follows the user | Natural |

Auto-detection: with `complete_spec_package.py --git-record-language auto`, decide from the CJK ratio of the `spec.md` title (CJK characters > 30% of alphabetic characters → `zh`, else `en`); fall back to `zh` when nothing is decidable.

`/spec:done` defaults to `auto`; force with `--git-record-language zh` or `en` when needed. Commit descriptions need no argument — they naturally follow the user's input.

## The review triangle

The three artifacts share one identity — the slug — and cover before, during, and after:

| Artifact | Role | Time | Link mechanism |
| --- | --- | --- | --- |
| slug | Identity anchor | Before (fixed at new) | Directory name |
| commit | Action log | During (each commit) | Footer `Spec: <slug>` points back to the identity |
| completion-summary | Outcome synthesis | After (generated at done) | Git record section references slug and commit |

Review paths:

- Human: `git log --oneline` for the timeline → open a commit → follow the footer to the package → read the summary.
- LLM: `grep -r "Spec: 2026-06-12"` hits the related commits, or scan `archive/` → read the summary → trace back to commits.
- The all-English structural layer keeps grep, sorting, and parsing stable under any user language.

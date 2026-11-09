# Changelog Guide

A manual helper that turns Git history into standardized changelog entries — never an automatic `done`/`push` step. CLI usage and entry format live in `generate_changelog.py --help`; commit conventions live in `naming-and-commits.md`.

## Release notes style

User-facing release notes (`RELEASE_NOTES.md` and cross-version capability summaries) must use an itemized format:

1. **Use bullets throughout**: group each version by topic (features / discipline / fixes and closure, corresponding to Added / Changed / Fixed), not prose paragraphs. Adjust the groups to the version's content.
2. **One capability per line**: each bullet has a **bold capability name**, a colon, and one sentence describing the mechanism and guarantee; use at most two sentences.
3. **Pair mechanism with guarantee**: explain both what it is and its effect for users. Describe the mechanism, not the internal process.
4. **Use accessible language**: everyday analogies are welcome. Omit internal codenames, including package slugs, finding IDs, internal script names, and review terminology; write for users, not implementers.
5. **Describe shipped behavior only**: do not include promises, plans, or unverified effects.
6. **Keep it to one screen**: aim for at most 15 bullets per version. Cross-version summaries may combine entries by topic, but each bullet still covers one capability.

`RELEASE_NOTES_EDITOR_PROMPT` in `.maintainer/tools/release/build_release.py` shares this contract; keep the two in sync.

Example excerpt:

```markdown
**Added**
- **Parallel delegation**: Batch work is split among subagents, each with a goal, scope, exclusions, and acceptance criteria; the main session retains acceptance authority.
- **Evidence freshness**: Acceptance evidence can be anchored to a code version and time; code changes invalidate old evidence and require verification again.
```

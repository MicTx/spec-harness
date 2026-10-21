# `/spec:doctor`

Goal: environment self-check and repair. Inspect every foundation and installation surface the skill depends on to find the "installed but broken" gaps; `--fix` repairs the safe items. Task-package content is out of scope (that is `scripts/check_spec_package.py`).

Compatible invocation:

- Claude Code CLI: `/spec:doctor`
- Codex / generic skill-aware CLI: command alias `spec:doctor`, triggered by `$spec`

Prefer running:

```bash
# Check the current project environment
python3 scripts/doctor_spec_environment.py --root <project>
# Auto-repair safe items (installer-owned paths only; backups go to ~/.spec-skill-backups/ before deletion)
python3 scripts/doctor_spec_environment.py --root <project> --fix
# Machine-readable output / limit to one host scan
python3 scripts/doctor_spec_environment.py --format json --host claude
```

Check catalog:

1. `python.runtime`: Python ≥ 3.9 (stdlib-only; newest syntax feature `str.removeprefix`)
2. `git.available`: git on PATH (the done/push and hook gates depend on it)
3. `skill.layout`: all 39 runtime files present (doctor holds the full manifest; core excludes extension files)
4. `skill.scripts`: every `scripts/*.py` compiles (detects truncated installs)
5. `skill.channels`: bounded `--help` stdout/stderr spot-check on the five sample scripts (channel contract: contract output on stdout, diagnostics on stderr)
6. `skill.marker` + `skill.drift`: installer ownership marker; when a source checkout exists, per-file hash comparison reports drift
7. `hosts.installed`: twelve-host directory scan (same environment variables and default paths), versions, partial installs, foreign same-name collisions
8. `claude.commands`: user-stage command files exist; legacy layout leftovers (`spec:<stage>.md` colon files, alias directories, the `spec.md` base file); claude-side probe — skipped unless claude is in `--host` scope
9. `claude.agents`: the four orchestration sidecar contracts (orchestrator/planner/reviewer/confirmer) are published as installer-owned native copies in `~/.claude/agents` and stay in sync with the skill source; claude-side probe — skipped unless claude is in the `--host` scope
10. `pi.prompts`: native Pi prompt templates (`/spec` + `/spec:<stage>`) — presence, stage-set/profile consistency, installer markers, thin-forwarder shape (quoted frontmatter, literal `$@`, contract-index pointer), plus installer-owned leftovers; pi-side probe — skipped unless pi is in `--host` scope
11. `migration.wrappers`: foreign `~/.agents/skills/source-command-spec-*` migration wrappers (Codex CLI command_migration artifacts) — report-only, never auto-removed
12. `zcode.symlink`: dangling `~/.zcode/skills/spec` links
13. `project.git`: whether the target project is a Git work tree
14. `project.spec`: the `.spec/specs/` and `.spec/specs/archive/` structure
15. `project.hooks`: whether rendered checker pointers still target existing scripts or drifted to another skill copy

Repair actions (`--fix`, all idempotent):

- Remove installer-owned legacy layout leftovers (backed up first; non-installer-owned items are only reported)
- Recreate `.spec/specs/` and `.spec/specs/archive/`
- Regenerate missing Claude stage command files by rerunning `INSTALL_HOSTS=claude bash install.sh` (prints the equivalent command when bash is not on PATH)
- On hook-pointer drift, back up the old hooks and rerun `scripts/install_git_hooks.py`

Exit codes: `0` no failures; `1` failures remain (after `--fix`); `2` argument error. Status levels: `ok / info / warn / fail / fixed`.

Output contract:

- Human-readable markdown or `--format json` (checks/summary/fixLog/exitCode).
- User-visible replies follow the script output: which foundations work, which are missing, which were fixed, which need manual action; never hand-write the check conclusions.
- Repairs touch installer-owned paths only; any deletion goes to `~/.spec-skill-backups/doctor-<ts>/` first.

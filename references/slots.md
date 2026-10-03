# Extension Slots

Slots answer a narrow extension question: can a capability ship beside the task-package state machine without changing that state machine? A slot is a self-contained directory under `slots/<name>/`, declared by `manifest.json` and shipped with `install.sh`; the core scripts stay untouched.

## When to build a slot

- The capability has an independent boundary (its own scripts / hooks / tests / docs)
- It works without modifying the spec core state machine (the task-package trio, gates, archiving)
- Typical example: `team-loop` (agents-team loop triggering and efficient management)

Not slot material: changes to the task-package lifecycle itself (they belong in core `scripts/`); pure documentation (belongs in `references/`).

## Contract

### Directory layout

```
slots/<name>/
├── manifest.json      # declaration (see below)
├── README.md          # slot protocol documentation (how agents use it)
├── scripts/           # CLI (agents invoke via bash)
├── hooks/             # Claude Code hook scripts (stdin JSON -> stdout decision)
├── references/        # the slot's own background docs (optional)
└── tests/             # bundled pytest tests + smoke (the slot's self-verification contract)
```

### manifest.json

```json
{
  "name": "<same as the directory>",
  "version": "1.0.0",
  "summary": "one-line capability summary",
  "description": "detailed description",
  "triggers": ["what task shapes should activate this slot"],
  "requires": ["external dependencies, e.g. the agents-team skill tool surface"],
  "scripts": ["scripts/relative/path.py"],
  "hooks": {"<ClaudeCodeEvent>": ["hooks/relative/path.py"]},
  "tests": "tests",
  "docs": ["README.md", "references/xxx.md"]
}
```

Hard rules (enforced by the registry):
- Required fields: `name / version / summary / scripts / hooks`
- No declared path may escape the slot directory
- Hook events must be known Claude Code events
- `name` must equal the directory name

## Registry

```bash
python3 scripts/slot_registry.py list        # enumerate + validity
python3 scripts/slot_registry.py show --slot team-loop
python3 scripts/slot_registry.py validate    # any invalid -> exit 2
```

## Hook install / rollback

```bash
python3 scripts/install_slot_hooks.py --slot team-loop                 # user scope (~/.claude/settings.json)
python3 scripts/install_slot_hooks.py --slot team-loop --scope project --project-root <repo>
python3 scripts/install_slot_hooks.py --slot team-loop --remove         # rollback
```

- Identity uses the relative marker `slots/<name>/hooks/<script>`: independent of the install root, so after a spec reinstall/migration old entries are still reclaimed by `--remove`.
- Idempotent, preserves other people's entries, backs up before writing, refuses to write corrupted settings.

## Distribution and tests

- `COPY_DIRS` of `scripts/export_skill_package.py` includes `slots`: slots ship wholesale with the installed copy.
- `pyproject.toml` sets `testpaths = ["tests", "slots"]`: slot tests are collected by `python3 -m pytest`; slot tests carry their own conftest (injecting sys.path for that slot's scripts only).

## Adding a slot

1. `mkdir slots/<name>`; lay out scripts/hooks/tests/docs per the contract.
2. Write `manifest.json`; run `python3 scripts/slot_registry.py validate` until fully green.
3. Write the protocol into the slot README (trigger conditions, hard preconditions, commands, discipline).
4. `python3 scripts/install_slot_hooks.py --slot <name>`; install and verify live.
5. To be auto-activated by task routing: register the trigger rule in the slots section of SKILL.md.

## Current slots

| Slot | Capability |
|---|---|
| `team-loop` | agents-team loop triggering and efficient management (routing triggers / state machine / retries / concurrency / heartbeats / interrupts / termination). See `slots/team-loop/README.md` |
| `workflow-runner` | Delegating execution segments to the repository-owned deterministic driver (shape detection / driver preconditions / pi + codex worker subprocess fan-out via `workflow_fanout.py` / JSONL evidence). See `slots/workflow-runner/README.md` |

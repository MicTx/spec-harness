# Pull Request

<!-- A good PR lets a reviewer answer: what changed, why, and what proves it? -->

What does this PR change, and which user problem does it solve?

## Type of Change

- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update
- [ ] Refactor / cleanup

## How Has This Been Tested?

Describe the tests that verify your changes.

## Compatibility

List changed commands, task-package formats, exported layouts, or migration steps. Write `None` when no compatibility note is needed.

## Checklist

- [ ] `python3 -m compileall -q scripts server hooks slots tests` passes
- [ ] `python3 scripts/slot_registry.py validate` passes
- [ ] `python3 scripts/smoke_test_spec_skill.py` passes
- [ ] `python3 -m pytest -q` passes
- [ ] `ruff check scripts/ server/ hooks/ slots/ tests/` passes
- [ ] `ruff format --check scripts/ server/ hooks/ slots/ tests/` passes
- [ ] `python3 scripts/organize_project_structure.py --root . --check` passes for documentation or layout changes
- [ ] User-facing behavior changes are reflected in `CHANGELOG.md`
- [ ] Runtime layout or installer changes were verified against an exported package
- [ ] README, `SKILL.md`, `references/*`, metadata, installer text, and templates stay aligned where applicable
- [ ] No unrelated changes mixed in

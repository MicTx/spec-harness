# Contributing

Keep the skill aligned with its implementation. Scripts and template fences are sources of truth. Other carriers point at them; they do not recopy gate lists.

## Development Rules

- Keep `SKILL.md` concise. Put command details, templates, output contracts, and storage rules in `references/`.
- If a workflow principle or script contract changes, update the source of truth first. Other docs should link, not duplicate the gate paragraph.
- Do not add new runtime carriers such as `CLAUDE.md`, `CURSOR.md`, marketplace metadata, or package-manager manifests unless the project explicitly adopts that distribution path.
- If `/spec:check` semantics change, update `check_spec_package.py` and any tests that lock its output. Docs describe the script; they do not restate every gate.
- If issue-closure semantics change, update `issue_closure_support.py` and the carriers that consume it.
- Keep helper scripts dependency-free unless there is a concrete need.
- Avoid claims in README or metadata that are not backed by scripts or documented skill behavior.
- Keep the installer surface minimal: root `install.sh` is the only runtime installer entrypoint unless a new distribution path is explicitly adopted.
- The book assets are authoring artifacts, not runtime obligations. `release/` is the only tracked distribution channel: refresh it from `build_release.py` after version bumps; no selling material is shipped.

## Verification

Before submitting a change, install development dependencies and run:

```bash
python3 -m pip install -e '.[dev]'
python3 -m py_compile scripts/*.py
python3 scripts/smoke_test_spec_skill.py
python3 -m pytest tests/
ruff check scripts/ tests/
```

For documentation changes that affect `/spec:check`, verify that the docs still describe the current `check_spec_package.py` behavior: base gates, optional new-gate activation, evidence refill, and script/test/build command evidence.

For script behavior changes, also create a temporary task package and run the affected scripts against it:

```bash
python3 scripts/init_spec_package.py --root /tmp/spec-test --slug demo --title Demo --force
python3 scripts/route_spec_package.py --root /tmp/spec-test --slug demo
python3 scripts/report_spec_package.py --root /tmp/spec-test --slug demo --view status
python3 scripts/check_spec_package.py --root /tmp/spec-test --slug demo
python3 scripts/complete_spec_package.py --root /tmp/spec-test --slug demo --allow-incomplete --force
```

The repository CI runs compile, smoke, pytest, and ruff checks on push and pull request. Keep local verification aligned with `.github/workflows/ci.yml`, and use `git diff --check` before committing to catch whitespace issues.

## Pull Request Checklist

- The change has a clear scope and does not include unrelated cleanup.
- User-facing text points at the same workflow as the scripts.
- `/spec:check` documentation matches `check_spec_package.py` when check gates, checklist evidence, or templates change.
- Script behavior matches the documented command contracts.
- New public-facing material is factual and does not overstate what the skill does.

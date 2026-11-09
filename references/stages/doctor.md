# `/spec:doctor`

Goal: find and repair "installed but broken" environment gaps. Package content is `scripts/check_spec_package.py`'s job.

```bash
python3 scripts/doctor_spec_environment.py --root <project> [--fix] [--format json] [--host claude]
```

It checks Python ≥ 3.9, git, the installed skill files and their drift, host command files, Git hook pointers and the `.spec/` layout. `--fix` repairs installer-owned items only, backing up to `~/.spec-skill-backups/` first. Exit codes: `0` healthy, `1` failures remain, `2` argument error. Relay the script's conclusions; do not hand-write them.

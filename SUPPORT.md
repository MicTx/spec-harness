# Support

Use the public issue tracker for installation questions, reproducible bugs, and feature proposals. The project has no hosted service or guaranteed response time, so the fastest route is a small, reproducible report.

Before opening an issue, narrow the question:

- Read the [README](README.md) and [Git workflow guide](docs/git-workflow.en.md).
- Search existing issues and the [release notes](https://github.com/MicTx/spec-harness/releases).
- Report the release tag or source commit when known, plus the OS, host CLI version, and installation method. A `main` checkout and a release archive can report the same package version.
- Run the smallest relevant verification command and include redacted output. For early stops, loops, or missing callbacks, see [evidence-driven continuation](docs/loop-continuity.md). Mention whether a verification ran and whether the host actually loaded the hooks.

Choose the right channel:

- Bug: use the [bug report form](https://github.com/MicTx/spec-harness/issues/new?template=bug_report.yml) with reproduction steps and the exact error.
- Feature: describe the user problem, the smallest useful behavior, and the compatibility impact.
- Security: follow [SECURITY.md](SECURITY.md); do not publish exploit details.

Support is provided through the public repository. There is no hosted Spec Harness service or guaranteed response time.

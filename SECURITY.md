# Security Policy

## Supported Versions

This repository currently maintains the `main` branch.

## Reporting A Vulnerability

Please report security issues through GitHub private vulnerability reporting if it is enabled for the repository. If it is not enabled, open a minimal public issue that avoids exploit details and ask for a private contact path.

Include:

- Affected file or script.
- Reproduction steps.
- Expected impact.
- Any relevant environment details.

## Security Scope

The helper scripts primarily operate on local task-package files under a project root. The optional `server/` adapter runs a standard-library HTTP service that initializes and projects task-package state; it is not a hosted service and has no built-in authentication. The server defaults to loopback, requires an explicit `ALLOWED_ROOTS` policy for production, resolves real paths to prevent symlink escape, and should be placed behind a trusted TLS/authentication proxy before network exposure. The installer can clone a pinned HTTPS repository when `REPO_URL` and full `REPO_REF` are provided, and `push_spec_package.py` can perform local Git merge/push/branch-deletion operations after its safety prechecks pass. Runtime helper scripts do not require third-party Python dependencies.

Security-relevant changes include server request parsing and response handling, `ALLOWED_ROOTS` real-path boundary validation and symlink handling, CORS/authentication/TLS deployment choices, path handling, `--specs-dir` root-boundary validation including symlink-resolved paths, archive behavior, file writes, installer replacement and backup rules, generated Claude Code slash-command files under `~/.claude/commands`, generated instructions that affect Git operations, explicit `spec:push` execution paths, `/spec:check` gates that authorize done/archive/commit/push flows, and workflow text that could cause an agent to touch files outside the intended project scope.

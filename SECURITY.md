# Security Policy

## Supported Versions

Security fixes target the latest release and the main branch. Older releases may not receive backported fixes.

## Report a Vulnerability

Use [GitHub private vulnerability reporting](https://github.com/MicTx/spec-harness/security/advisories/new) when it is available. If the form is unavailable, open a minimal public issue titled Security contact requested without exploit details; a maintainer will provide a private channel.

Include:

- The affected version or commit.
- The affected file, command, or server endpoint.
- Reproduction steps that do not expose credentials or personal data.
- Expected and observed behavior.
- Impact assessment and any suggested mitigation.

Do not include secrets, private keys, access tokens, personal data, or a weaponized proof of concept in a public issue.

## Scope

Spec Harness runs helper scripts against task-package files under a project root. The optional server/ adapter is a self-hosted HTTP service; it defaults to loopback and must be protected by an authentication and TLS boundary before network exposure. Security reports are especially useful for path traversal, symlink escapes, unsafe archive handling, process isolation, installer replacement, secret disclosure, and authorization bypasses in the server adapter.

## Disclosure

Maintainers will acknowledge a valid report, reproduce it, coordinate a fix, and publish a release note when disclosure is appropriate. Please allow time for a fix before sharing details publicly.

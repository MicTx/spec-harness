# spec server-mode

By default spec ships as a **local skill**: installed into `~/.claude/skills` (or Codex/other skill-aware CLIs), triggered by the host via `$spec`, reading and writing `.spec/` inside the caller's local Git repository. This local form is the reference behavior.

`server/` is spec's **second form**: it exposes task-package initialization and status projection over three HTTP endpoints for an asynchronous task-workflow platform to call remotely. It does not replace server-side `run`, `check`, or `done` operations and creates no second state machine; the `.spec` Development Record stays the single source of truth. Both forms share the same [`../scripts/`](../scripts) and [`../references/`](../references) sources — only the entry point differs.

## Three-endpoint contract

The platform calls the three endpoints in a fixed order; the adapter `server.py` never imports spec scripts — it invokes them via `subprocess`, so it stays a single file with zero dependencies (Python stdlib only).

| Endpoint | Method + path | Input | Returns |
|---|---|---|---|
| Start | `POST {ROOT}/start` | `{"root":"<absolute project path>","goal":"<development goal>"}` (Bearer token required) | `{"task_id":"<slug>",...}` |
| Result | `GET {ROOT}/result?task_id=<slug>&root=<path>` | `task_id`,`root` (Bearer token required) | `{"task_id":...,"workflow_status":"running\|completed\|failed","stage":...,"decision":...,"closure":...,"status_detail":...}` |
| Health | `GET {ROOT}/health` | none | `{"code":0,"status":"healthy"}` |

`ROOT` defaults to `/v1` (overridable via the `ROOT_PREFIX` environment variable); the listen address defaults to `127.0.0.1` (overridable via `HOST`). `workflow_status` mapping: an active-package status report containing a current stage → `running`; an archive counts as `completed` / `decision=closed` only after `check_all_spec_packages.py --require-archived --slug` validates the quartet, the trio, and the v1 issue dispositions, and the response then carries `closure.valid/version/issue_count`; broken packages, missing summaries, invalid new closeouts, or symlink archives → `failed` / `decision=invalid-archive`. `status_detail` has a response-size cap, but status decisions and closure parsing read the full summary the checker permits — never the truncated text. `SPEC_SKILL_DIR` defaults to this file's parent directory (the runtime package root containing `scripts/` and `references/`), overridable by environment. `ALLOWED_ROOTS` (comma-separated) whitelists the `root` a caller may pass, resolved against real paths to block symlink escapes; derived package paths under a whitelisted root get a second realpath-containment check that rejects symlinks pointing outside the root; empty means unrestricted (loopback development only). When `SPEC_SERVER_TOKEN` is set, `/start` and `/result` require `Authorization: Bearer <token>` (constant-time comparison); `/health` stays anonymous for liveness probes. Without a token set and with a non-loopback `HOST`, the adapter refuses to start; a non-integer or out-of-range `PORT` (1-65535) or an invalid `MAX_CONCURRENT_WORK` likewise refuses startup (clean error, no traceback). `MAX_CONCURRENT_WORK` (default 4) caps concurrent subprocess work slots; over-limit requests get 429. Auto-generated task slugs carry a random suffix, so the same goal never collides; an explicit `slug` keeps idempotent retry semantics. Initialization failures return a generic error only — details go to server logs. CORS is off by default, settable explicitly via `CORS_ORIGIN` (preflight already allows the `Authorization` header).

## Deployment

`install.sh` installs the exported runtime package to `/opt/spec-workflow`, wires up systemd + Caddy (automatic HTTPS), and creates a compliant git repository under `$PROJECTS_DIR/demo` for self-testing. `PROJECTS_DIR` defaults to `/srv/spec-projects`, overridable at install time. The installer validates `DOMAIN`, `PORT`, `HOST`, and `PROJECTS_DIR` before doing anything (rejecting newlines, control characters, spaces, quotes, `..`, bare `/` and `.` path components, and invalid hostnames/ports); with no `SPEC_SERVER_TOKEN` provided it generates one and writes it into the systemd unit (file mode 600). The installer must run as root (e.g. `sudo bash server/install.sh`); non-root is refused before any system directory is touched, and the demo repository is created with `runuser` privilege drop — no sudo needed. The self-test hard-validates the health check and the token-bearing `/start` with `curl -sSf` (any HTTP>=400 or a response missing `slug` aborts the install immediately). A real installation writes system directories, creates the service user, and modifies Caddy/systemd; an ordinary dev machine only runs `bash -n server/install.sh` and the local Python server smoke — never the installer itself.

```bash
# Run from the exported runtime package root (contains server/ scripts/ references/ SKILL.md)
DOMAIN=spec.yourdomain.com bash server/install.sh
```

Prerequisites: the domain A record points at this machine, ports 80/443 open. Debian/Ubuntu installs Caddy automatically; other distros need Caddy installed first per https://caddyserver.com/docs/install.

Post-install self-test (the token is in the install report and `/etc/systemd/system/spec-workflow.service`):

```bash
curl -s https://spec.yourdomain.com/v1/health
curl -s -XPOST https://spec.yourdomain.com/v1/start \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <token>' \
  -d '{"root":"/srv/spec-projects/demo","goal":"user login and registration"}'
```

Environment variables (written as `Environment=` in the systemd unit): `SPEC_SKILL_DIR`, `PORT` (8787), `HOST` (127.0.0.1), `ALLOWED_ROOTS` (default `/srv/spec-projects`), `SPEC_SERVER_TOKEN` (installer-generated or passed in), optional `MAX_CONCURRENT_WORK` (default 4), and optional `CORS_ORIGIN`.

## Creating a project repository the workflow can operate

spec's `new` requires the project to sit on a `main` branch with at least one commit. Do this once per project:

```bash
sudo -u spec bash -c '
  mkdir -p /srv/spec-projects/<proj> && cd /srv/spec-projects/<proj>
  git init -q -b main; git config user.email spec@uumit.local; git config user.name spec-server
  echo "# <proj>" > README.md; git add -A; git commit -qm init
'
```

The caller passes `root=/srv/spec-projects/<proj>` to `/start`. Note: one project repository carries one active task package at a time (`init` creates the `spec/YYYY-MM-DD_<slug>` integration branch).

## Operations

```bash
systemctl status spec-workflow        # adapter status
journalctl -u spec-workflow -f        # live logs
systemctl restart spec-workflow      # restart (after a package update)
systemctl reload caddy                # reload after a domain change
```

To upgrade server-side spec: run `install.sh` again (with the latest exported package), then `systemctl restart spec-workflow`.

## Local development smoke

No deployment needed — run directly from this repository:

```bash
SPEC_SKILL_DIR=/path/to/spec-repo PORT=8787 python3 server/server.py
curl -s http://localhost:8787/v1/health
```

- For a Linux container deployment, run the repository verification commands from `CONTRIBUTING.md`, then run `bash -n server/install.sh` and the server smoke checks in the target image.

## Semantic boundary

spec's origin is the **local-repository** task-package workflow. Once server-mode hosts it, packages land in **server-side** project directories rather than the caller's local machine; the platform caller receives the package slug, stage routing, active/archive gates, and the structured closure projection — never direct writes to the caller's repository. The server-side platform still owns the subsequent `run`, `check`, `done`, and archiving; the server never disguises a bare archive directory as completion.

## Difference from harness

多轮编排控制面为预留扩展，未随本仓库分发；`server/` 只负责通过 HTTP 暴露 spec 的任务包初始化与状态投影。
